"""股票訊號看板（台股 + 美股）。執行：streamlit run app.py"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import logging
import hmac
import hashlib
import inspect
import json
import os
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# 我的最愛拖曳排序：自己的小元件（favsort 資料夾，內含 SortableJS），不用另外安裝套件
import streamlit.components.v1 as _cv1
FAVSORT = _cv1.declare_component("favsort", path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "favsort"))
SBCOLLAPSE = _cv1.declare_component("sbcollapse", path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sbcollapse"))

import company
import engine
import indices
import kchart
import live
import news
import position
import prob
import radar
import recap
import session
import store
import symbols

# 各程式檔需要的功能。雲端更新時如果有檔案沒上傳、或主機還留著舊版，先自動重新載入；
# 還是舊的就清楚說明要上傳哪個檔案，而不是直接出現錯誤畫面。
import importlib as _il
_NEEDS = {radar: ("log_entry", "add_log", "evaluate", "factor_report", "scan"), live: ("tw_name_yahoo",),
          store: ("RADAR_LOG_MAX", "clean_groups"), session: ("pick_chart_day",), engine: ("fetch_tw_index",)}
_STALE = []
for _m, _attrs in _NEEDS.items():
    if not all(hasattr(_m, a) for a in _attrs):
        try:
            _il.reload(_m)
        except Exception:
            pass
        if not all(hasattr(_m, a) for a in _attrs):
            _STALE.append(_m.__name__ + ".py")

APP_VERSION = "v3.2.1（2026-11-02）"
st.set_page_config(page_title="股票訊號看板", page_icon="📈", layout="wide")
# 背景執行緒呼叫快取函式時 Streamlit 會印「missing ScriptRunContext」，不影響功能，關掉這個訊息
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)


@st.cache_resource
def task_pool_v2() -> cf.ThreadPoolExecutor:
    """上層工作（算機率、整理公司資料、預先載入）：會等下層結果，所以不能跟下層共用執行緒。"""
    return cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="task")


@st.cache_resource
def fetch_pool_v2() -> cf.ThreadPoolExecutor:
    """下層工作（實際去抓 K 線、新聞、營收…）：只抓資料、不等別人，所以永遠不會卡死。

    v2.4 以前上下層共用同一組 12 個執行緒：最愛一多，上層工作佔滿執行緒在等下層，
    下層卻排不到執行緒 → 整個 App 卡住（畫面一直「抓取資料中…」）。
    """
    return cf.ThreadPoolExecutor(max_workers=24, thread_name_prefix="fetch")


def bg(fn, *args):
    """丟出上層工作（主畫面用）。"""
    return task_pool_v2().submit(fn, *args)


def leaf(fn, *args):
    """丟出下層抓資料工作（上層工作裡面用）。"""
    return fetch_pool_v2().submit(fn, *args)


TIMEOUT = 90          # 單一資料來源最多等幾秒，超過就顯示錯誤，不再無限轉圈


def wait(fut, default=None, timeout: float = TIMEOUT):
    try:
        return fut.result(timeout=timeout)
    except cf.TimeoutError:
        if default is not None:
            return default
        raise TimeoutError(f"資料來源超過 {timeout} 秒沒有回應，請稍後按「清除快取並重新抓取」再試。")
    except Exception as e:     # 交給呼叫的地方顯示錯誤
        if default is not None:
            return default
        raise e

st.markdown("""
<style>
.block-container{padding-top:3.6rem;max-width:1200px}
.verdict{font-size:1.5rem;font-weight:700;margin:0}
.chip{display:inline-block;margin:2px 4px 2px 0;padding:2px 10px;border-radius:6px;font-size:.85rem;
      border:1px solid rgba(128,128,128,.35)}
.ai{border-left:3px solid #2f5bd3;padding:6px 0 6px 12px;line-height:1.75}
.small{color:gray;font-size:.82rem}
.card .lbl{color:gray;font-size:.82rem;margin-bottom:2px}
.card .val{font-size:1.55rem;font-weight:600;line-height:1.3;font-variant-numeric:tabular-nums}
.card .sub{font-size:.8rem;color:gray}
.live{border:1px solid rgba(128,128,128,.3);border-radius:10px;padding:10px 14px;margin:4px 0 10px}
.live .row{display:flex;flex-wrap:wrap;gap:6px 22px;align-items:baseline}
.live .px{font-size:1.9rem;font-weight:700;font-variant-numeric:tabular-nums}
[data-testid="InputInstructions"]{display:none!important}
/* 左側欄最愛：條列式，每列蓋一顆透明按鈕，整列都可以點 */
[class*="st-key-srow_"]{position:relative;gap:0}
[class*="st-key-srow_"] [class*="st-key-fav_"]{position:absolute!important;inset:0;z-index:2;margin:0;width:100%!important}
[class*="st-key-srow_"] [class*="st-key-fav_"] *{width:100%!important;height:100%!important}
[class*="st-key-srow_"] [class*="st-key-fav_"] button{opacity:0;cursor:pointer;min-height:0}
[class*="st-key-srow_"] [data-testid="stMarkdownContainer"] p{margin:0}
.st-key-srows{gap:0!important}
[class*="st-key-srow_"] [data-testid="stMarkdownContainer"]{margin-bottom:0!important}
.srow{display:flex;justify-content:space-between;align-items:center;padding:9px 10px;
      border-bottom:1px solid rgba(128,128,128,.22);border-radius:6px}
[class*="st-key-srow_"]:hover .srow{background:rgba(128,128,128,.12)}
.srow.on{background:rgba(47,91,211,.13);box-shadow:inset 3px 0 0 #2f5bd3}
.srow-code{font-weight:700;font-size:.95rem;line-height:1.25}
.srow-name{font-size:.8rem;color:gray;line-height:1.25;max-width:9.5em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.srow-r{text-align:right;font-variant-numeric:tabular-nums}
.srow-px{font-weight:700;font-size:.95rem;line-height:1.25}
.srow-ch{font-size:.8rem;line-height:1.25;white-space:nowrap}
.srow-lim{color:#fff;border-radius:4px;padding:0 4px;margin-right:4px;font-size:.72rem}
/* 畫面乾淨一點：隱藏右上角 Fork／GitHub、執行中的 Stop 小人、頂端彩色線，標題列改透明 */
[data-testid="stToolbarActions"],[data-testid="stAppDeployButton"],[data-testid="stStatusWidget"],
[data-testid="stDecoration"]{display:none!important}
header[data-testid="stHeader"]{background:transparent}
/* 左上角 LOGO（點了回首頁） */
.st-key-logo button{justify-content:flex-start;padding:2px 4px;margin:-8px 0 2px;border-radius:10px}
.st-key-logo button p{font-size:1.32rem;font-weight:800;letter-spacing:.02em}
.st-key-logo button:hover{background:rgba(128,128,128,.12)}
.st-key-logo button span[data-testid="stIconMaterial"]{font-size:1.9rem;color:#2f5bd3}
.lim{display:inline-block;color:#fff!important;padding:0 .4em;border-radius:5px;font-weight:700}
.lim-up{background:#d1352b}.lim-down{background:#14825a}
.lim-tag{display:inline-block;color:#fff;font-size:.75rem;font-weight:700;padding:1px 7px;border-radius:4px;margin-left:6px;vertical-align:middle}
.st-key-idx [data-testid="stMetricValue"]{font-size:1.45rem}
.st-key-idx [data-testid="stMetricLabel"] p{font-size:.85rem}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
</style>
""", unsafe_allow_html=True)


def secret(name: str) -> str:
    """依序讀取：.streamlit/secrets.toml（雲端則是 Secrets 設定）→ 環境變數。"""
    try:
        val = st.secrets.get(name, "")
    except Exception:          # 沒有 secrets.toml
        val = ""
    return str(val or os.environ.get(name, "")).strip()


# --- 選用的密碼保護：設定 APP_PASSWORD 後，開網頁要先輸入密碼 ---
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("股票訊號看板")
    with st.form("login"):
        pw = st.text_input("請輸入密碼", type="password")
        if st.form_submit_button("進入", type="primary"):
            if hmac.compare_digest(pw, APP_PASSWORD):
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("密碼錯誤")
    st.stop()

SAVED_TOKEN = secret("FINMIND_TOKEN")
company.set_sec_contact(secret("SEC_CONTACT"))
PERIODS = {"3 個月": 92, "6 個月": 183, "1 年": 365, "2 年": 730, "3 年": 1095, "5 年": 1825, "10 年": 3650}
DEFAULT_PERIOD = "3 個月"            # 每次打開預設看 3 個月
MIN_DAYS, MAX_DAYS = 5, 365 * 20
MODEL_DAYS = 730                     # 機率、評分、回測至少用 2 年資料，不受顯示期間影響


def parse_period(text: str) -> tuple[int, str]:
    """把「45」「6個月」「18個月」「3年」「250天」「12週」「2024-01-01」這類輸入轉成天數。只打數字＝天數。"""
    t = str(text or "").strip().replace(" ", "").lower()
    if t in {k.replace(" ", "") for k in PERIODS}:
        k = next(k for k in PERIODS if k.replace(" ", "") == t)
        return PERIODS[k], k
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})(?:[-/.月](\d{1,2})日?)?", t)
    if m:
        d0 = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        return (dt.date.today() - d0).days, f"{d0:%Y-%m-%d} 起"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(天|日|d|day|days|週|周|w|week|weeks|個月|月|m|mo|month|months|年|y|yr|year|years)?", t)
    if not m:
        raise ValueError(f"看不懂「{text}」，可以輸入例如：45（天）、3個月、18個月、3年、2024-01-01")
    n, u = float(m.group(1)), (m.group(2) or "天")
    mult = 1 if u in ("天", "日", "d", "day", "days") else 7 if u in ("週", "周", "w", "week", "weeks") \
        else 30.44 if u in ("個月", "月", "m", "mo", "month", "months") else 365.25
    unit_zh = {1: "天", 7: "週", 30.44: "個月", 365.25: "年"}[mult]
    return int(round(n * mult)), f"{n:g} {unit_zh}"


def on_period_change():
    try:
        d, label = parse_period(st.session_state.period_pick)
    except ValueError as e:
        st.session_state.store_err = str(e)
        st.session_state.period_pick = st.session_state.get("period_label", DEFAULT_PERIOD)
        return
    note = ""
    if d < MIN_DAYS:
        d, note = MIN_DAYS, f"（{label}太短，最少 {MIN_DAYS} 天）"
    elif d > MAX_DAYS:
        d, note = MAX_DAYS, "（最多 20 年）"
    st.session_state.period_days = d
    st.session_state.period_label = st.session_state.period_pick
    st.session_state.period_text = label if not note else (f"{d} 天" if d == MIN_DAYS else "20 年")
    if note:
        st.session_state.store_err = "資料期間" + note
    elif str(st.session_state.get("store_err", "")).startswith(("資料期間", "看不懂")):
        st.session_state.pop("store_err")
LIVE_EVERY = 60          # 盤中即時報價更新秒數
VIEWS = ["📈 市場總覽", "📰 盤後整理", "🔍 個股分析", "🧭 潛力股雷達"]
HOME, RECAP, STOCK, RADAR = VIEWS


def market_of(code: str) -> str:
    try:
        return engine.detect_market(code)
    except engine.DataError:
        return "?"


def colors(market: str, code: str = "") -> tuple[str, str]:
    """台股（含台股指數）紅漲綠跌，其他綠漲紅跌。"""
    red, green = "#d1352b", "#14825a"
    return (red, green) if market == "TW" or code in live.MIS_INDEX else (green, red)


# ----------------------------------------------------------------------------
# 資料快取：歷史資料 30 分鐘、即時報價 20 秒
# ----------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def name_table() -> dict:
    """代號 → 名稱（內建清單，不用連網）。"""
    tw = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tw_stocks.csv"), dtype=str)
    us = symbols.load_us()
    out = {r.code: {"name": r.name, "en": ""} for r in tw.itertuples()}
    out.update({r.code: {"name": r.cname, "en": r.name} for r in us.itertuples()})
    return out


def nice_name(code: str) -> str:
    """名稱；查不到（或是 NaN）時回傳空字串。"""
    n = quick_name(code)
    return n if isinstance(n, str) and n.lower() != "nan" else ""


def quick_name(code: str) -> str:
    if engine.is_index(code):
        n = indices.name_of(code)
        return "" if n == code else n
    return name_table().get(code, {}).get("name", "")


def news_name(code: str) -> str:
    """新聞搜尋用的名稱：台股用中文名，美股用英文公司名。"""
    t = name_table().get(code, {})
    return t.get("en") or t.get("name", "") if not code[0].isdigit() else t.get("name", "")


@st.cache_data(ttl=1800, show_spinner=False)
def load(code: str, days: int, token: str | None):
    market = engine.detect_market(code)
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    if market == "TW":
        df = engine.fetch_tw(code, start, token or None)
        name = quick_name(code) or engine.tw_name(code, token or None)
    elif market == "IDX":
        df = engine.fetch_index(code, start, token or None)
        name = quick_name(code)
    else:
        df = engine.fetch_us(code, start)
        name = quick_name(code) or engine.us_name(code)
    return market, name, df


@st.cache_data(ttl=1800, show_spinner=False)
def analyze(code: str, days: int, token: str | None):
    market, name, raw = load(code, days, token)
    return market, name, engine.analyze(raw, market)


@st.cache_data(ttl=900, show_spinner=False)
def get_news(code: str, market: str, name: str):
    if market == "IDX":
        q = {"^TWII": "台股 加權指數", "^TWOII": "櫃買指數 上櫃"}.get(code, f"{name or code}")
        lang = "zh" if code in ("^TWII", "^TWOII") or not name.isascii() else "en"
        return news._dedupe(news._google(f"{q} when:3d", lang))[:30]
    return news.fetch(code, market, name)


@st.cache_data(ttl=43200, show_spinner=False)
def get_rev(code: str, token: str):
    return prob.revenue(code, token or None)


@st.cache_data(ttl=1800, show_spinner=False)
def get_ov():
    try:
        return prob.overnight()
    except Exception:
        return {"ok": False, "nq": 0.0}


@st.cache_data(ttl=86400, show_spinner=False)
def get_groups(token: str):
    return symbols.load_tw(token or None)


@st.cache_data(ttl=86400, show_spinner=False)
def get_registry():
    return company.tw_registry()


@st.cache_data(ttl=21600, show_spinner=False)
def get_company(code: str, market: str, name: str, token: str):
    if market == "TW":
        f_g, f_reg = leaf(get_groups, token or ""), leaf(get_registry)
        g = wait(f_g, pd.DataFrame(columns=["code", "group"]))
        grp = g.loc[g["code"] == code, "group"]
        return company.tw(code, name, wait(f_reg, {}), token or None, grp.iloc[0] if len(grp) else "")
    t = name_table().get(code, {})
    return company.us(code, " ".join(x for x in [t.get("name", ""), t.get("en", "")] if x))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _tw_recap_cached(token: str, favs: tuple, bucket: str):
    return recap.tw(get_groups(token), token or None, list(favs))


def tw_recap_bucket(now: dt.datetime | None = None) -> str:
    """決定盤後資料多久重抓一次（換一個 bucket 就會重抓）。

    平日 13:30～18:00：每 5 分鐘（證交所收盤行情約 14:00～14:30 公布、法人與融資約 15:00～16:30 公布）
    其他時間：每 30 分鐘
    """
    now = now or live.now("TW")
    t = now.hour * 60 + now.minute
    if now.weekday() < 5 and 13 * 60 + 30 <= t < 18 * 60:
        return f"{now:%Y%m%d}-{t // 5}"
    return f"{now:%Y%m%d}-{t // 30}"


def get_tw_recap(token: str, favs: tuple):
    return _tw_recap_cached(token, favs, tw_recap_bucket())


def tw_recap_note(date_str: str, now: dt.datetime | None = None) -> str:
    """顯示的不是今天的資料時，說明原因與大概什麼時候會更新。"""
    now = now or live.now("TW")
    if now.weekday() >= 5 or date_str == f"{now:%Y-%m-%d}":
        return ""
    t = now.hour * 60 + now.minute
    shown = f"{date_str[5:7]}/{date_str[8:]}"
    if t < 13 * 60 + 30:
        return f"今天還沒收盤（13:30 收盤），目前顯示的是上一個交易日 {shown} 的盤後整理。"
    if t < 15 * 60 + 30:
        return (f"今天（{now:%m/%d}）的收盤行情證交所大約 14:00～14:30 公布，目前先顯示 {shown}。"
                "收盤後每 5 分鐘會自動重抓，也可以按右邊的「重新抓取」。")
    return (f"證交所還沒有今天（{now:%m/%d}）的資料，目前顯示 {shown}。"
            "如果今天是休市日（國定假日、颱風假），這是正常的。")


@st.cache_data(ttl=1800, show_spinner=False)
def get_us_recap(favs: tuple):
    return recap.us(list(favs))


@st.cache_data(ttl=900, show_spinner=False)
def get_prob(code: str, days: int, token: str | None):
    market = engine.detect_market(code)
    f_an = leaf(analyze, code, days, token)
    f_news = leaf(get_news, code, market, news_name(code))
    f_rev = leaf(get_rev, code, token or "") if market == "TW" else None
    f_ov = leaf(get_ov)
    _, _, r = wait(f_an)
    items = wait(f_news, [])
    rev = wait(f_rev, pd.DataFrame()) if f_rev else None
    return prob.estimate(r, code, items, rev, wait(f_ov, {"ok": False, "nq": 0.0}))


def warm(codes, token: str, days: int = 730, company_too: bool = False):
    """背景預先載入：使用者點進去時資料已經在快取裡，畫面會直接出現。"""
    futs = []
    for c in codes:
        futs.append(bg(get_prob, c, days, token))
        if company_too and engine.detect_market(c) != "IDX":
            futs.append(bg(get_company, c, engine.detect_market(c), news_name(c), token or ""))
    return futs


def with_prob(r: dict, P: dict | None) -> dict:
    if not P:
        return r
    return {**r, "p1": P["next"]["p_up"], "p10": P["h10"]["p_up"]}


def price_html(price: float, state: str, color: str, size: str = "") -> str:
    """漲停紅底白字、跌停綠底白字。"""
    txt = f"{price:,.2f}"
    if state:
        return (f"<span class='lim lim-{state}' style='{size}'>{txt}</span>"
                f"<span class='lim-tag lim-{state}'>{'漲停' if state == 'up' else '跌停'}</span>")
    return f"<span style='color:{color};{size}'>{txt}</span>"


def kv_html(rows) -> str:
    out = []
    for k, v in rows:
        v = str(v)
        val = f"<a href='{v}' target='_blank'>{v}</a>" if v.startswith("http") else v
        out.append("<div style='display:flex;gap:10px;padding:3px 0;border-bottom:1px dashed rgba(128,128,128,.2)'>"
                   f"<span class='small' style='min-width:6.5em'>{k}</span>"
                   f"<span style='word-break:break-all'>{val}</span></div>")
    return "".join(out)


@st.cache_data(ttl=60, show_spinner=False)
def get_intraday(code: str, market: str):
    return session.intraday(code, market)


@st.cache_data(ttl=20, show_spinner=False)
def get_quotes(codes: tuple[str, ...]) -> dict:
    return live.quotes(list(codes), market_of)


# ----------------------------------------------------------------------------
# 我的最愛
# ----------------------------------------------------------------------------
fav_store = store.make_store(secret("GITHUB_TOKEN"), secret("GITHUB_REPO"))
if "favs" not in st.session_state:
    try:
        _data = fav_store.load()
    except store.StoreError as e:
        _data = {"favorites": list(store.DEFAULT), "positions": {}, "indices": list(store.DEFAULT_INDICES),
                 "groups": store.clean_groups(None, store.DEFAULT)}
        st.session_state.store_err = str(e)
    st.session_state.groups = _data.get("groups") or store.clean_groups(None, _data["favorites"])
    st.session_state.favs = store.flatten(st.session_state.groups)
    st.session_state.positions = _data["positions"]
    st.session_state.indices = _data.get("indices", list(store.DEFAULT_INDICES))
    st.session_state.radar_log = _data.get("radar_log", [])


def save_favs():
    """群組是正本；favs（全部最愛的平面清單）一律由群組重新產生。"""
    st.session_state.groups = store.clean_groups(st.session_state.groups)
    st.session_state.favs = store.flatten(st.session_state.groups)
    st.session_state.manage_open = True        # 在首頁調整清單時，管理面板保持展開
    try:
        fav_store.save({"favorites": st.session_state.favs, "groups": st.session_state.groups,
                        "positions": st.session_state.positions, "indices": st.session_state.indices,
                        "radar_log": st.session_state.get("radar_log", [])})
        st.session_state.pop("store_err", None)
    except store.StoreError as e:
        st.session_state.store_err = str(e) + "（這次的變更只在目前畫面有效）"


def group_names() -> list[str]:
    return [g["name"] for g in st.session_state.groups]


def group_of(code: str) -> str | None:
    """第一個含有這檔股票的群組。"""
    return next((g["name"] for g in st.session_state.groups if code in g["codes"]), None)


def groups_of(code: str) -> list[str]:
    """含有這檔股票的所有群組（同一檔可以在好幾個群組）。"""
    return [g["name"] for g in st.session_state.groups if code in g["codes"]]


def _grp(name: str) -> dict | None:
    return next((g for g in st.session_state.groups if g["name"] == name), None)


def current_group() -> str | None:
    """左側欄下拉選單目前選的群組；選「全部」時回傳 None。"""
    g = st.session_state.get("side_group")
    return g if g in group_names() else None


def fav_add(code: str, group: str | None = None):
    """加到指定群組（已經在那個群組就不重複加）；沒指定就加到第一個群組。"""
    g = _grp(group or "") or st.session_state.groups[0]
    if code not in g["codes"]:
        g["codes"].append(code)
        save_favs()


def fav_remove(code: str, group: str | None = None):
    """指定群組就只從那個群組移除；沒指定就從所有群組移除。"""
    for g in st.session_state.groups:
        if (group is None or g["name"] == group) and code in g["codes"]:
            g["codes"].remove(code)
    save_favs()


def fav_move(code: str, step: int, group: str):
    """在群組內上移（-1）或下移（+1）；到頂／到底時移到上一個／下一個群組。"""
    gs = st.session_state.groups
    gi = group_names().index(group)
    lst = gs[gi]["codes"]
    i = lst.index(code)
    j = i + step
    if 0 <= j < len(lst):
        lst[i], lst[j] = lst[j], lst[i]
    elif 0 <= gi + step < len(gs):
        lst.remove(code)
        nxt = gs[gi + step]["codes"]
        if code not in nxt:
            nxt.append(code) if step < 0 else nxt.insert(0, code)
    save_favs()


def fav_to_group(code: str, group: str, wkey: str):
    target = st.session_state.get(wkey)
    if target and target != group:
        _grp(group)["codes"].remove(code)
        if code not in _grp(target)["codes"]:
            _grp(target)["codes"].append(code)
        save_favs()


def group_add():
    st.session_state.manage_open = True
    name = (st.session_state.get("new_group") or "").strip()
    st.session_state.new_group = ""
    if not name:
        return
    if name in group_names():
        st.session_state.store_err = f"已經有「{name}」這個群組了。"
        return
    st.session_state.groups.append({"name": name, "codes": []})
    save_favs()


def group_rename(old: str):
    new = (st.session_state.get(f"gname_{old}") or "").strip()
    if not new or new == old:
        return
    if new in group_names():
        st.session_state.store_err = f"已經有「{new}」這個群組了。"
        return
    _grp(old)["name"] = new
    if st.session_state.get("fav_view") == old:
        st.session_state.fav_view = new
    save_favs()


def group_move(name: str, step: int):
    gs = st.session_state.groups
    i = group_names().index(name)
    j = i + step
    if 0 <= j < len(gs):
        gs[i], gs[j] = gs[j], gs[i]
        save_favs()


def group_delete(name: str):
    gs = st.session_state.groups
    if len(gs) <= 1:
        return
    i = group_names().index(name)
    g = gs.pop(i)
    gs[0]["codes"].extend(g["codes"])        # 群組裡的股票移到（剩下的）第一個群組，不會不見
    if st.session_state.get("fav_view") == name:
        st.session_state.fav_view = "全部"
    save_favs()


def toggle_fav(code: str):
    """依左側選中的群組判斷：在這個群組裡就移除，不在就加進這個群組。"""
    g = current_group()
    if g is None:                                   # 「全部」：在任何群組就全部移除
        if code in st.session_state.favs:
            fav_remove(code)
            st.toast(f"已從所有群組移除 {code}")
        else:
            fav_add(code, st.session_state.get("add_group"))
            st.toast(f"已加入最愛 {code}（{group_of(code)}）")
        return
    if code in _grp(g)["codes"]:
        fav_remove(code, g)
        st.toast(f"已從「{g}」移除 {code}")
    else:
        fav_add(code, g)
        st.toast(f"已加入「{g}」：{code}")


def open_stock(code: str):
    st.session_state.manage_open = False
    st.session_state.collapse_sb = True        # 手機上選完自動收起左側選單
    st.session_state.code = code
    st.session_state.nav = STOCK
    st.session_state.jump = True      # 從 fragment 裡的按鈕觸發時，需要整頁重跑才會換頁


def go_home():
    st.session_state.nav = HOME
    st.session_state.collapse_sb = True


def on_nav_change():
    st.session_state.collapse_sb = True
    if st.session_state.nav != HOME:
        st.session_state.manage_open = False


def open_manage():
    st.session_state.nav = HOME
    st.session_state.manage_open = True
    st.session_state.collapse_sb = True


def drag_editor(prefix: str):
    """拖曳排序（可跨群組）。拖完先暫存，按「確認」才儲存，按「取消」還原。"""
    groups = st.session_state.groups
    pend_key, reset_key = f"{prefix}_pending", f"{prefix}_reset"
    args = [{"name": g["name"], "items": [{"code": c, "name": nice_name(c)} for c in g["codes"]]} for g in groups]
    res = FAVSORT(groups=args, key=f"favsort_{prefix}_{st.session_state.get(reset_key, 0)}", default=None)
    committed = [{"name": g["name"], "codes": list(g["codes"])} for g in groups]
    if res and isinstance(res, dict) and res.get("groups") is not None:
        new = [{"name": x["name"], "codes": list(x["codes"])} for x in res["groups"]]
        if new != committed and [x["name"] for x in new] == [x["name"] for x in committed]:
            st.session_state[pend_key] = new
        else:
            st.session_state.pop(pend_key, None)
    pending = st.session_state.get(pend_key)
    if pending:
        st.info("已調整順序，按「確認」才會儲存。")
    c1, c2 = st.columns(2)
    if c1.button("✅ 確認", key=f"{prefix}_ok", type="primary", width="stretch", disabled=not pending):
        st.session_state.groups = st.session_state.pop(pend_key)
        save_favs()
        st.session_state[reset_key] = st.session_state.get(reset_key, 0) + 1
        st.toast("已儲存新的順序")
        return True
    if c2.button("↩ 取消", key=f"{prefix}_cancel", width="stretch"):
        st.session_state.pop(pend_key, None)
        st.session_state[reset_key] = st.session_state.get(reset_key, 0) + 1   # 重新載入元件 → 回到原本順序
        return True
    return False


def side_fav_list():
    """左側欄的最愛：標題位置是群組下拉選單，下面一檔一列（代號、名稱、現價、漲跌），整列都可以點。"""
    names = group_names()
    NEW = "＋ 新增群組"
    opts = ((["全部"] + names) if len(names) > 1 else names) + [NEW]
    if st.session_state.get("side_group") not in opts:
        cur = group_of(st.session_state.code)            # 預設打開目前這檔股票所在的群組
        st.session_state.side_group = cur if cur in opts else opts[0]
    cnt = {"全部": len(st.session_state.favs), **{g["name"]: len(g["codes"]) for g in st.session_state.groups}}
    st.selectbox("⭐ 我的最愛", opts, key="side_group",
                 format_func=lambda x: x if x == NEW else f"📁 {x}（{cnt.get(x, 0)}）",
                 help="選擇要看的群組；選最下面的「＋ 新增群組」可以建立新群組（例如：群組一、群組二）。")
    if st.session_state.side_group == NEW:
        st.text_input("新群組名稱", key="side_new_group", placeholder="例如：群組一、半導體、存股")

        def _create():
            name = (st.session_state.get("side_new_group") or "").strip()
            st.session_state.side_new_group = ""
            if not name:
                return
            if name in group_names():
                st.session_state.store_err = f"已經有「{name}」這個群組了。"
                return
            st.session_state.groups.append({"name": name, "codes": []})
            save_favs()
            st.session_state.side_group = name
            st.toast(f"已建立群組「{name}」，可以用「排序／移動」把股票拖進來")
        st.button("建立", key="side_new_ok", type="primary", width="stretch", on_click=_create)
        return
    if st.session_state.get("side_edit"):
        st.caption("按住拖曳調整順序，也可以拖到別的群組；按「確認」才會儲存。")
        if drag_editor("side"):
            st.session_state.side_edit = False
            st.rerun()
        return
    sel = st.session_state.side_group
    codes = list(st.session_state.favs) if sel == "全部" else list(_grp(sel)["codes"])
    if not codes:
        st.caption("這個群組還沒有股票。")
        return
    try:                                                     # 報價最多等 2 秒，等不到先顯示名稱
        q = bg(get_quotes, tuple(codes)).result(timeout=2)
    except Exception:
        q = {}
    active = st.session_state.code if st.session_state.nav == STOCK else None
    # 超過 8 檔時清單放在固定高度的框裡，框內自己捲動，側邊欄不會越拉越長
    many = len(codes) > 8
    box = st.container(key="srows", height=8 * 54 + 6 if many else None, border=False) if many else st.container(key="srows")
    if many:
        st.caption(f"共 {len(codes)} 檔，在清單裡上下滑動可看全部")
    for c in codes:
        mk = market_of(c)
        up, down = colors(mk, c)
        qq = q.get(c) or {}
        nm = nice_name(c) or str(qq.get("name") or "")
        px, prev, ch = qq.get("price"), qq.get("prev"), qq.get("chg")
        if px:
            col = up if (ch or 0) > 0 else down if (ch or 0) < 0 else "gray"
            arrow = "▲" if (ch or 0) > 0 else "▼" if (ch or 0) < 0 else ""
            lim = live.limit_state(px, prev, mk, qq) if prev else None
            px_s = f"{px:,.0f}" if px >= 10000 else f"{px:,.2f}"
            badge = (f"<span class='srow-lim' style='background:{'#d1352b' if lim == 'up' else '#14825a'}'>"
                     f"{'漲停' if lim == 'up' else '跌停'}</span>") if lim else ""
            diff = (px - prev) if prev else None             # 漲跌多少元
            diff_s = "" if diff is None else (f"{diff:+,.0f}" if abs(diff) >= 1000 else f"{diff:+,.2f}") + "　"
            right = (f"<div class='srow-px' style='color:{col}'>{px_s}</div>"
                     f"<div class='srow-ch' style='color:{col}'>{badge}{arrow}{diff_s}{(ch or 0) * 100:+.2f}%</div>")
        else:
            right = "<div class='srow-px' style='color:gray'>—</div><div class='srow-ch' style='color:gray'>暫無報價</div>"
        with box.container(key=f"srow_{c}"):
            st.markdown(f"<div class='srow{' on' if c == active else ''}'>"
                        f"<div class='srow-l'><div class='srow-code'>{c}</div><div class='srow-name'>{nm}</div></div>"
                        f"<div class='srow-r'>{right}</div></div>", unsafe_allow_html=True)
            st.button(f"{c} {nm}", key=f"fav_{c}", on_click=open_stock, args=(c,))


@st.cache_data(ttl=86400, show_spinner=False)
def search_options(token: str):
    return symbols.build_options(symbols.load_tw(token or None), symbols.load_us())


@st.cache_data(ttl=86400, show_spinner=False)
def search_options_local():
    tw = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tw_stocks.csv"), dtype=str)
    return symbols.build_options(tw, symbols.load_us())


_f_opts = st.session_state.get("_f_opts")
if _f_opts is None:
    _f_opts = st.session_state["_f_opts"] = bg(search_options, SAVED_TOKEN)
SEARCH_OPTS, NAME2CODE = (_f_opts.result() if _f_opts.done() and not _f_opts.exception()
                          else search_options_local())


def _pick(key: str) -> str | None:
    """讀取搜尋框的值並清空，回傳代號；找不到時記錄錯誤訊息。"""
    text = st.session_state.get(key)
    st.session_state[key] = None
    st.session_state.pop("store_err", None)
    if not text:
        return None
    code = symbols.resolve(text, NAME2CODE)
    if not code or market_of(code) == "?":
        hits = [o.split("・")[0] for o in SEARCH_OPTS if str(text).upper() in o.upper()][:6]
        st.session_state.store_err = (f"「{text}」符合多檔：{'、'.join(hits)}…，請從下拉選單點選。" if len(hits) > 1 else
                                      f"找不到「{text}」。台股可輸入代號或名稱，美股可輸入代號（例如 MSFT）或英文公司名。")
        return None
    return code


def on_search():
    code = _pick("search")
    if code:
        open_stock(code)


def add_fav_from_input():
    code = _pick("add_search")
    if code:
        fav_add(code, st.session_state.get("add_group"))


def search_box(key: str, label: str, on_change):
    kw = {"filter_mode": "contains"} if "filter_mode" in inspect.signature(st.selectbox).parameters else {}
    st.selectbox(label, SEARCH_OPTS, index=None, key=key, on_change=on_change, accept_new_options=True,
                 placeholder="輸入代號、名稱或產業，例如：台積、2330、半導體、輝達、MSFT",
                 help="打幾個字就會列出符合的股票。清單外的美股可直接打代號或英文公司名後按 Enter。", **kw)


def remove_fav(code: str):
    fav_remove(code)


def on_table_select():
    rows = st.session_state.fav_table.selection.rows
    if rows:
        open_stock(st.session_state.fav_order[rows[0]])
        st.session_state.jump = True      # 表格在 fragment 裡，需要整頁重跑才會換頁


# ----------------------------------------------------------------------------
# 側邊欄
# ----------------------------------------------------------------------------
# 網址記住目前頁面、股票與資料期間：按瀏覽器重新整理（F5）會留在原地，不會跳回首頁
QP_VIEW = {"home": HOME, "recap": RECAP, "stock": STOCK, "radar": RADAR}
QP_KEY = {v: k for k, v in QP_VIEW.items()}
if "nav" not in st.session_state:
    _qp = st.query_params
    _v = QP_VIEW.get(_qp.get("v", ""), HOME)
    _c = (_qp.get("s") or "").strip()
    if _c and market_of(_c) != "?":
        st.session_state.code = _c
    elif _v == STOCK:
        _v = HOME
    st.session_state.nav = _v
    _p = _qp.get("p")
    if _p and "period_pick" not in st.session_state:
        try:
            _d, _t = parse_period(_p)
            st.session_state.period_pick = _p
            st.session_state.period_text = _t
            st.session_state.period_label = _p
            st.session_state.period_days = min(max(_d, MIN_DAYS), MAX_DAYS)
        except ValueError:
            pass
if "code" not in st.session_state:
    st.session_state.code = (st.session_state.favs or ["2330"])[0]

with st.sidebar:
    st.button("股票訊號看板", key="logo", icon=":material/candlestick_chart:", type="tertiary",
              on_click=go_home, help="回到首頁（市場總覽）")
    st.radio("頁面", VIEWS, key="nav", label_visibility="collapsed", on_change=on_nav_change)
    search_box("search", "🔍 搜尋股票", on_search)
    if "period_pick" not in st.session_state:
        st.session_state.period_pick, st.session_state.period_days = DEFAULT_PERIOD, PERIODS[DEFAULT_PERIOD]
    popts = list(PERIODS)
    if st.session_state.period_pick not in popts:
        popts = [st.session_state.period_pick] + popts
    st.selectbox("資料期間", popts, key="period_pick", on_change=on_period_change, accept_new_options=True,
                 help="可直接選，也可以自己打後按 Enter：直接打數字就是天數（例如 45 = 近 45 天），"
                      "也可以打 18個月、4年、12週、2024-01-01（從這天到今天）。"
                      "這裡決定 K 線與「期間表現」看多久；機率與評分固定用至少 2 年資料計算，不受影響。")

    if st.session_state.favs:
        side_fav_list()
    else:
        st.markdown("**⭐ 我的最愛**")
        st.caption("還沒有收藏。到個股頁按「☆ 加入最愛」。")
    if st.session_state.favs and not st.session_state.get("side_edit"):
        st.button("排序／移動", key="side_edit_btn", icon=":material/swap_vert:", type="tertiary",
                  on_click=lambda: st.session_state.update(side_edit=True),
                  help="拖曳調整順序或移到別的群組，按確認才儲存")
    st.button("管理清單／群組", key="side_manage", icon=":material/tune:", type="tertiary", on_click=open_manage,
              help="到首頁的「管理清單」新增、刪除、建立群組、改名")

    st.divider()
    token = SAVED_TOKEN          # FinMind token 從 .streamlit/secrets.toml 讀取
    if st.button("清除快取並重新抓取", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"最愛儲存於：{fav_store.label}")
    st.caption("歷史資料每 30 分鐘更新；開盤時報價每 60 秒自動更新。")
    st.caption(f"版本 {APP_VERSION}")

st.session_state.pop("jump", None)
if st.session_state.pop("collapse_sb", False):
    # 只有手機（窄螢幕、側邊欄蓋在畫面上）才自動收起；電腦上側邊欄保持打開
    st.session_state._collapse_n = st.session_state.get("_collapse_n", 0) + 1
    SBCOLLAPSE(nonce=st.session_state._collapse_n, key=f"sbc_{st.session_state._collapse_n}", default=None)
_want = {"v": QP_KEY[st.session_state.nav]}
if st.session_state.nav == STOCK:
    _want["s"] = st.session_state.code
if st.session_state.get("period_pick", DEFAULT_PERIOD) != DEFAULT_PERIOD:
    _want["p"] = st.session_state.period_pick
if {k: st.query_params.get(k) for k in st.query_params} != _want:
    st.query_params.from_dict(_want)
if st.session_state.nav != HOME:
    st.button("🏠 回首頁", on_click=go_home, type="tertiary")

if st.session_state.get("store_err"):
    st.warning(st.session_state.store_err)
if _STALE:
    st.error(f"偵測到舊版程式檔：{'、'.join(_STALE)}。請把新版的這些檔案上傳到 GitHub（覆蓋舊檔），"
             "再到右下角 Manage app →「⋮」→ Reboot app。")

days = int(st.session_state.get("period_days", PERIODS[DEFAULT_PERIOD]))
fetch_days = max(days, MODEL_DAYS)       # 實際抓的資料長度（模型需要）
any_open = live.is_open("TW") or live.is_open("US")


def pos_analyze(df, r, price, pos):
    bd = dt.date.fromisoformat(pos["date"]) if pos.get("date") else None
    return position.analyze(df, r, price, pos["cost"], pos.get("shares") or 0, bd, pos.get("risk", 8) / 100)


def fmt_chg(x):
    return "—" if x is None or pd.isna(x) else f"{'▲' if x > 0 else '▼' if x < 0 else ''}{x * 100:+.2f}%"


def fmt_move(price, prev, chg=None) -> str:
    """▼-0.40（-4.46%）：漲跌點數加百分比。"""
    if chg is None and price and prev:
        chg = price / prev - 1
    if chg is None or pd.isna(chg):
        return "—"
    arrow = "▲" if chg > 0 else "▼" if chg < 0 else ""
    if not prev or pd.isna(prev):
        return f"{arrow}{chg * 100:+.2f}%"
    d = price - prev
    return f"{arrow}{d:+,.2f}（{chg * 100:+.2f}%）"


# ============================================================================
# 頁面一：我的最愛總覽
# ============================================================================
if st.session_state.nav == HOME:
    st.title("📈 市場總覽")
    st.caption(f"台股 {live.status_text('TW')}｜美股 {live.status_text('US')}")

    # ---------- 市場指數 ----------
    @st.cache_data(ttl=60, show_spinner=False)
    def get_indices(syms: tuple):
        return indices.fetch(list(syms), token=SAVED_TOKEN)

    def on_indices_change():
        chosen = []
        for x in st.session_state.idx_pick or []:
            sym = str(x).split("｜")[0].strip().upper()
            if sym and sym not in chosen:
                chosen.append(sym)
        st.session_state.indices = chosen
        save_favs()

    @st.fragment(run_every=LIVE_EVERY if any_open else None)
    def index_board():
        if st.session_state.pop("jump", False):
            st.rerun(scope="app")
        syms = list(st.session_state.indices)
        if not syms:
            st.caption("還沒有選指數，展開下方「調整指數」加入。")
            return
        data = get_indices(tuple(syms))
        per_row = 3              # 一列 3 張，漲跌點數與百分比才放得下
        for i in range(0, len(syms), per_row):
            cols = st.columns(per_row)
            for col, sym in zip(cols, syms[i:i + per_row]):
                d = data.get(sym)
                with col:
                    if not d:
                        st.metric(indices.name_of(sym), "—", "取不到資料", delta_color="off", border=True)
                        continue
                    v = d["price"]
                    st.metric(indices.name_of(sym), f"{v:,.0f}" if v >= 10000 else f"{v:,.2f}",
                              None if d["chg"] is None else f"{d['price'] - d['prev']:+,.2f} ({d['chg'] * 100:+.2f}%)",
                              delta_color="inverse" if indices.is_tw(sym) else "normal",
                              chart_data=d["spark"] or None, chart_type="area", border=True,
                              help=f"{sym}｜{d['source']} {d['time']}")
                    st.button("K 線與分析 ›", key=f"idx_open_{sym}", on_click=open_stock, args=(sym,),
                              type="tertiary", width="stretch")

    # 背景預先載入：點進個股、盤後整理時就不用再等
    if not st.session_state.get("_warmed") or (dt.datetime.now() - st.session_state["_warmed"]).seconds > 900:
        st.session_state["_warmed"] = dt.datetime.now()
        warm(st.session_state.favs, token, company_too=True)
        bg(get_ov)
        bg(get_tw_recap, token, tuple(st.session_state.favs))
        bg(get_us_recap, tuple(st.session_state.favs))
        warm(["^TWII"], token)

    st.markdown("#### 📊 市場指數")
    with st.container(key="idx"):
        index_board()
    with st.expander("調整指數"):
        labels = {k: f"{k}｜{v[0]}" for k, v in indices.CATALOG.items()}
        current = [labels.get(x, x) for x in st.session_state.indices]
        options = list(dict.fromkeys(current + list(labels.values())))
        st.multiselect("顯示的指數（拖不動順序：先移除再加入，新的會排在最後）", options, default=current,
                       key="idx_pick", on_change=on_indices_change, accept_new_options=True,
                       placeholder="選擇或輸入 Yahoo 代號，例如 ^FTSE")
        st.caption("清單外的指數可以直接輸入 Yahoo Finance 代號後按 Enter，例如 ^FTSE（英國）、^BSESN（印度）、^AXJO（澳洲）。"
                   "台股指數紅漲綠跌，其他市場綠漲紅跌。")

    st.markdown("#### ⭐ 我的最愛")
    _gn = group_names()
    if len(_gn) > 1:
        _views = ["全部"] + _gn
        if st.session_state.get("fav_view") not in _views:
            st.session_state.fav_view = "全部"
        _lbl = {"全部": f"全部（{len(st.session_state.favs)}）",
                **{g["name"]: f"{g['name']}（{len(g['codes'])}）" for g in st.session_state.groups}}
        if hasattr(st, "segmented_control"):
            st.segmented_control("群組", _views, key="fav_view", format_func=lambda x: _lbl[x],
                                 label_visibility="collapsed")
            if not st.session_state.get("fav_view"):        # 再點一次會取消選取 → 回到全部
                st.session_state.fav_view = "全部"
        else:
            st.radio("群組", _views, key="fav_view", format_func=lambda x: _lbl[x], horizontal=True,
                     label_visibility="collapsed")
    fav_view = st.session_state.get("fav_view") or "全部"
    favs = list(st.session_state.favs) if fav_view == "全部" or len(_gn) <= 1 else list(_grp(fav_view)["codes"])
    if not st.session_state.favs:
        st.info("清單是空的。在下方「管理清單」輸入代號加入，或到個股頁按「☆ 加入最愛」。")
    elif not favs:
        st.info(f"「{fav_view}」群組還沒有股票，到下方「管理清單」加入或從別的群組移過來。")

    @st.fragment(run_every=LIVE_EVERY if any_open else None)
    def fav_board():
        if st.session_state.pop("jump", False):
            st.rerun(scope="app")
        f_q = bg(get_quotes, tuple(favs)) if favs else None
        f_prob = {c: bg(get_prob, c, 730, token) for c in favs}        # 所有最愛同時計算
        q = wait(f_q, {}) if f_q else {}
        rows, order = [], []
        prog = st.empty()
        for i, c in enumerate(favs):
            row = {"代號": c, "名稱": "", **({"群組": "、".join(groups_of(c))} if fav_view == "全部" and len(_gn) > 1 else {}), "市場": {"TW": "台股", "IDX": "指數"}.get(market_of(c), "美股"),
                   "現價": None, "漲跌": "—", "明日漲機率": None, "10日漲機率": None, "10日區間": "", "停損參考": None, "成本": "", "持倉損益": "", "狀態": ""}
            try:
                try:
                    Pc = f_prob[c].result(timeout=TIMEOUT)
                except engine.DataError:
                    raise
                except Exception:
                    Pc = None
                market, name, r = analyze(c, 730, token)
                r = with_prob(r, Pc)
                row.update({"名稱": name,
                            "明日漲機率": round(Pc["next"]["p_up"] * 100) if Pc else None,
                            "10日漲機率": round(Pc["h10"]["p_up"] * 100) if Pc else None,
                            "10日區間": f"{r['d10']['p10'] * 100:+.1f}% ~ {r['d10']['p90'] * 100:+.1f}%",
                            "停損參考": round(r["stop"], 2), "現價": r["close"], "漲跌": fmt_chg(r["chg"]),
                            "狀態": f"收盤 {r['asof']}"})
                qq = q.get(c)
                if qq:
                    lim = live.limit_state(qq["price"], qq.get("prev"), market, qq)
                    row.update({"現價": qq["price"], "漲跌": fmt_chg(qq["chg"]) + {"up": " 漲停", "down": " 跌停"}.get(lim, ""),
                                "名稱": name or qq.get("name", ""),
                                "狀態": f"即時 {qq['time']}" if live.is_open(live.session_of(c, market)) else f"最新 {qq['time']}"})
                pos = st.session_state.positions.get(c)
                if pos:
                    pp = pos_analyze(r["df"], r, row["現價"], pos)
                    row.update({"成本": f"{pos['cost']:,.2f}", "持倉損益": fmt_chg(pp["pnl"]), "停損參考": round(pp["stop"], 2)})
                    if pp["triggered"]:
                        row["狀態"] += "｜⚠ 跌破我的停損"
                elif qq and qq["price"] < r["stop"]:
                    row["狀態"] += "｜⚠ 跌破停損參考"
            except engine.DataError as e:
                row["狀態"] = "錯誤：" + str(e)[:40]
            rows.append(row)
            order.append(c)
        prog.empty()
        if not rows:
            return
        st.session_state.fav_order = order
        table = pd.DataFrame(rows)

        def color(row):
            up, down = colors("TW" if row["市場"] == "台股" else "US", row["代號"])
            styles = [""] * len(row)
            ch = row["漲跌"]
            pl = row["持倉損益"]
            if pl.startswith("▲"):
                styles[row.index.get_loc("持倉損益")] = f"color:{up}"
            elif pl.startswith("▼"):
                styles[row.index.get_loc("持倉損益")] = f"color:{down}"
            if ch.endswith("漲停") or ch.endswith("跌停"):     # 漲停紅底、跌停綠底
                bg = "#d1352b" if ch.endswith("漲停") else "#14825a"
                for k in ("現價", "漲跌"):
                    styles[row.index.get_loc(k)] = f"background-color:{bg};color:#fff;font-weight:700"
            elif ch.startswith("▲"):
                styles[row.index.get_loc("漲跌")] = f"color:{up}"
            elif ch.startswith("▼"):
                styles[row.index.get_loc("漲跌")] = f"color:{down}"
            for k in ("明日漲機率", "10日漲機率"):
                v = row[k]
                if pd.notna(v):
                    styles[row.index.get_loc(k)] = f"color:{up}" if v >= 56 else f"color:{down}" if v <= 44 else ""
            if "⚠" in row["狀態"]:
                styles[row.index.get_loc("狀態")] = "color:#b7791f"
            return styles

        st.dataframe(table.style.apply(color, axis=1), hide_index=True, width="stretch",
                     key="fav_table", on_select=on_table_select, selection_mode="single-row",
                     column_config={"現價": st.column_config.NumberColumn(format="%.2f"),
                                    "停損參考": st.column_config.NumberColumn(format="%.2f"),
                                    "明日漲機率": st.column_config.NumberColumn(format="%d%%"),
                                    "10日漲機率": st.column_config.NumberColumn(format="%d%%")})
        st.caption("點選任一列左側的方框即可開啟該股分析。"
                   + (f" 盤中每 {LIVE_EVERY} 秒自動更新報價。" if any_open else ""))

    fav_board()

    # expanded 參數每次都要一樣，否則 Streamlit 會把面板重建成收起來 → 頁面跳回最上面
    with st.expander("管理清單（群組、排序）", expanded=bool(st.session_state.get("manage_open", False))):
        _gn = group_names()
        a1, a2 = st.columns([3, 1.4], vertical_alignment="bottom")
        with a1:
            search_box("add_search", "新增到最愛", add_fav_from_input)
        if st.session_state.get("add_group") not in _gn:
            st.session_state.add_group = fav_view if fav_view in _gn else _gn[0]
        a2.selectbox("加到群組", _gn, key="add_group")
        b1, b2 = st.columns([3, 1.4], vertical_alignment="bottom")
        b1.text_input("新增群組", key="new_group", placeholder="輸入群組名稱，例如：半導體、存股、觀察中")
        b2.button("＋ 建立群組", on_click=group_add, width="stretch")
        btn_mode = st.toggle("改用按鈕調整順序（手機上拖曳不方便時用）", key="btn_mode")
        if not btn_mode:
            if st.session_state.get("home_edit"):
                st.markdown("**拖曳排序**：按住股票拖到想要的位置，也可以直接拖到別的群組，調整完按「確認」才會儲存。")
                if drag_editor("home"):
                    st.session_state.home_edit = False
                    st.rerun()
            else:
                st.button("排序／移動（拖曳）", key="home_edit_btn", icon=":material/swap_vert:",
                          on_click=lambda: st.session_state.update(home_edit=True, manage_open=True))
            st.markdown("**群組設定**（名稱改完按 Enter）")
            for gi, g in enumerate(st.session_state.groups):
                h1, h2, h3, h4 = st.columns([4, 1, 1, 1.4], vertical_alignment="bottom")
                h1.text_input("群組名稱", value=g["name"], key=f"gname_{g['name']}", on_change=group_rename,
                              args=(g["name"],), label_visibility="collapsed")
                h2.button("↑", key=f"gup_{g['name']}", on_click=group_move, args=(g["name"], -1),
                          disabled=gi == 0, width="stretch", help="群組上移")
                h3.button("↓", key=f"gdn_{g['name']}", on_click=group_move, args=(g["name"], 1),
                          disabled=gi == len(st.session_state.groups) - 1, width="stretch", help="群組下移")
                h4.button("刪除群組", key=f"gdel_{g['name']}", on_click=group_delete, args=(g["name"],),
                          disabled=len(st.session_state.groups) <= 1, width="stretch",
                          help="群組裡的股票會移到第一個群組，不會被刪掉")
            if st.session_state.favs:
                r1, r2 = st.columns([4, 1.4], vertical_alignment="bottom")
                _rm = r1.selectbox("從所有群組移除", st.session_state.favs, index=None, key="rm_pick",
                                   format_func=lambda c: f"{c}　{quick_name(c)}", placeholder="選擇要移除的股票")
                if r2.button("移除", width="stretch", disabled=not _rm):
                    remove_fav(_rm)
                    st.rerun()
        else:
            st.caption("↑↓ 調整順序（移到群組最上／最下再按一次，會移到上一個／下一個群組）；"
                       "「移到」可直接換群組；群組名稱改完按 Enter。")
        for gi, g in enumerate(st.session_state.groups if btn_mode else []):
            with st.container(border=True):
                h1, h2, h3, h4 = st.columns([4, 1, 1, 1.4], vertical_alignment="bottom")
                h1.text_input("群組名稱", value=g["name"], key=f"gname_{g['name']}", on_change=group_rename,
                              args=(g["name"],), label_visibility="collapsed")
                h2.button("↑", key=f"gup_{g['name']}", on_click=group_move, args=(g["name"], -1),
                          disabled=gi == 0, width="stretch", help="群組上移")
                h3.button("↓", key=f"gdn_{g['name']}", on_click=group_move, args=(g["name"], 1),
                          disabled=gi == len(st.session_state.groups) - 1, width="stretch", help="群組下移")
                h4.button("刪除群組", key=f"gdel_{g['name']}", on_click=group_delete, args=(g["name"],),
                          disabled=len(st.session_state.groups) <= 1, width="stretch",
                          help="群組裡的股票會移到第一個群組，不會被刪掉")
                if not g["codes"]:
                    st.caption("（空的）")
                for c in g["codes"]:
                    x, u, d_, mv, y = st.columns([3, 0.8, 0.8, 2, 1], vertical_alignment="center")
                    x.markdown(f"**{c}** <span class='small'>{quick_name(c)}</span>", unsafe_allow_html=True)
                    u.button("↑", key=f"up_{gi}_{c}", on_click=fav_move, args=(c, -1, g["name"]), width="stretch")
                    d_.button("↓", key=f"dn_{gi}_{c}", on_click=fav_move, args=(c, 1, g["name"]), width="stretch")
                    if len(st.session_state.groups) > 1:
                        _wk = f"mv_{gi}_{c}"
                        st.session_state[_wk] = g["name"]
                        mv.selectbox("移到", _gn, key=_wk, on_change=fav_to_group, args=(c, g["name"], _wk),
                                     label_visibility="collapsed")
                    y.button("移除", key=f"rm_{gi}_{c}", on_click=fav_remove, args=(c, g["name"]), width="stretch",
                             help=f"只從「{g['name']}」移除")

    st.caption("本工具為個人研究用技術原型，不構成投資建議。")
    st.stop()



# ============================================================================
# 頁面二：盤後整理
# ============================================================================
_SUFFIX = re.compile(r"[,.]?\s+(American Depositary Shares?|Depositary Shares?|Companies|Inc|Corp|Corporation|Company|Co|Ltd|Limited|Holdings?|Group|plc|PLC|N\.?V|S\.?A|AG|SE|LLC|L\.?P|"
                     r"Incorporated|Sponsored|ADR|Class [A-C]|Common Stock|Ordinary Shares)\b\.?", re.I)


def short_name(code: str, name) -> str:
    """有中文名用中文名；英文名去掉 Inc.、Corporation 這類字尾，最多 18 個字。"""
    zh = name_table().get(code, {}).get("name", "")
    if zh and not zh.isascii():
        return zh
    n = str(name or "")
    for _ in range(3):
        n = _SUFFIX.sub("", n).strip(" ,.")
    return n if len(n) <= 18 else n[:17] + "…"


def pct_table(df: pd.DataFrame, key: str, tw_colors: bool, cols: dict, height: int | None = None):
    """可點選的漲跌表，點一列開啟個股分析。"""
    if df is None or df.empty:
        st.caption("暫無資料")
        return
    df = df.copy()
    if "name" in df:
        df["name"] = [short_name(c, n) for c, n in zip(df["code"], df["name"])]
    # 欄位順序：代號 → 漲跌 → 收盤 → 名稱（漲跌一進來就看得到，不會被長名稱擠出畫面）
    order = [k for k in ("code", "pct", "close", "name", "value") if k in cols]
    cols = {k: cols[k] for k in order}
    show = df.rename(columns=cols)[list(cols.values())].reset_index(drop=True)
    up, down = ("#d1352b", "#14825a") if tw_colors else ("#14825a", "#d1352b")
    pct_col = cols.get("pct")

    lim = [("up" if v >= 0.095 else "down" if v <= -0.095 else "") if tw_colors else "" for v in df["pct"]]

    def style(col):
        if col.name == "收盤":
            return [f"background-color:{'#d1352b' if l == 'up' else '#14825a'};color:#fff;font-weight:700" if l else ""
                    for l in lim]
        if col.name != pct_col:
            return [""] * len(col)
        return [(f"background-color:{'#d1352b' if l == 'up' else '#14825a'};color:#fff;font-weight:700" if l else
                 f"color:{up}" if v > 0 else f"color:{down}" if v < 0 else "") for v, l in zip(col, lim)]

    fmt = {pct_col: lambda v: f"{v * 100:+.2f}%" + (" 漲停" if tw_colors and v >= 0.095 else " 跌停" if tw_colors and v <= -0.095 else "")} if pct_col else {}
    if "成交金額(億)" in show:
        show["成交金額(億)"] = show["成交金額(億)"] / 1e8
        fmt["成交金額(億)"] = "{:,.1f}"
    if "收盤" in show:
        fmt["收盤"] = "{:,.2f}"

    def on_pick(k=key, codes=list(df["code"])):
        rows = st.session_state[k].selection.rows
        if rows:
            open_stock(codes[rows[0]])

    cfg = {"代號": st.column_config.TextColumn(width="small"), "漲跌": st.column_config.TextColumn(width="small"),
           "收盤": st.column_config.TextColumn(width="small"), "名稱": st.column_config.TextColumn(width="medium")}
    st.dataframe(show.style.apply(style).format(fmt), hide_index=True, width="stretch", key=key,
                 on_select=on_pick, selection_mode="single-row", column_config=cfg,
                 **({"height": height} if height else {}))


def bar_chart(names, values, tw_colors: bool, title: str, unit: str = "%", height: int = 360):
    up, down = ("#d1352b", "#14825a") if tw_colors else ("#14825a", "#d1352b")
    vals = [v * 100 for v in values] if unit == "%" else list(values)
    fig = go.Figure(go.Bar(x=vals, y=list(names), orientation="h",
                           marker_color=[up if v >= 0 else down for v in vals],
                           text=[f"{v:+.2f}{unit}" if unit == "%" else f"{v:+,.1f}{unit}" for v in vals],
                           textposition="auto", insidetextanchor="end", cliponaxis=False))
    fig.update_layout(height=height, margin=dict(l=10, r=40, t=36, b=10), title=title,
                      yaxis=dict(autorange="reversed"), xaxis=dict(zeroline=True), dragmode=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


_METRIC_PARAMS = set(inspect.signature(st.metric).parameters)


def metric(where, *a, **kw):
    where.metric(*a, **{k: v for k, v in kw.items() if k in _METRIC_PARAMS})


def ai_recap_button(label: str, lines: list[str], key: str):
    ai_key = secret("ANTHROPIC_API_KEY")
    if not ai_key:
        return
    if st.button("✨ 請 Claude 寫盤後評論", key=key):
        try:
            with st.spinner("Claude 撰寫中…"):
                st.session_state[key + "_txt"] = news.ask_claude(ai_key, (
                    f"以下是{label}的盤後數據重點，請用繁體中文寫一段 150 字內的盤後評論，"
                    "說明今天盤勢的主軸與明天要留意的地方，不要編造數據以外的事實，不給個股買賣建議。\n\n" + "\n".join(lines)))
        except Exception as e:
            st.warning(f"AI 評論失敗：{e}")
    if st.session_state.get(key + "_txt"):
        st.info(st.session_state[key + "_txt"])


if st.session_state.nav == RECAP:
    st.title("📰 盤後整理")
    favs_t = tuple(st.session_state.favs)
    tab_tw, tab_us = st.tabs(["台股", "美股"])

    with tab_tw:
        try:
            with st.spinner("整理台股盤後資料中…"):
                tr = get_tw_recap(token, favs_t)
        except Exception as e:
            st.error(f"台股盤後資料取不到：{e}")
            tr = None
        if tr:
            n1, n2 = st.columns([5, 1.3], vertical_alignment="center")
            n1.caption(f"交易日 {tr['date']}｜資料：證交所每日收盤行情、FinMind 三大法人與融資"
                       + ("" if tr.get("has_otc") else "｜排行只含上市股票（上櫃資料尚未更新）"))
            if n2.button("🔄 重新抓取", key="tw_recap_reload", width="stretch", help="立刻重新向證交所抓今天的盤後資料"):
                _tw_recap_cached.clear()
                st.rerun()
            _note = tw_recap_note(tr["date"])
            if _note:
                st.info(_note)
            m = st.columns(3) + st.columns(3)
            t = tr.get("taiex")
            if t:
                metric(m[0], "加權指數", f"{t['close']:,.2f}", f"{t['chg']:+,.2f}（{t['pct'] * 100:+.2f}%）",
                       delta_color="inverse", border=True)
            o = tr.get("otc")
            if o:
                metric(m[1], "櫃買指數", f"{o['close']:,.2f}", f"{o['chg']:+,.2f}（{o['pct'] * 100:+.2f}%）",
                       delta_color="inverse", border=True)
            if tr.get("turnover"):
                metric(m[2], "成交金額", f"{tr['turnover'] / 1e8:,.0f} 億元", border=True)
            if "up" in tr:
                metric(m[3], "上漲／下跌家數", f"{tr['up']}／{tr['down']}", f"漲停 {tr['limit_up']}｜跌停 {tr['limit_down']}",
                       delta_color="off", delta_arrow="off", border=True)
            if tr.get("inst"):
                i_ = tr["inst"]
                metric(m[4], "三大法人", f"{'買' if i_['合計'] >= 0 else '賣'}超 {abs(i_['合計']):,.1f} 億",
                       f"{i_['外資']:+,.1f} 億（外資）", delta_color="inverse", border=True)
            if tr.get("margin"):
                mg = tr["margin"]
                metric(m[5], "融資餘額", f"{mg['balance']:,.0f} 億元", f"{mg['chg']:+,.1f} 億",
                       delta_color="off", border=True)

            try:
                Pi = get_prob("^TWII", 730, token)
            except Exception:
                Pi = None
            if Pi and Pi["next"].get("gap_mu") is not None:
                g = Pi["next"]
                gc = "#d1352b" if g["gap_mu"] > 0 else "#14825a"
                st.markdown(
                    f"<div class='live'><div class='small'>🌙 下一個交易日加權指數預估（{g['gap_src']}）</div>"
                    f"<div class='row'><span class='px' style='color:{gc}'>開盤 {g['gap_mu'] * 100:+.2f}%</span>"
                    f"<span>上漲機率 <b style='color:#d1352b'>{g['p_up'] * 100:.0f}%</b>　下跌機率 "
                    f"<b style='color:#14825a'>{(1 - g['p_up']) * 100:.0f}%</b></span>"
                    f"<span class='small'>常見範圍 {(g['gap_mu'] - g['gap_sd']) * 100:+.1f}% ～ "
                    f"{(g['gap_mu'] + g['gap_sd']) * 100:+.1f}%</span></div></div>", unsafe_allow_html=True)
                tr = {**tr, "summary": tr["summary"] + [
                    f"依{g['gap_src']}推估，下一個交易日加權指數開盤約 {g['gap_mu'] * 100:+.2f}%，"
                    f"當日上漲機率 {g['p_up'] * 100:.0f}%。"]}
            st.markdown("##### 重點摘要")
            st.markdown("\n".join(f"- {x}" for x in tr["summary"]))
            ai_recap_button("台股", tr["summary"], "ai_tw")

            c1, c2 = st.columns(2)
            with c1:
                sec = tr.get("sectors")
                if sec is not None and len(sec):
                    pick = pd.concat([sec.head(8), sec.tail(8)]).drop_duplicates("group")
                    bar_chart(pick["group"], pick["pct"], True, "類股漲跌（成交金額加權）", height=480)
            with c2:
                if tr.get("inst"):
                    inst = {k: v for k, v in tr["inst"].items()}
                    bar_chart(list(inst), list(inst.values()), True, "三大法人買賣超（億元）", unit=" 億", height=230)
                if tr.get("sub_idx"):
                    sub = pd.DataFrame(tr["sub_idx"])
                    bar_chart(sub["name"], sub["pct"], True, "主要類股指數", height=240)

            st.caption("點選表格任一列左側方框，可開啟該股分析。")
            cols = {"code": "代號", "name": "名稱", "close": "收盤", "pct": "漲跌", "value": "成交金額(億)"}
            a, b, c = st.columns(3)
            with a:
                st.markdown("##### 🔺 漲幅前 10")
                pct_table(tr["gainers"], "tw_gain", True, cols)
            with b:
                st.markdown("##### 🔻 跌幅前 10")
                pct_table(tr["losers"], "tw_lose", True, cols)
            with c:
                st.markdown("##### 💰 成交金額前 10")
                pct_table(tr["top_value"], "tw_value", True, cols)
            st.caption("漲跌幅排行不含 ETF，且只列成交金額 5,000 萬元以上的股票，避免冷門股干擾。")
            if tr.get("favs") is not None and len(tr["favs"]):
                st.markdown("##### ⭐ 我的最愛（台股）今日表現")
                pct_table(tr["favs"], "tw_favs", True, cols)

    with tab_us:
        try:
            with st.spinner("整理美股盤後資料中…"):
                ur = get_us_recap(favs_t)
        except Exception as e:
            st.error(f"美股盤後資料取不到：{e}")
            ur = None
        if ur:
            st.caption(f"交易日 {ur['date']}（美東時間）"
                       + ("｜⚠ 美股盤中，數字尚未收盤" if live.is_open("US") else "") + "｜資料：Yahoo Finance")
            idx = ur["indices"].reset_index(drop=True)
            m = st.columns(4)
            for i, row in idx.iterrows():
                unit = "%" if row["code"] == "^TNX" else ""
                m[i % 4].metric(row["name"], f"{row['close']:,.2f}{unit}", f"{row['pct'] * 100:+.2f}%",
                                delta_color="inverse" if row["code"] == "^VIX" else "normal", border=True)
            if ur.get("adr"):
                ad = ur["adr"]
                metric(st.columns(2)[0], "台積電 ADR 溢價（相對台股 2330）", f"{ad['premium'] * 100:+.1f}%",
                          f"ADR {ad['tsm']:,.2f} 美元 × 匯率 {ad['fx']:.2f} ÷ 5 = {ad['tsm'] * ad['fx'] / 5:,.0f} 元",
                          delta_color="off", delta_arrow="off", border=True)

            ov = get_ov()
            if ov.get("nq") is not None:
                nc = "#14825a" if ov["nq"] > 0 else "#d1352b"
                st.markdown(f"<div class='small'>🌙 那斯達克期貨目前 <b style='color:{nc}'>{ov['nq'] * 100:+.2f}%</b>"
                            "（下一個美股交易日方向參考）</div>", unsafe_allow_html=True)
            st.markdown("##### 重點摘要")
            st.markdown("\n".join(f"- {x}" for x in ur["summary"]))
            ai_recap_button("美股", ur["summary"], "ai_us")

            c1, c2 = st.columns(2)
            with c1:
                sec = ur.get("sectors")
                if sec is not None and len(sec):
                    bar_chart(sec["name"], sec["pct"], False, "11 大類股（SPDR 類股 ETF）", height=420)
            with c2:
                mg = ur.get("mega")
                if mg is not None and len(mg):
                    bar_chart(mg["name"], mg["pct"], False, "大型權值股", height=420 if len(mg) <= 12 else 560)

            ucols = {"code": "代號", "name": "名稱", "close": "收盤", "pct": "漲跌"}
            if ur.get("gainers") is not None:
                a, b, c = st.columns(3)
                with a:
                    st.markdown("##### 🔺 漲幅排行")
                    pct_table(ur["gainers"], "us_gain", False, ucols)
                with b:
                    st.markdown("##### 🔻 跌幅排行")
                    pct_table(ur["losers"], "us_lose", False, ucols)
                with c:
                    st.markdown("##### 🔥 成交最熱")
                    pct_table(ur["actives"], "us_act", False, ucols)
                st.caption("排行來自 Yahoo Finance 篩選（市值較大的美股）。點選任一列可開啟分析。")
            if ur.get("favs") is not None and len(ur["favs"]):
                st.markdown("##### ⭐ 我的最愛（美股）今日表現")
                f = ur["favs"].copy()
                f["name"] = [name_table().get(c, {}).get("name", "") for c in f["code"]]
                pct_table(f, "us_favs", False, ucols)

    st.caption("本工具為個人研究用技術原型，不構成投資建議。")
    st.stop()

# ============================================================================
# 頁面四：潛力股雷達（獨立頁面，不影響其他功能）
# ============================================================================
def _radar_cols() -> dict:
    """代號、名稱固定在左邊，表格往右捲也看得到（舊版 Streamlit 沒有 pinned 就略過）。"""
    pin = {"pinned": True} if "pinned" in inspect.signature(st.column_config.TextColumn).parameters else {}
    return {"分數": st.column_config.ProgressColumn("分數", min_value=0, max_value=100, format="%d"),
            "代號": st.column_config.TextColumn(width="small", **pin),
            "名稱": st.column_config.TextColumn(width="small", **pin),
            "標記": st.column_config.TextColumn(width="medium")}


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_radar(token: str, n: int, market: str, day: str, rules: str = "v3.1"):
    return radar.scan(token or None, n, market)


def radar_scan_tab():
        f1, f2, f3, f4 = st.columns([1.1, 1, 1.2, 1.3], vertical_alignment="bottom")
        r_market = f1.selectbox("市場", ["全部", "上市", "上櫃"], key="radar_market")
        r_n = f2.selectbox("候選數量", [20, 40, 60], index=1, key="radar_n",
                           help="第一階段挑出營收加速最明顯的前幾家來細算。越多越完整，但抓資料越久、也越耗 FinMind 額度")
        r_min = f3.slider("最低分數", 0, 100, 45, 5, key="radar_min")
        r_hot = f4.checkbox("也顯示「已大漲」的股票", key="radar_hot")
        r_small = f4.checkbox("只看中小型股（月營收 10 億以下）", key="radar_small",
                              help="風青是月營收約 1 億的小公司；大型股（例如世芯）就算條件符合，也不是同一種轉機型態")
        run = st.button("🔍 開始掃描", type="primary", key="radar_run_btn")
        if run:
            st.session_state.radar_run = True
        if not st.session_state.get("radar_run"):
            st.info("按「開始掃描」後約需 1～2 分鐘（要逐檔抓股價、法人與季報）。結果會保留 12 小時，之後再打開會直接顯示。")
            st.caption("本工具為個人研究用技術原型，不構成投資建議。")
            return
        try:
            with st.spinner(f"掃描中：先篩全市場月營收，再逐檔分析前 {r_n} 名，約 1～2 分鐘…"):
                R = get_radar(token or "", int(r_n), r_market, f"{dt.date.today():%Y%m%d}", "v3.1")
        except Exception as e:
            st.error(f"掃描失敗：{e}")
            return
        # 每天第一次掃描自動存進「歷史成績單」，之後才能對答案
        try:                                    # 記錄失敗也不影響掃描結果的顯示
            _entry = radar.log_entry(R, r_market, int(r_n), f"{dt.date.today():%Y-%m-%d}")
            _new, _added = radar.add_log(st.session_state.get("radar_log", []), _entry,
                                         getattr(store, "RADAR_LOG_MAX", 120))
            if _added:
                _keep = st.session_state.get("manage_open")
                st.session_state.radar_log = _new
                save_favs()
                st.session_state.manage_open = _keep
                st.toast(f"已把今天的 {len(_entry['rows'])} 檔記錄到「歷史成績單」")
        except Exception as _e:
            st.caption(f"（這次沒有記錄到歷史成績單：{_e}）")
        rows = [x for x in R["rows"] if x["score"] >= r_min and (r_hot or "已大漲" not in x["tags"])
                and (not r_small or "大型股" not in x.get("tags", []))]
        st.caption(f"營收資料：{radar.ym_text(R['ym'])}｜全市場 {R['n_all']} 家 → 營收加速候選 {R['n_cand']} 家 → "
                   f"符合條件 {len(rows)} 家｜來源：" + "、".join(f"{k} {v}" for k, v in R["status"].items()))
        if "上櫃" in R["status"] and not R["status"]["上櫃"].endswith("家"):
            st.warning("櫃買中心的月營收這次沒抓到，結果只包含上市股票。可以稍後按「清除快取並重新抓取」再試。")
        if not rows:
            st.info("目前沒有符合條件的股票，可以把「最低分數」調低一點看看。")
        else:
            tbl = pd.DataFrame([{
                "代號": x["code"], "名稱": x["name"], "分數": x["score"], "標記": "、".join(x["tags"]),
                "營收年增": f"{x['yoy']:+.0f}%", "累計年增": "—" if pd.isna(x["cum_yoy"]) else f"{x['cum_yoy']:+.0f}%",
                "最近EPS": "—" if pd.isna(x["eps"]) else f"{x['eps']:+.2f}",
                "去年同季": "—" if pd.isna(x["eps_ly"]) else f"{x['eps_ly']:+.2f}",
                "一年位置": "—" if pd.isna(x["pos"]) else f"{x['pos'] * 100:.0f}%",
                "近3個月": "—" if pd.isna(x["r60"]) else f"{x['r60'] * 100:+.0f}%",
                "營收／獲利／低檔／籌碼": f"{x['s_rev']}／{x['s_eps']}／{x['s_low']}／{x['s_chip']}",
                "產業": x["industry"],
            } for x in rows])
            codes = [x["code"] for x in rows]

            def on_radar_pick():
                sel = st.session_state.radar_table.selection.rows
                if sel:
                    open_stock(codes[sel[0]])

            st.dataframe(tbl, hide_index=True, width="stretch", key="radar_table", on_select=on_radar_pick,
                         selection_mode="single-row",
                         column_config=_radar_cols())
            st.caption("點任一列左邊的方框，就會開啟那檔股票的完整分析。「一年位置」0% 是一年最低、100% 是一年最高。")
            st.markdown("#### 前幾名的理由")
            for x in rows[:8]:
                with st.container(border=True):
                    h1, h2 = st.columns([5, 1.2], vertical_alignment="center")
                    h1.markdown(f"**{x['code']} {x['name']}**　<span class='small'>{x['industry']}｜{x['market']}</span>　"
                                f"**{x['score']} 分**" + ("　" + "".join(f"<span class='chip'>{t}</span>" for t in x["tags"]) if x["tags"] else ""),
                                unsafe_allow_html=True)
                    h2.button("看分析 ›", key=f"radar_open_{x['code']}", on_click=open_stock, args=(x["code"],), width="stretch")
                    st.markdown("、".join(x["why"]) + "。")
                    if x.get("note"):
                        st.caption(f"公司說明營收變動原因：{x['note'][:80]}")
        if R.get("errors"):
            with st.expander(f"有 {len(R['errors'])} 檔資料抓取失敗"):
                st.caption("；".join(R["errors"][:30]))
        st.caption("提醒：這是用公開數字找「條件像風青」的股票，不代表一定會漲。很多股票有類似訊號最後沒漲，"
                   "請把它當成觀察名單，等下一份營收或財報證實了再考慮。本工具為個人研究用技術原型，不構成投資建議。")


@st.cache_data(ttl=3600, show_spinner=False)
def get_radar_eval(log_json: str) -> pd.DataFrame:
    return radar.evaluate(json.loads(log_json))


def _pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"


def radar_history():
    """歷史成績單：過去入選的股票後來表現如何、哪個條件真的有用。"""
    log = st.session_state.get("radar_log", [])
    st.caption(f"每天第一次掃描會自動記錄分數最高的 30 檔與當天收盤價，之後用 Yahoo 收盤價對答案，跟加權指數比較。"
               f"紀錄存在：{fav_store.label}")
    if "本機" in fav_store.label:
        st.caption("⚠ 放在 Streamlit 雲端時，主機重開會讓本機檔案消失；要長期累積，請照 README 設定 GitHub 儲存（我的最愛同步那一段）。")
    if not log:
        st.info("還沒有紀錄。到「掃描」分頁按一次「開始掃描」，今天的結果就會存進來；至少累積 1 個月以上才看得出準不準。")
        return
    try:
        with st.spinner("計算過去入選股票的表現…"):
            df = get_radar_eval(json.dumps(log, ensure_ascii=False))
    except Exception as e:
        st.error(f"計算失敗：{e}")
        return
    if df.empty:
        st.info(f"已記錄 {len(log)} 次掃描，但還抓不到後續股價（可能是今天才記錄），明天再來看。")
        return
    m20 = df.dropna(subset=["excess20"])
    c1, c2, c3, c4 = st.columns(4)
    metric(c1, "已追蹤", f"{len(df)} 筆", f"{len(log)} 次掃描", delta_color="off", delta_arrow="off", border=True)
    metric(c2, "到今天平均報酬", _pct(df["ret"].mean()), f"同期大盤 {_pct(df['bench'].mean())}", delta_color="off",
              delta_arrow="off", border=True)
    metric(c3, "贏大盤的比例", f"{(df['excess'] > 0).mean() * 100:.0f}%", f"平均多 {_pct(df['excess'].mean())}",
              delta_color="off", delta_arrow="off", border=True)
    metric(c4, "滿 1 個月（20 交易日）", f"{len(m20)} 筆",
              ("平均贏大盤 " + _pct(m20["excess20"].mean())) if len(m20) else "還沒有", delta_color="off",
              delta_arrow="off", border=True)

    st.markdown("#### 分數高的真的比較好嗎？")
    bins = pd.cut(df["score"], [-1, 64, 79, 100], labels=["64 分以下", "65～79 分", "80 分以上"])
    g = df.groupby(bins, observed=True).agg(筆數=("ret", "size"), 平均報酬=("ret", "mean"), 平均贏大盤=("excess", "mean"),
                                            贏大盤比例=("excess", lambda x: (x > 0).mean()),
                                            一個月後贏大盤=("excess20", "mean")).reset_index().rename(columns={"score": "分數"})
    for col in ("平均報酬", "平均贏大盤", "一個月後贏大盤"):
        g[col] = g[col].map(_pct)
    g["贏大盤比例"] = g["贏大盤比例"].map(lambda v: f"{v * 100:.0f}%")
    st.dataframe(g, hide_index=True, width="stretch")
    st.caption("如果「80 分以上」長期比「64 分以下」好，代表評分有用；如果差不多甚至相反，就代表評分方法要調整。")

    st.markdown("#### 哪個條件真的有用？")
    fr = radar.factor_report(df, "20")
    if len(m20) < 20 or fr.empty:
        st.info(f"資料累積中：滿 1 個月的紀錄目前 {len(m20)} 筆，至少要 20 筆（而且高分組、低分組各 5 筆以上）才比較得出來。")
    else:
        show = fr.copy()
        for col in ("高分組贏大盤", "低分組贏大盤", "差距"):
            show[col] = show[col].map(_pct)
        st.dataframe(show, hide_index=True, width="stretch")
        best = fr.sort_values("差距", ascending=False).iloc[0]
        worst = fr.sort_values("差距").iloc[0]
        st.markdown(f"- 目前最有用的條件：**{best['條件']}**（高分組比低分組多贏大盤 {best['差距'] * 100:.1f} 個百分點）\n"
                    f"- 目前最沒用的條件：**{worst['條件']}**（差距 {worst['差距'] * 100:+.1f} 個百分點）")
        st.caption("比較的是「入選後 20 個交易日」贏大盤多少。樣本少時結果很容易被一兩檔大漲或大跌影響，至少看半年以上再決定要不要調整權重。")

    st.markdown("#### 每次掃描")
    per = df.groupby("date").agg(檔數=("ret", "size"), 平均報酬=("ret", "mean"), 同期大盤=("bench", "mean"),
                                 贏大盤比例=("excess", lambda x: (x > 0).mean())).reset_index().rename(columns={"date": "掃描日"})
    per = per.sort_values("掃描日", ascending=False)
    for col in ("平均報酬", "同期大盤"):
        per[col] = per[col].map(_pct)
    per["贏大盤比例"] = per["贏大盤比例"].map(lambda v: f"{v * 100:.0f}%")
    st.dataframe(per, hide_index=True, width="stretch")
    with st.expander("全部入選紀錄"):
        det = df[["date", "code", "name", "score", "tags", "days", "ret", "bench", "excess", "ret20", "excess20"]].copy()
        det.columns = ["掃描日", "代號", "名稱", "分數", "標記", "經過交易日", "報酬", "同期大盤", "贏大盤", "1個月報酬", "1個月贏大盤"]
        for col in ("報酬", "同期大盤", "贏大盤", "1個月報酬", "1個月贏大盤"):
            det[col] = det[col].map(_pct)
        st.dataframe(det.sort_values(["掃描日", "分數"], ascending=[False, False]), hide_index=True, width="stretch")
    st.caption("報酬以掃描當天收盤價為起點、Yahoo 最新收盤價為終點，不含股利與手續費。這是用來檢討方法，不是投資建議。")


if st.session_state.nav == RADAR:
    st.title("🧭 潛力股雷達")
    st.caption("用「風青（2061）」2026 年大漲前的訊號，找出目前條件相似、但股價還在低檔的股票。")
    with st.expander("篩選邏輯：從風青的經驗來", expanded=False):
        st.markdown(
            "風青大漲前看得到四個訊號，這裡每個面向各 25 分，總分 100：\n\n"
            "1. **營收動能**：最新月營收年增 15% 以上，而且比今年累計年增還好（正在加速）。"
            "風青 2026 年 1 月營收年增 43%。\n"
            "2. **獲利轉機**：最新一季 EPS 轉虧為盈或大幅成長、毛利率比去年同季回升。"
            "風青第一季 EPS 由虧轉盈、毛利率 5 年新高。\n"
            "3. **低檔打底**：股價在一年區間的低檔、季線走平或剛翻揚。近 3 個月已漲超過 50% 的標記為「已大漲」，這一項不給分。\n"
            "4. **籌碼乾淨**：近 20 日融資沒有增加、外資＋投信有買超、量能平穩股價守得住。\n\n"
            "標記說明：**仍在急跌**＝近 3 個月跌超過 20%，低檔分數打折（風青是打底，不是急跌）；"
            "**大型股**＝月營收 10 億以上；**可能一次性**＝營收暴增可能來自賣土地、處分資產等，要看公司說明；"
            "**已大漲**＝近 3 個月漲超過 50%。\n\n"
            "排序重點是「加速」：最新月營收年增比今年累計年增好越多越前面（風青就是這種轉機型）。"
            "金融業與建材營造（交屋才認列營收，單月暴增很常見）不列入。\n\n"
            "做法：先用證交所／櫃買中心的「每月營收彙總表」一次篩出全市場營收在加速的公司，"
            "再對這些候選股逐檔抓 FinMind 的股價、法人、融資與季報來評分。")
    t_scan, t_hist = st.tabs(["🔍 掃描", "📈 歷史成績單"])
    with t_hist:
        radar_history()
    with t_scan:
        radar_scan_tab()
    st.stop()


# ============================================================================
# 頁面三：個股分析
# ============================================================================
code = st.session_state.code
# 一進頁面就把需要的資料「同時」送出去抓，不再一個等一個
_mk = market_of(code)
F_AN = bg(analyze, code, fetch_days, token)
F_NEWS = bg(get_news, code, _mk, news_name(code))
F_PROB = bg(get_prob, code, fetch_days, token)
F_CO = bg(get_company, code, _mk, news_name(code), token or "") if _mk in ("TW", "US") else None
F_Q = bg(get_quotes, (code,))
F_INTRA = bg(get_intraday, code, _mk) if _mk != "IDX" or code in ("^TWII", "^GSPC", "^IXIC", "^DJI", "^SOX") else None
IDX_REF = {"TW": "^TWII", "US": "^GSPC"}.get(_mk)
F_IDXQ = bg(get_quotes, (IDX_REF,)) if IDX_REF else None
try:
    with st.spinner(f"抓取 {code} 資料中…"):
        market, name, r = wait(F_AN)
except (engine.DataError, TimeoutError) as e:
    st.error(str(e))
    st.stop()

try:
    P = F_PROB.result(timeout=TIMEOUT)
except Exception as e:
    P = None
    st.warning(f"機率計算失敗：{e}")
r = with_prob(r, P)
up_c, down_c = colors(market, code)
SESSION = live.session_of(code, market)
df, last = r["df"], r["last"]
cur = {"TW": "NT$", "US": "US$"}.get(market, "點")
mk_label = {"TW": "台股", "US": "美股", "IDX": "指數"}[market]
CUR_G = current_group()                       # 左側欄選中的群組（選「全部」時為 None）
is_fav = code in (_grp(CUR_G)["codes"] if CUR_G else st.session_state.favs)
held = st.session_state.positions.get(code)
_q0 = get_quotes((code,)).get(code)
pos_p = pos_analyze(df, r, _q0["price"] if _q0 else r["close"], held) if held else None
STOP_REF, STOP_LABEL = (pos_p["stop"], "我的停損") if pos_p else (r["stop"], "停損參考")

t1, t2 = st.columns([5, 1.2], vertical_alignment="center")
t1.title(f"{code} {name}".strip())
if CUR_G:                                      # 依目前群組判斷：不在這個群組就顯示「加入」
    if is_fav:
        t2.button("★ 已在最愛", on_click=toggle_fav, args=(code,), type="primary", width="stretch",
                  help=f"在「{CUR_G}」群組，再按一次從「{CUR_G}」移除")
    else:
        t2.button("☆ 加入最愛", on_click=toggle_fav, args=(code,), width="stretch",
                  help=f"加到「{CUR_G}」群組（左側欄可切換群組）")
    t2.caption(f"📁 {CUR_G}")
elif is_fav:
    t2.button("★ 已在最愛", on_click=toggle_fav, args=(code,), type="primary", width="stretch",
              help=f"在「{'、'.join(groups_of(code))}」，按一次從所有群組移除")
elif len(st.session_state.groups) == 1:
    t2.button("☆ 加入最愛", on_click=fav_add, args=(code,), width="stretch")
else:
    with t2.popover("☆ 加入最愛", width="stretch"):
        st.caption("加到哪個群組？")
        for _g in group_names():
            st.button(f"📁 {_g}", key=f"addto_{_g}", on_click=fav_add, args=(code, _g), width="stretch")

# 顯示期間：K 線一開始顯示的範圍、「期間表現」、籌碼圖都依這個；模型仍用完整資料
VIEW_START = pd.Timestamp(dt.date.today() - dt.timedelta(days=days))
dfv = df[df["date"] >= VIEW_START]
if len(dfv) < 2:
    dfv = df.tail(2)
N_VIEW = len(dfv)
PERIOD_LABEL = st.session_state.get("period_text") or st.session_state.get("period_pick") or DEFAULT_PERIOD
st.caption(f"{mk_label}｜分析基準：{r['asof']} 收盤｜顯示期間：{PERIOD_LABEL}（{N_VIEW} 個交易日）"
           f"｜機率與評分使用近 {len(df)} 個交易日資料")


@st.fragment(run_every=LIVE_EVERY if live.is_open(SESSION) else None)
def live_card():
    q = get_quotes((code,)).get(code)
    if not q:
        st.caption("即時報價暫時取不到，以下以最近收盤資料為準。")
        return
    opened = live.is_open(SESSION)
    chg_col = up_c if (q["chg"] or 0) > 0 else down_c if (q["chg"] or 0) < 0 else "gray"
    dot = "#d1352b" if opened else "#8a8d95"
    notes = []
    if q["price"] < STOP_REF:
        notes.append(f"<b style='color:#b7791f'>⚠ 已跌破{STOP_LABEL} {STOP_REF:,.2f}</b>")
    ma20 = last["ma20"]
    if pd.notna(ma20):
        notes.append(f"{'站上' if q['price'] >= ma20 else '低於'}月線 {ma20:,.2f}")
    hl = f"高 {q['high']:,.2f}　低 {q['low']:,.2f}" if q.get("high") and q.get("low") else ""
    st.markdown(
        f"<div class='live'><div class='small'><span class='dot' style='background:{dot}'></span>"
        f"{'盤中即時' if opened else '最新報價'}｜{q['source']} {q['time']}</div>"
        f"<div class='row'><span class='px'>{price_html(q['price'], live.limit_state(q['price'], q.get('prev'), market, q), chg_col)}</span>"
        f"<span style='color:{chg_col};font-weight:600'>{fmt_move(q['price'], q.get('prev'), q['chg'])}</span>"
        f"<span class='small'>{hl}</span><span class='small'>{'　'.join(notes)}</span></div></div>",
        unsafe_allow_html=True)


live_card()

# --- 摘要（依最近收盤計算，盤中不變） ---
def card(col, lbl, val, sub="", color="inherit", sub_color="gray"):
    col.markdown(f"<div class='card'><div class='lbl'>{lbl}</div>"
                 f"<div class='val' style='color:{color}'>{val}</div>"
                 f"<div class='sub' style='color:{sub_color}'>{sub}</div></div>", unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns([1, 1.3, 1.2, 1, 1])
chg_c = up_c if r["chg"] > 0 else down_c if r["chg"] < 0 else "gray"
card(c1, "收盤價（分析基準）", f"{r['close']:,.2f}",
     f"{r['close'] - r['close'] / (1 + r['chg']):+,.2f}（{r['chg'] * 100:+.2f}%）{cur}", sub_color=chg_c)
def prob_card(col, title, h):
    if not h:
        card(col, title, "—", "資料不足")
        return
    pu = h["p_up"]
    col.markdown(
        f"<div class='card'><div class='lbl'>{title}</div>"
        f"<div class='val' style='font-size:1.3rem;white-space:nowrap'><span style='color:{up_c}'>漲 {pu * 100:.0f}%</span>"
        f"<span style='font-size:.9rem;color:gray'> ｜ </span>"
        f"<span style='color:{down_c}'>跌 {(1 - pu) * 100:.0f}%</span></div>"
        f"<div style='display:flex;height:6px;border-radius:3px;overflow:hidden;margin:4px 0'>"
        f"<div style='width:{pu * 100:.0f}%;background:{up_c}'></div>"
        f"<div style='width:{(1 - pu) * 100:.0f}%;background:{down_c}'></div></div>"
        f"<div class='sub'>起點 {h['base'] * 100:.0f}%，依下方機率表加減</div></div>", unsafe_allow_html=True)


prob_card(c2, "下一個交易日", P["next"] if P else None)
d = r["d10"]
card(c3, "10 日預估區間（80%）",
     f"<span style='color:{down_c}'>{d['p10'] * 100:+.1f}%</span> ~ <span style='color:{up_c}'>{d['p90'] * 100:+.1f}%</span>",
     f"中位數 {d['p50'] * 100:+.1f}%")
card(c4, "停損參考", f"{r['stop']:,.2f}", f"跌破代表超出悲觀預估（{d['p10'] * 100:+.1f}%）")
prob_card(c5, "未來 10 個交易日", P["h10"] if P else None)

if P and P["next"].get("gap_mu") is not None:
    g = P["next"]
    gc = up_c if g["gap_mu"] > 0 else down_c
    st.markdown(f"<div class='small'>🌙 明日開盤預估（{g['gap_src']}）："
                f"<b style='color:{gc}'>{g['gap_mu'] * 100:+.2f}%</b>，約 {r['close'] * (1 + g['gap_mu']):,.2f}"
                f"（常見範圍 {(g['gap_mu'] - g['gap_sd']) * 100:+.1f}% ～ {(g['gap_mu'] + g['gap_sd']) * 100:+.1f}%）</div>",
                unsafe_allow_html=True)

st.markdown("#### AI 解讀")
st.markdown(f"<div class='ai'>{P['summary'] if P else r['explain']}</div>", unsafe_allow_html=True)


def period_summary(v: pd.DataFrame, full: pd.DataFrame, market: str):
    """依側邊欄選的期間（例如近 45 天）整理這段時間的表現與判讀。"""
    unit = {"TW": "張", "US": "股"}.get(market, "")
    c0 = float(full["close"][full["date"] < v["date"].iloc[0]].iloc[-1]) if (full["date"] < v["date"].iloc[0]).any() \
        else float(v["open"].iloc[0])
    c1 = float(v["close"].iloc[-1])
    ret = c1 / c0 - 1
    hi_i, lo_i = v["high"].idxmax(), v["low"].idxmin()
    hi, lo = float(v.loc[hi_i, "high"]), float(v.loc[lo_i, "low"])
    pos = (c1 - lo) / (hi - lo) if hi > lo else 0.5
    cl = v["close"].astype(float)
    dd = float((cl / cl.cummax() - 1).min())
    rets = cl.pct_change().dropna()
    vol_y = float(rets.std() * np.sqrt(252)) if len(rets) > 2 else np.nan
    up_days = int((rets > 0).sum())
    n = len(v)
    prev_v = full[full["date"] < v["date"].iloc[0]].tail(n)
    vr = float(v["vol"].mean() / prev_v["vol"].mean()) if len(prev_v) >= max(3, n // 2) and prev_v["vol"].mean() > 0 else np.nan
    metrics = [("區間漲跌", fmt_chg(ret), f"{c0:,.2f} → {c1:,.2f}"),
               ("區間最高／最低", f"{hi:,.2f}／{lo:,.2f}", f"{v.loc[hi_i, 'date']:%m/%d} 高、{v.loc[lo_i, 'date']:%m/%d} 低"),
               ("目前位置", f"{pos * 100:.0f}%", f"距最高 {c1 / hi - 1:+.1%}"),
               ("最大回落", f"{dd:.1%}", f"上漲 {up_days} 天／共 {len(rets)} 天")]
    lines = []
    trend = ("明顯上漲" if ret >= 0.08 else "偏多" if ret >= 0.02 else "明顯下跌" if ret <= -0.08 else "偏空" if ret <= -0.02 else "盤整")
    lines.append(f"這 {n} 個交易日{trend}（{ret:+.1%}），目前在區間的 {pos * 100:.0f}% 位置"
                 + ("，接近區間高點。" if pos >= 0.8 else "，接近區間低點。" if pos <= 0.2 else "。"))
    if pd.notna(vol_y):
        lines.append(f"年化波動約 {vol_y:.0%}，期間最大回落 {dd:.1%}" +
                     ("，波動偏大，停損要放寬或部位縮小。" if vol_y >= 0.45 else "。"))
    if pd.notna(vr):
        lines.append(f"平均成交量是前一段同樣天數的 {vr:.1f} 倍（{'量增' if vr >= 1.2 else '量縮' if vr <= 0.8 else '量平'}）"
                     + ("，價漲量增，買盤有跟上。" if ret > 0 and vr >= 1.2 else
                        "，價跌量增，賣壓較重。" if ret < 0 and vr >= 1.2 else
                        "，上漲但量縮，追價力道偏弱。" if ret > 0 and vr <= 0.8 else "。"))
    if market == "TW" and {"foreign", "trust", "dealer"} <= set(v.columns):
        f_, t_, d_ = (float(v[k].sum()) for k in ("foreign", "trust", "dealer"))
        if abs(f_) + abs(t_) + abs(d_) > 0:
            metrics.append(("三大法人合計", f"{f_ + t_ + d_:+,.0f} {unit}", f"外資 {f_:+,.0f}｜投信 {t_:+,.0f}｜自營 {d_:+,.0f}"))
            who = [nm for nm, x in (("外資", f_), ("投信", t_)) if x > 0]
            sold = [nm for nm, x in (("外資", f_), ("投信", t_)) if x < 0]
            lines.append(("期間" + "、".join(who) + "站在買方" if who else "") +
                         ("，" if who and sold else "") + ("期間" + "、".join(sold) + "站在賣方" if sold else "") + "。")
        if "margin" in v.columns and v["margin"].notna().sum() >= 2:
            m = v["margin"].dropna()
            dm = float(m.iloc[-1] - m.iloc[0])
            lines.append(f"融資{'增加' if dm >= 0 else '減少'} {abs(dm):,.0f} 張"
                         + ("，股價跌但融資增，散戶在攤平，要小心。" if ret < 0 and dm > 0 else
                            "，股價漲但融資減，籌碼往法人集中，偏健康。" if ret > 0 and dm < 0 else "。"))
    return metrics, lines


with st.expander(f"📅 期間表現（{PERIOD_LABEL}，{N_VIEW} 個交易日）", expanded=True):
    try:
        _pm, _pl = period_summary(dfv, df, market)
        _cols = st.columns(len(_pm))
        for _col, (_l, _v, _s) in zip(_cols, _pm):
            card(_col, _l, _v, _s)
        st.markdown("\n".join(f"- {x}" for x in _pl if x and x != "。"))
        st.caption("天數在左側「資料期間」調整，直接打數字就是天數（例如 45、120）。"
                   "這裡只統計這段期間；上方的上漲機率固定用至少 2 年資料計算。")
    except Exception as e:
        st.caption(f"期間表現計算失敗：{e}")


# --- 🕒 盤前／盤中／盤後整理 ---
@st.fragment(run_every=LIVE_EVERY if live.is_open(SESSION) else 300)   # 收盤後每 5 分鐘檢查，開盤時自動換成新的一天
def session_box():
    q = get_quotes((code,)).get(code)
    try:
        intra = get_intraday(code, market) if market != "IDX" or code in ("^TWII", "^GSPC") else None
    except Exception:
        intra = None
    idx_q = get_quotes((IDX_REF,)).get(IDX_REF) if IDX_REF else None
    try:
        items_s = wait(F_NEWS, [])
    except Exception:
        items_s = []
    b = session.build(code, market, SESSION, r, q, P, items_s, get_ov(), idx_q, STOP_REF, STOP_LABEL, intra)
    ph = b["phase"]
    names = {k: session.PHASE_NAME[k] + ("　● 現在" if k == ph else "") for k in ("pre", "live", "post")}
    labels = [names["pre"], names["live"], names["post"]]
    kw = {"default": names[ph]} if "default" in inspect.signature(st.tabs).parameters else {}
    t_pre, t_live, t_post = st.tabs(labels, **kw)

    def lines(sec):
        st.markdown("\n".join(f"- {x}" for x in sec["lines"]))

    def metric_row(sec):
        if sec.get("metrics"):
            cols = st.columns(len(sec["metrics"]))
            for col, (lbl, val, sub) in zip(cols, sec["metrics"]):
                card(col, lbl, val, sub)

    with t_pre:
        sec = b["pre"]
        st.markdown(f"**{sec['title']}**")
        lines(sec)
        st.markdown(kv_html(sec["levels"]), unsafe_allow_html=True)
    with t_live:
        sec = b["live"]
        st.markdown(f"**{sec['title']}**")
        metric_row(sec)
        lines(sec)
        ch = sec.get("chart") or {}
        if ch.get("day"):
            d = ch["rows"]
            prev = ch["prev"]
            fig = go.Figure()
            ys = []
            if d is not None:
                last_px = float(d["Close"].iloc[-1])
                col = up_c if prev is None or last_px >= prev else down_c
                fig.add_trace(go.Scatter(x=d["time"], y=d["Close"], mode="lines", line=dict(color=col, width=2),
                                         name="5 分 K 收盤", hovertemplate="%{x|%H:%M}　%{y:,.2f}<extra></extra>"))
                ys += [float(d["Low"].min()), float(d["High"].max())]
            if prev:
                fig.add_hline(y=prev, line_dash="dot", line_color="gray", annotation_text=f"昨收 {prev:,.2f}",
                              annotation_position="bottom right")
                ys.append(prev)
            lo, hi = (min(ys), max(ys)) if ys else (0, 1)
            pad = (hi - lo) * 0.08 or max(abs(hi) * 0.01, 1)
            tick = 30 if SESSION == "TW" else 60          # 台股每 30 分鐘、美股每 1 小時一個刻度
            tv = list(pd.date_range(ch["start"], ch["end"], freq=f"{tick}min"))
            if tv[-1] != ch["end"]:
                tv.append(pd.Timestamp(ch["end"]))      # 收盤時間一定要有刻度
            fig.update_layout(height=220, margin=dict(l=0, r=8, t=10, b=0), showlegend=False, dragmode=False,
                              yaxis=dict(range=[lo - pad, hi + pad]),
                              xaxis=dict(range=[ch["start"] - dt.timedelta(minutes=6), ch["end"] + dt.timedelta(minutes=12)],
                                         tickvals=tv,
                                         ticktext=[f"{x:%H:%M}" for x in tv]))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"intra_{code}")
            if SESSION == "TW":
                tz_note = "台灣時間"
            else:
                _tw = [dt.datetime.combine(ch["day"], x.time()).replace(tzinfo=live.TZ["US"]).astimezone(live.TZ["TW"])
                       for x in (ch["start"], ch["end"])]
                tz_note = f"美東時間（＝台灣 {_tw[0]:%H:%M}～{_tw[1]:%H:%M}）"
            today_ex = live.now(SESSION).date()
            day_note = "今天" if ch["day"] == today_ex else f"{ch['day']:%m/%d}（最近一個交易日，下次開盤時換成新的一天）"
            wait_note = "；今日資料還沒進來（Yahoo 約延遲 20 分鐘），稍後會自動出現" if ch.get("waiting") else ""
            st.caption(f"{day_note}走勢｜時間軸為完整交易時段，{tz_note}｜Yahoo 5 分 K{wait_note}")
    with t_post:
        sec = b["post"]
        st.markdown(f"**{sec['title']}**")
        metric_row(sec)
        lines(sec)

    ai_key = secret("ANTHROPIC_API_KEY")
    if ai_key:
        if st.button("✨ 請 Claude 把今日整理寫成一段話", key=f"sess_ai_{code}"):
            try:
                with st.spinner("Claude 撰寫中…"):
                    st.session_state[f"sess_ai_txt_{code}"] = news.ask_claude(ai_key, (
                        "請用繁體中文（台灣用語）、200 字內，把以下個股的盤前／盤中／盤後資料整理成一段給投資人看的短評，"
                        "只根據資料，不要編造，不給買賣建議：\n" + session.as_text(code, name, b)))
            except Exception as e:
                st.warning(f"AI 失敗：{e}")
        if st.session_state.get(f"sess_ai_txt_{code}"):
            st.info(st.session_state[f"sess_ai_txt_{code}"])


st.markdown("#### 🕒 今日整理（盤前／盤中／盤後）")
session_box()

if P:
    with st.expander("📊 機率表：每個因素加減了多少", expanded=False):
        def show_prob_table(h, key):
            t = prob.table(h)

            def sty(col):
                if col.name != "影響（百分點）":
                    return [""] * len(col)
                return [f"color:{up_c}" if v > 0.05 else f"color:{down_c}" if v < -0.05 else "color:gray" for v in col]

            st.dataframe(t.style.apply(sty).format({"影響（百分點）": lambda v: "" if pd.isna(v) else f"{v:+.1f}"}),
                         hide_index=True, width="stretch", key=key)
        pt1, pt2 = st.tabs(["下一個交易日", "未來 10 個交易日"])
        with pt1:
            show_prob_table(P["next"], "pt_next")
        with pt2:
            show_prob_table(P["h10"], "pt_h10")
        st.caption("技術與籌碼條件用這檔股票自己的歷史統計，依樣本數打折，總和再打五折（因為條件彼此相關）；"
                   "營收、新聞用固定的小權重；台股明日開盤用過去兩年「前一晚美股 → 台股開盤跳空」的迴歸。"
                   "機率限制在 20%～80%，因為短期漲跌本來就很難預測。這是統計上的傾向，不是保證。")

@st.cache_data(ttl=3600, show_spinner=False)
def get_ai_summary(key: str, label: str, titles: tuple):
    items = [news.Item(title=t, source=s, time=pd.Timestamp(tm).to_pydatetime()) for t, s, tm in titles]
    return news.ai_summary(key, label, items)


# --- 我的持倉 ---
def save_position():
    cost = st.session_state.get(f"pos_cost_{code}") or 0
    if cost <= 0:
        st.session_state.store_err = "請輸入大於 0 的買進價"
        return
    bd = st.session_state.get(f"pos_date_{code}")
    st.session_state.positions[code] = {
        "cost": float(cost), "shares": float(st.session_state.get(f"pos_shares_{code}") or 0),
        "date": bd.isoformat() if bd else "", "risk": float(st.session_state.get(f"pos_risk_{code}") or 8)}
    save_favs()
    st.toast(f"已儲存 {code} 的持倉")


def clear_position():
    st.session_state.positions.pop(code, None)
    st.session_state.pop(f"pos_ai_{code}", None)
    save_favs()


if market != "IDX":
    with st.expander("💼 我的持倉分析" + (f"（成本 {held['cost']:,.2f}）" if held else "：輸入買進價，取得停損與操作建議"),
                     expanded=bool(held)):
        with st.form(f"pos_form_{code}", border=False):
            f1, f2, f3, f4 = st.columns(4)
            f1.number_input("買進價", min_value=0.0, value=float(held["cost"]) if held else None, step=0.5,
                            format="%.2f", key=f"pos_cost_{code}", placeholder="例如 2300")
            f2.number_input("股數（選填，1 張 = 1000 股）" if market == "TW" else "股數（選填）", min_value=0.0,
                            value=float(held["shares"]) if held and held.get("shares") else None, step=1000.0 if market == "TW" else 1.0,
                            format="%.0f", key=f"pos_shares_{code}")
            f3.date_input("買進日（選填）", value=dt.date.fromisoformat(held["date"]) if held and held.get("date") else None,
                          max_value=dt.date.today(), key=f"pos_date_{code}", help="填了才能算買進後最高價，用來設移動停損")
            f4.number_input("可承受虧損 %", min_value=2.0, max_value=30.0, value=float(held.get("risk", 8)) if held else 8.0,
                            step=1.0, format="%.0f", key=f"pos_risk_{code}", help="固定停損的幅度，一般 5～10%")
            b1, b2, _ = st.columns([1, 1, 3])
            b1.form_submit_button("儲存並分析", type="primary", on_click=save_position, width="stretch")
            if held:
                b2.form_submit_button("清除持倉", on_click=clear_position, width="stretch")

        if held:
            p = pos_p
            price_now = p["price"]
            pc = up_c if p["pnl"] > 0 else down_c if p["pnl"] < 0 else "gray"
            k1, k2, k3, k4 = st.columns(4)
            card(k1, "現價／損益", f"{price_now:,.2f}",
                 f"<span style='color:{pc}'>{p['pnl'] * 100:+.2f}%（扣費 {p['net_pnl'] * 100:+.2f}%）</span>")
            card(k2, "損益金額" if p["pnl_amt"] is not None else "損益兩平價",
                 f"<span style='color:{pc}'>{p['pnl_amt']:+,.0f}</span>" if p["pnl_amt"] is not None else f"{p['breakeven']:,.2f}",
                 f"兩平價 {p['breakeven']:,.2f}" if p["pnl_amt"] is not None else "含買賣手續費與稅")
            card(k3, "建議停損", f"<span style='color:#b7791f'>{p['stop']:,.2f}</span>",
                 f"距現價 {(p['stop'] / price_now - 1) * 100:+.1f}%")
            card(k4, "風險報酬比", "—" if p["rr"] is None else f"{p['rr']:.1f}", f"ATR {p['atr']:,.2f}（{p['atr'] / price_now * 100:.1f}%/日）"
                 + (f"｜持有 {p['held']} 天" if p["held"] is not None else ""))
            box_fn = {"danger": st.error, "warn": st.warning, "info": st.info, "good": st.success}[p["level"]]
            box_fn(f"**{p['head']}**　{p['body']}")
            st.caption("停損依據：" + p["stop_why"])
            for e in p["extra"]:
                st.markdown("- " + e)

            ai_key = secret("ANTHROPIC_API_KEY")
            if ai_key:
                if st.button("✨ 讓 Claude 分析這筆交易", key=f"pos_ai_btn_{code}"):
                    try:
                        with st.spinner("Claude 分析中…"):
                            titles = [i["title"] for i in wait(F_NEWS, [])]
                            st.session_state[f"pos_ai_{code}"] = news.ask_claude(
                                ai_key, position.prompt(code, name, r, p, titles), model="claude-sonnet-5-5")
                    except Exception as e:
                        st.warning(f"AI 分析失敗：{e}")
                if st.session_state.get(f"pos_ai_{code}"):
                    st.info(st.session_state[f"pos_ai_{code}"])
            else:
                st.caption("想要更完整的文字分析，可在設定檔加入 ANTHROPIC_API_KEY，這裡會出現「讓 Claude 分析」按鈕。")
            st.caption("建議以風險控管為主，依據歷史價格與波動計算，不保證報酬，最終決定請自行判斷。")

# --- 圖表（左）＋ 新聞（右） ---
PLOTLY_CFG = {"scrollZoom": True, "displaylogo": False,
              "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]}


def embed_html(html: str, height: int):
    """新版 Streamlit 用 st.iframe；舊版退回 components.html。"""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(html, height=height)


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


main, side = st.columns([2.4, 1], gap="medium")

def company_panel():
    if market == "IDX":
        st.caption("指數沒有公司資料。")
        return
    try:
        with st.spinner("抓取公司資料中…"):
            ci = F_CO.result(timeout=TIMEOUT) if F_CO else get_company(code, market, news_name(code), token or "")
    except Exception as e:
        st.caption(f"公司資料暫時取不到：{e}")
        return
    w = ci.get("wiki")
    if w:
        st.markdown(f"<div style='line-height:1.75'>{w['text']}</div>"
                    f"<div class='small'>來源：<a href='{w['url']}' target='_blank'>維基百科「{w['title']}」</a></div>",
                    unsafe_allow_html=True)
    if ci.get("summary_en"):
        if w:
            with st.expander("英文完整公司介紹（Yahoo Finance）"):
                st.markdown(ci["summary_en"])
        else:
            st.markdown(f"<div style='line-height:1.6;font-size:.92rem'>{ci['summary_en']}</div>"
                        "<div class='small'>來源：Yahoo Finance</div>", unsafe_allow_html=True)
    if not w and not ci.get("summary_en"):
        st.caption("找不到公司簡介。")
    if market == "US" and not ci.get("yahoo_ok"):
        st.caption("Yahoo 的公司資料暫時取不到（Yahoo 常限制連線次數），以下改用其他來源。")
    ai_key = secret("ANTHROPIC_API_KEY")
    if ai_key and (ci.get("summary_en") or w):
        if st.button("✨ 請 Claude 用白話說明這家公司在做什麼", key=f"co_ai_{code}", width="stretch"):
            try:
                with st.spinner("Claude 撰寫中…"):
                    src = (w["text"] if w else "") + "\n" + ci.get("summary_en", "")
                    st.session_state[f"co_ai_txt_{code}"] = news.ask_claude(ai_key, (
                        f"請用繁體中文、150 字內，向一般投資人說明「{code} {name}」這家公司靠什麼賺錢、主要產品與客戶、"
                        f"所屬產業鏈位置。只根據以下資料，不要編造：\n{src}"))
            except Exception as e:
                st.warning(f"AI 說明失敗：{e}")
        if st.session_state.get(f"co_ai_txt_{code}"):
            st.info(st.session_state[f"co_ai_txt_{code}"])

    rows = list(ci.get("rows", []))
    pos = -1 if rows and rows[-1][0] == "網址" else len(rows)
    if ci.get("shares"):
        rows.insert(pos, ("市值（現價估算）", f"{ci['shares'] * float(r['close']) / 1e8:,.0f} 億元"))
    elif ci.get("us_shares"):
        rows.insert(pos, ("市值（現價估算）", f"{ci['us_shares'] * float(r['close']) / 1e9:,.1f} 十億美元"))
    if market == "US" and ci.get("eps_ttm") and ci["eps_ttm"] > 0 and not any("本益比" in k for k, _ in ci.get("valuation", [])):
        ci = {**ci, "valuation": [("本益比（近 4 季，SEC EPS 計算）", f"{float(r['close']) / ci['eps_ttm']:.1f} 倍")]
              + list(ci.get("valuation", []))}
    if rows:
        st.markdown("**基本資料**")
        st.markdown(kv_html(rows), unsafe_allow_html=True)
    if ci.get("valuation"):
        st.markdown("**估值**")
        st.markdown(kv_html(ci["valuation"]), unsafe_allow_html=True)
    m = ci.get("margin")
    if m:
        st.markdown(f"**獲利能力（{m['季度']}）**")
        st.markdown(kv_html([(k, f"{m[k] * 100:.1f}%") for k in ("毛利率", "營益率", "淨利率") if pd.notna(m.get(k))]),
                    unsafe_allow_html=True)
    if m and m.get("營收"):
        st.caption(f"單季營收 {m['營收'] / 1e9:,.2f} 十億美元")
    if ci.get("eps") is not None and len(ci["eps"]):
        e = ci["eps"]
        ecol = [c for c in e.columns if c.startswith("EPS")][0]
        unit = "美元" if "美元" in ecol else "元"
        st.markdown(f"**每季 EPS（{unit}）**" + (f"　近 4 季合計 {ci['eps_ttm']:.2f}" if ci.get("eps_ttm") else ""))
        fig = go.Figure(go.Bar(x=e["季度"], y=e[ecol], text=[f"{v:.2f}" for v in e[ecol]],
                               textposition="outside", cliponaxis=False,
                               marker_color=[up_c if v >= 0 else down_c for v in e[ecol]]))
        fig.update_layout(height=200, margin=dict(l=0, r=0, t=16, b=0), yaxis=dict(visible=False), dragmode=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"eps_{code}")
    if ci.get("dividends") is not None and len(ci["dividends"]):
        st.markdown("**近期股利（元）**")
        st.dataframe(ci["dividends"], hide_index=True, width="stretch")
    st.caption("資料：證交所／櫃買中心公司基本資料、FinMind、維基百科" if market == "TW"
               else "資料：Yahoo Finance、美國證管會 SEC（EPS、營收、毛利率）、維基百科")
    if ci.get("status"):
        st.caption("資料來源狀態：" + "｜".join(f"{k} {v}" for k, v in ci["status"].items()))
        if not any(v == "成功" for v in ci["status"].values()):
            st.warning("美股資料來源都取不到。請先關掉黑色視窗，重新雙擊「啟動.bat」（會自動更新 yfinance 套件），"
                       "仍不行的話把這段「資料來源狀態」截圖給我。")


with side:
    st.markdown("#### 📰 重點消息")
    with st.spinner("抓取新聞中…"):
        items = wait(F_NEWS, [])
    only_key = st.toggle("只看重點", value=True, help="財報、營收、法說、股利、評等、籌碼、營運、風險相關，以及官方重大訊息")
    shown = [i for i in items if i["kind"] == "official" or i["tags"]] if only_key else items
    ai_key = secret("ANTHROPIC_API_KEY")
    if ai_key and items:
        if st.button("✨ AI 整理重點", width="stretch"):
            st.session_state[f"ai_{code}"] = True
        if st.session_state.get(f"ai_{code}"):
            try:
                with st.spinner("Claude 整理中…"):
                    txt = get_ai_summary(ai_key, f"{code} {name}".strip(),
                                         tuple((i["title"], i["source"], i["time"].isoformat()) for i in items[:30]))
                st.info(txt)
            except Exception as e:
                st.warning(f"AI 摘要失敗：{e}")
    box = st.container(height=640, border=False)
    with box:
        if not shown:
            st.caption("近 7 天沒有找到相關新聞。" if not items else "沒有標記為重點的新聞，關掉「只看重點」可看全部。")
        tone_col = {"pos": up_c, "neg": down_c, "": "inherit"}
        for it in shown:
            tags = "".join(f"<span class='chip' style='font-size:.72rem;padding:0 6px'>{t}</span>" for t in it["tags"])
            if it["kind"] == "official":
                tags = "<span class='chip' style='font-size:.72rem;padding:0 6px;color:#b7791f'>官方重訊</span>" + tags
            arrow = {"pos": "▲ ", "neg": "▼ ", "": ""}[it["tone"]]
            title = it["title"].replace("<", "&lt;")
            st.markdown(
                f"<div style='padding:8px 0;border-bottom:1px solid rgba(128,128,128,.18)'>"
                f"<a href='{it['link']}' target='_blank' style='text-decoration:none;color:{tone_col[it['tone']]};"
                f"font-weight:500;line-height:1.5'>{arrow}{title}</a>"
                f"<div class='small'>{it['time']:%m/%d %H:%M}・{it['source']}</div><div>{tags}</div></div>",
                unsafe_allow_html=True)
    st.caption("▲▼ 依標題關鍵字粗略判斷，僅供快速瀏覽。點標題開原文。")

tab_k, tab_chip, tab_co, tab_bt, tab_about = main.tabs(["K 線與預估", "籌碼 / 量能", "🏢 公司資訊", "回測驗證", "說明"])

with tab_co:
    company_panel()

with tab_k:
    today = None
    q = get_quotes((code,)).get(code)
    if q and q.get("open") and q.get("high") and q.get("low"):
        try:
            qd = pd.Timestamp(dt.datetime.strptime(q["date"], "%Y%m%d"))
        except ValueError:
            qd = None
        if qd is not None and qd > last["date"] and (SESSION == "TW" or live.is_open(SESSION)):
            today = {"date": qd.strftime("%Y-%m-%d"), "open": q["open"], "high": q["high"],
                     "low": q["low"], "close": q["price"]}
    embed_html(kchart.build(df, r["cone"], up_c, down_c, STOP_REF, stop_label=STOP_LABEL, today=today, dark=is_dark(),
                                 cost=(st.session_state.positions.get(code) or {}).get("cost"), view=N_VIEW,
                                 vol_unit={"TW": "張", "US": "股"}.get(market, "")), height=600)
    st.caption("藍色帶狀是未來 20 個交易日的 80% 機率區間（依近 60 日波動度估計），不是預言的走勢；"
               "虛線是停損參考；空心 K 棒是今天盤中暫定值，收盤資料更新後才納入評分。")

show = max(N_VIEW, 20)

with tab_chip:
    ch = r["chips"].tail(show)
    series = [c for c in ch.columns if c != "date"]
    if market == "TW":
        st.markdown("外資、投信每日買賣超累計（張）與融資餘額。大戶持股（集保千張大戶）需 FinMind 付費等級，尚未納入。")
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        palette = {"外資累計（張）": "#2f5bd3", "投信累計（張）": "#d1352b", "融資餘額（張）": "#b7791f"}
        for s in series:
            fig2.add_trace(go.Scatter(x=ch["date"], y=ch[s], name=s, line=dict(color=palette.get(s), width=2)),
                           secondary_y=(s == "融資餘額（張）"))
        fig2.update_yaxes(title_text="累計買賣超（張）", secondary_y=False)
        fig2.update_yaxes(title_text="融資餘額（張）", secondary_y=True)
        bar = df.tail(min(show, 60))
        fig3 = go.Figure()
        for col, nm, colr in [("foreign", "外資", "#2f5bd3"), ("trust", "投信", "#d1352b"), ("dealer", "自營商", "#8a8d95")]:
            fig3.add_trace(go.Bar(x=bar["date"], y=bar[col], name=nm, marker_color=colr))
        fig3.update_layout(barmode="relative", height=320, margin=dict(l=10, r=10, t=36, b=10),
                           legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0))
        fig3.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    else:
        st.markdown("美股沒有每日公布的法人買賣超，改看 OBV 能量潮（量能是否支持價格）與相對 S&P 500 的強弱。")
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        for s in series:
            fig2.add_trace(go.Scatter(x=ch["date"], y=ch[s], name=s, line=dict(width=2)),
                           secondary_y=(s == "OBV 能量潮"))
        fig3 = None
    fig2.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.08),
                       hovermode="x unified", dragmode="pan")
    st.plotly_chart(fig2, width="stretch", config=PLOTLY_CFG)
    if fig3 is not None:
        st.markdown("**近 60 日三大法人每日買賣超（張）**")
        fig3.update_layout(dragmode="pan")
        st.plotly_chart(fig3, width="stretch", config=PLOTLY_CFG)

with tab_bt:
    st.caption("拖曳平移、滾輪縮放、雙擊復原。")
    bt = r["bt"]
    st.markdown(f"期間 {bt['start']} ~ {r['asof']}。規則：評分 ≥ {bt['hi_th']} 隔日開盤買進，"
                f"≤ {bt['lo_th']} 隔日開盤賣出，已扣手續費{'與證交稅' if market == 'TW' else ''}。共 {bt['trades']} 次進出。")
    eq = bt["eq"]
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(x=eq["date"], y=(eq["策略"] - 1) * 100, name="評分策略", line=dict(color="#2f5bd3", width=2)))
    fig4.add_trace(go.Scatter(x=eq["date"], y=(eq["買進持有"] - 1) * 100, name="買進持有", line=dict(color="#8a8d95", width=2)))
    fig4.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="累積報酬（%）",
                       legend=dict(orientation="h", y=1.08), hovermode="x unified", dragmode="pan")
    st.plotly_chart(fig4, width="stretch", config=PLOTLY_CFG)

    a, b = st.columns(2)
    with a:
        st.markdown("**策略績效**")
        st.dataframe(pd.DataFrame([
            {"策略": "評分策略", "總報酬": f"{bt['rule']['total'] * 100:+.1f}%",
             "最大回撤": f"{bt['rule']['maxdd'] * 100:.1f}%", "夏普值": f"{bt['rule']['sharpe']:.2f}"},
            {"策略": "買進持有", "總報酬": f"{bt['bh']['total'] * 100:+.1f}%",
             "最大回撤": f"{bt['bh']['maxdd'] * 100:.1f}%", "夏普值": f"{bt['bh']['sharpe']:.2f}"},
        ]), hide_index=True, width="stretch")
    with b:
        st.markdown("**評分與之後 10 日表現**")
        st.dataframe(pd.DataFrame([
            {"當天評分": x["bucket"], "天數": x["n"],
             "平均報酬": "—" if pd.isna(x["mean"]) else f"{x['mean'] * 100:+.2f}%",
             "上漲機率": "—" if pd.isna(x["win"]) else f"{x['win'] * 100:.0f}%"}
            for x in reversed(r["by_bucket"])]), hide_index=True, width="stretch")

    ml = r["ml"]
    if ml["acc"] is not None:
        verdict = "通過" if ml["passed"] else "未通過"
        st.markdown(f"**機器學習模型（邏輯迴歸，滾動式訓練）**：樣本外 {ml['n']} 天，準確率 {ml['acc'] * 100:.0f}%，"
                    f"「永遠猜多數方向」為 {ml['base'] * 100:.0f}%，驗證{verdict}。"
                    + ("" if ml["passed"] else "模型沒有比瞎猜好，機率數字不要當作依據。"))

with tab_about:
    st.markdown(f"""
**上漲／下跌機率怎麼算**：起點是這檔股票歷史上「隔天上漲」與「10 天後上漲」的比例，再依下列因素加減，每一項加減多少都列在「📊 機率表」裡：
- 技術與籌碼（均線、RSI、量能、外資／投信買賣超、融資）：用這檔股票自己的歷史統計，樣本少就打折，總和再打五折
- 營收（台股）：最新月營收年增率、成長是否加速
- 新聞：近 2 天／7 天標題的利多利空數，風險類新聞扣分
- 隔夜美股（台股明日）：前一晚那斯達克與費半漲跌，用過去兩年的資料推算台股開盤跳空，再乘上個股相對大盤的敏感度
- 機率限制在 20%～80%。收盤後會持續抓新聞與美股，明日的機率會跟著更新

**回測分頁的「評分策略」**：舊版的技術評分（每個條件 +1／−1 加總），保留在回測頁做參考。

**台股 vs 美股**：台股有每日三大法人與融資資料，用來代表籌碼；美股沒有每日法人資料，改用 OBV 量能、RSI 與相對大盤強弱。顏色也依各市場慣例：台股紅漲綠跌、美股綠漲紅跌。

**預估扇形**：用近 60 日每日波動度推算機率區間，並把技術評分在歷史上的平均表現打五折當作方向偏移。

**模型驗證**：模型只用「當時已知」的資料訓練，再預測下一段時間，避免偷看未來。準確率要比「永遠猜多數方向」高 3 個百分點以上，且樣本至少 150 天，才算通過。

**資料來源**：台股歷史資料 FinMind、即時報價證交所基本市況報導站（約 5 秒延遲）；美股 Yahoo Finance（可能延遲至 15 分鐘）。\n\n**更新頻率**：開著頁面時，盤中報價每 60 秒自動更新；歷史資料與評分每 30 分鐘重新抓取，收盤資料公布後就會換成新的一天。評分只用收盤資料計算，盤中不會跳動，這樣才跟回測的條件一致。

本工具為個人研究用技術原型，不構成投資建議。
""")
