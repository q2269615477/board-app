"""
annotation_service.py — Case(level_origin) + Relation 业务
系统不判定共振/反向；relation_note 仅存用户原文。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.annotation_repo import get_annotation_repo
from services import vault_writer


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    day = datetime.now().strftime("%Y%m%d")
    return f"{prefix}_{day}_{uuid.uuid4().hex[:8]}"


class AnnotationService:
    def __init__(self):
        self.repo = get_annotation_repo()

    def get_config(self) -> Dict[str, Any]:
        from core.config import OBSIDIAN_VAULT_NAME, OBSIDIAN_APP_PATH

        root = vault_writer.vault_root()
        return {
            "vault_path": str(root),
            "vault_writable": vault_writer.vault_writable(),
            "index_path": str(self.repo.db_path),
            "obsidian_vault_name": OBSIDIAN_VAULT_NAME or root.name,
            "obsidian_app_path": OBSIDIAN_APP_PATH,
            "obsidian_uri_supported": True,
        }

    def create_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case = dict(payload)
        case_type = case.get("type") or "chart-annotation"
        case["type"] = case_type

        if case_type == "level_origin":
            case = self._normalize_level_origin(case)

        if not case.get("id"):
            sym = case.get("symbol") or "x"
            case["id"] = _new_id(f"case_{sym}")
        case.setdefault("reactions", [])
        case.setdefault("relation_ids", [])
        case.setdefault("reminders", [])
        case.setdefault("notes", "")
        case.setdefault("created_at", _now())
        case["updated_at"] = _now()

        # 几何 overlays：level_origin 无用户画线时生成默认水平线；chart-annotation 保留采集几何
        if case_type == "level_origin" and not case.get("overlays"):
            case["overlays"] = self._default_level_overlays(case)
        if case_type == "chart-annotation" and not case.get("overlays"):
            raise ValueError("chart-annotation 需要 overlays（请从图上采集画线）")

        # Agent 学习默认：有备注或几何即可检索；不自动判定 outcome
        agent = case.get("agent") or {}
        agent.setdefault("distillable", True)
        agent.setdefault("quality", 3 if (case.get("notes") or "").strip() else 2)
        case["agent"] = agent
        case.setdefault("status", "active")
        case.setdefault("outcome", {"status": "pending"})

        self.repo.upsert_case(case)
        paths = vault_writer.write_case_files(case)
        case["vault"] = paths
        self.repo.upsert_case(case)  # 写回 vault 路径
        return case

    def _normalize_level_origin(self, case: Dict[str, Any]) -> Dict[str, Any]:
        sb = case.get("source_bar") or {}
        ohlc = sb.get("ohlc") or {}
        pe = sb.get("price_element") or case.get("price_element")
        if not pe:
            raise ValueError("level_origin 需要 price_element (open|high|low|close|custom)")
        pe = str(pe).lower()
        if pe != "custom":
            if pe not in ohlc:
                raise ValueError(f"source_bar.ohlc 缺少 {pe}")
            price = float(ohlc[pe])
        else:
            price = float(case.get("level", {}).get("price") or sb.get("price"))
        sb["price_element"] = pe
        sb["price"] = price
        case["source_bar"] = sb
        case["price_element"] = pe
        level = case.get("level") or {}
        level["price"] = price
        level.setdefault("role", "support")
        level.setdefault("status", "active")
        case["level"] = level
        # 校验
        if pe != "custom" and abs(float(level["price"]) - float(ohlc[pe])) > 1e-6:
            raise ValueError("level.price 与源 K 价格要素不一致")
        if not case.get("period"):
            raise ValueError("缺少 period")
        if not case.get("symbol"):
            raise ValueError("缺少 symbol")
        return case

    def _default_level_overlays(self, case: Dict[str, Any]) -> List[Dict]:
        sb = case.get("source_bar") or {}
        lv = case.get("level") or {}
        ts = sb.get("timestamp")
        if ts is None and sb.get("date"):
            import pandas as pd

            ts = int(pd.Timestamp(sb["date"]).timestamp() * 1000)
        price = lv.get("price")
        return [
            {
                "id": f"ov_level_{case['id']}",
                "type": "horizontalLine",
                "role": "level",
                "points": [{"timestamp": ts, "value": price}],
                "styles": {},
            },
            {
                "id": f"ov_src_{case['id']}",
                "type": "marker",
                "role": "source_bar",
                "points": [{"timestamp": ts, "value": price}],
                "label": f"源·{sb.get('price_element')}",
            },
        ]

    def update_case(self, case_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        case = self.repo.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        # 禁止静默改写无关字段策略：浅合并（level 子字段深合并）
        for k, v in patch.items():
            if k in ("id", "created_at"):
                continue
            if k == "level" and isinstance(v, dict) and isinstance(case.get("level"), dict):
                merged = dict(case["level"])
                merged.update(v)
                case[k] = merged
            else:
                case[k] = v
        case["updated_at"] = _now()
        if case.get("type") == "level_origin" and (
            "source_bar" in patch or "price_element" in patch or "level" in patch
        ):
            case = self._normalize_level_origin(case)
            case["overlays"] = self._default_level_overlays(case)
        paths = vault_writer.write_case_files(case)
        case["vault"] = paths
        self.repo.upsert_case(case)
        return case

    def add_reaction(self, case_id: str, reaction: Dict[str, Any]) -> Dict[str, Any]:
        case = self.repo.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        rxs = case.setdefault("reactions", [])
        reaction = dict(reaction)
        reaction.setdefault("id", f"rx_{uuid.uuid4().hex[:6]}")
        rxs.append(reaction)
        # 反应 marker
        ov = case.setdefault("overlays", [])
        ts = reaction.get("timestamp")
        if ts is None and reaction.get("date"):
            import pandas as pd

            ts = int(pd.Timestamp(reaction["date"]).timestamp() * 1000)
        ov.append(
            {
                "id": f"ov_{reaction['id']}",
                "type": "marker",
                "role": "reaction",
                "points": [
                    {"timestamp": ts, "value": reaction.get("price")}
                ],
                "label": reaction.get("kind") or "rx",
            }
        )
        case["updated_at"] = _now()
        paths = vault_writer.write_case_files(case)
        case["vault"] = paths
        self.repo.upsert_case(case)
        return case

    def list_cases(self, **kwargs) -> List[Dict[str, Any]]:
        return self.repo.list_cases(**kwargs)

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get_case(case_id)

    def search_cases(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.repo.search_cases(q, limit=limit)

    def get_overlays(self, case_id: str) -> List[Dict]:
        case = self.repo.get_case(case_id)
        if not case:
            return []
        return case.get("overlays") or []

    # ---- Relation（用户声明，无系统判定）----

    def create_relation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        rel = dict(payload)
        if not rel.get("id"):
            rel["id"] = _new_id("rel")
        rel.setdefault("type", "relation")
        rel.setdefault("members", [])
        rel.setdefault("relation_note", "")
        rel.setdefault("user_tags", [])
        rel.setdefault("notes", "")
        rel.setdefault("reminders", [])
        rel.setdefault("created_at", _now())
        rel["updated_at"] = _now()
        # 禁止自动评分字段
        for banned in (
            "auto_score",
            "correlation",
            "system_hypothesis",
            "verified_by_backtest",
        ):
            rel.pop(banned, None)

        paths = vault_writer.write_relation_files(rel)
        rel["vault"] = paths
        self.repo.upsert_relation(rel)

        # 回写 case.relation_ids
        for m in rel["members"]:
            cid = m.get("case_id")
            if not cid:
                continue
            case = self.repo.get_case(cid)
            if not case:
                continue
            ids = case.setdefault("relation_ids", [])
            if rel["id"] not in ids:
                ids.append(rel["id"])
            case["updated_at"] = _now()
            self.repo.upsert_case(case)
            vault_writer.write_case_files(case)
        return rel

    def update_relation(self, rel_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        rel = self.repo.get_relation(rel_id)
        if not rel:
            raise KeyError(rel_id)
        for k, v in patch.items():
            if k in ("id", "created_at"):
                continue
            if k in (
                "auto_score",
                "correlation",
                "system_hypothesis",
                "verified_by_backtest",
            ):
                continue
            rel[k] = v
        rel["updated_at"] = _now()
        paths = vault_writer.write_relation_files(rel)
        rel["vault"] = paths
        self.repo.upsert_relation(rel)
        return rel

    def get_relation(self, rel_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get_relation(rel_id)

    def search_relations(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.repo.search_relations(q, limit=limit)

    def list_due_reminders(self) -> List[Dict[str, Any]]:
        return self.repo.list_due_reminders()


_svc: Optional[AnnotationService] = None


def get_annotation_service() -> AnnotationService:
    global _svc
    if _svc is None:
        _svc = AnnotationService()
    return _svc
