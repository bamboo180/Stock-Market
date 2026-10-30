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
    """日期統一成西元：19870221（西元）、0760221／760221（民國）、1987/02/21 都轉成 1987-02-21"""
    d = re.sub(r"\D", "", str(d or ""))
    if len(d) == 8 and d[:2] in ("19", "20"):       # 已經是西元年
        y = int(d[:4])
    elif 6 <= len(d) <= 7:                           # 民國年
        y = int(d[:-4]) + 1911
    else:
        return ""
    m, day = int(d[-4:-2]), int(d[-2:])
    if not (1900 < y < 2100 and 1 <= m <= 12 and 1 <= day <= 31):
        return ""
    return f"{y}-{m:02d}-{day:02d}"


# ----------------------------------------------------------------------------
# 維基百科
# ----------------------------------------------------------------------------
def wiki(query: str, must_contain: list[str], also_need: list[str] | None = None) -> dict | None:
    """搜尋中文維基百科，只接受摘要裡真的提到這家公司的條目（避免抓到董事長或同名條目）。"""
    api = "https://zh.wikipedia.org/w/api.php"
    try:
        hits = requests.get(api, params={"action": "query", "list": "search", "srsearch": query, "srlimit": 4,
                                         "format": "json"}, headers=UA, timeout=10).json()["query"]["search"]
    except Exception:
        return None
    keys = [k.lower() for k in must_contain if k]
    # 先看條目標題是不是公司名稱，再看摘要開頭（避免抓到董事長、產品等相關條目）
    need = [n.lower() for n in (also_need or []) if n]
    ranked = sorted(hits, key=lambda h: 0 if any(k in h["title"].lower() for k in keys) else 1)
    for h in ranked:
        title_ok = any(k in h["title"].lower() for k in keys)
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
        if need and not any(n in text[:300].lower() for n in need):      # 例如美股要提到英文名或代號
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


def tw(code: str, name: str, reg: dict[str, dict], token: str | None, group: str = "") -> dict:
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
            ("網址", r.get("網址") or r.get("WebAddress") or "")]
    info["rows"] = [(k, v) for k, v in rows if v]
    info["shares"] = shares

    from concurrent.futures import ThreadPoolExecutor
    today = pd.Timestamp.today()
    short = re.sub(r"(股份)?有限公司$", "", full) if full else name
    keys = [k for k in {name, short, full} if k]
    with ThreadPoolExecutor(5) as ex:            # 估值、財報、股利、維基百科同時抓
        f_per = ex.submit(_fm, "TaiwanStockPER", code, (today - pd.Timedelta(days=21)).strftime("%Y-%m-%d"), token)
        f_fin = ex.submit(_fm, "TaiwanStockFinancialStatements", code,
                          (today - pd.Timedelta(days=760)).strftime("%Y-%m-%d"), token)
        f_div = ex.submit(_fm, "TaiwanStockDividend", code, (today - pd.Timedelta(days=800)).strftime("%Y-%m-%d"), token)
        f_wiki = ex.submit(wiki, f"{short or name} 公司", keys)
        f_wiki2 = ex.submit(wiki, name, keys) if name else None
    per = f_per.result()
    if not per.empty:
        p = per.iloc[-1]
        info["valuation"] = [("本益比", f"{p['PER']:.1f} 倍" if p["PER"] else "—（虧損或無資料）"),
                             ("殖利率", f"{p['dividend_yield']:.2f}%"), ("股價淨值比", f"{p['PBR']:.2f} 倍"),
                             ("資料日", p["date"])]

    fin = f_fin.result()
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

    div = f_div.result()
    if not div.empty:
        d = div.tail(6).iloc[::-1]
        info["dividends"] = pd.DataFrame({
            "所屬期間": d["year"], "現金股利": d["CashEarningsDistribution"].round(2),
            "股票股利": d["StockEarningsDistribution"].round(2), "除息日": d["CashExDividendTradingDate"]})

    info["wiki"] = f_wiki.result() or (f_wiki2.result() if f_wiki2 else None)
    return info


def _quarter(d) -> str:
    t = pd.Timestamp(d)
    return f"{t.year}Q{(t.month - 1) // 3 + 1}"


# ----------------------------------------------------------------------------
# 美股
# ----------------------------------------------------------------------------
SEC_UA = {"User-Agent": "StockSignalApp/1.8 personal-research research-tool@example.com"}


def _sec_cik(code: str) -> int | None:
    try:
        j = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=15).json()
    except Exception:
        return None
    code = code.upper().replace("-", ".")
    for v in j.values():
        if v["ticker"].upper().replace("-", ".") == code:
            return int(v["cik_str"])
    return None


def sec(code: str) -> dict:
    """美國證管會（SEC）官方資料：公司名稱、產業、總部、流通股數、每季 EPS、營收、毛利率。"""
    out: dict = {}
    cik = _sec_cik(code)
    if not cik:
        return out
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as ex:
        f_sub = ex.submit(requests.get, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC_UA, timeout=15)
        f_fac = ex.submit(requests.get, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=SEC_UA, timeout=20)
    try:
        sub = f_sub.result().json()
        a = (sub.get("addresses") or {}).get("business") or {}
        out.update(name=sub.get("name", ""), industry=sub.get("sicDescription", ""),
                   exchange="、".join(sub.get("exchanges") or []),
                   hq=", ".join(x for x in [(a.get("city") or "").title(), a.get("stateOrCountryDescription")] if x),
                   website=sub.get("website") or "")
    except Exception:
        pass
    try:
        facts = f_fac.result().json().get("facts", {})
    except Exception:
        return out
    gaap = facts.get("us-gaap", {})

    def quarterly(*tags, unit="USD"):
        """取單季數字（SEC 的 frame 格式 CY2026Q2，且期間約 3 個月）。"""
        for t in tags:
            rows = (gaap.get(t, {}).get("units", {}) or {}).get(unit, [])
            q = {}
            for r in rows:
                fr = r.get("frame", "")
                if re.fullmatch(r"CY\d{4}Q\d", fr) and r.get("start") and r.get("end"):
                    days = (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days
                    if 80 <= days <= 100:
                        q[fr] = r["val"]
            if q:
                return pd.Series(q).sort_index()
        return pd.Series(dtype=float)

    eps = quarterly("EarningsPerShareDiluted", "EarningsPerShareBasic", unit="USD/shares")
    rev = quarterly("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
    gp = quarterly("GrossProfit")
    oi = quarterly("OperatingIncomeLoss")
    ni = quarterly("NetIncomeLoss")
    if len(eps):
        e = eps.tail(8)
        out["eps"] = pd.DataFrame({"季度": [f[2:] for f in e.index], "EPS（美元）": e.values})
        if len(eps) >= 4:
            out["eps_ttm"] = float(eps.tail(4).sum())
    if len(rev):
        last = rev.index[-1]
        m = {"季度": last[2:], "營收": rev.iloc[-1]}
        for k, s_ in (("毛利率", gp), ("營益率", oi), ("淨利率", ni)):
            if last in s_.index and rev.iloc[-1]:
                m[k] = s_[last] / rev.iloc[-1]
        out["margin"] = m
    sh = (facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}) or {}).get("shares", [])
    if sh:
        out["shares"] = float(sorted(sh, key=lambda r: r["end"])[-1]["val"])
    return out


def us(code: str, cname: str = "") -> dict:
    """cname 例如「超微 Advanced Micro Devices」：中文名＋英文名。"""
    from concurrent.futures import ThreadPoolExecutor

    def yahoo():
        try:
            import yfinance as yf
            return yf.Ticker(code).info or {}
        except Exception:          # Yahoo 常限制次數，失敗就只用 SEC 資料
            return {}

    zh = cname.split()[0] if cname and not cname.split()[0].isascii() else ""
    en = " ".join(cname.split()[1:]) if zh else cname
    with ThreadPoolExecutor(3) as ex:
        f_y, f_sec = ex.submit(yahoo), ex.submit(sec, code)
        i, sc = f_y.result(), f_sec.result()

    def money(v):
        return f"{v / 1e9:,.1f} 十億美元" if v else ""

    rows = [("公司全名", i.get("longName") or sc.get("name", "")),
            ("產業", " / ".join(x for x in [i.get("sector"), i.get("industry")] if x) or sc.get("industry", "")),
            ("交易所", sc.get("exchange", "")),
            ("員工數", f"{i['fullTimeEmployees']:,}" if i.get("fullTimeEmployees") else ""),
            ("總部", ", ".join(x for x in [i.get("city"), i.get("country")] if x) or sc.get("hq", "")),
            ("市值", money(i.get("marketCap"))), ("網址", i.get("website") or sc.get("website", ""))]
    info: dict = {"rows": [(k, v) for k, v in rows if v], "summary_en": i.get("longBusinessSummary", ""), "wiki": None,
                  "eps": sc.get("eps"), "eps_ttm": sc.get("eps_ttm"), "margin": sc.get("margin"),
                  "us_shares": None if i.get("marketCap") else sc.get("shares"), "yahoo_ok": bool(i)}
    val = [("本益比（近 4 季）", f"{i['trailingPE']:.1f} 倍" if i.get("trailingPE") else ""),
           ("預估本益比", f"{i['forwardPE']:.1f} 倍" if i.get("forwardPE") else ""),
           ("EPS（近 4 季）", f"{i['trailingEps']:.2f} 美元" if i.get("trailingEps")
            else (f"{sc['eps_ttm']:.2f} 美元（SEC）" if sc.get("eps_ttm") else "")),
           ("殖利率", f"{i['dividendYield']:.2f}%" if i.get("dividendYield") else ""),
           ("股價淨值比", f"{i['priceToBook']:.2f} 倍" if i.get("priceToBook") else ""),
           ("52 週區間", f"{i['fiftyTwoWeekLow']:,.2f} ～ {i['fiftyTwoWeekHigh']:,.2f}"
            if i.get("fiftyTwoWeekLow") and i.get("fiftyTwoWeekHigh") else ""),
           ("Beta", f"{i['beta']:.2f}" if i.get("beta") else "")]
    info["valuation"] = [(k, v) for k, v in val if v]
    if not info["margin"] and i.get("grossMargins"):
        info["margin"] = {"季度": "近 4 季", "毛利率": i.get("grossMargins"), "營益率": i.get("operatingMargins"),
                          "淨利率": i.get("profitMargins")}

    long_name = i.get("longName") or sc.get("name", "") or en
    first_en = re.split(r"[ ,.]", long_name.strip())[0] if long_name else ""
    if zh:
        # 標題要含中文名，內文前段要提到英文名或代號，避免抓到同名條目（例如「蘋果」水果、「超微」對到美超微）
        en_first = re.split(r"[ ,.]", en.strip())[0] if en else ""
        info["wiki"] = wiki(f"{zh} 公司", [zh], also_need=[first_en, en_first, code])
    return info
