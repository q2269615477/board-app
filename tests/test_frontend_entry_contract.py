"""Frontend entry contract tests.

These tests protect the modular panel layout.  The page shell must load the
real modules instead of reintroducing a large inline frontend in index.html.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"


REQUIRED_IDS = (
    'id="toolbar"',
    'id="index-bar"',
    'id="idx-add-btn"',
    'id="nav-panel"',
    'id="pro-container"',
    'id="search-wrap"',
)


REQUIRED_SCRIPTS = (
    "/static/js/api-client.js",
    "/static/js/toast-modal.js",
    "/static/js/realtime-client.js",
    "/static/js/chart-core.js",
    "/static/js/nav-panel.js",
    "/static/js/index-bar.js",
    "/static/js/search-panel.js",
    "/static/js/sse-client.js",
    "/static/js/session-ui.js",
    "/static/js/app-init.js",
)


def _index_html():
    return INDEX.read_text(encoding="utf-8")


def test_index_shell_keeps_required_layout_mount_points():
    html = _index_html()
    for marker in REQUIRED_IDS:
        assert marker in html, f"missing frontend mount point: {marker}"


def test_index_shell_loads_modular_frontend_scripts():
    html = _index_html()
    for script in REQUIRED_SCRIPTS:
        assert script in html, f"missing required frontend module: {script}"


def test_index_shell_does_not_reintroduce_large_inline_frontend():
    html = _index_html()
    assert "<script>" not in html
    assert "<script " in html
    assert html.count("<style") == 0
    assert len(html.splitlines()) < 140


def test_session_panel_keeps_p1_interaction_entries():
    js = (ROOT / "static" / "js" / "session-ui.js").read_text(encoding="utf-8")
    for action in (
        'data-act="level"',
        'data-act="reaction"',
        'data-act="levels"',
        'data-act="resonance"',
        'data-act="proposal"',
    ):
        assert action in js, f"missing session panel action: {action}"
    for fn in (
        "startDrawLevel",
        "startReactionMode",
        "openLevelManager",
        "openResonancePanel",
        "openProposalPanel",
        "bindHotkeys",
    ):
        assert fn in js, f"missing session panel function: {fn}"


def test_left_nav_does_not_render_board_tag_chips():
    js = (ROOT / "static" / "js" / "nav-panel.js").read_text(encoding="utf-8")
    assert 'class="board-tag-chip"' not in js
    assert "function renderBoardTagChips" in js
    assert "function _typeTag" in js
