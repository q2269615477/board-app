from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_open_session_toggle_is_inside_panel_header():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "#sess-toggle-btn{position:absolute;left:8px}" in source
    assert "#sess-side .hd{padding:11px 14px 11px 44px;" in source


def test_session_toggle_buttons_share_geometry_and_anchor():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8"
    )

    assert "#sess-toggle-btn,#sess-expand-btn{width:24px;height:24px;top:8px;transform:none;" in source
    assert "#sess-expand-btn{position:fixed;top:50px;right:0;" in source
    assert "#sess-side.collapsed{transform:translateX(388px)}" in source


def test_search_wrap_avoids_open_session_panel_and_has_narrow_layout():
    source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "body.sess-side-on #search-wrap" in source
    assert "right:calc(var(--sess-panel-width) + 12px)" in source
    assert "width:min(230px,calc(100vw - var(--sess-panel-width) - 30px))" in source
    assert "--sess-panel-width:min(388px,max(240px,68vw))" in source
    assert "left:-24px" not in source


def test_initial_open_state_syncs_body_class_and_content_margin():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "document.body.classList.toggle('sess-side-on', open)" in source
    assert "if (sessPanel) {" in source
    assert "syncSessionLayout();" in source
    assert "document.body.appendChild(side);\n    syncSessionLayout();" in source


def test_session_panel_is_created_collapsed_by_default():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "side.classList.add('collapsed');" in source


def test_session_toggle_resizes_underlying_chart_after_layout_transition():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "function resizeSessionChart()" in source
    assert "global.__kline_chart" in source
    assert "typeof chart.resize === 'function'" in source
    assert "setTimeout(resizeSessionChart, 320)" in source
