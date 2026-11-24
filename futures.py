"""台指期近月（全盤）：即時報價、日 K、三大法人未平倉。

代號在 App 裡用 "TXF=F"（含 "=" 會被當成指數類，走指數的分析流程）。
  即時報價：期交所行情資訊網（mis.taifex.com.tw），日盤 08:45～13:45、夜盤 15:00～隔天 05:00
  日 K：FinMind TaiwanFuturesDaily（TX）。「全盤」＝前一晚夜盤＋當天日盤合成一根：
        開盤取夜盤開盤、最高最低取兩段的極值、收盤取日盤收盤、量相加
  法人：FinMind TaiwanFuturesInstitutionalInvestors（TX）外資／投信／自營商的多空未平倉
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd
import requests

SYMBOL = "TXF=F"
NAME = "台指期近全"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Content-Type": "application/json;charset=UTF-8", "Origin": "https://mis.taifex.com.tw",
      "Referer": "https://mis.taifex.com.tw/futures/RegularSession/EquityIndices/FuturesDomestic/"}
MIS_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
STATUS: dict[str, str] = {}
TZ = dt.timezone(dt.timedelta(hours=8))


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return np.nan


def _pick(row: dict, *names):
    for n in names:
        if n in row and row[n] not in (None, "", "-", "--"):
            return row[n]
    return None


def _mis(market_type: str) -> list[dict]:
    """market_type：'0' 日盤（一般交易時段）、'1' 夜盤（盤後交易時段）。"""
    body = {"MarketType": market_type, "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
            "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}
    r = requests.post(MIS_URL, json=body, headers=UA, timeout=10)
    j = r.json()
    rows = (j.get("RtData") or {}).get("QuoteList") or j.get("QuoteList") or []
    return [x for x in rows if isinstance(x, dict)]


def _near(rows: list[dict]) -> dict | None:
    """近月：排除價差單（代號有 /），取到期月份最早、而且有價格的那個。"""
    cand = []
    for x in rows:
        sid = str(_pick(x, "SymbolID", "Symbol") or "")
        if not sid or "/" in sid or not sid.upper().startswith("TXF"):
            continue
        px = _num(_pick(x, "CLastPrice", "LastPrice", "CDealPrice"))
        ref = _num(_pick(x, "CRefPrice", "RefPrice"))
        if np.isnan(px) and np.isnan(ref):
            continue
        cand.append(x)
    if not cand:
        return None
    # 依代號裡的月份碼排序：TXF + 月份字母（A＝1 月…L＝12 月）+ 年份個位數，例如 TXFL6＝2026/12、TXFA7＝2027/01
    def key(x):
        m = re.match(r"TXF([A-L])(\d)", str(_pick(x, "SymbolID", "Symbol") or "").upper())
        if not m:
            return 10**6
        y = 2020 + int(m.group(2))
        if y < dt.date.today().year - 1:          # 年份個位數跨十年（例如 2030 寫成 0）
            y += 10
        return y * 12 + "ABCDEFGHIJKL".index(m.group(1))
    cand.sort(key=key)
    return cand[0]


def realtime(now: dt.datetime | None = None) -> dict | None:
    """近月即時報價。夜盤時段（15:00～05:00）優先用夜盤，其他時間用日盤；抓不到就用另一段。"""
    now = now or dt.datetime.now(TZ)
    t = now.hour * 60 + now.minute
    night_first = t >= 15 * 60 or t < 5 * 60 + 5
    order = ["1", "0"] if night_first else ["0", "1"]
    errs = []
    for mt in order:
        try:
            x = _near(_mis(mt))
        except Exception as e:
            errs.append(f"{'夜盤' if mt == '1' else '日盤'}：{str(e)[:40]}")
            continue
        if not x:
            errs.append(f"{'夜盤' if mt == '1' else '日盤'}：沒有資料")
            continue
        px = _num(_pick(x, "CLastPrice", "LastPrice", "CDealPrice"))
        ref = _num(_pick(x, "CRefPrice", "RefPrice"))
        if np.isnan(px):
            px = ref
        if np.isnan(px):
            continue
        tm = str(_pick(x, "CTime", "Time") or "")
        tm = f"{tm[:2]}:{tm[2:4]}:{tm[4:6]}" if re.fullmatch(r"\d{6}", tm) else (tm or now.strftime("%H:%M:%S"))
        STATUS["realtime"] = "期交所 ✓"
        return {"price": px, "prev": ref if not np.isnan(ref) else None,
                "chg": (px / ref - 1) if ref and not np.isnan(ref) else None,
                "open": _num(_pick(x, "COpenPrice", "OpenPrice")), "high": _num(_pick(x, "CHighPrice", "HighPrice")),
                "low": _num(_pick(x, "CLowPrice", "LowPrice")), "vol": _num(_pick(x, "CTotalVolume", "TotalVolume")),
                "date": now.strftime("%Y%m%d"), "time": tm, "name": NAME,
                "source": "期交所" + ("夜盤" if mt == "1" else "日盤"), "session": "夜盤" if mt == "1" else "日盤",
                "contract": str(_pick(x, "DispCName", "SymbolID") or "")}
    STATUS["realtime"] = "；".join(errs) or "沒有資料"
    return None


def _finmind(dataset: str, start: str, token: str | None) -> pd.DataFrame:
    params = {"dataset": dataset, "data_id": "TX", "start_date": start}
    if token:
        params["token"] = token
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=30)
    j = r.json()
    if j.get("status") not in (200, None) and not j.get("data"):
        raise RuntimeError(j.get("msg") or f"FinMind {dataset} 失敗")
    return pd.DataFrame(j.get("data") or [])


def history(start: str, token: str | None = None) -> pd.DataFrame:
    """近月連續的「全盤」日 K：前一晚夜盤＋當天日盤合成一根。回傳 date, open, high, low, close, vol。"""
    raw = _finmind("TaiwanFuturesDaily", start, token)
    if raw.empty:
        raise RuntimeError("FinMind 沒有台指期日資料")
    d = raw.copy()
    d = d[~d["contract_date"].astype(str).str.contains("/")]                    # 排除價差
    for c in ("open", "max", "min", "close", "volume", "open_interest"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["close"] > 0]
    sess = d["trading_session"].astype(str) if "trading_session" in d else pd.Series("position", index=d.index)
    day = d[sess.str.contains("position|regular|一般", case=False)]
    night = d[sess.str.contains("after|盤後", case=False)]
    # 每天取近月：成交量最大的月份（換月時自然切到新的近月）
    pick = lambda x: x.loc[x.groupby("date")["volume"].idxmax()].set_index("date").sort_index()
    dd = pick(day)
    out = pd.DataFrame({"open": dd["open"], "high": dd["max"], "low": dd["min"], "close": dd["close"],
                        "vol": dd["volume"].astype(float)})
    if len(night):
        nn = pick(night)
        # 夜盤屬於「下一個交易日」：把夜盤接到它之後的第一個日盤
        days = out.index
        for nd, row in nn.iterrows():
            nxt = days[days > nd]
            if not len(nxt):
                continue
            k = nxt[0]
            out.loc[k, "open"] = row["open"]
            out.loc[k, "high"] = max(out.loc[k, "high"], row["max"])
            out.loc[k, "low"] = min(out.loc[k, "low"], row["min"])
            out.loc[k, "vol"] += float(row["volume"] or 0)
    out = out.dropna(subset=["close"]).reset_index()
    return out


def institutional(start: str, token: str | None = None) -> pd.DataFrame:
    """台指期三大法人多空未平倉（口）。回傳 date, 外資, 投信, 自營商（淨未平倉口數，正＝偏多）。"""
    raw = _finmind("TaiwanFuturesInstitutionalInvestors", start, token)
    if raw.empty:
        return pd.DataFrame()
    d = raw.copy()
    d["date"] = pd.to_datetime(d["date"])
    lo = next((c for c in d.columns if "long_open_interest_balance_volume" in c), None)
    sh = next((c for c in d.columns if "short_open_interest_balance_volume" in c), None)
    who = "institutional_investors" if "institutional_investors" in d else d.columns[2]
    if not lo or not sh:
        return pd.DataFrame()
    d["net"] = pd.to_numeric(d[lo], errors="coerce") - pd.to_numeric(d[sh], errors="coerce")
    name = lambda s: "外資" if "外資" in s or "Foreign" in s else "投信" if "投信" in s or "Trust" in s else "自營商"
    d["who"] = d[who].astype(str).map(name)
    return d.pivot_table(index="date", columns="who", values="net", aggfunc="sum").reset_index()
