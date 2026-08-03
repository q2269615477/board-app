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
        {"code": "02359", "name": "药明康德（港股候选）", "type": "stock",
         "display_code": "02359", "initials": "ymkd", "category": "个股"},
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

# Mock klinecharts.min.js — provides window.klinecharts.init and the chart API
MOCK_KLINECHARTS_JS = """
window.klinecharts = window.klinecharts || {};
window.__replayBars = [
  {timestamp: 1722470400000, open: 100, high: 103, low: 99, close: 102, volume: 1000},
  {timestamp: 1722556800000, open: 102, high: 104, low: 101, close: 103, volume: 1100},
  {timestamp: 1722643200000, open: 103, high: 105, low: 102, close: 104, volume: 1200},
  {timestamp: 1722729600000, open: 104, high: 106, low: 103, close: 105, volume: 1300},
  {timestamp: 1722816000000, open: 105, high: 107, low: 104, close: 106, volume: 1400},
  {timestamp: 1722902400000, open: 106, high: 108, low: 105, close: 107, volume: 1500},
  {timestamp: 1722988800000, open: 107, high: 109, low: 106, close: 108, volume: 1600},
  {timestamp: 1723075200000, open: 108, high: 110, low: 107, close: 109, volume: 1700},
  {timestamp: 1723161600000, open: 109, high: 111, low: 108, close: 110, volume: 1800},
  {timestamp: 1723248000000, open: 110, high: 112, low: 109, close: 111, volume: 1900},
  {timestamp: 1723334400000, open: 111, high: 113, low: 110, close: 112, volume: 2000},
  {timestamp: 1723420800000, open: 112, high: 114, low: 111, close: 113, volume: 2100}
];
window.klinecharts.init = function(dom, opts) {
  var data = window.__replayBars.slice();
  return {
    getDataList: function() { return data.slice(); },
    applyNewData: function(next) { data = (next || []).slice(); },
    updateData: function(next) {
      if (!next) return;
      var last = data.length ? data[data.length - 1] : null;
      if (last && last.timestamp === next.timestamp) data[data.length - 1] = next;
      else data.push(next);
    },
    getDom: function() { return dom; },
    convertFromPixel: function(point) {
      var isList = Array.isArray(point);
      var points = isList ? point : [point];
      var width = Math.max(dom.clientWidth || 900, 1);
      var converted = points.map(function(item) {
        var ratio = Math.max(0, Math.min(0.999, (item && item.x || 0) / width));
        var index = Math.floor(ratio * data.length);
        return {dataIndex: index, timestamp: data[index] && data[index].timestamp,
                value: data[index] && data[index].close};
      });
      return isList ? converted : converted[0];
    }
  };
};
"""

# Mock klinecharts-pro.umd.js — provides window.klinechartspro.KLineChartPro
# The mock Pro records all setSymbol calls in window.__proSetSymbolCalls.
MOCK_KLINECHARTS_PRO_JS = """
window.klinechartspro = window.klinechartspro || {};
window.klinechartspro.KLineChartPro = function(config) {
  var _sym = null;
  var _period = config.period;
  var _loadSeq = 0;
  var periodBar = document.createElement('div');
  periodBar.className = 'klinecharts-pro-period-bar';
  config.container.appendChild(periodBar);
  var chartDom = document.createElement('div');
  chartDom.className = 'mock-chart-surface';
  chartDom.style.cssText = 'position:absolute;inset:32px 0 0;';
  config.container.appendChild(chartDom);
  var chart = window.klinecharts.init(chartDom, config);
  if (window.__kline_chart !== chart) {
    window.__kline_chart = chart;
    window.dispatchEvent(new CustomEvent('kline-chart-ready', {detail: chart}));
  }
  this._chart = chart;
  this.chart = chart;
  this.setSymbol = function(sym) {
    _sym = sym;
    window.__proSetSymbolCalls = window.__proSetSymbolCalls || [];
    window.__proSetSymbolCalls.push(JSON.parse(JSON.stringify(sym)));
    var seq = ++_loadSeq;
    var now = Date.now();
    Promise.resolve(config.datafeed.getHistoryKLineData(
      sym, _period, now - 10 * 365 * 24 * 60 * 60 * 1000, now
    )).then(function(rows) {
      if (seq !== _loadSeq) return;
      chart.applyNewData(rows || []);
      try {
        window.dispatchEvent(new CustomEvent('kline-loaded', {
          detail: {
            symbol: sym.ticker,
            period: 'daily',
            ok: true,
            count: (rows || []).length
          }
        }));
      } catch(e) {}
    });
  };
  this.setPeriod = function(p) { _period = p; };
  this.getSymbol = function() { return _sym; };
  this.getPeriod = function() { return _period; };
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
        ticker = path.rstrip('/').split('/')[-1]
        base_price = {
            'sh000001': 100.0,
            'BK0446': 300.0,
            '603259': 600.0,
        }.get(ticker, 900.0)
        rows = [{
            "timestamp": 1722470400000 + i * 86400000,
            "open": base_price + i,
            "high": base_price + i + 2,
            "low": base_price + i - 1,
            "close": base_price + i + 1,
            "volume": 1000 + i * 100,
        } for i in range(12)]
        _json({"data": rows, "symbol": ticker, "period": "daily"})
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

        # Install before product scripts so tests can prove that user actions
        # crossed the public select-symbol event boundary.
        pg.add_init_script("""
            window.__selectSymbolEvents = [];
            window.addEventListener('select-symbol', function(event) {
                window.__selectSymbolEvents.push(
                    JSON.parse(JSON.stringify((event && event.detail) || {}))
                );
            });
        """)

        # Intercept every request
        pg.route('**/*', lambda route: _handle_route(route, mock_state))

        # Navigate to the app
        pg.goto('http://localhost/', wait_until='domcontentloaded')

        # The product starts with both side panels collapsed. Expand the left
        # panel and the first category explicitly because these interaction
        # scenarios exercise its rows.
        pg.wait_for_selector('#nav-expand-btn', state='visible', timeout=15000)
        pg.click('#nav-expand-btn')
        pg.wait_for_selector('.cat-item', state='visible', timeout=15000)
        pg.locator('.cat-item').first.click()
        pg.wait_for_selector('.sub-cat-item', state='visible', timeout=15000)
        pg.locator('.sub-cat-item').first.click()

        # Wait for nav-panel to render board items
        pg.wait_for_selector('.board-item', timeout=15000)

        # Wait for Pro to be initialised (mock setSymbol available)
        pg.wait_for_function(
            "typeof window.pro !== 'undefined' && "
            "typeof window.pro.setSymbol === 'function'",
            timeout=15000,
        )

        # Reset call tracking before each test
        pg.evaluate("""
            window.__proSetSymbolCalls = [];
            window.__selectSymbolEvents = [];
        """)
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
    """Old history is inert while ymkd debounces; results still select 603259."""
    previous_bars = page.evaluate(
        "window.__kline_chart && window.__kline_chart.getDataList()"
    )

    # Reproduce the production race: focus first renders a selected history
    # item that used to remain actionable throughout the debounce window.
    page.evaluate("""() => window.BoardSearchHistory.replace([{
        code: 'sz399989',
        value: '旧历史',
        name: '旧历史指数',
        type: 'index',
        category: '指数'
    }])""")
    page.click('#search-input')
    history = page.locator(
        '#search-results .search-item.selected[data-history="0"] .search-code'
    )
    assert history.inner_text().strip() == 'sz399989'

    # Dispatch the input and rapid keys in the same browser task so this test
    # deterministically exercises the pre-debounce window.  No history item or
    # stale match may remain available for the key handlers to activate.
    debounce_state = page.evaluate("""() => {
        const input = document.querySelector('#search-input');
        input.value = 'ymkd';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        for (const key of ['ArrowDown', 'Enter']) {
            input.dispatchEvent(new KeyboardEvent('keydown', {
                key,
                bubbles: true,
                cancelable: true
            }));
        }
        return {
            value: input.value,
            itemCount: document.querySelectorAll(
                '#search-results .search-item'
            ).length,
            loading: document.querySelector('#search-results').textContent,
            matches: window._sm,
            events: window.__selectSymbolEvents
        };
    }""")
    assert debounce_state['value'] == 'ymkd'
    assert debounce_state['itemCount'] == 0
    assert '搜索中' in debounce_state['loading']
    assert debounce_state['matches'] == []
    assert debounce_state['events'] == []

    # Wait for both results. Product rendering preselects the first item.
    page.wait_for_function(
        "document.querySelectorAll('#search-results .search-item[data-idx]').length === 2",
        timeout=8000,
    )
    page.wait_for_selector(
        '#search-results .search-item.selected[data-idx="0"]'
    )
    assert page.locator(
        '#search-results .search-item.selected[data-idx="0"] .search-code'
    ).inner_text() == '02359'

    # ArrowDown must move selection from the first result to 603259.
    page.press('#search-input', 'ArrowDown')
    page.wait_for_selector(
        '#search-results .search-item.selected[data-idx="1"]'
    )
    assert page.locator(
        '#search-results .search-item.selected[data-idx="1"] .search-code'
    ).inner_text() == '603259'

    # Press Enter to select
    page.press('#search-input', 'Enter')

    # The search must dispatch the product's public selection event.
    page.wait_for_function(
        "window.__selectSymbolEvents.some(e => e.code === '603259')",
        timeout=5000,
    )
    select_events = page.evaluate("window.__selectSymbolEvents")
    ym_event = next(
        (e for e in select_events if e.get('code') == '603259'),
        None,
    )
    assert ym_event is not None, \
        f"Expected select-symbol(603259), got: {select_events}"
    assert ym_event['type'] == 'stock', ym_event
    assert ym_event['source'] == 'bottom-search', ym_event
    assert ym_event['trigger'] == 'enter', ym_event

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

    # setSymbol must load through the real BoardDatafeed and apply that result
    # to the mock chart, rather than swapping metadata over stale chart data.
    page.wait_for_function(
        "window.__kline_chart.getDataList().length > 0 && "
        "window.__kline_chart.getDataList()[0].open === 600",
        timeout=5000,
    )
    assert '/api/kline/stock/603259' in mock_state.kline_requests, \
        f"Expected stock kline request, got: {mock_state.kline_requests}"
    current_bars = page.evaluate("window.__kline_chart.getDataList()")
    assert current_bars[0]['open'] == 600.0
    assert current_bars != previous_bars, \
        "603259 chart data must replace the previously displayed symbol data"

    page.wait_for_function(
        "window.__board_ctx && window.__board_ctx.code === '603259'",
        timeout=5000,
    )
    board_ctx = page.evaluate("window.__board_ctx")
    assert board_ctx['code'] == '603259', \
        f"Expected window.__board_ctx.code=603259, got: {board_ctx}"

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


def test_06_bar_replay_select_step_and_exit_restore_chart_data(page, mock_state):
    """Scenario 6: select replay start, step one bar, then exit to full data."""
    page.wait_for_selector('#bar-replay-btn', state='visible', timeout=8000)
    full_count = page.evaluate(
        "window.__kline_chart && window.__kline_chart.getDataList().length"
    )
    assert full_count == 12, f"Expected 12 mock bars, got {full_count}"

    page.click('#bar-replay-btn')
    page.wait_for_function(
        "document.querySelector('#bar-replay-controls').dataset.state === 'selecting'"
    )

    box = page.locator('.mock-chart-surface').bounding_box()
    assert box, 'chart container must be measurable for replay start selection'
    page.mouse.click(box['x'] + box['width'] * 0.35, box['y'] + box['height'] * 0.45)
    page.wait_for_function(
        "arg => window.__kline_chart.getDataList().length < arg",
        arg=full_count,
    )
    replay_count = page.evaluate("window.__kline_chart.getDataList().length")
    assert replay_count < full_count

    page.click('#bar-replay-step')
    page.wait_for_function(
        "arg => window.__kline_chart.getDataList().length === arg + 1",
        arg=replay_count,
    )
    assert page.evaluate("window.__kline_chart.getDataList().length") == replay_count + 1

    page.click('#bar-replay-exit')
    page.wait_for_function(
        "arg => window.__kline_chart.getDataList().length === arg",
        arg=full_count,
    )
    assert page.evaluate("window.__kline_chart.getDataList().length") == full_count


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
