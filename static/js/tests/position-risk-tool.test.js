const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'position-risk-tool.js'),
  'utf8'
);
const realModel = require(path.join(__dirname, '..', 'position-risk-model.js'));

function makeModel() {
  return {
    calculatePosition(input) {
      const long = input.direction === 'long';
      const profit = long ? input.target - input.entry : input.entry - input.target;
      const risk = long ? input.entry - input.stop : input.stop - input.entry;
      if (!(input.entry > 0) || !(profit > 0) || !(risk > 0)) {
        return { ok: false, error: { message: 'invalid levels' } };
      }
      const riskAmount = input.riskMode === 'amount'
        ? input.risk
        : input.accountSize * input.risk / 100;
      const qty = Math.min(
        riskAmount / risk / input.pointValue / input.lotSize,
        input.accountSize * input.leverage / input.entry * input.pointValue / input.lotSize
      );
      return {
        ok: true,
        entry: input.entry,
        target: input.target,
        stop: input.stop,
        targetPct: profit / input.entry * 100,
        stopPct: risk / input.entry * 100,
        riskReward: profit / risk,
        qty,
        profitPnl: profit * qty * input.pointValue * input.lotSize,
        lossPnl: -risk * qty * input.pointValue * input.lotSize,
      };
    },
    reversePosition(input) {
      return {
        ...input,
        direction: input.direction === 'long' ? 'short' : 'long',
        target: input.stop,
        stop: input.target,
      };
    },
  };
}

function loadTool(options) {
  options = options || {};
  const templates = [];
  const elements = [];
  const listeners = {};
  const document = options.document || {
    readyState: 'loading',
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    querySelector() { return null; },
  };
  const klinecharts = {
    registerOverlay(template) { templates.push(template); },
  };
  if (options.utils) klinecharts.utils = options.utils;
  const context = {
    console,
    document,
    setTimeout() { return 1; },
    clearTimeout() {},
    innerHeight: 800,
    addEventListener() {},
    PositionRiskModel: options.model || makeModel(),
    klinecharts,
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(SOURCE, context, { filename: 'position-risk-tool.js' });
  return { context, tool: context.PositionRiskTool, templates, listeners };
}

function makeElement(tagName, registry) {
  const element = {
    tagName: String(tagName).toUpperCase(),
    children: [],
    parentNode: null,
    className: '',
    id: '',
    hidden: false,
    textContent: '',
    innerHTML: '',
    style: {},
    attributes: {},
    offsetWidth: 0,
    offsetHeight: 0,
    clientWidth: 0,
    clientHeight: 0,
    classList: {
      add() {},
      remove() {},
      contains() { return false; },
    },
    setAttribute(name, value) {
      element.attributes[name] = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(element.attributes, name)
        ? element.attributes[name]
        : null;
    },
    appendChild(child) {
      child.parentNode = element;
      element.children.push(child);
      return child;
    },
    removeChild(child) {
      const index = element.children.indexOf(child);
      if (index >= 0) element.children.splice(index, 1);
      if (child.parentNode === element) child.parentNode = null;
      return child;
    },
    addEventListener(type, handler, capture) {
      (element._listeners[type] = element._listeners[type] || []).push({
        handler,
        capture: !!capture,
      });
    },
    removeEventListener(type, handler, capture) {
      const list = element._listeners[type] || [];
      const index = list.findIndex(
        (item) => item.handler === handler && item.capture === !!capture
      );
      if (index >= 0) list.splice(index, 1);
    },
    dispatchEvent(event) {
      event.target = event.target || element;
      let node = element;
      while (node) {
        const list = node._listeners && node._listeners[event.type];
        if (list) {
          list.slice().forEach((item) => {
            if (!event.propagationStopped) item.handler.call(node, event);
          });
        }
        if (event.propagationStopped) break;
        node = node.parentNode;
      }
      return !event.defaultPrevented;
    },
    getBoundingClientRect() {
      const left = parseFloat(element.style.left) || 0;
      const top = parseFloat(element.style.top) || 0;
      const width = element.offsetWidth || 0;
      const height = element.offsetHeight || 0;
      return { left, top, right: left + width, bottom: top + height, width, height };
    },
    querySelector(selector) {
      return registry ? registry.querySelector(selector) : null;
    },
    querySelectorAll(selector) {
      return registry ? registry.querySelectorAll(selector) : [];
    },
  };
  element._listeners = {};
  Object.defineProperty(element, 'firstChild', {
    get() { return element.children.length ? element.children[0] : null; },
  });
  return element;
}

function makeMouseEvent(element, type, init) {
  return Object.assign({
    type,
    target: element,
    button: 0,
    clientX: 0,
    clientY: 0,
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  }, init || {});
}

function makeModalRegistry() {
  const fields = {};
  const registry = {
    fields,
    querySelector(selector) {
      const nameMatch = /^\[name="([^"]+)"\]$/.exec(selector || '');
      if (nameMatch) {
        const field = fields[nameMatch[1]] || { value: '', addEventListener() {} };
        fields[nameMatch[1]] = field;
        return field;
      }
      if (selector === '[data-position-preview]') return registry.preview;
      if (selector === '[data-position-error]') return registry.error;
      if (selector === '#position-risk-title') return registry.title;
      return registry.stubs[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  registry.stubs = {};
  ['[data-position-save]', '[data-position-delete]', '[data-position-reverse]'].forEach((selector) => {
    registry.stubs[selector] = makeElement('button', registry);
  });
  registry.preview = makeElement('div', registry);
  registry.error = makeElement('div', registry);
  registry.title = makeElement('strong', registry);
  return registry;
}

function loadToolWithDom(options) {
  options = options || {};
  const registry = makeModalRegistry();
  const body = makeElement('body', registry);
  const main = makeElement('div', registry);
  const docListeners = {};
  const document = {
    readyState: 'complete',
    body,
    createElement(tagName) { return makeElement(tagName, registry); },
    addEventListener(type, handler, capture) {
      (docListeners[type] = docListeners[type] || []).push({
        handler,
        capture: !!capture,
      });
    },
    removeEventListener(type, handler, capture) {
      const list = docListeners[type] || [];
      const index = list.findIndex(
        (item) => item.handler === handler && item.capture === !!capture
      );
      if (index >= 0) list.splice(index, 1);
    },
    dispatchEvent(event) {
      (docListeners[event.type] || []).slice().forEach((item) => {
        item.handler.call(document, event);
      });
      return !event.defaultPrevented;
    },
    querySelector() { return null; },
  };
  const loaded = loadTool(Object.assign({}, options, { document }));
  function walkText(element) {
    const parts = [];
    const visit = (node) => {
      if (node.textContent) parts.push(node.textContent);
      (node.children || []).forEach(visit);
    };
    visit(element);
    return parts.join(' ');
  }
  return {
    tool: loaded.tool,
    context: loaded.context,
    document,
    main,
    registry,
    fields: registry.fields,
    docListeners,
    fireDocument(type, init) {
      const event = makeMouseEvent(null, type, init);
      document.dispatchEvent(event);
      return event;
    },
    fireElement(element, type, init) {
      const event = makeMouseEvent(element, type, init);
      element.dispatchEvent(event);
      return event;
    },
    summaryDom() { return main.children[0] || null; },
    summaryText() { return this.summaryDom() ? walkText(this.summaryDom()) : ''; },
    summaryLines() { return this.summaryDom() ? this.summaryDom().children.map(walkText) : []; },
  };
}

function points(entry, target, stop) {
  return [
    { timestamp: 1, value: entry },
    { timestamp: 2, value: target },
    { timestamp: 2, value: stop },
  ];
}

function coordinates(entryY, targetY, stopY) {
  return [
    { x: 20, y: entryY },
    { x: 220, y: targetY },
    { x: 220, y: stopY },
  ];
}

function loadSummaryWithChart() {
  const loaded = loadToolWithDom({ model: realModel });
  const { tool, main } = loaded;
  const overlay = {
    name: tool.names.long,
    points: points(100, 120, 90),
    extendData: tool.defaults('long'),
  };
  const chart = {
    createOverlayCount: 0,
    createOverlay() { this.createOverlayCount += 1; return 'created'; },
    getOverlays() { return [overlay]; },
    getDom() { return main; },
    subscribeAction() {},
    unsubscribeAction() {},
  };
  main.clientWidth = 800;
  main.clientHeight = 500;
  tool.onChartReady(chart);
  tool.renderSummary();
  const summary = loaded.summaryDom();
  summary.offsetWidth = 200;
  summary.offsetHeight = 120;
  return Object.assign(loaded, { overlay, chart, summary });
}

test('registers separate long and short custom overlay templates', () => {
  const { tool, templates } = loadTool();
  assert.equal(tool.registerTemplates(), true);
  assert.deepEqual(templates.map((item) => item.name), [
    'boardLongPosition',
    'boardShortPosition',
    'boardPriceRange',
  ]);
  assert.deepEqual(templates.map((item) => item.totalStep), [4, 4, 3]);
  assert.equal(templates.every((item) => item.needDefaultPointFigure), true);
  assert.deepEqual(Array.from(templates[0].pointRoles), ['entry', 'target', 'stop']);
  assert.deepEqual(Array.from(templates[1].pointRoles), ['entry', 'target', 'stop']);
});

test('price range renders A-share red for gains and green for losses', () => {
  const { tool } = loadTool();
  const up = tool.buildPriceRangeFigures(
    [{ x: 20, y: 120 }, { x: 220, y: 60 }],
    [{ value: 100 }, { value: 112 }],
    { pricePrecision: 2 },
    { width: 500 }
  );
  const down = tool.buildPriceRangeFigures(
    [{ x: 20, y: 60 }, { x: 220, y: 120 }],
    [{ value: 100 }, { value: 92 }],
    { pricePrecision: 2 },
    { width: 500 }
  );
  assert.equal(up.find((item) => item.key === 'price-range-text').styles.backgroundColor, '#ef5350');
  assert.equal(down.find((item) => item.key === 'price-range-text').styles.backgroundColor, '#26a69a');
  assert.match(up.find((item) => item.key === 'price-range-text').attrs.text, /\+12\.00%/);
  assert.match(down.find((item) => item.key === 'price-range-text').attrs.text, /-8\.00%/);
});

test('price range flat and invalid states never render NaN or Infinity', () => {
  const { tool } = loadTool();
  const flat = tool.buildPriceRangeFigures(
    [{ x: 20, y: 90 }, { x: 220, y: 90 }],
    [{ value: 100 }, { value: 100 }],
    {},
    { width: 500 }
  );
  const invalid = tool.buildPriceRangeFigures(
    [{ x: 20, y: 90 }, { x: 220, y: 120 }],
    [{ value: 0 }, { value: 90 }],
    {},
    { width: 500 }
  );
  const allText = [...flat, ...invalid]
    .map((item) => item.attrs && item.attrs.text)
    .filter(Boolean)
    .join(' ');
  assert.match(allText, /0\.00%/);
  assert.match(allText, /价格区间无效/);
  assert.doesNotMatch(allText, /NaN|Infinity/);
});

test('price range shows calendar days and exact daily K-line count', () => {
  const { tool } = loadTool();
  const day = 24 * 60 * 60 * 1000;
  const start = new Date(2026, 0, 5).getTime();
  const figures = tool.buildPriceRangeFigures(
    [{ x: 20, y: 120 }, { x: 320, y: 60 }],
    [
      { value: 100, timestamp: start, dataIndex: 10 },
      { value: 112, timestamp: start + 14 * day, dataIndex: 20 },
    ],
    { pricePrecision: 2, period: 'daily' },
    { width: 500 }
  );
  const timing = figures.find((item) => item.key === 'price-range-period-text');
  assert.ok(timing);
  assert.match(timing.attrs.text, /2026-01-05.*2026-01-19/);
  assert.match(timing.attrs.text, /跨度 14天/);
  assert.match(timing.attrs.text, /11根日线/);
});

test('price range uses period-aware duration and K-line labels', () => {
  const { tool } = loadTool();
  const day = 24 * 60 * 60 * 1000;
  const cases = [
    {
      period: { timespan: 'week', multiplier: 1 },
      start: new Date(2025, 0, 6).getTime(),
      end: new Date(2025, 2, 31).getTime(),
      endIndex: 16,
      expected: /跨度 12周.*13根周线/,
    },
    {
      period: '5m',
      start: new Date(2026, 0, 5, 9, 30).getTime(),
      end: new Date(2026, 0, 5, 10, 30).getTime(),
      endIndex: 16,
      expected: /跨度 60分钟.*13根5分钟线/,
    },
    {
      period: { timespan: 'hour', multiplier: 2 },
      start: new Date(2026, 0, 5, 9).getTime(),
      end: new Date(2026, 0, 5, 17).getTime(),
      endIndex: 8,
      expected: /跨度 8小时.*5根2小时线/,
    },
    {
      period: 'monthly',
      start: new Date(2026, 0, 1).getTime(),
      end: new Date(2026, 3, 1).getTime(),
      endIndex: 7,
      expected: /跨度 3个月.*4根月线/,
    },
    {
      period: 'quarterly',
      start: new Date(2025, 0, 1).getTime(),
      end: new Date(2025, 9, 1).getTime(),
      endIndex: 7,
      expected: /跨度 3个季度.*4根季线/,
    },
    {
      period: 'yearly',
      start: new Date(2024, 0, 1).getTime(),
      end: new Date(2026, 0, 1).getTime(),
      endIndex: 6,
      expected: /跨度 2年.*3根年线/,
    },
  ];

  for (const item of cases) {
    const figures = tool.buildPriceRangeFigures(
      [{ x: 20, y: 120 }, { x: 420, y: 60 }],
      [
        { value: 100, timestamp: item.start, dataIndex: 4 },
        { value: 112, timestamp: item.end, dataIndex: item.endIndex },
      ],
      { pricePrecision: 2, period: item.period },
      { width: 500 }
    );
    const timing = figures.find((figure) => figure.key === 'price-range-period-text');
    assert.ok(timing);
    assert.match(timing.attrs.text, item.expected);
  }
});

test('long position renders red profit and green loss zones with expected stats', () => {
  const { tool } = loadTool();
  const figures = tool.buildPositionFigures(
    'long',
    coordinates(100, 50, 145),
    points(100, 120, 90),
    tool.defaults('long'),
    { width: 500 }
  );
  const target = figures.find((item) => item.key === 'target-zone');
  const stop = figures.find((item) => item.key === 'stop-zone');
  const targetText = figures.find((item) => item.key === 'target-text');
  const entryText = figures.find((item) => item.key === 'entry-text');
  const stopText = figures.find((item) => item.key === 'stop-text');
  assert.match(target.styles.color, /239,68,68/);
  assert.match(stop.styles.color, /16,185,129/);
  assert.equal(targetText.styles.backgroundColor, '#dc2626');
  assert.equal(entryText.styles.backgroundColor, '#1d4ed8');
  assert.equal(stopText.styles.backgroundColor, '#059669');
  assert.match(entryText.attrs.text, /多头1/);
  assert.match(entryText.attrs.text, /盈亏比 2\.00:1/);
  assert.match(entryText.attrs.text, /买入 100 份/);
  assert.doesNotMatch(entryText.attrs.text, /数量/);
  assert.match(targetText.attrs.text, /盈利/);
  assert.match(stopText.attrs.text, /亏损/);
  assert.equal(entryText.ignoreEvent, false);
});

test('long position gives explicit entry and target previews before the third point', () => {
  const { tool } = loadTool();
  const settings = tool.defaults('long');
  const entryOnly = tool.buildPositionFigures(
    'long',
    [{ x: 20, y: 100 }],
    [{ value: 100 }],
    settings,
    { width: 500 }
  );
  assert.deepEqual(Array.from(entryOnly, (item) => item.key), ['entry-level', 'entry-text']);
  const entryText = entryOnly.find((item) => item.key === 'entry-text');
  assert.match(entryText.attrs.text, /买入/);
  assert.equal(entryText.ignoreEvent, false);

  const targetPreview = tool.buildPositionFigures(
    'long',
    [{ x: 20, y: 100 }, { x: 220, y: 50 }],
    [{ value: 100 }, { value: 120 }],
    settings,
    { width: 500 }
  );
  assert.deepEqual(Array.from(targetPreview, (item) => item.key), [
    'entry-level', 'entry-text', 'target-preview-zone', 'target-level', 'target-text',
  ]);
  assert.equal(targetPreview.some((item) => item.key === 'stop-zone'), false);
  assert.match(targetPreview.find((item) => item.key === 'target-text').attrs.text, /盈利/);
});

test('short position uses inverted price levels and keeps profit red and loss green', () => {
  const { tool } = loadTool();
  const figures = tool.buildPositionFigures(
    'short',
    coordinates(100, 145, 50),
    points(100, 80, 110),
    tool.defaults('short'),
    { width: 500 }
  );
  assert.match(figures.find((item) => item.key === 'target-zone').styles.color, /239,68,68/);
  assert.match(figures.find((item) => item.key === 'stop-zone').styles.color, /16,185,129/);
  assert.match(figures.find((item) => item.key === 'entry-text').attrs.text, /空头/);
});

test('long and short zones remain visible when target and stop are dragged left of entry', () => {
  const { tool } = loadTool();
  for (const [direction, pointValues] of [
    ['long', points(100, 120, 90)],
    ['short', points(100, 80, 110)],
  ]) {
    const figures = tool.buildPositionFigures(
      direction,
      [{ x: 320, y: 100 }, { x: 80, y: direction === 'long' ? 50 : 145 }, { x: 80, y: direction === 'long' ? 145 : 50 }],
      pointValues,
      tool.defaults(direction),
      { width: 500 }
    );
    const targetZone = figures.find((item) => item.key === 'target-zone');
    const stopZone = figures.find((item) => item.key === 'stop-zone');
    for (const zone of [targetZone, stopZone]) {
      const xs = zone.attrs.coordinates.map((point) => point.x);
      assert.equal(Math.min(...xs), 80);
      assert.equal(Math.max(...xs), 320);
      assert.ok(Math.max(...xs) - Math.min(...xs) > 0);
    }
    assert.match(targetZone.styles.color, /239,68,68/);
    assert.match(stopZone.styles.color, /16,185,129/);
  }
});

test('short position previews the target and only adds stop after the third point', () => {
  const { tool } = loadTool();
  const settings = tool.defaults('short');
  const preview = tool.buildPositionFigures(
    'short',
    [{ x: 20, y: 100 }, { x: 220, y: 145 }],
    [{ value: 100 }, { value: 80 }],
    settings,
    { width: 500 }
  );
  assert.equal(preview.some((item) => item.key === 'target-preview-zone'), true);
  assert.equal(preview.some((item) => item.key === 'stop-zone'), false);
  assert.match(preview.find((item) => item.key === 'target-text').attrs.text, /盈利/);

  const complete = tool.buildPositionFigures(
    'short',
    coordinates(100, 145, 50),
    points(100, 80, 110),
    settings,
    { width: 500 }
  );
  assert.equal(complete.some((item) => item.key === 'stop-zone'), true);
  assert.match(complete.find((item) => item.key === 'stop-text').attrs.text, /亏损/);
});

test('invalid level ordering renders an explicit editing hint instead of NaN', () => {
  const { tool } = loadTool();
  const figures = tool.buildPositionFigures(
    'long',
    coordinates(100, 120, 80),
    points(100, 90, 110),
    tool.defaults('long'),
    { width: 500 }
  );
  const text = figures.map((item) => item.attrs && item.attrs.text).filter(Boolean).join(' ');
  assert.match(text, /价格层级无效/);
  assert.doesNotMatch(text, /NaN|Infinity/);
});

test('dragging target or stop keeps timestamps aligned without exchanging price values', () => {
  const { tool } = loadTool();
  tool.registerTemplates();
  const template = tool.templateFor('long');
  const data = points(100, 120, 90);
  template.performEventPressedMove({
    points: data,
    performPointIndex: 1,
    performPoint: { timestamp: 8, dataIndex: 7, value: 125 },
  });
  assert.equal(data[2].timestamp, 8);
  assert.equal(data[2].dataIndex, 7);
  assert.equal(data[1].value, 120);
  assert.equal(data[2].value, 90);
  template.performEventPressedMove({
    points: data,
    performPointIndex: 2,
    performPoint: { timestamp: 11, dataIndex: 10, value: 75 },
  });
  assert.equal(data[1].timestamp, 11);
  assert.equal(data[1].dataIndex, 10);
  assert.equal(data[1].value, 120);
  assert.equal(data[2].value, 90);
});

test('createPosition delegates to the chart overlay API without any order side effect', () => {
  const { tool } = loadTool();
  const calls = [];
  const chart = {
    createOverlay(config) { calls.push(config); return 'position-1'; },
  };
  tool.onChartReady(chart);
  assert.equal(tool.createPosition('short'), 'position-1');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, 'boardShortPosition');
  assert.equal(calls[0].groupId, 'board-position-risk');
  assert.equal(calls[0].extendData.direction, 'short');
  assert.equal(calls[0].extendData.positionNumber, 1);
});

test('new drawings receive stable direction-specific position numbers', () => {
  const { tool } = loadTool();
  const overlays = [];
  const chart = {
    createOverlay(config) { overlays.push(config); return 'position-' + overlays.length; },
    getOverlays() { return overlays; },
  };
  tool.onChartReady(chart);
  tool.createPosition('long');
  tool.createPosition('long');
  tool.createPosition('short');
  assert.equal(overlays[0].extendData.positionNumber, 1);
  assert.equal(overlays[1].extendData.positionNumber, 2);
  assert.equal(overlays[2].extendData.positionNumber, 1);
});

test('createPriceRange delegates to the two-point overlay template', () => {
  const { tool } = loadTool();
  const calls = [];
  const chart = {
    createOverlay(config) { calls.push(config); return 'range-1'; },
  };
  tool.onChartReady(chart);
  assert.equal(tool.createPriceRange(), 'range-1');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, 'boardPriceRange');
  assert.equal(calls[0].groupId, 'board-position-risk');
});

test('summarizes multiple long and short drawings separately and in total', () => {
  const { tool } = loadTool();
  const overlays = [
    { name: tool.names.long, points: points(100, 120, 90), extendData: tool.defaults('long') },
    { name: tool.names.long, points: points(100, 130, 90), extendData: tool.defaults('long') },
    { name: tool.names.short, points: points(100, 80, 110), extendData: tool.defaults('short') },
  ];
  const summary = tool.summarizePositions(overlays);
  assert.deepEqual(Array.from(summary.items, (item) => item.label), ['多头1', '多头2', '空头1']);
  assert.equal(summary.long.count, 2);
  assert.equal(summary.short.count, 1);
  assert.equal(summary.total.count, 3);
  assert.equal(summary.total.profit, 7000);
  assert.equal(summary.total.loss, -3000);
  assert.equal(summary.total.ratio, 7 / 3);
});

test('summary keeps explicit chart position numbers', () => {
  const { tool } = loadTool();
  const first = Object.assign(tool.defaults('long'), { positionNumber: 2 });
  const second = Object.assign(tool.defaults('long'), { positionNumber: 4 });
  const summary = tool.summarizePositions([
    { name: tool.names.long, points: points(100, 120, 90), extendData: first },
    { name: tool.names.long, points: points(100, 130, 90), extendData: second },
  ]);
  assert.deepEqual(Array.from(summary.items, (item) => item.label), ['多头2', '多头4']);
});

test('new long positions default to a 10000 investment amount while short stays risk-based', () => {
  const { tool } = loadTool();
  assert.equal(tool.defaults('long').investmentAmount, 10000);
  assert.equal(tool.defaults('short').investmentAmount, null);
  const calls = [];
  const chart = {
    createOverlay(config) { calls.push(config); return 'id-' + calls.length; },
  };
  tool.onChartReady(chart);
  tool.createPosition('long');
  tool.createPosition('short');
  assert.equal(calls[0].extendData.investmentAmount, 10000);
  assert.equal(calls[1].extendData.investmentAmount, null);
});

test('summarizePositions weights average cost by result quantity for long and total buckets', () => {
  const { tool } = loadTool({ model: realModel });
  const base = {
    accountSize: 100000,
    riskMode: 'percent',
    risk: 1,
    lotSize: 1,
    leverage: 1,
    pointValue: 1,
    qtyPrecision: 0,
  };
  const overlays = [
    {
      name: tool.names.long,
      points: points(100, 120, 90),
      extendData: Object.assign({ direction: 'long', investmentAmount: 10000 }, base),
    },
    {
      name: tool.names.long,
      points: points(200, 220, 195),
      extendData: Object.assign({ direction: 'long', investmentAmount: 30000 }, base),
    },
    {
      name: tool.names.short,
      points: points(300, 280, 310),
      extendData: Object.assign({ direction: 'short', investmentAmount: null }, base),
    },
  ];
  const summary = tool.summarizePositions(overlays);

  assert.equal(summary.long.count, 2);
  assert.equal(summary.long.quantity, 250);
  assert.equal(summary.long.weightedCostValue, 40000);
  assert.equal(summary.long.weightedAverageCost, 160);
  assert.equal(summary.short.count, 1);
  assert.equal(summary.short.quantity, 100);
  assert.equal(summary.short.weightedAverageCost, 300);
  assert.equal(summary.total.count, 3);
  assert.equal(summary.total.quantity, 350);
  assert.equal(summary.total.weightedCostValue, 70000);
  assert.equal(summary.total.weightedAverageCost, 200);
  assert.equal(summary.total.profit, 7000);
  assert.equal(summary.total.loss, -2750);

  assert.equal(summary.items[0].entry, 100);
  assert.equal(summary.items[0].target, 120);
  assert.equal(summary.items[0].stop, 90);
  assert.equal(summary.items[0].quantity, 100);
  assert.equal(summary.items[0].targetPct, 20);
  assert.equal(summary.items[0].stopPct, 10);
  assert.equal(summary.items[1].entry, 200);
  assert.equal(summary.items[1].target, 220);
  assert.equal(summary.items[1].stop, 195);
  assert.equal(summary.items[1].quantity, 150);
  assert.equal(summary.items[2].entry, 300);
  assert.equal(summary.items[2].target, 280);
  assert.equal(summary.items[2].stop, 310);
  assert.equal(summary.items[2].quantity, 100);
});

test('target and stop labels on the chart include the concrete prices', () => {
  const { tool } = loadTool({ model: realModel });
  const figures = tool.buildPositionFigures(
    'long',
    coordinates(100, 50, 145),
    points(100, 120, 90),
    tool.defaults('long'),
    { width: 500 }
  );
  const targetText = figures.find((item) => item.key === 'target-text').attrs.text;
  const stopText = figures.find((item) => item.key === 'stop-text').attrs.text;
  assert.match(targetText, /盈利 120\.00/);
  assert.match(targetText, /\+20\.00%/);
  assert.match(targetText, /¥2,000\.00/);
  assert.match(stopText, /亏损 90\.00/);
  assert.match(stopText, /-10\.00%/);
  assert.match(stopText, /¥1,000\.00/);
});

test('position summary shows target and stop prices with percentages and amounts per item', () => {
  const loaded = loadToolWithDom({ model: realModel });
  const { tool } = loaded;
  const overlay = {
    name: tool.names.long,
    points: points(100, 120, 90),
    extendData: tool.defaults('long'),
  };
  const chart = {
    createOverlay() { return 'created'; },
    getOverlays() { return [overlay]; },
    getDom() { return loaded.main; },
    subscribeAction() {},
  };
  loaded.tool.onChartReady(chart);
  const result = loaded.tool.renderSummary();
  assert.equal(result.total.count, 1);
  const itemLine = loaded.summaryLines().find((line) => line.includes('多头1'));
  assert.match(itemLine, /买入价 100\.00/);
  assert.match(itemLine, /盈利\s+目标价 120\.00/);
  assert.match(itemLine, /\+20\.00%/);
  assert.match(itemLine, /\+¥2,000\.00/);
  assert.match(itemLine, /亏损\s+止损价 90\.00/);
  assert.match(itemLine, /-10\.00%/);
  assert.match(itemLine, /-¥1,000\.00/);
  assert.match(loaded.summaryText(), /加权平均价格 100\.00/);
  assert.doesNotMatch(loaded.summaryText(), /预计/);
});

test('settings preview shows target and stop prices with percentages and amounts', () => {
  const { tool, registry } = loadToolWithDom({ model: realModel });
  const overlay = {
    id: 'ov-1',
    name: tool.names.long,
    points: points(100, 120, 90),
    extendData: tool.defaults('long'),
  };
  const chart = {
    getOverlayById(id) { return id === overlay.id ? overlay : null; },
    createOverlay() { return overlay.id; },
    removeOverlay() {},
    overrideOverlay() {},
  };
  tool.onChartReady(chart);
  assert.equal(tool.openSettings(overlay.id), true);
  const preview = registry.preview.textContent;
  assert.match(preview, /投入 ¥10,000\.00/);
  assert.match(preview, /买入份数 100/);
  assert.doesNotMatch(preview, /数量/);
  assert.match(preview, /盈利 120\.00/);
  assert.match(preview, /\+20\.00%/);
  assert.match(preview, /¥2,000\.00/);
  assert.match(preview, /亏损 90\.00/);
  assert.match(preview, /-10\.00%/);
  assert.match(preview, /¥1,000\.00/);
});

test('wires entry clicks to settings and exposes three independent drawing tools', () => {
  assert.match(SOURCE, /onClick:\s*function \(event\) \{\s*return openEntrySettings\(event\);/);
  assert.match(SOURCE, /id: 'position-price-range-tool'/);
  assert.match(SOURCE, /id: 'position-long-tool'/);
  assert.match(SOURCE, /id: 'position-short-tool'/);
  assert.match(SOURCE, /position-risk-tool-icon-rail-/);
  assert.match(SOURCE, /position-risk-tool-icon-dot-start/);
  assert.doesNotMatch(SOURCE, /glyph: '[LS↕]'/);
  assert.doesNotMatch(SOURCE, /position-risk-launcher/);
  assert.doesNotMatch(SOURCE, /position-risk-menu|toggleToolMenu/);
});

test('all three custom templates right-click delete through the shared helper', () => {
  const { tool } = loadTool();
  tool.registerTemplates();
  const removed = [];
  const chart = {
    createOverlay() { return 'created'; },
    removeOverlay(config) { removed.push(config); },
  };
  tool.onChartReady(chart);
  const templates = [tool.templateFor('long'), tool.templateFor('short'), tool.priceRangeTemplate()];
  templates.forEach((template) => {
    assert.equal(typeof template.onRightClick, 'function');
    assert.equal(template.onRightClick({ overlay: { id: 'draw-1', name: template.name } }), true);
  });
  assert.deepEqual(removed.map((item) => item.id), ['draw-1', 'draw-1', 'draw-1']);
  assert.match(SOURCE, /onRightClick:\s*function \(event\) \{\s*return deleteOverlayFromEvent\(event\);/);
  assert.match(SOURCE, /onSelected:\s*function \(event\) \{\s*return openEntrySettings\(event\);/);
});

test('summary box drags with the left mouse and does not interfere with chart drawing', () => {
  const env = loadSummaryWithChart();
  const { summary, chart } = env;

  const down = env.fireElement(summary, 'mousedown', { clientX: 120, clientY: 80 });
  assert.equal(down.defaultPrevented, true);
  assert.equal(down.propagationStopped, true);
  assert.equal(env.docListeners.mousemove.length, 1);
  assert.equal(env.docListeners.mouseup.length, 1);

  const move = env.fireDocument('mousemove', { clientX: 320, clientY: 230 });
  assert.equal(move.defaultPrevented, true);
  assert.equal(move.propagationStopped, true);
  assert.equal(summary.style.left, '200px');
  assert.equal(summary.style.top, '150px');
  assert.equal(summary.style.right, 'auto');
  assert.equal(summary.style.bottom, 'auto');

  const up = env.fireDocument('mouseup', { clientX: 320, clientY: 230 });
  assert.equal(up.propagationStopped, true);
  assert.equal(env.docListeners.mousemove.length, 0);
  assert.equal(env.docListeners.mouseup.length, 0);

  env.fireDocument('mousemove', { clientX: 900, clientY: 900 });
  assert.equal(summary.style.left, '200px');
  assert.equal(summary.style.top, '150px');
  assert.equal(chart.createOverlayCount, 0);
});

test('summary drag starts only from the left mouse button', () => {
  const env = loadSummaryWithChart();
  const down = env.fireElement(env.summary, 'mousedown', {
    button: 2,
    clientX: 10,
    clientY: 10,
  });
  assert.equal(down.defaultPrevented, false);
  assert.equal(down.propagationStopped, false);
  assert.equal((env.docListeners.mousemove || []).length, 0);
  assert.equal((env.docListeners.mouseup || []).length, 0);
});

test('summary drag never starts from future buttons or interactive elements inside the box', () => {
  const env = loadSummaryWithChart();
  const button = env.document.createElement('button');
  button.textContent = '未来按钮';
  env.summary.appendChild(button);

  const buttonDown = env.fireElement(button, 'mousedown', { clientX: 10, clientY: 10 });
  assert.equal(buttonDown.defaultPrevented, false);
  assert.equal(buttonDown.propagationStopped, false);
  assert.equal((env.docListeners.mousemove || []).length, 0);

  const action = env.document.createElement('span');
  action.setAttribute('data-action', 'future');
  env.summary.appendChild(action);
  const actionDown = env.fireElement(action, 'mousedown', { clientX: 10, clientY: 10 });
  assert.equal(actionDown.propagationStopped, false);
  assert.equal((env.docListeners.mousemove || []).length, 0);

  const textLine = env.summary.children[0];
  const textDown = env.fireElement(textLine, 'mousedown', { clientX: 10, clientY: 10 });
  assert.equal(textDown.propagationStopped, true);
  assert.equal(env.docListeners.mousemove.length, 1);
  env.fireDocument('mouseup', {});
});

test('summary drag clamps inside the main chart visible bounds', () => {
  const env = loadSummaryWithChart();
  const { summary } = env;

  env.fireElement(summary, 'mousedown', { clientX: 50, clientY: 50 });
  env.fireDocument('mousemove', { clientX: 5000, clientY: 5000 });
  assert.equal(summary.style.left, '600px');
  assert.equal(summary.style.top, '380px');

  env.fireDocument('mousemove', { clientX: -5000, clientY: -5000 });
  assert.equal(summary.style.left, '0px');
  assert.equal(summary.style.top, '0px');
  env.fireDocument('mouseup', {});
});

test('summary drag position survives refresh and pane redraw recreation', () => {
  const env = loadSummaryWithChart();
  const { tool, summary } = env;

  env.fireElement(summary, 'mousedown', { clientX: 100, clientY: 60 });
  env.fireDocument('mousemove', { clientX: 300, clientY: 180 });
  env.fireDocument('mouseup', { clientX: 300, clientY: 180 });
  assert.equal(summary.style.left, '200px');
  assert.equal(summary.style.top, '120px');

  tool.renderSummary();
  const refreshed = env.summaryDom();
  assert.equal(refreshed, summary);
  assert.equal(refreshed.style.left, '200px');
  assert.equal(refreshed.style.top, '120px');

  const newMain = env.document.createElement('div');
  newMain.clientWidth = 800;
  newMain.clientHeight = 500;
  const newChart = {
    createOverlay() { return 'x'; },
    getOverlays() { return [env.overlay]; },
    getDom() { return newMain; },
    subscribeAction() {},
    unsubscribeAction() {},
  };
  tool.onChartReady(newChart);
  tool.renderSummary();
  const recreated = newMain.children[0];
  assert.ok(recreated);
  assert.equal(recreated.id, 'position-risk-summary');
  assert.equal(recreated.style.left, '200px');
  assert.equal(recreated.style.top, '120px');

  recreated.offsetWidth = 200;
  recreated.offsetHeight = 120;
  newMain.clientWidth = 300;
  tool.renderSummary();
  assert.equal(recreated.style.left, '100px');
  assert.equal(recreated.style.top, '120px');
});
