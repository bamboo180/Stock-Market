"""盤後整理：台股、美股。

台股：證交所每日收盤行情 MI_INDEX（指數、成交金額、漲跌家數、全部上市個股，收盤後當天就有）
      ＋ 櫃買中心開放資料（日期與上市相同時才併入）
      ＋ FinMind 大盤三大法人買賣金額、融資融券餘額
美股：Yahoo Finance 指數、11 大類股 ETF、漲跌幅排行、大型權值股、台積電 ADR 溢價
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd
import requests

import indices
import live

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _f(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return np.nan


# ============================================================================
# 台股
# ============================================================================
def _mi_index(date: dt.date) -> dict | None:
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    try:
        j = requests.get(url, params={"date": date.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
                         headers=UA, timeout=20).json()
    except Exception:
        return None
    return j if j.get("stat") == "OK" and j.get("tables") else None


def _table(j: dict, keyword: str) -> dict | None:
    return next((t for t in j["tables"] if keyword in str(t.get("title", ""))), None)


def _signed(sign_html: str, value) -> float:
    v = _f(value)
    return -v if "-" in re.sub(r"<[^>]+>", "", str(sign_html)) or "green" in str(sign_html) else v


def _tpex_quotes(date_str: str) -> pd.DataFrame:
    """櫃買中心上櫃收盤行情（開放資料）。日期跟上市不同就不用，避免混到前一天。"""
    try:
        rows = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                            headers=UA, timeout=20).json()
    except Exception:
        return pd.DataFrame()
    out = []
    for r in rows:
        k = {key.lower(): key for key in r}
        get = lambda *names: next((r[k[n.lower()]] for n in names if n.lower() in k), None)
        d = str(get("Date") or "")
        if d and len(d) == 7:                       # 民國年 1151029
            d = str(int(d[:3]) + 1911) + d[3:]
        if d != date_str:
            return pd.DataFrame()
        code = str(get("SecuritiesCompanyCode", "Code") or "")
        close, chg = _f(get("Close", "ClosingPrice")), _f(get("Change"))
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code) or not close or np.isnan(close):
            continue
        out.append({"code": code, "name": get("CompanyName", "Name"), "close": close, "change": chg,
                    "value": _f(get("TransactionAmount", "TradeValue")), "market": "上櫃"})
    return pd.DataFrame(out)


def _finmind_total(dataset: str, date: str, token: str | None) -> pd.DataFrame:
    try:
        h = {"Authorization": f"Bearer {token}"} if token else {}
        j = requests.get(FINMIND, params={"dataset": dataset, "start_date": date, "end_date": date},
                         headers=h, timeout=20).json()
        return pd.DataFrame(j.get("data", []))
    except Exception:
        return pd.DataFrame()


def tw(groups: pd.DataFrame | None = None, token: str | None = None, favs: list[str] | None = None) -> dict:
    """groups: 代號→產業對照（symbols.load_tw 的結果），用來算類股表現。"""
    j, day = None, dt.datetime.now(live.TZ["TW"]).date()
    for _ in range(7):                               # 往前找最近一個有資料的交易日
        j = _mi_index(day)
        if j:
            break
        day -= dt.timedelta(days=1)
    if not j:
        raise RuntimeError("證交所收盤行情暫時取不到，請稍後再試。")
    date_str = j["date"]
    res: dict = {"date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"}

    # 指數
    idx = {}
    for t in j["tables"]:
        if "價格指數" in str(t.get("title", "")):
            for row in t.get("data", []):
                # 漲跌點數不帶正負號（看 row[2] 的符號），漲跌百分比本身已帶正負號
                idx[row[0]] = {"close": _f(row[1]), "chg": _signed(row[2], row[3]), "pct": _f(row[4]) / 100}
    res["taiex"] = idx.get("發行量加權股價指數")
    q = live.quotes_mis(["otc_o00.tw"]).get("o00")
    res["otc"] = ({"close": q["price"], "chg": q["price"] - q["prev"], "pct": q["price"] / q["prev"] - 1}
                  if q and q.get("prev") else None)
    picks = ["電子類指數", "半導體類指數", "金融保險類指數", "航運類指數", "電腦及週邊設備類指數", "生技醫療類指數"]
    res["sub_idx"] = [{"name": n.replace("類指數", ""), **idx[n]} for n in picks if n in idx]

    # 成交金額、漲跌家數
    st_tbl = _table(j, "大盤統計")
    if st_tbl:
        total = next((r for r in st_tbl["data"] if "總計" in r[0]), None)
        res["turnover"] = _f(total[1]) if total else None
    ud = _table(j, "漲跌證券數")
    if ud:
        def cnt(label):
            row = next((r for r in ud["data"] if r[0].startswith(label)), None)
            if not row:
                return 0, 0
            m = re.match(r"([\d,]+)(?:\((\d+)\))?", row[2])
            return int(m.group(1).replace(",", "")), int(m.group(2) or 0)
        (res["up"], res["limit_up"]), (res["down"], res["limit_down"]) = cnt("上漲"), cnt("下跌")
        res["flat"] = cnt("持平")[0]

    # 個股
    qt = _table(j, "每日收盤行情")
    rows = []
    for r in (qt or {}).get("data", []):
        code, close = r[0].strip(), _f(r[8])
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code) or np.isnan(close):
            continue
        rows.append({"code": code, "name": r[1].strip(), "close": close, "change": _signed(r[9], r[10]),
                     "value": _f(r[4]), "market": "上市"})
    df = pd.DataFrame(rows)
    otc = _tpex_quotes(date_str)
    res["has_otc"] = not otc.empty
    df = pd.concat([df, otc], ignore_index=True)
    df["pct"] = df["change"] / (df["close"] - df["change"])
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["pct"])
    stocks = df[~df["code"].str.startswith("0")]                  # 排行不含 ETF
    liquid = stocks[stocks["value"] >= 5e7]                       # 成交金額 5,000 萬以上，避免冷門股
    cols = ["code", "name", "close", "pct", "value", "market"]
    res["gainers"] = liquid.nlargest(10, "pct")[cols]
    res["losers"] = liquid.nsmallest(10, "pct")[cols]
    res["top_value"] = df.nlargest(10, "value")[cols]

    # 類股（成交金額加權平均漲跌）
    if groups is not None and len(groups):
        g = stocks.merge(groups[["code", "group"]], on="code", how="left").dropna(subset=["group"])
        g = g[~g["group"].isin(["ETF", "ETN", "上櫃ETF", "其他", "所有證券"])]
        sec = g.groupby("group").apply(
            lambda x: pd.Series({"pct": np.average(x["pct"], weights=x["value"].clip(lower=1)),
                                 "value": x["value"].sum(), "n": len(x)}), include_groups=False)
        sec = sec[sec["n"] >= 5].sort_values("pct", ascending=False)
        res["sectors"] = sec.reset_index()

    # 三大法人、融資
    d = res["date"]
    inst = _finmind_total("TaiwanStockTotalInstitutionalInvestors", d, token)
    if not inst.empty:
        net = (inst["buy"] - inst["sell"]) / 1e8
        by = dict(zip(inst["name"], net))
        res["inst"] = {"外資": by.get("Foreign_Investor", 0) + by.get("Foreign_Dealer_Self", 0),
                       "投信": by.get("Investment_Trust", 0),
                       "自營商": by.get("Dealer_self", 0) + by.get("Dealer_Hedging", 0)}
        res["inst"]["合計"] = sum(res["inst"].values())
    mg = _finmind_total("TaiwanStockTotalMarginPurchaseShortSale", d, token)
    if not mg.empty:
        m = mg.set_index("name")
        if "MarginPurchaseMoney" in m.index:
            r_ = m.loc["MarginPurchaseMoney"]
            res["margin"] = {"balance": r_["TodayBalance"] / 1e8, "chg": (r_["TodayBalance"] - r_["YesBalance"]) / 1e8}

    if favs:
        f = df[df["code"].isin(favs)][cols]
        res["favs"] = f.sort_values("pct", ascending=False)
    res["summary"] = _tw_text(res)
    return res


def _tw_text(r: dict) -> list[str]:
    out = []
    t = r.get("taiex")
    if t:
        line = f"加權指數{'上漲' if t['chg'] >= 0 else '下跌'} {abs(t['chg']):,.2f} 點（{t['pct'] * 100:+.2f}%），收 {t['close']:,.2f}"
        if r.get("turnover"):
            line += f"，成交金額 {r['turnover'] / 1e8:,.0f} 億元"
        out.append(line + "。")
    if r.get("otc"):
        o = r["otc"]
        out.append(f"櫃買指數 {o['pct'] * 100:+.2f}%，收 {o['close']:,.2f}。")
    if "up" in r:
        breadth = "多數個股上漲" if r["up"] > r["down"] * 1.3 else "多數個股下跌" if r["down"] > r["up"] * 1.3 else "漲跌家數接近"
        out.append(f"上市股票上漲 {r['up']} 家（漲停 {r['limit_up']}）、下跌 {r['down']} 家（跌停 {r['limit_down']}），{breadth}。")
    if r.get("inst"):
        i = r["inst"]
        out.append("三大法人" + ("買超" if i["合計"] >= 0 else "賣超") + f" {abs(i['合計']):,.1f} 億："
                   + "、".join(f"{k}{'買' if v >= 0 else '賣'}超 {abs(v):,.1f} 億" for k, v in i.items() if k != "合計") + "。")
    if r.get("margin"):
        m = r["margin"]
        out.append(f"融資餘額 {m['balance']:,.0f} 億元，{'增加' if m['chg'] >= 0 else '減少'} {abs(m['chg']):,.1f} 億。")
    sec = r.get("sectors")
    if sec is not None and len(sec) >= 6:
        out.append("強勢類股：" + "、".join(f"{s.group} {s.pct * 100:+.1f}%" for s in sec.head(3).itertuples())
                   + "；弱勢類股：" + "、".join(f"{s.group} {s.pct * 100:+.1f}%" for s in sec.tail(3)[::-1].itertuples()) + "。")
    return out


# ============================================================================
# 美股
# ============================================================================
US_INDICES = ["^GSPC", "^IXIC", "^DJI", "^SOX", "^RUT", "^VIX", "^TNX", "DX-Y.NYB"]
SECTORS = {"XLK": "科技", "XLC": "通訊服務", "XLY": "非必需消費", "XLF": "金融", "XLV": "醫療保健", "XLI": "工業",
           "XLE": "能源", "XLB": "原物料", "XLP": "必需消費", "XLU": "公用事業", "XLRE": "房地產"}
MEGA = {"AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "GOOGL": "Alphabet", "AMZN": "亞馬遜", "META": "Meta",
        "TSLA": "特斯拉", "AVGO": "博通", "TSM": "台積電 ADR", "AMD": "超微", "NFLX": "網飛", "ORCL": "甲骨文",
        "PLTR": "Palantir", "MU": "美光", "INTC": "英特爾", "QCOM": "高通", "ASML": "艾司摩爾", "SMCI": "美超微",
        "LLY": "禮來", "JPM": "摩根大通", "COST": "好市多", "WMT": "沃爾瑪"}


def _changes(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(symbols, period="10d", interval="1d", group_by="ticker", auto_adjust=False,
                      progress=False, threads=True)
    rows = []
    for s in symbols:
        try:
            c = (raw[s]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).dropna()
        except Exception:
            continue
        if len(c) >= 2:
            rows.append({"code": s, "close": float(c.iloc[-1]), "pct": float(c.iloc[-1] / c.iloc[-2] - 1),
                         "date": c.index[-1].strftime("%Y-%m-%d")})
    return pd.DataFrame(rows)


def _screen(name: str, n: int = 10) -> pd.DataFrame:
    try:
        import yfinance as yf
        qs = yf.screen(name, count=n).get("quotes", [])
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame([{"code": q.get("symbol"), "name": q.get("shortName") or q.get("longName", ""),
                          "close": q.get("regularMarketPrice"), "pct": (q.get("regularMarketChangePercent") or 0) / 100,
                          "volume": q.get("regularMarketVolume"), "mcap": q.get("marketCap")} for q in qs])


def us(favs: list[str] | None = None) -> dict:
    names = dict(indices.CATALOG)
    names["DX-Y.NYB"] = ("美元指數", "US", None)
    idx = _changes(US_INDICES)
    if idx.empty:
        raise RuntimeError("Yahoo Finance 暫時取不到美股資料，請稍後再試。")
    idx["name"] = idx["code"].map(lambda s: names.get(s, (s,))[0])
    res: dict = {"date": idx["date"].max(), "indices": idx}

    sec = _changes(list(SECTORS))
    if not sec.empty:
        sec["name"] = sec["code"].map(SECTORS)
        res["sectors"] = sec.sort_values("pct", ascending=False)
    mega = _changes(list(MEGA))
    if not mega.empty:
        mega["name"] = mega["code"].map(MEGA)
        res["mega"] = mega.sort_values("pct", ascending=False)

    g, l, a = _screen("day_gainers"), _screen("day_losers"), _screen("most_actives")
    if not g.empty:
        res["gainers"], res["losers"], res["actives"] = g, l, a

    # 台積電 ADR 溢價：1 股 ADR = 5 股台股
    try:
        tsm = mega.set_index("code").loc["TSM", "close"]
        fx = _changes(["TWD=X"]).iloc[-1]["close"]
        tw2330 = live.quotes_tw(["2330"]).get("2330", {}).get("price")
        if tw2330:
            res["adr"] = {"tsm": tsm, "fx": fx, "tw": tw2330, "premium": tsm * fx / 5 / tw2330 - 1}
    except Exception:
        pass

    if favs:
        us_favs = [f for f in favs if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", f) and not f.isdigit()]
        if us_favs:
            res["favs"] = _changes(us_favs).sort_values("pct", ascending=False)
    res["summary"] = _us_text(res)
    return res


def _us_text(r: dict) -> list[str]:
    out = []
    i = r["indices"].set_index("code")
    parts = [f"{i.loc[s, 'name']} {i.loc[s, 'pct'] * 100:+.2f}%" for s in ["^DJI", "^GSPC", "^IXIC", "^SOX"] if s in i.index]
    if parts:
        out.append("四大指數：" + "、".join(parts) + "。")
    if "^VIX" in i.index:
        v = i.loc["^VIX", "close"]
        mood = "市場情緒緊張" if v >= 25 else "市場情緒偏謹慎" if v >= 18 else "市場情緒平穩"
        out.append(f"VIX 恐慌指數 {v:.1f}（{i.loc['^VIX', 'pct'] * 100:+.1f}%），{mood}。")
    if "^TNX" in i.index:
        out.append(f"美國 10 年期公債殖利率 {i.loc['^TNX', 'close']:.2f}%。")
    sec = r.get("sectors")
    if sec is not None and len(sec):
        out.append(f"類股最強：{sec.iloc[0]['name']} {sec.iloc[0]['pct'] * 100:+.2f}%，"
                   f"最弱：{sec.iloc[-1]['name']} {sec.iloc[-1]['pct'] * 100:+.2f}%。")
    mega = r.get("mega")
    if mega is not None and len(mega):
        top, bot = mega.iloc[0], mega.iloc[-1]
        out.append(f"大型股中 {top['name']} 表現最好（{top['pct'] * 100:+.2f}%），{bot['name']} 最弱（{bot['pct'] * 100:+.2f}%）。")
    if r.get("adr"):
        a = r["adr"]
        out.append(f"台積電 ADR {a['tsm']:,.2f} 美元，換算台股價 {a['tsm'] * a['fx'] / 5:,.0f} 元，"
                   f"較台股 {a['tw']:,.0f} 元{'溢價' if a['premium'] >= 0 else '折價'} {abs(a['premium']) * 100:.1f}%。")
    return out
