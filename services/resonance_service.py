"""
resonance_service.py — 跨板块共振支撑检测（Phase 2）

纪律（铁律精神）：
- 判断标准全部来自用户（成员组、阈值、最少对齐数）；系统不发明关联。
- 只做确定性硬算：一组标的中"现价距各自支撑 < 阈值"的成员数 >= min_aligned → 共振。
- 输出候选 + 命中支撑的用户原话；**不输出买/卖结论**，不算相关性/信号强度断言。
- score 仅用于候选排序，非"信号强度"。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.annotation_repo import get_annotation_repo

_DISCLAIMER = "标准由用户定义；共振候选仅供参考，不构成买卖建议，最终判断在用户。"

_SCAN_ALL_CACHE_TTL = 60  # scan_all 结果缓存秒数

# _classification_index() 的模块级缓存：首次加载后复用，避免每次重读 16KB JSON。
# 值为 (file_path, index_dict) 或 None（未加载/加载失败）。
_classification_cache: list = [None, None]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_group_id() -> str:
    return f"grp_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"


def get_current_price(symbol: str, period: str = "daily") -> Optional[float]:
    """最新收盘价（现价代理）。member 周期优先，回退日线。"""
    try:
        from data.sqlite_repo import get_sqlite_repo
        repo = get_sqlite_repo()
    except Exception:
        return None
    seen = []
    for p in (period, "daily"):
        if not p or p in seen:
            continue
        seen.append(p)
        try:
            df = repo.read_kline(symbol, p)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        except Exception:
            continue
    return None


def get_price_date(symbol: str, period: str = "daily") -> Optional[str]:
    """该现价来自哪一天（用于暴露数据陈旧，避免拿旧价判共振）。"""
    try:
        from data.sqlite_repo import get_sqlite_repo
        repo = get_sqlite_repo()
    except Exception:
        return None
    seen = []
    for p in (period, "daily"):
        if not p or p in seen:
            continue
        seen.append(p)
        try:
            df = repo.read_kline(symbol, p)
            if df is not None and not df.empty and "date" in df.columns:
                return str(df["date"].iloc[-1])
        except Exception:
            continue
    return None


def _last_trading_date() -> Optional[str]:
    try:
        from data.board_api import get_last_trading_date
        d = get_last_trading_date()
        return str(d) if d else None
    except Exception:
        return None


class ResonanceService:
    def __init__(self):
        self.repo = get_annotation_repo()
        # scan_all 结果缓存：key -> (timestamp, result)
        self._scan_all_cache: Dict[tuple, tuple] = {}

    # ---- 组 CRUD（用户显式定义）----

    def create_group(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        g = dict(payload)
        if not g.get("id"):
            g["id"] = _new_group_id()
        g.setdefault("theme", "")
        g.setdefault("members", [])
        g.setdefault("threshold_pct", 3.0)
        g.setdefault("min_aligned", 2)
        g.setdefault("created_at", _now())
        g["updated_at"] = _now()
        # 禁止自动判定字段（守住铁律精神）
        for banned in ("auto_score", "correlation", "system_hypothesis", "verified_by_backtest"):
            g.pop(banned, None)
        self.repo.upsert_group(g)
        return g

    def update_group(self, group_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        g = self.repo.get_group(group_id)
        if not g:
            raise KeyError(group_id)
        for k, v in patch.items():
            if k in ("id", "created_at"):
                continue
            if k in ("auto_score", "correlation", "system_hypothesis", "verified_by_backtest"):
                continue
            g[k] = v
        g["updated_at"] = _now()
        self.repo.upsert_group(g)
        return g

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get_group(group_id)

    def list_groups(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.repo.list_groups(limit=limit)

    def delete_group(self, group_id: str) -> None:
        self.repo.delete_group(group_id)

    # ---- 支撑位查询 ----

    def _supports_for(self, symbol: str, period: str) -> List[Dict[str, Any]]:
        """该标的该周期**仍有效**的 level_origin 支撑位（role=support）。

        生命周期由用户维护（level.status）：active 参与共振扫描；
        broken(已跌破) / invalid(作废) 的位不再参与，避免旧位污染判定。
        缺省视为 active，兼容早期未带 status 的记录。
        """
        cases = self.repo.list_cases(symbol=symbol, period=period, type_="level_origin", limit=200)
        out = []
        for c in cases:
            lv = c.get("level") or {}
            if (lv.get("role") or "support") != "support":
                continue
            if lv.get("price") is None:
                continue
            if (lv.get("status") or "active") != "active":
                continue
            out.append(c)
        return out

    def _eval_cell(self, sym: str, period: str, threshold: float) -> Dict[str, Any]:
        """评估单个 (标的, 周期)：现价距最近支撑多远、是否到位。

        这是共振的最小判定单元；列表视图与矩阵视图共用，保证口径一致。
        """
        price = get_current_price(sym, period) if sym else None
        supports = self._supports_for(sym, period) if sym else []
        best = None
        for c in supports:
            lp = float((c.get("level") or {}).get("price"))
            if price and lp:
                # 有符号距离：>0 = 现价在支撑上方（尚未跌破）；<0 = 已跌破。
                # 早期实现用 abs()，于是"刚跌破 2% "与"上方 2% "同样被判为到位，
                # 一组标的同时破位反而会被报成"共振成立、score 高" —— 与 MCP 给
                # Agent 的话术（"现价逼近支撑"）直接冲突，会给出反向结论。
                signed_pct = (price - lp) / lp * 100.0
                dist_pct = abs(signed_pct)
                if best is None or dist_pct < best["dist_pct"]:
                    best = {
                        "case_id": c.get("id"),
                        "level_price": lp,
                        "source_date": (c.get("source_bar") or {}).get("date"),
                        "dist_pct": round(dist_pct, 3),
                        "signed_pct": round(signed_pct, 3),
                        "side": "above" if signed_pct > 0 else ("below" if signed_pct < 0 else "at"),
                        "note": c.get("notes") or "",  # 用户原话
                    }
        # 判定用未舍入的原值（round 后 2.9996 会被当成 3.0 而误判未到位）
        at_support = False
        broken_through = False
        if best:
            sp = best["signed_pct"]
            at_support = -1e-9 <= sp < threshold      # 现价在支撑上方 threshold 内（含刚触及）
            broken_through = sp < -1e-9               # 已跌破
        return {
            "symbol": sym,
            "period": period,
            "current_price": price,
            "price_date": get_price_date(sym, period) if sym else None,
            "nearest_support": best,
            "at_support": at_support,
            "broken_through": broken_through,
            "support_count": len(supports),
        }

    def scan_matrix(
        self,
        group: Dict[str, Any],
        periods: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """矩阵扫描：成员 × 周期。

        用户的共振既可跨标的、也可**同一标的跨周期**，矩阵是两者的统一表达：
        行=标的，列=周期，格子=距各自支撑的距离%。
        `require_periods` 让用户指定"哪几个周期必须同时到位"才算共振。
        """
        threshold = float(group.get("threshold_pct", 3.0))
        min_aligned = int(group.get("min_aligned", 2))
        periods = periods or group.get("matrix_periods") or [
            "daily", "weekly", "monthly",
        ]
        require = group.get("require_periods") or []
        members = group.get("members") or []

        rows = []
        for m in members:
            sym = m.get("symbol")
            cells = [self._eval_cell(sym, p, threshold) for p in periods]
            rows.append({
                "symbol": sym,
                "symbol_name": m.get("symbol_name"),
                "member_role": m.get("role"),
                "cells": cells,
                "aligned_periods": [c["period"] for c in cells if c["at_support"]],
            })

        # 每个周期有多少标的到位（列汇总）
        per_period = {}
        for i, p in enumerate(periods):
            hit = [r["symbol"] for r in rows if r["cells"][i]["at_support"]]
            per_period[p] = {"aligned": hit, "count": len(hit)}

        # require_periods 必须是 periods 的子集，否则那一列根本没扫，
        # 判定会静默为假（用户以为"没共振"，实际是没算）
        unknown_require = [p for p in require if p not in periods]

        # 判定一：跨标的 —— 某周期内到位的**标的数** >= min_aligned
        if require:
            hit_periods = [p for p in require
                           if per_period.get(p, {}).get("count", 0) >= min_aligned]
            cross_symbol = bool(require) and len(hit_periods) == len(require) and not unknown_require
        else:
            hit_periods = [p for p in periods if per_period[p]["count"] >= min_aligned]
            cross_symbol = len(hit_periods) > 0

        # 判定二：单标的跨周期 —— 某个标的到位的**周期数** >= min_periods。
        # 用户明确要求支持「同一标的不同周期共振」，但 min_aligned 数的是标的数，
        # 成员只有 1 个时默认值 2 让它永远不成立。这里单列一个判据。
        min_periods = int(group.get("min_periods_per_symbol") or 0)
        if not min_periods:
            min_periods = 2 if len(members) == 1 else 0   # 单标的组默认按跨周期判
        cross_period_syms = []
        if min_periods:
            need = require or periods
            for r in rows:
                hit = [p for p in r["aligned_periods"] if p in need]
                if len(hit) >= min_periods:
                    cross_period_syms.append({"symbol": r["symbol"], "periods": hit})

        is_resonance = bool(members) and (cross_symbol or bool(cross_period_syms))

        total_cells = len(rows) * len(periods)
        lit = sum(len(r["aligned_periods"]) for r in rows)
        ltd = _last_trading_date()
        stale = [
            {"symbol": r["symbol"], "period": c["period"], "price_date": c["price_date"]}
            for r in rows for c in r["cells"]
            if c.get("price_date") and ltd and str(c["price_date"]) < str(ltd)
        ]
        return {
            "group_id": group.get("id"),
            "theme": group.get("theme"),
            "periods": periods,
            "require_periods": require,
            "threshold_pct": threshold,
            "min_aligned": min_aligned,
            "rows": rows,
            "per_period": per_period,
            "is_resonance": is_resonance,
            "resonant_periods": hit_periods,
            "cross_symbol_resonance": cross_symbol,
            "cross_period_symbols": cross_period_syms,
            "min_periods_per_symbol": min_periods,
            "invalid_require_periods": unknown_require,
            "lit_cells": lit,
            "total_cells": total_cells,
            "last_trading_date": ltd,
            "stale_cells": stale,
            "data_warning": (
                f"{len(stale)} 个格子的价格不是最近交易日({ltd})的数据；请先更新数据。"
            ) if stale else None,
            "disclaimer": _DISCLAIMER,
        }

    def scan_matrix_by_id(self, group_id: str, periods=None) -> Dict[str, Any]:
        g = self.repo.get_group(group_id)
        if not g:
            raise KeyError(group_id)
        return self.scan_matrix(g, periods)

    # ---- 共振扫描（确定性）----

    def scan(self, group: Dict[str, Any]) -> Dict[str, Any]:
        threshold = float(group.get("threshold_pct", 3.0))
        min_aligned = int(group.get("min_aligned", 2))
        members = group.get("members") or []

        member_results: List[Dict[str, Any]] = []
        for m in members:
            cell = self._eval_cell(m.get("symbol"), m.get("period", "daily"), threshold)
            cell.update({
                "symbol_name": m.get("symbol_name"),
                "member_role": m.get("role"),   # leader_board / sector_index / leader_stock…
            })
            member_results.append(cell)

        aligned = [r for r in member_results if r["at_support"]]
        is_resonance = len(aligned) >= min_aligned and len(members) > 0

        frac = (len(aligned) / len(members)) if members else 0.0
        tightness = 0.0
        if aligned:
            avg_dist = sum(r["nearest_support"]["dist_pct"] for r in aligned) / len(aligned)
            tightness = max(0.0, 1.0 - (avg_dist / threshold if threshold else 0))
        score = round(frac * 70 + tightness * 30, 1)  # 仅用于排序

        # 数据新鲜度：现价取自库内最后一根K，若落后最近交易日必须显式告警，
        # 否则会拿旧价判"共振成立"而误导用户。
        ltd = _last_trading_date()
        stale = [
            {"symbol": r["symbol"], "price_date": r["price_date"]}
            for r in member_results
            if r.get("price_date") and ltd and str(r["price_date"]) < str(ltd)
        ]
        missing = [r["symbol"] for r in member_results if r.get("current_price") is None]

        return {
            "group_id": group.get("id"),
            "theme": group.get("theme"),
            "threshold_pct": threshold,
            "min_aligned": min_aligned,
            "member_count": len(members),
            "aligned_count": len(aligned),
            "is_resonance": is_resonance,
            "score": score,
            "members": member_results,
            "aligned": aligned,
            "last_trading_date": ltd,
            "stale_members": stale,
            "missing_price_members": missing,
            "data_warning": (
                f"{len(stale)} 个成员的价格不是最近交易日({ltd})的数据；"
                "请先更新数据再据此判断。"
            ) if stale else None,
            "disclaimer": _DISCLAIMER,
        }

    # ---- Phase 4：自动分组 + 全市场扫描 ----

    def symbols_with_levels(self, period: str = None) -> List[Dict[str, Any]]:
        """有「有效支撑位」的 (标的, 周期)。

        扫描范围只取用户教过的标的 —— 没画过线的标的没有支撑位，扫了也无意义。
        """
        cases = self.repo.list_cases(type_="level_origin", limit=5000)
        seen = {}
        for c in cases:
            lv = c.get("level") or {}
            if (lv.get("role") or "support") != "support":
                continue
            if lv.get("price") is None or (lv.get("status") or "active") != "active":
                continue
            p = c.get("period") or "daily"
            if period and p != period:
                continue
            key = (c.get("symbol"), p)
            if key[0] and key not in seen:
                seen[key] = {
                    "symbol": key[0], "period": p,
                    "symbol_name": c.get("symbol_name") or key[0],
                    "asset_type": c.get("asset_type") or "",
                }
        return list(seen.values())

    def _classification_index(self) -> Dict[str, Dict[str, str]]:
        """code -> {primary, secondary}，用于按主题自动分组。

        首次加载后缓存到模块级 `_classification_cache`（按 file_path 作 key，
        文件路径变了会自动重载），后续调用直接复用，避免每次重读 16KB JSON。
        """
        try:
            import json
            import os
            try:
                from core import config as _cfg
                path = getattr(_cfg, "BOARD_CLASSIFICATION_FILE", None)
            except Exception:
                path = None
            if not path:
                path = os.path.join("static", "board_classification.json")
        except Exception:
            return {}

        cached_path, cached_idx = _classification_cache
        if cached_path == path and cached_idx is not None:
            return cached_idx

        try:
            with open(path, encoding="utf-8") as f:
                doc = json.loads(f.read())
        except Exception:
            # 加载失败也缓存空结果，避免反复尝试打开不存在的文件
            _classification_cache[0] = path
            _classification_cache[1] = {}
            return {}

        cats = doc.get("categories", doc) if isinstance(doc, dict) else doc
        out: Dict[str, Dict[str, str]] = {}

        def take(b, primary, secondary):
            if isinstance(b, dict) and b.get("code"):
                out[b["code"]] = {
                    "primary": b.get("primary_category") or primary or "",
                    "secondary": b.get("secondary_category") or secondary or "",
                    "name": b.get("name") or b["code"],
                }
        for cat in cats or []:
            cname = cat.get("name", "")
            for b in cat.get("boards") or []:
                take(b, cname, "")
            for sub in cat.get("subcategories") or []:
                for b in sub.get("boards") or []:
                    take(b, cname, sub.get("name", ""))
        _classification_cache[0] = path
        _classification_cache[1] = out
        return out

    def auto_groups(self, period: str = None) -> List[Dict[str, Any]]:
        """按现有分类把「有支撑位的标的」自动聚成同方向组。

        分类里查不到的（多为指数/个股）归入 `未分类`，仍可参与同标的跨周期共振。
        """
        members = self.symbols_with_levels(period=period)
        cls = self._classification_index()
        buckets: Dict[str, Dict[str, Any]] = {}
        for m in members:
            info = cls.get(m["symbol"]) or {}
            theme = info.get("secondary") or info.get("primary") or "未分类"
            g = buckets.setdefault(theme, {
                "theme": theme, "auto": True,
                "threshold_pct": 3.0, "min_aligned": 2, "members": [],
            })
            # 按 asset_type 推断 role（仅类型推断，不做成交额排序选龙头 —— 那是 Phase 4 后续）
            at = (m.get("asset_type") or "").lower()
            if at in ("industry", "concept"):
                role = "leader_board"
            elif at == "index":
                role = "sector_index"
            elif at == "stock":
                role = "leader_stock"
            else:
                role = ""  # 未知不强行猜
            m = dict(m, role=role)
            g["members"].append(m)
        return list(buckets.values())

    def scan_all(
        self,
        period: str = None,
        threshold_pct: float = 3.0,
        min_aligned: int = 2,
        only_resonant: bool = True,
    ) -> Dict[str, Any]:
        """全市场扫描：对每个自动组跑确定性共振，按 score 排序。

        带 60 秒 TTL 缓存（key=参数四元组），命中且未过期直接返回浅拷贝，
        避免调用方修改污染缓存。
        """
        import time
        key = (period, threshold_pct, min_aligned, only_resonant)
        now = time.time()
        cached = self._scan_all_cache.get(key)
        if cached is not None:
            ts, result = cached
            if now - ts < _SCAN_ALL_CACHE_TTL:
                return dict(result)  # 浅拷贝，保护缓存

        groups = self.auto_groups(period=period)
        results = []
        for g in groups:
            g = dict(g, threshold_pct=threshold_pct, min_aligned=min_aligned)
            if len(g["members"]) < min_aligned:
                continue          # 成员不够，不可能共振
            r = self.scan(g)
            if only_resonant and not r["is_resonance"]:
                continue
            results.append(r)
        results.sort(key=lambda r: -r["score"])
        result = {
            "scanned_groups": len(groups),
            "resonant_groups": len(results),
            "threshold_pct": threshold_pct,
            "min_aligned": min_aligned,
            "results": results,
            "disclaimer": _DISCLAIMER,
        }
        self._scan_all_cache[key] = (now, result)
        return dict(result)

    def scan_by_id(self, group_id: str) -> Dict[str, Any]:
        g = self.repo.get_group(group_id)
        if not g:
            raise KeyError(group_id)
        return self.scan(g)


_svc: Optional[ResonanceService] = None


def get_resonance_service() -> ResonanceService:
    global _svc
    if _svc is None:
        _svc = ResonanceService()
    return _svc
