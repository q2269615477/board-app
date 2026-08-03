const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const state = require('../session-state.js');

describe('SessionState projections', () => {
  test('projects panel context without mutating either source', () => {
    const boardContext = {
      symbol: '603259',
      name: '药明康德',
      type: 'stock',
      period: { timespan: 'minute', multiplier: 5 },
    };
    const selected = { code: '000001', name: 'stale', type: 'index', period: 'weekly' };
    const projected = state.projectPanelContext(boardContext, selected);

    assert.deepEqual(projected, {
      symbol: '603259',
      symbol_name: '药明康德',
      asset_type: 'stock',
      period: '5m',
    });
    assert.deepEqual(boardContext.period, { timespan: 'minute', multiplier: 5 });
    assert.equal(selected.period, 'weekly');
  });

  test('normalizes Pro period objects and uses safe defaults', () => {
    assert.equal(state.normalizePeriod({ timespan: 'hour', multiplier: 2 }), '120m');
    assert.equal(state.normalizePeriod({ timespan: 'day', multiplier: 1 }), 'daily');
    assert.equal(state.normalizePeriod({ timespan: 'week', multiplier: 1 }), 'weekly');
    assert.equal(state.normalizePeriod({ timespan: 'month', multiplier: 3 }), 'quarterly');
    assert.equal(state.normalizePeriod({ timespan: 'month', multiplier: 12 }), 'yearly');
    assert.equal(state.normalizePeriod({ timespan: 'year', multiplier: 1 }), 'yearly');
    assert.equal(state.normalizePeriod({ timespan: 'unknown' }), 'daily');
    assert.equal(state.normalizePeriod(null), 'daily');
  });

  test('normalizes overlays and filters temporary session highlights', () => {
    const overlay = {
      name: 'segment',
      points: [
        { timestamp: 1700000000000, value: 10 },
        { dataIndex: 2, y: 11 },
      ],
      styles: { color: '#f00' },
      extendData: { risk: 0.5 },
    };
    const first = state.normalizeOverlayInstance(overlay);
    const second = state.normalizeOverlayInstance(overlay);

    assert.equal(first.type, 'segment');
    assert.equal(first.id, second.id, 'content-based ids must be stable');
    assert.deepEqual(first.points, [
      { timestamp: 1700000000000, value: 10 },
      { timestamp: 2, value: 11 },
    ]);
    assert.deepEqual(first.styles, { color: '#f00' });
    assert.deepEqual(first.extendData, { risk: 0.5 });
    assert.equal(state.normalizeOverlayInstance({ id: 'sess_hl_1', points: [] }), null);
    assert.deepEqual(overlay.points[1], { dataIndex: 2, y: 11 });
  });

  test('snaps a picked price to the nearest OHLC element or keeps a custom price', () => {
    const bar = { open: 10, high: 11, low: 9, close: 10.5 };
    assert.deepEqual(state.snapPriceElement(bar, 10.49), {
      price_element: 'close',
      price: 10.5,
    });
    assert.deepEqual(state.snapPriceElement(bar, 10.8), {
      price_element: 'custom',
      price: 10.8,
    });
    assert.deepEqual(state.snapPriceElement({ open: null, high: undefined }, 10), {
      price_element: 'custom',
      price: 10,
    });
    assert.deepEqual(state.snapPriceElement(bar, 'not-a-price'), {
      price_element: null,
      price: 'not-a-price',
    });
  });

  test('projects a bar into a session K-bar without global session state', () => {
    const bar = {
      timestamp: 1700000000,
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      vol: 1234,
      amount: 9876,
    };
    const kbar = state.projectBarToKbar(
      bar,
      10.49,
      { symbol: '603259', period: 'daily' },
      'chart-1'
    );

    assert.deepEqual(kbar, {
      timestamp: 1700000000,
      date: '2023-11-14',
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: 1234,
      amount: 9876,
      price_element: 'close',
      price: 10.5,
      symbol: '603259',
      period: 'daily',
      chart_id: 'chart-1',
    });
    assert.equal(bar.timestamp, 1700000000);
  });
});
