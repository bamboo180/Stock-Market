"""市場指數：走勢用 Yahoo Finance 日線，台股加權、櫃買盤中改用證交所即時值。"""
from __future__ import annotations

import pandas as pd

import live

# 代號: (中文名稱, 市場, 證交所即時代碼)
CATALOG: dict[str, tuple[str, str, str | None]] = {
    "^TWII": ("加權指數", "TW", "tse_t00.tw"),
    "^TWOII": ("櫃買指數", "TW", "otc_o00.tw"),
    "^GSPC": ("S&P 500", "US", None),
    "^IXIC": ("那斯達克", "US", None),
    "^DJI": ("道瓊工業", "US", None),
    "^SOX": ("費城半導體", "US", None),
    "^RUT": ("羅素 2000", "US", None),
    "^VIX": ("VIX 恐慌指數", "US", None),
    "ES=F": ("標普 500 期貨", "US", None),
    "NQ=F": ("那斯達克期貨", "US", None),
    "YM=F": ("道瓊期貨", "US", None),
    "^TNX": ("美國 10 年期公債殖利率", "US", None),
    "^N225": ("日經 225", "JP", None),
    "^KS11": ("韓國綜合", "KR", None),
    "^HSI": ("恆生指數", "HK", None),
    "000001.SS": ("上證指數", "CN", None),
    "^GDAXI": ("德國 DAX", "EU", None),
    "^STOXX50E": ("歐洲 STOXX 50", "EU", None),
    "TWD=X": ("美元兌台幣", "FX", None),
    "GC=F": ("黃金期貨", "商品", None),
    "CL=F": ("原油期貨", "商品", None),
    "BTC-USD": ("比特幣", "加密", None),
}
DEFAULT = ["^TWII", "^TWOII", "^GSPC", "^IXIC", "^DJI", "^SOX"]


def name_of(sym: str) -> str:
    return CATALOG.get(sym, (sym,))[0]


def is_tw(sym: str) -> bool:
    return CATALOG.get(sym, ("", ""))[1] == "TW"


def fetch(symbols: list[str], spark_days: int = 22) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not symbols:
        return out
    try:
        import yfinance as yf
        raw = yf.download(symbols, period="3mo", interval="1d", group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
    except Exception:
        raw = None
    for s in symbols:
        try:
            close = (raw[s]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).dropna()
        except Exception:
            continue
        if len(close) < 2:
            continue
        out[s] = {"price": float(close.iloc[-1]), "prev": float(close.iloc[-2]),
                  "spark": [round(float(x), 2) for x in close.tail(spark_days)],
                  "date": close.index[-1].strftime("%Y%m%d"), "time": close.index[-1].strftime("%m/%d"),
                  "source": "Yahoo"}

    # 台股指數：用證交所即時值（盤中約 5 秒延遲，收盤後為當日收盤）
    tw = {CATALOG[s][2]: s for s in symbols if s in CATALOG and CATALOG[s][2]}
    if tw:
        q = live.quotes_mis(list(tw))
        for ex_ch, s in tw.items():
            m = q.get(ex_ch.split("_")[1].split(".")[0])
            if not m or not m.get("price"):
                continue
            d = out.setdefault(s, {"spark": [], "date": ""})
            if d["date"] and m["date"] > d["date"]:
                d["spark"] = (d["spark"] + [round(m["price"], 2)])[-spark_days:]
            elif d["spark"]:
                d["spark"][-1] = round(m["price"], 2)
            d.update({"price": m["price"], "prev": m["prev"] or d.get("prev"), "date": m["date"],
                      "time": m["time"], "source": "證交所"})

    for d in out.values():
        d["chg"] = d["price"] / d["prev"] - 1 if d.get("prev") else None
    return out
