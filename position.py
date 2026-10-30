"""持倉分析：輸入買進價，算出損益、建議停損與操作建議。

建議停損的邏輯（以風險控管為主，不預測漲跌）：
- 虧損中：取「成本 × (1 − 可承受虧損%)」與「近 20 日低點 − 0.5×ATR」中較高（較緊）的一個
- 獲利中：移動停損 =「買進後最高收盤 − 2.5×ATR」，獲利超過 2×ATR 後至少拉到成本價（保本）
ATR = 近 14 日平均真實波幅，代表這檔股票「正常一天會晃多少」。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from engine import COSTS


def atr(df: pd.DataFrame, n: int = 14) -> float:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def analyze(df: pd.DataFrame, r: dict, price: float, cost: float, shares: float = 0,
            buy_date: dt.date | None = None, risk_pct: float = 0.08) -> dict:
    market = r["market"]
    fee = COSTS[market]
    a = atr(df)
    last = df.iloc[-1]
    ma20, ma60 = last["ma20"], last["ma60"]

    pnl = price / cost - 1
    net_pnl = price * (1 - fee["sell"]) / (cost * (1 + fee["buy"])) - 1        # 扣手續費、稅
    breakeven = cost * (1 + fee["buy"]) / (1 - fee["sell"])

    since = df[df["date"] >= pd.Timestamp(buy_date)] if buy_date else df.tail(0)
    peak = max([price] + list(since["close"]))
    held = (dt.date.today() - buy_date).days if buy_date else None

    hard = cost * (1 - risk_pct)
    swing = float(df["low"].tail(20).min()) - 0.5 * a
    if price >= cost:
        trail = peak - 2.5 * a
        stop = max(trail, breakeven) if price - cost > 2 * a else max(trail, hard)
        stop_why = ("移動停損：買進後最高收盤 − 2.5×ATR，已獲利超過 2×ATR，所以至少拉到損益兩平"
                    if price - cost > 2 * a else "移動停損：買進後最高收盤 − 2.5×ATR（不低於固定停損）")
    else:
        stop = max(hard, swing) if swing < price else hard
        stop_why = ("近 20 日低點下方 0.5×ATR（技術支撐），比固定停損更緊" if stop == swing and swing > hard
                    else f"固定停損：成本 −{risk_pct * 100:.0f}%")
    triggered = price <= stop

    up_to = price * (1 + r["d10"]["p90"])
    reward, risk = up_to - price, max(price - stop, 1e-9)
    rr = None if triggered else reward / risk
    score, n = r["score"], r["n_parts"]
    p10 = r.get("p10")
    if p10 is not None:            # 有上漲機率時，用機率取代技術評分
        score = 1 if p10 >= 0.55 else -1 if p10 <= 0.45 else 0
    trend = f"10 日上漲機率 {p10 * 100:.0f}%" if p10 is not None else f"評分 {r['score']:+d}"
    below20 = pd.notna(ma20) and price < ma20

    # ---------- 建議 ----------
    if triggered and pnl >= 0:
        level, head = "danger", "已跌破移動停損，獲利回吐中"
        body = (f"現價 {price:,.2f} 已低於移動停損 {stop:,.2f}（從買進後高點 {peak:,.2f} 回落 "
                f"{(1 - price / peak) * 100:.1f}%）。目前仍獲利 {pnl * 100:.1f}%，照紀律應獲利了結，"
                "或至少先賣一半，保住剩下的獲利。")
    elif triggered:
        level, head = "danger", "已觸及停損條件"
        body = (f"現價 {price:,.2f} 已在建議停損 {stop:,.2f} 之下（虧損 {pnl * 100:.1f}%）。照紀律應該出場，"
                "或至少先減碼一半，不要因為「等反彈」而讓虧損擴大。")
    elif pnl < 0 and (score <= -1 or (below20 and score <= 0)):
        level, head = "warn", "虧損中，趨勢偏弱"
        body = (f"目前虧損 {pnl * 100:.1f}%，{trend}{'，股價在月線之下' if below20 else ''}。"
                f"嚴守停損 {stop:,.2f}；若反彈到月線 {ma20:,.2f} 附近仍站不上，可考慮減碼。不建議攤平。")
    elif pnl < 0:
        level, head = "info", "虧損中，趨勢尚可"
        body = (f"目前虧損 {pnl * 100:.1f}%，但{trend}、趨勢沒有轉壞，可以續抱觀察，"
                f"停損設在 {stop:,.2f}，跌破就執行。")
    elif score >= 1 and not below20:
        level, head = "good", "獲利中，趨勢偏多"
        body = (f"目前獲利 {pnl * 100:.1f}%，趨勢仍偏多，可以續抱讓獲利跑。"
                f"移動停損上移到 {stop:,.2f}，跌破再出場。")
    else:
        level, head = "warn", "獲利中，但趨勢轉弱"
        body = (f"目前獲利 {pnl * 100:.1f}%，但{trend}{'、已跌破月線' if below20 else ''}。"
                f"可以考慮分批獲利了結（例如先賣一半），剩下的用移動停損 {stop:,.2f} 保護。")

    extra = []
    if rr is not None and rr < 1:
        extra.append(f"風險報酬比 {rr:.1f}（往下到停損 {risk / price * 100:.1f}%，往上到樂觀預估 "
                     f"{reward / price * 100:.1f}%），不適合在這裡加碼。")
    elif rr is not None and rr >= 2 and level in ("good", "info"):
        extra.append(f"風險報酬比 {rr:.1f}，往上空間相對往下風險較大。")
    if a / price > 0.04:
        extra.append(f"這檔波動大（ATR 約 {a / price * 100:.1f}%/日），停損不要設太近，否則容易被洗出場。")
    if shares:
        exit_px = price if triggered else stop
        at_stop = (exit_px * (1 - fee["sell"]) - cost * (1 + fee["buy"])) * shares
        extra.append(f"{'現在以現價出場' if triggered else '若跌到停損出場'}，這筆部位"
                     f"{'約虧' if at_stop < 0 else '約賺'} {abs(at_stop):,.0f} 元（含手續費與稅）。")

    return {
        "price": price, "cost": cost, "shares": shares, "pnl": pnl, "net_pnl": net_pnl,
        "pnl_amt": (price * (1 - fee["sell"]) - cost * (1 + fee["buy"])) * shares if shares else None,
        "breakeven": breakeven, "atr": a, "peak": peak, "held": held,
        "stop": stop, "stop_why": stop_why, "hard": hard, "swing": swing, "triggered": triggered,
        "rr": rr, "level": level, "head": head, "body": body, "extra": extra,
        "ma20": ma20, "ma60": ma60,
    }


def prompt(code: str, name: str, r: dict, p: dict, news_titles: list[str]) -> str:
    """給 Claude 的分析提示。"""
    parts = "、".join(f"{k}{'是' if v > 0 else '否'}" for k, v in r["parts"].items())
    lines = "\n".join(f"- {t}" for t in news_titles[:12]) or "（無）"
    return f"""你是協助散戶做風險控管的分析助手。請用繁體中文、條列方式，給這筆持倉具體建議。

股票：{code} {name}（{'台股' if r['market'] == 'TW' else '美股'}）
買進價 {p['cost']:,.2f}，現價 {p['price']:,.2f}，損益 {p['pnl'] * 100:+.1f}%（扣費 {p['net_pnl'] * 100:+.1f}%），損益兩平價 {p['breakeven']:,.2f}
{f"持有 {p['held']} 天，買進後最高收盤 {p['peak']:,.2f}" if p['held'] is not None else ''}
ATR(14) {p['atr']:,.2f}（{p['atr'] / p['price'] * 100:.1f}%/日），月線 {p['ma20']:,.2f}，季線 {p['ma60']:,.2f}
系統建議停損 {p['stop']:,.2f}（{p['stop_why']}）
技術條件：{parts}
綜合上漲機率：明日 {r.get('p1', 0.5) * 100:.0f}%、10 日 {r.get('p10', 0.5) * 100:.0f}%（含技術、籌碼、營收、新聞、隔夜美股）
未來 10 日 80% 區間 {r['d10']['p10'] * 100:+.1f}% ~ {r['d10']['p90'] * 100:+.1f}%
注意：這套評分回測時預測力很弱，請以風險控管為主，不要把評分當成漲跌預測。
近期新聞標題：
{lines}

請回答：
1. 這筆交易現在的狀況（一句話）
2. 停損：同意系統停損價嗎？如果不同意，給出你的價位與理由
3. 接下來的操作建議（續抱／減碼／出場／加碼的條件，用具體價位描述）
4. 新聞面有沒有需要注意的風險
最後加一句：以上為依據資料的分析，不保證報酬，最終決定請自行判斷。全文 250 字以內。"""
