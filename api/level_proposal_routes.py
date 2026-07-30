"""
api/level_proposal_routes.py — Phase 3：候选支撑位提议与反馈闭环

系统只提议、不判定：accept 才落成正式 level_origin；reject 记为负样本。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.level_proposal_service import get_level_proposal_service

logger = logging.getLogger("level_proposal_api")

bp = Blueprint("level_proposal", __name__, url_prefix="")


def _err(msg: str, status: int = 400, code: str = None):
    body = {"ok": False, "success": False, "error": msg}
    if code:
        body["code"] = code
    return jsonify(body), status


@bp.route("/api/levels/propose", methods=["POST", "GET"])
def propose_levels():
    try:
        if request.method == "GET":
            symbol = request.args.get("symbol")
            period = request.args.get("period", "daily")
            top_n = int(request.args.get("top_n", 5))
        else:
            p = request.get_json(force=True, silent=True) or {}
            symbol = p.get("symbol")
            period = p.get("period", "daily")
            top_n = int(p.get("top_n", 5))
        if not symbol:
            return _err("缺少 symbol")
        data = get_level_proposal_service().propose(symbol, period, top_n=top_n)
        return jsonify({"ok": True, "success": True, "data": data})
    except Exception as e:
        logger.exception("propose_levels")
        return _err(str(e), 500)


@bp.route("/api/levels/style", methods=["GET"])
def user_style():
    """当前用户画法画像（样本数 / 偏好 / 置信度）。"""
    try:
        s = get_level_proposal_service().user_style(
            request.args.get("symbol"), request.args.get("period")
        )
        return jsonify({"ok": True, "success": True, "data": s})
    except Exception as e:
        logger.exception("user_style")
        return _err(str(e), 500)


@bp.route("/api/levels/feedback", methods=["POST"])
def proposal_feedback():
    """accept → 落成 level_origin；reject → 记负样本（供后续校准画法）。"""
    try:
        p = request.get_json(force=True, silent=True) or {}
        cand = p.get("candidate") or {}
        verdict = (p.get("verdict") or "").lower()
        if verdict not in ("accepted", "rejected"):
            return _err("verdict 必须是 accepted / rejected")
        if not cand.get("symbol") or cand.get("price") is None:
            return _err("candidate 需含 symbol 与 price")

        svc = get_level_proposal_service()
        case_id = None
        if verdict == "accepted":
            from services.annotation_service import get_annotation_service
            sb = dict(cand.get("source_bar") or {})
            case = get_annotation_service().create_case({
                "type": "level_origin",
                "symbol": cand["symbol"],
                "symbol_name": cand.get("symbol_name") or cand["symbol"],
                "asset_type": cand.get("asset_type") or "",
                "period": cand.get("period", "daily"),
                "source_bar": sb,
                "level": {"role": cand.get("role", "support"), "status": "active"},
                "notes": p.get("note") or "（AI 提议，用户确认）",
                "feature_snapshot": cand.get("feature_snapshot") or {},
                "origin": "ai_proposal",     # 标注来源，便于日后区分自画/确认
            })
            case_id = case["id"]

        svc.repo.record_proposal_feedback({
            "symbol": cand.get("symbol"), "period": cand.get("period", "daily"),
            "price": cand.get("price"), "role": cand.get("role"),
            "verdict": verdict, "reason": p.get("reason") or "",
            "case_id": case_id, "candidate": cand,
        })
        return jsonify({"ok": True, "success": True, "verdict": verdict, "case_id": case_id})
    except Exception as e:
        logger.exception("proposal_feedback")
        return _err(str(e), 500)


@bp.route("/api/levels/feedback", methods=["GET"])
def list_feedback():
    try:
        items = get_level_proposal_service().repo.list_proposal_feedback(
            symbol=request.args.get("symbol"),
            verdict=request.args.get("verdict"),
            limit=int(request.args.get("limit", 200)),
        )
        return jsonify({"ok": True, "success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.exception("list_feedback")
        return _err(str(e), 500)
