(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else {
    root.ChartComparisonModel = factory();
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /*
   * Pure model functions extracted verbatim from chart-comparison.js.
   * No DOM/network/chart-instance code lives here. Browser global name:
   * ChartComparisonModel.
   */
  var DAY = 86400000;
  var PALETTE = [
    '#f59e0b', '#2563eb', '#7c3aed', '#0891b2',
    '#db2777', '#0f766e', '#9333ea', '#ea580c',
    '#0369a1', '#c026d3', '#ca8a04', '#4f46e5'
  ];

  function finiteNumber(value) {
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function stablePercentage(value) {
    var number = Number(value);
    return isFinite(number) ? Number(number.toFixed(10)) : null;
  }

  function toMillis(value) {
    var number = finiteNumber(value);
    if (number == null) return null;
    return number < 10000000000 ? number * 1000 : number;
  }

  function periodToApi(period) {
    if (typeof period === 'string') return period || 'daily';
    period = period || {};
    var timespan = period.timespan || period.span || period.type;
    var multiplier = Number(period.multiplier || period.value || 1);
    if (!isFinite(multiplier) || multiplier <= 0) multiplier = 1;
    if (timespan === 'minute') return multiplier + 'm';
    if (timespan === 'hour') return (multiplier * 60) + 'm';
    if (timespan === 'day' || timespan === 'daily') return 'daily';
    if (timespan === 'week' || timespan === 'weekly') return 'weekly';
    if (timespan === 'month' || timespan === 'monthly') {
      if (multiplier === 3) return 'quarterly';
      if (multiplier === 12) return 'yearly';
      return 'monthly';
    }
    if (timespan === 'quarter' || timespan === 'quarterly') return 'quarterly';
    if (timespan === 'year' || timespan === 'yearly') return 'yearly';
    return 'daily';
  }

  function periodBucket(timestamp, apiPeriod) {
    var value = toMillis(timestamp);
    if (value == null) return '';
    var date = new Date(value);
    var year = date.getUTCFullYear();
    var month = date.getUTCMonth();
    var day = date.getUTCDate();
    if (apiPeriod === 'daily') return year + '-' + month + '-' + day;
    if (apiPeriod === 'weekly') {
      var start = Date.UTC(year, month, day);
      return 'W' + (start - ((date.getUTCDay() + 6) % 7) * DAY);
    }
    if (apiPeriod === 'monthly') return year + '-M' + month;
    if (apiPeriod === 'quarterly') return year + '-Q' + Math.floor(month / 3);
    if (apiPeriod === 'yearly') return year + '-Y';
    var minute = apiPeriod && /^(\d+)m$/.exec(apiPeriod);
    if (minute) return 'I' + Math.floor(value / (Number(minute[1]) * 60000));
    return 'T' + value;
  }

  function validRows(rows) {
    return (Array.isArray(rows) ? rows : []).filter(function (row) {
      return row && toMillis(row.timestamp) != null && finiteNumber(row.close) != null;
    }).map(function (row) {
      var copy = {};
      Object.keys(row).forEach(function (key) { copy[key] = row[key]; });
      copy.timestamp = toMillis(row.timestamp);
      copy.close = Number(row.close);
      return copy;
    }).sort(function (a, b) { return a.timestamp - b.timestamp; });
  }

  function alignSeries(mainRows, overlayRows, period) {
    var apiPeriod = periodToApi(period);
    var main = validRows(mainRows);
    var overlay = validRows(overlayRows);
    var byBucket = new Map();
    overlay.forEach(function (row) {
      var key = periodBucket(row.timestamp, apiPeriod);
      if (key) byBucket.set(key, row);
    });
    return main.map(function (row) {
      var match = byBucket.get(periodBucket(row.timestamp, apiPeriod));
      if (!match) return null;
      return { timestamp: row.timestamp, main: row, overlay: match };
    }).filter(Boolean);
  }

  function computeReturns(rows) {
    var valid = validRows(rows);
    if (!valid.length || Number(valid[0].close) === 0) return [];
    var base = Number(valid[0].close);
    return valid.map(function (row) {
      return {
        timestamp: row.timestamp,
        close: row.close,
        returnPct: (Number(row.close) / base - 1) * 100,
        source: row
      };
    });
  }

  function normalizeSeries(rows) {
    return computeReturns(rows);
  }

  function computeComparison(alignedRows) {
    var rows = Array.isArray(alignedRows) ? alignedRows : [];
    var main = computeReturns(rows.map(function (row) { return row.main; }));
    var overlay = computeReturns(rows.map(function (row) { return row.overlay; }));
    var count = Math.min(main.length, overlay.length);
    var result = [];
    for (var index = 0; index < count; index += 1) {
      result.push({
        timestamp: rows[index].timestamp,
        mainReturnPct: main[index].returnPct,
        overlayReturnPct: overlay[index].returnPct,
        differencePct: main[index].returnPct - overlay[index].returnPct,
        main: rows[index].main,
        overlay: rows[index].overlay
      });
    }
    return result;
  }

  function rangeOhlc(row) {
    if (!row || toMillis(row.timestamp) == null) return null;
    var close = finiteNumber(row.close);
    if (close == null || close <= 0) return null;
    return {
      timestamp: toMillis(row.timestamp),
      open: finiteNumber(row.open),
      high: finiteNumber(row.high),
      low: finiteNumber(row.low),
      close: close,
      source: row
    };
  }

  function exactPeriodRow(rows, timestamp, period) {
    var key = periodBucket(timestamp, periodToApi(period));
    if (!key) return null;
    var match = null;
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (!row || periodBucket(row.timestamp, periodToApi(period)) !== key) return;
      match = row;
    });
    return match;
  }

  function commonRangeRows(mainRows, overlayRows, period, startIndex, endIndex) {
    var apiPeriod = periodToApi(period);
    var overlayByBucket = new Map();
    (Array.isArray(overlayRows) ? overlayRows : []).forEach(function (row) {
      var key = periodBucket(row && row.timestamp, apiPeriod);
      if (key) overlayByBucket.set(key, row);
    });
    var rows = [];
    for (var index = startIndex; index <= endIndex; index += 1) {
      var main = rangeOhlc(mainRows[index]);
      if (!main) continue;
      var overlay = rangeOhlc(overlayByBucket.get(periodBucket(main.timestamp, apiPeriod)));
      if (overlay) rows.push({ mainIndex: index, main: main, overlay: overlay });
    }
    return rows;
  }

  function computePercentScale(values, height, options) {
    var finiteValues = (Array.isArray(values) ? values : []).map(finiteNumber).filter(function (value) { return value != null; });
    if (!finiteValues.length) finiteValues = [0];
    finiteValues.push(0);
    var minValue = Math.min.apply(Math, finiteValues);
    var maxValue = Math.max.apply(Math, finiteValues);
    var span = maxValue - minValue;
    if (!isFinite(span) || span <= 0) span = 1;
    var paddingRatio = options && finiteNumber(options.paddingRatio) != null ? Number(options.paddingRatio) : 0.08;
    if (!isFinite(paddingRatio) || paddingRatio < 0) paddingRatio = 0.08;
    var padding = Math.max(1, span * paddingRatio);
    var min = minValue - padding;
    var max = maxValue + padding;
    var safeHeight = Math.max(1, Number(height) || 1);
    var top = options && finiteNumber(options.top) != null ? Number(options.top) : 14;
    var bottom = options && finiteNumber(options.bottom) != null ? Number(options.bottom) : 26;
    top = Math.max(0, Math.min(safeHeight - 1, top));
    bottom = Math.max(0, Math.min(safeHeight - top - 1, bottom));
    var plotHeight = Math.max(1, safeHeight - top - bottom);
    var pixelsPerPercent = plotHeight / (max - min);
    return {
      min: min,
      max: max,
      top: top,
      bottom: bottom,
      plotHeight: plotHeight,
      pixelsPerPercent: pixelsPerPercent,
      zeroY: top + (max / (max - min)) * plotHeight
    };
  }

  function mapPercentToY(value, scale) {
    var number = finiteNumber(value);
    if (number == null || !scale || !isFinite(scale.min) || !isFinite(scale.max) || scale.max <= scale.min) return null;
    return Number(scale.top) + (Number(scale.max) - number) / (Number(scale.max) - Number(scale.min)) * Number(scale.plotHeight);
  }

  function returnPctToEquivalentPrice(basePrice, returnPct) {
    var base = finiteNumber(basePrice);
    var percentage = finiteNumber(returnPct);
    if (base == null || base <= 0 || percentage == null) return null;
    return stablePercentage(base * (1 + percentage / 100));
  }

  function computeEquivalentPriceExtent(mainRows, overlays) {
    var values = [];
    validRows(mainRows).forEach(function (row) {
      var low = finiteNumber(row.low);
      var high = finiteNumber(row.high);
      if (low != null) values.push(low);
      if (high != null) values.push(high);
    });
    var overlayValues = [];
    (Array.isArray(overlays) ? overlays : []).forEach(function (item) {
      var rows = Array.isArray(item && item.comparison) ? item.comparison : [];
      if (!rows.length) return;
      var base = finiteNumber(rows[0] && rows[0].main && rows[0].main.close);
      if (base == null || base <= 0) return;
      rows.forEach(function (row) {
        var equivalent = returnPctToEquivalentPrice(base, row.overlayReturnPct);
        if (equivalent != null) {
          values.push(equivalent);
          overlayValues.push(equivalent);
          row.equivalentPrice = equivalent;
        }
      });
    });
    if (!overlayValues.length || !values.length) return null;
    return {
      min: Math.min.apply(Math, values),
      max: Math.max.apply(Math, values),
      overlayMin: Math.min.apply(Math, overlayValues),
      overlayMax: Math.max.apply(Math, overlayValues)
    };
  }

  function computePriceScale(extent, height, options) {
    if (!extent || finiteNumber(extent.min) == null || finiteNumber(extent.max) == null) return null;
    var minValue = Number(extent.min);
    var maxValue = Number(extent.max);
    var span = maxValue - minValue;
    if (!isFinite(span) || span <= 0) span = Math.max(1, Math.abs(maxValue) * 0.01);
    var paddingRatio = options && finiteNumber(options.paddingRatio) != null ? Number(options.paddingRatio) : 0.08;
    var padding = Math.max(span * Math.max(0, paddingRatio), 1e-8);
    var safeHeight = Math.max(1, Number(height) || 1);
    var top = options && finiteNumber(options.top) != null ? Number(options.top) : 14;
    var bottom = options && finiteNumber(options.bottom) != null ? Number(options.bottom) : 26;
    top = Math.max(0, Math.min(safeHeight - 1, top));
    bottom = Math.max(0, Math.min(safeHeight - top - 1, bottom));
    return {
      min: minValue - padding,
      max: maxValue + padding,
      top: top,
      bottom: bottom,
      plotHeight: Math.max(1, safeHeight - top - bottom)
    };
  }

  function mapPriceToY(value, scale) {
    var number = finiteNumber(value);
    if (number == null || !scale || scale.max <= scale.min) return null;
    return scale.top + (scale.max - number) / (scale.max - scale.min) * scale.plotHeight;
  }

  function countRangeBars(rows, startIndex, endIndex) {
    var count = 0;
    for (var index = Number(startIndex); index <= Number(endIndex); index += 1) {
      if (rangeOhlc(Array.isArray(rows) ? rows[index] : null)) count += 1;
    }
    return count;
  }

  function countRangeTradingDays(rows, startIndex, endIndex) {
    var days = {};
    for (var index = Number(startIndex); index <= Number(endIndex); index += 1) {
      var item = rangeOhlc(Array.isArray(rows) ? rows[index] : null);
      if (!item) continue;
      var date = new Date(item.timestamp);
      var key = date.getUTCFullYear() + '-' + date.getUTCMonth() + '-' + date.getUTCDate();
      days[key] = true;
    }
    return Object.keys(days).length;
  }

  function naturalDayCount(startTimestamp, endTimestamp) {
    var start = toMillis(startTimestamp);
    var end = toMillis(endTimestamp);
    if (start == null || end == null) return 0;
    return Math.floor(Math.abs(end - start) / DAY) + 1;
  }

  function clampEndpoint(endpoint, min, max) {
    var value = Math.round(Number(endpoint));
    var low = Math.round(Number(min));
    var high = Math.round(Number(max));
    if (!isFinite(low)) low = 0;
    if (!isFinite(high)) high = low;
    if (high < low) { var swap = low; low = high; high = swap; }
    if (!isFinite(value)) value = high;
    return Math.max(low, Math.min(high, value));
  }

  function symbolIdentity(code) {
    return String(code == null ? '' : code).toLowerCase().replace(/^(sh|sz|bj)/, '');
  }

  function pathFor(points) {
    return points.map(function (point, index) {
      return (index ? 'L' : 'M') + point.x.toFixed(2) + ',' + point.y.toFixed(2);
    }).join(' ');
  }

  function formatPct(value) {
    var number = Number(value);
    if (!isFinite(number)) return '--';
    return (number >= 0 ? '+' : '') + number.toFixed(2) + '%';
  }

  function formatPoints(value) {
    var number = Number(value);
    if (!isFinite(number)) return '--';
    return (number >= 0 ? '+' : '') + number.toFixed(2) + ' 个百分点';
  }

  function formatPointMagnitude(value) {
    var number = Number(value);
    if (!isFinite(number)) return '--';
    return Math.abs(number).toFixed(2) + ' 个百分点';
  }

  function rangeMovementStyle(returnPct) {
    var value = finiteNumber(returnPct);
    if (value != null && value > 0) return { direction: 'up', color: '#ef5350' };
    if (value != null && value < 0) return { direction: 'down', color: '#26a69a' };
    return { direction: 'flat', color: '#64748b' };
  }

  function formatDate(timestamp) {
    var date = new Date(Number(timestamp));
    if (!isFinite(date.getTime())) return '--';
    var month = String(date.getMonth() + 1);
    var day = String(date.getDate());
    return date.getFullYear() + '-' + (month.length < 2 ? '0' : '') + month + '-' + (day.length < 2 ? '0' : '') + day;
  }

  function hashCode(value) {
    var hash = 2166136261;
    String(value || '').split('').forEach(function (character) {
      hash ^= character.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    });
    return hash >>> 0;
  }

  function pixelPoint(value) {
    var point = Array.isArray(value) ? value[0] : value;
    if (Array.isArray(point)) point = point[0];
    var x = point && finiteNumber(point.x);
    var y = point && finiteNumber(point.y);
    return x == null ? null : { x: Number(x), y: y == null ? null : Number(y) };
  }

  /*
   * Same selection loop as ComparisonController.prototype._pickColor in
   * chart-comparison.js; `used` is the map of colors already taken.
   */
  function pickColor(used, code) {
    var start = hashCode(code) % PALETTE.length;
    for (var offset = 0; offset < PALETTE.length; offset += 1) {
      var candidate = PALETTE[(start + offset) % PALETTE.length];
      if (!used[candidate]) return candidate;
    }
    return null;
  }

  return {
    DAY: DAY,
    PALETTE: PALETTE,
    finiteNumber: finiteNumber,
    stablePercentage: stablePercentage,
    toMillis: toMillis,
    periodToApi: periodToApi,
    periodBucket: periodBucket,
    validRows: validRows,
    alignSeries: alignSeries,
    computeReturns: computeReturns,
    normalizeSeries: normalizeSeries,
    computeComparison: computeComparison,
    rangeOhlc: rangeOhlc,
    exactPeriodRow: exactPeriodRow,
    commonRangeRows: commonRangeRows,
    computePercentScale: computePercentScale,
    mapPercentToY: mapPercentToY,
    returnPctToEquivalentPrice: returnPctToEquivalentPrice,
    computeEquivalentPriceExtent: computeEquivalentPriceExtent,
    computePriceScale: computePriceScale,
    mapPriceToY: mapPriceToY,
    countRangeBars: countRangeBars,
    countRangeTradingDays: countRangeTradingDays,
    naturalDayCount: naturalDayCount,
    clampEndpoint: clampEndpoint,
    symbolIdentity: symbolIdentity,
    pathFor: pathFor,
    formatPct: formatPct,
    formatPoints: formatPoints,
    formatPointMagnitude: formatPointMagnitude,
    rangeMovementStyle: rangeMovementStyle,
    formatDate: formatDate,
    hashCode: hashCode,
    pixelPoint: pixelPoint,
    pickColor: pickColor
  };
}));
