"""Playwright-level interaction acceptance tests.

Tests 5 critical user interaction scenarios:
1. Click top index → chart switches + /api/ctx syncs
2. Click left board → chart switches
3. Click board constituent stock → chart switches to stock
4. Search "ymkd", arrow keys, Enter → shows 药明康德
5. Trigger refresh-current-symbol → only refreshes current symbol

Requires: playwright + chromium browser
  pip install playwright
  playwright install chromium
"""
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

pytestmark = pytest.mark.skipif(
    not HAS_PLAYWRIGHT,
    reason="playwright not installed; run: pip install playwright && playwright install chromium",
)

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"

# ─── Mock data ───────────────────────────────────────────────────────

MOCK_CLASSIFICATION = {
    "version": "5.0",
    "categories": [{
        "name": "医药生物与健康",
        "subcategories": [{
            "name": "创新药与 CXO",
            "boards": [
                {"code": "BK0446", "name": "CRO概念", "type": "concept",
                 "tags": ["创新药", "CXO"],
                 "primary_category": "医药生物与健康",
                 "secondary_category": "创新药与 CXO"},
                {"code": "BK0447", "name": "创新药", "type": "concept",
                 "tags": ["创新药"],
                 "primary_category": "医药生物与健康",
                 "secondary_category": "创新药与 CXO"},
            ],
        }],
    }],
}

MOCK_SEARCH_YMKD = {
    "data": [
        {"code": "603259", "name": "药明康德", "type": "stock",
         "display_code": "603259", "initials": "ymkd", "category": "个股"},
    ],
}

MOCK_CONSTITUENTS = {
    "data": [
        {"code": "603259", "name": "药明康德", "change_pct": 2.5,
         "close": 65.30, "mkt_cap": 17000},
        {"code": "300122", "name": "智飞生物", "change_pct": -1.2,
         "close": 45.60, "mkt_cap": 7300},
    ],
}

# Mock klinecharts.min.js — provides window.klinecharts.init
MOCK_KLINECHARTS_JS = """
window.klinecharts = window.klinecharts || {};
window.klinecharts.init = function(dom, opts) {
  return { getDataList: function() { return []; } };
};
"""

# Mock klinecharts-pro.umd.js — provides window.klinechartspro.KLineChartPro
# The mock Pro records all setSymbol calls in window.__proSetSymbolCalls.
MOCK_KLINECHARTS_PRO_JS = """
window.klinechartspro = window.klinechartspro || {};
window.klinechartspro.KLineChartPro = function(config) {
  var _sym = null;
  this.setSymbol = function(sym) {
    _sym = sym;
    window.__proSetSymbolCalls = window.__proSetSymbolCalls || [];
    window.__proSetSymbolCalls.push(JSON.parse(JSON.stringify(sym)));
    setTimeout(function() {
      try {
        window.dispatchEvent(new CustomEvent('kline-loaded', {
          detail: { symbol: sym.ticker, period: 'daily', ok: true, count: 100 }
        }));
      } catch(e) {}
    }, 5);
  };
  this.setPeriod = function(p) {};
  this.getSymbol = function() { return _sym; };
  window.pro = this;
  return this;
};
"""


# ─── Mock server state ───────────────────────────────────────────────

class _MockState:
    """Captures API requests for verification."""

    def __init__(self):
        self.ctx_posts = []       # /api/ctx POST bodies
        self.kline_requests = []  # kline API request paths

    def reset(self):
        self.ctx_posts.clear()
        self.kline_requests.clear()


# ─── Route handler ───────────────────────────────────────────────────

def _handle_route(route, state: _MockState):
    """Intercept all browser requests; serve static files or mock API data."""
    try:
        url = route.request.url
        parsed = urlparse(url)
        path = parsed.path

        # ── Mock third-party libraries ──
        if path.endswith('/klinecharts.min.js'):
            route.fulfill(content_type='application/javascript',
                          body=MOCK_KLINECHARTS_JS)
            return
        if path.endswith('/klinecharts-pro.umd.js'):
            route.fulfill(content_type='application/javascript',
                          body=MOCK_KLINECHARTS_PRO_JS)
            return

        # ── Mock API endpoints ──
        if path.startswith('/api/'):
            _handle_api(route, path, parsed, state)
            return

        # ── Serve static files from disk ──
        # HTML uses absolute paths like /static/js/..., so resolve
        # relative to project ROOT (which contains the static/ dir).
        if path == '/' or path == '/index.html':
            file_path = STATIC_DIR / 'index.html'
        else:
            file_path = ROOT / path.lstrip('/')

        if file_path.exists() and file_path.is_file():
            mime, _ = mimetypes.guess_type(str(file_path))
            content = file_path.read_bytes()
            route.fulfill(content_type=mime or 'application/octet-stream',
                          body=content)
        else:
            # 404 for missing files (favicon, etc.)
            route.fulfill(status=404, body=f'Not found: {path}')
    except Exception as e:
        try:
            route.fulfill(status=500, body=f'Route error: {e}')
        except Exception:
            pass


def _handle_api(route, path, parsed, state: _MockState):
    """Return mock JSON for each API endpoint."""
    method = route.request.method

    def _json(data, status=200):
        route.fulfill(status=status,
                      content_type='application/json',
                      body=json.dumps(data))

    # ── /api/ctx POST — capture body for verification ──
    if path == '/api/ctx' and method == 'POST':
        body = route.request.post_data
        if body:
            try:
                state.ctx_posts.append(json.loads(body))
            except Exception:
                state.ctx_posts.append({"raw": body})
        _json({"ok": True})
        return

    # ── /api/events (SSE) — return empty stream ──
    if path == '/api/events':
        route.fulfill(status=200,
                      content_type='text/event-stream',
                      body='')
        return

    # ── Classification ──
    if path == '/api/classification/load':
        _json(MOCK_CLASSIFICATION)
        return

    # ── Search ──
    if path == '/api/search':
        qs = parse_qs(parsed.query)
        q = (qs.get('q', [''])[0]).lower()
        if 'ymkd' in q:
            _json(MOCK_SEARCH_YMKD)
        else:
            _json({"data": []})
        return

    # ── Board constituents ──
    if '/api/board-cons-sorted/' in path:
        _json(MOCK_CONSTITUENTS)
        return

    # ── Spot prices ──
    if path.startswith('/api/spot/indices'):
        _json({"data": {"sh000001": {"price": 3000, "change_pct": 0.5}}})
        return
    if '/api/spot/' in path:
        _json({"data": {"price": 100.0, "change_pct": 1.5}})
        return

    # ── Board changes ──
    if path == '/api/board-changes':
        _json({"data": {}})
        return

    # ── Annotation counts ──
    if path == '/api/annotations/counts':
        _json({"data": {}})
        return

    # ── Pinyin ──
    if path == '/api/pinyin/all':
        _json({"data": []})
        return

    # ── Frontend config ──
    if path == '/api/system/frontend-config':
        _json({})
        return

    # ── Health / hooks ──
    if path == '/api/health':
        _json({"ok": True})
        return
    if path == '/api/hooks/status':
        _json({})
        return

    # ── Kline data ──
    if '/api/kline/' in path:
        state.kline_requests.append(path)
        _json({"data": [], "symbol": "", "period": "daily"})
        return

    # ── Signals ──
    if path.startswith('/api/signals/'):
        _json({"data": []})
        return

    # ── Default: empty JSON ──
    _json({"data": [], "ok": True})


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mock_state():
    return _MockState()


@pytest.fixture
def page(mock_state):
    """Create a Playwright page with all routes mocked."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        pg = context.new_page()

        # Intercept every request
        pg.route('**/*', lambda route: _handle_route(route, mock_state))

        # Navigate to the app
        pg.goto('http://localhost/', wait_until='domcontentloaded')

        # Wait for nav-panel to render board items
        pg.wait_for_selector('.board-item', timeout=15000)

        # Wait for Pro to be initialised (mock setSymbol available)
        pg.wait_for_function(
            "typeof window.pro !== 'undefined' && "
            "typeof window.pro.setSymbol === 'function'",
            timeout=15000,
        )

        # Reset call tracking before each test
        pg.evaluate("window.__proSetSymbolCalls = [];")
        mock_state.reset()

        yield pg

        context.close()
        browser.close()


# ─── Tests ───────────────────────────────────────────────────────────

def test_01_click_top_index_switches_chart_and_syncs_ctx(page, mock_state):
    """Scenario 1: Click top index → chart switches + /api/ctx syncs."""
    # Click 上证指数 in the top index bar
    page.click('.idx-item[data-ticker="sh000001"]')

    # Verify pro.setSymbol was called with sh000001
    page.wait_for_function(
        "window.__proSetSymbolCalls && "
        "window.__proSetSymbolCalls.some(c => c.ticker === 'sh000001')",
        timeout=5000,
    )
    calls = page.evaluate("window.__proSetSymbolCalls")
    assert any(c['ticker'] == 'sh000001' for c in calls), \
        f"Expected setSymbol(sh000001), got: {calls}"

    # Verify /api/ctx was POSTed with the correct symbol
    page.wait_for_timeout(500)
    assert len(mock_state.ctx_posts) >= 1, \
        f"Expected at least 1 /api/ctx POST, got {len(mock_state.ctx_posts)}"
    ctx = mock_state.ctx_posts[-1]
    assert ctx['code'] == 'sh000001', f"Expected code=sh000001, got {ctx}"
    assert ctx['type'] == 'index', f"Expected type=index, got {ctx}"


def test_02_click_left_board_switches_chart(page, mock_state):
    """Scenario 2: Click left board → chart switches."""
    # Click CRO概念 board in the nav panel
    page.click('.board-item[data-code="BK0446"]')

    page.wait_for_function(
        "window.__proSetSymbolCalls && "
        "window.__proSetSymbolCalls.some(c => c.ticker === 'BK0446')",
        timeout=5000,
    )
    calls = page.evaluate("window.__proSetSymbolCalls")
    assert any(c['ticker'] == 'BK0446' for c in calls), \
        f"Expected setSymbol(BK0446), got: {calls}"

    # Verify /api/ctx synced
    page.wait_for_timeout(500)
    assert len(mock_state.ctx_posts) >= 1
    ctx = mock_state.ctx_posts[-1]
    assert ctx['code'] == 'BK0446', f"Expected code=BK0446, got {ctx}"


def test_03_click_board_constituent_switches_to_stock(page, mock_state):
    """Scenario 3: Click board constituent stock → chart switches to stock."""
    # First click a board to open the constituents panel
    page.click('.board-item[data-code="BK0446"]')

    # Wait for constituents to load (药明康德 appears)
    page.wait_for_selector('.fcons-name:has-text("药明康德")', timeout=8000)

    # Click the 药明康德 constituent item
    page.click('.fcons-item:has(.fcons-name:has-text("药明康德"))')

    # Verify pro.setSymbol was called with the stock code 603259
    page.wait_for_function(
        "window.__proSetSymbolCalls && "
        "window.__proSetSymbolCalls.some(c => c.ticker === '603259')",
        timeout=5000,
    )
    calls = page.evaluate("window.__proSetSymbolCalls")
    stock_calls = [c for c in calls if c['ticker'] == '603259']
    assert len(stock_calls) >= 1, \
        f"Expected setSymbol(603259), got: {calls}"

    # Verify /api/ctx synced with stock type
    page.wait_for_timeout(500)
    ctx_codes = [c['code'] for c in mock_state.ctx_posts]
    assert '603259' in ctx_codes, \
        f"Expected 603259 in ctx posts, got: {ctx_codes}"


def test_04_search_ymkd_arrow_enter_shows_yimingkangde(page, mock_state):
    """Scenario 4: Search 'ymkd', arrow down, Enter → shows 药明康德."""
    # Focus the search input and type
    page.click('#search-input')
    page.fill('#search-input', 'ymkd')

    # Wait for search results to render
    page.wait_for_selector('.search-item', timeout=8000)

    # Press ArrowDown to highlight the first result
    page.press('#search-input', 'ArrowDown')

    # Press Enter to select
    page.press('#search-input', 'Enter')

    # Verify pro.setSymbol was called with 药明康德's code
    page.wait_for_function(
        "window.__proSetSymbolCalls && "
        "window.__proSetSymbolCalls.some(c => c.ticker === '603259')",
        timeout=5000,
    )
    calls = page.evaluate("window.__proSetSymbolCalls")
    ym_calls = [c for c in calls if c['ticker'] == '603259']
    assert len(ym_calls) >= 1, \
        f"Expected setSymbol(603259) for 药明康德, got: {calls}"

    # Verify /api/ctx synced
    page.wait_for_timeout(500)
    ctx_codes = [c['code'] for c in mock_state.ctx_posts]
    assert '603259' in ctx_codes, \
        f"Expected 603259 in ctx posts, got: {ctx_codes}"


def test_05_refresh_current_symbol_only_refreshes(page, mock_state):
    """Scenario 5: Trigger refresh-current-symbol → only refreshes current."""
    # First, select a specific symbol (上证指数)
    page.click('.idx-item[data-ticker="sh000001"]')
    page.wait_for_function(
        "window.__proSetSymbolCalls && "
        "window.__proSetSymbolCalls.some(c => c.ticker === 'sh000001')",
        timeout=5000,
    )

    # Record the current number of setSymbol calls
    initial_count = page.evaluate("window.__proSetSymbolCalls.length")

    # Dispatch refresh-current-symbol event
    page.evaluate("""
        window.dispatchEvent(new CustomEvent('refresh-current-symbol', {
            detail: { source: 'test-refresh' }
        }));
    """)

    # Verify a new setSymbol call was made (refresh)
    page.wait_for_function(
        f"window.__proSetSymbolCalls.length > {initial_count}",
        timeout=5000,
    )

    # The refreshed symbol must be the SAME as the current one (sh000001)
    calls = page.evaluate("window.__proSetSymbolCalls")
    last_call = calls[-1]
    assert last_call['ticker'] == 'sh000001', \
        f"Refresh should re-apply sh000001, got: {last_call['ticker']}"

    # Ensure no DIFFERENT symbol was switched to during refresh
    # (all calls after initial_count should be sh000001)
    refresh_calls = calls[initial_count:]
    wrong_switches = [c for c in refresh_calls if c['ticker'] != 'sh000001']
    assert wrong_switches == [], \
        f"Refresh should not switch to other symbols, got: {wrong_switches}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
