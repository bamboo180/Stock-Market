"""個股新聞：抓取、過濾、標記重點。

來源：
- Google 新聞 RSS（台股中文、美股英文＋中文），近 7 天
- 台股：證交所 OpenAPI「上市公司每日重大訊息」（官方公告，只有最新一天）
- 美股：Yahoo Finance 新聞（yfinance）
- 選用：設定 ANTHROPIC_API_KEY 後可用 Claude 把標題整理成重點摘要
"""
from __future__ import annotations

import datetime as dt
import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests

TW_TZ = ZoneInfo("Asia/Taipei")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# 重點類別：(標籤, 關鍵字)
TOPICS = [
    ("財報", r"財報|EPS|每股盈餘|獲利|毛利率|淨利|earnings|quarterly results|profit|revenue beat|guidance"),
    ("營收", r"營收|revenue|sales"),
    ("法說", r"法說|法人說明會|conference call|investor day"),
    ("股利", r"股利|配息|除息|除權|殖利率|dividend|buyback|庫藏股|回購"),
    ("評等", r"目標價|評等|調升|調降|升評|降評|price target|upgrade|downgrade|rating"),
    ("籌碼", r"外資|投信|自營商|買超|賣超|融資|融券|大戶"),
    ("營運", r"擴產|訂單|新廠|產能|併購|收購|合併|合作|簽約|acquisition|merger|deal|partnership|launch"),
    ("風險", r"處置|注意股|裁罰|訴訟|調查|召回|停工|lawsuit|probe|investigation|recall|fine"),
]
POS = r"創新高|大漲|漲停|勁揚|調升|上修|優於預期|年增|季增|成長|看好|利多|買超|連\s*\d*\s*買|beat|surge|soar|jump|rally|record high|upgrade|raises|strong"
NEG = r"大跌|跌停|重挫|下修|調降|衰退|年減|季減|虧損|低於預期|利空|看壞|賣超|連\s*\d*\s*賣|miss|plunge|slump|drop|fall|downgrade|cuts|weak|loss"
NOISE = r"股價、新聞、報價|報價和記錄|Stock Price, News, Quote|stock price today|即時報價|走勢圖"


class Item(dict):
    """title, source, time(datetime), link, tags(list), tone('pos'|'neg'|''), kind('news'|'official')"""


def _tag(item: Item) -> Item:
    t = item["title"]
    item["tags"] = [name for name, pat in TOPICS if re.search(pat, t, re.I)]
    pos, neg = bool(re.search(POS, t, re.I)), bool(re.search(NEG, t, re.I))
    item["tone"] = "pos" if pos and not neg else "neg" if neg and not pos else ""
    return item


def _google(query: str, lang: str) -> list[Item]:
    hl, gl, ceid = ("zh-TW", "TW", "TW:zh-Hant") if lang == "zh" else ("en-US", "US", "US:en")
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    try:
        r = requests.get(url, headers=UA, timeout=12)
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        if not title or re.search(NOISE, title, re.I):
            continue
        try:
            t = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TW_TZ)
        except Exception:
            continue
        out.append(_tag(Item(title=title, source=src or "Google 新聞", time=t,
                             link=it.findtext("link") or "", kind="news")))
    return out


def _twse_official(code: str) -> list[Item]:
    try:
        rows = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", headers=UA, timeout=12).json()
    except Exception:
        return []
    out = []
    for r in rows:
        if str(r.get("公司代號", "")).strip() != code:
            continue
        subject = next((v for k, v in r.items() if k.strip() == "主旨"), "") or ""
        subject = re.sub(r"\s+", "", subject)
        d, tm = str(r.get("發言日期", "")), str(r.get("發言時間", "")).zfill(6)
        try:   # 民國年 1151028 → 2026-10-28
            t = dt.datetime(int(d[:-4]) + 1911, int(d[-4:-2]), int(d[-2:]),
                            int(tm[:2]), int(tm[2:4]), tzinfo=TW_TZ)
        except ValueError:
            t = dt.datetime.now(TW_TZ)
        out.append(_tag(Item(title=subject, source="公開資訊觀測站（重大訊息）", time=t,
                             link="https://mops.twse.com.tw/mops/web/t05sr01_1", kind="official")))
    return out


def _yahoo(ticker: str) -> list[Item]:
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    out = []
    for n in raw:
        c = n.get("content", n)              # 新舊兩種格式
        title = c.get("title", "")
        if not title:
            continue
        ts = c.get("pubDate") or c.get("providerPublishTime")
        try:
            t = (dt.datetime.fromtimestamp(ts, TW_TZ) if isinstance(ts, (int, float))
                 else dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TW_TZ))
        except Exception:
            continue
        prov = c.get("provider") or {}
        link = (c.get("canonicalUrl") or {}).get("url") or c.get("link", "")
        out.append(_tag(Item(title=title, source=prov.get("displayName") or c.get("publisher") or "Yahoo Finance",
                             time=t, link=link, kind="news")))
    return out


def _dedupe(items: list[Item]) -> list[Item]:
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["time"], reverse=True):
        key = re.sub(r"[\W_]+", "", it["title"].lower())[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def fetch(code: str, market: str, name: str = "", limit: int = 40) -> list[Item]:
    from concurrent.futures import ThreadPoolExecutor
    items: list[Item] = []
    with ThreadPoolExecutor(4) as ex:            # 各個新聞來源同時抓
        if market == "TW":
            nm = name or code
            futs = [ex.submit(_twse_official, code), ex.submit(_google, f"{nm} {code} when:7d", "zh"),
                    ex.submit(_google, f"{nm} 股 when:7d", "zh")]
        else:
            futs = [ex.submit(_yahoo, code), ex.submit(_google, f'"{code}" stock when:7d', "en")]
            if name:
                futs.append(ex.submit(_google, f"{name.split()[0]} 股價 when:7d", "zh"))
        for f in futs:
            try:
                items += f.result()
            except Exception:
                pass
    cutoff = dt.datetime.now(TW_TZ) - dt.timedelta(days=8)
    items = [i for i in items if i["time"] >= cutoff or i["kind"] == "official"]
    return _dedupe(items)[:limit]


# ----------------------------------------------------------------------------
# 選用：Claude 摘要
# ----------------------------------------------------------------------------
def ai_summary(api_key: str, label: str, items: list[Item], model: str = "claude-haiku-5-5") -> str:
    lines = [f"- [{i['time']:%m/%d}] {i['title']}（{i['source']}）" for i in items[:30]]
    prompt = (
        f"以下是「{label}」最近一週的新聞標題。請用繁體中文整理成 3 到 5 點重點，"
        "每點一行，開頭標註【利多】【利空】或【中性】，只根據標題內容，不要編造標題沒有的數字，"
        "不要給買賣建議。最後一行用一句話說明整體消息面偏多、偏空或中性。\n\n" + "\n".join(lines))
    return ask_claude(api_key, prompt, model)


def ask_claude(api_key: str, prompt: str, model: str = "claude-haiku-5-5", max_tokens: int = 800) -> str:
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
    j = r.json()
    if r.status_code != 200:
        raise RuntimeError(j.get("error", {}).get("message", f"HTTP {r.status_code}"))
    return "".join(b.get("text", "") for b in j.get("content", []))
