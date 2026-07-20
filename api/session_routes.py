# -*- coding: utf-8 -*-
"""会话 / 图表 / 因果 API"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.session_service import (
    ReadOnlySessionError,
    RevisionConflictError,
    get_session_service,
)

logger = logging.getLogger("session_api")
bp = Blueprint("session", __name__)


def _svc():
    return get_session_service()


def _err(msg: str, status: int = 400, code: str = None):
    body = {"ok": False, "success": False, "error": msg}
    if code:
        body["code"] = code
    return jsonify(body), status


def _ok(data=None, **extra):
    body = {"ok": True, "success": True, "data": data}
    body.update(extra)
    return jsonify(body)


def _readonly_resp(err: ReadOnlySessionError):
    return (
        jsonify(
            {
                "ok": False,
                "success": False,
                "code": "COMMITTED_READONLY",
                "error": str(err),
                "current_status": err.status,
                "session_id": err.session_id,
                "hint": "请调用 POST /api/sessions/<id>/clone 创建可编辑副本",
            }
        ),
        409,
    )


@bp.route("/api/sessions", methods=["GET"])
def list_sessions():
    try:
        limit = int(request.args.get("limit", 50))
        items = _svc().list_sessions(limit=limit)
        active = _svc().repo.get_active_id()
        return _ok(items, active_id=active, count=len(items))
    except Exception as e:
        logger.exception("list_sessions")
        return _err(str(e), 500)


@bp.route("/api/sessions", methods=["POST"])
def create_session():
    try:
        body = request.get_json(force=True, silent=True) or {}
        # 若带 payload，先存旧的
        if body.get("save_payload") and body["save_payload"].get("id"):
            _svc().save_progress(body["save_payload"], write_vault=True)
        sess = _svc().create_session(title=body.get("title"))
        return _ok(sess), 201
    except Exception as e:
        logger.exception("create_session")
        return _err(str(e), 500)


@bp.route("/api/sessions/active", methods=["GET"])
def get_active():
    try:
        sess = _svc().get_active()
        return _ok(sess)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    try:
        sess = _svc().get_session(session_id)
        if not sess:
            return _err("会话不存在", 404, "NOT_FOUND")
        return _ok(sess)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>/activate", methods=["POST"])
def activate_session(session_id: str):
    try:
        body = request.get_json(force=True, silent=True) or {}
        if body.get("save_payload") and body["save_payload"].get("id"):
            _svc().save_progress(body["save_payload"], write_vault=True)
        sess = _svc().activate_session(session_id)
        return _ok(sess)
    except KeyError:
        return _err("会话不存在", 404, "NOT_FOUND")
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>/clone", methods=["POST"])
def clone_session(session_id: str):
    """基于已有会话（含 committed）克隆为新 drafting 会话。"""
    try:
        cloned = _svc().clone_session(session_id)
        return _ok(cloned), 201
    except KeyError:
        return _err("会话不存在", 404, "NOT_FOUND")
    except Exception as e:
        logger.exception("clone_session")
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>/replay", methods=["GET"])
def replay_session(session_id: str):
    """会话回放数据：返回当前 chart 及其 overlays / visible_range。

    Query:
    - chart_id: 指定要回放的 chart（缺省：current_chart_id；再无则取 charts[0]）
    - event_id: 指定事件，响应里附带该事件的 elements（kind/kbar/overlay/note）

    响应：
    { ok, data: { chart, overlays, visible_range, elements } }
    """
    try:
        sess = _svc().get_session(session_id)
        if not sess:
            return _err("会话不存在", 404, "NOT_FOUND")
        chart_id = request.args.get("chart_id")
        event_id = request.args.get("event_id")

        # 选择 chart
        chart = None
        charts = sess.get("charts") or []
        if chart_id:
            chart = next((c for c in charts if c.get("id") == chart_id), None)
        if not chart and sess.get("current_chart_id"):
            chart = next(
                (c for c in charts if c.get("id") == sess["current_chart_id"]), None
            )
        if not chart and charts:
            chart = charts[0]

        overlays = (chart or {}).get("overlays") or []
        visible_range = (chart or {}).get("visible_range")

        # event 元素（如指定）
        elements = []
        if event_id:
            ev = next(
                (e for e in (sess.get("events") or []) if e.get("id") == event_id),
                None,
            )
            if ev:
                elements = ev.get("elements") or []

        return _ok(
            {
                "chart": chart,
                "overlays": overlays,
                "visible_range": visible_range,
                "elements": elements,
            }
        )
    except Exception as e:
        logger.exception("replay_session")
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>", methods=["PUT", "PATCH"])
def save_session(session_id: str):
    """保存进度：body 为完整 session payload。

    乐观锁：若 body 含 base_rev，与服务端现有 rev 比对，不等则 409 + REVISION_CONFLICT。
    未传 base_rev 时向后兼容（不校验）。
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        body["id"] = session_id
        write_vault = body.pop("write_vault", True)
        if isinstance(write_vault, str):
            write_vault = write_vault.lower() not in ("0", "false", "no")
        # 提取 base_rev（不入库）：body 顶层 / session 字段 都支持
        expected_rev = body.pop("base_rev", None)
        if expected_rev is None and isinstance(body.get("session"), dict):
            expected_rev = body["session"].pop("base_rev", None)
        if expected_rev is not None:
            try:
                expected_rev = int(expected_rev)
            except (TypeError, ValueError):
                return _err("base_rev 必须是整数", 400, "VALIDATION")
        sess = _svc().save_progress(
            body, write_vault=bool(write_vault), expected_rev=expected_rev
        )
        return _ok(sess)
    except ReadOnlySessionError as e:
        return _readonly_resp(e)
    except RevisionConflictError as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "success": False,
                    "code": "REVISION_CONFLICT",
                    "error": str(e),
                    "current_rev": e.current_rev,
                    "current_session": e.current_session,
                }
            ),
            409,
        )
    except ValueError as e:
        return _err(str(e), 400, "VALIDATION")
    except Exception as e:
        logger.exception("save_session")
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>/commit", methods=["POST"])
def commit_session(session_id: str):
    try:
        body = request.get_json(force=True, silent=True) or {}
        payload = body.get("session") if isinstance(body.get("session"), dict) else body
        if isinstance(payload, dict) and (
            "charts" in payload or "causes" in payload or "effects" in payload
        ):
            payload = dict(payload)
            payload["id"] = session_id
            expected_rev = payload.pop("base_rev", None) or body.get("base_rev")
            if expected_rev is not None:
                try:
                    expected_rev = int(expected_rev)
                except (TypeError, ValueError):
                    return _err("base_rev 必须是整数", 400, "VALIDATION")
            sess = _svc().commit_session(session_id, payload, expected_rev=expected_rev)
        else:
            expected_rev = body.get("base_rev")
            if expected_rev is not None:
                try:
                    expected_rev = int(expected_rev)
                except (TypeError, ValueError):
                    return _err("base_rev 必须是整数", 400, "VALIDATION")
            sess = _svc().commit_session(
                session_id, None, expected_rev=expected_rev
            )
        return _ok(sess)
    except ReadOnlySessionError as e:
        return _readonly_resp(e)
    except RevisionConflictError as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "success": False,
                    "code": "REVISION_CONFLICT",
                    "error": str(e),
                    "current_rev": e.current_rev,
                    "current_session": e.current_session,
                }
            ),
            409,
        )
    except KeyError:
        return _err("会话不存在", 404)
    except Exception as e:
        logger.exception("commit_session")
        return _err(str(e), 500)


@bp.route("/api/sessions/<session_id>/actions", methods=["POST"])
def session_action(session_id: str):
    """
    统一动作 body: { action, session?, ...args }
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        action = body.get("action")
        sess = body.get("session") or _svc().get_session(session_id)
        if not sess:
            return _err("会话不存在", 404)
        sess = dict(sess)
        sess["id"] = session_id
        svc = _svc()
        known = True

        if action in ("major_cause", "root_cause", "create_cause_root"):
            sess = svc.create_cause(
                sess, parent_id=None, title=body.get("title") or "", as_root=True
            )
        elif action in ("minor_cause", "child_cause", "create_cause"):
            sess = svc.create_cause(
                sess,
                parent_id=body.get("parent_id"),
                title=body.get("title") or "",
                as_root=False,
            )
        elif action == "focus_cause":
            sess = svc.focus_cause(sess, body.get("cause_id") or "")
        elif action == "focus_event":
            sess = svc.focus_event(sess, body.get("event_id") or "")
        elif action == "focus_element":
            ui = sess.setdefault("ui", {})
            event_id = body.get("event_id") or ui.get("active_event_id")
            if event_id:
                ui["active_event_id"] = event_id
                ev = next(
                    (e for e in (sess.get("events") or []) if e.get("id") == event_id),
                    None,
                )
                if ev and ev.get("cause_id"):
                    ui["active_cause_id"] = ev["cause_id"]
            ui["active_element_id"] = body.get("element_id")
        elif action == "start_event":
            sess = svc.start_event(
                sess,
                cause_id=body.get("cause_id"),
                side=body.get("side"),
                title=body.get("title") or "",
            )
        elif action == "click_effect":
            sess = svc.click_effect(sess, effect_id=body.get("effect_id"))
        elif action in ("delete_event", "remove_event"):
            sess = svc.delete_event(sess, body.get("event_id") or "")
        elif action in ("delete_element", "remove_element"):
            sess = svc.delete_element(
                sess,
                event_id=body.get("event_id"),
                element_id=body.get("element_id") or "",
            )
        elif action in ("delete_cause", "remove_cause", "delete_chain"):
            sess = svc.delete_cause(
                sess,
                body.get("cause_id") or "",
                recursive=body.get("recursive", True) is not False,
            )
        elif action == "note":
            sess = svc.append_note(sess, body.get("text") or "", target=body.get("target"))
        elif action == "kbars":
            sess = svc.append_kbars(sess, body.get("kbars") or [], side=body.get("side"))
        elif action == "overlays":
            sess = svc.set_overlays_on_active(
                sess,
                body.get("overlays") or [],
                chart_id=body.get("chart_id"),
                side=body.get("side"),
            )
        elif action == "ensure_chart":
            sess, ch = svc.ensure_chart(
                sess,
                symbol=body.get("symbol") or "",
                period=body.get("period") or "daily",
                symbol_name=body.get("symbol_name") or "",
                asset_type=body.get("asset_type") or "",
            )
        elif action == "update_chart":
            sess = svc.update_chart(sess, body.get("chart_id"), body.get("patch") or {})
        elif action == "set_ui":
            ui = sess.setdefault("ui", {})
            for k in (
                "tool",
                "side",
                "active_cause_id",
                "active_effect_id",
                "active_event_id",
                "active_element_id",
            ):
                if k in body:
                    ui[k] = body[k]
        else:
            known = False

        if not known:
            return _err(f"未知 action: {action}")

        write_vault = body.get("write_vault", False)
        sess = svc.save_progress(sess, write_vault=bool(write_vault))
        return _ok(sess)
    except ReadOnlySessionError as e:
        return _readonly_resp(e)
    except ValueError as e:
        return _err(str(e), 400, "VALIDATION")
    except KeyError as e:
        return _err(str(e), 404)
    except Exception as e:
        logger.exception("session_action")
        return _err(str(e), 500)
