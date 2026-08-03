"""Write-protection coverage tests.

Ensures every POST route in the Flask app is either:
  - decorated with @write_protected (write-state endpoints), or
  - explicitly listed in READONLY_POST_ALLOWLIST (read-only POST with reason).

Also verifies the runtime behaviour of @write_protected:
  - With BOARD_APP_WRITE_TOKEN set, non-local requests without token → 403.
  - Local requests (127.0.0.1) always pass.
"""
import os
import importlib

import pytest

# ---------------------------------------------------------------------------
# App fixture — create once, reuse across tests.
# ---------------------------------------------------------------------------

_app = None


def _get_app():
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app


# ---------------------------------------------------------------------------
# Allowlist: POST endpoints that are intentionally NOT protected.
#
# Each entry: (rule_string, reason)
# These are read-only POST routes that do not modify persistent state.
# ---------------------------------------------------------------------------

READONLY_POST_ALLOWLIST = {
    # /api/resonance/scan_all — read-only market scan, does not persist
    "/api/resonance/scan_all",
    # /api/resonance/matrix — read-only matrix scan, does not persist
    "/api/resonance/matrix",
    # /api/resonance/scan — read-only group scan, does not persist
    "/api/resonance/scan",
    # /api/levels/propose — read-only proposal computation, does not persist
    "/api/levels/propose",
    # /api/cache/prices — read-only, fetches cached prices from memory
    "/api/cache/prices",
    # /api/qmt/prices — read-only, fetches QMT realtime prices
    "/api/qmt/prices",
    # /api/qmt/kline — read-only, fetches QMT kline data
    "/api/qmt/kline",
    # /api/stream/batch — read-only, streams existing K-line data
    "/api/stream/batch",
}


# ---------------------------------------------------------------------------
# 1. Enumerate all POST rules and verify protection.
# ---------------------------------------------------------------------------

def _post_rules():
    """Return list of (rule_string, endpoint_name) for all POST routes."""
    app = _get_app()
    rules = []
    for rule in app.url_map.iter_rules():
        if "POST" in rule.methods:
            rules.append((rule.rule, rule.endpoint))
    return rules


def test_all_post_routes_are_covered():
    """Every POST route must be either @write_protected or in the allowlist."""
    app = _get_app()
    uncovered = []
    for rule_str, endpoint in _post_rules():
        if rule_str in READONLY_POST_ALLOWLIST:
            continue
        # Look up the view function
        view_func = app.view_functions.get(endpoint)
        if view_func is None:
            uncovered.append((rule_str, endpoint, "view function not found"))
            continue
        # Check for the marker set by @write_protected
        if not getattr(view_func, "_board_app_write_protected", False):
            uncovered.append(
                (rule_str, endpoint, "missing @write_protected")
            )
    assert not uncovered, (
        f"POST routes without write protection or allowlist entry:\n"
        + "\n".join(f"  {r} ({e}): {reason}" for r, e, reason in uncovered)
    )


def test_allowlist_entries_actually_exist():
    """Every allowlist entry must correspond to a real POST route."""
    actual_rules = {rule_str for rule_str, _ in _post_rules()}
    missing = READONLY_POST_ALLOWLIST - actual_rules
    assert not missing, (
        f"Allowlist entries that are not real POST routes: {missing}"
    )


def test_allowlist_routes_are_not_protected():
    """Allowlist routes must NOT have @write_protected (they are read-only)."""
    app = _get_app()
    wrongly_protected = []
    for rule_str, endpoint in _post_rules():
        if rule_str not in READONLY_POST_ALLOWLIST:
            continue
        view_func = app.view_functions.get(endpoint)
        if view_func and getattr(view_func, "_board_app_write_protected", False):
            wrongly_protected.append(rule_str)
    assert not wrongly_protected, (
        f"Read-only allowlist routes that are incorrectly protected: "
        f"{wrongly_protected}"
    )


# ---------------------------------------------------------------------------
# 2. Runtime behaviour: token enforcement.
# ---------------------------------------------------------------------------

def test_non_local_request_blocked_when_token_set():
    """With BOARD_APP_WRITE_TOKEN set, non-local POST without token → 403."""
    old_token = os.environ.get("BOARD_APP_WRITE_TOKEN")
    os.environ["BOARD_APP_WRITE_TOKEN"] = "test-secret-token"
    try:
        app = _get_app()
        # Simulate a non-local request by setting remote_addr to a public IP.
        with app.test_request_context(
            "/api/ctx",
            method="POST",
            json={"code": "sh000001"},
            environ_base={"REMOTE_ADDR": "203.0.113.1"},
        ):
            from api.auth_guard import write_protected

            @write_protected
            def dummy():
                return "ok"

            from flask import jsonify

            # Re-wrap to get the actual response
            resp = dummy()
            # dummy returns a tuple (jsonify(...), 403) when blocked
            if isinstance(resp, tuple):
                status = resp[1]
            else:
                status = resp.status_code if hasattr(resp, "status_code") else 200
            assert status == 403, (
                f"Expected 403 for non-local request without token, got {status}"
            )
    finally:
        if old_token is None:
            os.environ.pop("BOARD_APP_WRITE_TOKEN", None)
        else:
            os.environ["BOARD_APP_WRITE_TOKEN"] = old_token


def test_local_request_passes_when_token_set():
    """With BOARD_APP_WRITE_TOKEN set, local POST (127.0.0.1) still passes."""
    old_token = os.environ.get("BOARD_APP_WRITE_TOKEN")
    os.environ["BOARD_APP_WRITE_TOKEN"] = "test-secret-token"
    try:
        app = _get_app()
        with app.test_request_context(
            "/api/ctx",
            method="POST",
            json={"code": "sh000001"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            from api.auth_guard import write_protected

            @write_protected
            def dummy():
                return "ok"

            result = dummy()
            assert result == "ok", (
                f"Local request should pass, got: {result}"
            )
    finally:
        if old_token is None:
            os.environ.pop("BOARD_APP_WRITE_TOKEN", None)
        else:
            os.environ["BOARD_APP_WRITE_TOKEN"] = old_token


def test_no_token_means_no_protection():
    """Without BOARD_APP_WRITE_TOKEN, all requests pass (default open)."""
    old_token = os.environ.get("BOARD_APP_WRITE_TOKEN")
    os.environ.pop("BOARD_APP_WRITE_TOKEN", None)
    try:
        app = _get_app()
        with app.test_request_context(
            "/api/ctx",
            method="POST",
            json={"code": "sh000001"},
            environ_base={"REMOTE_ADDR": "203.0.113.1"},
        ):
            from api.auth_guard import write_protected

            @write_protected
            def dummy():
                return "ok"

            result = dummy()
            assert result == "ok", (
                f"Without token, request should pass, got: {result}"
            )
    finally:
        if old_token is not None:
            os.environ["BOARD_APP_WRITE_TOKEN"] = old_token
