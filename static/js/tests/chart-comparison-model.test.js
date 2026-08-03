'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const model = require('../chart-comparison-model.js');

const DAY = 86400000;
const T = Date.UTC(2024, 0, 15, 0, 0, 0); // 2024-01-15T00:00:00Z, Monday
const T2 = Date.UTC(2024, 0, 14, 12, 0, 0); // 2024-01-14T12:00:00Z, Sunday

describe('module surface', () => {
  test('exports constants and every pure function', () => {
    const functions = [
      'finiteNumber', 'stablePercentage', 'toMillis', 'periodToApi', 'periodBucket',
      'validRows', 'alignSeries', 'computeReturns', 'normalizeSeries', 'computeComparison',
      'rangeOhlc', 'exactPeriodRow', 'commonRangeRows', 'computePercentScale',
      'mapPercentToY', 'returnPctToEquivalentPrice', 'computeEquivalentPriceExtent',
      'computePriceScale', 'mapPriceToY', 'countRangeBars', 'countRangeTradingDays',
      'naturalDayCount', 'clampEndpoint', 'symbolIdentity', 'pathFor', 'formatPct',
      'formatPoints', 'formatPointMagnitude', 'rangeMovementStyle', 'formatDate',
      'hashCode', 'pixelPoint', 'pickColor'
    ];
    functions.forEach((name) => assert.strictEqual(typeof model[name], 'function', name));
    assert.strictEqual(model.DAY, DAY);
    assert.deepEqual(model.PALETTE, [
      '#f59e0b', '#2563eb', '#7c3aed', '#0891b2',
      '#db2777', '#0f766e', '#9333ea', '#ea580c',
      '#0369a1', '#c026d3', '#ca8a04', '#4f46e5'
    ]);
  });

  test('does not move controller/DOM functions into the model', () => {
    assert.strictEqual(model.computeRangeComparison, undefined);
    assert.strictEqual(model.computeMainRange, undefined);
    assert.strictEqual(model.computeRangeDetails, undefined);
    assert.strictEqual(model.createSearchUrl, undefined);
  });
});

describe('periodToApi', () => {
  test('maps the 11 period forms', () => {
    assert.strictEqual(model.periodToApi({ timespan: 'minute', multiplier: 30 }), '30m');
    assert.strictEqual(model.periodToApi({ timespan: 'hour', multiplier: 2 }), '120m');
    assert.strictEqual(model.periodToApi({ timespan: 'day' }), 'daily');
    assert.strictEqual(model.periodToApi({ timespan: 'daily' }), 'daily');
    assert.strictEqual(model.periodToApi({ span: 'week' }), 'weekly');
    assert.strictEqual(model.periodToApi({ type: 'weekly' }), 'weekly');
    assert.strictEqual(model.periodToApi({ timespan: 'month', multiplier: 1 }), 'monthly');
    assert.strictEqual(model.periodToApi({ timespan: 'monthly', multiplier: 3 }), 'quarterly');
    assert.strictEqual(model.periodToApi({ timespan: 'month', multiplier: 12 }), 'yearly');
    assert.strictEqual(model.periodToApi({ timespan: 'quarter' }), 'quarterly');
    assert.strictEqual(model.periodToApi({ timespan: 'year' }), 'yearly');
  });

  test('handles strings, empty, null, unknown and bad multipliers', () => {
    assert.strictEqual(model.periodToApi('daily'), 'daily');
    assert.strictEqual(model.periodToApi(''), 'daily');
    assert.strictEqual(model.periodToApi(null), 'daily');
    assert.strictEqual(model.periodToApi(undefined), 'daily');
    assert.strictEqual(model.periodToApi({ timespan: 'second' }), 'daily');
    assert.strictEqual(model.periodToApi({ timespan: 'minute', multiplier: 0 }), '1m');
    assert.strictEqual(model.periodToApi({ timespan: 'minute', multiplier: 'x' }), '1m');
    assert.strictEqual(model.periodToApi({ timespan: 'minute', value: 15 }), '15m');
  });
});

describe('periodBucket', () => {
  test('builds daily, monthly, quarterly and yearly buckets', () => {
    assert.strictEqual(model.periodBucket(T, 'daily'), '2024-0-15');
    assert.strictEqual(model.periodBucket(Date.UTC(2024, 3, 1), 'quarterly'), '2024-Q1');
    assert.strictEqual(model.periodBucket(T, 'monthly'), '2024-M0');
    assert.strictEqual(model.periodBucket(T, 'yearly'), '2024-Y');
  });

  test('weekly bucket starts on Monday', () => {
    assert.strictEqual(model.periodBucket(T, 'weekly'), 'W1705276800000'); // Monday itself
    assert.strictEqual(model.periodBucket(T2, 'weekly'), 'W1704672000000'); // Sunday -> Monday Jan 8
  });

  test('builds minute and unknown-period buckets', () => {
    assert.strictEqual(model.periodBucket(T, '30m'), 'I947376');
    assert.strictEqual(model.periodBucket(T, 'abc'), 'T1705276800000');
  });

  test('invalid timestamps', () => {
    assert.strictEqual(model.periodBucket(undefined, 'daily'), '');
    assert.strictEqual(model.periodBucket('bad', 'daily'), '');
    assert.strictEqual(model.periodBucket(null, 'daily'), '1970-0-1'); // verbatim: Number(null) === 0
  });
});

describe('toMillis / finiteNumber / stablePercentage', () => {
  test('toMillis converts seconds but keeps milliseconds', () => {
    assert.strictEqual(model.toMillis(1705276800), 1705276800000);
    assert.strictEqual(model.toMillis(1705276800000), 1705276800000);
    assert.strictEqual(model.toMillis('1705276800'), 1705276800000);
    assert.strictEqual(model.toMillis(undefined), null);
    assert.strictEqual(model.toMillis('bad'), null);
    assert.strictEqual(model.toMillis(null), 0); // verbatim: Number(null) === 0
  });

  test('finiteNumber and stablePercentage', () => {
    assert.strictEqual(model.finiteNumber('12.5'), 12.5);
    assert.strictEqual(model.finiteNumber('bad'), null);
    assert.strictEqual(model.finiteNumber(Infinity), null);
    assert.strictEqual(model.stablePercentage(0.1 + 0.2), 0.3);
    assert.strictEqual(model.stablePercentage(1.23456789123), 1.2345678912);
    assert.strictEqual(model.stablePercentage(Infinity), null);
  });
});

describe('validRows', () => {
  test('filters invalid rows, converts fields, copies and sorts ascending', () => {
    const rows = [
      { timestamp: 3, close: '2' },
      { timestamp: 1, close: 1, extra: 'x' },
      { timestamp: '2', close: 3 },
      { timestamp: 'abc', close: 4 }, // dropped: bad timestamp
      { timestamp: null, close: 5 }, // kept verbatim: toMillis(null) === 0
      { timestamp: undefined, close: 6 }, // dropped
      null, // dropped
      { timestamp: 4, close: 'bad' } // dropped: bad close
    ];
    assert.deepEqual(model.validRows(rows), [
      { timestamp: 0, close: 5 },
      { timestamp: 1000, close: 1, extra: 'x' },
      { timestamp: 2000, close: 3 },
      { timestamp: 3000, close: 2 }
    ]);
    assert.deepEqual(rows[1], { timestamp: 1, close: 1, extra: 'x' }); // inputs untouched
    assert.strictEqual(rows[4].close, 5);
  });

  test('non-array input yields empty array', () => {
    assert.deepEqual(model.validRows(null), []);
    assert.deepEqual(model.validRows(undefined), []);
    assert.deepEqual(model.validRows('x'), []);
  });
});

describe('alignSeries', () => {
  test('aligns same-period rows by bucket (intraday times differ)', () => {
    const mainRows = [
      { timestamp: T, close: 1 },
      { timestamp: T + DAY, close: 2 },
      { timestamp: T + 2 * DAY, close: 3 }
    ];
    const overlayRows = [
      { timestamp: T + 3600000, close: 10 },
      { timestamp: T + DAY + 3600000, close: 20 } // third day has no overlay
    ];
    assert.deepEqual(model.alignSeries(mainRows, overlayRows, 'daily'), [
      { timestamp: T, main: mainRows[0], overlay: overlayRows[0] },
      { timestamp: T + DAY, main: mainRows[1], overlay: overlayRows[1] }
    ]);
  });

  test('aligns weekly buckets across different weekdays', () => {
    const mainRows = [
      { timestamp: T, close: 1 }, // Mon Jan 15
      { timestamp: T + 7 * DAY, close: 2 } // Mon Jan 22
    ];
    const overlayRows = [
      { timestamp: T + DAY, close: 10 }, // Tue Jan 16
      { timestamp: T + 8 * DAY, close: 20 } // Tue Jan 23
    ];
    const aligned = model.alignSeries(mainRows, overlayRows, 'weekly');
    assert.strictEqual(aligned.length, 2);
    assert.strictEqual(aligned[0].overlay.close, 10);
    assert.strictEqual(aligned[1].overlay.close, 20);
  });

  test('invalid or non-array input yields empty array', () => {
    assert.deepEqual(model.alignSeries(null, null, 'daily'), []);
    assert.deepEqual(model.alignSeries([{ timestamp: T, close: 1 }], [{ timestamp: T + 3600000, close: 2 }], 'abc'), []);
  });
});

describe('computeReturns / normalizeSeries', () => {
  test('computes returns against the first close after sorting', () => {
    const result = model.computeReturns([
      { timestamp: 3, close: '12.5' },
      { timestamp: 1, close: 10 },
      { timestamp: 2, close: 11 }
    ]);
    assert.strictEqual(result.length, 3);
    assert.strictEqual(result[0].timestamp, 1000);
    assert.strictEqual(result[0].returnPct, 0);
    assert.ok(Math.abs(result[1].returnPct - 10) < 1e-9); // float: 11 / 10 * 100
    assert.strictEqual(result[2].returnPct, 25);
    assert.strictEqual(result[2].source.close, 12.5);
  });

  test('empty rows or zero base yield empty array', () => {
    assert.deepEqual(model.computeReturns(null), []);
    assert.deepEqual(model.computeReturns([]), []);
    assert.deepEqual(model.computeReturns([{ timestamp: 1, close: 0 }, { timestamp: 2, close: 5 }]), []);
  });

  test('normalizeSeries delegates to computeReturns', () => {
    const rows = [{ timestamp: 2, close: 11 }, { timestamp: 1, close: 10 }];
    assert.deepEqual(model.normalizeSeries(rows), model.computeReturns(rows));
  });
});

describe('computeComparison', () => {
  test('combines aligned main/overlay returns with difference', () => {
    const aligned = [
      { timestamp: T, main: { timestamp: T, close: 10 }, overlay: { timestamp: T, close: 100 } },
      { timestamp: T + DAY, main: { timestamp: T + DAY, close: 11 }, overlay: { timestamp: T + DAY, close: 105 } }
    ];
    const result = model.computeComparison(aligned);
    assert.strictEqual(result.length, 2);
    assert.strictEqual(result[0].timestamp, T);
    assert.strictEqual(result[0].mainReturnPct, 0);
    assert.strictEqual(result[0].overlayReturnPct, 0);
    assert.strictEqual(result[0].differencePct, 0);
    assert.ok(Math.abs(result[1].mainReturnPct - 10) < 1e-9);
    assert.ok(Math.abs(result[1].overlayReturnPct - 5) < 1e-9);
    assert.ok(Math.abs(result[1].differencePct - 5) < 1e-9);
    assert.strictEqual(result[1].main, aligned[1].main);
    assert.strictEqual(result[1].overlay, aligned[1].overlay);
  });

  test('invalid input yields empty array', () => {
    assert.deepEqual(model.computeComparison(null), []);
    assert.deepEqual(model.computeComparison([]), []);
  });
});

describe('rangeOhlc / exactPeriodRow / commonRangeRows', () => {
  test('rangeOhlc normalizes a valid row and rejects invalid rows', () => {
    const row = { timestamp: T / 1000, open: 9, high: 11, low: 8, close: '10' };
    assert.deepEqual(model.rangeOhlc(row), {
      timestamp: T,
      open: 9,
      high: 11,
      low: 8,
      close: 10,
      source: row
    });
    assert.strictEqual(model.rangeOhlc(null), null);
    assert.strictEqual(model.rangeOhlc({ timestamp: T, close: 0 }), null);
    assert.strictEqual(model.rangeOhlc({ timestamp: T, close: 'bad' }), null);
    assert.strictEqual(model.rangeOhlc({ timestamp: 'bad', close: 5 }), null);
  });

  test('exactPeriodRow returns the last row matching the period bucket', () => {
    const rows = [
      { timestamp: T, close: 1 },
      { timestamp: T + 3600000, close: 2 },
      { timestamp: T + DAY, close: 3 }
    ];
    assert.strictEqual(model.exactPeriodRow(rows, T, 'daily').close, 2);
    assert.strictEqual(model.exactPeriodRow(rows, T / 1000, 'daily').close, 2); // seconds query
    assert.strictEqual(model.exactPeriodRow(rows, T + 2 * DAY, 'daily'), null);
    assert.strictEqual(model.exactPeriodRow(null, T, 'daily'), null);
    assert.strictEqual(model.exactPeriodRow(rows, 'bad', 'daily'), null);
  });

  test('commonRangeRows joins main indices to same-bucket overlays', () => {
    const mainRows = [
      { timestamp: T, close: 10 },
      null,
      { timestamp: T + DAY, close: 11 },
      { timestamp: T + 2 * DAY, close: 12 }
    ];
    const overlayRows = [
      { timestamp: T, close: 100 },
      { timestamp: T + DAY + 3600000, close: 110 } // third main day has no overlay
    ];
    const result = model.commonRangeRows(mainRows, overlayRows, 'daily', 0, 3);
    assert.strictEqual(result.length, 2);
    assert.strictEqual(result[0].mainIndex, 0);
    assert.strictEqual(result[0].main.close, 10);
    assert.strictEqual(result[0].overlay.close, 100);
    assert.strictEqual(result[1].mainIndex, 2);
    assert.strictEqual(result[1].overlay.close, 110);
    assert.deepEqual(model.commonRangeRows([], [], 'daily', 0, 2), []);
    assert.throws(() => model.commonRangeRows(null, null, 'daily', 0, 2), TypeError); // verbatim: no null guard
  });

  test('commonRangeRows matches weekly buckets across weekdays', () => {
    const result = model.commonRangeRows(
      [{ timestamp: T, close: 10 }],
      [{ timestamp: T + 6 * DAY, close: 100 }], // Sunday of same week
      'weekly', 0, 0
    );
    assert.strictEqual(result.length, 1);
    assert.strictEqual(result[0].overlay.close, 100);
  });
});

describe('computeEquivalentPriceExtent', () => {
  test('computes extent and writes equivalentPrice onto overlay rows', () => {
    const mainRows = [
      { timestamp: T, open: 9.5, low: 9, high: 11, close: 10 },
      { timestamp: T + DAY, open: 10, low: 9.5, high: 11.5, close: 11 }
    ];
    const overlayRowA = { main: { close: 10 }, overlayReturnPct: 0 };
    const overlayRowB = { main: { close: 10 }, overlayReturnPct: 10 };
    const extent = model.computeEquivalentPriceExtent(mainRows, [
      { comparison: [overlayRowA, overlayRowB] }
    ]);
    assert.deepEqual(extent, {
      min: 9,
      max: 11.5,
      overlayMin: 10,
      overlayMax: 11
    });
    assert.strictEqual(overlayRowA.equivalentPrice, 10); // side effect preserved
    assert.strictEqual(overlayRowB.equivalentPrice, 11);
    assert.strictEqual(mainRows[0].equivalentPrice, undefined); // main rows untouched
  });

  test('returns null without overlays, values or a valid base', () => {
    assert.strictEqual(model.computeEquivalentPriceExtent(null, null), null);
    assert.strictEqual(model.computeEquivalentPriceExtent(
      [{ timestamp: T, low: 9, high: 11, close: 10 }], null
    ), null);
    assert.strictEqual(model.computeEquivalentPriceExtent(
      [{ timestamp: T, low: 9, high: 11, close: 10 }],
      [{ comparison: [{ main: { close: 0 }, overlayReturnPct: 10 }] }]
    ), null);
    assert.strictEqual(model.computeEquivalentPriceExtent(
      [{ timestamp: T, low: 9, high: 11, close: 10 }], [{}]
    ), null);
  });
});

describe('percent and price coordinate helpers', () => {
  test('computePercentScale pads and maps 0 to zeroY', () => {
    const scale = model.computePercentScale([0, 10], 200, { paddingRatio: 0, top: 20, bottom: 20 });
    assert.strictEqual(scale.min, -1); // minimum padding is 1
    assert.strictEqual(scale.max, 11);
    assert.strictEqual(scale.top, 20);
    assert.strictEqual(scale.bottom, 20);
    assert.strictEqual(scale.plotHeight, 160);
    assert.strictEqual(scale.pixelsPerPercent, 160 / 12);
    assert.strictEqual(scale.zeroY, 20 + (11 / 12) * 160);
  });

  test('computePercentScale survives empty values and degenerate height', () => {
    const scale = model.computePercentScale(null, 0, null);
    assert.strictEqual(scale.min, -1);
    assert.strictEqual(scale.max, 1);
    assert.strictEqual(scale.top, 0);
    assert.strictEqual(scale.bottom, 0);
    assert.strictEqual(scale.plotHeight, 1);
    assert.strictEqual(scale.zeroY, 0.5);
  });

  test('mapPercentToY maps values and rejects invalid input', () => {
    const scale = model.computePercentScale([0, 10], 200, { paddingRatio: 0, top: 20, bottom: 20 });
    assert.strictEqual(model.mapPercentToY(5, scale), 100);
    assert.strictEqual(model.mapPercentToY(11, scale), 20);
    assert.strictEqual(model.mapPercentToY(-1, scale), 180);
    assert.strictEqual(model.mapPercentToY('bad', scale), null);
    assert.strictEqual(model.mapPercentToY(5, null), null);
    assert.strictEqual(model.mapPercentToY(5, { min: 5, max: 5, top: 0, plotHeight: 100 }), null);
  });

  test('returnPctToEquivalentPrice converts a return into an equivalent price', () => {
    assert.strictEqual(model.returnPctToEquivalentPrice(100, 10), 110);
    assert.strictEqual(model.returnPctToEquivalentPrice(100, 0), 100);
    assert.strictEqual(model.returnPctToEquivalentPrice('100', '5'), 105);
    assert.strictEqual(model.returnPctToEquivalentPrice(0, 10), null);
    assert.strictEqual(model.returnPctToEquivalentPrice(-1, 10), null);
    assert.strictEqual(model.returnPctToEquivalentPrice(100, 'bad'), null);
  });

  test('computePriceScale pads with a floor and handles zero span', () => {
    const scale = model.computePriceScale({ min: 0, max: 10 }, 200, { paddingRatio: 0, top: 20, bottom: 20 });
    assert.strictEqual(scale.min, -1e-8);
    assert.strictEqual(scale.max, 10 + 1e-8);
    assert.strictEqual(scale.top, 20);
    assert.strictEqual(scale.bottom, 20);
    assert.strictEqual(scale.plotHeight, 160);

    const flat = model.computePriceScale({ min: 0, max: 0 }, 100, null);
    assert.strictEqual(flat.min, -0.08);
    assert.strictEqual(flat.max, 0.08);
    assert.strictEqual(flat.plotHeight, 60);

    assert.strictEqual(model.computePriceScale(null, 100, null), null);
    assert.strictEqual(model.computePriceScale({ min: 'x', max: 5 }, 100, null), null);
  });

  test('mapPriceToY maps values and rejects invalid input', () => {
    const scale = { min: 0, max: 10, top: 20, plotHeight: 160 };
    assert.strictEqual(model.mapPriceToY(0, scale), 180);
    assert.strictEqual(model.mapPriceToY(5, scale), 100);
    assert.strictEqual(model.mapPriceToY(10, scale), 20);
    assert.strictEqual(model.mapPriceToY('bad', scale), null);
    assert.strictEqual(model.mapPriceToY(5, null), null);
    assert.strictEqual(model.mapPriceToY(5, { min: 5, max: 5, top: 0, plotHeight: 1 }), null);
  });
});

describe('range counts', () => {
  test('countRangeBars skips invalid and zero-close rows', () => {
    const rows = [
      { timestamp: T, close: 1 },
      null,
      { timestamp: T, close: 0 },
      { timestamp: T + DAY, close: 2 }
    ];
    assert.strictEqual(model.countRangeBars(rows, 0, 3), 2);
    assert.strictEqual(model.countRangeBars(null, 0, 3), 0);
  });

  test('countRangeTradingDays counts distinct UTC days', () => {
    const rows = [
      { timestamp: T, close: 1 },
      { timestamp: T + 3600000, close: 2 },
      null,
      { timestamp: T + DAY, close: 3 }
    ];
    assert.strictEqual(model.countRangeTradingDays(rows, 0, 3), 2);
    assert.strictEqual(model.countRangeTradingDays(null, 0, 3), 0);
  });

  test('naturalDayCount includes both endpoints and is direction-agnostic', () => {
    assert.strictEqual(model.naturalDayCount(T, T), 1);
    assert.strictEqual(model.naturalDayCount(T, T + 2 * DAY), 3);
    assert.strictEqual(model.naturalDayCount(T + 2 * DAY, T), 3);
    assert.strictEqual(model.naturalDayCount('bad', T), 0);
    assert.strictEqual(model.naturalDayCount(null, null), 1); // verbatim: toMillis(null) === 0
  });
});

describe('clampEndpoint / symbolIdentity', () => {
  test('clampEndpoint rounds and clamps within bounds', () => {
    assert.strictEqual(model.clampEndpoint(5, 0, 10), 5);
    assert.strictEqual(model.clampEndpoint(-3, 0, 10), 0);
    assert.strictEqual(model.clampEndpoint(12, 0, 10), 10);
    assert.strictEqual(model.clampEndpoint(5.6, 0, 10), 6);
    assert.strictEqual(model.clampEndpoint(5, 10, 0), 5); // swapped bounds
    assert.strictEqual(model.clampEndpoint('x', 0, 5), 5); // non-finite endpoint -> max
    assert.strictEqual(model.clampEndpoint(2, 'x', 'y'), 0); // non-finite bounds -> 0
    assert.strictEqual(model.clampEndpoint(NaN, 0, 3), 3);
  });

  test('symbolIdentity strips exchange prefixes and lowercases', () => {
    assert.strictEqual(model.symbolIdentity('SH600000'), '600000');
    assert.strictEqual(model.symbolIdentity('sz000001'), '000001');
    assert.strictEqual(model.symbolIdentity('bj430047'), '430047');
    assert.strictEqual(model.symbolIdentity('600000'), '600000');
    assert.strictEqual(model.symbolIdentity(null), '');
  });
});

describe('pathFor / pixelPoint', () => {
  test('pathFor emits SVG path commands with two decimals', () => {
    assert.strictEqual(model.pathFor([{ x: 1, y: 2 }, { x: 3, y: 4 }]), 'M1.00,2.00 L3.00,4.00');
    assert.strictEqual(model.pathFor([{ x: 1.006, y: 2.004 }]), 'M1.01,2.00');
    assert.strictEqual(model.pathFor([]), '');
  });

  test('pixelPoint unwraps nested arrays and normalizes numbers', () => {
    assert.deepEqual(model.pixelPoint({ x: 1, y: 2 }), { x: 1, y: 2 });
    assert.deepEqual(model.pixelPoint([{ x: 3, y: 4 }]), { x: 3, y: 4 });
    assert.deepEqual(model.pixelPoint([[{ x: 5, y: null }]]), { x: 5, y: 0 }); // Number(null) === 0
    assert.deepEqual(model.pixelPoint({ x: 1, y: undefined }), { x: 1, y: null });
    assert.deepEqual(model.pixelPoint({ x: 0, y: 5 }), { x: 0, y: 5 });
    assert.strictEqual(model.pixelPoint({ x: 'bad', y: 2 }), null);
    assert.strictEqual(model.pixelPoint(null), null);
  });
});

describe('formatters', () => {
  test('formatPct / formatPoints / formatPointMagnitude', () => {
    assert.strictEqual(model.formatPct(3.5), '+3.50%');
    assert.strictEqual(model.formatPct(-2), '-2.00%');
    assert.strictEqual(model.formatPct(0), '+0.00%');
    assert.strictEqual(model.formatPct('bad'), '--');
    assert.strictEqual(model.formatPoints(1.23), '+1.23 个百分点');
    assert.strictEqual(model.formatPoints(-4.5), '-4.50 个百分点');
    assert.strictEqual(model.formatPoints('bad'), '--');
    assert.strictEqual(model.formatPointMagnitude(-4.5), '4.50 个百分点');
    assert.strictEqual(model.formatPointMagnitude('bad'), '--');
  });

  test('rangeMovementStyle picks direction and color', () => {
    assert.deepEqual(model.rangeMovementStyle(1), { direction: 'up', color: '#ef5350' });
    assert.deepEqual(model.rangeMovementStyle(-1), { direction: 'down', color: '#26a69a' });
    assert.deepEqual(model.rangeMovementStyle(0), { direction: 'flat', color: '#64748b' });
    assert.deepEqual(model.rangeMovementStyle('bad'), { direction: 'flat', color: '#64748b' });
  });

  test('formatDate formats local calendar dates', () => {
    assert.strictEqual(model.formatDate(new Date(2024, 0, 5).getTime()), '2024-01-05');
    assert.strictEqual(model.formatDate(new Date(2024, 11, 31).getTime()), '2024-12-31');
    assert.strictEqual(model.formatDate('bad'), '--');
  });
});

describe('hashCode / pickColor', () => {
  test('hashCode is the deterministic FNV-1a variant', () => {
    assert.strictEqual(model.hashCode('a'), 3826002220);
    assert.strictEqual(model.hashCode('abc'), 440920331);
    assert.strictEqual(model.hashCode('sh600000'), 1770163608);
    assert.strictEqual(model.hashCode(''), 2166136261);
    assert.ok(model.hashCode('a') !== model.hashCode('b'));
    assert.ok(model.hashCode('x') >= 0 && model.hashCode('x') <= 0xFFFFFFFF);
  });

  test('pickColor deduplicates against used colors and falls back to null', () => {
    assert.strictEqual(model.pickColor({}, 'sh600000'), model.PALETTE[0]); // start index 0
    assert.strictEqual(model.pickColor({ [model.PALETTE[0]]: true }, 'sh600000'), model.PALETTE[1]);
    const elevenUsed = {};
    model.PALETTE.slice(0, 11).forEach((color) => { elevenUsed[color] = true; });
    assert.strictEqual(model.pickColor(elevenUsed, 'sh600000'), model.PALETTE[11]);
    const allUsed = {};
    model.PALETTE.forEach((color) => { allUsed[color] = true; });
    assert.strictEqual(model.pickColor(allUsed, 'sh600000'), null);
    assert.strictEqual(model.pickColor({}, 'abc'), model.PALETTE[11]); // 440920331 % 12 === 11
  });
});
