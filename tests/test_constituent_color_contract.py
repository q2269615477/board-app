"""Regression tests for constituent stock change colors and labels."""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAV_PANEL = ROOT / "static" / "js" / "nav-panel.js"
APP_CSS = ROOT / "static" / "css" / "app.css"


def _render_constituents(rows):
    """Execute the production renderBody function and return its HTML."""
    rows_json = json.dumps(rows, ensure_ascii=False)
    script = f"""
const fs = require('fs');
const source = fs.readFileSync({json.dumps(str(NAV_PANEL))}, 'utf8');
const start = source.indexOf('  function renderBody(){{');
const end = source.indexOf('  function refreshUI(){{', start);
if (start < 0 || end < 0) throw new Error('renderBody boundaries not found');
const renderBodySource = source.slice(start, end);
let _consData = {rows_json};
const body = {{ innerHTML: '' }};
const panel = {{ querySelector: (selector) => selector === '.fcons-body' ? body : null }};
const escAttr = (value) => String(value);
const escHtml = (value) => String(value);
eval(renderBodySource + '\\nrenderBody();');
process.stdout.write(body.innerHTML);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def test_constituent_change_values_render_red_green_neutral_classes_and_signs():
    html = _render_constituents(
        [
            {"code": "000001", "name": "上涨样本", "change_pct": 1.2, "close": 10},
            {"code": "000002", "name": "下跌样本", "change_pct": -2.3, "close": 9},
            {"code": "000003", "name": "平盘样本", "change_pct": 0, "close": 8},
        ]
    )

    rendered = re.findall(
        r'<span class="fcons-pct (up|down|flat)"[^>]*>([+-]?\d+\.\d{2}%)</span>',
        html,
    )

    assert rendered == [
        ("up", "+1.20%"),
        ("down", "-2.30%"),
        ("flat", "0.00%"),
    ]


def test_constituent_semantic_classes_map_to_red_green_and_neutral_css_tokens():
    css = APP_CSS.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", "", css)

    assert "#floating-cons.fcons-item.fcons-pct.up{color:var(--ui-red)}" in normalized
    assert "#floating-cons.fcons-item.fcons-pct.down{color:var(--ui-green)}" in normalized
    assert "#floating-cons.fcons-item.fcons-pct.flat{color:var(--ui-muted)}" in normalized
