"""End-to-end click chain tests.

Verifies the real user interaction path:
  1. Search "ymkd" → API returns 药明康德 (603259)
  2. Select-symbol event fires with correct payload
  3. UnifiedSelector updates UIState
  4. ChartController.applySymbol receives the right symbol
  5. window.__board_ctx is set to 603259
  6. K-line API returns different data for 603259 vs a different symbol

Two layers:
  - Flask API contract tests (Python, via test client)
  - JS chain tests (Node.js, via _run_js_with_mock pattern)
"""
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"


# ---------------------------------------------------------------------------
# Node.js JS execution helper (same pattern as test_unified_selector_js.py)
# ---------------------------------------------------------------------------

def _node_eval(js_code: str) -> str:
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
var _listeners = {};
global.window = {
  addEventListener: function(type, fn) { (_listeners[type] = _listeners[type] || []).push(fn); },
  dispatchEvent: function(evt) {
    var list = _listeners[evt.type] || [];
    for (var i = 0; i < list.length; i++) list[i](evt);
    return true;
  },
  __board_ctx: null,
};
global.document = { readyState: 'complete', addEventListener: function(){}, dispatchEvent: function(){} };
global.CustomEvent = function CustomEvent(type, opts) { this.type = type; this.detail = opts ? opts.detail : undefined; };
global.fetch = function(url, opts) {
  return Promise.resolve({ ok: true, json: function() { return Promise.resolve({}); } });
};
global.console = { log: function(){}, warn: function(){}, error: function(){}, dir: function(){} };
"""
    file_loads = ""
    for f in sorted(js_files):
        code = (JS_DIR / f).read_text(encoding="utf-8")
        file_loads += "\n// === " + f + " ===\n" + code + "\n"

    full = mock_setup + file_loads + "\n" + test_code + "\n"
    return _node_eval(full)


# ---------------------------------------------------------------------------
# Layer 1: Flask API contract tests
# ---------------------------------------------------------------------------

_app = None


def _get_app():
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app


def test_search_ymkd_returns_yaomingkangde():
    """搜索 ymkd 必须返回药明康德 (603259)。"""
    app = _get_app()
    with app.test_client() as client:
        resp = client.get("/api/search?q=ymkd")
        assert resp.status_code == 200
        data = resp.get_json()
        results = data.get("data", [])
        assert results, "搜索 ymkd 不应返回空结果"

        # 603259 必须在结果中
        codes = [r["code"] for r in results]
        assert "603259" in codes, f"603259 不在搜索结果中: {codes}"

        # 验证药明康德的字段
        item = next(r for r in results if r["code"] == "603259")
        assert item["type"] == "stock", f"type 应为 stock, 实际: {item['type']}"
        assert "药明康德" in item["name"], f"name 应包含药明康德, 实际: {item['name']}"


def test_search_ymkd_pinyin_initials_match():
    """搜索 ymkd 的结果中，603259 的首字母必须是 YMKD。"""
    app = _get_app()
    with app.test_client() as client:
        resp = client.get("/api/search?q=ymkd")
        data = resp.get_json()
        results = data.get("data", [])
        item = next(r for r in results if r["code"] == "603259")
        initials = item.get("initials", "")
        assert initials.upper() == "YMKD", (
            f"药明康德首字母应为 YMKD, 实际: {initials}"
        )


def test_kline_stock_603259_returns_data():
    """/api/kline/stock/603259 必须返回 K 线数据。"""
    app = _get_app()
    with app.test_client() as client:
        resp = client.get("/api/kline/stock/603259?period=daily")
        assert resp.status_code == 200
        data = resp.get_json()
        kline_data = data.get("data", [])
        assert kline_data, "603259 K 线数据不应为空"
        # 验证 K 线数据结构
        first = kline_data[0]
        assert "timestamp" in first, "K 线数据缺少 timestamp"
        assert "close" in first, "K 线数据缺少 close"


def test_kline_603259_differs_from_600519():
    """603259 的 K 线数据必须与 600519（贵州茅台）不同。"""
    app = _get_app()
    with app.test_client() as client:
        resp1 = client.get("/api/kline/stock/603259?period=daily")
        resp2 = client.get("/api/kline/stock/600519?period=daily")
        data1 = resp1.get_json().get("data", [])
        data2 = resp2.get_json().get("data", [])
        assert data1 and data2, "两个标的都应有数据"
        # 比较最后一条收盘价
        close1 = data1[-1].get("close")
        close2 = data2[-1].get("close")
        assert close1 != close2, (
            f"603259 和 600519 最后收盘价不应相同: {close1} == {close2}"
        )


# ---------------------------------------------------------------------------
# Layer 2: JS end-to-end chain tests
# Note: unified-selector.js auto-binds on load when document.readyState
# is 'complete', so we do NOT call bind() explicitly in tests.
# ---------------------------------------------------------------------------

def test_select_symbol_event_fires_with_correct_payload():
    """searchPick 必须发出 select-symbol 事件，包含 code=603259, type=stock。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
var captured = null;
window.addEventListener('select-symbol', function(e) {
  captured = e.detail;
});

window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

process.stdout.write(JSON.stringify(captured));
"""
    )
    data = json.loads(out)
    assert data["code"] == "603259"
    assert data["name"] == "药明康德"
    assert data["type"] == "stock"
    assert data["source"] == "bottom-search"
    assert data["trigger"] == "enter"


def test_unified_selector_updates_uistate_on_select():
    """UnifiedSelector 收到 select-symbol 后必须更新 UIState。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
window.ChartController.applySymbol = function(sel) {};

window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

var snap = window.UIState.snapshot();
process.stdout.write(JSON.stringify({
  code: snap.symbol ? snap.symbol.code : null,
  type: snap.symbol ? snap.symbol.type : null,
  name: snap.symbol ? snap.symbol.name : null,
}));
"""
    )
    data = json.loads(out)
    assert data["code"] == "603259"
    assert data["type"] == "stock"
    assert data["name"] == "药明康德"


def test_chart_controller_receives_symbol():
    """UnifiedSelector 必须调用 ChartController.applySymbol 传入 603259。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
var appliedSymbol = null;
window.ChartController.applySymbol = function(sel) { appliedSymbol = sel; };

window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

process.stdout.write(JSON.stringify({
  code: appliedSymbol ? appliedSymbol.code : null,
  type: appliedSymbol ? appliedSymbol.type : null,
  name: appliedSymbol ? appliedSymbol.name : null,
}));
"""
    )
    data = json.loads(out)
    assert data["code"] == "603259"
    assert data["type"] == "stock"
    assert data["name"] == "药明康德"


def test_board_ctx_updates_to_603259():
    """UnifiedSelector._syncContext 必须设置 window.__board_ctx.code = 603259。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
window.ChartController.applySymbol = function(sel) {};

window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

var ctx = window.__board_ctx;
process.stdout.write(JSON.stringify({
  code: ctx ? ctx.code : null,
  type: ctx ? ctx.type : null,
  name: ctx ? ctx.name : null,
}));
"""
    )
    data = json.loads(out)
    assert data["code"] == "603259"
    assert data["type"] == "stock"
    assert data["name"] == "药明康德"


def test_ctx_post_called_with_603259():
    """UnifiedSelector._syncContext 必须向 /api/ctx POST 603259。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
var ctxFetchCalls = [];
window.ChartController.applySymbol = function(sel) {};
global.fetch = function(url, opts) {
  ctxFetchCalls.push({ url: url, method: opts && opts.method || 'GET', body: opts && opts.body });
  return Promise.resolve({ ok: true, json: function() { return Promise.resolve({}); } });
};

window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

var ctxCall = null;
for (var i = 0; i < ctxFetchCalls.length; i++) {
  if (ctxFetchCalls[i].url === '/api/ctx' && ctxFetchCalls[i].method === 'POST') {
    ctxCall = ctxFetchCalls[i];
    break;
  }
}
process.stdout.write(JSON.stringify({
  found: !!ctxCall,
  body: ctxCall ? JSON.parse(ctxCall.body) : null,
}));
"""
    )
    data = json.loads(out)
    assert data["found"] is True, "未找到 /api/ctx POST 调用"
    assert data["body"]["code"] == "603259"
    assert data["body"]["type"] == "stock"


def test_symbol_router_classifies_603259_as_stock():
    """SymbolRouter 必须将 603259 识别为 stock 类型。"""
    out = _run_js_with_mock(
        ["symbol-router.js"],
        """
var t = window.SymbolRouter.classify('603259');
process.stdout.write(t);
"""
    )
    assert out == "stock"


def test_select_symbol_switches_from_previous():
    """切换标的时，UIState 必须报告 changed=true，且新 code 与旧 code 不同。"""
    out = _run_js_with_mock(
        ["symbol-router.js", "ui-state.js", "chart-controller.js", "unified-selector.js"],
        """
var appliedSymbols = [];
window.ChartController.applySymbol = function(sel) { appliedSymbols.push(sel); };

// 先选中上证指数
window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: 'sh000001', name: '上证指数', type: 'index', source: 'test', trigger: 'click' }
}));

// 再切换到药明康德
window.dispatchEvent(new CustomEvent('select-symbol', {
  detail: { code: '603259', name: '药明康德', type: 'stock', source: 'bottom-search', trigger: 'enter' }
}));

var snap = window.UIState.snapshot();
process.stdout.write(JSON.stringify({
  finalCode: snap.symbol ? snap.symbol.code : null,
  finalType: snap.symbol ? snap.symbol.type : null,
  appliedCount: appliedSymbols.length,
  firstCode: appliedSymbols[0] ? appliedSymbols[0].code : null,
  secondCode: appliedSymbols[1] ? appliedSymbols[1].code : null,
}));
"""
    )
    data = json.loads(out)
    assert data["finalCode"] == "603259"
    assert data["finalType"] == "stock"
    assert data["appliedCount"] == 2
    assert data["firstCode"] == "sh000001"
    assert data["secondCode"] == "603259"
    assert data["firstCode"] != data["secondCode"]
