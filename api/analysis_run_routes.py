"""
api/analysis_run_routes.py — 分析场次（一个绘画面板 = 一场关联分析）

自动留痕：标的/周期切换、画出的水平位、反应点、共振扫描；按日历归档。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.analysis_run_service import get_analysis_run_service

logger = logging.getLogger("analysis_run_api")

bp = Blueprint("analysis_run", __name__, url_prefix="")


def _svc():
    return get_analysis_run_service()


def _err(msg: str, status: int = 400, code: str = None):
    body = {"ok": False, "success": False, "error": msg}
    if code:
        body["code"] = code
    return jsonify(body), status


@bp.route("/api/runs/current", methods=["GET"])
def current_run():
    try:
        run = _svc().current(auto_create=request.args.get("create", "1") != "0")
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("current_run")
        return _err(str(e), 500)


@bp.route("/api/runs", methods=["POST"])
def start_run():
    try:
        p = request.get_json(force=True, silent=True) or {}
        run = _svc().start(title=p.get("title", ""), theme=p.get("theme", ""))
        return jsonify({"ok": True, "success": True, "data": run}), 201
    except Exception as e:
        logger.exception("start_run")
        return _err(str(e), 500)


@bp.route("/api/runs/calendar", methods=["GET"])
def runs_calendar():
    """按今天/昨天/更早分组的日历目录。"""
    try:
        data = _svc().list_by_date(limit=int(request.args.get("limit", 300)))
        return jsonify({"ok": True, "success": True, "data": data})
    except Exception as e:
        logger.exception("runs_calendar")
        return _err(str(e), 500)


@bp.route("/api/runs/<run_id>", methods=["GET"])
def get_run(run_id: str):
    try:
        run = _svc().repo.get_run(run_id)
        if not run:
            return _err("场次不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("get_run")
        return _err(str(e), 500)


@bp.route("/api/runs/<run_id>", methods=["PATCH"])
def patch_run(run_id: str):
    try:
        run = _svc().update(run_id, request.get_json(force=True, silent=True) or {})
        if not run:
            return _err("场次不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("patch_run")
        return _err(str(e), 500)


@bp.route("/api/runs/<run_id>/close", methods=["POST"])
def close_run(run_id: str):
    try:
        run = _svc().close(run_id)
        if not run:
            return _err("场次不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("close_run")
        return _err(str(e), 500)


@bp.route("/api/runs/<run_id>", methods=["DELETE"])
def delete_run(run_id: str):
    try:
        if not _svc().repo.delete_run(run_id):
            return _err("场次不存在", 404, "NOT_FOUND")
        return jsonify({"ok": True, "success": True, "deleted": run_id})
    except Exception as e:
        logger.exception("delete_run")
        return _err(str(e), 500)


@bp.route("/api/runs/visit", methods=["POST"])
def record_visit():
    """前端切标的/周期时上报，自动并入当前场次。"""
    try:
        p = request.get_json(force=True, silent=True) or {}
        if not p.get("symbol"):
            return _err("缺少 symbol")
        run = _svc().record_visit(
            p["symbol"], p.get("period", "daily"),
            p.get("symbol_name", ""), p.get("asset_type", ""), p.get("run_id"),
        )
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("record_visit")
        return _err(str(e), 500)


@bp.route("/api/runs/scan", methods=["POST"])
def record_scan():
    try:
        p = request.get_json(force=True, silent=True) or {}
        run = _svc().record_scan(p.get("summary") or p, p.get("run_id"))
        return jsonify({"ok": True, "success": True, "data": run})
    except Exception as e:
        logger.exception("record_scan")
        return _err(str(e), 500)
