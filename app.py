"""股票訊號看板（台股 + 美股）。執行：streamlit run app.py"""
from __future__ import annotations

import datetime as dt
import hmac
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import engine

st.set_page_config(page_title="股票訊號看板", page_icon="📈", layout="wide")

st.markdown("""
<style>
.block-container{padding-top:1.4rem;max-width:1200px}
.verdict{font-size:1.5rem;font-weight:700;margin:0}
.chip{display:inline-block;margin:2px 4px 2px 0;padding:2px 10px;border-radius:6px;font-size:.85rem;
      border:1px solid rgba(128,128,128,.35)}
.ai{border-left:3px solid #2f5bd3;padding:6px 0 6px 12px;line-height:1.75}
.small{color:gray;font-size:.82rem}
.card .lbl{color:gray;font-size:.82rem;margin-bottom:2px}
.card .val{font-size:1.55rem;font-weight:600;line-height:1.3;font-variant-numeric:tabular-nums}
.card .sub{font-size:.8rem;color:gray}
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
QUICK = ["2330", "2317", "2454", "0050", "AAPL", "NVDA", "TSLA", "QQQ"]


# ----------------------------------------------------------------------------
# 資料（快取 1 小時，避免重複打 API）
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load(code: str, days: int, token: str | None):
    market = engine.detect_market(code)
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    if market == "TW":
        df = engine.fetch_tw(code, start, token or None)
        name = engine.tw_name(code, token or None)
    else:
        df = engine.fetch_us(code, start)
        name = engine.us_name(code)
    return market, name, df


def colors(market: str) -> tuple[str, str]:
    """台股紅漲綠跌，美股綠漲紅跌。"""
    red, green = "#d1352b", "#14825a"
    return (red, green) if market == "TW" else (green, red)


# ----------------------------------------------------------------------------
# 側邊欄
# ----------------------------------------------------------------------------
if "code" not in st.session_state:
    st.session_state.code = "2330"

with st.sidebar:
    st.header("查詢")
    with st.form("query"):
        code_in = st.text_input("股票代號", value=st.session_state.code,
                                help="台股輸入數字（2330、0050），美股輸入英文（AAPL、NVDA）")
        period = st.selectbox("資料期間", list(PERIODS), index=1)
        submitted = st.form_submit_button("分析", type="primary", width="stretch")
    if submitted:
        st.session_state.code = code_in.strip().upper()

    st.caption("快速選擇")
    cols = st.columns(2)
    for i, q in enumerate(QUICK):
        if cols[i % 2].button(q, key=f"q_{q}", width="stretch"):
            st.session_state.code = q
            st.rerun()

    st.divider()
    if SAVED_TOKEN:
        st.caption("FinMind token：已從設定檔載入 ✓")
        token = SAVED_TOKEN
    else:
        token = st.text_input("FinMind token（選填）", type="password",
                              help="不填也能用，但每小時次數有限。可寫進 .streamlit/secrets.toml 就不用每次貼。")
    if st.button("清除快取並重新抓取", width="stretch"):
        load.clear()
        st.rerun()
    st.caption("資料：台股 FinMind、美股 Yahoo Finance。收盤後資料，非即時報價。")

code = st.session_state.code
days = PERIODS[period]

# ----------------------------------------------------------------------------
# 主畫面
# ----------------------------------------------------------------------------
try:
    with st.spinner(f"抓取 {code} 資料中…"):
        market, name, raw = load(code, days, token)
    r = engine.analyze(raw, market)
except engine.DataError as e:
    st.error(str(e))
    st.stop()

up_c, down_c = colors(market)
df, last = r["df"], r["last"]
cur = "NT$" if market == "TW" else "US$"
mk_label = "台股" if market == "TW" else "美股"

st.title(f"{code} {name}".strip())
st.caption(f"{mk_label}｜資料日 {r['asof']}（收盤）｜{len(df)} 個交易日")

# --- 摘要 ---
def card(col, lbl, val, sub="", color="inherit", sub_color="gray"):
    col.markdown(f"<div class='card'><div class='lbl'>{lbl}</div>"
                 f"<div class='val' style='color:{color}'>{val}</div>"
                 f"<div class='sub' style='color:{sub_color}'>{sub}</div></div>", unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns([1, 1.3, 1.2, 1, 1])
chg_c = up_c if r["chg"] > 0 else down_c if r["chg"] < 0 else "gray"
card(c1, "收盤價", f"{r['close']:,.2f}", f"{r['chg'] * 100:+.2f}%（{cur}）", sub_color=chg_c)
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

# --- 圖表分頁 ---
tab_k, tab_chip, tab_bt, tab_about = st.tabs(["K 線與預估", "籌碼 / 量能", "回測驗證", "說明"])

with tab_k:
    show = st.slider("顯示最近幾個交易日", 60, len(df), min(180, len(df)), step=10, key="k_range")
    v = df.tail(show)
    cone = r["cone"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=v["date"], open=v["open"], high=v["high"], low=v["low"], close=v["close"],
                                 increasing_line_color=up_c, increasing_fillcolor=up_c,
                                 decreasing_line_color=down_c, decreasing_fillcolor=down_c, name="K 線"), 1, 1)
    fig.add_trace(go.Scatter(x=v["date"], y=v["ma20"], name="月線 MA20", line=dict(color="#b7791f", width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=v["date"], y=v["ma60"], name="季線 MA60", line=dict(color="#8a8d95", width=1.3)), 1, 1)
    x0 = [last["date"]]
    y0 = [r["close"]]
    fig.add_trace(go.Scatter(x=x0 + list(cone["date"]), y=y0 + list(cone["p90"]), line=dict(color="#2f5bd3", width=1, dash="dot"),
                             name="P90 樂觀", showlegend=False), 1, 1)
    fig.add_trace(go.Scatter(x=x0 + list(cone["date"]), y=y0 + list(cone["p10"]), line=dict(color="#2f5bd3", width=1, dash="dot"),
                             fill="tonexty", fillcolor="rgba(47,91,211,.15)", name="80% 機率區間"), 1, 1)
    fig.add_trace(go.Scatter(x=x0 + list(cone["date"]), y=y0 + list(cone["p50"]), line=dict(color="#2f5bd3", width=2),
                             name="預估中位數"), 1, 1)
    vcol = np.where(v["close"] >= v["open"], up_c, down_c)
    fig.add_trace(go.Bar(x=v["date"], y=v["vol"], marker_color=vcol, opacity=0.5, name="成交量", showlegend=False), 2, 1)
    fig.update_layout(height=620, margin=dict(l=10, r=10, t=10, b=10), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch")
    st.caption("藍色區域是未來 20 個交易日的 80% 機率區間（依近 60 日波動度估計），不是一條預言的走勢。")

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
                       hovermode="x unified")
    st.plotly_chart(fig2, width="stretch")
    if fig3 is not None:
        st.plotly_chart(fig3, width="stretch")

with tab_bt:
    bt = r["bt"]
    st.markdown(f"期間 {bt['start']} ~ {r['asof']}。規則：評分 ≥ {bt['hi_th']} 隔日開盤買進，"
                f"≤ {bt['lo_th']} 隔日開盤賣出，已扣手續費{'與證交稅' if market == 'TW' else ''}。共 {bt['trades']} 次進出。")
    eq = bt["eq"]
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(x=eq["date"], y=(eq["策略"] - 1) * 100, name="評分策略", line=dict(color="#2f5bd3", width=2)))
    fig4.add_trace(go.Scatter(x=eq["date"], y=(eq["買進持有"] - 1) * 100, name="買進持有", line=dict(color="#8a8d95", width=2)))
    fig4.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="累積報酬（%）",
                       legend=dict(orientation="h", y=1.08), hovermode="x unified")
    st.plotly_chart(fig4, width="stretch")

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

**資料來源**：台股 FinMind（免費版有每小時次數限制）、美股 Yahoo Finance（透過 yfinance）。都是收盤後資料。

本工具為個人研究用技術原型，不構成投資建議。
""")
