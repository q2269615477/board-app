const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const indicators = require('../chart-indicator-manager.js');

function makeStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

function makeHost(initial = {}) {
  const registered = [];
  return {
    localStorage: makeStorage(initial),
    setTimeout(callback) { callback(); },
    klinecharts: {
      registerIndicator(definition) { registered.push(definition); },
    },
    registered,
  };
}

describe('chart indicator manager', () => {
  test('defaults MA periods to 5, 20 and 60', () => {
    assert.deepEqual(indicators.normalizeMaPeriods(null), [5, 20, 60]);
    assert.deepEqual(indicators.normalizeMaPeriods('5, 20 60'), [5, 20, 60]);
  });

  test('deduplicates periods and rejects invalid values', () => {
    assert.deepEqual(indicators.normalizeMaPeriods([5, 20, 5, 60]), [5, 20, 60]);
    assert.throws(() => indicators.normalizeMaPeriods([5, 0, 60]), /正整数/);
    assert.throws(() => indicators.normalizeMaPeriods([]), /至少保留/);
    assert.throws(() => indicators.normalizeMaPeriods([1, 2, 3, 4, 5, 6, 7, 8, 9]), /最多/);
  });

  test('amount rows prefer amount and fall back to turnover', () => {
    assert.deepEqual(indicators.buildAmountRows([
      { amount: 100, turnover: 900 },
      { turnover: 200 },
      { amount: 0, turnover: 300 },
      {},
    ]), [
      { amount: 100 },
      { amount: 200 },
      { amount: 0 },
      { amount: 0 },
    ]);
  });

  test('defines a volume-series amount bar indicator', () => {
    const definition = indicators.createAmountIndicatorDefinition();
    assert.equal(definition.name, 'AMOUNT');
    assert.equal(definition.shortName, '成交额');
    assert.equal(definition.series, 'volume');
    assert.equal(definition.shouldFormatBigNumber, true);
    assert.equal(definition.figures[0].type, 'bar');
    assert.deepEqual(definition.calc([{ amount: 123 }]), [{ amount: 123 }]);
  });

  test('loads, persists and applies MA periods through chart API', () => {
    const host = makeHost();
    const overrides = [];
    const chart = {
      overrideIndicator(config, paneId) { overrides.push({ config, paneId }); },
    };
    const manager = indicators.createManager(host);

    manager.onChartReady(chart);
    assert.deepEqual(overrides.at(-1), {
      config: { name: 'MA', calcParams: [5, 20, 60] },
      paneId: 'candle_pane',
    });

    assert.deepEqual(manager.setMaPeriods([5, 13, 34]), [5, 13, 34]);
    assert.equal(
      host.localStorage.getItem(indicators.MA_STORAGE_KEY),
      '[5,13,34]'
    );
    assert.deepEqual(overrides.at(-1).config.calcParams, [5, 13, 34]);
  });

  test('creates and removes one amount pane and persists the toggle', () => {
    const host = makeHost();
    const calls = [];
    let alive = false;
    const chart = {
      overrideIndicator() {},
      createIndicator(name, stack, options) {
        calls.push(['create', name, stack, options]);
        alive = true;
        return 'amount-pane';
      },
      getIndicatorByPaneId(paneId, name) {
        return alive && paneId === 'amount-pane' && name === 'AMOUNT' ? { name } : null;
      },
      removeIndicator(paneId, name) {
        calls.push(['remove', paneId, name]);
        alive = false;
      },
    };
    const manager = indicators.createManager(host);
    manager.onChartReady(chart);

    assert.equal(manager.setAmountEnabled(true), true);
    assert.equal(manager.setAmountEnabled(true), true);
    assert.equal(calls.filter((entry) => entry[0] === 'create').length, 1);
    assert.equal(host.localStorage.getItem(indicators.AMOUNT_STORAGE_KEY), '1');

    assert.equal(manager.setAmountEnabled(false), true);
    assert.deepEqual(calls.at(-1), ['remove', 'amount-pane', 'AMOUNT']);
    assert.equal(host.localStorage.getItem(indicators.AMOUNT_STORAGE_KEY), '0');
  });
});
