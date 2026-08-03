"""
analysis_run_service.py — 分析场次：一个绘画面板 = 一场完整的关联分析

用户的一次分析天然是跨图的：在 A 标的画支撑 → 切到 B 标的画支撑 → 再切周期看，
这些动作属于**同一场分析**。本服务把这条轨迹自动记录下来：

- `visits[]`   期间访问过的 (标的, 周期) 及时间，切换即留痕
- `levels[]`   期间画下的水平位（level_origin id + 摘要）
- `reactions[]`期间标注的反应点
- `scans[]`    期间做过的共振扫描结果摘要

按 `day` 归档，形成可按今天/昨天/更早浏览的长期数据。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from data.annotation_repo import get_annotation_repo


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class AnalysisRunService:
    def __init__(self):
        self.repo = get_annotation_repo()
        self._create_lock = threading.RLock()   # 防并发建出多个 open 场次

    # ---- 生命周期 ----

    def start(self, title: str = "", theme: str = "", close_others: bool = True) -> Dict[str, Any]:
        # 开新场先关掉其它 open 场次，否则 latest_open_run 会在多个 open 之间
        # 按 updated_at 摇摆，同一场分析的轨迹被劈成两半
        if close_others:
            try:
                for r in self.repo.list_runs(limit=50):
                    if r.get("status") == "open":
                        r["status"] = "closed"
                        r["closed_at"] = _now()
                        self.repo.upsert_run(r)
            except Exception:
                pass
        run = {
            "id": f"run_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}",
            "title": title or f"分析 {datetime.now().strftime('%m-%d %H:%M')}",
            "theme": theme,
            "day": _today(),
            "status": "open",
            "visits": [], "levels": [], "reactions": [], "scans": [],
            "notes": "",
            "created_at": _now(),
        }
        self.repo.upsert_run(run)
        return run

    def current(self, auto_create: bool = True) -> Optional[Dict[str, Any]]:
        """当前进行中的场次；跨天则自动开新场（避免昨天的分析混进今天）。

        建场必须加锁并二次确认：前端的 refreshRunBox 与 recordVisit 会并发命中
        `latest_open_run() is None`，无锁时会建出两个 open 场次。
        """
        run = self.repo.latest_open_run()
        if run and run.get("day") != _today():
            self.close(run["id"])
            run = None
        if run or not auto_create:
            return run
        with self._create_lock:
            run = self.repo.latest_open_run()       # double-check
            if run and run.get("day") == _today():
                return run
            if run:
                self.close(run["id"])
            return self.start()

    def close(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.repo.get_run(run_id)
        if not run:
            return None
        run["status"] = "closed"
        run["closed_at"] = _now()
        self.repo.upsert_run(run)
        return run

    def update(self, run_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        run = self.repo.get_run(run_id)
        if not run:
            return None
        for k, v in patch.items():
            if k in ("id", "created_at", "day"):
                continue
            run[k] = v
        self.repo.upsert_run(run)
        return run

    # ---- 自动留痕 ----

    def _apply(self, run_id: Optional[str], mutator) -> Dict[str, Any]:
        """统一的事务化追加：定位当前场次 → 在事务内读改写。

        全部追加操作都走这里，避免各自 `get_run → 改 → upsert` 造成
        后写覆盖前写的丢更新（前端 recordVisit / 画线上报 / 扫描上报会并发）。
        """
        if not run_id:
            cur = self.current()
            if not cur:
                return {}
            run_id = cur["id"]
        return self.repo.mutate_run(run_id, mutator) or {}

    def record_visit(self, symbol: str, period: str, symbol_name: str = "",
                     asset_type: str = "", run_id: str = None) -> Dict[str, Any]:
        """记录一次标的/周期切换。连续重复的同一 (标的,周期) 不重复记。"""
        def _m(run):
            visits = run.setdefault("visits", [])
            if visits and visits[-1].get("symbol") == symbol and visits[-1].get("period") == period:
                visits[-1]["at"] = _now()      # 仍停留在同一张图，只更新时间
            else:
                visits.append({
                    "symbol": symbol, "symbol_name": symbol_name or symbol,
                    "period": period, "asset_type": asset_type, "at": _now(),
                })
        return self._apply(run_id, _m)

    def record_level(self, case: Dict[str, Any], run_id: str = None) -> Dict[str, Any]:
        lv = case.get("level") or {}
        entry = {
            "case_id": case.get("id"),
            "symbol": case.get("symbol"),
            "symbol_name": case.get("symbol_name") or case.get("symbol"),
            "period": case.get("period"),
            "role": lv.get("role"), "price": lv.get("price"),
            "source_date": (case.get("source_bar") or {}).get("date"),
            "notes": case.get("notes") or "",
            "at": _now(),
        }

        def _m(run):
            levels = run.setdefault("levels", [])
            if not any(x.get("case_id") == entry["case_id"] for x in levels):
                levels.append(entry)
        return self._apply(run_id, _m)

    def record_reaction(self, case_id: str, symbol: str, price: float,
                        date: str = "", run_id: str = None) -> Dict[str, Any]:
        def _m(run):
            run.setdefault("reactions", []).append({
                "case_id": case_id, "symbol": symbol, "price": price,
                "date": date, "at": _now(),
            })
        return self._apply(run_id, _m)

    def record_scan(self, summary: Dict[str, Any], run_id: str = None) -> Dict[str, Any]:
        # 兼容列表版(scan)与矩阵版(scan_matrix)两种结果形状
        entry = {
            "at": _now(),
            "is_resonance": summary.get("is_resonance"),
            "theme": summary.get("theme"),
            "aligned": summary.get("aligned_count"),
            "members": summary.get("member_count"),
            "periods": summary.get("periods"),
            "resonant_periods": summary.get("resonant_periods"),
            "score": summary.get("score"),
            "lit_cells": summary.get("lit_cells"),
            "total_cells": summary.get("total_cells"),
        }

        def _m(run):
            run.setdefault("scans", []).append(entry)
        return self._apply(run_id, _m)

    def remove_level(self, case_id: str, run_id: str = None) -> Dict[str, Any]:
        """标注被删除时，从**进行中**的场次里同步移除，避免悬挂引用。

        历史场次保留原样（那是当时的真实留痕）。
        """
        def _m(run):
            run["levels"] = [x for x in (run.get("levels") or [])
                             if x.get("case_id") != case_id]
            run["reactions"] = [x for x in (run.get("reactions") or [])
                                if x.get("case_id") != case_id]
        cur = self.repo.get_run(run_id) if run_id else self.repo.latest_open_run()
        if not cur:
            return {}
        return self.repo.mutate_run(cur["id"], _m) or {}

    # ---- 按日历归档 ----

    def list_by_date(self, limit: int = 300) -> Dict[str, Any]:
        """按今天 / 昨天 / 更早（按日）分组，形成日历目录。"""
        runs = self.repo.list_runs(limit=limit)
        today = _today()
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for r in runs:
            buckets.setdefault(r.get("day") or "未知", []).append(self._summary(r))
        groups = []
        for day in sorted(buckets.keys(), reverse=True):
            label = "今天" if day == today else ("昨天" if day == yesterday else day)
            groups.append({"day": day, "label": label, "runs": buckets[day],
                           "count": len(buckets[day])})
        return {"groups": groups, "total": len(runs), "today": today}

    def _summary(self, r: Dict[str, Any]) -> Dict[str, Any]:
        visits = r.get("visits") or []
        return {
            "id": r.get("id"), "title": r.get("title"), "theme": r.get("theme"),
            "day": r.get("day"), "status": r.get("status"),
            "visit_count": len(visits),
            "symbols": list(dict.fromkeys([v.get("symbol") for v in visits if v.get("symbol")]))[:8],
            "level_count": len(r.get("levels") or []),
            "reaction_count": len(r.get("reactions") or []),
            "scan_count": len(r.get("scans") or []),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        }


_svc: Optional[AnalysisRunService] = None


def get_analysis_run_service() -> AnalysisRunService:
    global _svc
    if _svc is None:
        _svc = AnalysisRunService()
    return _svc
