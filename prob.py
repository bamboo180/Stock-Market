"""上漲／下跌機率：把技術、籌碼、營收、新聞、隔夜美股整合成一張動態機率表。

做法（每一項都標出加減了幾個百分點，看得到機率是怎麼來的）：
1. 起點：這檔股票歷史上「明天上漲」與「10 天後上漲」的比例
2. 技術與籌碼條件：用這檔股票自己的歷史算「條件成立時」的上漲比例，扣掉起點，
   再依樣本數打折（樣本越少越不相信）。多個條件彼此相關，總和再打五折避免重複計算
3. 營收、新聞：沒有夠長的歷史可以驗證，用固定的小權重，單項最多 ±6 個百分點
4. 明日開盤（台股）：用過去兩年「前一晚那斯達克、費半漲跌」對「台股隔天開盤跳空」做迴歸，
   再乘上這檔股票相對大盤的敏感度（beta）
最後把機率限制在 20%～80%，因為短期股價本來就很難預測，太極端的數字一定是過度自信。
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import requests

import live

LO, HI = 0.20, 0.80
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _clip(p: float) -> float:
    return float(min(HI, max(LO, p)))


# ----------------------------------------------------------------------------
# 資料
# ----------------------------------------------------------------------------
def revenue(code: str, token: str | None = None) -> pd.DataFrame:
    try:
        h = {"Authorization": f"Bearer {token}"} if token else {}
        start = (dt.date.today() - dt.timedelta(days=800)).isoformat()
        j = requests.get(FINMIND, params={"dataset": "TaiwanStockMonthRevenue", "data_id": code, "start_date": start},
                         headers=h, timeout=20).json()
        d = pd.DataFrame(j.get("data", []))
        if d.empty:
            return d
        d["ym"] = d["revenue_year"] * 100 + d["revenue_month"]
        return d.sort_values("ym").drop_duplicates("ym").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def overnight() -> dict:
    """台股開盤跳空模型 + 最新一晚的美股表現。"""
    import yfinance as yf
    raw = yf.download(["^TWII", "^IXIC", "^SOX", "NQ=F", "TSM"], period="2y", interval="1d",
                      group_by="ticker", auto_adjust=False, progress=False, threads=True)

    def col(sym, field):
        try:
            return raw[sym][field].dropna()
        except Exception:
            return pd.Series(dtype=float)

    tw_o, tw_c = col("^TWII", "Open"), col("^TWII", "Close")
    ix, sox = col("^IXIC", "Close").pct_change(), col("^SOX", "Close").pct_change()
    out: dict = {"ok": False}
    if len(tw_c) > 120 and len(ix) > 120:
        gap = (tw_o / tw_c.shift(1) - 1).dropna()
        rows = []
        for d, g in gap.items():
            prev_us = ix.index[ix.index < d]          # 台股 d 日開盤前最後一個美股交易日
            if len(prev_us) == 0:
                continue
            u = prev_us[-1]
            rows.append({"gap": g, "ix": ix.get(u, np.nan), "sox": sox.get(u, np.nan)})
        m = pd.DataFrame(rows).dropna()
        if len(m) > 100:
            X = np.c_[np.ones(len(m)), m["ix"], m["sox"]]
            beta, *_ = np.linalg.lstsq(X, m["gap"].to_numpy(), rcond=None)
            resid = m["gap"].to_numpy() - X @ beta
            pred_dir = np.sign(X @ beta)
            out.update(ok=True, coef=beta.tolist(), sd=float(resid.std()),
                       hit=float((pred_dir == np.sign(m["gap"])).mean()), n=len(m),
                       r2=float(1 - resid.var() / m["gap"].var()))
    out["twii"] = pd.DataFrame({"Open": tw_o, "Close": tw_c}).dropna()
    out["tw_last"] = tw_c.index[-1].date() if len(tw_c) else None
    out["us_last"] = ix.index[-1].date() if len(ix) else None
    out["ix"] = float(ix.iloc[-1]) if len(ix) else 0.0
    out["sox"] = float(sox.iloc[-1]) if len(sox) else 0.0
    nq = col("NQ=F", "Close")
    out["nq"] = float(nq.iloc[-1] / nq.iloc[-2] - 1) if len(nq) > 1 else 0.0
    tsm = col("TSM", "Close")
    out["tsm"] = float(tsm.iloc[-1] / tsm.iloc[-2] - 1) if len(tsm) > 1 else 0.0
    return out


# ----------------------------------------------------------------------------
# 各類因子
# ----------------------------------------------------------------------------
def _conditions(df: pd.DataFrame, market: str) -> dict[str, pd.Series]:
    c = df["close"]
    cond = {
        "股價在月線之上": c > df["ma20"],
        "月線在季線之上": df["ma20"] > df["ma60"],
        "RSI 過熱（>70）": df["rsi"] > 70,
        "RSI 超賣（<30）": df["rsi"] < 30,
        "近 5 日上漲": c.pct_change(5) > 0,
    }
    if df["vol"].fillna(0).sum() > 0:
        vr = df["vol"] / df["vol"].rolling(20).mean()
        cond["今日爆量（>2倍均量）"] = vr > 2
    if market == "TW":
        if "f10" in df:
            cond["外資近 10 日買超"] = df["f10"] > 0
            cond["外資近 3 日買超"] = df["foreign"].rolling(3).sum() > 0
        if "t10" in df:
            cond["投信近 10 日買超"] = df["t10"] > 0
        if "margin_chg10" in df and df["margin"].notna().sum() > 60:
            cond["融資 10 日增加超過 5%"] = df["margin_chg10"] > 0.05
    if market == "US" and "rs20" in df:
        cond["20 日強於大盤"] = df["rs20"] > 0
    return cond


def _empirical(df: pd.DataFrame, cond: dict[str, pd.Series], horizon: int) -> tuple[float, list[dict]]:
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    known = fwd.notna() & df["ma60"].notna()
    base = float((fwd[known] > 0).mean())
    rows = []
    for name, s in cond.items():
        now = bool(s.iloc[-1]) if pd.notna(s.iloc[-1]) else False
        sel = known & (s.fillna(False) == now)
        n = int(sel.sum())
        if n < 15:
            continue
        win = float((fwd[sel] > 0).mean())
        k = 60 if horizon > 1 else 120                      # 打折常數：樣本 = k 時只信一半
        effect = (win - base) * n / (n + k)
        if abs(effect) < 0.003:
            continue
        rows.append({"group": "技術／籌碼", "name": name, "value": "是" if now else "否",
                     "detail": f"歷史上同樣情況 {n} 次，上漲 {win * 100:.0f}%", "pp": effect})
    return base, rows


def _revenue_rows(rev: pd.DataFrame) -> list[dict]:
    if rev is None or len(rev) < 13:
        return []
    last = rev.iloc[-1]
    ym = int(last["ym"])
    prev_y = rev[rev["ym"] == ym - 100]
    rows = []
    if len(prev_y):
        yoy = last["revenue"] / prev_y.iloc[0]["revenue"] - 1
        pp = float(np.clip(yoy * 0.10, -0.05, 0.05))           # 年增 20% → +2 個百分點
        rows.append({"group": "基本面", "name": f"{int(last['revenue_month'])} 月營收年增率",
                     "value": f"{yoy * 100:+.1f}%", "detail": f"營收 {last['revenue'] / 1e8:,.0f} 億元", "pp": pp})
        # 年增率是否加速
        yoys = []
        for i in range(max(0, len(rev) - 4), len(rev) - 1):
            r_ = rev.iloc[i]
            p_ = rev[rev["ym"] == int(r_["ym"]) - 100]
            if len(p_):
                yoys.append(r_["revenue"] / p_.iloc[0]["revenue"] - 1)
        if yoys:
            accel = yoy - float(np.mean(yoys))
            rows.append({"group": "基本面", "name": "營收成長動能（vs 前 3 月平均年增）",
                         "value": f"{accel * 100:+.1f} 個百分點", "detail": "加速為正、放緩為負",
                         "pp": float(np.clip(accel * 0.10, -0.03, 0.03))})
    return rows


def _news_rows(items: list[dict], days: int) -> list[dict]:
    if not items:
        return []
    now = max(i["time"] for i in items)
    recent = [i for i in items if (now - i["time"]).days < days]
    pos = sum(1 for i in recent if i["tone"] == "pos")
    neg = sum(1 for i in recent if i["tone"] == "neg")
    official = sum(1 for i in recent if i.get("kind") == "official")
    rows = []
    if pos + neg:
        pp = float(np.clip((pos - neg) / math.sqrt(pos + neg) * 0.02, -0.06, 0.06))
        rows.append({"group": "消息面", "name": f"近 {days} 天新聞標題利多／利空",
                     "value": f"{pos} 利多／{neg} 利空", "detail": f"共 {len(recent)} 則", "pp": pp})
    risk = [i for i in recent if "風險" in i.get("tags", [])]
    if risk:
        rows.append({"group": "消息面", "name": "風險類新聞（處置、訴訟、調查…）",
                     "value": f"{len(risk)} 則", "detail": risk[0]["title"][:30], "pp": -0.02 * min(len(risk), 2)})
    if official:
        rows.append({"group": "消息面", "name": "官方重大訊息", "value": f"{official} 則",
                     "detail": "內容請看右側消息欄（不計入機率）", "pp": 0.0})
    return rows


# ----------------------------------------------------------------------------
# 主函式
# ----------------------------------------------------------------------------
def estimate(r: dict, code: str, items: list[dict] | None = None, rev: pd.DataFrame | None = None,
             ov: dict | None = None, index_df: pd.DataFrame | None = None) -> dict:
    df, market = r["df"], r["market"]
    cond = _conditions(df, market)
    out = {}

    # ---------- 未來 10 個交易日 ----------
    base10, tech10 = _empirical(df, cond, 10)
    rows10 = [{"group": "起點", "name": "這檔股票歷史上 10 天後上漲的比例", "value": f"{base10 * 100:.0f}%",
               "detail": f"{len(df)} 個交易日", "pp": 0.0}]
    tech_sum = sum(x["pp"] for x in tech10)
    for x in tech10:
        x["pp"] *= 0.5                                   # 條件彼此相關，總和打五折
    rows10 += tech10 + _revenue_rows(rev) + _news_rows(items or [], 7)
    p10 = _clip(base10 + sum(x["pp"] for x in rows10))
    out["h10"] = {"p_up": p10, "base": base10, "rows": rows10}

    # ---------- 下一個交易日 ----------
    base1, tech1 = _empirical(df, cond, 1)
    rows1 = [{"group": "起點", "name": "這檔股票歷史上隔天上漲的比例", "value": f"{base1 * 100:.0f}%",
              "detail": "", "pp": 0.0}]
    for x in tech1:
        x["pp"] *= 0.5
    rows1 += tech1 + _news_rows(items or [], 2)
    gap_mu, gap_sd, gap_note = None, None, ""

    if ov and ov.get("ok") and (market == "TW" or code in ("^TWII", "^TWOII")):
        tw_last = df["date"].iloc[-1].date()
        now = live.now("TW")
        if now.weekday() < 5 and now.time() >= dt.time(9, 0):
            tw_last = max(tw_last, now.date())          # 今天已開盤：下一個交易日是明天
        us_after = bool(ov["us_last"]) and ov["us_last"] >= tw_last
        if us_after:
            ix_r, sox_r, src = ov["ix"], ov["sox"], f"美股 {ov['us_last']:%m/%d} 收盤"
        else:   # 美股還沒收盤：用那斯達克期貨目前漲跌代替
            ix_r, sox_r, src = ov["nq"], ov["nq"] * 1.3, "那斯達克期貨（盤中）"
        b0, b_ix, b_sox = ov["coef"]
        idx_gap = b0 + b_ix * ix_r + b_sox * sox_r
        beta, sd_stock = 1.0, ov["sd"]
        index_df = index_df if index_df is not None else ov.get("twii")
        if index_df is not None and len(index_df) and code not in ("^TWII", "^TWOII"):
            s_gap = (df.set_index("date")["open"] / df.set_index("date")["close"].shift(1) - 1)
            i_gap = (index_df["Open"] / index_df["Close"].shift(1) - 1)
            j = pd.concat([s_gap, i_gap], axis=1, join="inner").dropna().tail(250)
            if len(j) > 60 and j.iloc[:, 1].var() > 0:
                beta = float(np.clip(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / j.iloc[:, 1].var(), 0, 2.5))
                sd_stock = float((j.iloc[:, 0] - beta * j.iloc[:, 1]).std())
        gap_mu = beta * idx_gap
        gap_sd = math.sqrt((beta * ov["sd"]) ** 2 + sd_stock ** 2) if code not in ("^TWII", "^TWOII") else ov["sd"]
        # 對「收盤漲跌」的影響：開盤跳空相對於一整天的波動有多大
        day_sd = float(df["close"].pct_change().tail(60).std()) or gap_sd
        p_gap = _phi(gap_mu / day_sd) if day_sd > 0 else 0.5
        rows1.append({"group": "隔夜美股", "name": f"{src}：那斯達克 {ix_r * 100:+.2f}%、費半 {sox_r * 100:+.2f}%",
                      "value": f"預估開盤 {gap_mu * 100:+.2f}%",
                      "detail": f"迴歸 {ov['n']} 天，方向命中 {ov['hit'] * 100:.0f}%，個股 beta {beta:.2f}",
                      "pp": float(np.clip(p_gap - 0.5, -0.15, 0.15))})
        if code == "2330" and us_after:
            rows1.append({"group": "隔夜美股", "name": "台積電 ADR", "value": f"{ov['tsm'] * 100:+.2f}%",
                          "detail": "已反映在上方費半裡，僅供參考", "pp": 0.0})
        gap_note = src
    elif market in ("US", "IDX") and ov:
        rows1.append({"group": "期貨", "name": "那斯達克期貨目前漲跌", "value": f"{ov['nq'] * 100:+.2f}%",
                      "detail": "美股盤前方向參考", "pp": float(np.clip(ov["nq"] * 8, -0.08, 0.08))})

    p1 = _clip(base1 + sum(x["pp"] for x in rows1))
    out["next"] = {"p_up": p1, "base": base1, "rows": rows1, "gap_mu": gap_mu, "gap_sd": gap_sd, "gap_src": gap_note}
    out["tech_raw"] = tech_sum
    out["summary"] = _summary(out, r)
    return out


def _summary(o: dict, r: dict) -> str:
    n, h = o["next"], o["h10"]
    def lean(p):
        return "偏多" if p >= 0.56 else "偏空" if p <= 0.44 else "多空接近"
    s = [f"下一個交易日上漲機率 {n['p_up'] * 100:.0f}%（{lean(n['p_up'])}），"
         f"未來 10 個交易日上漲機率 {h['p_up'] * 100:.0f}%（{lean(h['p_up'])}）。"]
    if n.get("gap_mu") is not None:
        lo, hi = n["gap_mu"] - n["gap_sd"], n["gap_mu"] + n["gap_sd"]
        s.append(f"依{n['gap_src']}推估，明天開盤約 {n['gap_mu'] * 100:+.2f}%（多數情況落在 {lo * 100:+.1f}% ～ {hi * 100:+.1f}%）。")
    movers = sorted([x for x in h["rows"] if x["pp"]], key=lambda x: -abs(x["pp"]))[:3]
    if movers:
        s.append("影響最大的因素：" + "、".join(f"{x['name']}（{x['pp'] * 100:+.1f}）" for x in movers) + "。")
    d = r["d10"]
    s.append(f"依近 60 日波動度，未來 10 日 80% 機率落在 {d['p10'] * 100:+.1f}% 到 {d['p90'] * 100:+.1f}% 之間。")
    return "".join(s)


def table(h: dict) -> pd.DataFrame:
    rows = [{"類別": x["group"], "因素": x["name"], "目前": x["value"], "說明": x["detail"],
             "影響（百分點）": x["pp"] * 100} for x in h["rows"]]
    rows.append({"類別": "合計", "因素": "上漲機率（限制在 20%～80%）", "目前": f"{h['p_up'] * 100:.0f}%",
                 "說明": f"下跌機率 {(1 - h['p_up']) * 100:.0f}%", "影響（百分點）": np.nan})
    return pd.DataFrame(rows)
