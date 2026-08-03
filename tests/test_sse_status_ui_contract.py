from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SSE_CLIENT = (ROOT / "static" / "js" / "sse-client.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_sse_reconnect_uses_stable_status_indicator_without_recovery_toast():
    assert "id = 'sse-status-indicator'" in SSE_CLIENT
    assert "function _setSseStatus(state)" in SSE_CLIENT
    assert "el.dataset.state = state" in SSE_CLIENT
    assert "_setSseStatus('connected')" in SSE_CLIENT
    assert "toast('实时推送已恢复')" not in SSE_CLIENT


def test_sse_status_indicator_has_persistent_and_accessible_state():
    assert "setAttribute('role', 'status')" in SSE_CLIENT
    assert "setAttribute('aria-live', 'polite')" in SSE_CLIENT
    assert "data-state=\"connected\"" in APP_CSS
    assert "data-state=\"reconnecting\"" in APP_CSS
    assert "data-state=\"disconnected\"" in APP_CSS


def test_data_update_and_error_feedback_paths_remain_present():
    assert "toast(payload.message)" in SSE_CLIENT
    assert "toast('数据更新中...')" in SSE_CLIENT
    assert "data_update_incomplete" in SSE_CLIENT
    assert "showToastBar('任务失败: ' + msg)" in SSE_CLIENT
