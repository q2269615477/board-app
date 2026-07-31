"""Runtime logic tests for unified-selector and chart-controller.

These tests execute the actual JS module logic within a Python-controlled
DOM/callback simulation (no full browser needed).

We use Node.js to evaluate the JS files against a mocked window object.
"""
import subprocess
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"


def _node_eval(js_code: str) -> str:
    """Run JS code in Node.js, return stdout."""
    result = subprocess.run(
        ["node", "-e", js_code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"Node error:\n{result.stderr}")
    return result.stdout.strip()


def _run_js_with_mock(js_files: list, test_code: str) -> str:
    """Load JS files with a mocked window, then run test_code."""
    mock_setup = """
// Simple event emitter mock for window
const _listeners = {};
global.window = {
  addEventListener: function(type, fn) { (_listeners[type] = _listeners[type] || []).push(fn); },
  dispatchEvent: function(evt) {
    const list = _listeners[evt.type] || [];
    for (const fn of list) fn(evt);
    return true;
  }
};
global.document = { readyState: 'complete', addEventListener: () => {}, dispatchEvent: () => {} };
global.CustomEvent = class CustomEvent {
  constructor(type, opts) { this.type = type; this.detail = opts.detail; }
};
global.console = { log: () => {}, warn: () => {}, error: () => {}, dir: () => {} };
"""
    file_loads = ""
    for f in sorted(js_files):
        code = (JS_DIR / f).read_text(encoding="utf-8")
        file_loads += f"\n// === {f} ===\n{code}\n"

    full = mock_setup + file_loads + "\n" + test_code + "\n"
    return _node_eval(full)


def test_symbol_router_classify():
    """SymbolRouter.classify must correctly identify symbol types."""
    out = _run_js_with_mock(
        ["symbol-router.js"],
        """
const cases = [
  ['sh000001', 'index'],
  ['600519', 'stock'],
  ['000001', 'stock'],
  ['BK0001', 'board'],
  ['HSI', 'hk_index'],
  ['SPX', 'global_index'],
  ['800000', 'index'],
  ['', 'stock'],
];
let ok = true;
for (const [input, expected] of cases) {
  const got = window.SymbolRouter.classify(input);
  if (got !== expected) { ok = false; console.log('FAIL:', input, '→', got, 'expected', expected); }
}
process.stdout.write(ok ? 'ALL_PASS' : 'SOME_FAIL');
"""
    )
    assert "ALL_PASS" in out, f"classify failed: {out}"


def test_ui_state_set_symbol():
    """UIState.setSymbol must update the snapshot."""
    out = _run_js_with_mock(
        ["ui-state.js", "symbol-router.js"],
        """
const changed = window.UIState.setSymbol({ code: 'sh000001', name: '上证指数', type: 'index' }, 'test');
const snap = window.UIState.snapshot();
process.stdout.write(JSON.stringify({ changed, code: snap.symbol.code, type: snap.symbol.type }));
"""
    )
    data = json.loads(out)
    assert data["changed"] is True
    assert data["code"] == "sh000001"
    assert data["type"] == "index"


def test_ui_state_dedup_same_symbol():
    """Setting same symbol twice must report no change second time."""
    out = _run_js_with_mock(
        ["ui-state.js", "symbol-router.js"],
        """
window.UIState.setSymbol({ code: 'sh000001', name: '上证指数', type: 'index' });
const second = window.UIState.setSymbol({ code: 'sh000001', name: '上证指数', type: 'index' });
process.stdout.write(second ? 'CHANGED' : 'NO_CHANGE');
"""
    )
    assert "NO_CHANGE" in out


def test_ui_state_subscribe():
    """UIState.subscribe must notify listeners on symbol change."""
    out = _run_js_with_mock(
        ["ui-state.js"],
        """
let notified = null;
window.UIState.subscribe(e => { notified = e; });
window.UIState.setSymbol({ code: '600519', name: '贵州茅台', type: 'stock' });
process.stdout.write(JSON.stringify({ notifiedType: notified.type, code: notified.symbol.code }));
"""
    )
    data = json.loads(out)
    assert data["notifiedType"] == "symbol-changed"
    assert data["code"] == "600519"


def test_unified_selector_handle_select():
    """UnifiedSelector.handleSelect must call UIState + ChartController apply."""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
let uiUpdated = false;
let chartCalled = false;
const origSnap = window.UIState.snapshot;

// Simulate ChartController.applySymbol being callable
window.ChartController = window.ChartController || {};
window.ChartController.applySymbol = function(sel) { chartCalled = true; };

window.UnifiedSelector.handleSelect({ code: 'sh000300', name: '沪深300', type: 'index', source: 'test', trigger: 'click' });

const snap = window.UIState.snapshot();
process.stdout.write(JSON.stringify({ code: snap.symbol.code, type: snap.symbol.type, chartCalled }));
"""
    )
    data = json.loads(out)
    assert data["code"] == "sh000300"
    assert data["type"] == "index"
    assert data["chartCalled"] is True


def test_chart_controller_refresh_current():
    """ChartController.refreshCurrent must re-apply the current symbol via pro.setSymbol."""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
let appliedSymbol = null;
global.pro = { setSymbol: function(sym) { appliedSymbol = sym; } };
window.pro = global.pro;

window.UIState.setSymbol({ code: 'sh000001', name: '上证指数', type: 'index' }, 'test');
window.ChartController.refreshCurrent({ source: 'test-refresh' });

process.stdout.write(JSON.stringify({ code: appliedSymbol ? appliedSymbol.ticker : null, type: appliedSymbol ? appliedSymbol.type : null }));
"""
    )
    data = json.loads(out)
    assert data["code"] == "sh000001"
    assert data["type"] == "index"


def test_refresh_current_symbol_event():
    """Dispatching 'refresh-current-symbol' must trigger ChartController.refreshCurrent."""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js"],
        """
let appliedSymbol = null;
global.pro = { setSymbol: function(sym) { appliedSymbol = sym; } };
window.pro = global.pro;

window.UIState.setSymbol({ code: '600519', name: '贵州茅台', type: 'stock' }, 'test');
window.ChartController.init();
window.dispatchEvent(new CustomEvent('refresh-current-symbol', { detail: { source: 'sse-test' } }));

process.stdout.write(JSON.stringify({ code: appliedSymbol ? appliedSymbol.ticker : null }));
"""
    )
    data = json.loads(out)
    assert data["code"] == "600519"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
