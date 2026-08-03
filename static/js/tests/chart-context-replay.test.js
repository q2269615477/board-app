const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadChartCore() {
  const calls = [];
  const events = [];
  let replayActive = true;
  const window = {
    __board_ctx: { code: 'sh000001', name: '上证指数', type: 'index', period: 'daily' },
    store: {},
    BarReplayController: {
      isActive: () => replayActive,
      exit(options) {
        calls.push({ type: 'replay-exit', options });
        replayActive = false;
      },
    },
    ChartVerticalPanController: { reset: () => calls.push({ type: 'pan-reset' }) },
    pro: {
      currentPeriod: { timespan: 'day', multiplier: 1 },
      setPeriod(period) {
        calls.push({ type: 'set-period', period });
        this.currentPeriod = period;
        return period;
      },
      getPeriod() { return this.currentPeriod; },
      setSymbol(symbol) {
        calls.push({ type: 'set-symbol', symbol });
        return symbol;
      },
    },
    addEventListener() {},
    dispatchEvent(event) { events.push(event); },
  };
  function CustomEvent(type, init) {
    this.type = type;
    this.detail = init && init.detail;
  }
  const context = vm.createContext({
    window,
    document: { createElement: () => ({}), head: { appendChild() {} }, getElementById: () => null },
    CustomEvent,
    console,
    setTimeout: () => 0,
    clearTimeout() {},
    fetch: async () => ({ json: async () => ({}) }),
  });
  const source = fs.readFileSync(require.resolve('../chart-core.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'chart-core.js' });
  context._bindProContextSync();
  return { context, window, calls, events, setReplayActive(value) { replayActive = value; } };
}

describe('native chart context changes isolate replay state', () => {
  const periods = [
    [{ timespan: 'minute', multiplier: 1 }, '1m'],
    [{ timespan: 'minute', multiplier: 5 }, '5m'],
    [{ timespan: 'minute', multiplier: 15 }, '15m'],
    [{ timespan: 'hour', multiplier: 1 }, '60m'],
    [{ timespan: 'hour', multiplier: 2 }, '120m'],
    [{ timespan: 'hour', multiplier: 4 }, '240m'],
    [{ timespan: 'day', multiplier: 1 }, 'daily'],
    [{ timespan: 'week', multiplier: 1 }, 'weekly'],
    [{ timespan: 'month', multiplier: 1 }, 'monthly'],
    [{ timespan: 'month', multiplier: 3 }, 'quarterly'],
    [{ timespan: 'year', multiplier: 1 }, 'yearly'],
  ];

  test('period switch exits replay before loading the new period and publishes all period mappings', () => {
    periods.forEach(([period, expected], index) => {
      const env = loadChartCore();
      env.window.pro.setPeriod(period);
      assert.equal(env.calls[0].type, 'replay-exit', expected + ' must exit replay first');
      assert.equal(env.calls[0].options.restore, false);
      assert.equal(env.calls[0].options.silent, true);
      assert.equal(env.calls[0].options.reason, 'period-change');
      assert.ok(env.calls.findIndex((item) => item.type === 'set-period') > 0);
      assert.equal(env.window.__board_ctx.period, expected);
      const event = env.events.find((item) => item.type === 'period-change');
      assert.ok(event, expected + ' must publish period-change');
      assert.equal(event.detail.period, expected);
      assert.equal(event.detail.source, 'pro-setPeriod');
      assert.equal(index >= 0, true);
    });
  });

  test('direct symbol switch cannot restore history from the previous symbol', () => {
    const env = loadChartCore();
    env.window.pro.setSymbol({ ticker: 'sh000300', name: '沪深300', type: 'index' });
    assert.equal(env.calls[0].type, 'replay-exit');
    assert.equal(env.calls[0].options.restore, false);
    assert.equal(env.calls[0].options.reason, 'symbol-change');
    assert.ok(env.calls.findIndex((item) => item.type === 'set-symbol') > 0);
    assert.equal(env.window.__board_ctx.code, 'sh000300');
  });
});
