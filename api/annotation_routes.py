"""
api/annotation_routes.py — Case / Relation / Reminder / vault 配置
双写：SQLite 索引 + Obsidian vault 文件夹（非插件）。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.annotation_service import get_annotation_service
from api.auth_guard import write_protected

logger = logging.getLogger("annotation_api")

bp = Blueprint("annotation", __name__, url_prefix="")


def _svc():
    return get_annotation_service()


def _err(msg: str, status: int = 400, code: str = None):
    body = {"ok": False, "success": False, "error": msg}
    if code:
        body["code"] = code
    return jsonify(body), status


@bp.route("/api/annotations/config", methods=["GET"])
def annotations_config():
    try:
        cfg = _svc().get_config()
        return jsonify({"ok": True, "success": True, "data": cfg})
    except Exception as e:
        logger.exception("config")
        return _err(str(e), 500)


@bp.route("/api/annotations", methods=["GET"])
def list_annotations():
    try:
        symbol = request.args.get("symbol")
        period = request.args.get("period")
        type_ = request.args.get("type")
        q = request.args.get("q")
        limit = int(request.args.get("limit", 100))
        svc = _svc()
        if q:
            items = svc.search_cases(q, limit=limit)
        else:
            items = svc.list_cases(
                symbol=symbol, period=period, type_=type_, limit=limit
            )
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("list_annotations")
        return _err(str(e), 500)


@bp.route("/api/annotations/counts", methods=["GET"])
def annotation_counts():
    """Per-symbol annotation counts for the classification nav badges."""
    try:
        conn = _svc().repo._conn()
        rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS n
            FROM cases
            WHERE symbol IS NOT NULL AND symbol != ''
            GROUP BY symbol
            """
        ).fetchall()
        data = {row["symbol"]: int(row["n"]) for row in rows}
        return jsonify({"ok": True, "success": True, "data": data})
    except Exception as e:
        logger.exception("annotation_counts")
        return _err(str(e), 500)


@bp.route("/api/annotations", methods=["POST"])
@write_protected
def create_annotation():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        if not payload:
            return _err("请求体为空")
        case = _svc().create_case(payload)
        return jsonify({"ok": True, "success": True, "data": case}), 201
    except ValueError as e:
        return _err(str(e), 400, "VALIDATION")
    except Exception as e:
        logger.exception("create_annotation")
        return _err(str(e), 500)


@bp.route("/api/annotations/<case_id>", methods=["GET"])
def get_annotation(case_id: str):
    try:
        case = _svc().get_case(case_id)
        if not case:
            return _err("case 不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": case})
    except Exception as e:
        logger.exception("get_annotation")
        return _err(str(e), 500)


@bp.route("/api/annotations/<case_id>", methods=["PATCH"])
def patch_annotation(case_id: str):
    try:
        patch = request.get_json(force=True, silent=True) or {}
        case = _svc().update_case(case_id, patch)
        return jsonify({"ok": True, "success": True, "data": case})
    except KeyError:
        return _err("case 不存在", 404, "NOT_FOUND")
    except ValueError as e:
        return _err(str(e), 400, "VALIDATION")
    except Exception as e:
        logger.exception("patch_annotation")
        return _err(str(e), 500)


@bp.route("/api/annotations/<case_id>/reactions", methods=["POST"])
@write_protected
def add_reaction(case_id: str):
    try:
        reaction = request.get_json(force=True, silent=True) or {}
        if not reaction:
            return _err("reaction 体为空")
        case = _svc().add_reaction(case_id, reaction)
        return jsonify({"ok": True, "success": True, "data": case})
    except KeyError:
        return _err("case 不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("add_reaction")
        return _err(str(e), 500)


@bp.route("/api/annotations/<case_id>/overlays", methods=["GET"])
def get_overlays(case_id: str):
    try:
        ov = _svc().get_overlays(case_id)
        if _svc().get_case(case_id) is None:
            return _err("case 不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": ov})
    except Exception as e:
        logger.exception("get_overlays")
        return _err(str(e), 500)


@bp.route("/api/relations", methods=["GET"])
def list_relations():
    try:
        q = request.args.get("q")
        limit = int(request.args.get("limit", 100))
        svc = _svc()
        if q:
            items = svc.search_relations(q, limit=limit)
        else:
            items = svc.repo.list_relations(limit=limit)
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("list_relations")
        return _err(str(e), 500)


@bp.route("/api/relations", methods=["POST"])
@write_protected
def create_relation():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        if not payload:
            return _err("请求体为空")
        # 强制：系统不接受自动判定字段
        for banned in (
            "auto_score",
            "correlation",
            "system_hypothesis",
            "verified_by_backtest",
        ):
            payload.pop(banned, None)
        rel = _svc().create_relation(payload)
        return jsonify({"ok": True, "success": True, "data": rel}), 201
    except Exception as e:
        logger.exception("create_relation")
        return _err(str(e), 500)


@bp.route("/api/relations/<rel_id>", methods=["GET"])
def get_relation(rel_id: str):
    try:
        rel = _svc().get_relation(rel_id)
        if not rel:
            return _err("relation 不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": rel})
    except Exception as e:
        logger.exception("get_relation")
        return _err(str(e), 500)


@bp.route("/api/relations/<rel_id>", methods=["PATCH"])
def patch_relation(rel_id: str):
    try:
        patch = request.get_json(force=True, silent=True) or {}
        rel = _svc().update_relation(rel_id, patch)
        return jsonify({"ok": True, "success": True, "data": rel})
    except KeyError:
        return _err("relation 不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("patch_relation")
        return _err(str(e), 500)


@bp.route("/api/relations/<rel_id>/members", methods=["POST"])
@write_protected
def add_relation_member(rel_id: str):
    try:
        member = request.get_json(force=True, silent=True) or {}
        if not member:
            return _err("member 体为空")
        rel = _svc().get_relation(rel_id)
        if not rel:
            return _err("relation 不存在", 404, "NOT_FOUND")
        members = list(rel.get("members") or [])
        members.append(member)
        rel = _svc().update_relation(rel_id, {"members": members})
        # 若带 case_id，回写 case.relation_ids
        cid = member.get("case_id")
        if cid:
            case = _svc().get_case(cid)
            if case:
                ids = case.setdefault("relation_ids", [])
                if rel_id not in ids:
                    ids.append(rel_id)
                    _svc().update_case(cid, {"relation_ids": ids})
        return jsonify({"ok": True, "success": True, "data": rel})
    except Exception as e:
        logger.exception("add_relation_member")
        return _err(str(e), 500)


@bp.route("/api/reminders/due", methods=["GET"])
def due_reminders():
    try:
        items = _svc().list_due_reminders()
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("due_reminders")
        return _err(str(e), 500)
