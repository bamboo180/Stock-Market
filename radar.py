"""潛力股雷達：把「風青」的經驗變成可以掃描的條件。

風青（2061）2026 年大漲前看得到的訊號：
  1. 營收開始回溫，而且最新月份比今年累計還好（加速）
  2. 獲利由虧轉盈、毛利率回升（轉型開始見效）
  3. 股價還在低檔、季線走平（市場還沒反應）
  4. 融資沒有增加，法人或主力悄悄在買（籌碼乾淨）

做法分兩階段（全台股一千多檔，不可能每檔都抓完整歷史）：
  第一階段：證交所／櫃買中心「每月營收彙總表」一次拿到全部公司的最新月營收 → 篩出營收在加速的候選股
  第二階段：候選股逐檔抓 FinMind 的股價、法人、融資與季報 → 依四大面向各 25 分，總分 100
"""
from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import engine

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TWSE_REV = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REV = ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            "https://www.tpex.org.tw/openapi/v1/t187ap05_O"]
# 金融業沒有一般營收概念；建材營造是交屋才認列，單月年增 200% 很常見，不代表轉機
EXCLUDE_INDUSTRY = ("金融", "保險", "證券", "銀行", "存託憑證", "建材營造")


# ----------------------------------------------------------------------------
# 第一階段：全市場月營收
# ----------------------------------------------------------------------------
def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except ValueError:
        return np.nan


def _pick(row: dict, *keys, exclude=()) -> object:
    """欄位名稱各家寫法不同：用「包含某些字」找欄位。"""
    for k in row:
        if all(s in k for s in keys) and not any(e in k for e in exclude):
            return row[k]
    return None


def _rev_rows(rows: list, market: str) -> pd.DataFrame:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(_pick(r, "公司代號") or _pick(r, "CompanyCode") or "").strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        rev = _num(_pick(r, "當月營收", exclude=("累計", "上月", "去年")) or _pick(r, "Revenue", exclude=("Last", "Accum")))
        last_y = _num(_pick(r, "去年當月營收") or np.nan)
        yoy = _num(_pick(r, "去年同月增減") or np.nan)
        if np.isnan(yoy) and rev and last_y:
            yoy = (rev / last_y - 1) * 100
        cum_yoy = _num(_pick(r, "累計", "前期比較增減") or _pick(r, "累計", "增減") or np.nan)
        ym = str(_pick(r, "資料年月") or "")
        out.append({"code": code, "name": str(_pick(r, "公司名稱") or "").strip(),
                    "industry": str(_pick(r, "產業別") or "").strip(), "market": market,
                    "ym": ym, "rev": rev, "yoy": yoy, "cum_yoy": cum_yoy,
                    "mom": _num(_pick(r, "上月比較增減") or np.nan), "note": str(_pick(r, "備註") or "").strip()})
    return pd.DataFrame(out)


def revenue_all() -> tuple[pd.DataFrame, dict]:
    """上市＋上櫃最新月營收。回傳 (資料, 各來源狀態)。"""
    status, frames = {}, []
    try:
        df = _rev_rows(requests.get(TWSE_REV, headers=UA, timeout=30).json(), "上市")
        frames.append(df)
        status["上市"] = f"{len(df)} 家" if len(df) else "沒有資料"
    except Exception as e:
        status["上市"] = f"失敗（{str(e)[:40]}）"
    for url in TPEX_REV:
        try:
            df = _rev_rows(requests.get(url, headers=UA, timeout=30).json(), "上櫃")
        except Exception as e:
            status["上櫃"] = f"失敗（{str(e)[:40]}）"
            continue
        if len(df):
            frames.append(df)
            status["上櫃"] = f"{len(df)} 家"
            break
        status["上櫃"] = "沒有資料"
    all_ = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return all_, status


def stage1(rev: pd.DataFrame, n: int = 40, market: str = "全部") -> pd.DataFrame:
    """營收在加速的公司：當月年增 ≥ 15%，而且比累計年增好（或累計也已轉正）。"""
    if rev is None or rev.empty:
        return pd.DataFrame()
    d = rev.copy()
    if market in ("上市", "上櫃"):
        d = d[d["market"] == market]
    d = d[~d["industry"].str.contains("|".join(EXCLUDE_INDUSTRY), na=False)]
    d = d[(d["rev"] >= 20_000)]                                   # 月營收至少 2,000 萬（單位：千元）
    d = d[d["yoy"].between(15, 500)]
    d = d[(d["yoy"] > d["cum_yoy"]) | (d["cum_yoy"] >= 10)]
    # 排序看「加速」多於看「成長多大」：風青不是成長最快的，而是「最新月份明顯比今年累計好」的轉機型
    accel = (d["yoy"] - d["cum_yoy"]).clip(-50, 150).fillna(0)
    d = d.assign(s1=d["yoy"].clip(0, 150) * 0.3 + accel * 0.7 + d["cum_yoy"].clip(-30, 60).fillna(0) * 0.2)
    return d.sort_values("s1", ascending=False).head(n).reset_index(drop=True)


# ----------------------------------------------------------------------------
# 第二階段：逐檔評分
# ----------------------------------------------------------------------------
def quarterly(code: str, token: str | None) -> pd.DataFrame:
    """每季 EPS 與毛利率（FinMind 綜合損益表）。"""
    start = (dt.date.today() - dt.timedelta(days=800)).isoformat()
    fs = engine._finmind("TaiwanStockFinancialStatements", code, start, token)
    if fs.empty:
        return pd.DataFrame()
    piv = fs.pivot_table(index="date", columns="type", values="value", aggfunc="last").sort_index()
    out = pd.DataFrame(index=piv.index)
    out["eps"] = piv.get("EPS")
    if "GrossProfit" in piv and "Revenue" in piv:
        out["gm"] = piv["GrossProfit"] / piv["Revenue"].replace(0, np.nan)
    return out.reset_index()


def score(info: dict, px: pd.DataFrame, q: pd.DataFrame) -> dict:
    """四大面向各 25 分。info 是第一階段的那一列。"""
    why, tags = [], []
    # ---- 1. 營收動能 ----
    yoy, cum = info["yoy"], info["cum_yoy"]
    a = 15 if yoy >= 50 else 12 if yoy >= 30 else 8 if yoy >= 15 else 0
    acc = yoy - cum if not np.isnan(cum) else 0
    a += 6 if acc >= 20 else 3 if acc >= 5 else 0
    a += 4 if (not np.isnan(cum) and cum >= 10) else 0
    a = min(a, 25)
    why.append(f"營收年增 {yoy:+.0f}%" + (f"（累計 {cum:+.0f}%，正在加速）" if acc >= 5 else f"（累計 {cum:+.0f}%）"))

    # ---- 2. 獲利轉機 ----
    b, eps_last, eps_ly = 0, np.nan, np.nan
    if q is not None and len(q) and q["eps"].notna().sum() >= 5:
        e = q.dropna(subset=["eps"]).reset_index(drop=True)
        eps_last, eps_ly = float(e["eps"].iloc[-1]), float(e["eps"].iloc[-5])
        if eps_last > 0 >= eps_ly:
            b, txt = 18, "EPS 轉虧為盈"
        elif eps_last > 0 and eps_ly > 0 and eps_last >= eps_ly * 1.5:
            b, txt = 14, "EPS 年增 50% 以上"
        elif eps_last > eps_ly and eps_last > 0:
            b, txt = 8, "EPS 比去年同季好"
        elif eps_last > eps_ly:
            b, txt = 4, "虧損縮小"
        else:
            txt = "獲利還沒改善"
        why.append(f"{txt}（{eps_last:+.2f} vs 去年同季 {eps_ly:+.2f}）")
        if "gm" in q and q["gm"].notna().sum() >= 5:
            g = q.dropna(subset=["gm"]).reset_index(drop=True)
            dgm = (g["gm"].iloc[-1] - g["gm"].iloc[-5]) * 100
            if dgm >= 2:
                b += 7
                why.append(f"毛利率回升 {dgm:+.1f} 個百分點")
            elif dgm > 0:
                b += 3
        if eps_last < 0 and eps_last <= eps_ly:
            tags.append("虧損中")
    else:
        why.append("季報資料不足")
    b = min(b, 25)

    # ---- 3. 低檔打底 ----
    c, pos, r60, r20 = 0, np.nan, np.nan, np.nan
    d = px.tail(250).reset_index(drop=True) if px is not None else pd.DataFrame()
    if len(d) >= 120:
        cl = d["close"].astype(float)
        hi, lo, last = float(d["high"].max()), float(d["low"].min()), float(cl.iloc[-1])
        pos = (last - lo) / (hi - lo) if hi > lo else 0.5
        r60 = last / float(cl.iloc[-61]) - 1
        r20 = last / float(cl.iloc[-21]) - 1
        ma60 = cl.rolling(60).mean()
        slope = float(ma60.iloc[-1] / ma60.iloc[-21] - 1)
        c = 15 if pos <= 0.3 else 11 if pos <= 0.5 else 5 if pos <= 0.7 else 0
        if -0.02 <= slope <= 0.08:
            c += 6
            why.append("季線走平或剛翻揚")
        if last >= float(ma60.iloc[-1]):
            c += 4
        why.append(f"股價在一年區間的 {pos * 100:.0f}% 位置")
        if r60 > 0.5:
            c = 0
            tags.append("已大漲")
            why.append(f"近 3 個月已漲 {r60 * 100:.0f}%")
    else:
        why.append("股價資料不足一年")
    c = min(c, 25)

    # ---- 4. 籌碼 ----
    dd, mg_chg, inst20 = 0, np.nan, np.nan
    if len(d) >= 120:
        if "margin" in d and d["margin"].notna().sum() >= 21:
            m = d["margin"].ffill()
            mg_chg = float(m.iloc[-1] - m.iloc[-21])
            if mg_chg <= 0:
                dd += 9
                why.append("融資沒有增加")
        if {"foreign", "trust"} <= set(d.columns):
            inst20 = float((d["foreign"].fillna(0) + d["trust"].fillna(0)).tail(20).sum())
            if inst20 > 0:
                dd += 9
                why.append(f"外資＋投信 20 日買超 {inst20:,.0f} 張")
        v = d["vol"].astype(float)
        vr = float(v.tail(20).mean() / max(v.tail(120).mean(), 1e-9))
        if 0.5 <= vr <= 1.6 and r20 >= -0.08:
            dd += 7
            why.append("量能平穩、股價守得住")
    dd = min(dd, 25)

    return {"code": info["code"], "name": info["name"], "industry": info["industry"], "market": info["market"],
            "score": int(a + b + c + dd), "s_rev": a, "s_eps": b, "s_low": c, "s_chip": dd,
            "yoy": yoy, "cum_yoy": cum, "eps": eps_last, "eps_ly": eps_ly, "pos": pos, "r60": r60,
            "margin20": mg_chg, "inst20": inst20, "tags": tags, "why": why, "note": info.get("note", "")}


def scan(token: str | None, n: int = 40, market: str = "全部", workers: int = 6) -> dict:
    rev, status = revenue_all()
    cand = stage1(rev, n, market)
    if cand.empty:
        return {"rows": [], "status": status, "ym": "", "n_all": len(rev), "n_cand": 0, "errors": []}
    start = (dt.date.today() - dt.timedelta(days=400)).isoformat()

    def one(info):
        try:
            px = engine.fetch_tw(info["code"], start, token)
        except Exception as e:
            return None, f"{info['code']} 股價：{str(e)[:30]}"
        try:
            q = quarterly(info["code"], token)
        except Exception:
            q = pd.DataFrame()
        try:
            return score(info, px, q), None
        except Exception as e:
            return None, f"{info['code']} 評分：{str(e)[:30]}"

    rows, errors = [], []
    with ThreadPoolExecutor(workers) as ex:
        for res, err in ex.map(one, [r for _, r in cand.iterrows()]):
            if res:
                rows.append(res)
            if err:
                errors.append(err)
    rows.sort(key=lambda r: r["score"], reverse=True)
    ym = str(cand["ym"].mode().iloc[0]) if "ym" in cand and len(cand) else ""
    return {"rows": rows, "status": status, "ym": ym, "n_all": len(rev), "n_cand": len(cand), "errors": errors}


def ym_text(ym: str) -> str:
    """11509 → 2026 年 9 月"""
    if re.fullmatch(r"\d{5}", ym or ""):
        return f"{int(ym[:3]) + 1911} 年 {int(ym[3:])} 月"
    return ym or "—"
