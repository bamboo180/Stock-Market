"""互動 K 線圖（TradingView Lightweight Charts 4.1.3）。

外觀與操作仿看盤軟體：
  上方：1分／5分／15分／30分／60分／日／週／月 切換（日、週、月在圖內即時換算；分線資料由 Python 先抓好放進來）
  第二列：游標所在 K 棒的 開高低收、漲跌、量、MA5／MA20／MA60
  左側：畫線工具（趨勢線、射線、水平線、垂直線、矩形、平行通道、費波那契、文字、量測、磁吸、刪除）
  右上「指標」：開關均線、成交量、預估區間、停損線、成本線
  畫的線與指標設定存在瀏覽器（換週期、重新整理都還在；每檔股票各自一份）
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

LIB = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"

# 分線：Yahoo 的限制是 1 分線只能抓近 7 天、5 分線 60 天、60 分線 2 年；抓少一點讓網頁不會太大
INTRA = {"1": ("1m", "2d"), "5": ("5m", "1mo"), "60": ("60m", "6mo")}


def _sec(x) -> int:
    """日期 → 當天 00:00 的 UTC 秒數（圖上的時間一律當成 UTC 顯示，就不會被時區位移）。"""
    t = pd.Timestamp(x)
    if t.tz is not None:
        t = t.tz_localize(None)
    return int(t.normalize().value // 10**9)


def _r(v: float) -> float:
    v = float(v)
    return round(v, 2) if abs(v) >= 10 else round(v, 4)


def intraday_sets(code: str, market: str) -> dict:
    """抓 1／5／60 分線（15、30 分由 5 分線在網頁裡合成）。時間轉成交易所當地時間再當 UTC 存，圖上就顯示當地時間。
    回傳 {"1": [[t, o, h, l, c, v], …], "5": …, "60": …}；抓不到的週期就不放。"""
    try:
        import yfinance as yf
    except Exception:
        return {}
    tw = market == "TW" or code in ("^TWII", "^TWOII")
    tz = "Asia/Taipei" if tw else "America/New_York"
    tickers = [code + ".TW", code + ".TWO"] if market == "TW" else [code]
    scale = 1000 if market == "TW" else 1          # 台股 Yahoo 的量是「股」，換成「張」跟日線一致
    for t in tickers:
        out = {}
        for k, (iv, per) in INTRA.items():
            try:
                d = yf.Ticker(t).history(period=per, interval=iv, prepost=False)
            except Exception:
                continue
            if d is None or d.empty:
                continue
            d = d.dropna(subset=["Open", "High", "Low", "Close"])
            idx = d.index.tz_convert(tz).tz_localize(None) if d.index.tz is not None else d.index
            secs = (idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)    # 不受 ns／us 儲存單位影響
            vol = d["Volume"].fillna(0).to_numpy() / scale
            out[k] = [[int(s), _r(o), _r(h), _r(l), _r(c), int(round(v))]
                      for s, o, h, l, c, v in zip(secs, d["Open"], d["High"], d["Low"], d["Close"], vol)]
        if out:
            return out
    return {}


def build(df: pd.DataFrame, cone: pd.DataFrame, up: str, down: str, stop: float | None, stop_label: str = "停損參考",
          today: dict | None = None, dark: bool = False, cost: float | None = None, height: int = 600,
          vol_unit: str = "張", view: int = 66, intra: dict | None = None, name: str = "", key: str = "",
          source: str = "") -> str:
    """view：日線一開始顯示最近幾根 K 棒（側邊欄「資料期間」）。intra：intraday_sets() 的結果。"""
    day = [[_sec(r.date), _r(r.open), _r(r.high), _r(r.low), _r(r.close), float(r.vol)] for r in df.itertuples()]
    prov = None
    if today:   # 今日盤中暫定：空心 K 棒
        prov = [_sec(today["date"]), _r(today["open"]), _r(today["high"]), _r(today["low"]), _r(today["close"]), 0]
    last_t, last_c = day[-1][0], day[-1][4]
    cone_d = {}
    if cone is not None and len(cone):
        for q in ("p10", "p50", "p90"):
            cone_d[q] = [[last_t, last_c]] + [[_sec(d), _r(v)] for d, v in zip(cone["date"], cone[q]) if pd.notna(v)]

    th = ({"bg": "#131722", "panel": "#1a1e2b", "fg": "#d1d4dc", "sub": "#8b8f9a", "grid": "#252a37",
           "border": "#2a2e39", "act": "#c9a227", "actFg": "#131722", "cone": "rgba(125,155,255,.16)",
           "hover": "#262b3a"}
          if dark else
          {"bg": "#f3efe6", "panel": "#efe9dd", "fg": "#2b2b2b", "sub": "#7b766c", "grid": "#e4ddcf",
           "border": "#ddd5c4", "act": "#7a5a12", "actFg": "#ffffff", "cone": "rgba(47,91,211,.10)",
           "hover": "#e6dfd0"})
    data = json.dumps({"day": day, "prov": prov, "intra": intra or {}, "cone": cone_d, "stop": stop,
                       "stopLabel": stop_label, "cost": cost, "up": up, "down": down, "th": th, "unit": vol_unit,
                       "view": int(view), "name": name, "key": key or name, "source": source}, ensure_ascii=False)
    chart_h = height - 74
    return TEMPLATE.replace("__DATA__", data).replace("__LIB__", LIB).replace("__H__", str(chart_h)) \
        .replace("__BG__", th["bg"]).replace("__PANEL__", th["panel"]).replace("__FG__", th["fg"]) \
        .replace("__SUB__", th["sub"]).replace("__BORDER__", th["border"]).replace("__ACT__", th["act"]) \
        .replace("__ACTFG__", th["actFg"]).replace("__HOVER__", th["hover"])


# 左側工具列的小圖示（SVG）
_ICON = {
    "cursor": '<path d="M5 3l12 7-5 1.5L9.5 17z"/>',
    "trend": '<path d="M4 16L16 4"/><circle cx="4" cy="16" r="1.6"/><circle cx="16" cy="4" r="1.6"/>',
    "ray": '<path d="M4 16L17 3"/><circle cx="4" cy="16" r="1.6"/><path d="M14 3h3v3"/>',
    "hline": '<path d="M2 10h16"/><circle cx="10" cy="10" r="1.6"/>',
    "vline": '<path d="M10 2v16"/><circle cx="10" cy="10" r="1.6"/>',
    "rect": '<rect x="4" y="5" width="12" height="10" rx="1"/>',
    "channel": '<path d="M3 13L13 3M7 17L17 7"/>',
    "fib": '<path d="M3 4h14M3 8h14M3 12h14M3 16h14" stroke-dasharray="2 2"/><path d="M4 16L16 4"/>',
    "text": '<path d="M5 4h10M10 4v12"/>',
    "measure": '<path d="M3 15L15 3l2 2L5 17z"/><path d="M6 12l1.5 1.5M9 9l1.5 1.5M12 6l1.5 1.5"/>',
    "magnet": '<path d="M5 3v7a5 5 0 0010 0V3h-3v7a2 2 0 01-4 0V3z"/>',
    "trash": '<path d="M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11"/>',
    "undo": '<path d="M7 5L3 9l4 4"/><path d="M3 9h9a5 5 0 010 10h-2"/>',
}
_TOOLS = [("cursor", "游標（拖曳平移、滾輪縮放）"), ("trend", "趨勢線"), ("ray", "射線"), ("hline", "水平線"),
          ("vline", "垂直線"), ("rect", "矩形"), ("channel", "平行通道（先畫一條線，再點一下決定寬度）"),
          ("fib", "費波那契回撤"), ("text", "文字"), ("measure", "量測（拉一段看漲跌幅與根數）")]


def _svg(k: str) -> str:
    return (f'<svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{_ICON[k]}</svg>')


_TOOLBAR = "".join(f'<button class="tool" data-tool="{k}" title="{t}">{_svg(k)}</button>' for k, t in _TOOLS) + \
    '<div class="sep"></div>' + \
    f'<button class="tool" id="magnet" title="磁吸（點到 K 棒的開高低收）">{_svg("magnet")}</button>' + \
    f'<button class="tool" id="undo" title="復原上一筆（Ctrl+Z）">{_svg("undo")}</button>' + \
    f'<button class="tool" id="trash" title="刪除全部畫線（雙擊某條線只刪那一條）">{_svg("trash")}</button>'

TEMPLATE = """
<div id="wrap">
  <div id="top">
    <div id="tfs">
      <button data-tf="1">1分</button><button data-tf="5">5分</button><button data-tf="15">15分</button>
      <button data-tf="30">30分</button><button data-tf="60">60分</button>
      <button data-tf="D">日</button><button data-tf="W">週</button><button data-tf="M">月</button>
    </div>
    <div id="src"></div>
    <div style="flex:1"></div>
    <div id="indWrap"><button id="indBtn">指標 ▾</button><div id="indMenu"></div></div>
  </div>
  <div id="legend"></div>
  <div id="body">
    <div id="tools">__TOOLBAR__</div>
    <div id="c"><canvas id="ov"></canvas><input id="txt" placeholder="輸入文字，Enter 完成"></div>
  </div>
</div>
<style>
 html,body{margin:0;background:__BG__;font-family:system-ui,-apple-system,"Microsoft JhengHei",sans-serif;color:__FG__}
 #wrap{border:1px solid __BORDER__;border-radius:10px;overflow:hidden;background:__BG__}
 #top{display:flex;align-items:center;gap:10px;padding:6px 10px;background:__PANEL__;border-bottom:1px solid __BORDER__;
      flex-wrap:wrap}
 #tfs{display:flex;gap:2px;flex-wrap:wrap}
 #tfs button,#indBtn{font:inherit;font-size:13px;padding:4px 10px;border:0;border-radius:7px;background:transparent;
      color:__FG__;cursor:pointer}
 #tfs button:hover,#indBtn:hover{background:__HOVER__}
 #tfs button.on{background:__ACT__;color:__ACTFG__;font-weight:700}
 #tfs button:disabled{opacity:.35;cursor:not-allowed}
 #src{font-size:12px;color:__SUB__}
 #indWrap{position:relative}
 #indMenu{display:none;position:absolute;right:0;top:30px;z-index:20;background:__BG__;border:1px solid __BORDER__;
      border-radius:8px;padding:6px 10px;box-shadow:0 6px 18px rgba(0,0,0,.15);white-space:nowrap;font-size:13px}
 #indMenu label{display:flex;align-items:center;gap:6px;padding:3px 0;cursor:pointer}
 #legend{font-size:12.5px;padding:5px 10px 4px 52px;line-height:1.6;
      font-variant-numeric:tabular-nums;min-height:18px}
 #legend b{font-weight:700}
 #legend .k{color:__SUB__;margin-left:8px}
 #body{display:flex}
 #tools{width:40px;flex:none;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 0;
      border-right:1px solid __BORDER__;background:__PANEL__}
 #tools .tool{width:32px;height:30px;border:0;border-radius:7px;background:transparent;color:__FG__;cursor:pointer;
      display:flex;align-items:center;justify-content:center;padding:0}
 #tools .tool:hover{background:__HOVER__}
 #tools .tool.on{background:__ACT__;color:__ACTFG__}
 #tools .sep{width:24px;height:1px;background:__BORDER__;margin:4px 0}
 #c{position:relative;flex:1;height:__H__px;min-width:0}
 #ov{position:absolute;left:0;top:0;z-index:4;pointer-events:none}
 #c.drawing #ov{pointer-events:auto;cursor:crosshair}
 #txt{position:absolute;display:none;z-index:6;font:13px system-ui;padding:3px 6px;border:1px solid #2962ff;border-radius:4px;
      background:__BG__;color:__FG__;width:160px}
</style>
<script src="__LIB__"></script>
<script>
const D = __DATA__;
const T = D.th, UP = D.up, DN = D.down;
const el = document.getElementById('c'), ov = document.getElementById('ov'), ctx = ov.getContext('2d');
const legend = document.getElementById('legend');
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const IND_DEF = [['ma5', 'MA5', '#e8a33d'], ['ma20', 'MA20', '#3b7ddd'], ['ma60', 'MA60', '#9b59b6'],
                 ['vol', '成交量'], ['cone', '預估區間（日線）'], ['stop', D.stopLabel || '停損參考'], ['cost', '我的成本']];
const ind = Object.assign({ma5: true, ma20: true, ma60: true, vol: true, cone: true, stop: true, cost: true},
                          store.get('kind', {}));
const DKEY = 'kdraw:' + D.key;
let drawings = store.get(DKEY, []);

// ---------------- 資料：各週期 K 棒 ----------------
const toBar = a => ({time: a[0], open: a[1], high: a[2], low: a[3], close: a[4], vol: a[5]});
const dayBars = D.day.map(toBar);
if (D.prov) dayBars.push(Object.assign(toBar(D.prov), {prov: true}));
function agg(bars, keyFn) {
  const out = [];
  for (const b of bars) {
    const k = keyFn(b.time), o = out[out.length - 1];
    if (o && o._k === k) { o.high = Math.max(o.high, b.high); o.low = Math.min(o.low, b.low); o.close = b.close;
                           o.vol += b.vol; if (b.prov) o.prov = true; }
    else out.push({_k: k, time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, vol: b.vol, prov: b.prov});
  }
  return out;
}
const weekKey = t => { const d = new Date(t * 1000), w = (d.getUTCDay() + 6) % 7; return t - w * 86400; };
const monthKey = t => { const d = new Date(t * 1000); return d.getUTCFullYear() * 12 + d.getUTCMonth(); };
const I = {}; for (const k in D.intra) I[k] = D.intra[k].map(toBar);
const BARS = {
  D: () => dayBars, W: () => agg(dayBars, weekKey), M: () => agg(dayBars, monthKey),
  '1': () => I['1'] || [], '5': () => I['5'] || [], '60': () => I['60'] || [],
  '15': () => I['5'] ? agg(I['5'], t => Math.floor(t / 900)) : [], '30': () => I['5'] ? agg(I['5'], t => Math.floor(t / 1800)) : [],
};
const intraday = tf => !['D', 'W', 'M'].includes(tf);

// ---------------- 圖表 ----------------
const WK = '日一二三四五六';
const pad = n => String(n).padStart(2, '0');
let TF = store.get('ktf', 'D'); if (!BARS[TF] || !BARS[TF]().length) TF = 'D';
const chart = LightweightCharts.createChart(el, {
  autoSize: true,
  layout: {background: {type: 'solid', color: T.bg}, textColor: T.fg, fontSize: 12,
           fontFamily: 'system-ui,-apple-system,"Microsoft JhengHei",sans-serif'},
  grid: {vertLines: {color: T.grid}, horzLines: {color: T.grid}},
  rightPriceScale: {borderColor: T.border, scaleMargins: {top: 0.06, bottom: 0.24}},
  timeScale: {borderColor: T.border, rightOffset: 6, barSpacing: 8, minBarSpacing: 1, timeVisible: true,
    tickMarkFormatter: (t, type) => { const d = new Date(t * 1000);
      if (type === 0) return d.getUTCFullYear() + '年';
      if (type === 1) return (d.getUTCMonth() + 1) + '月';
      if (type === 2) return intraday(TF) ? (d.getUTCMonth() + 1) + '/' + d.getUTCDate() : d.getUTCDate() + '日';
      return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()); }},
  crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
  handleScroll: {mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
  handleScale: {mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true},
  localization: {priceFormatter: p => p.toLocaleString('en-US', {minimumFractionDigits: p < 10 ? 2 : 2, maximumFractionDigits: p < 10 ? 3 : 2}),
    timeFormatter: t => { const d = new Date(t * 1000), ds = d.getUTCFullYear() + '/' + pad(d.getUTCMonth() + 1) + '/' + pad(d.getUTCDate());
      return intraday(TF) ? ds + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) : ds + '（' + WK[d.getUTCDay()] + '）'; }},
});
const band = top => chart.addAreaSeries({lineColor: '#2f5bd3', lineWidth: 1, lineStyle: 2,
  topColor: top ? T.cone : T.bg, bottomColor: top ? T.cone : T.bg,
  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false});
const c90 = band(true), c10 = band(false);
const c50 = chart.addLineSeries({color: '#2f5bd3', lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false});
const vol = chart.addHistogramSeries({priceScaleId: 'vol', priceFormat: {type: 'volume'}, lastValueVisible: false, priceLineVisible: false});
chart.priceScale('vol').applyOptions({scaleMargins: {top: 0.8, bottom: 0}});
const cs = chart.addCandlestickSeries({upColor: UP, downColor: DN, borderUpColor: UP, borderDownColor: DN,
  wickUpColor: UP, wickDownColor: DN, priceLineStyle: 2});
const MA = {};
for (const [k, , col] of IND_DEF.slice(0, 3))
  MA[k] = chart.addLineSeries({color: col, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false});
let stopLine = null, costLine = null;

let bars = [], maVals = {}, axisT = [];
function sma(n) { const out = []; let s = 0;
  bars.forEach((b, i) => { s += b.close; if (i >= n) s -= bars[i - n].close; out.push(i >= n - 1 ? s / n : null); }); return out; }
function render(keepRange) {
  bars = BARS[TF]();
  cs.setData(bars.map(b => b.prov ? {time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    color: 'rgba(0,0,0,0)', borderColor: b.close >= b.open ? UP : DN, wickColor: b.close >= b.open ? UP : DN}
    : {time: b.time, open: b.open, high: b.high, low: b.low, close: b.close}));
  vol.setData(ind.vol ? bars.map(b => ({time: b.time, value: b.vol, color: (b.close >= b.open ? UP : DN) + '99'})) : []);
  maVals = {ma5: sma(5), ma20: sma(20), ma60: sma(60)};
  for (const k in MA) MA[k].setData(ind[k] ? bars.map((b, i) => maVals[k][i] == null ? null : {time: b.time, value: maVals[k][i]}).filter(Boolean) : []);
  const showCone = TF === 'D' && ind.cone && D.cone.p50;
  const cd = q => showCone ? D.cone[q].map(a => ({time: a[0], value: a[1]})) : [];
  c90.setData(cd('p90')); c10.setData(cd('p10')); c50.setData(cd('p50'));
  if (stopLine) { cs.removePriceLine(stopLine); stopLine = null; }
  if (costLine) { cs.removePriceLine(costLine); costLine = null; }
  if (D.stop && ind.stop) stopLine = cs.createPriceLine({price: D.stop, color: '#b7791f', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: D.stopLabel});
  if (D.cost && ind.cost) costLine = cs.createPriceLine({price: D.cost, color: '#7c3aed', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: '我的成本'});
  chart.priceScale('right').applyOptions({scaleMargins: {top: 0.06, bottom: ind.vol ? 0.24 : 0.06}});
  // 時間軸上所有時間點（含預估區間的未來日期）：畫線用時間換算位置
  axisT = bars.map(b => b.time);
  if (showCone) for (const a of D.cone.p50) if (a[0] > axisT[axisT.length - 1]) axisT.push(a[0]);
  if (!keepRange) {
    const N = bars.length, n = TF === 'D' ? D.view : TF === 'W' ? 104 : TF === 'M' ? 60 : TF === '1' ? 270 : 160;
    chart.timeScale().setVisibleLogicalRange({from: Math.max(0, N - n), to: axisT.length + 3});
  }
  document.querySelectorAll('#tfs button').forEach(b => b.classList.toggle('on', b.dataset.tf === TF));
  showLegend(null);
}

// ---------------- 上方資訊列 ----------------
const fmt = v => v == null ? '—' : v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: v < 10 ? 3 : 2});
const fv = v => v >= 1e8 ? (v / 1e8).toFixed(2) + '億' : v >= 1e4 ? (v / 1e4).toFixed(v >= 1e6 ? 0 : 1) + '萬' : Math.round(v).toLocaleString();
function showLegend(time) {
  let i = time == null ? bars.length - 1 : bars.findIndex(b => b.time === time);
  if (i < 0) i = bars.length - 1;
  const b = bars[i]; if (!b) { legend.innerHTML = '<b>' + D.name + '</b>'; return; }
  const p = i > 0 ? bars[i - 1].close : b.open, ch = b.close - p, pct = ch / p * 100, col = ch >= 0 ? UP : DN;
  const maTxt = IND_DEF.slice(0, 3).filter(([k]) => ind[k]).map(([k, n, c]) =>
    `<span class="k" style="color:${c}">${n} <b>${fmt(maVals[k][i])}</b></span>`).join('');
  legend.innerHTML = `<b>${D.name}</b>${b.prov ? ' <span style="color:#b7791f">盤中暫定</span>' : ''}` +
    `<span class="k">開</span> ${fmt(b.open)}<span class="k">高</span> ${fmt(b.high)}<span class="k">低</span> ${fmt(b.low)}` +
    `<span class="k">收</span> <b style="color:${col}">${fmt(b.close)}</b>` +
    `<span style="color:${col};margin-left:8px">${ch >= 0 ? '+' : ''}${fmt(ch)}（${ch >= 0 ? '+' : ''}${pct.toFixed(2)}%）</span>` +
    `<span class="k">量</span> ${b.prov && !b.vol ? '—' : fv(b.vol) + D.unit}` + maTxt;
}
chart.subscribeCrosshairMove(p => showLegend(p && p.time != null ? p.time : null));

// ---------------- 週期切換、指標選單 ----------------
document.querySelectorAll('#tfs button').forEach(b => {
  if (!BARS[b.dataset.tf]().length) { b.disabled = true; b.title = '抓不到這個週期的資料（分線資料來自 Yahoo）'; }
  b.onclick = () => { TF = b.dataset.tf; store.set('ktf', TF); measure = null; render(false); };
});
document.getElementById('src').textContent = D.source;
const menu = document.getElementById('indMenu');
menu.innerHTML = IND_DEF.filter(([k]) => !(k === 'stop' && !D.stop) && !(k === 'cost' && !D.cost))
  .map(([k, n, c]) => `<label><input type="checkbox" data-k="${k}" ${ind[k] ? 'checked' : ''}>` +
       `${c ? `<span style="display:inline-block;width:14px;height:3px;background:${c}"></span>` : ''}${n}</label>`).join('');
document.getElementById('indBtn').onclick = e => { e.stopPropagation(); menu.style.display = menu.style.display === 'block' ? 'none' : 'block'; };
document.addEventListener('click', e => { if (!menu.contains(e.target)) menu.style.display = 'none'; });
menu.querySelectorAll('input').forEach(x => x.onchange = () => { ind[x.dataset.k] = x.checked; store.set('kind', ind); render(true); });

// ---------------- 畫線 ----------------
const ts = chart.timeScale();
function step() { const n = axisT.length; if (n < 2) return 86400;
  const d = []; for (let i = Math.max(1, n - 40); i < n; i++) d.push(axisT[i] - axisT[i - 1]);
  d.sort((a, b) => a - b); return d[Math.floor(d.length / 2)] || 86400; }
function tToL(t) { const A = axisT, n = A.length; if (!n) return null;
  if (t <= A[0]) return (t - A[0]) / step(); if (t >= A[n - 1]) return n - 1 + (t - A[n - 1]) / step();
  let lo = 0, hi = n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (A[m] <= t) lo = m; else hi = m; }
  return lo + (t - A[lo]) / (A[hi] - A[lo]); }
function lToT(l) { const A = axisT, n = A.length; if (!n) return 0;
  if (l <= 0) return A[0] + l * step(); if (l >= n - 1) return A[n - 1] + (l - (n - 1)) * step();
  const i = Math.floor(l); return A[i] + (l - i) * (A[i + 1] - A[i]); }
const X = t => ts.logicalToCoordinate(tToL(t)), Y = p => cs.priceToCoordinate(p);
let magnet = store.get('kmag', false), tool = 'cursor', active = null, measure = null, downXY = null;
function ptFrom(e) {
  const r = ov.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  let l = ts.coordinateToLogical(x), p = cs.coordinateToPrice(y);
  if (magnet && l != null) { const i = Math.round(l), b = bars[i];
    if (b) { l = i; p = [b.open, b.high, b.low, b.close].reduce((a, v) => Math.abs(v - p) < Math.abs(a - p) ? v : a); } }
  return {t: lToT(l), p, x, y};
}
const save = () => store.set(DKEY, drawings);
function setTool(t) { tool = t; active = null;
  document.querySelectorAll('#tools .tool[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
  el.classList.toggle('drawing', t !== 'cursor'); }
document.querySelectorAll('#tools .tool[data-tool]').forEach(b => b.onclick = () => { measure = null; setTool(b.dataset.tool); });
const magBtn = document.getElementById('magnet');
magBtn.classList.toggle('on', magnet);
magBtn.onclick = () => { magnet = !magnet; store.set('kmag', magnet); magBtn.classList.toggle('on', magnet); };
document.getElementById('trash').onclick = () => { drawings = []; measure = null; save(); };
document.getElementById('undo').onclick = () => { drawings.pop(); save(); };
const TWO = ['trend', 'ray', 'rect', 'fib', 'measure', 'channel'];
const txt = document.getElementById('txt');
ov.addEventListener('pointerdown', e => {
  e.preventDefault(); const pt = ptFrom(e);
  if (tool === 'hline' || tool === 'vline') { drawings.push({type: tool, pts: [pt]}); save(); setTool('cursor'); return; }
  if (tool === 'text') { txt.style.left = pt.x + 'px'; txt.style.top = (pt.y - 14) + 'px'; txt.style.display = 'block';
    txt.value = ''; txt.dataset.t = pt.t; txt.dataset.p = pt.p; setTimeout(() => txt.focus(), 0); return; }
  if (!TWO.includes(tool)) return;
  if (active && active.type === 'channel' && active.stage === 2) { active.pts[2] = pt; delete active.stage; drawings.push(active); active = null; save(); setTool('cursor'); return; }
  if (!active) { active = {type: tool, pts: [pt, pt]}; downXY = [pt.x, pt.y]; ov.setPointerCapture(e.pointerId); }
  else finish(pt);
});
ov.addEventListener('pointermove', e => { if (!active) return; const pt = ptFrom(e);
  if (active.stage === 2) active.pts[2] = pt; else active.pts[1] = pt; });
ov.addEventListener('pointerup', e => { if (!active || active.stage === 2) return;
  const pt = ptFrom(e); if (Math.hypot(pt.x - downXY[0], pt.y - downXY[1]) > 4) finish(pt); });   // 沒拖動：等第二次點擊
function finish(pt) {
  active.pts[1] = pt;
  if (active.type === 'channel') { active.stage = 2; active.pts[2] = pt; return; }
  if (active.type === 'measure') { measure = active; active = null; setTool('cursor'); return; }
  drawings.push(active); active = null; save(); setTool('cursor');
}
txt.addEventListener('keydown', e => {
  if (e.key === 'Enter' && txt.value.trim()) { drawings.push({type: 'text', pts: [{t: +txt.dataset.t, p: +txt.dataset.p}], text: txt.value.trim()}); save(); }
  if (e.key === 'Enter' || e.key === 'Escape') { txt.style.display = 'none'; setTool('cursor'); }
});
document.addEventListener('keydown', e => {
  if (e.target === txt) return;
  if (e.key === 'Escape') { active = null; measure = null; setTool('cursor'); }
  if ((e.ctrlKey || e.metaKey) && e.key === 'z') { drawings.pop(); save(); }
});
// 游標模式下，雙擊某條線就刪掉那一條
function segDist(px, py, x1, y1, x2, y2) { const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy;
  const u = L ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / L)) : 0;
  return Math.hypot(px - x1 - u * dx, py - y1 - u * dy); }
el.addEventListener('dblclick', e => {
  if (tool !== 'cursor') return;
  const r = ov.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  let best = -1, bd = 7;
  drawings.forEach((d, i) => { for (const s of shapeSegs(d)) { const v = segDist(px, py, ...s); if (v < bd) { bd = v; best = i; } } });
  if (best >= 0) { drawings.splice(best, 1); save(); }
});
function shapeSegs(d) {
  const W = ov.clientWidth, H = ov.clientHeight, P = d.pts.map(q => [X(q.t), Y(q.p)]);
  if (P.some(([x, y]) => x == null || y == null)) {
    if (d.type === 'hline' && P[0][1] != null) return [[0, P[0][1], W, P[0][1]]];
    if (d.type === 'vline' && P[0][0] != null) return [[P[0][0], 0, P[0][0], H]];
    return [];
  }
  const [a, b, c] = P;
  switch (d.type) {
    case 'trend': return [[...a, ...b]];
    case 'ray': { const k = (b[0] - a[0]) || 1e-9, s = (b[1] - a[1]) / k, ex = b[0] >= a[0] ? W : 0;
      return [[...a, ex, a[1] + s * (ex - a[0])]]; }
    case 'hline': return [[0, a[1], W, a[1]]];
    case 'vline': return [[a[0], 0, a[0], H]];
    case 'rect': return [[a[0], a[1], b[0], a[1]], [b[0], a[1], b[0], b[1]], [b[0], b[1], a[0], b[1]], [a[0], b[1], a[0], a[1]]];
    case 'channel': { const off = channelOff(d); return [[...a, ...b], [a[0], Y(d.pts[0].p + off), b[0], Y(d.pts[1].p + off)]]; }
    case 'fib': return FIB.map(f => { const y = Y(d.pts[1].p + (d.pts[0].p - d.pts[1].p) * f); return [Math.min(a[0], b[0]), y, Math.max(a[0], b[0]), y]; });
    case 'text': return [[a[0], a[1], a[0] + 8 * d.text.length, a[1]]];
  }
  return [];
}
const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const FIBC = ['#787b86', '#f23645', '#ff9800', '#4caf50', '#089981', '#00bcd4', '#787b86'];
function channelOff(d) { if (!d.pts[2]) return 0; const [p0, p1, p2] = d.pts, l0 = tToL(p0.t), l1 = tToL(p1.t), l2 = tToL(p2.t);
  const base = l1 === l0 ? p0.p : p0.p + (p1.p - p0.p) * (l2 - l0) / (l1 - l0); return p2.p - base; }

// ---------------- 繪製（每一畫格重畫，縮放、平移、換週期都會跟著動） ----------------
function frame() {
  const W = el.clientWidth, H = el.clientHeight, dpr = window.devicePixelRatio || 1;
  if (ov.width !== W * dpr || ov.height !== H * dpr) { ov.width = W * dpr; ov.height = H * dpr; ov.style.width = W + 'px'; ov.style.height = H + 'px'; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  const plotW = W - (chart.priceScale('right').width() || 0), plotH = H - (ts.height() || 0);
  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();
  const all = drawings.concat(active ? [active] : []);
  for (const d of all) paint(d, plotW);
  if (measure) paintMeasure(measure);
  ctx.restore();
  requestAnimationFrame(frame);
}
function line(x1, y1, x2, y2, col, w, dash) { ctx.strokeStyle = col; ctx.lineWidth = w || 1.6; ctx.setLineDash(dash || []);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]); }
function dot(x, y) { ctx.fillStyle = T.bg; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 7); ctx.fill(); ctx.stroke(); }
function label(s, x, y, col, bg) { ctx.font = '12px system-ui,"Microsoft JhengHei"'; const w = ctx.measureText(s).width + 8;
  ctx.fillStyle = bg || T.bg; ctx.fillRect(x, y - 9, w, 18); ctx.fillStyle = col; ctx.fillText(s, x + 4, y + 4); }
function paint(d, W) {
  const C = '#2962ff', segs = shapeSegs(d); if (!segs.length) return;
  if (d.type === 'rect') { const [a, b] = [segs[0], segs[1]]; ctx.fillStyle = 'rgba(41,98,255,.10)';
    ctx.fillRect(Math.min(a[0], a[2]), Math.min(a[1], b[3]), Math.abs(a[2] - a[0]), Math.abs(b[3] - a[1])); }
  if (d.type === 'channel' && segs.length === 2) { const [s1, s2] = segs; ctx.fillStyle = 'rgba(41,98,255,.08)';
    ctx.beginPath(); ctx.moveTo(s1[0], s1[1]); ctx.lineTo(s1[2], s1[3]); ctx.lineTo(s2[2], s2[3]); ctx.lineTo(s2[0], s2[1]); ctx.fill(); }
  if (d.type === 'fib') { segs.forEach((s, i) => { line(...s, FIBC[i], 1.2);
      label(`${FIB[i]}（${fmt(d.pts[1].p + (d.pts[0].p - d.pts[1].p) * FIB[i])}）`, s[0] + 2, s[1] - 9, FIBC[i], 'rgba(0,0,0,0)'); });
    line(X(d.pts[0].t), Y(d.pts[0].p), X(d.pts[1].t), Y(d.pts[1].p), '#787b86', 1, [4, 4]); return; }
  if (d.type === 'text') { const [x, y] = [X(d.pts[0].t), Y(d.pts[0].p)];
    ctx.font = '13px system-ui,"Microsoft JhengHei"'; ctx.fillStyle = C; ctx.fillText(d.text, x, y); return; }
  for (const s of segs) line(...s, C);
  if (d.type === 'hline') label(fmt(d.pts[0].p), 4, segs[0][1], C);
  if (d.type === 'trend' || d.type === 'ray' || d.type === 'channel') d.pts.slice(0, 2).forEach(q => dot(X(q.t), Y(q.p)));
}
function paintMeasure(m) {
  const [a, b] = m.pts, x1 = X(a.t), y1 = Y(a.p), x2 = X(b.t), y2 = Y(b.p); if ([x1, y1, x2, y2].includes(null)) return;
  const ch = b.p - a.p, pct = ch / a.p * 100, n = Math.round(tToL(b.t) - tToL(a.t)), col = ch >= 0 ? UP : DN;
  ctx.fillStyle = col + '22'; ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
  line(x1, y1, x2, y2, col, 1.2, [4, 3]);
  const s = `${ch >= 0 ? '+' : ''}${fmt(ch)}（${ch >= 0 ? '+' : ''}${pct.toFixed(2)}%）　${Math.abs(n)} 根`;
  ctx.font = 'bold 12px system-ui,"Microsoft JhengHei"'; const w = ctx.measureText(s).width + 12;
  const bx = (x1 + x2) / 2 - w / 2, by = Math.max(y1, y2) + 8;
  ctx.fillStyle = col; ctx.fillRect(bx, by, w, 20); ctx.fillStyle = '#fff'; ctx.fillText(s, bx + 6, by + 14);
}

setTool('cursor');
render(false);
requestAnimationFrame(frame);
</script>
""".replace("__TOOLBAR__", _TOOLBAR)
