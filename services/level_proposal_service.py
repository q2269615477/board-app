"""
level_proposal_service.py — Phase 3：学用户画法，提议候选支撑位

设计纪律：
- **候选价格来自真实 K 线的价格要素**（某根源K的 open/high/low/close），
  与用户手画的 level_origin 同构；绝不让模型凭空报一个价格。
- **排序 = 启发式强度 × 与用户历史画法的相似度**。相似度从用户已画样本
  统计而来（偏好哪个价格要素、是否偏好局部极值、量能分位、触碰次数…）。
- 每个候选附 `evidence`（为什么提它）与 `similar_case`（最像你画过的哪一条），
  保证可解释、可复述。
- **系统只提议，不判定**。用户 accept 才落成正式 level_origin；reject 记为负样本。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from data.annotation_repo import get_annotation_repo

_PRICE_ELEMENTS = ("open", "high", "low", "close")


def _load_bars(symbol: str, period: str) -> List[Dict[str, Any]]:
    try:
        from data.sqlite_repo import get_sqlite_repo
        df = get_sqlite_repo().read_kline(symbol, period)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    cols = {c.lower(): c for c in df.columns}
    out = []
    for _, r in df.iterrows():
        try:
            out.append({
                "date": str(r[cols.get("date", "date")]),
                "open": float(r[cols.get("open", "open")]),
                "high": float(r[cols.get("high", "high")]),
                "low": float(r[cols.get("low", "low")]),
                "close": float(r[cols.get("close", "close")]),
                "volume": float(r[cols.get("volume", "volume")] or 0),
            })
        except Exception:
            continue
    return out


def _percentile(sorted_vals: List[float], v: float) -> float:
    if not sorted_vals:
        return 0.0
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    return round(lo / len(sorted_vals), 3)


def compute_features(bars: List[Dict], idx: int, price: float, window: int = 60) -> Dict[str, Any]:
    """与前端 computeFeatureSnapshot 同构，保证提议样本与用户样本可比。"""
    n = len(bars)
    bar = bars[idx]
    out: Dict[str, Any] = {"window_bars": window}
    lo = max(0, idx - window)
    hi = min(n - 1, idx + window)
    win = bars[lo:hi + 1]

    k = 5
    nb = bars[max(0, idx - k): min(n, idx + k + 1)]
    is_low = all(bar["low"] <= b["low"] for b in nb)
    is_high = all(bar["high"] >= b["high"] for b in nb)
    out["is_local_extremum"] = bool(is_low or is_high)
    out["extremum_type"] = "low" if is_low else ("high" if is_high else None)

    vols = sorted(b["volume"] for b in win)
    out["volume_percentile"] = _percentile(vols, bar["volume"])

    if bar["open"]:
        out["body_pct"] = round(abs(bar["close"] - bar["open"]) / bar["open"], 4)

    if len(win) > 2:
        c0, c1 = win[0]["close"], win[-1]["close"]
        ch = (c1 - c0) / (c0 or 1)
        out["trend_context"] = "uptrend" if ch > 0.03 else ("downtrend" if ch < -0.03 else "range")

    if price:
        tol = abs(price) * 0.005
        out["prior_touches"] = sum(
            1 for b in bars[idx + 1:] if b["low"] - tol <= price <= b["high"] + tol
        )

    if idx >= 1:
        trs = []
        for i in range(max(1, idx - 13), idx + 1):
            b, p = bars[i], bars[i - 1]
            trs.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
        if trs:
            out["atr14"] = round(sum(trs) / len(trs), 4)
    return out


class LevelProposalService:
    def __init__(self):
        self.repo = get_annotation_repo()

    # ---- 用户画法画像 ----

    def user_style(self, symbol: str = None, period: str = None) -> Dict[str, Any]:
        """从用户已画的 level_origin 统计其画法偏好。

        样本不足时返回 sample_count 小的画像，调用方据此降低提议置信度
        —— 冷启动必然不准，必须如实暴露而不是假装有信心。
        """
        cases = self.repo.list_cases(type_="level_origin", limit=500)
        if period:
            cases = [c for c in cases if c.get("period") == period] or cases
        elems: Dict[str, int] = {}
        roles: Dict[str, int] = {}
        extremum_hits = 0
        vol_ps: List[float] = []
        touches: List[float] = []
        for c in cases:
            pe = (c.get("source_bar") or {}).get("price_element") or c.get("price_element")
            if pe:
                elems[pe] = elems.get(pe, 0) + 1
            rl = (c.get("level") or {}).get("role") or "support"
            roles[rl] = roles.get(rl, 0) + 1
            fs = c.get("feature_snapshot") or {}
            if fs.get("is_local_extremum"):
                extremum_hits += 1
            if fs.get("volume_percentile") is not None:
                vol_ps.append(float(fs["volume_percentile"]))
            if fs.get("prior_touches") is not None:
                touches.append(float(fs["prior_touches"]))
        n = len(cases)
        return {
            "sample_count": n,
            "price_element_pref": elems or {"low": 1},
            "role_pref": roles or {"support": 1},   # 用户主要画支撑还是阻力
            "extremum_rate": round(extremum_hits / n, 3) if n else 0.0,
            "avg_volume_percentile": round(sum(vol_ps) / len(vol_ps), 3) if vol_ps else None,
            "avg_prior_touches": round(sum(touches) / len(touches), 1) if touches else None,
            "confidence": "low" if n < 20 else ("medium" if n < 100 else "usable"),
        }

    def _style_similarity(
        self, feats: Dict[str, Any], elem: str, style: Dict[str, Any], role: str = "support"
    ) -> float:
        """候选与用户画法的相似度 0~1（越像用户平时画的越高）。"""
        score = 0.0
        pref = style.get("price_element_pref") or {}
        total = sum(pref.values()) or 1
        score += 0.3 * (pref.get(elem, 0) / total)          # 价格要素偏好
        # 角色偏好：用户以画支撑为主时，不该给他一堆阻力位候选
        rp = style.get("role_pref") or {}
        rtot = sum(rp.values()) or 1
        score += 0.3 * (rp.get(role, 0) / rtot)
        er = style.get("extremum_rate") or 0.0
        score += 0.2 * (er if feats.get("is_local_extremum") else (1 - er))
        avp = style.get("avg_volume_percentile")
        if avp is not None and feats.get("volume_percentile") is not None:
            score += 0.2 * max(0.0, 1 - abs(feats["volume_percentile"] - avp))
        else:
            score += 0.1
        return round(min(1.0, score), 3)

    # ---- 候选生成 ----

    def propose(
        self,
        symbol: str,
        period: str = "daily",
        top_n: int = 5,
        lookback: int = 400,
        pivot_k: int = 5,
    ) -> Dict[str, Any]:
        bars = _load_bars(symbol, period)
        if len(bars) < 30:
            return {
                "symbol": symbol, "period": period, "candidates": [],
                "error": "K线数据不足（需≥30根）", "bars": len(bars),
            }
        bars = bars[-lookback:] if len(bars) > lookback else bars
        n = len(bars)
        last_close = bars[-1]["close"]
        style = self.user_style(symbol, period)

        # 已画的位，避免重复提议
        existing = [
            float((c.get("level") or {}).get("price"))
            for c in self.repo.list_cases(symbol=symbol, period=period, type_="level_origin", limit=200)
            if (c.get("level") or {}).get("price") is not None
        ]

        cands: List[Dict[str, Any]] = []
        for i in range(pivot_k, n - pivot_k):
            nb = bars[i - pivot_k: i + pivot_k + 1]
            is_low = all(bars[i]["low"] <= b["low"] for b in nb)
            is_high = all(bars[i]["high"] >= b["high"] for b in nb)
            if not (is_low or is_high):
                continue
            role = "support" if is_low else "resistance"
            # 候选价格取该源K的各价格要素（与用户画法同构）
            for elem in _PRICE_ELEMENTS:
                price = bars[i][elem]
                if not price or price <= 0:
                    continue
                if any(abs(price - e) / max(e, 1e-9) < 0.005 for e in existing):
                    continue                                  # 你已经画过，跳过
                dist_pct = abs(last_close - price) / price * 100
                if dist_pct > 25:
                    continue                                  # 离现价太远，参考价值低
                feats = compute_features(bars, i, price)
                sim = self._style_similarity(feats, elem, style, role)
                touches = feats.get("prior_touches", 0) or 0
                strength = min(1.0, touches / 12.0) * 0.5 + (feats.get("volume_percentile") or 0) * 0.3 + \
                    (0.2 if feats.get("is_local_extremum") else 0.0)
                proximity = max(0.0, 1 - dist_pct / 25.0)
                score = round((strength * 0.4 + sim * 0.4 + proximity * 0.2) * 100, 1)
                cands.append({
                    "symbol": symbol, "period": period, "role": role,
                    "price": round(price, 4),
                    "source_bar": {
                        "date": bars[i]["date"],
                        "ohlc": {k2: bars[i][k2] for k2 in _PRICE_ELEMENTS},
                        "price_element": elem,
                    },
                    "feature_snapshot": feats,
                    "dist_to_last_pct": round(dist_pct, 2),
                    "style_similarity": sim,
                    "score": score,
                    "evidence": self._evidence(feats, elem, dist_pct, role),
                })

        # 同价位去重（保留分高的），再按分排序
        cands.sort(key=lambda c: -c["score"])
        picked: List[Dict[str, Any]] = []
        for c in cands:
            if any(abs(c["price"] - p["price"]) / max(p["price"], 1e-9) < 0.008 for p in picked):
                continue
            picked.append(c)
            if len(picked) >= top_n:
                break

        for c in picked:
            c["similar_case"] = self._similar_case(c, symbol, period)

        return {
            "symbol": symbol, "period": period,
            "bars": n, "last_close": last_close,
            "user_style": style,
            "candidates": picked,
            "agent_hint": (
                "这些是按你历史画法排序的**候选**，不是结论。"
                f"当前画像基于 {style['sample_count']} 个样本（置信度 {style['confidence']}），"
                "样本越多越贴合你的风格。请逐条确认或否决。"
            ),
        }

    def _evidence(self, feats: Dict, elem: str, dist_pct: float, role: str) -> List[str]:
        ev = []
        if feats.get("is_local_extremum"):
            ev.append(f"该K是±5根内的局部{'低点' if feats.get('extremum_type') == 'low' else '高点'}")
        t = feats.get("prior_touches")
        if t:
            ev.append(f"此后被{t}根K触及（±0.5%）")
        vp = feats.get("volume_percentile")
        if vp is not None and vp >= 0.7:
            ev.append(f"量能处于窗口{int(vp * 100)}分位")
        if feats.get("trend_context"):
            ev.append({"uptrend": "处于上升段", "downtrend": "处于下降段", "range": "处于震荡段"}
                      .get(feats["trend_context"], ""))
        ev.append(f"取源K的{ {'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘'}[elem] }价")
        ev.append(f"距现价{dist_pct:.1f}%")
        return [e for e in ev if e]

    def _similar_case(self, cand: Dict, symbol: str, period: str) -> Optional[Dict[str, Any]]:
        """找出用户画过的最相似的一条，作为可复述的依据。"""
        cases = self.repo.list_cases(type_="level_origin", limit=300)
        best, best_d = None, 1e9
        cf = cand["feature_snapshot"]
        for c in cases:
            fs = c.get("feature_snapshot") or {}
            if not fs:
                continue
            d = 0.0
            d += 0.0 if (fs.get("is_local_extremum") == cf.get("is_local_extremum")) else 1.0
            if fs.get("volume_percentile") is not None and cf.get("volume_percentile") is not None:
                d += abs(fs["volume_percentile"] - cf["volume_percentile"])
            pe_c = (c.get("source_bar") or {}).get("price_element")
            d += 0.0 if pe_c == cand["source_bar"]["price_element"] else 0.5
            if d < best_d:
                best_d, best = d, c
        if not best:
            return None
        return {
            "case_id": best.get("id"),
            "symbol": best.get("symbol"),
            "period": best.get("period"),
            "price": (best.get("level") or {}).get("price"),
            "note": best.get("notes") or "",     # 用户原话，供 Agent 复述
            "distance": round(best_d, 3),
        }


_svc: Optional[LevelProposalService] = None


def get_level_proposal_service() -> LevelProposalService:
    global _svc
    if _svc is None:
        _svc = LevelProposalService()
    return _svc
