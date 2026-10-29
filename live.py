"""即時報價與交易時段。

台股：證交所基本市況報導站（mis.twse.com.tw），約 5 秒延遲，上市上櫃都支援。
美股：Yahoo Finance（yfinance fast_info），通常延遲數秒到 15 分鐘。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import requests

TZ = {"TW": ZoneInfo("Asia/Taipei"), "US": ZoneInfo("America/New_York")}
SESSION = {"TW": (dt.time(9, 0), dt.time(13, 30)), "US": (dt.time(9, 30), dt.time(16, 0))}
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


# ----------------------------------------------------------------------------
# 交易時段（不含國定假日；假日時報價會停在前一交易日，不影響正確性）
# ----------------------------------------------------------------------------
def now(market: str) -> dt.datetime:
    return dt.datetime.now(TZ[market])


def is_open(market: str, t: dt.datetime | None = None) -> bool:
    t = t or now(market)
    if t.weekday() >= 5:
        return False
    start, end = SESSION[market]
    return start <= t.time() <= end


def status_text(market: str) -> str:
    t = now(market)
    tw_time = t.astimezone(TZ["TW"]).strftime("%H:%M")
    if is_open(market, t):
        return f"盤中（台灣時間 {tw_time}）"
    return f"已收盤／未開盤（台灣時間 {tw_time}）"


# ----------------------------------------------------------------------------
# 報價
# ----------------------------------------------------------------------------
def _num(x) -> float | None:
    try:
        v = float(str(x).split("_")[0])
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def quotes_mis(ex_ch: list[str]) -> dict[str, dict]:
    """查證交所基本市況報導站。ex_ch 例如 ["tse_2330.tw", "otc_o00.tw"]，回傳以代號為 key。"""
    if not ex_ch:
        return {}
    try:
        r = requests.get(MIS, params={"ex_ch": "|".join(ex_ch), "json": 1, "delay": 0}, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
        rows = r.json().get("msgArray", [])
    except Exception:
        return {}
    out = {}
    for m in rows:
        code = m.get("c")
        if not code:
            continue
        prev = _num(m.get("y"))
        # 最新成交價：頂層 z → trade.z → 買一賣一中間價 → 昨收
        price = _num(m.get("z")) or _num((m.get("trade") or {}).get("z"))
        if price is None:
            bid, ask = _num(m.get("b")), _num(m.get("a"))
            price = (bid + ask) / 2 if bid and ask else bid or ask or prev
        if price is None:
            continue
        out[code] = {
            "price": price, "prev": prev,
            "chg": (price / prev - 1) if prev else None,
            "open": _num(m.get("o")), "high": _num(m.get("h")), "low": _num(m.get("l")),
            "vol": _num(m.get("v")),                       # 張
            "date": m.get("d", ""), "time": m.get("t") or m.get("%", ""),
            "name": m.get("n", ""), "source": "證交所",
        }
    return out


def quotes_tw(codes: list[str]) -> dict[str, dict]:
    """一次查多檔台股。每檔同時查上市(tse)與上櫃(otc)，哪個有資料用哪個。"""
    return quotes_mis([f"{ex}_{c}.tw" for c in codes for ex in ("tse", "otc")])


def quote_us(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        price, prev = fi.last_price, fi.previous_close
        if not price:
            return None
        return {
            "price": float(price), "prev": float(prev) if prev else None,
            "chg": (price / prev - 1) if prev else None,
            "open": _safe(fi, "open"), "high": _safe(fi, "day_high"), "low": _safe(fi, "day_low"),
            "vol": _safe(fi, "last_volume"),
            "date": now("US").strftime("%Y%m%d"), "time": now("US").strftime("%H:%M:%S") + " ET",
            "name": "", "source": "Yahoo",
        }
    except Exception:
        return None


def _safe(fi, attr):
    try:
        v = getattr(fi, attr)
        return float(v) if v else None
    except Exception:
        return None


def quotes(codes: list[str], market_of) -> dict[str, dict]:
    tw = [c for c in codes if market_of(c) == "TW"]
    us = [c for c in codes if market_of(c) == "US"]
    out = quotes_tw(tw)
    for c in us:
        q = quote_us(c)
        if q:
            out[c] = q
    return out
