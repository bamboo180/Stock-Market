"""股票訊號看板（台股 + 美股）。執行：streamlit run app.py"""
from __future__ import annotations

import datetime as dt
import hmac
import inspect
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import engine
import indices
import kchart
import live
import news
import position
import recap
import store
import symbols

st.set_page_config(page_title="股票訊號看板", page_icon="📈", layout="wide")

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
PERIODS = {"1 年": 365, "2 年": 730, "3 年": 1095, "5 年": 1825}
LIVE_EVERY = 60          # 盤中即時報價更新秒數
VIEWS = ["📈 市場總覽", "📰 盤後整理", "🔍 個股分析"]
HOME, RECAP, STOCK = VIEWS


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
@st.cache_data(ttl=1800, show_spinner=False)
def load(code: str, days: int, token: str | None):
    market = engine.detect_market(code)
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    if market == "TW":
        df = engine.fetch_tw(code, start, token or None)
        name = engine.tw_name(code, token or None)
    elif market == "IDX":
        df = engine.fetch_index(code, start)
        name = indices.name_of(code) if indices.name_of(code) != code else ""
    else:
        df = engine.fetch_us(code, start)
        name = engine.us_name(code)
    return market, name, df


@st.cache_data(ttl=1800, show_spinner=False)
def analyze(code: str, days: int, token: str | None):
    market, name, raw = load(code, days, token)
    return market, name, engine.analyze(raw, market)


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
        _data = {"favorites": list(store.DEFAULT), "positions": {}, "indices": list(store.DEFAULT_INDICES)}
        st.session_state.store_err = str(e)
    st.session_state.favs = _data["favorites"]
    st.session_state.positions = _data["positions"]
    st.session_state.indices = _data.get("indices", list(store.DEFAULT_INDICES))


def save_favs():
    try:
        fav_store.save({"favorites": st.session_state.favs, "positions": st.session_state.positions,
                        "indices": st.session_state.indices})
        st.session_state.pop("store_err", None)
    except store.StoreError as e:
        st.session_state.store_err = str(e) + "（這次的變更只在目前畫面有效）"


def toggle_fav(code: str):
    favs = st.session_state.favs
    if code in favs:
        favs.remove(code)
        st.toast(f"已從最愛移除 {code}")
    else:
        favs.append(code)
        st.toast(f"已加入最愛 {code}")
    save_favs()


def open_stock(code: str):
    st.session_state.code = code
    st.session_state.nav = STOCK
    st.session_state.jump = True      # 從 fragment 裡的按鈕觸發時，需要整頁重跑才會換頁


def go_home():
    st.session_state.nav = HOME


@st.cache_data(ttl=86400, show_spinner=False)
def search_options(token: str):
    return symbols.build_options(symbols.load_tw(token or None), symbols.load_us())


SEARCH_OPTS, NAME2CODE = search_options(SAVED_TOKEN)


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
    if code and code not in st.session_state.favs:
        st.session_state.favs.append(code)
        save_favs()


def search_box(key: str, label: str, on_change):
    kw = {"filter_mode": "contains"} if "filter_mode" in inspect.signature(st.selectbox).parameters else {}
    st.selectbox(label, SEARCH_OPTS, index=None, key=key, on_change=on_change, accept_new_options=True,
                 placeholder="輸入代號、名稱或產業，例如：台積、2330、半導體、輝達、MSFT",
                 help="打幾個字就會列出符合的股票。清單外的美股可直接打代號或英文公司名後按 Enter。", **kw)


def remove_fav(code: str):
    if code in st.session_state.favs:
        st.session_state.favs.remove(code)
        save_favs()


def on_table_select():
    rows = st.session_state.fav_table.selection.rows
    if rows:
        open_stock(st.session_state.fav_order[rows[0]])
        st.session_state.jump = True      # 表格在 fragment 裡，需要整頁重跑才會換頁


# ----------------------------------------------------------------------------
# 側邊欄
# ----------------------------------------------------------------------------
if "code" not in st.session_state:
    st.session_state.code = (st.session_state.favs or ["2330"])[0]
if "nav" not in st.session_state:
    st.session_state.nav = HOME

with st.sidebar:
    st.radio("頁面", VIEWS, key="nav", label_visibility="collapsed")
    search_box("search", "🔍 搜尋股票", on_search)
    period = st.selectbox("資料期間", list(PERIODS), index=1)

    st.caption("⭐ 我的最愛")
    if st.session_state.favs:
        cols = st.columns(2)
        for i, f in enumerate(st.session_state.favs):
            cols[i % 2].button(f, key=f"fav_{f}", width="stretch", on_click=open_stock, args=(f,),
                               type="primary" if f == st.session_state.code and st.session_state.nav == STOCK else "secondary")
    else:
        st.caption("還沒有收藏。到個股頁按「☆ 加入最愛」。")

    st.divider()
    token = SAVED_TOKEN          # FinMind token 從 .streamlit/secrets.toml 讀取
    if st.button("清除快取並重新抓取", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"最愛儲存於：{fav_store.label}")
    st.caption("歷史資料每 30 分鐘更新；開盤時報價每 60 秒自動更新。")

st.session_state.pop("jump", None)
if st.session_state.nav != HOME:
    st.button("🏠 回首頁", on_click=go_home, type="tertiary")

if st.session_state.get("store_err"):
    st.warning(st.session_state.store_err)

days = PERIODS[period]
any_open = live.is_open("TW") or live.is_open("US")


def pos_analyze(df, r, price, pos):
    bd = dt.date.fromisoformat(pos["date"]) if pos.get("date") else None
    return position.analyze(df, r, price, pos["cost"], pos.get("shares") or 0, bd, pos.get("risk", 8) / 100)


def fmt_chg(x):
    return "—" if x is None or pd.isna(x) else f"{'▲' if x > 0 else '▼' if x < 0 else ''}{x * 100:+.2f}%"


# ============================================================================
# 頁面一：我的最愛總覽
# ============================================================================
if st.session_state.nav == HOME:
    st.title("📈 市場總覽")
    st.caption(f"台股 {live.status_text('TW')}｜美股 {live.status_text('US')}")

    # ---------- 市場指數 ----------
    @st.cache_data(ttl=60, show_spinner=False)
    def get_indices(syms: tuple):
        return indices.fetch(list(syms))

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
        per_row = 6
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
                              None if d["chg"] is None else f"{d['chg'] * 100:+.2f}%",
                              delta_color="inverse" if indices.is_tw(sym) else "normal",
                              chart_data=d["spark"] or None, chart_type="area", border=True,
                              help=f"{sym}｜{d['source']} {d['time']}")
                    st.button("K 線與分析 ›", key=f"idx_open_{sym}", on_click=open_stock, args=(sym,),
                              type="tertiary", width="stretch")

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
    favs = list(st.session_state.favs)
    if not favs:
        st.info("清單是空的。在下方輸入代號加入，或到個股頁按「☆ 加入最愛」。")

    @st.fragment(run_every=LIVE_EVERY if any_open else None)
    def fav_board():
        if st.session_state.pop("jump", False):
            st.rerun(scope="app")
        q = get_quotes(tuple(favs)) if favs else {}
        rows, order = [], []
        prog = st.empty()
        for i, c in enumerate(favs):
            if len(favs) > 1:
                prog.caption(f"分析中 {i + 1}/{len(favs)}：{c}")
            row = {"代號": c, "名稱": "", "市場": {"TW": "台股", "IDX": "指數"}.get(market_of(c), "美股"),
                   "現價": None, "漲跌": "—", "評分": None, "判讀": "", "10日區間": "", "停損參考": None, "成本": "", "持倉損益": "", "狀態": ""}
            try:
                market, name, r = analyze(c, 730, token)
                row.update({"名稱": name, "評分": r["score"], "判讀": r["verdict"],
                            "10日區間": f"{r['d10']['p10'] * 100:+.1f}% ~ {r['d10']['p90'] * 100:+.1f}%",
                            "停損參考": round(r["stop"], 2), "現價": r["close"], "漲跌": fmt_chg(r["chg"]),
                            "狀態": f"收盤 {r['asof']}"})
                qq = q.get(c)
                if qq:
                    row.update({"現價": qq["price"], "漲跌": fmt_chg(qq["chg"]),
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
            if ch.startswith("▲"):
                styles[row.index.get_loc("漲跌")] = f"color:{up}"
            elif ch.startswith("▼"):
                styles[row.index.get_loc("漲跌")] = f"color:{down}"
            if pd.notna(row["評分"]):
                c2 = up if row["評分"] >= 3 else down if row["評分"] <= -3 else ""
                styles[row.index.get_loc("判讀")] = f"color:{c2}" if c2 else ""
            if "⚠" in row["狀態"]:
                styles[row.index.get_loc("狀態")] = "color:#b7791f"
            return styles

        st.dataframe(table.style.apply(color, axis=1), hide_index=True, width="stretch",
                     key="fav_table", on_select=on_table_select, selection_mode="single-row",
                     column_config={"現價": st.column_config.NumberColumn(format="%.2f"),
                                    "停損參考": st.column_config.NumberColumn(format="%.2f"),
                                    "評分": st.column_config.NumberColumn(format="%+d")})
        st.caption("點選任一列左側的方框即可開啟該股分析。"
                   + (f" 盤中每 {LIVE_EVERY} 秒自動更新報價。" if any_open else ""))

    fav_board()

    with st.expander("管理清單"):
        search_box("add_search", "新增到最愛", add_fav_from_input)
        for c in favs:
            x, y = st.columns([4, 1], vertical_alignment="center")
            x.write(c)
            y.button("移除", key=f"rm_{c}", on_click=remove_fav, args=(c,), width="stretch")

    st.caption("本工具為個人研究用技術原型，不構成投資建議。")
    st.stop()



# ============================================================================
# 頁面二：盤後整理
# ============================================================================
@st.cache_data(ttl=86400, show_spinner=False)
def get_groups(token: str):
    return symbols.load_tw(token or None)


@st.cache_data(ttl=1800, show_spinner=False)
def get_tw_recap(token: str, favs: tuple):
    return recap.tw(get_groups(token), token or None, list(favs))


@st.cache_data(ttl=1800, show_spinner=False)
def get_us_recap(favs: tuple):
    return recap.us(list(favs))


def pct_table(df: pd.DataFrame, key: str, tw_colors: bool, cols: dict, height: int | None = None):
    """可點選的漲跌表，點一列開啟個股分析。"""
    if df is None or df.empty:
        st.caption("暫無資料")
        return
    show = df.rename(columns=cols)[list(cols.values())].reset_index(drop=True)
    up, down = ("#d1352b", "#14825a") if tw_colors else ("#14825a", "#d1352b")
    pct_col = cols.get("pct")

    def style(col):
        if col.name != pct_col:
            return [""] * len(col)
        return [f"color:{up}" if v > 0 else f"color:{down}" if v < 0 else "" for v in col]

    fmt = {pct_col: lambda v: f"{v * 100:+.2f}%"} if pct_col else {}
    if "成交金額(億)" in show:
        show["成交金額(億)"] = show["成交金額(億)"] / 1e8
        fmt["成交金額(億)"] = "{:,.1f}"
    if "收盤" in show:
        fmt["收盤"] = "{:,.2f}"

    def on_pick(k=key, codes=list(df["code"])):
        rows = st.session_state[k].selection.rows
        if rows:
            open_stock(codes[rows[0]])

    st.dataframe(show.style.apply(style).format(fmt), hide_index=True, width="stretch", key=key,
                 on_select=on_pick, selection_mode="single-row", **({"height": height} if height else {}))


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
            st.caption(f"交易日 {tr['date']}｜資料：證交所每日收盤行情、FinMind 三大法人與融資"
                       + ("" if tr.get("has_otc") else "｜排行只含上市股票（上櫃資料尚未更新）"))
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
                f["name"] = ""
                pct_table(f, "us_favs", False, ucols)

    st.caption("本工具為個人研究用技術原型，不構成投資建議。")
    st.stop()

# ============================================================================
# 頁面三：個股分析
# ============================================================================
code = st.session_state.code
try:
    with st.spinner(f"抓取 {code} 資料中…"):
        market, name, r = analyze(code, days, token)
except engine.DataError as e:
    st.error(str(e))
    st.stop()

up_c, down_c = colors(market, code)
SESSION = live.session_of(code, market)
df, last = r["df"], r["last"]
cur = {"TW": "NT$", "US": "US$"}.get(market, "點")
mk_label = {"TW": "台股", "US": "美股", "IDX": "指數"}[market]
is_fav = code in st.session_state.favs
held = st.session_state.positions.get(code)
_q0 = get_quotes((code,)).get(code)
pos_p = pos_analyze(df, r, _q0["price"] if _q0 else r["close"], held) if held else None
STOP_REF, STOP_LABEL = (pos_p["stop"], "我的停損") if pos_p else (r["stop"], "停損參考")

t1, t2 = st.columns([5, 1.2], vertical_alignment="center")
t1.title(f"{code} {name}".strip())
t2.button("★ 已在最愛" if is_fav else "☆ 加入最愛", on_click=toggle_fav, args=(code,),
          type="primary" if is_fav else "secondary", width="stretch")
st.caption(f"{mk_label}｜分析基準：{r['asof']} 收盤｜{len(df)} 個交易日")


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
        f"<div class='row'><span class='px' style='color:{chg_col}'>{q['price']:,.2f}</span>"
        f"<span style='color:{chg_col};font-weight:600'>{fmt_chg(q['chg'])}</span>"
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
card(c1, "收盤價（分析基準）", f"{r['close']:,.2f}", f"{r['chg'] * 100:+.2f}%（{cur}）", sub_color=chg_c)
tone_color = {"up": up_c, "down": down_c, "flat": "inherit"}[r["tone"]]
card(c2, f"綜合評分（±{r['n_parts']}）",
     f"{r['score']:+d}　<span style='font-size:1.05rem'>{r['verdict']}</span>", color=tone_color)
d = r["d10"]
card(c3, "10 日預估區間（80%）",
     f"<span style='color:{down_c}'>{d['p10'] * 100:+.1f}%</span> ~ <span style='color:{up_c}'>{d['p90'] * 100:+.1f}%</span>",
     f"中位數 {d['p50'] * 100:+.1f}%")
card(c4, "停損參考", f"{r['stop']:,.2f}", f"跌破代表超出悲觀預估（{d['p10'] * 100:+.1f}%）")
ml = r["ml"]
if r["prob"] is None or ml["acc"] is None:
    card(c5, "模型上漲機率", "—", "樣本不足")
else:
    card(c5, "模型上漲機率", f"{r['prob'] * 100:.0f}%",
         "已通過驗證" if ml["passed"] else "未通過驗證，僅供參考",
         sub_color="#14825a" if ml["passed"] else "#b7791f")

chips = "".join(
    f"<span class='chip' style='color:{up_c if v > 0 else down_c}'>{'＋' if v > 0 else '－'} {engine.label(k, v)}</span>"
    for k, v in r["parts"].items())
st.markdown(chips, unsafe_allow_html=True)

st.markdown("#### AI 解讀")
st.markdown(f"<div class='ai'>{r['explain']}</div>", unsafe_allow_html=True)

@st.cache_data(ttl=900, show_spinner=False)
def get_news(code: str, market: str, name: str):
    return news.fetch(code, market, name)


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
                            titles = [i["title"] for i in get_news(code, market, name)]
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

with side:
    st.markdown("#### 📰 重點消息")
    with st.spinner("抓取新聞中…"):
        items = get_news(code, market, name)
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

tab_k, tab_chip, tab_bt, tab_about = main.tabs(["K 線與預估", "籌碼 / 量能", "回測驗證", "說明"])

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
                                 cost=(st.session_state.positions.get(code) or {}).get("cost"),
                                 vol_unit={"TW": "張", "US": "股"}.get(market, "")), height=600)
    st.caption("藍色帶狀是未來 20 個交易日的 80% 機率區間（依近 60 日波動度估計），不是預言的走勢；"
               "虛線是停損參考；空心 K 棒是今天盤中暫定值，收盤資料更新後才納入評分。")

show = len(df)

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
        fig3.update_layout(barmode="relative", height=300, margin=dict(l=10, r=10, t=30, b=10),
                           title="近 60 日三大法人每日買賣超（張）", legend=dict(orientation="h", y=1.15))
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

    if ml["acc"] is not None:
        verdict = "通過" if ml["passed"] else "未通過"
        st.markdown(f"**機器學習模型（邏輯迴歸，滾動式訓練）**：樣本外 {ml['n']} 天，準確率 {ml['acc'] * 100:.0f}%，"
                    f"「永遠猜多數方向」為 {ml['base'] * 100:.0f}%，驗證{verdict}。"
                    + ("" if ml["passed"] else "模型沒有比瞎猜好，機率數字不要當作依據。"))

with tab_about:
    st.markdown(f"""
**評分怎麼算**：每個條件成立 +1、不成立 -1，加總為綜合評分。目前使用 {r['n_parts']} 項：{"、".join(r["parts"])}。

**台股 vs 美股**：台股有每日三大法人與融資資料，用來代表籌碼；美股沒有每日法人資料，改用 OBV 量能、RSI 與相對大盤強弱。顏色也依各市場慣例：台股紅漲綠跌、美股綠漲紅跌。

**預估扇形**：用近 60 日每日波動度推算機率區間，並把這個評分在歷史上的平均表現打五折當作方向偏移。不考慮除權息、財報與重大新聞。

**模型驗證**：模型只用「當時已知」的資料訓練，再預測下一段時間，避免偷看未來。準確率要比「永遠猜多數方向」高 3 個百分點以上，且樣本至少 150 天，才算通過。

**資料來源**：台股歷史資料 FinMind、即時報價證交所基本市況報導站（約 5 秒延遲）；美股 Yahoo Finance（可能延遲至 15 分鐘）。\n\n**更新頻率**：開著頁面時，盤中報價每 60 秒自動更新；歷史資料與評分每 30 分鐘重新抓取，收盤資料公布後就會換成新的一天。評分只用收盤資料計算，盤中不會跳動，這樣才跟回測的條件一致。

本工具為個人研究用技術原型，不構成投資建議。
""")
