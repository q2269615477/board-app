"""Static test: forbid direct pro.setSymbol() calls in non-allowed files.

Only ChartController and the Pro initialization path in chart-core.js
may directly call pro.setSymbol(). All other modules must use the unified
'select-symbol' / 'refresh-current-symbol' events.
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"

# Files allowed to call pro.setSymbol directly.
ALLOWED_FILES = {
    "chart-controller.js",   # the single legal entry point
}

# Patterns that indicate the file is a legitimate allowed use.
ALLOWED_PATTERNS = {
    "chart-controller.js": [
        re.compile(r"pro\.setSymbol\(symbol\)"),  # the main apply
        re.compile(r"typeof pro\.setSymbol"),       # capability check
    ],
}

# Pro-init monkey-patching is a cross-cutting concern, not a bypass.
MONKEY_PATCH_PATTERN = re.compile(r"window\.pro\.setSymbol\s*=")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _find_offending_lines(text: str, filename: str):
    """Return list of (line_no, line_text) for illegal pro.setSymbol calls."""
    offenders = []
    lines = text.splitlines()
    in_init_pro_block = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comment-only lines
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # Check for direct pro.setSymbol( calls (not just capability checks)
        if not re.search(r"pro\.setSymbol\(", line):
            continue

        # Skip capability checks like `typeof pro.setSymbol !== 'function'`
        if re.search(r"typeof\s+[\w.]*\.?pro\.setSymbol", line):
            continue

        # Skip monkey-patching
        if MONKEY_PATCH_PATTERN.search(line):
            continue

        # Check if this file is in the allowed list
        if filename in ALLOWED_FILES:
            allowed = False
            for pat in ALLOWED_PATTERNS.get(filename, []):
                if pat.search(line):
                    allowed = True
                    break
            if allowed:
                continue

        # If we reach here, the line is a real direct pro.setSymbol call
        # that is NOT in an allowed file.
        offenders.append((i, line.strip()))

    return offenders


def test_no_direct_pro_setsymbol_in_frontend():
    """All pro.setSymbol() calls must go through ChartController."""
    all_offenders = {}

    for js_file in sorted(JS_DIR.glob("*.js")):
        name = js_file.name
        if name in ALLOWED_FILES:
            continue  # These are explicitly allowed

        text = _read_text(js_file)
        if not text:
            continue

        bad = _find_offending_lines(text, name)
        if bad:
            all_offenders[name] = bad

    if all_offenders:
        details = []
        for fname, lines in all_offenders.items():
            for lineno, content in lines:
                details.append(f"  {fname}:{lineno}: {content}")

        raise AssertionError(
            f"Direct pro.setSymbol() calls found in non-allowed files:\n"
            + "\n".join(details)
            + "\n\nUse 'select-symbol' / 'refresh-current-symbol' events instead."
        )


def test_chart_controller_is_only_setsymbol_gateway():
    """chart-controller.js must contain the main pro.setSymbol(symbol) path."""
    cc = JS_DIR / "chart-controller.js"
    text = _read_text(cc)

    # The canonical apply must exist
    assert "pro.setSymbol(symbol)" in text, (
        "chart-controller.js must call pro.setSymbol(symbol) as the single gateway"
    )

    # refreshCurrent must exist
    assert "refreshCurrent" in text, (
        "chart-controller.js must expose refreshCurrent()"
    )

    # Must listen for refresh-current-symbol
    assert "refresh-current-symbol" in text, (
        "chart-controller.js must listen for 'refresh-current-symbol' events"
    )


def test_unified_selector_dispatches_standard_event():
    """unified-selector.js must dispatch 'select-symbol' via the standard event."""
    us = JS_DIR / "unified-selector.js"
    text = _read_text(us)

    assert "select-symbol" in text, (
        "unified-selector.js must dispatch 'select-symbol' events"
    )
    assert "UIState" in text, (
        "unified-selector.js must use UIState for state management"
    )


def test_sse_client_no_direct_setsymbol():
    """sse-client.js must NOT directly call pro.setSymbol."""
    text = _read_text(JS_DIR / "sse-client.js")
    # Look for pro.setSymbol( pattern (not just typeof check)
    offenders = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("//"):
            continue
        if re.search(r"pro\.setSymbol\(", line) and not re.search(r"typeof.*pro\.setSymbol", line):
            offenders.append((i, s))
    assert offenders == [], f"sse-client.js has direct pro.setSymbol: {offenders}"


def test_session_ui_no_direct_setsymbol():
    """session-ui.js must NOT directly call pro.setSymbol for session restore."""
    text = _read_text(JS_DIR / "session-ui.js")
    # Count pro.setSymbol( calls (excluding typeof checks)
    call_lines = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("//"):
            continue
        # Match pro.setSymbol( but not capability checks like `typeof pro.setSymbol`
        if re.search(r"pro\.setSymbol\(", line) and "typeof" not in line:
            call_lines.append((i, s))
    assert call_lines == [], f"session-ui.js has direct pro.setSymbol: {call_lines}"


def test_search_controller_not_loaded_in_html():
    """index.html must NOT load search-controller.js (to avoid double-binding)."""
    html = _read_text(ROOT / "static" / "index.html")
    assert "search-controller.js\"" not in html.replace(" ", ""), (
        "index.html must not load search-controller.js alongside search-panel.js"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
