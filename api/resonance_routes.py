"""
api/resonance_routes.py — 跨板块共振支撑（Phase 2）
组 CRUD + 确定性扫描。标准由用户定义；不输出买卖结论。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.resonance_service import get_resonance_service

logger = logging.getLogger("resonance_api")

bp = Blueprint("resonance", __name__, url_prefix="")


def _svc():
    return get_resonance_service()


def _err(msg: str, status: int = 400, code: str = None):
    body = {"ok": False, "success": False, "error": msg}
    if code:
        body["code"] = code
    return jsonify(body), status


@bp.route("/api/resonance/groups", methods=["GET"])
def list_groups():
    try:
        items = _svc().list_groups(limit=int(request.args.get("limit", 200)))
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("list_groups")
        return _err(str(e), 500)


@bp.route("/api/resonance/groups", methods=["POST"])
def create_group():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        if not payload:
            return _err("请求体为空")
        g = _svc().create_group(payload)
        return jsonify({"ok": True, "success": True, "data": g}), 201
    except Exception as e:
        logger.exception("create_group")
        return _err(str(e), 500)


@bp.route("/api/resonance/groups/<group_id>", methods=["GET"])
def get_group(group_id: str):
    try:
        g = _svc().get_group(group_id)
        if not g:
            return _err("组不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": g})
    except Exception as e:
        logger.exception("get_group")
        return _err(str(e), 500)


@bp.route("/api/resonance/groups/<group_id>", methods=["PATCH"])
def patch_group(group_id: str):
    try:
        patch = request.get_json(force=True, silent=True) or {}
        g = _svc().update_group(group_id, patch)
        return jsonify({"ok": True, "success": True, "data": g})
    except KeyError:
        return _err("组不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("patch_group")
        return _err(str(e), 500)


@bp.route("/api/resonance/groups/<group_id>", methods=["DELETE"])
def delete_group(group_id: str):
    try:
        _svc().delete_group(group_id)
        return jsonify({"ok": True, "success": True})
    except Exception as e:
        logger.exception("delete_group")
        return _err(str(e), 500)


@bp.route("/api/resonance/auto_groups", methods=["GET"])
def auto_groups():
    """按分类自动聚出的同方向组（范围=已画过支撑位的标的）。"""
    try:
        items = _svc().auto_groups(period=request.args.get("period"))
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("auto_groups")
        return _err(str(e), 500)


@bp.route("/api/resonance/scan_all", methods=["POST", "GET"])
def scan_all():
    """全市场扫描：对每个自动组跑共振，按 score 排序。"""
    try:
        p = request.get_json(force=True, silent=True) if request.method == "POST" else {}
        p = p or {}
        args = request.args
        result = _svc().scan_all(
            period=p.get("period") or args.get("period"),
            threshold_pct=float(p.get("threshold_pct") or args.get("threshold_pct") or 3.0),
            min_aligned=int(p.get("min_aligned") or args.get("min_aligned") or 2),
            only_resonant=str(p.get("only_resonant", args.get("only_resonant", "1"))) not in ("0", "false", "False"),
        )
        return jsonify({"ok": True, "success": True, "data": result})
    except Exception as e:
        logger.exception("scan_all")
        return _err(str(e), 500)


@bp.route("/api/resonance/matrix", methods=["POST"])
def scan_matrix():
    """矩阵扫描：成员 × 周期。支持跨标的与同标的跨周期的统一表达。"""
    try:
        payload = request.get_json(force=True, silent=True) or {}
        periods = payload.get("periods")
        gid = payload.get("group_id")
        if gid:
            result = _svc().scan_matrix_by_id(gid, periods)
        elif payload.get("group") or payload.get("members"):
            result = _svc().scan_matrix(payload.get("group") or payload, periods)
        else:
            return _err("需提供 group_id 或 group/members")
        return jsonify({"ok": True, "success": True, "data": result})
    except KeyError:
        return _err("组不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("scan_matrix")
        return _err(str(e), 500)


@bp.route("/api/resonance/scan", methods=["POST"])
def scan():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        gid = payload.get("group_id")
        if gid:
            result = _svc().scan_by_id(gid)
        elif payload.get("group") or payload.get("members"):
            result = _svc().scan(payload.get("group") or payload)
        else:
            return _err("需提供 group_id 或 group/members")
        return jsonify({"ok": True, "success": True, "data": result})
    except KeyError:
        return _err("组不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("scan")
        return _err(str(e), 500)
