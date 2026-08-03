const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const MODULE_FILE = path.resolve(__dirname, '..', 'replay-trade-geometry.js');
const geometry = require(MODULE_FILE);

const rows = [
  { timestamp: 1704067200000, high: 12, low: 8 },
  { timestamp: 1704153600000, high: 14, low: 10 },
  { timestamp: 1704240000000, high: 16, low: 12 },
];

describe('ReplayTradeGeometry contract', () => {
  test('exports pure helpers and attaches the browser global without a DOM', () => {
    for (const name of [
      'indexFromValue', 'eventCoordinates', 'extractConvertedPrice',
      'firstConvertedPoint', 'convertedPrice', 'pixelConversionInput',
      'indexConversionInputs', 'convertedIndex', 'proportionalIndexFromX',
      'priceConversionInputs', 'convertedPixelPoint', 'legacyPriceToPixel',
    ]) assert.equal(typeof geometry[name], 'function', `${name} must be exported`);

    const code = fs.readFileSync(MODULE_FILE, 'utf8');
    const sandbox = {};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
    assert.equal(typeof sandbox.ReplayTradeGeometry.legacyPriceToPixel, 'function');
  });

  test('stays independent from chart and pane adapters', () => {
    const source = fs.readFileSync(MODULE_FILE, 'utf8');
    assert.equal(source.includes('convertFromPixel'), false);
    assert.equal(source.includes('convertToPixel'), false);
    assert.equal(source.includes('candle_pane'), false);
  });
});

describe('index and pointer conversion', () => {
  test('resolves direct indexes, second timestamps and millisecond timestamps', () => {
    assert.equal(geometry.indexFromValue(1, rows), 1);
    assert.equal(geometry.indexFromValue(1704240000, rows), 2);
    assert.equal(geometry.indexFromValue(1704067200000, rows), 0);
    assert.equal(geometry.indexFromValue(9, rows), -1);
  });

  test('prefers client coordinates relative to the supplied rect and falls back to offsets', () => {
    assert.deepEqual(geometry.eventCoordinates({ clientX: 110, clientY: 70 }, { left: 10, top: 20 }), {
      x: 100, y: 50, rect: { left: 10, top: 20 },
    });
    assert.deepEqual(geometry.eventCoordinates({ offsetX: 7, offsetY: 8 }), {
      x: 7, y: 8, rect: { left: 0, top: 0 },
    });
  });

  test('keeps chart index conversion separate from the later proportional fallback', () => {
    assert.deepEqual(geometry.indexConversionInputs({ x: 50, y: 10 }), [
      [{ x: 50, y: 10 }], { x: 50, y: 10 },
    ]);
    assert.equal(geometry.convertedIndex([{ dataIndex: 2 }], rows), 2);
    assert.equal(geometry.convertedIndex(null, rows), -1);
    assert.equal(geometry.proportionalIndexFromX(50, 100, rows.length), 1);
    assert.equal(geometry.proportionalIndexFromX(-50, 100, rows.length), 0);
    assert.equal(geometry.proportionalIndexFromX(150, 100, rows.length), 2);
  });
});

describe('price conversion compatibility', () => {
  test('unwraps nested KLineChart return shapes and keeps the last array value behavior', () => {
    assert.equal(geometry.convertedPrice([{ value: [1704067200000, 10.5] }]), 10.5);
    assert.equal(geometry.convertedPrice([123, 456]), 456);
    assert.equal(geometry.convertedPrice({ value: 1, price: 2 }), 1);
    assert.equal(geometry.convertedPrice([{ value: 1 }, { price: 2 }]), 1);
    assert.equal(geometry.convertedPrice({ price: { quote: 12.25 } }), 12.25);
    assert.equal(geometry.convertedPrice({ close: 9.75 }), 9.75);
    assert.equal(geometry.extractConvertedPrice({ price: 2, value: 1 }, 0), 2);
    assert.equal(geometry.extractConvertedPrice([1, 2], 0), 2);
    assert.equal(geometry.convertedPrice({ value: { deeply: { nested: { beyond: { limit: 5 } } } } }), null);
  });

  test('builds array then object convertFromPixel inputs without invoking a chart', () => {
    assert.deepEqual(geometry.pixelConversionInput({ x: null, y: 40 }, 200), {
      x: 100,
      y: 40,
      attempts: [[{ x: 100, y: 40 }], { x: 100, y: 40 }],
    });
    assert.equal(geometry.pixelConversionInput({ x: 1, y: null }, 10), null);
  });

  test('prefers chart pixels and preserves the OHLC fallback projection', () => {
    assert.deepEqual(geometry.convertedPixelPoint([{ x: 33, y: 44 }]), { x: 33, y: 44 });
    assert.deepEqual(geometry.legacyPriceToPixel(1, 12, rows, 300, 200), { x: 150, y: 100 });
    assert.deepEqual(geometry.legacyPriceToPixel(1, 20, rows, 300, 200), { x: 150, y: -100 });
  });

  test('tries all historical convertToPixel shapes before falling back', () => {
    const attempts = geometry.priceConversionInputs(1, 12, rows);
    assert.equal(attempts.length, 4);
    assert.ok(Array.isArray(attempts[0]));
    assert.equal(Array.isArray(attempts[1]), false);
    assert.ok(Array.isArray(attempts[2]));
    assert.equal(Array.isArray(attempts[3]), false);
    assert.deepEqual(geometry.convertedPixelPoint({ x: 9, y: 10 }), { x: 9, y: 10 });
    assert.equal(geometry.convertedPixelPoint({ x: NaN, y: 10 }), null);
  });
});
