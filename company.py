"""公司基本資料與說明。

台股：證交所／櫃買中心「公司基本資料」、FinMind（本益比、EPS、毛利率、股利）、中文維基百科簡介
美股：Yahoo Finance（產業、員工數、市值、本益比、公司簡介）、中文維基百科簡介
"""
from __future__ import annotations

import re

import pandas as pd
import requests

UA = {"User-Agent": "StockSignalApp/1.0 (personal research tool)"}
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _fm(dataset: str, code: str, start: str, token: str | None) -> pd.DataFrame:
    try:
        h = {"Authorization": f"Bearer {token}"} if token else {}
        j = requests.get(FINMIND, params={"dataset": dataset, "data_id": code, "start_date": start},
                         headers=h, timeout=20).json()
        return pd.DataFrame(j.get("data", []))
    except Exception:
        return pd.DataFrame()


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _roc(d: str) -> str:
    """民國日期 0760221 / 760221 → 1987-02-21"""
    d = re.sub(r"\D", "", str(d or ""))
    if len(d) < 6:
        return ""
    return f"{int(d[:-4]) + 1911}-{d[-4:-2]}-{d[-2:]}"


# ----------------------------------------------------------------------------
# 維基百科
# ----------------------------------------------------------------------------
def wiki(query: str, must_contain: list[str]) -> dict | None:
    """搜尋中文維基百科，只接受摘要裡真的提到這家公司的條目（避免抓到董事長或同名條目）。"""
    api = "https://zh.wikipedia.org/w/api.php"
    try:
        hits = requests.get(api, params={"action": "query", "list": "search", "srsearch": query, "srlimit": 4,
                                         "format": "json"}, headers=UA, timeout=10).json()["query"]["search"]
    except Exception:
        return None
    keys = [k.lower() for k in must_contain if k]
    # 先看條目標題是不是公司名稱，再看摘要開頭（避免抓到董事長、產品等相關條目）
    ranked = sorted(hits, key=lambda h: 0 if any(k in h["title"].lower() or h["title"].lower() in k for k in keys) else 1)
    for h in ranked:
        title_ok = any(k in h["title"].lower() or h["title"].lower() in k for k in keys)
        try:
            ex = requests.get(api, params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                           "redirects": 1, "variant": "zh-tw", "format": "json", "titles": h["title"]},
                              headers=UA, timeout=10).json()
            page = next(iter(ex["query"]["pages"].values()))
        except Exception:
            continue
        text = (page.get("extract") or "").strip()
        start = text[:12].lower()
        if not text or "消歧義" in text[:60] or not (title_ok or any(k[:4] in start for k in keys)):
            continue
        paras = [p for p in text.split("\n") if p.strip()]
        summary = paras[0]
        if len(summary) < 120 and len(paras) > 1:
            summary += "\n\n" + paras[1]
        return {"title": page["title"], "text": summary[:600],
                "url": "https://zh.wikipedia.org/wiki/" + page["title"].replace(" ", "_")}
    return None


# ----------------------------------------------------------------------------
# 台股
# ----------------------------------------------------------------------------
def tw_registry() -> dict[str, dict]:
    """上市＋上櫃公司基本資料，代號 → 欄位。"""
    out = {}
    for url, mkt in [("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "上市"),
                     ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "上櫃")]:
        try:
            rows = requests.get(url, headers=UA, timeout=20).json()
        except Exception:
            continue
        for r in rows:
            r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            code = r.get("公司代號") or r.get("SecuritiesCompanyCode")
            if code:
                out[str(code)] = {**r, "_market": mkt}
    return out


def tw(code: str, name: str, reg: dict[str, dict], price: float | None, token: str | None, group: str = "") -> dict:
    info: dict = {"rows": [], "valuation": [], "eps": None, "dividends": None, "wiki": None, "margin": None}
    r = reg.get(code, {})
    full = r.get("公司名稱") or r.get("CompanyName") or ""
    shares = _num(r.get("已發行普通股數或TDR原股發行股數") or r.get("IssueShares"))
    capital = _num(r.get("實收資本額") or r.get("Paidin.Capital.NTDollars"))
    rows = [("公司全名", full), ("市場", r.get("_market", "")), ("產業別", group or (r.get("產業別") if not str(r.get("產業別", "")).isdigit() else "")),
            ("董事長", r.get("董事長") or r.get("Chairman") or ""), ("總經理", r.get("總經理") or r.get("GeneralManager") or ""),
            ("成立日期", _roc(r.get("成立日期") or r.get("DateOfIncorporation"))),
            ("上市櫃日期", _roc(r.get("上市日期") or r.get("DateOfListing"))),
            ("實收資本額", f"{capital / 1e8:,.1f} 億元" if capital else ""),
            ("市值（現價估算）", f"{shares * price / 1e8:,.0f} 億元" if shares and price else ""),
            ("網址", r.get("網址") or r.get("WebAddress") or "")]
    info["rows"] = [(k, v) for k, v in rows if v]

    start = (pd.Timestamp.today() - pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    per = _fm("TaiwanStockPER", code, start, token)
    if not per.empty:
        p = per.iloc[-1]
        info["valuation"] = [("本益比", f"{p['PER']:.1f} 倍" if p["PER"] else "—（虧損或無資料）"),
                             ("殖利率", f"{p['dividend_yield']:.2f}%"), ("股價淨值比", f"{p['PBR']:.2f} 倍"),
                             ("資料日", p["date"])]

    fin = _fm("TaiwanStockFinancialStatements", code,
              (pd.Timestamp.today() - pd.Timedelta(days=760)).strftime("%Y-%m-%d"), token)
    if not fin.empty:
        piv = fin.pivot_table(index="date", columns="type", values="value", aggfunc="last").sort_index()
        if "EPS" in piv:
            eps = piv["EPS"].dropna().tail(8)
            info["eps"] = pd.DataFrame({"季度": [_quarter(d) for d in eps.index], "EPS（元）": eps.values})
            info["eps_ttm"] = float(eps.tail(4).sum()) if len(eps) >= 4 else None
        last = piv.iloc[-1]
        if last.get("Revenue"):
            info["margin"] = {"季度": _quarter(piv.index[-1]),
                              "毛利率": last.get("GrossProfit", float("nan")) / last["Revenue"],
                              "營益率": last.get("OperatingIncome", float("nan")) / last["Revenue"],
                              "淨利率": last.get("IncomeAfterTaxes", float("nan")) / last["Revenue"]}

    div = _fm("TaiwanStockDividend", code, (pd.Timestamp.today() - pd.Timedelta(days=800)).strftime("%Y-%m-%d"), token)
    if not div.empty:
        d = div.tail(6).iloc[::-1]
        info["dividends"] = pd.DataFrame({
            "所屬期間": d["year"], "現金股利": d["CashEarningsDistribution"].round(2),
            "股票股利": d["StockEarningsDistribution"].round(2), "除息日": d["CashExDividendTradingDate"]})

    short = re.sub(r"(股份)?有限公司$", "", full) if full else name
    keys = [k for k in {name, short, full} if k]
    info["wiki"] = wiki(f"{short or name} 公司", keys) or (wiki(name, keys) if name else None)
    return info


def _quarter(d) -> str:
    t = pd.Timestamp(d)
    return f"{t.year}Q{(t.month - 1) // 3 + 1}"


# ----------------------------------------------------------------------------
# 美股
# ----------------------------------------------------------------------------
def us(code: str, cname: str = "") -> dict:
    info: dict = {"rows": [], "valuation": [], "summary_en": "", "wiki": None}
    try:
        import yfinance as yf
        i = yf.Ticker(code).info or {}
    except Exception:
        i = {}

    def money(v):
        return f"{v / 1e9:,.1f} 十億美元" if v else ""

    rows = [("公司全名", i.get("longName", "")), ("產業", " / ".join(x for x in [i.get("sector"), i.get("industry")] if x)),
            ("員工數", f"{i['fullTimeEmployees']:,}" if i.get("fullTimeEmployees") else ""),
            ("總部", ", ".join(x for x in [i.get("city"), i.get("country")] if x)),
            ("市值", money(i.get("marketCap"))), ("網址", i.get("website", ""))]
    info["rows"] = [(k, v) for k, v in rows if v]
    val = [("本益比（近 4 季）", f"{i['trailingPE']:.1f} 倍" if i.get("trailingPE") else ""),
           ("預估本益比", f"{i['forwardPE']:.1f} 倍" if i.get("forwardPE") else ""),
           ("EPS（近 4 季）", f"{i['trailingEps']:.2f} 美元" if i.get("trailingEps") else ""),
           ("殖利率", f"{i['dividendYield']:.2f}%" if i.get("dividendYield") else ""),
           ("股價淨值比", f"{i['priceToBook']:.2f} 倍" if i.get("priceToBook") else ""),
           ("毛利率", f"{i['grossMargins'] * 100:.1f}%" if i.get("grossMargins") else ""),
           ("營益率", f"{i['operatingMargins'] * 100:.1f}%" if i.get("operatingMargins") else ""),
           ("52 週區間", f"{i['fiftyTwoWeekLow']:,.2f} ～ {i['fiftyTwoWeekHigh']:,.2f}"
            if i.get("fiftyTwoWeekLow") and i.get("fiftyTwoWeekHigh") else ""),
           ("Beta", f"{i['beta']:.2f}" if i.get("beta") else "")]
    info["valuation"] = [(k, v) for k, v in val if v]
    info["summary_en"] = i.get("longBusinessSummary", "")
    long_name = i.get("longName") or i.get("shortName") or ""
    keys = [k for k in {cname.split()[0] if cname else "", long_name.split(",")[0].replace(" Inc.", ""), code} if k]
    q = cname.split()[0] if cname else long_name
    if q:
        info["wiki"] = wiki(f"{q} 公司", keys)
    return info
