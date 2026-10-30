"""個股的盤前／盤中／盤後整理。

盤前：前一交易日收盤與 K 棒、隔夜美股或期貨、預估開盤、今日關鍵價位、收盤後的新消息
盤中：現價、開盤跳空（實際 vs 預估）、今日高低與位置、量能（依時間推算）、突破或跌破關鍵價、5 分 K 走勢
盤後：K 棒型態、收在今日區間的位置、量能、相對大盤、法人與融資（台股）、明日機率與開盤預估
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import live

PHASE_NAME = {"pre": "盤前", "live": "盤中", "post": "盤後"}


# ----------------------------------------------------------------------------
# 時段
# ----------------------------------------------------------------------------
def phase(sess: str, now: dt.datetime | None = None) -> str:
    now = now or live.now(sess)
    if now.weekday() >= 5:
        return "post"
    t = now.time()
    if sess == "TW":
        return "pre" if t < dt.time(9, 0) else "live" if t <= dt.time(13, 30) else "post"
    # 美股（美東時間）：04:00～09:30 盤前，09:30～16:00 盤中，其他時間算盤後
    if dt.time(4, 0) <= t < dt.time(9, 30):
        return "pre"
    return "live" if dt.time(9, 30) <= t <= dt.time(16, 0) else "post"


def session_bounds(sess: str):
    return (dt.time(9, 0), dt.time(13, 30)) if sess == "TW" else (dt.time(9, 30), dt.time(16, 0))


def elapsed_frac(sess: str, now: dt.datetime | None = None) -> float:
    now = now or live.now(sess)
    a, b = session_bounds(sess)
    total = (dt.datetime.combine(now.date(), b) - dt.datetime.combine(now.date(), a)).seconds
    done = (dt.datetime.combine(now.date(), now.time()) - dt.datetime.combine(now.date(), a)).total_seconds()
    return float(min(1.0, max(0.05, done / total)))


# ----------------------------------------------------------------------------
# 盤中 5 分 K（Yahoo）
# ----------------------------------------------------------------------------
def intraday(code: str, market: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame()
    tickers = [code + ".TW", code + ".TWO"] if market == "TW" else [code]
    for t in tickers:
        try:
            # 抓 5 天：收盤後到隔天開盤前，仍要顯示最近一個交易日的走勢
            d = yf.Ticker(t).history(period="5d", interval="5m", prepost=(market == "US"))
        except Exception:
            continue
        if d is not None and not d.empty:
            d = d.reset_index().rename(columns={"Datetime": "time", "Date": "time"})
            return d[["time", "Open", "High", "Low", "Close", "Volume"]]
    return pd.DataFrame()


def pick_chart_day(intra: pd.DataFrame | None, sess: str, ph: str, now: dt.datetime, df: pd.DataFrame) -> dict:
    """決定「今日走勢」圖要畫哪一天。

    盤中：畫今天（就算 Yahoo 還沒資料，也先畫出完整時段的空白座標軸）
    收盤後、週末、隔天開盤前：繼續畫最近一個交易日，等下一次開盤才換成新的一天
    圖只畫正規交易時段，x 軸固定為整個時段（台股 09:00～13:30、美股美東 09:30～16:00）
    """
    a, b = session_bounds(sess)
    out = {"rows": None, "day": None, "prev": None, "start": None, "end": None, "pre_rows": None, "waiting": False}
    rows = None
    if intra is not None and len(intra):
        try:
            t = pd.to_datetime(intra["time"])
            t = t.dt.tz_convert(live.TZ[sess]) if t.dt.tz is not None else t
            rows = intra.assign(time=t.dt.tz_localize(None) if t.dt.tz is not None else t)
        except Exception:
            rows = None
    today = now.date()
    if rows is not None and len(rows):
        tt = rows["time"].dt.time
        # 美股今天開盤前的盤前成交
        pre = rows[(rows["time"].dt.date == today) & (tt < a)]
        out["pre_rows"] = pre if len(pre) else None
        rows = rows[(tt >= a) & (tt <= b)]
    if ph == "live" and now.weekday() < 5:
        day = today
    elif rows is not None and len(rows):
        day = rows["time"].dt.date.max()
    else:
        day = None
    if day is None:
        return out
    day_rows = rows[rows["time"].dt.date == day] if rows is not None and len(rows) else rows
    out["day"] = day
    out["start"] = dt.datetime.combine(day, a)
    out["end"] = dt.datetime.combine(day, b)
    out["rows"] = day_rows if day_rows is not None and len(day_rows) else None
    out["waiting"] = out["rows"] is None
    before = df[df["date"].dt.date < day]
    if len(before):
        out["prev"] = float(before["close"].iloc[-1])
    return out


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def kbar(o, h, l, c, prev) -> str:
    """K 棒型態，用一般人看得懂的說法。"""
    if not all(pd.notna([o, h, l, c])) or h <= l:
        return "資料不足"
    rng, body = h - l, c - o
    up_sh, lo_sh = h - max(o, c), min(o, c) - l
    big = prev and abs(body) / prev >= 0.025
    if abs(body) <= rng * 0.1:
        if up_sh > rng * 0.35 and lo_sh > rng * 0.35:
            return "十字線（多空拉鋸，方向未明）"
        if lo_sh > rng * 0.6:
            return "T 字線／蜻蜓線（低檔有買盤撐住）"
        if up_sh > rng * 0.6:
            return "倒 T 字線（高檔賣壓重）"
        return "十字線（多空拉鋸）"
    color = "紅" if body > 0 else "黑"
    if up_sh >= 2 * abs(body) and up_sh > rng * 0.45:
        return f"長上影線{color} K（衝高被賣回，上方有壓力）"
    if lo_sh >= 2 * abs(body) and lo_sh > rng * 0.45:
        return f"長下影線{color} K（殺低有人接，下方有支撐）"
    if big:
        return "長紅 K（買盤強勢）" if body > 0 else "長黑 K（賣壓強勢）"
    return "小紅 K" if body > 0 else "小黑 K"


def pos_in_range(c, h, l) -> str:
    if not (pd.notna(h) and pd.notna(l)) or h <= l:
        return ""
    p = (c - l) / (h - l)
    return ("收在最高附近" if p >= 0.85 else "收在高檔" if p >= 0.6 else "收在中間" if p >= 0.4
            else "收在低檔" if p >= 0.15 else "收在最低附近") + f"（區間位置 {p * 100:.0f}%）"


def streak(s: pd.Series) -> tuple[int, int]:
    """連續買超（+）或賣超（−）天數。"""
    s = s.dropna()
    if s.empty or s.iloc[-1] == 0:
        return 0, 0
    sign = np.sign(s.iloc[-1])
    n = 0
    for v in s.iloc[::-1]:
        if np.sign(v) != sign:
            break
        n += 1
    return int(sign), n


def f2(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:,.2f}"


def pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"


# ----------------------------------------------------------------------------
# 主函式
# ----------------------------------------------------------------------------
def build(code: str, market: str, sess: str, r: dict, q: dict | None, P: dict | None, items: list,
          ov: dict | None, idx_q: dict | None, stop: float, stop_label: str, intra: pd.DataFrame | None) -> dict:
    df = r["df"]
    now = live.now(sess)
    ph = phase(sess, now)
    chart = pick_chart_day(intra, sess, ph, now, df)
    intra = chart["pre_rows"]           # 美股今天的盤前成交（只用在盤前文字）
    today = now.date()
    vol_unit = "張" if market == "TW" else "股"

    # 今日 K 棒：優先用即時報價，其次用日線最後一筆
    q_date = None
    if q and q.get("date"):
        try:
            q_date = dt.datetime.strptime(str(q["date"]), "%Y%m%d").date()
        except ValueError:
            q_date = None
    today_bar = None
    if q and q_date == today and q.get("open") and q.get("high") and q.get("low"):
        today_bar = {"date": today, "o": q["open"], "h": q["high"], "l": q["low"], "c": q["price"],
                     "v": q.get("vol"), "prev": q.get("prev") or float(df["close"].iloc[-1])}
    elif df["date"].iloc[-1].date() == today:
        L = df.iloc[-1]
        today_bar = {"date": today, "o": L["open"], "h": L["high"], "l": L["low"], "c": L["close"],
                     "v": L["vol"], "prev": float(df["close"].iloc[-2])}
    # 最近一個「已完成」的交易日
    hist = df[df["date"].dt.date < today] if today_bar else df
    H = hist.iloc[-1]
    done = {"date": H["date"].date(), "o": H["open"], "h": H["high"], "l": H["low"], "c": H["close"],
            "v": H["vol"], "prev": float(hist["close"].iloc[-2])}
    if ph == "post" and today_bar:
        done = today_bar
    vol5 = float(hist["vol"].tail(5).mean()) if len(hist) >= 5 else np.nan
    ma20 = float(df["ma20"].iloc[-1]) if pd.notna(df["ma20"].iloc[-1]) else np.nan
    hi20 = float(hist["high"].tail(20).max())

    out = {"phase": ph, "pre": {}, "live": {}, "post": {}}

    # ======================= 盤前 =======================
    b = done if ph != "post" else done
    lines = []
    ch = b["c"] / b["prev"] - 1
    day_label = "今日" if b["date"] == today else "前一交易日"
    lines.append(f"{day_label}（{b['date']:%m/%d}）收 {f2(b['c'])}，{pct(ch)}，{kbar(b['o'], b['h'], b['l'], b['c'], b['prev'])}。")
    if ov:
        if market == "TW" or code in ("^TWII", "^TWOII"):
            if ov.get("us_last") and ov["us_last"] >= b["date"]:
                lines.append(f"隔夜美股：那斯達克 {pct(ov.get('ix'))}、費城半導體 {pct(ov.get('sox'))}"
                             + (f"、台積電 ADR {pct(ov.get('tsm'))}" if code == "2330" else "") + "。")
            else:
                lines.append(f"美股還沒收盤，那斯達克期貨目前 {pct(ov.get('nq'))}。")
        else:
            lines.append(f"那斯達克期貨目前 {pct(ov.get('nq'))}。")
    if market == "US" and intra is not None and len(intra) and ph == "pre":
        pre_px = float(intra["Close"].iloc[-1])
        if abs(pre_px / b["c"] - 1) < 0.5:          # 排除明顯錯誤的資料
            lines.append(f"盤前交易價 {f2(pre_px)}，相對昨收 {pct(pre_px / b['c'] - 1)}。")
    gap = None
    if ph == "live":
        if today_bar:
            lines.append(f"今天實際開盤 {f2(today_bar['o'])}（跳空 {pct(today_bar['o'] / today_bar['prev'] - 1)}）。")
    elif P and P["next"].get("gap_mu") is not None:
        gap = P["next"]["gap_mu"]
        lines.append(f"預估開盤 {pct(gap)}，約 {f2(b['c'] * (1 + gap))}（常見範圍 {pct(gap - P['next']['gap_sd'])} ～ "
                     f"{pct(gap + P['next']['gap_sd'])}）。")
    if P and ph != "live":
        lines.append(f"下一個交易日上漲機率 {P['next']['p_up'] * 100:.0f}%。")
    close_t = pd.Timestamp.combine(b["date"], dt.time(13, 30) if sess == "TW" else dt.time(16, 0))
    fresh = []
    for it in items or []:
        try:
            t = it["time"].astimezone(live.TZ[sess]).replace(tzinfo=None)
        except Exception:
            continue
        if t >= close_t:
            fresh.append(it)
    if fresh:
        pos = sum(1 for i in fresh if i["tone"] == "pos")
        neg = sum(1 for i in fresh if i["tone"] == "neg")
        lines.append(f"收盤後有 {len(fresh)} 則新消息（{pos} 則偏多、{neg} 則偏空），重點：" +
                     "；".join(i["title"][:28] for i in fresh[:3]) + "。")
    else:
        lines.append("收盤後沒有新的相關消息。")
    levels = [("壓力", f"昨高 {f2(b['h'])}、近 20 日高 {f2(hi20)}"),
              ("支撐", f"昨低 {f2(b['l'])}、月線 {f2(ma20)}"),
              (stop_label, f2(stop))]
    pre_title = {"pre": "今天盤前重點", "live": "今天盤前重點（回顧）", "post": "下一個交易日盤前重點"}[ph]
    out["pre"] = {"title": pre_title, "lines": lines, "levels": levels}

    # ======================= 盤中 =======================
    lines, metrics = [], []
    T = today_bar if today_bar else None
    if T:
        chg = T["c"] / T["prev"] - 1
        g_act = T["o"] / T["prev"] - 1
        lim = live.limit_state(T["c"], T["prev"], market, q)
        metrics = [("現價" if ph == "live" else "收盤", f2(T["c"]), pct(chg)),
                   ("開盤", f2(T["o"]), f"跳空 {pct(g_act)}"),
                   ("最高／最低", f"{f2(T['h'])}／{f2(T['l'])}", f"振幅 {pct((T['h'] - T['l']) / T['prev'])}")]
        lines.append(f"開盤 {f2(T['o'])}（跳空 {pct(g_act)}）。")
        lines.append(f"目前{pos_in_range(T['c'], T['h'], T['l']).replace('收在', '位在')}。"
                     if ph == "live" else f"{pos_in_range(T['c'], T['h'], T['l'])}。")
        if T.get("v") and pd.notna(vol5) and vol5 > 0:
            frac = elapsed_frac(sess, now) if ph == "live" else 1.0
            ratio = T["v"] / (vol5 * frac)
            lines.append(f"成交量 {T['v']:,.0f} {vol_unit}，{'依目前時間推算，' if ph == 'live' else ''}約為 5 日均量的 "
                         f"{ratio:.1f} 倍（{'爆量' if ratio >= 2 else '量增' if ratio >= 1.2 else '量縮' if ratio < 0.7 else '量平'}）。")
        brk = []
        if T["h"] > done["h"] and ph != "post":
            brk.append(f"盤中突破昨高 {f2(done['h'])}")
        if T["l"] < done["l"] and ph != "post":
            brk.append(f"盤中跌破昨低 {f2(done['l'])}")
        if pd.notna(ma20):
            brk.append(f"{'站在' if T['c'] >= ma20 else '低於'}月線 {f2(ma20)}")
        if T["c"] < stop:
            brk.append(f"⚠ 低於{stop_label} {f2(stop)}")
        if lim:
            brk.append("🔴 漲停" if lim == "up" else "🟢 跌停")
        if brk:
            lines.append("、".join(brk) + "。")
    elif ph == "pre":
        lines.append("今天還沒開盤，開盤後這裡會即時顯示現價、跳空、量能與突破／跌破。")
    else:
        lines.append("今天沒有交易資料（可能是休市）。")
    out["live"] = {"title": "盤中動態" if ph == "live" else "今日盤中回顧" if ph == "post" else "盤中動態",
                   "lines": lines, "metrics": metrics, "chart": chart}

    # ======================= 盤後 =======================
    lines, metrics = [], []
    D = today_bar if (ph == "post" and today_bar) else done
    label = "今日" if (ph == "post" and today_bar) else f"前一交易日（{D['date']:%m/%d}）"
    chg = D["c"] / D["prev"] - 1
    metrics.append(("收盤", f2(D["c"]), pct(chg)))
    lines.append(f"{label}{kbar(D['o'], D['h'], D['l'], D['c'], D['prev'])}，{pos_in_range(D['c'], D['h'], D['l'])}。")
    if D.get("v") and pd.notna(vol5) and vol5 > 0:
        ratio = D["v"] / vol5
        metrics.append(("成交量", f"{D['v']:,.0f} {vol_unit}", f"5 日均量的 {ratio:.1f} 倍"))
        if chg > 0 and ratio >= 1.3:
            lines.append("價漲量增，買盤積極。")
        elif chg > 0 and ratio < 0.8:
            lines.append("價漲量縮，追價力道偏弱。")
        elif chg < 0 and ratio >= 1.3:
            lines.append("價跌量增，賣壓較重，留意是否有人出貨。")
        elif chg < 0 and ratio < 0.8:
            lines.append("價跌量縮，賣壓不算重。")
    if idx_q and idx_q.get("chg") is not None:
        rel = chg - idx_q["chg"]
        lines.append(f"大盤 {pct(idx_q['chg'])}，這檔{'強於' if rel > 0 else '弱於'}大盤 {abs(rel) * 100:.2f} 個百分點。")
    if market == "TW":
        row = df[df["date"].dt.date == D["date"]]
        if len(row) and (row[["foreign", "trust", "dealer"]].abs().sum(axis=1).iloc[0] > 0):
            R = row.iloc[0]
            metrics.append(("三大法人", f"{R['foreign'] + R['trust'] + R['dealer']:+,.0f} 張",
                            f"外資 {R['foreign']:+,.0f}｜投信 {R['trust']:+,.0f}｜自營 {R['dealer']:+,.0f}"))
            sg, n = streak(df.loc[df["date"] <= R["date"], "foreign"])
            if n >= 2:
                lines.append(f"外資連續 {n} 天{'買超' if sg > 0 else '賣超'}。")
            sg, n = streak(df.loc[df["date"] <= R["date"], "trust"])
            if n >= 2:
                lines.append(f"投信連續 {n} 天{'買超' if sg > 0 else '賣超'}。")
            idx = row.index[0]
            if idx > 0 and pd.notna(df.loc[idx, "margin"]) and pd.notna(df.loc[idx - 1, "margin"]):
                dm = df.loc[idx, "margin"] - df.loc[idx - 1, "margin"]
                lines.append(f"融資{'增加' if dm >= 0 else '減少'} {abs(dm):,.0f} 張"
                             + ("，散戶進場追價。" if dm > 0 and chg > 0 else "，散戶逢低承接。" if dm > 0 else "。"))
        elif ph == "post":
            lines.append("今日法人與融資資料尚未公布（證交所約 15:00～16:30 公布），稍後重新整理就會出現。")
    if P:
        lines.append(f"下一個交易日上漲機率 {P['next']['p_up'] * 100:.0f}%，未來 10 個交易日上漲機率 {P['h10']['p_up'] * 100:.0f}%。")
        if P["next"].get("gap_mu") is not None:
            lines.append(f"依{P['next']['gap_src']}推估，明天開盤約 {pct(P['next']['gap_mu'])}。")
    out["post"] = {"title": f"{label}盤後整理", "lines": lines, "metrics": metrics}
    return out


def as_text(code: str, name: str, b: dict) -> str:
    """給 Claude 寫成段落用的純文字。"""
    parts = []
    for k in ("pre", "live", "post"):
        s = b[k]
        parts.append(f"【{PHASE_NAME[k]}】" + " ".join(s.get("lines", [])))
    return f"{code} {name}\n" + "\n".join(parts)
