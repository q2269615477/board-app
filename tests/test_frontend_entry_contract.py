"""Frontend entry contract tests.

These tests protect the modular panel layout.  The page shell must load the
real modules instead of reintroducing a large inline frontend in index.html.

Entry scripts are auto-parsed from ``static/index.html`` — no hardcoded list.
"""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_baseline.ps1"


REQUIRED_IDS = (
    'id="toolbar"',
    'id="index-bar"',
    'id="idx-add-btn"',
    'id="nav-panel"',
    'id="pro-container"',
    'id="search-wrap"',
)

# Regex to extract <script src="/static/js/..."> from index.html
_SCRIPT_SRC_RE = re.compile(r'<script\s+[^>]*src="(/static/js/[^"]+)"')


def _index_html():
    return INDEX.read_text(encoding="utf-8")


def _parse_entry_scripts():
    """Parse all local <script src="/static/js/..."> from index.html.

    Returns a list of relative file paths (e.g. ``static/js/app-init.js``)
    with query strings stripped and duplicates removed.
    """
    html = _index_html()
    seen = set()
    result = []
    for m in _SCRIPT_SRC_RE.finditer(html):
        src = m.group(1)
        # Strip query string (e.g. ?v=20260729)
        clean = src.split("?")[0]
        # Convert /static/js/foo.js → static/js/foo.js
        rel = clean.lstrip("/")
        if rel not in seen:
            seen.add(rel)
            result.append(rel)
    return result


def test_index_shell_keeps_required_layout_mount_points():
    html = _index_html()
    for marker in REQUIRED_IDS:
        assert marker in html, f"missing frontend mount point: {marker}"


def test_index_shell_loads_modular_frontend_scripts():
    """All auto-parsed entry scripts must be referenced in index.html."""
    scripts = _parse_entry_scripts()
    assert len(scripts) >= 10, (
        f"Expected at least 10 entry scripts, found {len(scripts)}: {scripts}"
    )
    # Sanity: the core modules must always be present
    must_have = (
        "static/js/api-client.js",
        "static/js/app-init.js",
        "static/js/chart-core.js",
    )
    for required in must_have:
        assert required in scripts, f"missing core entry script: {required}"


def test_all_entry_scripts_exist_on_disk():
    """Every entry script referenced in index.html must exist on disk."""
    scripts = _parse_entry_scripts()
    missing = [s for s in scripts if not (ROOT / s).exists()]
    assert not missing, f"Entry scripts missing on disk: {missing}"


def test_all_entry_scripts_are_git_tracked():
    """Every entry script referenced in index.html must be tracked by Git."""
    scripts = _parse_entry_scripts()
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
    )
    tracked = set(
        p.decode("utf-8", errors="replace").strip().replace("\\", "/")
        for p in result.stdout.split(b"\0")
        if p.strip()
    )
    untracked = [s for s in scripts if s not in tracked]
    assert not untracked, (
        f"Entry scripts not tracked by Git: {untracked}"
    )


def test_verify_baseline_ps1_does_not_hardcode_file_list():
    """verify_baseline.ps1 must auto-parse index.html, not hardcode a file list."""
    content = VERIFY_SCRIPT.read_text(encoding="utf-8")
    # Must reference index.html for parsing
    assert "index.html" in content, (
        "verify_baseline.ps1 must read static/index.html to discover scripts"
    )
    # Must NOT contain a hardcoded array of JS file paths
    # (the old pattern was: $jsFiles = @("static/js/api-client.js", ...))
    hardcoded_pattern = re.compile(
        r'\$jsFiles\s*=\s*@\(\s*"static/js/'
    )
    assert not hardcoded_pattern.search(content), (
        "verify_baseline.ps1 must not hardcode a JS file list; "
        "it should auto-parse from index.html"
    )


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
