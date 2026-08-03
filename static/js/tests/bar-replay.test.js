const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeElement(tagName) {
  const listeners = new Map();
  const element = {
    tagName: tagName.toUpperCase(),
    id: '',
    type: '',
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    clientWidth: 1000,
    style: {},
    dataset: {},
    children: [],
    parentNode: null,
    parentElement: null,
    classList: {
      add() {},
      remove() {},
    },
    appendChild(child) {
      this.children.push(child);
      child.parentNode = this;
      child.parentElement = this;
      if (child.id) elementsById.set(child.id, child);
      return child;
    },
    setAttribute(name, value) {
      this[name] = String(value);
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
      return { left: 0, top: 0 };
    },
  };
  return element;
}

const elementsById = new Map();

function loadController(options = {}) {
  elementsById.clear();
  const periodBar = makeElement('div');
  periodBar.className = 'klinecharts-pro-period-bar';
  const tooltip = options.withTooltip ? makeElement('div') : null;
  const chartContainer = options.withChartContainer ? makeElement('div') : null;
  if (tooltip) {
    tooltip.id = 'kline-tooltip';
    elementsById.set(tooltip.id, tooltip);
  }
  if (chartContainer) {
    chartContainer.id = 'pro-container';
    elementsById.set(chartContainer.id, chartContainer);
  }
  const document = {
    createElement: (tagName) => makeElement(tagName),
    getElementById: (id) => elementsById.get(id) || null,
    querySelector: (selector) => selector === '.klinecharts-pro-period-bar' ? periodBar : null,
  };
  const windowListeners = new Map();
  const intervals = new Map();
  let intervalId = 0;
  const window = {
    document,
    innerWidth: options.viewportWidth || 1440,
    addEventListener(name, handler) {
      if (!windowListeners.has(name)) windowListeners.set(name, []);
      windowListeners.get(name).push(handler);
    },
    dispatchEvent(event) {
      (windowListeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    setInterval(handler) {
      const id = ++intervalId;
      intervals.set(id, handler);
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
    },
    setTimeout: () => 0,
    clearTimeout: () => {},
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    Event: function Event(type) {
      this.type = type;
    },
  };
  if (options.getProPeriod || Object.prototype.hasOwnProperty.call(options, 'proPeriod')) {
    window.pro = {
      getPeriod: () => options.getProPeriod ? options.getProPeriod() : options.proPeriod,
    };
  }
  if (Object.prototype.hasOwnProperty.call(options, 'context')) {
    window.__board_ctx = options.context;
  }
  const context = vm.createContext({
    window,
    document,
    console,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
  });
  const source = fs.readFileSync(require.resolve('../bar-replay.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'bar-replay.js' });
  return {
    controller: window.BarReplayController,
    window,
    periodBar,
    tooltip,
    chartContainer,
    flushIntervals() {
      [...intervals.values()].forEach((handler) => handler());
    },
    hasInterval() {
      return intervals.size > 0;
    },
  };
}

function makeChart(count = 5) {
  const all = Array.from({ length: count }, (_, index) => ({
    timestamp: index + 1,
    open: index,
    high: index + 1,
    low: index,
    close: index + 0.5,
  }));
  const dom = makeElement('div');
  const chart = {
    all,
    visible: all.slice(),
    updates: [],
    applies: [],
    getDataList() {
      return this.visible;
    },
    applyNewData(data) {
      this.visible = data.slice();
      this.applies.push(data.slice());
    },
    updateData(bar) {
      this.visible.push(bar);
      this.updates.push(bar);
    },
    getDom() {
      return dom;
    },
    convertFromPixel() {
      return [{ dataIndex: 1 }];
    },
  };
  return { chart, dom };
}

const PERIOD_CASES = [
  { source: { timespan: 'minute', multiplier: 1 }, expected: '1m', via: 'pro' },
  { source: { timespan: 'minute', multiplier: 5 }, expected: '5m', via: 'pro' },
  { source: { timespan: 'minute', multiplier: 15 }, expected: '15m', via: 'pro' },
  { source: { timespan: 'hour', multiplier: 1 }, expected: '60m', via: 'pro' },
  { source: { timespan: 'hour', multiplier: 2 }, expected: '120m', via: 'pro' },
  { source: { timespan: 'hour', multiplier: 4 }, expected: '240m', via: 'pro' },
  { source: '日', expected: 'daily', via: 'context' },
  { source: '周', expected: 'weekly', via: 'context' },
  { source: '月', expected: 'monthly', via: 'context' },
  { source: '季', expected: 'quarterly', via: 'context' },
  { source: '年', expected: 'yearly', via: 'context' },
];

describe('BarReplayController', () => {
  test('选择起点后隐藏所有未来 K 线', () => {
    const env = loadController();
    const { chart, dom } = makeChart();
    env.controller.init({ chart });

    assert.equal(env.controller.startSelection(), true);
    assert.equal(env.controller.getState().status, 'selecting');
    dom.dispatchEvent({ type: 'click', dataIndex: 2, preventDefault() {} });
    assert.equal(env.controller.getState().status, 'paused');
    assert.equal(chart.visible.map((bar) => bar.timestamp).join(','), '1,2,3');
    assert.equal(env.controller.getState().visibleCount, 3);
    assert.ok(env.window.document.getElementById('bar-replay-btn'));
    assert.ok(env.window.document.getElementById('bar-replay-controls'));
  });

  test('桌面环境下回放控制条优先挂载到价格信息栏', () => {
    const env = loadController({ withTooltip: true, withChartContainer: true, viewportWidth: 1440 });
    const { chart } = makeChart();
    env.controller.init({ chart });
    env.controller.startSelection();

    const controls = env.window.document.getElementById('bar-replay-controls');
    assert.ok(controls);
    assert.equal(controls.parentNode, env.tooltip);
    assert.notEqual(controls.parentNode, env.periodBar);
    assert.notEqual(controls.parentNode, env.chartContainer);
  });

  test('价格信息栏不存在时回放控制条安全回退到可用容器', () => {
    const env = loadController({ withTooltip: false, withChartContainer: true, viewportWidth: 1440 });
    const { chart } = makeChart();
    env.controller.init({ chart });
    env.controller.startSelection();

    const controls = env.window.document.getElementById('bar-replay-controls');
    assert.ok(controls);
    assert.ok([env.periodBar, env.chartContainer].includes(controls.parentNode));
  });

  test('step 恰好推进一根 K 线，并优先调用 updateData', () => {
    const env = loadController();
    const { chart } = makeChart();
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(1);

    assert.equal(env.controller.step(), true);
    assert.equal(chart.updates.length, 1);
    assert.equal(chart.updates[0].timestamp, 3);
    assert.equal(chart.visible.map((bar) => bar.timestamp).join(','), '1,2,3');
    assert.equal(env.controller.getState().cursor, 2);
  });

  test('播放到末尾后自动停止', () => {
    const env = loadController();
    const { chart } = makeChart(4);
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(0);
    assert.equal(env.controller.togglePlay(), true);

    env.flushIntervals();
    env.flushIntervals();
    env.flushIntervals();
    assert.equal(env.controller.getState().status, 'paused');
    assert.equal(env.controller.getState().cursor, 3);
    assert.equal(env.controller.getState().timerActive, false);
    assert.equal(env.hasInterval(), false);
  });

  test('退出恢复完整历史并为待处理刷新发出事件', () => {
    const env = loadController();
    const { chart } = makeChart();
    let refreshes = 0;
    env.window.addEventListener('refresh-current-symbol', () => { refreshes += 1; });
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(1);
    env.controller.markRefreshPending();

    assert.equal(env.controller.exit(), true);
    assert.equal(chart.visible.map((bar) => bar.timestamp).join(','), '1,2,3,4,5');
    assert.equal(env.controller.getState().status, 'idle');
    assert.equal(env.controller.isActive(), false);
    assert.equal(refreshes, 1);
  });

  test('切换标的时静默退出且不恢复旧标的历史', () => {
    const env = loadController();
    const { chart } = makeChart();
    let refreshes = 0;
    env.window.addEventListener('refresh-current-symbol', () => { refreshes += 1; });
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(1);
    env.controller.markRefreshPending();

    assert.equal(env.controller.exit({ restore: false, silent: true, reason: 'context-change' }), true);
    assert.equal(chart.visible.map((bar) => bar.timestamp).join(','), '1,2');
    assert.equal(env.controller.getState().status, 'idle');
    assert.equal(refreshes, 0);
  });

  test('活动状态和 Shift 快捷键可控制播放与单步', () => {
    const env = loadController();
    const { chart } = makeChart();
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(0);
    assert.equal(env.controller.isActive(), true);

    let prevented = 0;
    env.window.dispatchEvent({
      type: 'keydown', key: 'ArrowRight', shiftKey: true,
      preventDefault() { prevented += 1; },
    });
    assert.equal(env.controller.getState().cursor, 1);
    assert.equal(prevented, 1);

    env.window.dispatchEvent({
      type: 'keydown', key: 'ArrowDown', shiftKey: true,
      preventDefault() { prevented += 1; },
    });
    assert.equal(env.controller.getState().status, 'playing');
    env.window.dispatchEvent({
      type: 'keydown', key: 'ArrowDown', shiftKey: true,
      preventDefault() { prevented += 1; },
    });
    assert.equal(env.controller.getState().status, 'paused');
    assert.ok(prevented >= 3);
  });

  test('11 个支持周期在完整回放生命周期中保持启动周期', () => {
    PERIOD_CASES.forEach((fixture) => {
      const options = fixture.via === 'pro'
        ? { proPeriod: fixture.source, context: { period: 'yearly' } }
        : { context: { period: fixture.source } };
      const env = loadController(options);
      const { chart } = makeChart();
      env.controller.init({ chart });
      const records = { START: [], CURSOR: [], STATUS: [], EXIT: [] };
      const events = env.window.BarReplayEvents;
      events.on(events.START, (detail) => records.START.push(detail));
      events.on(events.CURSOR, (detail) => records.CURSOR.push(detail));
      events.on(events.STATUS, (detail) => records.STATUS.push(detail));
      events.on(events.EXIT, (detail) => records.EXIT.push(detail));

      assert.equal(env.controller.normalizePeriod(fixture.source), fixture.expected);
      assert.equal(env.controller.startSelection(), true);
      assert.equal(env.controller.getState().period, fixture.expected);
      assert.equal(env.controller.getState().startPeriod, fixture.expected);
      assert.equal(env.controller.selectIndex(1), true);
      assert.equal(records.START[0].period, fixture.expected);
      assert.equal(records.START[0].startPeriod, fixture.expected);
      assert.equal(records.CURSOR[0].period, fixture.expected);

      assert.equal(env.controller.step(), true);
      assert.equal(records.CURSOR[records.CURSOR.length - 1].period, fixture.expected);
      assert.equal(env.controller.togglePlay(), true);
      assert.equal(records.STATUS[records.STATUS.length - 1].period, fixture.expected);
      assert.equal(env.controller.togglePlay(), false);
      assert.equal(records.STATUS[records.STATUS.length - 1].period, fixture.expected);

      assert.equal(env.controller.exit({ silent: true }), true);
      assert.equal(records.EXIT[0].period, fixture.expected);
      assert.equal(records.EXIT[0].startPeriod, fixture.expected);
      assert.equal(env.controller.getState().period, null);
    });
  });

  test('回放期间外部周期变化不会污染启动快照', () => {
    let currentPeriod = { timespan: 'day', multiplier: 1 };
    let periodReads = 0;
    const env = loadController({
      getProPeriod: () => {
        periodReads += 1;
        return currentPeriod;
      },
      context: { period: 'weekly' },
    });
    const { chart } = makeChart();
    env.controller.init({ chart });
    const records = { START: [], CURSOR: [], STATUS: [], EXIT: [] };
    const events = env.window.BarReplayEvents;
    events.on(events.START, (detail) => records.START.push(detail));
    events.on(events.CURSOR, (detail) => records.CURSOR.push(detail));
    events.on(events.STATUS, (detail) => records.STATUS.push(detail));
    events.on(events.EXIT, (detail) => records.EXIT.push(detail));

    assert.equal(env.controller.startSelection(), true);
    assert.equal(periodReads, 1);
    assert.equal(env.controller.selectIndex(1), true);
    currentPeriod = { timespan: 'month', multiplier: 3 };
    env.window.__board_ctx.period = 'yearly';

    assert.equal(env.controller.step(), true);
    assert.equal(env.controller.togglePlay(), true);
    assert.equal(env.controller.togglePlay(), false);
    assert.equal(env.controller.exit({ silent: true }), true);

    const emitted = [records.START[0], ...records.CURSOR, ...records.STATUS, records.EXIT[0]];
    emitted.forEach((detail) => {
      assert.equal(detail.period, 'daily');
      assert.equal(detail.startPeriod, 'daily');
    });
    assert.equal(periodReads, 1);
  });

  test('周期上下文事件会静默结束旧周期回放且不恢复旧历史', () => {
    const env = loadController({ proPeriod: { timespan: 'day', multiplier: 1 } });
    const { chart } = makeChart();
    env.controller.init({ chart });
    env.controller.startSelection();
    env.controller.selectIndex(1);

    env.window.dispatchEvent({ type: 'period-change', detail: { period: 'monthly' } });

    assert.equal(env.controller.getState().status, 'idle');
    assert.equal(env.controller.getState().period, null);
    assert.equal(chart.visible.map((bar) => bar.timestamp).join(','), '1,2');
  });

  test('小时 UI 标签只作为输入，输出统一为 API canonical 周期', () => {
    const env = loadController();
    assert.equal(env.controller.normalizePeriod('1H'), '60m');
    assert.equal(env.controller.normalizePeriod('2H'), '120m');
    assert.equal(env.controller.normalizePeriod('4H'), '240m');
    assert.equal(env.controller.normalizePeriod('60m'), '60m');
    assert.equal(env.controller.normalizePeriod('120m'), '120m');
    assert.equal(env.controller.normalizePeriod('240m'), '240m');
  });
});
