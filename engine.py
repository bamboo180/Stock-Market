"""資料抓取與分析引擎（台股 + 美股）。

台股：FinMind（日K、三大法人、融資融券），失敗時價格改用 yfinance。
美股：yfinance（日K），另抓 S&P 500 (SPY) 做相對強弱。
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HORIZON = 10          # 預測未來幾個交易日
CONE_DAYS = 20        # 預估扇形天數
FINMIND = "https://api.finmindtrade.com/api/v4/data"

# 交易成本（單邊）
COSTS = {
    "TW": {"buy": 0.001425, "sell": 0.001425 + 0.003},   # 手續費 + 證交稅
    "US": {"buy": 0.0005, "sell": 0.0005},               # 假設 0.05% 滑價/手續費
    "IDX": {"buy": 0.0005, "sell": 0.0005},              # 指數（以 ETF 追蹤的成本估計）
}


class DataError(Exception):
    """抓不到資料時丟出，訊息直接顯示給使用者。"""


# ----------------------------------------------------------------------------
# 市場判斷
# ----------------------------------------------------------------------------
def is_index(code: str) -> bool:
    """指數、期貨、匯率、加密幣等 Yahoo 代號：^TWII、ES=F、TWD=X、BTC-USD、000001.SS"""
    c = code.strip().upper()
    return c.startswith("^") or "=" in c or c.endswith("-USD") or bool(re.fullmatch(r"\d{6}\.(SS|SZ)", c)) or c == "DX-Y.NYB"


def detect_market(code: str) -> str:
    code = code.strip().upper()
    if is_index(code):
        return "IDX"
    if re.fullmatch(r"\d{4,6}[A-Z]?", code):
        return "TW"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", code):
        return "US"
    raise DataError(f"看不懂代號「{code}」。台股請輸入數字代號（例如 2330），美股請輸入英文代號（例如 AAPL）。")


# ----------------------------------------------------------------------------
# 台股：FinMind
# ----------------------------------------------------------------------------
def _finmind(dataset: str, code: str, start: str, token: str | None) -> pd.DataFrame:
    params = {"dataset": dataset, "start_date": start}
    if code:
        params["data_id"] = code
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.get(FINMIND, params=params, headers=headers, timeout=30)
        j = r.json()
    except Exception as e:  # 網路錯誤
        raise DataError(f"連不到 FinMind（{e}）。請確認網路連線。") from e
    if j.get("status") != 200:
        msg = j.get("msg", "")
        if "limit" in msg.lower() or r.status_code == 402:
            raise DataError("FinMind 免費額度用完了（每小時有次數限制）。可以稍後再試，或到 finmindtrade.com 免費註冊取得 token 填在側邊欄。")
        raise DataError(f"FinMind 回傳錯誤：{msg}")
    return pd.DataFrame(j.get("data", []))


def tw_name(code: str, token: str | None = None) -> str:
    try:
        info = _finmind("TaiwanStockInfo", code, "2000-01-01", token)
        if len(info):
            return str(info.iloc[0]["stock_name"])
    except DataError:
        pass
    return ""


def fetch_tw(code: str, start: str, token: str | None = None) -> pd.DataFrame:
    # 價格、法人、融資三個請求同時送出，不用一個等一個
    from concurrent.futures import ThreadPoolExecutor

    def safe(ds):
        try:
            return _finmind(ds, code, start, token)
        except DataError as e:
            return e
    with ThreadPoolExecutor(3) as ex:
        f_px, f_inst, f_mg = (ex.submit(safe, ds) for ds in (
            "TaiwanStockPrice", "TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMarginPurchaseShortSale"))
    px, inst_r, mg_r = f_px.result(), f_inst.result(), f_mg.result()
    if isinstance(px, DataError):
        raise px
    if px.empty:
        px = _fetch_tw_yf(code, start)
        if px.empty:
            raise DataError(f"找不到台股代號 {code} 的價格資料，請確認代號是否正確。")
        df = px
    else:
        px = px[px["Trading_Volume"] > 0]
        df = pd.DataFrame({
            "date": pd.to_datetime(px["date"]),
            "open": px["open"].astype(float), "high": px["max"].astype(float),
            "low": px["min"].astype(float), "close": px["close"].astype(float),
            "vol": (px["Trading_Volume"] / 1000).round(),           # 張
        })

    # 三大法人（張）
    inst = inst_r if isinstance(inst_r, pd.DataFrame) else pd.DataFrame()
    if not inst.empty:
        inst["net"] = (inst["buy"] - inst["sell"]) / 1000
        grp = inst["name"].map(lambda n: "foreign" if n.startswith("Foreign")
                               else "trust" if n == "Investment_Trust"
                               else "dealer" if n.startswith("Dealer") else None)
        inst = inst.assign(grp=grp).dropna(subset=["grp"])
        piv = inst.pivot_table(index="date", columns="grp", values="net", aggfunc="sum")
        piv.index = pd.to_datetime(piv.index)
        df = df.merge(piv, left_on="date", right_index=True, how="left")

    # 融資融券（張）
    mg = mg_r if isinstance(mg_r, pd.DataFrame) else pd.DataFrame()
    if not mg.empty:
        mg = pd.DataFrame({"date": pd.to_datetime(mg["date"]),
                           "margin": mg["MarginPurchaseTodayBalance"],
                           "short": mg["ShortSaleTodayBalance"]})
        df = df.merge(mg, on="date", how="left")

    for col in ["foreign", "trust", "dealer", "margin", "short"]:
        if col not in df:
            df[col] = np.nan
    df[["foreign", "trust", "dealer"]] = df[["foreign", "trust", "dealer"]].fillna(0)
    df[["margin", "short"]] = df[["margin", "short"]].ffill()
    return df.sort_values("date").reset_index(drop=True)


def _fetch_tw_yf(code: str, start: str) -> pd.DataFrame:
    for suffix in (".TW", ".TWO"):
        df = _yf(code + suffix, start)
        if not df.empty:
            df["vol"] = (df["vol"] / 1000).round()
            return df
    return pd.DataFrame()


# ----------------------------------------------------------------------------
# 美股：yfinance
# ----------------------------------------------------------------------------
def _yf(ticker: str, start: str) -> pd.DataFrame:
    import yfinance as yf
    try:
        raw = yf.download(ticker, start=start, auto_adjust=True, progress=False, threads=False)
    except Exception as e:
        raise DataError(f"連不到 Yahoo Finance（{e}）。請確認網路連線。") from e
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.reset_index()
    return pd.DataFrame({
        "date": pd.to_datetime(raw["Date"]).dt.tz_localize(None),
        "open": raw["Open"].astype(float), "high": raw["High"].astype(float),
        "low": raw["Low"].astype(float), "close": raw["Close"].astype(float),
        "vol": raw["Volume"].astype(float),
    }).dropna()


def fetch_index(symbol: str, start: str) -> pd.DataFrame:
    df = _yf(symbol, start)
    if df.empty:
        raise DataError(f"找不到指數 {symbol} 的資料，請確認是 Yahoo Finance 的代號（例如 ^TWII、^GSPC）。")
    df["bench"] = np.nan
    return df


def fetch_us(ticker: str, start: str) -> pd.DataFrame:
    df = _yf(ticker, start)
    if df.empty:
        raise DataError(f"找不到美股代號 {ticker} 的資料，請確認代號是否正確（例如 AAPL、NVDA、BRK-B）。")
    bench = _yf("SPY", start)
    if not bench.empty:
        df = df.merge(bench[["date", "close"]].rename(columns={"close": "bench"}), on="date", how="left")
        df["bench"] = df["bench"].ffill()
    else:
        df["bench"] = np.nan
    return df


def us_name(ticker: str) -> str:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return info.get("shortName") or info.get("longName") or ""
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# 分析
# ----------------------------------------------------------------------------
# 條件不成立時顯示的說法
NEG_LABEL = {
    "價格站上月線": "價格跌破月線", "月線在季線之上": "月線在季線之下",
    "外資10日買超": "外資10日賣超", "投信10日買超": "投信10日賣超",
    "融資10日減少": "融資10日增加", "OBV 10日上升": "OBV 10日下降",
    "RSI 高於 50": "RSI 低於 50", "20日強於大盤": "20日弱於大盤", "20日漲幅為正": "20日漲幅為負",
}


def label(name: str, sign: int) -> str:
    return name if sign > 0 else NEG_LABEL.get(name, "未符合：" + name)


def _sign(cond: pd.Series) -> pd.Series:
    return pd.Series(np.where(cond, 1, -1), index=cond.index)


def analyze(df: pd.DataFrame, market: str) -> dict:
    if len(df) < 120:
        raise DataError(f"資料只有 {len(df)} 個交易日，至少需要 120 天才能分析。請把期間拉長，或這檔股票上市時間太短。")

    df = df.copy().reset_index(drop=True)
    c = df["close"]
    df["ma20"] = c.rolling(20).mean()
    df["ma60"] = c.rolling(60).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    df["logret"] = np.log(c).diff()
    df["vol10"] = df["vol"].rolling(10).sum()

    feat = {
        "ret5": c.pct_change(5), "ret20": c.pct_change(20),
        "dist_ma20": c / df["ma20"] - 1, "ma20_ma60": df["ma20"] / df["ma60"] - 1,
        "rsi": df["rsi"],
    }
    if df["vol"].fillna(0).sum() > 0:
        feat["vol_ratio"] = df["vol"] / df["vol"].rolling(20).mean()

    parts: dict[str, pd.Series] = {
        "價格站上月線": _sign(c > df["ma20"]),
        "月線在季線之上": _sign(df["ma20"] > df["ma60"]),
    }
    chip_series: dict[str, pd.Series] = {}

    if market == "TW":
        df["f10"] = df["foreign"].rolling(10).sum()
        df["t10"] = df["trust"].rolling(10).sum()
        df["margin_chg10"] = df["margin"].pct_change(10, fill_method=None)
        parts["外資10日買超"] = _sign(df["f10"] > 0)
        parts["投信10日買超"] = _sign(df["t10"] > 0)
        has_margin = df["margin"].notna().sum() > 60
        if has_margin:
            parts["融資10日減少"] = _sign(df["margin_chg10"] < 0)
            feat["margin_chg10"] = df["margin_chg10"]
        feat["f10_ratio"] = df["f10"] / df["vol10"]
        feat["t10_ratio"] = df["t10"] / df["vol10"]
        chip_series = {"外資累計（張）": df["foreign"].cumsum(),
                       "投信累計（張）": df["trust"].cumsum()}
        if has_margin:
            chip_series["融資餘額（張）"] = df["margin"]
    elif market == "IDX":
        # 指數：沒有法人資料，很多也沒有成交量，只看趨勢與動能
        parts["RSI 高於 50"] = _sign(df["rsi"] > 50)
        parts["20日漲幅為正"] = _sign(c.pct_change(20) > 0)
        chip_series = {"RSI(14)": df["rsi"]}
    else:
        # 美股沒有每日法人資料：改用量能與相對強弱
        df["obv"] = (np.sign(c.diff()).fillna(0) * df["vol"]).cumsum()
        df["obv_chg10"] = df["obv"].diff(10)
        parts["OBV 10日上升"] = _sign(df["obv_chg10"] > 0)
        parts["RSI 高於 50"] = _sign(df["rsi"] > 50)
        if df["bench"].notna().sum() > 60:
            df["rs20"] = c.pct_change(20) - df["bench"].pct_change(20)
            parts["20日強於大盤"] = _sign(df["rs20"] > 0)
            feat["rs20"] = df["rs20"]
            chip_series["相對 S&P500（起點=100）"] = 100 * (c / c.iloc[0]) / (df["bench"] / df["bench"].iloc[0])
        feat["obv_chg10"] = df["obv_chg10"] / df["vol10"]
        chip_series["OBV 能量潮"] = df["obv"]

    n_parts = len(parts)
    df["score"] = sum(parts.values())
    df.loc[df["ma60"].isna(), "score"] = np.nan
    df["fwd"] = c.shift(-HORIZON) / c - 1

    # ---------- 滾動式（walk-forward）邏輯迴歸 ----------
    X = pd.DataFrame(feat)
    y = (df["fwd"] > 0).astype(float)
    valid = X.notna().all(axis=1) & np.isfinite(X).all(axis=1)
    df["prob"] = np.nan
    idx = np.arange(len(df))
    start_ml, step = max(150, len(df) // 3), 20
    for t in range(start_ml, len(df), step):
        tr = valid & (idx <= t - HORIZON - 1) & df["fwd"].notna()
        te = valid & (idx >= t) & (idx < t + step)
        if tr.sum() < 100 or te.sum() == 0 or y[tr].nunique() < 2:
            continue
        m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))
        m.fit(X[tr], y[tr])
        df.loc[te, "prob"] = m.predict_proba(X[te])[:, 1]
    oos = df["prob"].notna() & df["fwd"].notna()
    ml = {"n": int(oos.sum()), "acc": None, "base": None, "passed": False}
    if oos.sum() >= 60:
        ml["acc"] = float(((df.loc[oos, "prob"] > 0.5) == (df.loc[oos, "fwd"] > 0)).mean())
        base = float((df.loc[oos, "fwd"] > 0).mean())
        ml["base"] = max(base, 1 - base)       # 永遠猜多數方向的準確率
        ml["passed"] = ml["acc"] > ml["base"] + 0.03 and oos.sum() >= 150

    # ---------- 回測 ----------
    hi_th = max(2, n_parts - 2)                # 5 項時 ≥3 進場
    lo_th = -1
    start = int(df["score"].first_valid_index())
    cost = COSTS[market]
    eq_rule, trades = _backtest(df, hi_th, lo_th, cost)
    eq_rule = eq_rule / eq_rule[start]
    bh = (c / c.iloc[start]).to_numpy()

    # 依評分分組的未來報酬
    bins = [-n_parts - 1, -hi_th, hi_th - 1, n_parts + 1]
    labels = [f"偏空（≤{-hi_th}）", "中性", f"偏多（≥{hi_th}）"]
    bucket = pd.cut(df["score"], bins, labels=labels)
    by_bucket = []
    for b in labels:
        sel = (bucket == b) & df["fwd"].notna()
        by_bucket.append({"bucket": b, "n": int(sel.sum()),
                          "mean": float(df.loc[sel, "fwd"].mean()) if sel.sum() else np.nan,
                          "win": float((df.loc[sel, "fwd"] > 0).mean()) if sel.sum() else np.nan})

    # ---------- 預估扇形 ----------
    last = df.iloc[-1]
    sigma = float(df["logret"].tail(60).std())
    cur_b = bucket.iloc[-1]
    bmean = next((x["mean"] for x in by_bucket if x["bucket"] == cur_b), 0.0)
    bmean = 0.0 if not np.isfinite(bmean) else bmean
    drift = 0.5 * np.log(1 + bmean) / HORIZON
    k = np.arange(1, CONE_DAYS + 1)
    zq = {"p10": -1.2816, "p50": 0.0, "p90": 1.2816}
    fut = _future_dates(last["date"], CONE_DAYS)
    cone = pd.DataFrame({"date": fut, **{q: last["close"] * np.exp(drift * k + z * sigma * np.sqrt(k)) for q, z in zq.items()}})
    d10 = {q: float(np.exp(drift * HORIZON + z * sigma * np.sqrt(HORIZON)) - 1) for q, z in zq.items()}

    score = int(last["score"])
    if score >= hi_th:
        verdict, tone = "偏多・買入參考", "up"
    elif score <= -hi_th:
        verdict, tone = "偏空・賣出參考", "down"
    elif score < 0:
        verdict, tone = "中性偏弱・觀望", "flat"
    else:
        verdict, tone = "中性偏強・觀望", "flat"

    result = {
        "market": market, "df": df, "cone": cone,
        "asof": last["date"].strftime("%Y-%m-%d"),
        "close": float(last["close"]),
        "chg": float(c.iloc[-1] / c.iloc[-2] - 1),
        "score": score, "n_parts": n_parts, "verdict": verdict, "tone": tone,
        "parts": {k: int(v.iloc[-1]) for k, v in parts.items()},
        "prob": None if pd.isna(last["prob"]) else float(last["prob"]),
        "ml": ml, "d10": d10, "sigma": sigma,
        "stop": float(last["close"] * (1 + d10["p10"])),
        "bt": {"rule": _stats(eq_rule[start:]), "bh": _stats(bh[start:]), "trades": trades,
               "start": df.loc[start, "date"].strftime("%Y-%m-%d"),
               "eq": pd.DataFrame({"date": df["date"].iloc[start:], "策略": eq_rule[start:], "買進持有": bh[start:]}),
               "hi_th": hi_th, "lo_th": lo_th},
        "by_bucket": by_bucket,
        "chips": pd.DataFrame({"date": df["date"], **chip_series}),
        "last": last,
    }
    result["explain"] = explain(result)
    return result


def _future_dates(last_date: pd.Timestamp, n: int) -> list[pd.Timestamp]:
    return list(pd.bdate_range(last_date + pd.Timedelta(days=1), periods=n))


def _backtest(df: pd.DataFrame, hi_th: int, lo_th: int, cost: dict) -> tuple[np.ndarray, int]:
    sc = df["score"].fillna(0).to_numpy()
    o, cl = df["open"].to_numpy(), df["close"].to_numpy()
    pos, eq, trades = 0, 1.0, 0
    out = np.ones(len(df))
    for i in range(1, len(df)):
        want = pos
        if pos == 0 and sc[i - 1] >= hi_th:
            want = 1
        elif pos == 1 and sc[i - 1] <= lo_th:
            want = 0
        if want != pos:
            trades += 1
            if want == 1:
                eq *= (1 - cost["buy"]) * cl[i] / o[i]
            else:
                eq *= (o[i] / cl[i - 1]) * (1 - cost["sell"])
            pos = want
        elif pos == 1:
            eq *= cl[i] / cl[i - 1]
        out[i] = eq
    return out, trades


def _stats(eq: np.ndarray) -> dict:
    r = np.diff(eq) / eq[:-1]
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    sd = r.std()
    return {"total": float(eq[-1] - 1), "maxdd": float(dd),
            "sharpe": float(r.mean() / sd * np.sqrt(250)) if sd > 0 else 0.0}


# ----------------------------------------------------------------------------
# 白話解讀（規則式，之後可換成 LLM）
# ----------------------------------------------------------------------------
def explain(r: dict) -> str:
    last, parts, market = r["last"], r["parts"], r["market"]
    pos = [label(k, v) for k, v in parts.items() if v > 0]
    neg = [label(k, v) for k, v in parts.items() if v < 0]
    s = [f"目前評分 {r['score']:+d}（滿分 ±{r['n_parts']}），判讀為「{r['verdict']}」。"]
    if pos:
        s.append("正面因素：" + "、".join(pos) + "。")
    if neg:
        s.append("負面因素：" + "、".join(neg) + "。")
    c20 = r["df"]["close"].iloc[-1] / r["df"]["close"].iloc[-21] - 1 if len(r["df"]) > 21 else 0
    if market == "TW":
        f10, t10 = last.get("f10", np.nan), last.get("t10", np.nan)
        if pd.notna(f10):
            s.append(f"外資近 10 日{'買' if f10 > 0 else '賣'}超約 {abs(f10):,.0f} 張，投信{'買' if t10 > 0 else '賣'}超約 {abs(t10):,.0f} 張。")
        mc = last.get("margin_chg10", np.nan)
        if pd.notna(mc):
            s.append(f"融資 10 日{'增加' if mc > 0 else '減少'} {abs(mc) * 100:.1f}%"
                     + ("，散戶追價，籌碼偏不安定。" if mc > 0.05 else "，籌碼相對安定。" if mc < 0 else "。"))
    elif market == "IDX":
        s.append(f"RSI 為 {last['rsi']:.0f}，近 20 日{'上漲' if c20 > 0 else '下跌'} {abs(c20) * 100:.1f}%。")
    else:
        rs = last.get("rs20", np.nan)
        if pd.notna(rs):
            s.append(f"近 20 日{'強' if rs > 0 else '弱'}於大盤 {abs(rs) * 100:.1f} 個百分點。")
        s.append(f"RSI 為 {last['rsi']:.0f}。")
    d = r["d10"]
    s.append(f"依近 60 日波動度，未來 10 日 80% 機率落在 {d['p10'] * 100:+.1f}% 到 {d['p90'] * 100:+.1f}% 之間；"
             f"若收盤跌破 {r['stop']:,.2f}，代表走勢超出悲觀預估，應重新評估。")
    bt = r["bt"]
    if bt["rule"]["total"] < bt["bh"]["total"]:
        s.append(f"提醒：這套評分在回測期間（{bt['start']} 起）報酬輸給買進持有，訊號只能當參考。")
    return "".join(s)
