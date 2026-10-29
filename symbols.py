"""股票清單與關鍵字搜尋。

台股：優先用 FinMind 最新清單（上市、上櫃，含 ETF），失敗時用內建 data/tw_stocks.csv。
美股：內建常見個股與 ETF（附中文名）；清單外的代號可直接輸入，
      或輸入英文公司名，會用 Yahoo 搜尋找代號。
"""
from __future__ import annotations

import os
import re

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
GENERIC = {"電子工業", "所有證券", "Index", "大盤", "其他"}


def load_tw(token: str | None = None) -> pd.DataFrame:
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        j = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInfo"},
                         headers=headers, timeout=20).json()
        d = pd.DataFrame(j.get("data", []))
        d = d[d["type"].isin(["twse", "tpex"]) & d["stock_id"].str.fullmatch(r"\d{4,6}[A-Z]?")]
        if len(d) < 1000:
            raise ValueError("too few rows")
        # 同一檔可能列在多個產業，優先取具體的產業名稱
        d["generic"] = d["industry_category"].isin(GENERIC)
        d = d.sort_values(["stock_id", "generic"]).drop_duplicates("stock_id")
        return pd.DataFrame({"code": d["stock_id"], "name": d["stock_name"], "group": d["industry_category"],
                             "market": d["type"].map({"twse": "上市", "tpex": "上櫃"})}).reset_index(drop=True)
    except Exception:
        return pd.read_csv(os.path.join(HERE, "data", "tw_stocks.csv"), dtype=str)


def load_us() -> pd.DataFrame:
    return pd.read_csv(os.path.join(HERE, "data", "us_stocks.csv"), dtype=str)


def build_options(tw: pd.DataFrame, us: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    """回傳（選項文字清單, 名稱→代號 對照）。選項文字的第一個詞就是代號。"""
    opts, name2code = [], {}
    for r in tw.itertuples():
        opts.append(f"{r.code} {r.name}・{r.group}（{r.market}）")
        name2code[str(r.name).upper()] = r.code
    for r in us.itertuples():
        opts.append(f"{r.code} {r.cname} {r.name}・{r.group}（美股）")
        name2code[str(r.cname).upper()] = r.code
        name2code[str(r.name).upper()] = r.code
    return opts, name2code


def yahoo_search(query: str) -> str | None:
    """清單外的英文關鍵字，用 Yahoo Finance 搜尋第一個股票或 ETF。"""
    try:
        import yfinance as yf
        for q in yf.Search(query, max_results=8).quotes:
            if q.get("quoteType") in ("EQUITY", "ETF") and "." not in q.get("symbol", "."):
                return q["symbol"]
    except Exception:
        pass
    return None


def resolve(text: str, name2code: dict[str, str]) -> str | None:
    """把使用者選的或打的文字轉成代號。"""
    t = (text or "").strip()
    if not t:
        return None
    first = t.split()[0].upper()
    if re.fullmatch(r"\d{4,6}[A-Z]?", first):                # 台股代號
        return first
    if t.upper() in name2code:                               # 完整名稱
        return name2code[t.upper()]
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", first) and len(t.split()) > 1:   # 從選單選的美股
        return first
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,5}", t):      # 直接打的短代號，例如 msft
        return t.upper()
    hits = [c for n, c in name2code.items() if t.upper() in n]
    if len(set(hits)) == 1:                                  # 名稱片段只對到一檔
        return hits[0]
    if re.search(r"[A-Za-z]", t):
        return yahoo_search(t)
    return None
