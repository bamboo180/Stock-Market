"""互動 K 線圖：TradingView Lightweight Charts（拖曳平移、滾輪／雙指縮放、價格軸自動縮放）。"""
from __future__ import annotations

import json

import pandas as pd

LIB = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"


def _d(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def build(df: pd.DataFrame, cone: pd.DataFrame, up: str, down: str, stop: float | None, stop_label: str = "停損參考",
          today: dict | None = None, dark: bool = False, cost: float | None = None, height: int = 560, vol_unit: str = "張") -> str:
    candles = [{"time": _d(r.date), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
               for r in df.itertuples()]
    vols = [{"time": _d(r.date), "value": float(r.vol),
             "color": (up if r.close >= r.open else down) + "66"} for r in df.itertuples()]
    if today:   # 今日盤中暫定：空心 K 棒
        candles.append({"time": today["date"], "open": today["open"], "high": today["high"], "low": today["low"],
                        "close": today["close"], "color": "rgba(0,0,0,0)",
                        "borderColor": up if today["close"] >= today["open"] else down,
                        "wickColor": up if today["close"] >= today["open"] else down, "provisional": True})
    ma = {k: [{"time": _d(d), "value": round(float(v), 3)} for d, v in zip(df["date"], df[k]) if pd.notna(v)]
          for k in ("ma20", "ma60")}
    last = {"time": _d(df["date"].iloc[-1]), "value": float(df["close"].iloc[-1])}
    cone_d = {q: [last] + [{"time": _d(d), "value": round(float(v), 3)} for d, v in zip(cone["date"], cone[q])]
              for q in ("p10", "p50", "p90")}

    th = ({"bg": "#0e1117", "fg": "#c9ccd3", "grid": "#262a33", "cone": "rgba(125,155,255,.18)", "tip": "#1c1f26"}
          if dark else
          {"bg": "#ffffff", "fg": "#4a4d55", "grid": "#eef0f3", "cone": "rgba(47,91,211,.12)", "tip": "#ffffff"})
    data = json.dumps({"candles": candles, "vols": vols, "ma": ma, "cone": cone_d, "stop": stop, "stopLabel": stop_label, "cost": cost,
                       "up": up, "down": down, "th": th, "unit": vol_unit})

    return f"""
<div id="wrap" style="font-family:system-ui,-apple-system,'Microsoft JhengHei',sans-serif;color:{th['fg']}">
  <div id="bar" style="display:flex;gap:6px;flex-wrap:wrap;margin:0 0 6px">
    <button data-n="22">1月</button><button data-n="66">3月</button><button data-n="132">6月</button>
    <button data-n="250">1年</button><button data-n="0">全部</button>
    <span style="flex:1"></span>
    <span style="font-size:12px;opacity:.7;align-self:center">拖曳平移・滾輪／雙指縮放・雙擊價格軸復原</span>
  </div>
  <div id="c" style="position:relative;height:{height}px">
    <div id="tip" style="position:absolute;left:8px;top:6px;z-index:5;font-size:12.5px;line-height:1.55;
         background:{th['tip']}e6;padding:4px 8px;border-radius:6px;pointer-events:none;font-variant-numeric:tabular-nums"></div>
  </div>
</div>
<style>
 body{{margin:0;background:{th['bg']}}}
 #bar button{{font:inherit;font-size:12.5px;padding:3px 10px;border-radius:6px;cursor:pointer;
   border:1px solid {th['grid']};background:{th['bg']};color:{th['fg']}}}
 #bar button.on{{border-color:#2f5bd3;color:#2f5bd3;font-weight:600}}
</style>
<script src="{LIB}"></script>
<script>
const D = {data};
const el = document.getElementById('c'), tip = document.getElementById('tip');
const chart = LightweightCharts.createChart(el, {{
  autoSize: true,
  layout: {{background: {{type: 'solid', color: D.th.bg}}, textColor: D.th.fg, fontSize: 12}},
  grid: {{vertLines: {{visible: false}}, horzLines: {{color: D.th.grid}}}},
  rightPriceScale: {{borderColor: D.th.grid, scaleMargins: {{top: 0.08, bottom: 0.25}}}},
  timeScale: {{borderColor: D.th.grid, rightOffset: 4, barSpacing: 7, minBarSpacing: 1.5}},
  crosshair: {{mode: LightweightCharts.CrosshairMode.Normal}},
  handleScroll: {{mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false}},
  handleScale: {{mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true}},
  localization: {{dateFormat: 'yyyy/MM/dd', priceFormatter: p => p.toLocaleString('en-US', {{maximumFractionDigits: 2}})}},
}});
// 預估扇形：上緣填色、下緣用底色蓋掉，形成帶狀
const band = (q, top) => {{
  const s = chart.addAreaSeries({{lineColor: '#2f5bd3', lineWidth: 1, lineStyle: 2,
    topColor: top ? D.th.cone : D.th.bg, bottomColor: top ? D.th.cone : D.th.bg,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false}});
  s.setData(D.cone[q]); return s;
}};
band('p90', true); band('p10', false);
const mid = chart.addLineSeries({{color: '#2f5bd3', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false}});
mid.setData(D.cone.p50);

const vol = chart.addHistogramSeries({{priceScaleId: 'vol', priceFormat: {{type: 'volume'}}, lastValueVisible: false, priceLineVisible: false}});
chart.priceScale('vol').applyOptions({{scaleMargins: {{top: 0.8, bottom: 0}}}});
vol.setData(D.vols);

const cs = chart.addCandlestickSeries({{upColor: D.up, downColor: D.down, borderUpColor: D.up, borderDownColor: D.down,
  wickUpColor: D.up, wickDownColor: D.down}});
cs.setData(D.candles.map(({{provisional, ...c}}) => c));
const line = (data, color) => {{ const s = chart.addLineSeries({{color, lineWidth: 1.5, priceLineVisible: false,
  lastValueVisible: false, crosshairMarkerVisible: false}}); s.setData(data); return s; }};
const m20 = line(D.ma.ma20, '#b7791f'), m60 = line(D.ma.ma60, '#8a8d95');
if (D.stop) cs.createPriceLine({{price: D.stop, color: '#b7791f', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: D.stopLabel}});
if (D.cost) cs.createPriceLine({{price: D.cost, color: '#7c3aed', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: '我的成本'}});

// 資訊列
const byTime = new Map(D.candles.map((c, i) => [c.time, i]));
const coneMap = new Map(D.cone.p50.map((c, i) => [c.time, i]));
const f = v => v == null ? '—' : v.toLocaleString('en-US', {{maximumFractionDigits: 2}});
const fv = v => v >= 1e8 ? (v / 1e8).toFixed(2) + '億' : v >= 1e4 ? (v / 1e4).toFixed(1) + '萬' : f(v);
function show(time) {{
  const i = byTime.get(time);
  if (i === undefined) {{
    const j = coneMap.get(time);
    if (j === undefined) return;
    tip.innerHTML = `<b>${{time}}</b>　<span style="color:#2f5bd3">預估</span>　P90 ${{f(D.cone.p90[j].value)}}　中位 ${{f(D.cone.p50[j].value)}}　P10 ${{f(D.cone.p10[j].value)}}`;
    return;
  }}
  const c = D.candles[i], p = i > 0 ? D.candles[i - 1].close : c.open, ch = (c.close / p - 1) * 100;
  const col = ch >= 0 ? D.up : D.down, v = D.vols[i];
  const ma = (arr) => {{ const x = arr.find(a => a.time === time) || (c.provisional ? arr[arr.length - 1] : null);
    return x ? f(x.value) : '—'; }};
  tip.innerHTML = `<b>${{time}}</b>${{c.provisional ? ' <span style="color:#b7791f">盤中暫定</span>' : ''}}　` +
    `開 ${{f(c.open)}}　高 ${{f(c.high)}}　低 ${{f(c.low)}}　收 <b style="color:${{col}}">${{f(c.close)}}</b>　` +
    `<span style="color:${{col}}">${{ch >= 0 ? '+' : ''}}${{ch.toFixed(2)}}%</span>` +
    (v ? `　量 ${{fv(v.value)}}${{D.unit}}` : '') +
    `<br><span style="color:#b7791f">MA20 ${{ma(D.ma.ma20)}}</span>　<span style="color:#8a8d95">MA60 ${{ma(D.ma.ma60)}}</span>`;
}}
const lastT = D.candles[D.candles.length - 1].time;
show(lastT);
chart.subscribeCrosshairMove(p => show(p && p.time ? p.time : lastT));

// 區間按鈕
const btns = [...document.querySelectorAll('#bar button')];
function setRange(n, b) {{
  const N = D.candles.length, extra = D.cone.p50.length;
  if (!n) chart.timeScale().setVisibleLogicalRange({{from: 0, to: N + extra}});
  else chart.timeScale().setVisibleLogicalRange({{from: Math.max(0, N - n), to: N + extra - 1}});
  btns.forEach(x => x.classList.toggle('on', x === b));
}}
btns.forEach(b => b.onclick = () => setRange(+b.dataset.n, b));
setRange(132, btns[2]);
</script>
"""
