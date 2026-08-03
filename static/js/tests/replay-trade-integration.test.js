const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_ROOT = path.resolve(__dirname, '..');

function makeElement(tagName, registry) {
  const listeners = new Map();
  return {
    tagName: tagName.toUpperCase(),
    id: '',
    type: '',
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    clientWidth: 1000,
    clientHeight: 420,
    style: {},
    dataset: {},
    children: [],
    classList: { add() {}, remove() {} },
    appendChild(child) {
      this.children.push(child);
      child.parentNode = this;
      registry.register(child);
      return child;
    },
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index >= 0) this.children.splice(index, 1);
      child.parentNode = null;
      return child;
    },
    insertBefore(child, before) {
      const index = this.children.indexOf(before);
      if (index < 0) return this.appendChild(child);
      this.children.splice(index, 0, child);
      registry.register(child);
      return child;
    },
    querySelector(selector) {
      if (selector === '.item.tools') return this.children.find((child) => child.className === 'item tools') || null;
      return null;
    },
    setAttribute(name, value) {
      this[name] = String(value);
      if (name === 'id') this.id = String(value);
      if (name === 'class') this.className = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    },
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      const values = listeners.get(name) || [];
      listeners.set(name, values.filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight };
    },
  };
}

function nativeArray(value, mapper) {
  return Array.from(value || [], mapper);
}

function closeEnough(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(Number(actual) - expected) <= epsilon, `${actual} is not close to ${expected}`);
}

function treeSome(node, predicate) {
  if (!node) return false;
  if (predicate(node)) return true;
  return Array.from(node.children || []).some((child) => treeSome(child, predicate));
}

function treeFind(node, predicate) {
  if (!node) return null;
  if (predicate(node)) return node;
  for (const child of Array.from(node.children || [])) {
    const found = treeFind(child, predicate);
    if (found) return found;
  }
  return null;
}

function findSummaryGroup(document) {
  const summary = document.getElementById('replay-trade-summary');
  if (!summary) return null;
  return summary.children.find((child) => child.className === 'replay-trade-summary-group') || null;
}

function findSummaryBox(document) {
  const group = findSummaryGroup(document);
  if (!group) return null;
  return group.children.find((child) => child.className === 'replay-trade-summary-box') || null;
}

function descendants(node) {
  const result = [];
  const visit = (current) => {
    Array.from(current && current.children || []).forEach((child) => {
      result.push(child);
      visit(child);
    });
  };
  visit(node);
  return result;
}

function findInjectedTradeStyle(document) {
  return document.head.children.find((child) => child.id === 'replay-trade-ui-style') || null;
}

function findCssRule(styleText, selector) {
  return String(styleText || '').split('}').find((rule) => rule.includes(selector)) || '';
}

function findHorizontalMarkerLine(marker) {
  return descendants(marker).find((node) => node.tagName === 'LINE' &&
    Number(node.x1) !== Number(node.x2) && Number(node.y1) === Number(node.y2)) || null;
}

function findRailRow(overlay, orderNumber) {
  return Array.from(overlay && overlay.children || []).find((node) => (
    node.className === 'replay-trade-order-rail-row' &&
    String(node['data-order-number']) === String(orderNumber)
  )) || null;
}

function findRailLabel(row, className) {
  return descendants(row).find((node) => (
    node.tagName === 'TEXT' &&
    String(node.className || '').split(/\s+/).includes(className)
  )) || null;
}

function findPickerPriceButtons(document) {
  const picker = document.getElementById('replay-trade-price-picker');
  if (!picker) return [];
  return descendants(picker).filter((node) => (
    node.tagName === 'BUTTON' &&
    ['开盘', '最高', '最低', '收盘'].some((label) => String(node.textContent || '').startsWith(label + ' '))
  ));
}

function selectPickerPrice(env, label) {
  const button = findPickerPriceButtons(env.window.document).find((node) => (
    String(node.textContent || '').startsWith(label + ' ')
  ));
  assert.ok(button, `missing ${label} price option`);
  button.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  return button;
}

function findRailPnls(row) {
  return descendants(row).filter((node) => (
    node.tagName === 'TEXT' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-order-rail-pnl')
  ));
}

function railPnlText(row) {
  return findRailPnls(row).map((node) => String(node.textContent || '')).join(' · ');
}

function cancelAllBracketOrders(env) {
  while (env.engine.getState().bracketOrders.length) {
    assert.equal(env.engine.cancelPending(env.engine.getState().bracketOrders[0].side), true);
  }
}

function dragSummaryBox(env, fromX, fromY, toX, toY) {
  const group = findSummaryGroup(env.window.document);
  if (!group) throw new Error('replay-trade-summary group is missing');
  group.dispatchEvent({ type: 'mousedown', button: 0, clientX: fromX, clientY: fromY, preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: toX, clientY: toY });
  env.window.dispatchEvent({ type: 'mouseup', clientX: toX, clientY: toY });
}

function makeIntegrationHarness() {
  const registry = {
    elements: new Map(),
    register(element) {
      if (element.id) this.elements.set(element.id, element);
      element.children.forEach((child) => this.register(child));
    },
  };
  const periodBar = makeElement('div', registry);
  periodBar.className = 'klinecharts-pro-period-bar';
  const body = makeElement('body', registry);
  const head = makeElement('head', registry);
  const documentElement = makeElement('html', registry);
  const status = makeElement('span', registry);
  status.id = 'bar-replay-status';
  body.appendChild(status);
  const document = {
    createElement: (tagName) => makeElement(tagName, registry),
    createElementNS: (_namespace, tagName) => makeElement(tagName, registry),
    getElementById: (id) => registry.elements.get(id) || null,
    querySelector: (selector) => selector === '.klinecharts-pro-period-bar' ? periodBar : null,
    getElementsByTagName: (name) => name === 'head' ? [head] : name === 'body' ? [body] : [],
    body,
    head,
    documentElement,
  };
  const listeners = new Map();
  const intervals = new Map();
  let intervalId = 0;
  const window = {
    document,
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      const values = listeners.get(name) || [];
      listeners.set(name, values.filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    setInterval(handler) {
      const id = ++intervalId;
      intervals.set(id, handler);
      return id;
    },
    clearInterval(id) { intervals.delete(id); },
    setTimeout() { return 0; },
    clearTimeout() {},
    innerWidth: 1200,
    innerHeight: 800,
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    Event: function Event(type) { this.type = type; },
    fetch() { throw new Error('network access is forbidden in replay integration tests'); },
  };
  window.window = window;
  const context = vm.createContext({
    window,
    document,
    console,
    fetch: window.fetch,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
  });

  const bars = [
    { timestamp: 1704067200000, open: 10, high: 12, low: 9, close: 10 },
    { timestamp: 1704153600000, open: 10, high: 11, low: 7, close: 8 },
    { timestamp: 1704240000000, open: 8, high: 13, low: 7, close: 12 },
  ];
  const dom = makeElement('div', registry);
  const chart = {
    all: bars,
    visible: bars.slice(),
    applies: [],
    updates: [],
    convertCalls: [],
    convertToCalls: [],
    getDataList() { return this.visible; },
    applyNewData(data) {
      this.visible = data.slice();
      this.applies.push(data.slice());
    },
    updateData(bar) {
      this.visible.push(bar);
      this.updates.push(bar);
    },
    getDom() { return dom; },
    convertFromPixel(pixel) {
      const point = Array.isArray(pixel) ? pixel[0] : pixel;
      this.convertCalls.push(point);
      if (!point || point.y == null) return [{ dataIndex: 0 }];
      return [{ dataIndex: 999, value: [1704067200000, point.y === 160 ? 10.5 : 11.25] }];
    },
    convertToPixel(pixel) {
      const point = Array.isArray(pixel) ? pixel[0] : pixel;
      this.convertToCalls.push(point);
      const price = point && point.value != null ? Number(point.value) : NaN;
      return {
        x: point && point.dataIndex === 2 ? 900 : 100,
        y: price === 10.5 ? 160 : price === 11.25 ? 220 : price === 9.5 ? 200 : 180,
      };
    },
  };
  window.__kline_chart = chart;

  const files = [
    'bar-replay-events.js',
    'bar-replay.js',
    'replay-trade-engine.js',
    'replay-trade-state-model.js',
    'replay-trade-geometry.js',
    'replay-trade-overlay-renderer.js',
    'replay-trade-ui.js',
  ].map((name) => path.join(JS_ROOT, name));
  files.forEach((file) => assert.ok(fs.existsSync(file), `missing production module: ${file}`));
  files.forEach((file) => vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file }));

  const events = window.BarReplayEvents || context.BarReplayEvents;
  const replay = window.BarReplayController || context.BarReplayController;
  const engine = window.ReplayTradeEngine || context.ReplayTradeEngine;
  const tradeUi = window.ReplayTradeUI || context.ReplayTradeUI;
  assert.ok(events, 'BarReplayEvents must be available');
  assert.ok(replay, 'BarReplayController must be available');
  assert.ok(engine, 'ReplayTradeEngine must be available');
  assert.ok(tradeUi, 'ReplayTradeUI must be available');
  replay.init({ chart, events, eventBus: events, replayEvents: events, tradeEngine: engine });
  engine.reset({ defaultAmount: 1000 });

  return { window, chart, bars, events, replay, engine, tradeUi, intervals };
}

test('replay step drives trade engine at the next bar close, and exit clears it', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.replay.getState().cursor, 0);

  assert.equal(env.engine.openManual({
    barIndex: 0,
    timestamp: env.bars[0].timestamp,
    field: 'close',
    price: env.bars[0].close,
    amount: 1000,
  }), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);

  assert.equal(env.replay.step(), true);
  const marked = env.engine.getState();
  assert.equal(marked.cursor, 1);
  assert.equal(marked.lastBar.timestamp, env.bars[1].timestamp);
  assert.equal(marked.lastBar.close, 8);
  assert.equal(marked.position.entry.barIndex, 0);
  assert.equal(marked.position.currentBarIndex, 1);
  assert.equal(marked.position.currentPrice, 8);
  assert.equal(marked.unrealizedPnl.amount, -200);
  closeEnough(marked.unrealizedPnl.percent, -20);
  assert.deepEqual(nativeArray(env.chart.visible, (bar) => bar.timestamp), [
    env.bars[0].timestamp,
    env.bars[1].timestamp,
  ]);

  assert.equal(env.replay.exit({ silent: true }), true);
  const cleared = env.engine.getState();
  assert.equal(cleared.status, 'flat');
  assert.equal(cleared.holding, false);
  assert.equal(cleared.position, null);
  assert.equal(cleared.cursor, -1);
  assert.equal(cleared.realizedPnl.amount, 0);
  assert.equal(cleared.unrealizedPnl.amount, 0);
  assert.deepEqual(nativeArray(env.chart.visible, (bar) => bar.timestamp), env.bars.map((bar) => bar.timestamp));
});

test('buy marks profit on every replay bar and sell exposes one full settlement', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);

  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', env.bars[0].close, 1000), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);

  assert.equal(env.replay.step(), true);
  let summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'holding');
  assert.equal(summary.label, '总持仓亏损');
  closeEnough(summary.pct, -20);
  closeEnough(summary.amount, -200);

  assert.equal(env.replay.step(), true);
  summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'holding');
  assert.equal(summary.label, '总持仓获利');
  closeEnough(summary.pct, 20);
  closeEnough(summary.amount, 200);

  assert.equal(env.tradeUi.selectPrice('sell', {
    index: 2,
    row: env.bars[2],
  }, 'close', env.bars[2].close), true);
  summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.equal(summary.label, '总交易获利（已结算）');
  closeEnough(summary.pct, 20);
  closeEnough(summary.amount, 200);
  assert.match(summary.details.join(' '), /投入/);
  assert.match(summary.details.join(' '), /卖出/);
  assert.match(summary.details.join(' '), /剩余 0/);
  assert.match(summary.details.join(' '), /投入 ¥1,000\.00/);
  assert.match(summary.details.join(' '), /卖出 ¥1,200\.00/);
  assert.match(summary.details.join(' '), /B1 → S1 · 已卖出/);
  assert.match(summary.details.join(' '), /实际盈亏 · \+20\.00% · \+¥200\.00/);
});

test('preset buy and sell use free horizontal chart prices without OHLC inputs', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.openPresetPanel(), true);

  const buyPriceInput = env.window.document.getElementById('replay-trade-preset-buy-price');
  const sellPriceInput = env.window.document.getElementById('replay-trade-preset-sell-price');
  assert.equal(buyPriceInput, null);
  assert.equal(sellPriceInput, null);

  const amountInput = env.window.document.getElementById('replay-trade-preset-buy-amount');
  assert.equal(amountInput.value, '10000');
  amountInput.value = '1500';
  env.window.document.getElementById('replay-trade-preset-buy-select').dispatchEvent({ type: 'click' });
  let uiState = env.tradeUi.getState();
  assert.equal(uiState.presetSelection.active, true);
  assert.equal(uiState.presetSelection.side, 'buy');
  assert.equal(uiState.presetSelection.amount, 1500);

  const previewEvent = {
    type: 'mousemove',
    offsetX: 120,
    offsetY: 220,
    clientX: 120,
    clientY: 160,
  };
  env.chart.getDom().dispatchEvent(previewEvent);
  uiState = env.tradeUi.getState();
  assert.equal(uiState.presetSelection.previewPrice, 10.5);
  assert.match(env.window.document.getElementById('bar-replay-status').textContent, /移动鼠标选择水平价格，点击确认买入/);
  const overlay = env.window.document.getElementById('replay-trade-overlay');
  assert.ok(overlay.children.some((child) => String(child.className).includes('replay-trade-preset-preview-buy')));
  assert.ok(treeSome(overlay, (child) => String(child.className).includes('replay-trade-preset-label-buy')));
  assert.ok(env.chart.convertCalls.length > 0);

  env.window.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
  uiState = env.tradeUi.getState();
  assert.equal(uiState.presetSelection.active, false);
  assert.equal(uiState.presetSelection.previewPrice, null);
  assert.equal(overlay.children.length, 0);

  assert.equal(env.tradeUi.openPresetPanel(), true);
  env.window.document.getElementById('replay-trade-preset-buy-amount').value = '1500';
  env.window.document.getElementById('replay-trade-preset-buy-select').dispatchEvent({ type: 'click' });
  env.chart.getDom().dispatchEvent({ type: 'mousemove', offsetX: 900, offsetY: 160 });
  const submittedPreviewPrice = env.tradeUi.getState().presetSelection.previewPrice;
  env.chart.getDom().dispatchEvent({ type: 'click', offsetX: 900, offsetY: 160, preventDefault() {} });
  let engineState = env.engine.getState();
  assert.equal(engineState.pendingOrders.buy.price, submittedPreviewPrice);
  assert.equal(engineState.pendingOrders.buy.amount, 1500);
  assert.equal(engineState.pendingOrders.buy.targetPrice, 10.5);
  assert.ok(env.chart.convertToCalls.length > 0);
  assert.ok(env.chart.convertToCalls.some((point) => point && Number(point.value) === 10.5));
  uiState = env.tradeUi.getState();
  assert.equal(uiState.presetSelection.active, false);
  assert.ok(overlay.children.some((child) => String(child.className).includes('replay-trade-preset-buy')));
  assert.ok(treeSome(overlay, (child) => String(child.className).includes('replay-trade-preset-label-buy')));
  assert.match(env.window.document.getElementById('bar-replay-status').textContent, /已设置预设买入/);

  assert.equal(env.replay.step(), true);
  engineState = env.engine.getState();
  assert.equal(engineState.position.entryPrice, 10.5);
  assert.equal(engineState.position.amount, 1500);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);

  assert.equal(env.tradeUi.openPresetPanel(), true);
  env.window.document.getElementById('replay-trade-preset-sell-select').dispatchEvent({ type: 'click' });
  env.chart.getDom().dispatchEvent({ type: 'mousemove', offsetX: 50, offsetY: 220 });
  uiState = env.tradeUi.getState();
  assert.equal(uiState.presetSelection.previewPrice, 11.25);
  env.chart.getDom().dispatchEvent({ type: 'click', offsetX: 50, offsetY: 220, preventDefault() {} });
  engineState = env.engine.getState();
  assert.equal(engineState.pendingOrders.sell.price, 11.25);
  assert.equal(engineState.pendingOrders.sell.amount, null);

  assert.equal(env.replay.step(), true);
  engineState = env.engine.getState();
  assert.equal(engineState.status, 'flat');
  assert.equal(engineState.lastSettlement.exitPrice, 11.25);
  assert.equal(engineState.lastSettlement.remainingQuantity, 0);
  closeEnough(engineState.lastSettlement.profitPercent, (11.25 / 10.5 - 1) * 100);
});

test('trade summary is anchored to the bottom-right of the main price pane', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  const summary = env.window.document.getElementById('replay-trade-summary');
  const group = summary.children.find((child) => child.className === 'replay-trade-summary-group');
  assert.ok(group);
  assert.equal(group['pointer-events'], 'all');
  const box = group.children.find((child) => child.className === 'replay-trade-summary-box');
  assert.ok(box);
  assert.equal(Number(box.width), 360);
  assert.equal(Number(box.x), 628);
  assert.equal(Number(box.y), 420 - Number(box.height) - 12);
  assert.equal(summary.parentNode, env.chart.getDom());
  assert.equal(summary['pointer-events'], undefined);
  const summaryTexts = group.children.filter((child) => child.tagName === 'TEXT');
  assert.ok(summaryTexts.length >= 5);
  const orderHeading = group.children.find((child) => child['data-order-number'] === '1');
  const totalHeading = group.children.find((child) => child.tagName === 'TEXT' && child.textContent === '总计');
  assert.ok(orderHeading);
  assert.ok(totalHeading);
  assert.ok(Number(totalHeading.x) > Number(orderHeading.x));
  assert.ok(group.children.some((child) => child.className === 'replay-trade-summary-separator'));
});

test('trade summary keeps the bottom-right default across redraws until dragged', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);

  let position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, null);
  assert.equal(position.y, null);
  let box = findSummaryBox(env.window.document);
  assert.ok(box);
  const defaultX = Number(box.x);
  const defaultY = Number(box.y);

  env.tradeUi.redraw();
  box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), defaultX);
  assert.equal(Number(box.y), defaultY);

  assert.equal(env.replay.step(), true);
  box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), defaultX);
  assert.equal(Number(box.y), defaultY);
  position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, null);
  assert.equal(position.y, null);
});

test('trade summary box drags freely with the left mouse button and commits its position', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  const group = findSummaryGroup(env.window.document);
  assert.ok(group);
  const startBox = findSummaryBox(env.window.document);
  const startX = Number(startBox.x);
  const startY = Number(startBox.y);

  group.dispatchEvent({ type: 'mousedown', button: 0, clientX: 700, clientY: 330, preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: 550, clientY: 230 });
  let box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), startX - 150);
  assert.equal(Number(box.y), startY - 100);
  env.window.dispatchEvent({ type: 'mouseup', clientX: 550, clientY: 230 });

  const position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, startX - 150);
  assert.equal(position.y, startY - 100);
  box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), startX - 150);
  assert.equal(Number(box.y), startY - 100);
});

test('trade summary box ignores non-left mouse buttons', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  const group = findSummaryGroup(env.window.document);
  const startX = Number(findSummaryBox(env.window.document).x);
  group.dispatchEvent({ type: 'mousedown', button: 2, clientX: 700, clientY: 330, preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: 550, clientY: 230 });
  env.window.dispatchEvent({ type: 'mouseup', clientX: 550, clientY: 230 });

  assert.equal(Number(findSummaryBox(env.window.document).x), startX);
  const position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, null);
  assert.equal(position.y, null);
});

test('trade summary box clamps to the main chart bounds', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  dragSummaryBox(env, 700, 330, 500, 250);
  const boxWidth = Number(findSummaryBox(env.window.document).width);
  const boxHeight = Number(findSummaryBox(env.window.document).height);
  const maxX = 1000 - boxWidth - 12;
  const maxY = 420 - boxHeight - 12;

  dragSummaryBox(env, 700, 330, 3000, 2000);
  let box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), maxX);
  assert.equal(Number(box.y), maxY);

  const group = findSummaryGroup(env.window.document);
  group.dispatchEvent({ type: 'mousedown', button: 0, clientX: 900, clientY: 300, preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: -500, clientY: -500 });
  env.window.dispatchEvent({ type: 'mouseup', clientX: -500, clientY: -500 });
  box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), 12);
  assert.equal(Number(box.y), 12);
  const position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, 12);
  assert.equal(position.y, 12);
});

test('trade summary user position survives redraw and replay advancement', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  dragSummaryBox(env, 700, 330, 550, 230);
  const moved = env.tradeUi.getState().summaryPosition;
  assert.notEqual(moved.x, null);

  env.tradeUi.redraw();
  let box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), moved.x);
  assert.equal(Number(box.y), moved.y);

  assert.equal(env.replay.step(), true);
  box = findSummaryBox(env.window.document);
  assert.equal(Number(box.x), moved.x);
  assert.equal(Number(box.y), moved.y);
  const position = env.tradeUi.getState().summaryPosition;
  assert.equal(position.x, moved.x);
  assert.equal(position.y, moved.y);
});

test('dragging the trade summary box does not trigger chart trade selection', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.tradeUi.enterSelection('buy'), true);

  dragSummaryBox(env, 700, 330, 550, 230);

  const clicksBefore = env.chart.convertCalls.length;
  env.chart.getDom().dispatchEvent({ type: 'click', dataIndex: 0, clientX: 500, clientY: 160, preventDefault() {}, stopPropagation() {} });
  assert.equal(env.window.document.getElementById('replay-trade-price-picker'), null);
  assert.equal(env.chart.convertCalls.length, clicksBefore);

  env.chart.getDom().dispatchEvent({ type: 'click', dataIndex: 0, clientX: 500, clientY: 160, preventDefault() {}, stopPropagation() {} });
  assert.ok(env.window.document.getElementById('replay-trade-price-picker'));
});

test('each manual buy immediately creates bound +/-5% take-profit and stop-loss orders without a confirm DOM', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);

  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  let engineState = env.engine.getState();
  assert.equal(engineState.bracketOrders.length, 2);
  const takeProfit = engineState.bracketOrders.find((order) => order.side === 'takeProfit');
  const stopLoss = engineState.bracketOrders.find((order) => order.side === 'stopLoss');
  assert.ok(takeProfit);
  assert.ok(stopLoss);
  closeEnough(takeProfit.price, 10.5);
  closeEnough(stopLoss.price, 9.5);
  assert.deepEqual(nativeArray(takeProfit.orderNumbers), [1]);
  assert.deepEqual(nativeArray(stopLoss.orderNumbers), [1]);
  assert.deepEqual(nativeArray(takeProfit.executionIds), [engineState.executions[0].id]);
  assert.equal(engineState.pendingOrders.takeProfit.price, 10.5);
  assert.equal(engineState.pendingOrders.stopLoss.price, 9.5);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  assert.ok(treeSome(overlay, (node) => node.textContent === 'B1'));
  assert.ok(treeSome(overlay, (node) => String(node.textContent).includes('B1止盈 10.50 +5.00% · +¥50.00')));
  assert.ok(treeSome(overlay, (node) => String(node.textContent).includes('B1止损 9.50 -5.00% · -¥50.00')));
  assert.equal(env.window.document.getElementById('replay-trade-bracket-confirm'), null);
  assert.equal(env.tradeUi.getState().bracketDrafts.length, 0);

  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'open', 8, 800), true);
  engineState = env.engine.getState();
  assert.equal(engineState.bracketOrders.length, 4);
  assert.ok(engineState.bracketOrders.some((order) => order.side === 'takeProfit' && order.orderNumbers.includes(2)));
  assert.ok(engineState.bracketOrders.some((order) => order.side === 'stopLoss' && order.orderNumbers.includes(2)));
  assert.ok(treeSome(overlay, (node) => node.textContent === 'B1'));
  assert.ok(treeSome(overlay, (node) => node.textContent === 'B2'));
  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.label, '总持仓获利');
  assert.match(summary.details.join(' '), /B1 买入 · .*投入 ¥1,000\.00/);
  assert.match(summary.details.join(' '), /B2 买入 · .*投入 ¥800\.00/);
  assert.match(summary.details.join(' '), /持仓 2 笔 · 加权价格 9\.00/);
  assert.match(summary.details.join(' '), /预期止盈 · 价格 10\.50/);
  assert.match(summary.details.join(' '), /预期止损 · 价格 9\.50/);
  assert.match(summary.details.join(' '), /预期止盈 · 价格 8\.40/);
  assert.match(summary.details.join(' '), /预期止损 · 价格 7\.60/);
});

test('auto stop-loss on the next bar generates S1, hides live lines, and restores ghosts from the summary', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  assert.ok(overlay.children.some((node) => node['data-preset-role'] === 'takeProfit'));
  assert.ok(overlay.children.some((node) => node['data-preset-role'] === 'stopLoss'));
  assert.equal(env.window.document.getElementById('replay-trade-bracket-confirm'), null);

  assert.equal(env.replay.step(), true);
  const state = env.engine.getState();
  assert.equal(state.status, 'flat');
  assert.equal(state.lastExecution.trigger, 'pending-stopLoss');
  assert.equal(state.lastExecution.label, 'S1');
  assert.equal(state.lastSettlement.exitPrice, 9.5);
  assert.equal(state.lastSettlement.trigger, 'pending-stopLoss');
  assert.equal(state.pendingOrders.takeProfit, null);
  assert.equal(state.pendingOrders.stopLoss, null);
  assert.equal(state.completedTrades[0].plannedBrackets.length, 2);
  assert.equal(overlay.children.some((node) => node['data-preset-order-id']), false);

  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.equal(summary.label, '总交易亏损（已结算）');
  assert.match(summary.details.join(' '), /B1 → S1 · 已止损/);
  assert.match(summary.details.join(' '), /S1 实际止损/);
  assert.match(summary.details.join(' '), /实际盈亏 · -5\.00% · -¥50\.00/);
  assert.ok(treeSome(overlay, (node) => node.textContent === 'S1'));

  const group = findSummaryGroup(env.window.document);
  const toggleRow = group.children.find((child) => child['data-order-number'] === '1');
  assert.ok(toggleRow);
  assert.equal(toggleRow.textContent, 'B1 → S1');
  assert.match(toggleRow.className, /replay-trade-negative/);
  const actualStopStatus = group.children.find((child) => (
    String(child.textContent).includes('实际止损') &&
    String(child.className).includes('replay-trade-summary-line')
  ));
  assert.ok(actualStopStatus);
  assert.match(actualStopStatus.className, /replay-trade-negative/);
  assert.ok(group.children.some((child) => String(child.textContent).includes('实际止损 -5.00%')));
  toggleRow.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  assert.ok(treeSome(overlay, (node) => String(node.className).includes('replay-trade-history-ghost')));
  assert.ok(treeSome(overlay, (node) => String(node.textContent).includes('B1 历史止盈 10.50')));
  assert.ok(treeSome(overlay, (node) => String(node.textContent).includes('B1 历史止损 9.50')));

  toggleRow.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  assert.equal(treeSome(overlay, (node) => String(node.className).includes('replay-trade-history-ghost')), false);
});

test('top-left B/S rail is chronological and clicking S1 opens an order-bound OHLC picker', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'open', 8, 800), true);
  while (env.engine.getState().bracketOrders.some((order) => order.side === 'takeProfit')) {
    assert.equal(env.engine.cancelPending('takeProfit'), true);
  }
  while (env.engine.getState().bracketOrders.some((order) => order.side === 'stopLoss')) {
    assert.equal(env.engine.cancelPending('stopLoss'), true);
  }
  assert.equal(env.replay.step(), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const b1 = treeFind(overlay, (node) => node.textContent === 'B1' && node.className === 'replay-trade-order-rail-buy');
  const b2 = treeFind(overlay, (node) => node.textContent === 'B2' && node.className === 'replay-trade-order-rail-buy');
  const s1 = treeFind(overlay, (node) => node['data-sell-order-number'] === '1');
  const s2 = treeFind(overlay, (node) => node['data-sell-order-number'] === '2');
  assert.ok(b1);
  assert.ok(b2);
  assert.ok(s1);
  assert.ok(s2);
  assert.ok(Number(b1.y) < Number(b2.y));
  assert.equal(s1.textContent, 'S1');
  assert.equal(s2.textContent, 'S2');
  assert.equal(Number(s1.y), Number(b1.y));
  assert.equal(Number(s2.y), Number(b2.y));

  const closeManualCalls = [];
  const closeManual = env.engine.closeManual;
  env.engine.closeManual = (payload) => {
    closeManualCalls.push(payload);
    return closeManual(payload);
  };
  s1.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  assert.equal(closeManualCalls.length, 0, 'clicking S1 must wait for an explicit price choice');
  const picker = env.window.document.getElementById('replay-trade-price-picker');
  assert.ok(picker);
  assert.equal(picker.hidden, false);
  assert.deepEqual(
    findPickerPriceButtons(env.window.document).map((button) => (
      ['开盘', '最高', '最低', '收盘'].find((label) => String(button.textContent).startsWith(label + ' '))
    )),
    ['开盘', '最高', '最低', '收盘']
  );
  selectPickerPrice(env, '最高');
  assert.equal(closeManualCalls.length, 1);
  assert.deepEqual(nativeArray(closeManualCalls[0].orderNumbers), [1]);
  assert.equal(closeManualCalls[0].field, 'high');
  assert.equal(closeManualCalls[0].price, env.bars[1].high);
  assert.equal(closeManualCalls[0].barIndex, env.replay.getState().cursor);
  assert.equal(closeManualCalls[0].timestamp, env.bars[env.replay.getState().cursor].timestamp);

  const state = env.engine.getState();
  assert.equal(state.holding, true);
  assert.deepEqual(nativeArray(state.position.lots, (lot) => lot.orderNumber), [2]);
  assert.equal(state.lastExecution.label, 'S1');
  assert.deepEqual(nativeArray(state.lastExecution.orderNumbers), [1]);

  const group = findSummaryGroup(env.window.document);
  const orderHeadings = group.children.filter((node) => node['data-order-number']);
  assert.deepEqual(orderHeadings.map((node) => node.textContent), ['B1 → S1', 'B2 持仓']);
  assert.ok(Number(orderHeadings[0].x) < Number(orderHeadings[1].x));
  const totalHeading = group.children.find((node) => node.textContent === '总计');
  assert.ok(Number(orderHeadings[1].x) < Number(totalHeading.x));
  const summarySeparators = group.children.filter((child) => (
    child.className === 'replay-trade-summary-separator'
  ));
  assert.equal(summarySeparators.length, 2, 'two order columns need one divider plus the total divider');
  summarySeparators.forEach((separator) => {
    assert.equal(Number(separator.x1), Number(separator.x2));
    assert.ok(Number(separator.y1) < Number(separator.y2));
  });
});

test('actual B1 sell removes B1-bound TP/SL from active preset rendering while B2 stays active', () => {
  const env = makeIntegrationHarness();
  // Keep both lots open through the next bar so the rail click is an actual manual S1.
  Object.assign(env.bars[1], { open: 10, high: 10.2, low: 9.8, close: 10 });
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 1000), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 800), true);

  let state = env.engine.getState();
  assert.deepEqual(nativeArray(state.position.lots, (lot) => lot.orderNumber), [1, 2]);
  const b1BracketIds = state.bracketOrders
    .filter((order) => order.orderNumbers.includes(1))
    .map((order) => order.id);
  assert.equal(b1BracketIds.length, 2);
  assert.equal(state.bracketOrders.length, 4);

  assert.equal(env.replay.step(), true);
  state = env.engine.getState();
  assert.deepEqual(nativeArray(state.position.lots, (lot) => lot.orderNumber), [1, 2]);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const row1 = findRailRow(overlay, 1);
  const sell1 = findRailLabel(row1, 'replay-trade-order-rail-sell');
  assert.ok(sell1);
  sell1.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  selectPickerPrice(env, '收盘');

  state = env.engine.getState();
  assert.deepEqual(nativeArray(state.position.lots, (lot) => lot.orderNumber), [2]);
  assert.deepEqual(nativeArray(state.lastExecution.orderNumbers), [1]);
  assert.deepEqual(
    nativeArray(state.bracketOrders, (order) => nativeArray(order.orderNumbers)),
    [[2], [2]],
    'only B2 bracket bindings may remain active after actual S1'
  );

  const activePresetGroups = descendants(overlay).filter((node) => (
    String(node.className || '').split(/\s+/).includes('replay-trade-preset-order') &&
    node['data-preset-order-id']
  ));
  const activePresetIds = activePresetGroups.map((node) => node['data-preset-order-id']).sort();
  const expectedPresetIds = nativeArray(state.bracketOrders, (order) => order.id).sort();
  assert.deepEqual(activePresetIds, expectedPresetIds);
  b1BracketIds.forEach((id) => assert.equal(activePresetIds.includes(id), false));
  assert.equal(activePresetGroups.some((node) => node['data-preset-role'] === 'takeProfit'), true);
  assert.equal(activePresetGroups.some((node) => node['data-preset-role'] === 'stopLoss'), true);
});

test('summary columns follow buy timestamps even when order numbers are no longer chronological', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  while (env.engine.getState().bracketOrders.length) {
    assert.equal(env.engine.cancelPending(env.engine.getState().bracketOrders[0].side), true);
  }

  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 1, row: env.bars[1] }, 'close', 8, 800), true);
  while (env.engine.getState().bracketOrders.length) {
    assert.equal(env.engine.cancelPending(env.engine.getState().bracketOrders[0].side), true);
  }

  const firstBuyId = env.engine.getState().executions[0].id;
  assert.equal(env.engine.updateExecution(firstBuyId, {
    barIndex: 2,
    timestamp: env.bars[2].timestamp,
    field: 'close',
    price: 10,
    amount: 1000,
  }), true);
  env.tradeUi.redraw();

  const group = findSummaryGroup(env.window.document);
  const orderHeadings = group.children.filter((node) => node['data-order-number']);
  assert.deepEqual(orderHeadings.map((node) => node.textContent), ['B2 持仓', 'B1 持仓']);
  assert.ok(Number(orderHeadings[0].x) < Number(orderHeadings[1].x));
  const totalHeading = group.children.find((node) => node.textContent === '总计');
  assert.ok(totalHeading);
  assert.ok(Number(orderHeadings[1].x) < Number(totalHeading.x));

  const separators = group.children.filter((child) => child.className === 'replay-trade-summary-separator');
  assert.equal(separators.length, 2);
  separators.forEach((separator) => {
    assert.equal(Number(separator.x1), Number(separator.x2));
    assert.ok(Number(separator.y1) < Number(separator.y2));
  });
});

test('B1 and S1 keep horizontal order lines with the same group color', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', env.bars[0].close, 1000), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);
  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('sell', {
    index: 1,
    row: env.bars[1],
  }, 'close', env.bars[1].close), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const buyMarker = treeFind(overlay, (node) => (
    node['data-trade-side'] === 'buy' && String(node['aria-label']).includes('B1')
  ));
  const sellMarker = treeFind(overlay, (node) => (
    node['data-trade-side'] === 'sell' && String(node['aria-label']).includes('S1')
  ));
  assert.ok(buyMarker);
  assert.ok(sellMarker);
  const buyLine = findHorizontalMarkerLine(buyMarker);
  const sellLine = findHorizontalMarkerLine(sellMarker);
  assert.ok(buyLine, 'B1 must retain a horizontal order line');
  assert.ok(sellLine, 'S1 must retain a horizontal order line');
  assert.equal(Number(buyLine.x1), 0);
  assert.equal(Number(buyLine.x2), env.chart.getDom().clientWidth);
  assert.equal(Number(sellLine.x1), 0);
  assert.equal(Number(sellLine.x2), env.chart.getDom().clientWidth);
  assert.equal(buyLine.stroke, sellLine.stroke, 'B1 and S1 in one order group must share a color');
  assert.ok(buyLine.stroke);
});

test('left rail keeps compact rounded B/S controls, arrows, and the TP/SL visual contract', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', env.bars[0].close, 1000), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const row = findRailRow(overlay, 1);
  assert.ok(row);
  const buyLabel = descendants(row).find((node) => node.textContent === 'B1');
  const sellLabel = descendants(row).find((node) => node.textContent === 'S1');
  assert.ok(buyLabel);
  assert.ok(sellLabel);

  const shapes = descendants(row).filter((node) => node.tagName === 'POLYGON' || node.tagName === 'PATH');
  assert.equal(shapes.length, 2, 'the row must expose one buy pointer and one sell pointer');
  shapes.forEach((shape) => {
    assert.ok(String(shape.points || shape.d || '').trim(), 'buy/sell pointer geometry must be rendered');
  });
  const pointerPaths = descendants(row).filter((node) => (
    node.tagName === 'PATH' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-order-rail-pointer')
  ));
  assert.equal(pointerPaths.length, 2, 'the B/S rail must expose one pointer for each side');

  const visibleRailText = descendants(row)
    .filter((node) => node.tagName === 'TEXT')
    .map((node) => String(node.textContent || ''))
    .join(' ');
  assert.doesNotMatch(visibleRailText, /买入|卖出/, 'the compact B/S rail must not duplicate Chinese buy/sell action text');

  const stripBackground = descendants(row).find((node) => (
    String(node.className || '').split(/\s+/).includes('replay-trade-order-strip-bg')
  ));
  assert.ok(stripBackground);
  assert.equal(stripBackground.tagName, 'RECT');
  assert.ok(Number(stripBackground.rx) >= 4, 'the compact B/S rail needs a rounded base');

  const buyInfoBackground = descendants(row).find((node) => (
    String(node.className || '').split(/\s+/).includes('replay-trade-order-rail-buy-bg')
  ));
  assert.ok(buyInfoBackground);
  assert.equal(buyInfoBackground.tagName, 'RECT', 'buy information must use a rounded background, not a pointed fill');
  assert.ok(Number(buyInfoBackground.rx) >= 4, 'buy information background must have rounded corners');

  const style = findInjectedTradeStyle(env.window.document);
  assert.ok(style);
  const buyRule = findCssRule(style.textContent, '.replay-trade-order-rail-buy');
  const sellRule = findCssRule(style.textContent, '.replay-trade-order-rail-sell');
  assert.match(buyRule, /fill\s*:\s*(?:#fff|#ffffff|white)/i);
  assert.match(sellRule, /fill\s*:\s*(?:#fff|#ffffff|white)/i);
  assert.match(buyRule, /stroke\s*:\s*(?:#ef4444|red|var\([^)]*#ef4444)/i);
  assert.match(sellRule, /stroke\s*:\s*(?:#2563eb|blue|var\([^)]*#2563eb)/i);

  const takeProfitRule = findCssRule(style.textContent, '.replay-trade-preset-takeProfit');
  const stopLossRule = findCssRule(style.textContent, '.replay-trade-preset-stopLoss');
  assert.match(takeProfitRule, /stroke\s*:\s*#ef4444/i, 'take-profit area must be red');
  assert.match(stopLossRule, /stroke\s*:\s*#10b981/i, 'stop-loss area must be green');
  const takeProfitZone = treeFind(overlay, (node) => (
    String(node.className || '').split(/\s+/).includes('replay-trade-risk-zone-takeProfit')
  ));
  const stopLossZone = treeFind(overlay, (node) => (
    String(node.className || '').split(/\s+/).includes('replay-trade-risk-zone-stopLoss')
  ));
  assert.ok(takeProfitZone, 'an open order must render a red take-profit zone');
  assert.ok(stopLossZone, 'an open order must render a green stop-loss zone');
  const bracketLabelRule = findCssRule(style.textContent, '.replay-trade-preset-label');
  assert.match(bracketLabelRule, /font:\s*400\s+10px/i, 'TP/SL labels must stay fine text');
});

test('each top-left order rail shows live or settled P&L between red Bn and blue Sn', () => {
  const env = makeIntegrationHarness();
  // Keep the final close between the two valid entry prices: B1 gains and B2 loses.
  env.bars[2].close = 10;
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);

  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'low', env.bars[0].low, 900), true);
  cancelAllBracketOrders(env);

  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 1,
    row: env.bars[1],
  }, 'high', env.bars[1].high, 2200), true);
  cancelAllBracketOrders(env);

  assert.equal(env.replay.step(), true);

  let overlay = env.window.document.getElementById('replay-trade-overlay');
  let row1 = findRailRow(overlay, 1);
  let row2 = findRailRow(overlay, 2);
  assert.ok(row1);
  assert.ok(row2);

  let buy1 = findRailLabel(row1, 'replay-trade-order-rail-buy');
  let pnl1 = findRailPnls(row1);
  let sell1 = findRailLabel(row1, 'replay-trade-order-rail-sell');
  let buy2 = findRailLabel(row2, 'replay-trade-order-rail-buy');
  let pnl2 = findRailPnls(row2);
  let sell2 = findRailLabel(row2, 'replay-trade-order-rail-sell');
  [buy1, sell1, buy2, sell2].forEach((node) => assert.ok(node));
  assert.equal(pnl1.length, 2);
  assert.equal(pnl2.length, 2);

  assert.equal(buy1.textContent, 'B1');
  assert.equal(sell1.textContent, 'S1');
  assert.equal(buy2.textContent, 'B2');
  assert.equal(sell2.textContent, 'S2');
  assert.ok(Number(buy1.x) < Number(pnl1[0].x), 'B1 must be left of its P&L');
  pnl1.forEach((node) => assert.ok(Number(node.x) < Number(sell1.x), 'B1 P&L must be left of S1'));
  assert.ok(Number(buy2.x) < Number(pnl2[0].x), 'B2 must be left of its P&L');
  pnl2.forEach((node) => assert.ok(Number(node.x) < Number(sell2.x), 'B2 P&L must be left of S2'));
  assert.equal(Number(pnl1[0].x), Number(pnl1[1].x));
  assert.equal(Number(pnl2[0].x), Number(pnl2[1].x));
  assert.ok(Number(pnl1[0].y) < Number(pnl1[1].y), 'percentage must precede amount in the center');
  assert.ok(Number(pnl2[0].y) < Number(pnl2[1].y), 'percentage must precede amount in the center');
  assert.ok(Number(buy1.y) < Number(buy2.y), 'multiple order rows must stack vertically');
  assert.ok(Number(buy1.x) < 100 && Number(buy1.y) < 50, 'rail must remain at the top-left');

  assert.equal(railPnlText(row1), '+11.11% · +¥100.00');
  assert.equal(railPnlText(row2), '-9.09% · -¥200.00');
  assert.match(String(row1['aria-label']), /当前盈亏 \+11\.11% \+¥100\.00/);
  assert.match(String(row2['aria-label']), /当前盈亏 -9\.09% -¥200\.00/);
  pnl1.forEach((node) => assert.match(String(node.className), /replay-trade-order-rail-pnl-positive/));
  pnl2.forEach((node) => assert.match(String(node.className), /replay-trade-order-rail-pnl-negative/));

  const style = findInjectedTradeStyle(env.window.document);
  assert.ok(style);
  const buyBackgroundRule = findCssRule(style.textContent, '.replay-trade-order-rail-buy-bg');
  const sellBackgroundRule = findCssRule(style.textContent, '.replay-trade-order-rail-sell-bg');
  assert.match(buyBackgroundRule, /fill\s*:\s*(?:#ef4444|red|var\([^)]*#ef4444)/i);
  assert.match(buyBackgroundRule, /stroke\s*:\s*(?:#b91c1c|#ef4444|red|var\([^)]*#(?:b91c1c|ef4444))/i);
  assert.match(sellBackgroundRule, /fill\s*:\s*(?:#2563eb|blue|var\([^)]*#2563eb)/i);
  assert.match(sellBackgroundRule, /stroke\s*:\s*(?:#1d4ed8|#2563eb|blue|var\([^)]*#(?:1d4ed8|2563eb))/i);
  const pnlRules = String(style.textContent || '').split('}')
    .filter((rule) => rule.includes('replay-trade-order-rail-pnl')).join('}');
  assert.match(pnlRules, /#ef4444|var\([^)]*#ef4444/i, 'profit P&L must be red');
  assert.match(pnlRules, /#10b981|var\([^)]*#10b981/i, 'loss P&L must be green');

  // S1 must settle only B1, leaving B2 live and still showing its loss.
  sell1.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  selectPickerPrice(env, '收盘');
  let state = env.engine.getState();
  assert.equal(state.holding, true);
  assert.deepEqual(nativeArray(state.position.lots, (lot) => lot.orderNumber), [2]);
  assert.equal(state.lastExecution.label, 'S1');
  assert.deepEqual(nativeArray(state.lastExecution.orderNumbers), [1]);

  overlay = env.window.document.getElementById('replay-trade-overlay');
  row1 = findRailRow(overlay, 1);
  row2 = findRailRow(overlay, 2);
  pnl1 = findRailPnls(row1);
  pnl2 = findRailPnls(row2);
  sell1 = findRailLabel(row1, 'replay-trade-order-rail-sell');
  sell2 = findRailLabel(row2, 'replay-trade-order-rail-sell');
  assert.ok(row1);
  assert.ok(row2);
  assert.equal(pnl1.length, 2);
  assert.equal(pnl2.length, 2);
  assert.equal(railPnlText(row1), '+11.11% · +¥100.00');
  assert.equal(railPnlText(row2), '-9.09% · -¥200.00');
  assert.match(String(row1['aria-label']), /已结算盈亏 \+11\.11% \+¥100\.00/);
  assert.match(String(row2['aria-label']), /当前盈亏 -9\.09% -¥200\.00/);
  assert.equal(row1['data-order-closed'], 'true');
  assert.equal(row2['data-order-closed'], 'false');
  assert.equal(sell1['data-order-state'], 'closed');
  assert.equal(sell2['data-order-state'], 'open');

  // Choosing a price for S2 completes only the second order and exposes the aggregate settled state.
  sell2.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  selectPickerPrice(env, '收盘');
  state = env.engine.getState();
  assert.deepEqual(nativeArray(state.lastExecution.orderNumbers), [2]);
  assert.deepEqual(nativeArray(state.lastSettlement.closedOrderNumbers), [2]);
  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.equal(summary.label, '总交易亏损（已结算）');
  assert.match(summary.details.join(' '), /剩余 0/);

  overlay = env.window.document.getElementById('replay-trade-overlay');
  row1 = findRailRow(overlay, 1);
  row2 = findRailRow(overlay, 2);
  pnl1 = findRailPnls(row1);
  pnl2 = findRailPnls(row2);
  sell1 = findRailLabel(row1, 'replay-trade-order-rail-sell');
  sell2 = findRailLabel(row2, 'replay-trade-order-rail-sell');
  assert.equal(row1['data-order-closed'], 'true');
  assert.equal(row2['data-order-closed'], 'true');
  assert.equal(railPnlText(row1), '+11.11% · +¥100.00');
  assert.equal(railPnlText(row2), '-9.09% · -¥200.00');
  assert.match(String(row1['aria-label']), /已结算盈亏 \+11\.11% \+¥100\.00/);
  assert.match(String(row2['aria-label']), /已结算盈亏 -9\.09% -¥200\.00/);
  assert.equal(sell1['data-order-state'], 'closed');
  assert.equal(sell2['data-order-state'], 'closed');
});

test('every top-left red Bn rail area displays its buy price and keeps rows vertically ordered', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 1000), true);
  cancelAllBracketOrders(env);

  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 1,
    row: env.bars[1],
  }, 'high', 11, 800), true);
  cancelAllBracketOrders(env);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const row1 = findRailRow(overlay, 1);
  const row2 = findRailRow(overlay, 2);
  assert.ok(row1);
  assert.ok(row2);

  const style = findInjectedTradeStyle(env.window.document);
  assert.ok(style);
  const buyBackgroundRule = findCssRule(style.textContent, '.replay-trade-order-rail-buy-bg');
  assert.match(buyBackgroundRule, /fill\s*:\s*(?:#ef4444|red|var\([^)]*#ef4444)/i);

  [[row1, 'B1', '10.00', 10], [row2, 'B2', '11.00', 11]].forEach(([row, labelText, priceText, price]) => {
    const buyLabel = findRailLabel(row, 'replay-trade-order-rail-buy');
    const buyPrice = descendants(row).find((node) => (
      node.tagName === 'TEXT' &&
      String(node.className || '').split(/\s+/).includes('replay-trade-order-rail-price')
    ));
    const buyBackground = descendants(row).find((node) => (
      String(node.className || '').split(/\s+/).includes('replay-trade-order-rail-buy-bg')
    ));
    assert.ok(buyLabel);
    assert.ok(buyPrice);
    assert.ok(buyBackground);
    assert.equal(buyLabel.textContent, labelText);
    assert.equal(buyPrice.textContent, priceText);
    assert.equal(Number(row['data-buy-price']), price);
    assert.equal(buyPrice.parentNode, row);
    assert.equal(Number(buyPrice.x), Number(buyLabel.x));
    assert.ok(Number(buyPrice.y) > Number(buyLabel.y));
  });

  const buy1 = findRailLabel(row1, 'replay-trade-order-rail-buy');
  const buy2 = findRailLabel(row2, 'replay-trade-order-rail-buy');
  assert.ok(Number(buy1.y) < Number(buy2.y), 'multiple order rails must remain vertically stacked');
});

test('an open B1 keeps its marker, dashed full-width level, and persistent entry risk label', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 1000), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const marker = treeFind(overlay, (node) => (
    node.tagName === 'G' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-buy-marker') &&
    node['data-trade-side'] === 'buy'
  ));
  assert.ok(marker);
  const markerLabel = descendants(marker).find((node) => (
    node.tagName === 'TEXT' && node.className === 'replay-trade-marker-label'
  ));
  assert.ok(markerLabel);
  assert.equal(markerLabel.textContent, 'B1');

  const level = descendants(marker).find((node) => (
    node.tagName === 'LINE' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-execution-level')
  ));
  assert.ok(level);
  assert.equal(level['data-trade-label'], 'B1');
  assert.equal(Number(level.x1), 0);
  assert.equal(Number(level.x2), env.chart.getDom().clientWidth);
  assert.equal(Number(level.y1), Number(level.y2));

  const entryLabel = descendants(marker).find((node) => (
    node.tagName === 'DIV' && String(node.className || '').split(/\s+/).includes('replay-trade-execution-label')
  ));
  assert.ok(entryLabel);
  assert.match(entryLabel.textContent, /^B1买入\s+10\.00\s+·\s+盈亏比\s+\d+(?:\.\d+)?:1$/);
  assert.match(String(entryLabel.style), /font-weight:\s*400/i);

  const style = findInjectedTradeStyle(env.window.document);
  assert.ok(style);
  const levelRule = findCssRule(style.textContent, '.replay-trade-execution-level');
  assert.match(levelRule, /stroke-dasharray\s*:\s*\d+(?:\.\d+)?\s+\d+(?:\.\d+)?/i);

  env.tradeUi.redraw();
  const persistentLabel = treeFind(env.window.document.getElementById('replay-trade-overlay'), (node) => (
    node.tagName === 'DIV' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-execution-label') &&
    String(node.textContent).startsWith('B1买入')
  ));
  assert.ok(persistentLabel, 'the B1 entry label must survive a redraw');
});

test('take-profit and stop-loss labels are B1-first and use normal font weight', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 1000), true);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const takeProfitLabel = descendants(overlay).find((node) => (
    node.tagName === 'TEXT' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-preset-label-takeProfit') &&
    !String(node.className || '').split(/\s+/).includes('replay-trade-preset-delete')
  ));
  const stopLossLabel = descendants(overlay).find((node) => (
    node.tagName === 'TEXT' &&
    String(node.className || '').split(/\s+/).includes('replay-trade-preset-label-stopLoss') &&
    !String(node.className || '').split(/\s+/).includes('replay-trade-preset-delete')
  ));
  assert.ok(takeProfitLabel);
  assert.ok(stopLossLabel);
  assert.equal(takeProfitLabel.parentNode['data-preset-role'], 'takeProfit');
  assert.equal(stopLossLabel.parentNode['data-preset-role'], 'stopLoss');
  assert.equal(takeProfitLabel.textContent, 'B1止盈 10.50 +5.00% · +¥50.00');
  assert.equal(stopLossLabel.textContent, 'B1止损 9.50 -5.00% · -¥50.00');
  assert.doesNotMatch(takeProfitLabel.textContent, /^止盈.*B1/);
  assert.doesNotMatch(stopLossLabel.textContent, /^止损.*B1/);

  const style = findInjectedTradeStyle(env.window.document);
  assert.ok(style);
  const labelRule = findCssRule(style.textContent, '.replay-trade-preset-label');
  assert.match(labelRule, /font-weight\s*:\s*400\s*!important/i);
  assert.doesNotMatch(labelRule, /font-weight\s*:\s*(?:bold|[5-9]\d{2})/i);
});

test('a single open B1 order still settles through S1 as one complete trade', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', {
    index: 0,
    row: env.bars[0],
  }, 'close', 10, 1000), true);
  cancelAllBracketOrders(env);

  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('sell', {
    index: 1,
    row: env.bars[1],
  }, 'close', 8), true);

  const state = env.engine.getState();
  assert.equal(state.status, 'flat');
  assert.equal(state.holding, false);
  assert.equal(state.lastExecution.label, 'S1');
  assert.deepEqual(nativeArray(state.lastExecution.orderNumbers), [1]);
  assert.equal(state.lastSettlement.exitPrice, 8);

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const row = findRailRow(overlay, 1);
  assert.ok(row);
  assert.equal(row['data-order-closed'], 'true');
  const sell = findRailLabel(row, 'replay-trade-order-rail-sell');
  assert.equal(sell['data-order-state'], 'closed');
  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.match(summary.details.join(' '), /B1 → S1 · 已卖出/);
});

test('auto take-profit on the next bar generates S1 at the planned exit price', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);

  assert.equal(env.replay.step(), true);
  const state = env.engine.getState();
  assert.equal(state.status, 'flat');
  assert.equal(state.lastExecution.trigger, 'pending-takeProfit');
  assert.equal(state.lastExecution.label, 'S1');
  assert.equal(state.lastSettlement.exitPrice, 10.5);
  closeEnough(state.lastSettlement.profitAmount, 50);
  assert.equal(state.completedTrades[0].plannedBrackets.length, 1);

  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.equal(summary.label, '总交易获利（已结算）');
  assert.match(summary.details.join(' '), /B1 → S1 · 已止盈/);
  assert.match(summary.details.join(' '), /实际盈亏 · \+5\.00% · \+¥50\.00/);
  const overlay = env.window.document.getElementById('replay-trade-overlay');
  assert.ok(treeSome(overlay, (node) => node.textContent === 'S1'));
  const group = findSummaryGroup(env.window.document);
  const orderHeading = group.children.find((child) => child['data-order-number'] === '1');
  assert.ok(orderHeading);
  assert.match(orderHeading.className, /replay-trade-positive/);
  const actualTakeStatus = group.children.find((child) => (
    String(child.textContent).includes('实际止盈') &&
    String(child.className).includes('replay-trade-summary-line')
  ));
  assert.ok(actualTakeStatus);
  assert.match(actualTakeStatus.className, /replay-trade-positive/);
});

test('pending order line drags vertically and commits the converted chart price', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.engine.setPendingOrder({ side: 'buy', price: 10.5, amount: 1000 }), true);
  env.tradeUi.redraw();

  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const orderLine = overlay.children.find((child) => child['data-preset-role'] === 'buy');
  assert.ok(orderLine);
  orderLine.dispatchEvent({ type: 'mousedown', preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: 600, clientY: 220 });
  env.window.dispatchEvent({ type: 'mouseup', clientX: 600, clientY: 220 });

  const engineState = env.engine.getState();
  assert.equal(engineState.pendingOrders.buy.price, 11.25);
  assert.match(env.window.document.getElementById('bar-replay-status').textContent, /已设置预设买入/);
  const redrawnLine = overlay.children.find((child) => child['data-preset-role'] === 'buy');
  assert.match(redrawnLine['aria-label'], /11\.25/);
});

test('preset panel exposes independent take-profit and stop-loss chart selection', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.openPresetPanel(), true);
  const takeProfit = env.window.document.getElementById('replay-trade-preset-takeProfit-select');
  const stopLoss = env.window.document.getElementById('replay-trade-preset-stopLoss-select');
  assert.ok(takeProfit);
  assert.ok(stopLoss);
  takeProfit.dispatchEvent({ type: 'click' });
  assert.equal(env.tradeUi.getState().presetSelection.side, 'takeProfit');
  env.window.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
  assert.equal(env.tradeUi.openPresetPanel(), true);
  env.window.document.getElementById('replay-trade-preset-stopLoss-select').dispatchEvent({ type: 'click' });
  assert.equal(env.tradeUi.getState().presetSelection.side, 'stopLoss');
});

test('take-profit and stop-loss selections commit to independent engine orders', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.openPresetPanel(), true);
  env.window.document.getElementById('replay-trade-preset-takeProfit-select').dispatchEvent({ type: 'click' });
  env.chart.getDom().dispatchEvent({ type: 'mousemove', clientX: 500, clientY: 160 });
  env.chart.getDom().dispatchEvent({ type: 'click', clientX: 500, clientY: 160, preventDefault() {} });
  assert.equal(env.engine.getState().pendingOrders.takeProfit.price, 10.5);

  assert.equal(env.tradeUi.openPresetPanel(), true);
  env.window.document.getElementById('replay-trade-preset-stopLoss-select').dispatchEvent({ type: 'click' });
  env.chart.getDom().dispatchEvent({ type: 'mousemove', clientX: 500, clientY: 220 });
  env.chart.getDom().dispatchEvent({ type: 'click', clientX: 500, clientY: 220, preventDefault() {} });
  const engineState = env.engine.getState();
  assert.equal(engineState.pendingOrders.takeProfit.price, 10.5);
  assert.equal(engineState.pendingOrders.stopLoss.price, 11.25);
  env.tradeUi.redraw();
  const overlay = env.window.document.getElementById('replay-trade-overlay');
  assert.ok(overlay.children.some((child) => child['data-preset-role'] === 'takeProfit'));
  assert.ok(overlay.children.some((child) => child['data-preset-role'] === 'stopLoss'));
});

test('two open executions use aggregate lots without creating a duplicate position buy', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  env.engine.getState = () => ({
    executions: [
      { id: 'buy-1', side: 'buy', price: 10, amount: 1000, quantity: 100, index: 0 },
      { id: 'buy-2', side: 'buy', price: 12, amount: 600, quantity: 50, index: 0 },
    ],
    position: {
      lots: [
        { id: 'lot-1', entryPrice: 10, amount: 1000, quantity: 100 },
        { id: 'lot-2', entryPrice: 12, amount: 600, quantity: 50 },
      ],
      currentPrice: 11,
      marketValue: 1650,
    },
  });

  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'holding');
  assert.match(summary.details.join(' '), /持仓 2 笔/);
  assert.match(summary.details.join(' '), /加权价格 10\.67/);
  assert.match(summary.details.join(' '), /剩余 150\.0000/);
  closeEnough(summary.amount, 50);
});

test('manual sell forwards the requested quantity to closeManual', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  let received = null;
  env.engine.closeManual = (payload) => { received = payload; return true; };
  assert.equal(env.tradeUi.selectPrice('sell', { index: 1, row: env.bars[1] }, 'close', 8, null, 25), true);
  assert.equal(received.quantity, 25);
});

test('multiple buys and partial sells settle with full-cycle cumulative totals', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'high', 12, 600), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);
  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('sell', { index: 1, row: env.bars[1] }, 'close', 8, null, 100), true);
  assert.equal(env.replay.step(), true);
  assert.equal(env.tradeUi.selectPrice('sell', { index: 2, row: env.bars[2] }, 'close', 12, null, 50), true);

  const summary = env.tradeUi.getPerformanceSummary();
  assert.equal(summary.kind, 'settled');
  assert.equal(summary.label, '总交易亏损（已结算）');
  assert.match(summary.details.join(' '), /成交 2 笔/);
  assert.match(summary.details.join(' '), /投入 ¥1,600\.00/);
  assert.match(summary.details.join(' '), /卖出 ¥1,400\.00/);
  assert.match(summary.details.join(' '), /累计已实现 -¥200\.00/);
  assert.match(summary.details.join(' '), /剩余 0/);
  assert.match(summary.details.join(' '), /B1 → S1 · 已卖出/);
  assert.match(summary.details.join(' '), /B2 → S2 · 已卖出/);
});

test('buy marker opens editor and calls updateExecution with price and amount', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  let received = null;
  env.engine.getState = () => ({
    executions: [{ id: 'execution-edit-1', side: 'buy', price: 10, amount: 1000, quantity: 100, index: 0 }],
    position: { lots: [{ entryPrice: 10, amount: 1000, quantity: 100 }], currentPrice: 10 },
  });
  env.engine.updateExecution = (id, patch) => { received = { id, patch }; return true; };
  env.tradeUi.redraw();
  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const marker = overlay.children.find((child) => child['data-execution-id'] === 'execution-edit-1');
  assert.ok(marker);
  marker.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  const panel = env.window.document.getElementById('replay-trade-execution-editor');
  assert.ok(panel);
  env.window.document.getElementById('replay-trade-edit-price').value = '11';
  env.window.document.getElementById('replay-trade-edit-amount').value = '1200';
  const actions = panel.children[panel.children.length - 1];
  actions.children[0].dispatchEvent({ type: 'click' });
  assert.equal(received.id, 'execution-edit-1');
  assert.equal(received.patch.price, 11);
  assert.equal(received.patch.amount, 1200);
});

test('buy marker B1 drags in both X and Y to update its price, barIndex, and timestamp', () => {
  const env = makeIntegrationHarness();
  assert.equal(env.replay.startSelection(), true);
  assert.equal(env.replay.selectIndex(0), true);
  assert.equal(env.tradeUi.selectPrice('buy', { index: 0, row: env.bars[0] }, 'close', 10, 1000), true);
  assert.equal(env.engine.cancelPending('takeProfit'), true);
  assert.equal(env.engine.cancelPending('stopLoss'), true);
  assert.equal(env.replay.step(), true);
  const executionId = env.engine.getState().executions[0].id;
  const overlay = env.window.document.getElementById('replay-trade-overlay');
  const marker = overlay.children.find((child) => child['data-execution-id'] === executionId);
  assert.ok(marker);

  marker.dispatchEvent({ type: 'mousedown', clientX: 100, clientY: 180, preventDefault() {}, stopPropagation() {} });
  env.window.dispatchEvent({ type: 'mousemove', clientX: 600, clientY: 220 });
  env.window.dispatchEvent({ type: 'mouseup', clientX: 600, clientY: 220 });

  const state = env.engine.getState();
  assert.equal(state.executions[0].price, 11.25);
  assert.equal(state.executions[0].amount, 1000);
  assert.equal(state.executions[0].barIndex, 1);
  assert.equal(state.executions[0].timestamp, env.bars[1].timestamp);
  assert.equal(state.position.entryPrice, 11.25);
  assert.equal(state.bracketOrders.length, 0);
});
