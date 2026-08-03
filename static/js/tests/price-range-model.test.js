const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

const MODEL_FILE = path.resolve(__dirname, '..', 'price-range-model.js');
const model = require(MODEL_FILE);

function assertStructuredError(result, field) {
  assert.equal(result.ok, false);
  assert.equal(typeof result.error.code, 'string');
  assert.equal(typeof result.error.message, 'string');
  assert.equal(result.error.field, field);
}

describe('PriceRangeModel UMD/CommonJS contract', () => {
  test('exports calculatePriceRange through CommonJS and browser globals', () => {
    assert.equal(typeof model.calculatePriceRange, 'function');

    const source = require('node:fs').readFileSync(MODEL_FILE, 'utf8');
    const context = { window: {}, globalThis: {}, Number, isFinite };
    vm.runInNewContext(source, context, { filename: MODEL_FILE });
    assert.equal(typeof context.window.PriceRangeModel.calculatePriceRange, 'function');
  });
});

describe('calculatePriceRange', () => {
  test('calculates an upward range from the start price', () => {
    assert.deepEqual(model.calculatePriceRange({ startPrice: 100, endPrice: 125 }), {
      ok: true,
      startPrice: 100,
      endPrice: 125,
      difference: 25,
      absoluteDifference: 25,
      percent: 25,
      direction: 'up',
    });
  });

  test('calculates a downward range with signed difference and percentage', () => {
    assert.deepEqual(model.calculatePriceRange({ startPrice: 200, endPrice: 150 }), {
      ok: true,
      startPrice: 200,
      endPrice: 150,
      difference: -50,
      absoluteDifference: 50,
      percent: -25,
      direction: 'down',
    });
  });

  test('returns a flat range for equal prices', () => {
    assert.deepEqual(model.calculatePriceRange({ startPrice: 88.8, endPrice: 88.8 }), {
      ok: true,
      startPrice: 88.8,
      endPrice: 88.8,
      difference: 0,
      absoluteDifference: 0,
      percent: 0,
      direction: 'flat',
    });
  });

  test('calculates absolute tick count when tickSize is provided', () => {
    const up = model.calculatePriceRange({ startPrice: 10, endPrice: 11.25, tickSize: 0.25 });
    const down = model.calculatePriceRange({ startPrice: 11.25, endPrice: 10, tickSize: 0.25 });
    assert.equal(up.tickCount, 5);
    assert.equal(down.tickCount, 5);
  });

  test('applies pricePrecision to prices, differences and tick count', () => {
    const result = model.calculatePriceRange({
      startPrice: 1.005,
      endPrice: 1.015,
      tickSize: 0.005,
      pricePrecision: 2,
    });
    assert.equal(result.ok, true);
    assert.equal(result.startPrice, 1.01);
    assert.equal(result.endPrice, 1.02);
    assert.equal(result.difference, 0.01);
    assert.equal(result.absoluteDifference, 0.01);
    assert.equal(result.tickCount, 2);
    assert.equal(result.direction, 'up');
    assert.equal(Number.isFinite(result.percent), true);

    const falling = model.calculatePriceRange({
      startPrice: 1.015,
      endPrice: 1.005,
      pricePrecision: 2,
    });
    assert.equal(falling.difference, -0.01);
    assert.equal(falling.absoluteDifference, 0.01);
    assert.equal(falling.direction, 'down');
  });

  test('accepts numeric strings but rejects non-finite and non-positive prices', () => {
    const result = model.calculatePriceRange({ startPrice: '10', endPrice: '12.5' });
    assert.equal(result.ok, true);
    assert.equal(result.percent, 25);

    for (const value of [0, -1, Infinity, -Infinity, NaN, null, '', true, {}]) {
      const invalidStart = model.calculatePriceRange({ startPrice: value, endPrice: 10 });
      assert.equal(invalidStart.ok, false, `startPrice=${String(value)} should fail`);
      assert.equal(typeof invalidStart.error.code, 'string');
    }
  });

  test('validates optional tickSize and pricePrecision', () => {
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, tickSize: 0 }), 'tickSize');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, tickSize: -0.1 }), 'tickSize');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, tickSize: Infinity }), 'tickSize');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, pricePrecision: -1 }), 'pricePrecision');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, pricePrecision: 1.5 }), 'pricePrecision');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, pricePrecision: 21 }), 'pricePrecision');
    assertStructuredError(model.calculatePriceRange({ startPrice: 10, endPrice: 11, pricePrecision: NaN }), 'pricePrecision');
  });

  test('rejects invalid top-level input and never returns non-finite numbers', () => {
    assertStructuredError(model.calculatePriceRange(null), 'input');
    assertStructuredError(model.calculatePriceRange([]), 'input');
    assertStructuredError(model.calculatePriceRange({ startPrice: 1 }), 'endPrice');

    const result = model.calculatePriceRange({ startPrice: Number.MAX_VALUE, endPrice: Number.MAX_VALUE / 2 });
    assert.equal(result.ok, true);
    for (const value of Object.values(result)) {
      if (typeof value === 'number') assert.equal(Number.isFinite(value), true);
    }
  });
});
