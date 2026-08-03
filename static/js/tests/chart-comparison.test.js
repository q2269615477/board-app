const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const DAY = 86400000;

function makeElement(tagName, byId) {
  const listeners = new Map();
  let text = '';
  const attributes = Object.create(null);
  const element = {
    tagName: String(tagName || 'div').toUpperCase(),
    id: '', type: '', className: '', value: '',
    style: {}, dataset: {}, children: [], parentNode: null,
    clientWidth: 800, clientHeight: 400,
    appendChild(child) {
      if (child.parentNode && child.parentNode.removeChild) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.children.push(child);
      if (child.id) byId.set(child.id, child);
      return child;
    },
    insertBefore(child, reference) {
      if (!reference || !this.children.includes(reference)) return this.appendChild(child);
      if (child.parentNode && child.parentNode.removeChild) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.children.splice(this.children.indexOf(reference), 0, child);
      if (child.id) byId.set(child.id, child);
      return child;
    },
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index >= 0) this.children.splice(index, 1);
      child.parentNode = null;
      return child;
    },
    setAttribute(name, value) {
      const stringValue = String(value);
      attributes[name] = stringValue;
      this[name] = stringValue;
      if (name === 'id') {
        this.id = stringValue;
        byId.set(stringValue, this);
      }
      if (name === 'class') this.className = stringValue;
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = stringValue;
    },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name); },
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      listeners.set(name, (listeners.get(name) || []).filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      event.target = event.target || this;
      event.currentTarget = this;
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
      return true;
    },
    getBoundingClientRect() {
      return { left: 0, top: 0, bottom: 30, width: this.clientWidth, height: this.clientHeight };
    },
    contains(node) {
      if (node === this) return true;
      return this.children.some((child) => child.contains && child.contains(node));
    },
    focus() {},
  };

  Object.defineProperty(element, 'firstChild', { get: () => element.children[0] || null });
  Object.defineProperty(element, 'textContent', {
    get: () => text + element.children.map((child) => child.textContent || '').join(''),
    set: (value) => {
      text = String(value == null ? '' : value);
      if (text === '') element.children.length = 0;
    },
  });
  Object.defineProperty(element, 'classList', {
    value: {
      add(...names) {
        const values = new Set(String(element.className || '').split(/\s+/).filter(Boolean));
        names.forEach((name) => values.add(name));
        element.className = Array.from(values).join(' ');
      },
      remove(...names) {
        const values = new Set(String(element.className || '').split(/\s+/).filter(Boolean));
        names.forEach((name) => values.delete(name));
        element.className = Array.from(values).join(' ');
      },
      contains(name) { return String(element.className || '').split(/\s+/).includes(name); },
      toggle(name, force) {
        const present = this.contains(name);
        const next = force == null ? !present : Boolean(force);
        if (next) this.add(name); else this.remove(name);
        return next;
      },
    },
  });

  function matches(selector, node) {
    const value = String(selector || '').trim();
    if (!value) return false;
    if (value.includes(',')) return value.split(',').some((part) => matches(part, node));
    const tag = value.match(/^[a-zA-Z][a-zA-Z0-9-]*/);
    if (tag && node.tagName !== tag[0].toUpperCase()) return false;
    const id = value.match(/#([\w-]+)/);
    if (id && node.id !== id[1]) return false;
    const classes = Array.from(value.matchAll(/\.([\w-]+)/g)).map((match) => match[1]);
    if (classes.some((name) => !node.classList.contains(name))) return false;
    const attrs = Array.from(value.matchAll(/\[([^\]=]+)(?:=["']?([^\]"']+)["']?)?\]/g));
    return attrs.every((match) => {
      const attr = match[1];
      const actual = node.getAttribute(attr);
      return actual !== null && (match[2] == null || actual === match[2]);
    });
  }

  function descendants(node) {
    return node.children.reduce((all, child) => all.concat(child, descendants(child)), []);
  }

  element.querySelectorAll = (selector) => descendants(element).filter((node) => matches(selector, node));
  element.querySelector = (selector) => element.querySelectorAll(selector)[0] || null;
  return element;
}

function makeEventTarget() {
  const listeners = new Map();
  return {
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      listeners.set(name, (listeners.get(name) || []).filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
      return true;
    },
  };
}

function loadController() {
  const byId = new Map();
  const periodBar = makeElement('div', byId);
  periodBar.className = 'klinecharts-pro-period-bar';
  const drawingBar = makeElement('div', byId);
  drawingBar.className = 'klinecharts-pro-drawing-bar';
  const body = makeElement('body', byId);
  body.appendChild(periodBar);
  body.appendChild(drawingBar);
  const documentEvents = makeEventTarget();
  const document = {
    readyState: 'loading',
    body,
    createElement: (tagName) => makeElement(tagName, byId),
    createElementNS: (_namespace, tagName) => makeElement(tagName, byId),
    getElementById: (id) => byId.get(id) || body.querySelector('#' + id),
    querySelector: (selector) => body.querySelector(selector),
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    addEventListener: documentEvents.addEventListener,
    removeEventListener: documentEvents.removeEventListener,
    dispatchEvent: documentEvents.dispatchEvent,
  };
  const windowEvents = makeEventTarget();
  const indicatorRegistrations = [];
  const window = {
    document,
    API: '',
    klinecharts: {
      ActionType: { OnVisibleRangeChange: 'onVisibleRangeChange' },
      DomPosition: { Main: 'main' },
      registerIndicator(definition) { indicatorRegistrations.push(definition); },
    },
    setTimeout,
    clearTimeout,
    addEventListener: windowEvents.addEventListener,
    removeEventListener: windowEvents.removeEventListener,
    dispatchEvent: windowEvents.dispatchEvent,
  };
  const context = vm.createContext({
    window,
    document,
    globalThis: window,
    console,
    Promise,
    Map,
    Set,
    Date,
    Math,
    setTimeout,
    clearTimeout,
  });
  const source = fs.readFileSync(require.resolve('../chart-comparison.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'chart-comparison.js' });
  return { controller: window.ChartComparisonController, window, document, periodBar, drawingBar, byId, indicatorRegistrations };
}

function makeChart(rows, initialRange) {
  const mainDom = makeElement('div', new Map());
  mainDom.clientWidth = 800;
  mainDom.clientHeight = 400;
  let data = rows.slice();
  let range = { ...initialRange };
  const subscriptions = new Map();
  const indicatorCalls = [];
  const indicators = new Map();
  return {
    getDom: () => mainDom,
    getDataList: () => data,
    getVisibleRange: () => range,
    subscribeAction(type, handler) {
      if (!subscriptions.has(type)) subscriptions.set(type, []);
      subscriptions.get(type).push(handler);
    },
    unsubscribeAction(type, handler) {
      subscriptions.set(type, (subscriptions.get(type) || []).filter((item) => item !== handler));
    },
    setRange(next) { range = { ...next }; },
    setRows(next) { data = next.slice(); },
    emitVisibleRange() {
      (subscriptions.get('onVisibleRangeChange') || []).slice().forEach((handler) => handler({ type: 'onVisibleRangeChange' }));
    },
    createIndicator(definition, isStack, paneOptions) {
      indicatorCalls.push({ type: 'create', definition, isStack, paneOptions });
      indicators.set(definition.name, { ...definition });
      return paneOptions && paneOptions.id ? paneOptions.id : 'candle_pane';
    },
    overrideIndicator(definition, paneId) {
      indicatorCalls.push({ type: 'override', definition, paneId });
      indicators.set(definition.name, { ...(indicators.get(definition.name) || {}), ...definition });
      return true;
    },
    removeIndicator(paneId, name) {
      indicatorCalls.push({ type: 'remove', paneId, name });
      indicators.delete(name);
      return true;
    },
    getIndicatorByPaneId(_paneId, name) { return indicators.get(name) || null; },
    indicatorCalls,
    indicators,
    mainDom,
  };
}

function row(timestamp, close, fields = {}) {
  return {
    timestamp,
    open: fields.open == null ? close : fields.open,
    high: fields.high == null ? close : fields.high,
    low: fields.low == null ? close : fields.low,
    close,
    volume: fields.volume == null ? 1 : fields.volume,
  };
}

function makeRows(closes, start = Date.UTC(2026, 0, 1)) {
  return closes.map((close, index) => row(start + index * DAY, close));
}

function createEnv(options = {}) {
  const env = loadController();
  const rows = options.rows || makeRows([100, 110, 120, 130]);
  let period = options.period || { timespan: 'day', multiplier: 1 };
  env.window.__board_ctx = options.context || { code: 'sh000001', name: '上证指数', type: 'index' };
  env.window.pro = { getPeriod: () => period };
  env.chart = makeChart(rows, options.range || { realFrom: 0, realTo: rows.length - 1 });
  env.controller.init(env.chart);
  env.setPeriod = (next) => { period = next; };
  return env;
}

function installKlineFetch(env, payloads) {
  const calls = [];
  env.window.fetch = (url) => {
    const value = String(url);
    calls.push(value);
    if (value.includes('/api/search')) {
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ data: [] }) });
    }
    const path = value.split('/api/kline/')[1] || '';
    const encodedCode = path.split('?')[0].split('/').pop();
    const code = decodeURIComponent(encodedCode);
    return Promise.resolve({ status: 200, json: () => Promise.resolve({ data: payloads[code] || [] }) });
  };
  return calls;
}

async function waitFor(predicate, message) {
  const deadline = Date.now() + 1200;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 15));
  }
  assert.ok(predicate(), message);
}

function overlayPaths(env) {
  return env.controller.svg.querySelectorAll('path[data-series="overlay"]');
}

function overlayPathFor(env, code) {
  return Array.from(overlayPaths(env)).find((path) => path.getAttribute('data-overlay-code') === code);
}

function pathPoints(path) {
  const d = path.getAttribute('d') || '';
  return Array.from(d.matchAll(/[ML](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g))
    .map((match) => ({ x: Number(match[1]), y: Number(match[2]) }));
}

function installPixelMapper(env, xByAbsoluteIndex, yByPrice = (price) => 500 - price) {
  const calls = [];
  env.chart.convertToPixel = (payload) => {
    calls.push(payload);
    const point = Array.isArray(payload) ? payload[0] : payload;
    const price = Number(point && point.value);
    if (Number.isFinite(price)) {
      const y = yByPrice(price);
      assert.ok(Number.isFinite(y), `missing pixel mapping for price ${price}`);
      return Array.isArray(payload) ? [{ x: 0, y }] : { x: 0, y };
    }
    const absoluteIndex = Number(point && point.dataIndex);
    const x = xByAbsoluteIndex(absoluteIndex, point && point.timestamp);
    assert.ok(Number.isFinite(x), `missing pixel mapping for dataIndex ${absoluteIndex}`);
    return Array.isArray(payload) ? [{ x, y: 0 }] : { x, y: 0 };
  };
  return calls;
}

function comparisonFor(controller, code) {
  const series = (controller.comparisons || []).find((item) => item.code === code || (item.overlay && item.overlay.code === code));
  assert.ok(series, `missing comparison series for ${code}`);
  return series.rows || series.data || series.comparison;
}

function requireMultiOverlayContract(controller) {
  ['addOverlay', 'removeOverlay', 'clearOverlays', 'setEndpointIndex'].forEach((name) => {
    assert.equal(typeof controller[name], 'function', `missing public overlay contract: ${name}`);
  });
  assert.ok(Array.isArray(controller.overlays), 'overlays must be an array');
  assert.ok(Array.isArray(controller.comparisons), 'comparisons must be an array');
}

async function addThreeOverlays(env, payloads) {
  requireMultiOverlayContract(env.controller);
  const calls = installKlineFetch(env, payloads);
  const items = [
    { code: 'sh000300', name: '沪深300', type: 'index' },
    { code: 'sz399006', name: '创业板指', type: 'index' },
    { code: 'sh000905', name: '中证500', type: 'index' },
  ];
  for (const item of items) assert.equal(await Promise.resolve(env.controller.addOverlay(item)), true);
  await waitFor(() => overlayPaths(env).length === items.length, 'three overlay paths were not drawn');
  return { calls, items };
}

function isTrafficRedOrGreen(color) {
  const match = String(color || '').match(/^#([0-9a-f]{6})$/i);
  if (!match) return false;
  const channels = [0, 2, 4].map((offset) => parseInt(match[1].slice(offset, offset + 2), 16) / 255);
  const max = Math.max(...channels);
  const min = Math.min(...channels);
  if (max === min) return false;
  let hue;
  const delta = max - min;
  if (max === channels[0]) hue = 60 * (((channels[1] - channels[2]) / delta) % 6);
  else if (max === channels[1]) hue = 60 * ((channels[2] - channels[0]) / delta + 2);
  else hue = 60 * ((channels[0] - channels[1]) / delta + 4);
  if (hue < 0) hue += 360;
  return hue <= 20 || hue >= 340 || (hue >= 80 && hue <= 165);
}

describe('ChartComparisonController legacy contracts', () => {
  test('normalizes different absolute prices and uses close only', () => {
    const env = loadController();
    const result = env.controller.computeComparison([
      {
        timestamp: 1,
        main: row(1, 100, { open: 900, high: 999, low: 1 }),
        overlay: row(1, 1000, { open: 9000, high: 9999, low: 1 }),
      },
      {
        timestamp: 2,
        main: row(2, 110, { open: 1, high: 2000, low: 2 }),
        overlay: row(2, 1200, { open: 1, high: 20000, low: 2 }),
      },
    ]);
    assert.equal(result[0].mainReturnPct, 0);
    assert.equal(result[0].overlayReturnPct, 0);
    assert.ok(Math.abs(result[1].mainReturnPct - 10) < 1e-9);
    assert.ok(Math.abs(result[1].overlayReturnPct - 20) < 1e-9);
    assert.ok(Math.abs(result[1].differencePct + 10) < 1e-9);
  });

  test('aligns common daily buckets and maps all supported periods', () => {
    const env = loadController();
    const aligned = env.controller.alignSeries(
      [row(Date.UTC(2026, 0, 1, 9), 100), row(Date.UTC(2026, 0, 2, 9), 120)],
      [row(Date.UTC(2026, 0, 1, 15), 1000), row(Date.UTC(2026, 0, 2, 15), 1100)],
      'daily',
    );
    assert.equal(aligned.length, 2);
    assert.ok(Math.abs(env.controller.computeReturns([row(1, 100), row(2, 110)])[1].returnPct - 10) < 1e-9);
    assert.equal(env.controller.periodToApi({ timespan: 'minute', multiplier: 5 }), '5m');
    assert.equal(env.controller.periodToApi({ timespan: 'hour', multiplier: 2 }), '120m');
    assert.equal(env.controller.periodToApi({ timespan: 'day', multiplier: 1 }), 'daily');
    assert.equal(env.controller.periodToApi({ timespan: 'week', multiplier: 1 }), 'weekly');
    assert.equal(env.controller.periodToApi({ timespan: 'month', multiplier: 3 }), 'quarterly');
    assert.equal(env.controller.periodToApi({ timespan: 'year', multiplier: 1 }), 'yearly');
  });

  test('preserves pinyin search URL and keyboard selection', () => {
    const env = loadController();
    assert.equal(env.controller.createSearchUrl('ymkd'), '/api/search?q=ymkd');
    assert.equal(env.controller.createSearchUrl('药明 康德'), '/api/search?q=%E8%8D%AF%E6%98%8E%20%E5%BA%B7%E5%BE%B7');
    env.controller.init({ getDom: () => null, getDataList: () => [], getVisibleRange: () => ({ from: 0, to: -1 }) });
    env.controller._renderSearchResults([
      { code: 'sh600000', name: '浦发银行', type: 'stock' },
      { code: 'sh603259', name: '药明康德', type: 'stock' },
    ]);
    let selected = null;
    env.controller.selectOverlay = (item) => { selected = item; return true; };
    const input = { value: 'ymkd' };
    env.controller._onSearchKeydown({ key: 'ArrowDown', target: input, preventDefault() {} });
    env.controller._onSearchKeydown({ key: 'Enter', target: input, preventDefault() {} });
    assert.equal(selected && selected.code, 'sh603259');
  });

  test('persists overlay selections and restores the shared five-item history', () => {
    const env = createEnv();
    let stored = [
      { code: 'sz399006', name: '创业板指', value: 'cybz', type: 'index' },
      { code: 'sh000300', name: '沪深300', value: 'hs300', type: 'index' },
    ];
    env.window.BoardSearchHistory = {
      list: () => stored.slice(0, 5),
      add: (item) => {
        stored = [item].concat(stored.filter((existing) => existing.code !== item.code)).slice(0, 5);
        return stored;
      },
    };
    env.controller._renderSearchHistory();
    assert.deepEqual(env.controller.searchResults.map((item) => item.code), ['sz399006', 'sh000300']);
    assert.match(env.document.getElementById('chart-comparison-results').textContent, /最近搜索/);

    let selected = null;
    env.controller.selectOverlay = (item) => { selected = item; return true; };
    const input = env.document.getElementById('chart-comparison-input');
    input.value = 'zzyl';
    env.controller._selectSearchResult({ code: 'sh000905', name: '中证医疗', type: 'index' });
    assert.equal(selected.code, 'sh000905');
    assert.equal(stored[0].value, 'zzyl');
    assert.equal(stored[0].code, 'sh000905');
    assert.deepEqual(env.controller.searchResults.map((item) => item.code), ['sh000905', 'sz399006', 'sh000300']);
  });

  test('K-line URL requests the complete main history window without a fixed 800-row cap', () => {
    const history = makeRows(Array.from({ length: 1201 }, (_, index) => 100 + index));
    const env = createEnv({ rows: history });
    const url = env.controller.buildKlineUrl({ code: 'sh000300', name: '沪深300', type: 'index' }, history);
    const query = new URLSearchParams(url.split('?')[1]);
    assert.match(url, /\/api\/kline\/index\/sh000300\?/);
    assert.equal(query.get('period'), 'daily');
    assert.equal(query.get('from'), String(history[0].timestamp));
    assert.equal(query.get('to'), String(history[history.length - 1].timestamp));
    assert.equal(query.get('cache_first'), '1');
    assert.ok(query.get('limit') == null || Number(query.get('limit')) >= history.length);
    assert.notEqual(query.get('limit'), '800');
  });
});

function makeRangeFixture() {
  const start = Date.UTC(2026, 0, 1);
  const period = { timespan: 'day', multiplier: 1 };
  const mainRows = [
    row(start, 110, { open: 100, high: 9999, low: 1 }),
    row(start + DAY, 120, { open: 111, high: 8888, low: 2 }),
    row(start + 2 * DAY, 132, { open: 120, high: 7777, low: 3 }),
  ];
  const overlayRows = [
    row(start, 220, { open: 200, high: 99999, low: 10 }),
    row(start + DAY, 230, { open: 210, high: 88888, low: 20 }),
    row(start + 2 * DAY, 250, { open: 200, high: 77777, low: 30 }),
  ];
  return { start, period, mainRows, overlayRows };
}

function makeValidRangeRow(timestamp, open, close) {
  return row(timestamp, close, {
    open,
    high: Math.max(open, close) + 10,
    low: Math.min(open, close) - 10,
  });
}

function assertClose(actual, expected, message) {
  assert.equal(typeof actual, 'number', message || 'expected a numeric result');
  assert.ok(Math.abs(actual - expected) <= 1e-9, `${message || 'values differ'}: ${actual} !== ${expected}`);
}

function assertRangePercentages(result, expected) {
  assert.ok(result, 'range comparison must return a result for valid endpoints');
  assertClose(result.mainReturnPct, expected.mainReturnPct, 'mainReturnPct');
  assertClose(result.overlayReturnPct, expected.overlayReturnPct, 'overlayReturnPct');
  assertClose(result.differencePct, expected.differencePct, 'differencePct');
}

function rangeRectangle(env) {
  return env.controller.svg.querySelectorAll('rect[data-comparison-range="true"]');
}

function rangeObjects(env) {
  return env.controller.svg.querySelectorAll('g[data-comparison-range-object]');
}

function rangeText(env) {
  return Array.from(rangeObjects(env), (node) => node.textContent).join('\n');
}

async function createRangeOverlayEnv() {
  const fixture = makeRangeFixture();
  const env = createEnv({ rows: fixture.mainRows, range: { realFrom: 0, realTo: 2 } });
  const calls = installKlineFetch(env, { sh000300: fixture.overlayRows });
  assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
  await waitFor(() => env.controller.overlays[0].rows.length === fixture.overlayRows.length, 'range overlay did not load');
  return { env, calls, fixture };
}

test('comparison summary can be freely dragged inside the main pane and keeps its position after redraw', async () => {
  const { env } = await createRangeOverlayEnv();
  const summary = env.controller.summary;
  summary.clientWidth = 240;
  summary.clientHeight = 80;
  const down = {
    type: 'mousedown', button: 0, clientX: 20, clientY: 330,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; },
  };
  summary.dispatchEvent(down);
  assert.equal(down.prevented, true);
  assert.equal(down.stopped, true);
  assert.equal(down.__boardDrawingHandled, true);

  env.document.dispatchEvent({
    type: 'mousemove', clientX: 300, clientY: 150,
    preventDefault() {}, stopPropagation() {},
  });
  env.document.dispatchEvent({
    type: 'mouseup', clientX: 300, clientY: 150,
    preventDefault() {}, stopPropagation() {},
  });
  assert.equal(summary.style.left, '290px');
  assert.equal(summary.style.top, '132px');
  assert.equal(summary.style.right, 'auto');
  assert.equal(summary.style.bottom, 'auto');

  env.controller._draw();
  assert.equal(summary.style.left, '290px');
  assert.equal(summary.style.top, '132px');

  summary.dispatchEvent({
    type: 'mousedown', button: 0, clientX: 300, clientY: 150,
    preventDefault() {}, stopPropagation() {},
  });
  env.document.dispatchEvent({
    type: 'mousemove', clientX: 2000, clientY: 2000,
    preventDefault() {}, stopPropagation() {},
  });
  env.document.dispatchEvent({
    type: 'mouseup', clientX: 2000, clientY: 2000,
    preventDefault() {}, stopPropagation() {},
  });
  assert.equal(summary.style.left, '560px');
  assert.equal(summary.style.top, '320px');
});

test('comparison endpoint has a wide horizontal drag target and recalculates at the dragged date', async () => {
  const { env, fixture } = await createRangeOverlayEnv();
  let hit = env.controller.svg.querySelector('circle[data-comparison-endpoint-hit="true"]');
  assert.ok(hit, 'the endpoint needs a larger invisible drag target');
  const down = {
    type: 'mousedown', button: 0, clientX: 790, clientY: 200,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; },
  };
  hit.dispatchEvent(down);
  assert.equal(down.prevented, true);
  assert.equal(down.stopped, true);
  assert.equal(down.__boardDrawingHandled, true);

  env.document.dispatchEvent({
    type: 'mousemove', clientX: 5, clientY: 200,
    preventDefault() {}, stopPropagation() {},
  });
  env.document.dispatchEvent({
    type: 'mouseup', clientX: 5, clientY: 200,
    preventDefault() {}, stopPropagation() {},
  });
  assert.equal(env.controller.endpoint, 0);
  assert.equal(env.controller.endpointPinned, true);
  assert.equal(env.controller.endpointTimestamp, fixture.mainRows[0].timestamp);
  assert.match(env.controller.summary.textContent, /2026-01-01/);
  const endpoint = env.controller.svg.querySelector('circle[data-comparison-endpoint="true"]');
  const endpointLine = env.controller.svg.querySelector('line[data-comparison-endpoint-line="true"]');
  assert.equal(Number(endpoint.getAttribute('cx')), Number(endpointLine.getAttribute('x1')));
  assert.ok(Number(endpoint.getAttribute('cx')) < 20);
});

const MINUTE = 60000;
const PERIOD_MATRIX = [
  { label: '1m', api: '1m', period: { timespan: 'minute', multiplier: 1 }, stepMs: MINUTE, overlayOffsetMs: 20000 },
  { label: '5m', api: '5m', period: { timespan: 'minute', multiplier: 5 }, stepMs: 5 * MINUTE, overlayOffsetMs: 2 * MINUTE },
  { label: '15m', api: '15m', period: { timespan: 'minute', multiplier: 15 }, stepMs: 15 * MINUTE, overlayOffsetMs: 5 * MINUTE },
  { label: '1H', api: '60m', period: { timespan: 'hour', multiplier: 1 }, stepMs: 60 * MINUTE, overlayOffsetMs: 20 * MINUTE },
  { label: '2H', api: '120m', period: { timespan: 'hour', multiplier: 2 }, stepMs: 120 * MINUTE, overlayOffsetMs: 30 * MINUTE },
  { label: '4H', api: '240m', period: { timespan: 'hour', multiplier: 4 }, stepMs: 240 * MINUTE, overlayOffsetMs: 60 * MINUTE },
  { label: 'daily', api: 'daily', period: { timespan: 'day', multiplier: 1 }, calendarStep: 'day', overlayOffsetMs: 6 * 60 * MINUTE },
  { label: 'weekly', api: 'weekly', period: { timespan: 'week', multiplier: 1 }, calendarStep: 'week', overlayOffsetMs: 2 * 86400000 },
  { label: 'monthly', api: 'monthly', period: { timespan: 'month', multiplier: 1 }, calendarStep: 'month', overlayOffsetMs: 12 * 60 * MINUTE },
  { label: 'quarterly', api: 'quarterly', period: { timespan: 'month', multiplier: 3 }, calendarStep: 'quarter', overlayOffsetMs: 10 * 86400000 },
  { label: 'yearly', api: 'yearly', period: { timespan: 'year', multiplier: 1 }, calendarStep: 'year', overlayOffsetMs: 90 * 86400000 },
];

function periodMatrixTimestamp(periodCase, index) {
  if (!periodCase.calendarStep) return Date.UTC(2026, 0, 5, 8, 0) + index * periodCase.stepMs;
  if (periodCase.calendarStep === 'day') return Date.UTC(2026, 0, 5 + index, 8, 0);
  if (periodCase.calendarStep === 'week') return Date.UTC(2026, 0, 5 + index * 7, 8, 0);
  if (periodCase.calendarStep === 'month') return Date.UTC(2026, index, 5, 8, 0);
  if (periodCase.calendarStep === 'quarter') return Date.UTC(2026, index * 3, 5, 8, 0);
  return Date.UTC(2026 + index, 0, 5, 8, 0);
}

function makePeriodMatrixFixture(periodCase) {
  const mainPrices = [
    { open: 100, close: 110 },
    { open: 120, close: 140 },
    { open: 130, close: 160 },
    { open: 150, close: 180 },
  ];
  const overlayPrices = [
    [
      { open: 200, close: 220 },
      { open: 210, close: 230 },
      { open: 230, close: 280 },
      { open: 280, close: 300 },
    ],
    [
      { open: 400, close: 380 },
      { open: 380, close: 450 },
      { open: 420, close: 500 },
      { open: 500, close: 560 },
    ],
  ];
  const mainRows = mainPrices.map((price, index) => makeValidRangeRow(periodMatrixTimestamp(periodCase, index), price.open, price.close));
  const overlayRows = overlayPrices.map((prices) => prices.map((price, index) => makeValidRangeRow(
    periodMatrixTimestamp(periodCase, index) + periodCase.overlayOffsetMs,
    price.open,
    price.close,
  )));
  return { mainRows, overlayRows };
}

function assertPeriodMatrixRows(env, periodCase, mainRows, overlayRows) {
  assert.equal(env.controller.periodToApi(periodCase.period), periodCase.api, `${periodCase.label} periodToApi`);
  assert.equal(new Set(mainRows.map((item) => item.timestamp)).size, mainRows.length, `${periodCase.label} main timestamps collide`);
  overlayRows.forEach((rows, index) => {
    assert.equal(new Set(rows.map((item) => item.timestamp)).size, rows.length, `${periodCase.label} overlay ${index} timestamps collide`);
    const aligned = env.controller.alignSeries(mainRows, rows, periodCase.period);
    assert.equal(aligned.length, mainRows.length, `${periodCase.label} overlay ${index} did not align by period bucket`);
    assert.deepEqual(aligned.map((item) => item.timestamp), mainRows.map((item) => item.timestamp));
    assert.ok(aligned.every((item) => item.main.timestamp !== item.overlay.timestamp), `${periodCase.label} fixture must exercise shifted same-bucket timestamps`);
  });
}

async function waitForOverlayRows(env, expectedLength) {
  await waitFor(() => env.controller.overlays.length === 2 && env.controller.overlays.every((item) => item.rows.length === expectedLength), 'period matrix overlays did not load');
}

describe('ChartComparisonController 11-period overlay/range/replay matrix', { concurrency: false }, () => {
  PERIOD_MATRIX.forEach((periodCase) => {
    test(`${periodCase.label} maps, aligns, ranges, and replays locally`, async () => {
      const fixture = makePeriodMatrixFixture(periodCase);
      const env = createEnv({ rows: fixture.mainRows, period: periodCase.period, range: { realFrom: 0, realTo: 3 } });
      assertPeriodMatrixRows(env, periodCase, fixture.mainRows, fixture.overlayRows);

      const calls = installKlineFetch(env, {
        sh000300: fixture.overlayRows[0],
        sz399006: fixture.overlayRows[1],
      });
      assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
      assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sz399006', name: '创业板指', type: 'index' })), true);
      await waitForOverlayRows(env, fixture.mainRows.length);
      const overlayUrls = calls.filter((url) => url.includes('/api/kline/'));
      assert.equal(overlayUrls.length, 2, `${periodCase.label} should fetch each overlay exactly once`);
      overlayUrls.forEach((url) => assert.match(url, new RegExp('period=' + periodCase.api.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))));

      const rangeResults = fixture.overlayRows.map((rows) => env.controller.computeRangeComparison(
        fixture.mainRows,
        rows,
        periodCase.period,
        0,
        2,
      ));
      assertRangePercentages(rangeResults[0], {
        mainReturnPct: (160 / 110 - 1) * 100,
        overlayReturnPct: (280 / 220 - 1) * 100,
        differencePct: (160 / 110 - 280 / 220) * 100,
      });
      assertRangePercentages(rangeResults[1], {
        mainReturnPct: (160 / 110 - 1) * 100,
        overlayReturnPct: (500 / 380 - 1) * 100,
        differencePct: (160 / 110 - 500 / 380) * 100,
      });

      assert.equal(env.controller.startRangeSelection(), true);
      assert.equal(env.controller.setRangeSelectionIndices(0, 2), true);
      assert.equal(rangeRectangle(env).length, 1, `${periodCase.label} range rectangle missing`);
      const rangeSummary = rangeText(env);
      assert.match(rangeSummary, /沪深300/);
      assert.match(rangeSummary, /创业板指/);
      assert.match(rangeSummary, /相对跌幅 18\.18 个百分点/);
      assert.match(rangeSummary, /相对跌幅 13\.88 个百分点/);

      const replayEnv = createEnv({ rows: fixture.mainRows.slice(0, 2), period: periodCase.period, range: { realFrom: 0, realTo: 1 } });
      replayEnv.window.dispatchEvent({
        type: 'bar-replay-start',
        detail: { history: fixture.mainRows, cursor: 1, bar: fixture.mainRows[1], visibleBars: fixture.mainRows.slice(0, 2), status: 'paused' },
      });
      const replayCalls = installKlineFetch(replayEnv, {
        sh000300: fixture.overlayRows[0],
        sz399006: fixture.overlayRows[1],
      });
      assert.equal(await Promise.resolve(replayEnv.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
      assert.equal(await Promise.resolve(replayEnv.controller.addOverlay({ code: 'sz399006', name: '创业板指', type: 'index' })), true);
      await waitForOverlayRows(replayEnv, fixture.mainRows.length);
      assert.ok(replayCalls.every((url) => url.includes('period=' + periodCase.api)), `${periodCase.label} replay URL period mismatch`);
      const requestCount = replayCalls.length;
      assert.equal(replayEnv.controller.startRangeSelection(), true);
      assert.equal(replayEnv.controller.setRangeSelectionIndices(0, 1), true);

      replayEnv.chart.setRows(fixture.mainRows.slice(0, 3));
      replayEnv.chart.setRange({ realFrom: 0, realTo: 2 });
      replayEnv.window.dispatchEvent({
        type: 'bar-replay-cursor',
        detail: { history: fixture.mainRows, cursor: 2, bar: fixture.mainRows[2], visibleBars: fixture.mainRows.slice(0, 3), status: 'paused' },
      });
      await waitFor(() => replayEnv.controller.overlays.every((item) => item.comparison.length === 3), `${periodCase.label} replay cursor did not advance overlays locally`);
      assert.equal(replayCalls.length, requestCount, `${periodCase.label} replay cursor must not refetch overlays`);
      assert.equal(replayEnv.controller.getRangeSelection().startIndex, 0);
      assert.equal(replayEnv.controller.getRangeSelection().endIndex, 1);
      assert.equal(rangeRectangle(replayEnv).length, 1, `${periodCase.label} replay range rectangle disappeared`);
    });
  });
});

describe('ChartComparisonController range selection contract', { concurrency: false }, () => {
  test('uses left close to right close for both main and overlay series', () => {
    const env = loadController();
    const { period, mainRows, overlayRows } = makeRangeFixture();
    const result = env.controller.computeRangeComparison(mainRows, overlayRows, period, 0, 2);
    assertRangePercentages(result, {
      mainReturnPct: 20,
      overlayReturnPct: (250 / 220 - 1) * 100,
      differencePct: 20 - (250 / 220 - 1) * 100,
    });
  });

  test('ignores open, high and low and normalizes reversed indices', () => {
    const env = loadController();
    const { period, mainRows, overlayRows } = makeRangeFixture();
    const forward = env.controller.computeRangeComparison(mainRows, overlayRows, period, 0, 2);
    const reverse = env.controller.computeRangeComparison(mainRows, overlayRows, period, 2, 0);
    const expected = {
      mainReturnPct: 20,
      overlayReturnPct: (250 / 220 - 1) * 100,
      differencePct: 20 - (250 / 220 - 1) * 100,
    };
    assertRangePercentages(forward, expected);
    assertRangePercentages(reverse, expected);
    assert.equal(reverse.startIndex, 0, 'reversed selection must normalize startIndex');
    assert.equal(reverse.endIndex, 2, 'reversed selection must normalize endIndex');
  });

  test('uses the first and last common period buckets when overlay endpoints are missing', () => {
    const env = loadController();
    const { period, mainRows, overlayRows } = makeRangeFixture();
    const missingLeft = overlayRows.slice(1);
    const missingRight = overlayRows.slice(0, 2);
    const missingMiddle = [overlayRows[0], overlayRows[2]];
    const invalidOpen = overlayRows.map((item) => ({ ...item, open: 0 }));
    const invalidClose = overlayRows.map((item) => ({ ...item, close: 'not-a-price' }));
    const leftResult = env.controller.computeRangeComparison(mainRows, missingLeft, period, 0, 2);
    const rightResult = env.controller.computeRangeComparison(mainRows, missingRight, period, 0, 2);
    const middleResult = env.controller.computeRangeComparison(mainRows, missingMiddle, period, 0, 2);
    assert.equal(leftResult.startIndex, 1);
    assert.equal(leftResult.endIndex, 2);
    assert.equal(leftResult.startTimestamp, mainRows[1].timestamp);
    assert.equal(rightResult.startIndex, 0);
    assert.equal(rightResult.endIndex, 1);
    assert.equal(rightResult.endTimestamp, mainRows[1].timestamp);
    assert.equal(middleResult.startIndex, 0);
    assert.equal(middleResult.endIndex, 2);
    assert.equal(middleResult.endTimestamp, mainRows[2].timestamp);
    assertRangePercentages(env.controller.computeRangeComparison(mainRows, invalidOpen, period, 0, 2), {
      mainReturnPct: 20,
      overlayReturnPct: (250 / 220 - 1) * 100,
      differencePct: 20 - (250 / 220 - 1) * 100,
    });
    assert.equal(env.controller.computeRangeComparison(mainRows, invalidClose, period, 0, 2), null);
  });

  test('computes three overlay results independently', () => {
    const env = loadController();
    const { period, mainRows, start } = makeRangeFixture();
    const overlays = [
      [
        makeValidRangeRow(start, 200, 220),
        makeValidRangeRow(start + DAY, 225, 230),
        makeValidRangeRow(start + 2 * DAY, 200, 250),
      ],
      [
        makeValidRangeRow(start, 400, 400),
        makeValidRangeRow(start + DAY, 380, 350),
        makeValidRangeRow(start + 2 * DAY, 400, 360),
      ],
      [
        makeValidRangeRow(start, 80, 80),
        makeValidRangeRow(start + DAY, 85, 90),
        makeValidRangeRow(start + 2 * DAY, 80, 100),
      ],
    ];
    const expected = [
      { mainReturnPct: 20, overlayReturnPct: (250 / 220 - 1) * 100, differencePct: 20 - (250 / 220 - 1) * 100 },
      { mainReturnPct: 20, overlayReturnPct: -10, differencePct: 30 },
      { mainReturnPct: 20, overlayReturnPct: 25, differencePct: -5 },
    ];
    overlays.forEach((rows, index) => {
      const result = env.controller.computeRangeComparison(mainRows, rows, period, 0, 2);
      assertRangePercentages(result, expected[index]);
    });
  });

  test('setRangeSelectionIndices draws a persistent on-chart range object', async () => {
    const { env } = await createRangeOverlayEnv();
    const rangeTool = env.document.getElementById('chart-comparison-range-select');
    assert.ok(rangeTool);
    assert.equal(rangeTool.parentNode, env.drawingBar);
    assert.equal(rangeTool.getAttribute('aria-label'), '框选区间');
    assert.match(rangeTool.title, /框选区间/);
    assert.equal(rangeTool.textContent, '');
    assert.ok(rangeTool.querySelector('.board-comparison-range-glyph'));
    assert.ok(rangeTool.querySelector('.board-comparison-range-glyph-frame'));
    assert.ok(rangeTool.querySelector('.board-comparison-range-glyph-measure'));
    assert.equal(env.document.getElementById('chart-comparison-range-clear'), null);
    assert.equal(env.controller.setRangeSelectionIndices(2, 0), true);
    const selection = env.controller.getRangeSelection();
    assert.ok(selection);
    assert.equal(selection.startIndex, 0);
    assert.equal(selection.endIndex, 2);
    assert.equal(selection.startTimestamp, Date.UTC(2026, 0, 1));
    assert.equal(selection.endTimestamp, Date.UTC(2026, 0, 3));
    assert.equal(rangeRectangle(env).length, 1);
    assert.equal(rangeObjects(env).length, 1);
    const summary = rangeText(env);
    assert.match(summary, /2026-01-01/);
    assert.match(summary, /2026-01-03/);
    assert.match(summary, /上证指数/);
    assert.match(summary, /沪深300/);
    assert.match(summary, /上证指数/);
    assert.doesNotMatch(summary, /差/);
    assert.match(summary, /相对跌幅 6\.36 个百分点/);
    assert.match(summary, /\+20\.00%/);
    assert.match(summary, /\+13\.64%/);
  });

  test('clearRangeSelection removes the rectangle and restores endpoint summary', async () => {
    const { env } = await createRangeOverlayEnv();
    const endpointSummary = env.controller.summary.textContent;
    assert.equal(env.controller.setRangeSelectionIndices(0, 2), true);
    assert.equal(rangeRectangle(env).length, 1);
    env.controller.clearRangeSelection();
    assert.equal(rangeRectangle(env).length, 0);
    assert.equal(env.controller.summary.textContent, endpointSummary);
    assert.equal(env.controller.getRangeSelection(), null);
  });

  test('uses two left clicks: first fixes the start, mouse movement previews, second fixes the end', async () => {
    const { env } = await createRangeOverlayEnv();
    assert.equal(env.controller.startRangeSelection(), true);
    let interaction = env.controller.svg.querySelector('[data-comparison-range-interaction="true"]');
    interaction.dispatchEvent({
      type: 'click', button: 0, clientX: 120, clientY: 90,
      preventDefault() {}, stopPropagation() {},
    });
    env.document.dispatchEvent({ type: 'mouseup', clientX: 120, clientY: 90, preventDefault() {} });
    assert.equal(env.controller.getRangeSelection(), null, 'the first click must only fix the start point');
    assert.equal(env.controller.state.rangeSelecting, true);
    assert.equal(env.controller.state.rangeDragging, true);
    assert.notEqual(env.controller.rangeDraft.startIndex, null);

    env.document.dispatchEvent({ type: 'mousemove', clientX: 500, clientY: 220, preventDefault() {} });
    assert.notEqual(env.controller.rangeDraft.endIndex, env.controller.rangeDraft.startIndex);
    assert.equal(rangeRectangle(env).length, 1, 'moving after the first click must preview the rectangle');

    interaction = env.controller.svg.querySelector('[data-comparison-range-interaction="true"]');
    const wheel = { type: 'wheel', prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    interaction.dispatchEvent(wheel);
    assert.equal(wheel.prevented, false, 'wheel zoom must remain available while choosing the second point');
    assert.equal(wheel.stopped, false, 'the native chart must receive the wheel event');

    interaction.dispatchEvent({ type: 'click', button: 0, clientX: 500, clientY: 220, preventDefault() {}, stopPropagation() {} });
    assert.ok(env.controller.getRangeSelection(), 'the second click should complete the range');
    assert.equal(env.controller.state.rangeSelecting, false);
    assert.equal(env.controller.state.rangeDragging, false);
    assert.equal(env.controller.svg.style.overflow, 'hidden', 'range graphics must be clipped to the main pane');
    const rectangle = rangeRectangle(env)[0];
    assert.ok(Number(rectangle.getAttribute('width')) >= 8, 'the completed range must remain visibly rectangular');
    const rectangleBottom = Number(rectangle.getAttribute('y')) + Number(rectangle.getAttribute('height'));
    assert.ok(rectangleBottom <= env.chart.mainDom.clientHeight + 0.001, `rectangle bottom ${rectangleBottom} exceeded main pane height`);
  });

  test('keeps completed ranges anchored to dates when wheel zoom changes the visible window', () => {
    const rows = makeRows([100, 105, 110, 115, 120, 125]);
    const env = createEnv({ rows, range: { realFrom: 0, realTo: 5 } });
    assert.equal(env.controller.setRangeSelectionIndices(3, 5), true);
    assert.ok(Number(rangeRectangle(env)[0].getAttribute('width')) > 2);

    env.chart.setRange({ realFrom: 0, realTo: 1 });
    env.controller._draw();
    assert.equal(rangeRectangle(env).length, 0, 'an off-screen range must be hidden instead of collapsing onto the left edge');

    env.chart.setRange({ realFrom: 2, realTo: 5 });
    env.controller._draw();
    const restored = rangeRectangle(env)[0];
    assert.ok(restored, 'the date-anchored range should return when its dates are visible again');
    assert.ok(Number(restored.getAttribute('x')) > 8, 'the restored range must not be pinned to the screen left edge');
    assert.ok(Number(restored.getAttribute('width')) > 2, 'the restored range must remain a rectangle');
  });

  test('anchors range corners to K-line closes and redraws round endpoints after price-scale zoom', () => {
    const rows = makeRows([100, 110, 120, 130]);
    const env = createEnv({ rows, range: { realFrom: 0, realTo: 3 } });
    let scale = 2;
    installPixelMapper(env, (absoluteIndex) => 40 + absoluteIndex * 100, (price) => 380 - price * scale);
    assert.equal(env.controller.setRangeSelectionIndices(1, 3), true);

    let rectangle = rangeRectangle(env)[0];
    let anchors = env.controller.svg.querySelectorAll('circle[data-comparison-range-anchor]');
    assert.equal(Number(rectangle.getAttribute('x')), 140);
    assert.equal(Number(rectangle.getAttribute('y')), 120);
    assert.equal(Number(rectangle.getAttribute('height')), 40);
    assert.equal(anchors.length, 2);
    assert.deepEqual(Array.from(anchors, (dot) => [
      dot.getAttribute('data-comparison-range-anchor'),
      Number(dot.getAttribute('cx')),
      Number(dot.getAttribute('cy')),
    ]), [['start', 140, 160], ['end', 340, 120]]);

    scale = 1;
    env.controller._draw();
    rectangle = rangeRectangle(env)[0];
    anchors = env.controller.svg.querySelectorAll('circle[data-comparison-range-anchor]');
    assert.equal(Number(rectangle.getAttribute('y')), 250);
    assert.equal(Number(rectangle.getAttribute('height')), 20);
    assert.deepEqual(Array.from(anchors, (dot) => Number(dot.getAttribute('cy'))), [270, 250]);
  });

  test('uses native pixel-to-data conversion and keeps the first date while zooming between clicks', () => {
    const rows = makeRows([100, 102, 104, 106, 108, 110, 112, 114]);
    const env = createEnv({ rows, range: { realFrom: 0, realTo: 7 } });
    let nativeIndex = 3;
    env.chart.convertFromPixel = (payload) => {
      const result = { dataIndex: nativeIndex, timestamp: rows[nativeIndex].timestamp };
      return Array.isArray(payload) ? [result] : result;
    };
    assert.equal(env.controller.startRangeSelection(), true);
    let interaction = env.controller.svg.querySelector('[data-comparison-range-interaction="true"]');
    interaction.dispatchEvent({
      type: 'click', button: 0, clientX: 760, clientY: 100,
      preventDefault() {}, stopPropagation() {},
    });
    assert.equal(env.controller.rangeDraft.startTimestamp, rows[3].timestamp, 'the first point must use native chart hit testing');

    env.chart.setRange({ realFrom: 2, realTo: 6 });
    env.chart.emitVisibleRange();
    env.controller._draw();
    assert.equal(env.controller.rangeDraft.startTimestamp, rows[3].timestamp, 'zoom must not change the fixed first date');
    assert.equal(env.controller.rangeDraft.startIndex, 1, 'the fixed date must remap into the zoomed visible range');

    nativeIndex = 5;
    interaction = env.controller.svg.querySelector('[data-comparison-range-interaction="true"]');
    interaction.dispatchEvent({
      type: 'click', button: 0, clientX: 40, clientY: 220,
      preventDefault() {}, stopPropagation() {},
    });
    const selection = env.controller.getRangeSelection();
    assert.equal(selection.startTimestamp, rows[3].timestamp);
    assert.equal(selection.endTimestamp, rows[5].timestamp);
    assert.equal(selection.absoluteStartIndex, 3);
    assert.equal(selection.absoluteEndIndex, 5);
  });

  test('renders rising ranges red and falling ranges green', () => {
    const rising = createEnv({ rows: makeRows([100, 110, 120]), range: { realFrom: 0, realTo: 2 } });
    assert.equal(rising.controller.setRangeSelectionIndices(0, 2), true);
    const risingObject = rangeObjects(rising)[0];
    const risingRect = rangeRectangle(rising)[0];
    assert.equal(risingObject.getAttribute('data-comparison-range-direction'), 'up');
    assert.equal(risingRect.getAttribute('fill'), '#ef5350');
    assert.equal(risingRect.getAttribute('stroke'), '#ef5350');

    const falling = createEnv({ rows: makeRows([120, 110, 100]), range: { realFrom: 0, realTo: 2 } });
    assert.equal(falling.controller.setRangeSelectionIndices(0, 2), true);
    const fallingObject = rangeObjects(falling)[0];
    const fallingRect = rangeRectangle(falling)[0];
    assert.equal(fallingObject.getAttribute('data-comparison-range-direction'), 'down');
    assert.equal(fallingRect.getAttribute('fill'), '#26a69a');
    assert.equal(fallingRect.getAttribute('stroke'), '#26a69a');
  });

  test('right-click deletes only the range under the pointer', async () => {
    const { env } = await createRangeOverlayEnv();
    assert.equal(env.controller.setRangeSelectionIndices(0, 1), true);
    assert.equal(env.controller.setRangeSelectionIndices(1, 2), true);
    assert.equal(env.controller.getRangeSelections().length, 2);

    const second = {
      type: 'contextmenu', clientX: 600, clientY: 200,
      prevented: false, stopped: false,
      preventDefault() { this.prevented = true; },
      stopPropagation() { this.stopped = true; },
    };
    env.document.dispatchEvent(second);
    assert.equal(second.prevented, true);
    assert.equal(second.stopped, true);
    assert.equal(second.__boardDrawingHandled, true);
    assert.equal(env.controller.getRangeSelections().length, 1);
    assert.equal(env.controller.getRangeSelection().startIndex, 0);
    assert.equal(env.controller.getRangeSelection().endIndex, 1);
    assert.equal(rangeObjects(env).length, 1);

    const outside = {
      type: 'contextmenu', clientX: 200, clientY: 10, prevented: false,
      preventDefault() { this.prevented = true; }, stopPropagation() {},
    };
    env.document.dispatchEvent(outside);
    assert.equal(outside.prevented, false, 'right-click outside the rectangle must keep the browser/chart behavior');
    assert.equal(env.controller.getRangeSelections().length, 1);

    const below = {
      type: 'contextmenu', clientX: 200, clientY: 390, prevented: false,
      preventDefault() { this.prevented = true; }, stopPropagation() {},
    };
    env.document.dispatchEvent(below);
    assert.equal(below.prevented, false, 'the hit area must not extend below the visible rectangle');
    assert.equal(env.controller.getRangeSelections().length, 1);

    const first = {
      type: 'contextmenu', clientX: 200, clientY: 200,
      preventDefault() {}, stopPropagation() {},
    };
    env.document.dispatchEvent(first);
    assert.equal(env.controller.getRangeSelections().length, 0);
    assert.equal(env.controller.getRangeSelection(), null);
    assert.equal(rangeObjects(env).length, 0);
  });

  test('right-click can delete a one-K-line range through the expanded hit tolerance', async () => {
    const { env } = await createRangeOverlayEnv();
    assert.equal(env.controller.setRangeSelectionIndices(1, 1), true);
    const rectangle = rangeRectangle(env)[0];
    assert.ok(Number(rectangle.getAttribute('width')) >= 8, 'a one-K-line range must remain visibly rectangular');
    const event = {
      type: 'contextmenu',
      clientX: Number(rectangle.getAttribute('x')) + Number(rectangle.getAttribute('width')) + 4,
      clientY: Number(rectangle.getAttribute('y')) + Number(rectangle.getAttribute('height')) / 2,
      prevented: false,
      preventDefault() { this.prevented = true; },
      stopPropagation() {},
    };
    env.document.dispatchEvent(event);
    assert.equal(event.prevented, true);
    assert.equal(env.controller.getRangeSelections().length, 0);
    assert.equal(rangeObjects(env).length, 0);
  });

  test('keeps multiple completed range objects with independent on-chart details', async () => {
    const { env } = await createRangeOverlayEnv();
    assert.equal(env.controller.setRangeSelectionIndices(0, 1), true);
    assert.equal(env.controller.setRangeSelectionIndices(1, 2), true);
    assert.equal(env.controller.getRangeSelections().length, 2);
    assert.equal(rangeObjects(env).length, 2);
    assert.equal(rangeRectangle(env).length, 2);
    const text = rangeText(env);
    assert.match(text, /区间 1/);
    assert.match(text, /区间 2/);
    assert.match(text, /自然日/);
    assert.match(text, /交易日/);
    assert.match(text, /K线数量 2 根/);
    assert.match(text, /上证指数/);
    assert.match(text, /沪深300/);
    assert.equal(rangeObjects(env)[0].getAttribute('data-comparison-range-kline-count'), '2');
    assert.equal(rangeObjects(env)[1].getAttribute('data-comparison-range-kline-count'), '2');
    assert.equal(env.controller.getRangeSelection().startIndex, 1);
    assert.equal(env.controller.getRangeSelection().endIndex, 2);
  });

  test('works without overlays and releases chart pointer interaction after completion or clear', async () => {
    const empty = createEnv();
    assert.equal(empty.controller.startRangeSelection(), true);
    assert.equal(empty.controller.setRangeSelectionIndices(0, 2), true);
    assert.equal(rangeRectangle(empty).length, 1);
    assert.match(rangeText(empty), /区间 1/);
    assert.match(rangeText(empty), /上证指数 \+20\.00%/);
    assert.doesNotMatch(rangeText(empty), /暂无共同数据/);
    empty.controller.clearRangeSelection();
    assert.equal(empty.controller.getRangeSelection(), null);

    const { env } = await createRangeOverlayEnv();
    assert.equal(env.controller.startRangeSelection(), true);
    assert.equal(env.controller.state.rangeSelecting, true);
    assert.equal(env.controller.setRangeSelectionIndices(0, 1), true);
    assert.equal(env.controller.state.rangeSelecting, false);
    assert.equal(env.controller.svg.style.pointerEvents, 'none');
    const completedPointer = { type: 'pointerdown', clientX: 100, clientY: 100, prevented: false, preventDefault() { this.prevented = true; } };
    env.chart.mainDom.dispatchEvent(completedPointer);
    assert.equal(completedPointer.prevented, false);

    assert.equal(env.controller.startRangeSelection(), true);
    env.controller.clearRangeSelection();
    assert.equal(env.controller.state.rangeSelecting, false);
    assert.equal(env.controller.getRangeSelection(), null);
    assert.equal(env.controller.svg.style.pointerEvents, 'none');
    const clearedPointer = { type: 'pointerdown', clientX: 120, clientY: 120, prevented: false, preventDefault() { this.prevented = true; } };
    env.chart.mainDom.dispatchEvent(clearedPointer);
    assert.equal(clearedPointer.prevented, false);
  });

  test('recomputes the selected range with replay cursors without fetching again', async () => {
    const fixture = makeRangeFixture();
    const history = fixture.mainRows.concat(row(fixture.start + 3 * DAY, 140, { open: 132, high: 150, low: 130 }));
    const env = createEnv({ rows: history.slice(0, 2), range: { realFrom: 0, realTo: 1 } });
    env.window.dispatchEvent({
      type: 'bar-replay-start',
      detail: { history, cursor: 1, bar: history[1], visibleBars: history.slice(0, 2), status: 'paused' },
    });
    const calls = installKlineFetch(env, { sh000300: fixture.overlayRows.concat(makeValidRangeRow(fixture.start + 3 * DAY, 250, 260)) });
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
    await waitFor(() => env.controller.overlays[0].rows.length === 4, 'replay overlay did not load full history');
    env.chart.setRows(history.slice(0, 2));
    assert.equal(env.controller.startRangeSelection(), true);
    assert.equal(env.controller.setRangeSelectionIndices(0, 1), true);
    const requestCount = calls.length;

    env.chart.setRows(history.slice(0, 3));
    env.chart.setRange({ realFrom: 0, realTo: 2 });
    env.window.dispatchEvent({
      type: 'bar-replay-cursor',
      detail: { history, cursor: 2, bar: history[2], visibleBars: history.slice(0, 3), status: 'paused' },
    });
    await waitFor(() => rangeRectangle(env).length === 1, 'range rectangle disappeared after replay cursor');
    assert.equal(calls.length, requestCount, 'range replay cursor must recompute locally without a fetch');
    assert.doesNotMatch(env.controller.summary.textContent, /差/);
    assert.match(env.controller.summary.textContent, /相对跌幅/);
  });
});

describe('ChartComparisonController overlay contract', () => {
  test('real _draw renders overlay paths only and never redraws a main price or return path', () => {
    const env = createEnv();
    env.controller.overlay = { code: 'sh000300', name: '沪深300', type: 'index' };
    env.controller.overlayRows = makeRows([1000, 1100, 1200]);
    env.chart.setRows(makeRows([100, 110, 120]));
    env.chart.setRange({ realFrom: 0, realTo: 2 });
    env.controller._recomputeVisible();
    env.controller._draw();
    const paths = env.controller.svg.querySelectorAll('path');
    assert.equal(paths.length, 1, 'the main price/return path must not be drawn in comparison SVG');
    assert.equal(env.controller.svg.querySelector('path[data-series="main"]'), null);
    assert.equal(env.controller.svg.querySelector('path[data-series="main-return"]'), null);
    const overlay = env.controller.svg.querySelector('path[data-series="overlay"]');
    assert.ok(overlay, 'the comparison path must be tagged as an overlay series');
    assert.equal(overlay.getAttribute('data-overlay-code'), 'sh000300');
  });

  test('adds at least three overlays and rejects duplicate and main symbols', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1100, 1200, 1300]),
      sz399006: makeRows([2000, 2100, 2200, 2300]),
      sh000905: makeRows([3000, 2900, 3100, 3200]),
    };
    const { items } = await addThreeOverlays(env, payloads);
    assert.equal(env.controller.overlays.length, 3);
    assert.equal(await Promise.resolve(env.controller.addOverlay(items[0])), false);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000001', name: '上证指数', type: 'index' })), false);
    assert.equal(env.controller.overlays.length, 3);
  });

  test('assigns stable distinct non-traffic colors to each overlay', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1100, 1200, 1300]),
      sz399006: makeRows([2000, 2100, 2200, 2300]),
      sh000905: makeRows([3000, 2900, 3100, 3200]),
    };
    await addThreeOverlays(env, payloads);
    const paths = overlayPaths(env);
    const colors = paths.map((path) => path.getAttribute('stroke'));
    assert.equal(new Set(colors).size, 3);
    colors.forEach((color) => {
      assert.match(color, /^#[0-9a-f]{6}$/i);
      assert.equal(isTrafficRedOrGreen(color), false, `overlay color looks like traffic red/green: ${color}`);
    });
    env.controller._draw();
    assert.deepEqual(overlayPaths(env).map((path) => path.getAttribute('stroke')), colors);
  });

  test('each overlay comparison is close-based and lines share the main-bar x positions', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1200, 1100, 1300]),
      sz399006: makeRows([2000, 1800, 2200, 2400]),
      sh000905: makeRows([3000, 3300, 3600, 3900]),
    };
    await addThreeOverlays(env, payloads);
    assert.ok(Math.abs(comparisonFor(env.controller, 'sh000300')[1].overlayReturnPct - 20) < 1e-9);
    assert.ok(Math.abs(comparisonFor(env.controller, 'sz399006')[1].overlayReturnPct + 10) < 1e-9);
    assert.ok(Math.abs(comparisonFor(env.controller, 'sh000905')[1].overlayReturnPct - 10) < 1e-9);
    const points = overlayPaths(env).map(pathPoints);
    assert.ok(points.every((series) => series.length === points[0].length));
    const firstX = points[0].map((point) => point.x);
    points.slice(1).forEach((series) => assert.deepEqual(series.map((point) => point.x), firstX));
  });

  test('uses convertToPixel for overlay, baseline, endpoint, and range x coordinates', async () => {
    const env = createEnv({ rows: makeRows([100, 110, 120, 130]) });
    const networkCalls = installKlineFetch(env, { sh000300: makeRows([1000, 1100, 1200, 1300]) });
    installPixelMapper(env, (absoluteIndex) => 40 + absoluteIndex * 120);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 1, 'pixel-mapped overlay path was not drawn');

    const points = pathPoints(overlayPaths(env)[0]);
    assert.deepEqual(points.map((point) => point.x), [40, 160, 280, 400]);
    const baseline = env.controller.svg.querySelector('line[data-comparison-baseline="true"]');
    const endpointLine = env.controller.svg.querySelector('line[data-comparison-endpoint-line="true"]');
    const endpoint = env.controller.svg.querySelector('circle[data-comparison-endpoint="true"]');
    assert.equal(Number(baseline.getAttribute('x1')), 40);
    assert.equal(Number(baseline.getAttribute('x2')), 400);
    assert.equal(Number(endpointLine.getAttribute('x1')), 400);
    assert.equal(Number(endpoint.getAttribute('cx')), 400);
    assert.equal(env.controller.setRangeSelectionIndices(1, 3), true);
    const rectangle = env.controller.svg.querySelector('rect[data-comparison-range="true"]');
    assert.equal(Number(rectangle.getAttribute('x')), 160);
    assert.equal(Number(rectangle.getAttribute('width')), 240);
    assert.ok(networkCalls.length >= 1, 'the fixture should have made the overlay request');
  });

  test('converts overlay returns into equivalent main prices without changing percentage distance', () => {
    const env = loadController();
    assertClose(env.controller.returnPctToEquivalentPrice(100, 0), 100, 'zero return equivalent price');
    assertClose(env.controller.returnPctToEquivalentPrice(100, 40), 140, 'main return equivalent price');
    assertClose(env.controller.returnPctToEquivalentPrice(100, 116.82), 216.82, 'overlay return equivalent price');
    assertClose(
      env.controller.returnPctToEquivalentPrice(100, 116.82) - env.controller.returnPctToEquivalentPrice(100, 40),
      76.82,
      'equivalent-price distance must equal the relative percentage-point distance at a 100 base',
    );
  });

  test('renders overlay and main candles in the same native price coordinate system', async () => {
    const env = createEnv({ rows: makeRows([100, 140]) });
    installPixelMapper(env, (absoluteIndex) => 40 + absoluteIndex * 300, (price) => 500 - price);
    installKlineFetch(env, { sh000300: makeRows([1000, 2168.2]) });
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 1, 'equivalent-price overlay path was not drawn');
    const axis = env.controller.svg.querySelector('g[data-comparison-percent-axis="true"]');
    const baseline = env.controller.svg.querySelector('line[data-comparison-baseline="true"]');
    assert.equal(axis, null, 'a second percent axis would make the overlay incomparable with native candles');
    assert.ok(baseline, 'shared native-price baseline is required');
    assert.equal(env.controller.svg.querySelector('path[data-series="main-return"]'), null);
    const points = pathPoints(overlayPaths(env)[0]);
    assert.ok(points.length >= 2, overlayPaths(env)[0].getAttribute('d'));
    assertClose(points[0].y, 400, 'overlay must start at the main close pixel');
    assertClose(points[points.length - 1].y, 283.18, 'overlay end must use its main-price equivalent');
    assertClose(Number(baseline.getAttribute('y1')), 400, 'baseline must use the native main-price coordinate');
    assertClose(360 - points[points.length - 1].y, 76.82, 'visual endpoint gap must equal the 76.82 percentage-point excess at base 100');
    assert.equal(overlayPaths(env)[0].getAttribute('data-source'), 'close-return-equivalent-price');
    assertClose(Number(overlayPaths(env)[0].getAttribute('data-end-equivalent-price')), 216.82, 'path metadata must expose equivalent endpoint');
    const scaleIndicator = env.chart.indicators.get('BOARD_COMPARISON_SCALE');
    assert.ok(scaleIndicator, 'overlay equivalent prices must participate in candle-pane auto scale');
    assert.equal(env.indicatorRegistrations.length, 1, 'custom price-scale indicator must be registered before it is attached');
    assert.equal(env.indicatorRegistrations[0].name, 'BOARD_COMPARISON_SCALE');
    assert.ok(scaleIndicator.minValue <= 100);
    assert.ok(scaleIndicator.maxValue >= 216.82);
  });

  test('uses common dates for overlay endpoints and skips missing middle dates', async () => {
    const mainRows = makeRows([100, 110, 120, 130, 140]);
    const overlayRows = [mainRows[1], mainRows[2], mainRows[4]].map((mainRow, index) => row(
      mainRow.timestamp,
      1000 + index * 100,
      { open: 950 + index * 100 },
    ));
    const env = createEnv({ rows: mainRows });
    installPixelMapper(env, (absoluteIndex) => 30 + absoluteIndex * 90);
    installKlineFetch(env, { sh000300: overlayRows });
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 1, 'sparse overlay path was not drawn');

    const comparison = comparisonFor(env.controller, 'sh000300');
    assert.deepEqual(Array.from(comparison, (item) => item.mainIndex), [1, 2, 4]);
    assert.deepEqual(Array.from(comparison, (item) => item.timestamp), [mainRows[1].timestamp, mainRows[2].timestamp, mainRows[4].timestamp]);
    assert.deepEqual(pathPoints(overlayPaths(env)[0]).map((point) => point.x), [120, 210, 390]);
    assert.match(env.controller.summary.textContent, /2026-01-05/);
  });

  test('remaps after pan or zoom without issuing another overlay request', async () => {
    const mainRows = makeRows([100, 110, 120, 130, 140]);
    const env = createEnv({ rows: mainRows, range: { realFrom: 0, realTo: 4 } });
    const networkCalls = installKlineFetch(env, { sh000300: makeRows([1000, 1100, 1200, 1300, 1400]) });
    installPixelMapper(env, (absoluteIndex) => 25 + absoluteIndex * 75);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 1, 'pan fixture overlay path was not drawn');
    const requestCount = networkCalls.length;

    env.chart.setRange({ realFrom: 1, realTo: 3 });
    env.chart.emitVisibleRange();
    await waitFor(() => {
      const paths = overlayPaths(env);
      return paths.length === 1 && pathPoints(paths[0]).map((point) => point.x).join(',') === '100,175,250';
    }, 'overlay path did not remap to the converted visible coordinates');
    assert.equal(networkCalls.length, requestCount, 'panning/zooming must not fetch overlay data again');
  });

  test('rebases every visible overlay to the first visible main close at a shared visual origin', async () => {
    const mainRows = makeRows([100, 110, 120, 130, 140]);
    const payloads = {
      sz399006: makeRows([1000, 2000, 2100, 2200, 2300]),
      sh000905: makeRows([2000, 1000, 1200, 1300, 1400]),
    };
    const env = createEnv({ rows: mainRows, range: { realFrom: 1, realTo: 4 } });
    installPixelMapper(env, (absoluteIndex) => 50 + absoluteIndex * 100);
    installKlineFetch(env, payloads);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sz399006', name: '创业板指', type: 'index' })), true);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000905', name: '中证医疗', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 2, 'visible-baseline overlay paths were not drawn');

    const growth = comparisonFor(env.controller, 'sz399006');
    const healthcare = comparisonFor(env.controller, 'sh000905');
    [growth, healthcare].forEach((rows) => {
      assert.equal(rows[0].timestamp, mainRows[1].timestamp);
      assert.equal(rows[0].main.timestamp, mainRows[1].timestamp);
      assert.equal(rows[0].mainReturnPct, 0);
      assert.equal(rows[0].overlayReturnPct, 0);
    });
    assertClose(growth[1].mainReturnPct, (120 / 110 - 1) * 100, 'main return must use visible left close');
    assertClose(growth[1].overlayReturnPct, 5, '创业板指 must use its own same-day close as base');
    assertClose(healthcare[1].overlayReturnPct, 20, '中证医疗 must use its own same-day close as base');

    const baseline = env.controller.svg.querySelector('line[data-comparison-baseline="true"]');
    const baselineY = Number(baseline.getAttribute('y1'));
    const paths = [overlayPathFor(env, 'sz399006'), overlayPathFor(env, 'sh000905')];
    assert.ok(paths.every(Boolean), 'all overlay paths must be addressable by symbol');
    const firstPoints = paths.map((path) => pathPoints(path)[0]);
    assert.equal(new Set(firstPoints.map((point) => point.x)).size, 1, 'all overlays must share the visible left x origin');
    assert.equal(new Set(firstPoints.map((point) => point.y)).size, 1, 'all overlays must share the normalized y origin');
    assert.ok(Math.abs(firstPoints[0].y - baselineY) <= 0.01, 'normalized overlay origin must sit on the zero-return baseline');
  });

  test('rebases every overlay at the first visible common K-line without a new request', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1200, 1320, 1400]),
      sz399006: makeRows([2000, 1800, 1980, 2200]),
      sh000905: makeRows([3000, 3300, 3630, 3900]),
    };
    const { calls, items } = await addThreeOverlays(env, payloads);
    items.forEach((item) => {
      assert.equal(comparisonFor(env.controller, item.code)[0].mainReturnPct, 0);
      assert.equal(comparisonFor(env.controller, item.code)[0].overlayReturnPct, 0);
    });
    const countBeforePan = calls.length;
    env.chart.setRange({ realFrom: 1, realTo: 3 });
    env.chart.emitVisibleRange();
    await waitFor(() => env.controller.comparisons.every((series) => (series.rows || series.data)[0].mainReturnPct === 0), 'all series did not rebase');
    assert.equal(calls.length, countBeforePan, 'panning must not issue another K-line request');
    items.forEach((item) => {
      const rows = comparisonFor(env.controller, item.code);
      assert.equal(rows[0].mainReturnPct, 0);
      assert.equal(rows[0].overlayReturnPct, 0);
    });
  });

  test('lets each overlay end at its own last available date and labels relative direction', async () => {
    const mainRows = makeRows([100, 110, 120, 130, 140]);
    const payloads = {
      sz399006: makeRows([1000, 1100, 1200, 1300, 1500]),
      sh000905: makeRows([2000, 2100, 2200, 2400]),
    };
    const env = createEnv({ rows: mainRows, range: { realFrom: 0, realTo: 4 } });
    installPixelMapper(env, (absoluteIndex) => 40 + absoluteIndex * 100);
    installKlineFetch(env, payloads);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sz399006', name: '创业板指', type: 'index' })), true);
    assert.equal(await Promise.resolve(env.controller.addOverlay({ code: 'sh000905', name: '中证医疗', type: 'index' })), true);
    await waitFor(() => overlayPaths(env).length === 2, 'independent-endpoint overlay paths were not drawn');

    const growthPath = overlayPathFor(env, 'sz399006');
    const healthcarePath = overlayPathFor(env, 'sh000905');
    assert.deepEqual(pathPoints(growthPath).map((point) => point.x), [40, 140, 240, 340, 440]);
    assert.deepEqual(pathPoints(healthcarePath).map((point) => point.x), [40, 140, 240, 340]);
    assert.equal(comparisonFor(env.controller, 'sz399006').at(-1).timestamp, mainRows[4].timestamp);
    assert.equal(comparisonFor(env.controller, 'sh000905').at(-1).timestamp, mainRows[3].timestamp);

    const summary = env.controller.summary.textContent;
    assert.match(summary, /创业板指 \+50\.00% · 主图 \+40\.00% · 相对涨幅 \+10\.00 个百分点/);
    assert.match(summary, /中证医疗 \+20\.00% · 主图 \+30\.00% · 相对跌幅 10\.00 个百分点/);
    assert.doesNotMatch(summary, /差/);
  });

  test('removes one overlay and clears all remaining overlays', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1100, 1200, 1300]),
      sz399006: makeRows([2000, 2100, 2200, 2300]),
      sh000905: makeRows([3000, 3100, 3200, 3300]),
    };
    await addThreeOverlays(env, payloads);
    assert.equal(await Promise.resolve(env.controller.removeOverlay('sz399006')), true);
    assert.equal(env.controller.overlays.length, 2);
    assert.equal(overlayPaths(env).length, 2);
    await Promise.resolve(env.controller.clearOverlays());
    assert.equal(env.controller.overlays.length, 0);
    assert.equal(overlayPaths(env).length, 0);
    assert.equal(env.controller.summary.textContent, '');
  });

  test('main symbol or period changes reload every overlay item', async () => {
    const env = createEnv();
    const payloads = {
      sh000300: makeRows([1000, 1100, 1200, 1300]),
      sz399006: makeRows([2000, 2100, 2200, 2300]),
      sh000905: makeRows([3000, 3100, 3200, 3300]),
    };
    const { calls, items } = await addThreeOverlays(env, payloads);
    const countBeforeReload = calls.length;
    env.window.__board_ctx = { code: 'sz399001', name: '深证成指', type: 'index' };
    env.setPeriod({ timespan: 'week', multiplier: 1 });
    env.window.dispatchEvent({ type: 'select-symbol' });
    env.window.dispatchEvent({ type: 'kline-loaded' });
    await waitFor(() => calls.length >= countBeforeReload + items.length, 'not all overlays reloaded after context change');
    const reloaded = calls.slice(countBeforeReload);
    assert.equal(reloaded.length, 3);
    items.forEach((item) => assert.ok(reloaded.some((url) => url.includes('/' + item.code + '?') && url.includes('period=weekly'))));
  });

  test('replay cursor advances every overlay locally and adding during replay fetches the full replay window', async () => {
    const history = makeRows([100, 110, 120, 130]);
    const env = createEnv({ rows: history.slice(0, 2), range: { realFrom: 0, realTo: 1 } });
    env.window.dispatchEvent({
      type: 'bar-replay-start',
      detail: { history, cursor: 1, bar: history[1], visibleBars: history.slice(0, 2), status: 'paused' },
    });
    const calls = installKlineFetch(env, { sh000300: makeRows([1000, 1100, 1200, 1300]) });
    assert.equal(env.controller.addOverlay({ code: 'sh000300', name: '沪深300', type: 'index' }), true);
    await waitFor(() => comparisonFor(env.controller, 'sh000300').length === 2, 'initial replay comparison missing');
    assert.match(calls[0], new RegExp('to=' + history[history.length - 1].timestamp));

    const requestCount = calls.length;
    env.chart.setRows(history.slice(0, 3));
    env.chart.setRange({ realFrom: 0, realTo: 2 });
    env.window.dispatchEvent({
      type: 'bar-replay-cursor',
      detail: { history, cursor: 2, bar: history[2], visibleBars: history.slice(0, 3), status: 'paused' },
    });
    await waitFor(() => comparisonFor(env.controller, 'sh000300').length === 3, 'replay step did not advance overlay');
    assert.equal(calls.length, requestCount, 'replay steps must not refetch overlay history');
  });
});
