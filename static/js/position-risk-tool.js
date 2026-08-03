/**
 * position-risk-tool.js - Interactive long/short risk-reward drawing tools.
 *
 * This is a chart measurement tool only. It never submits an order.
 */
(function (global) {
  'use strict';

  var LONG_NAME = 'boardLongPosition';
  var SHORT_NAME = 'boardShortPosition';
  var PRICE_RANGE_NAME = 'boardPriceRange';
  var GROUP_ID = 'board-position-risk';
  var chart = null;
  var registered = false;
  var toolButtons = [];
  var modal = null;
  var editingId = null;
  var lastCreatedId = null;
  var summaryDom = null;
  var summaryTimer = null;
  var summaryPosition = null;
  var summaryDrag = null;
  var summaryDragCleanup = null;
  var chartUnbinds = [];

  function model() {
    return global.PositionRiskModel || null;
  }

  function rangeModel() {
    return global.PriceRangeModel || null;
  }

  function finite(value) {
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function formatNumber(value, digits) {
    var number = finite(value);
    if (number == null) return '--';
    return number.toLocaleString('zh-CN', {
      minimumFractionDigits: digits == null ? 2 : digits,
      maximumFractionDigits: digits == null ? 2 : digits,
    });
  }

  function formatMoney(value) {
    var number = finite(value);
    if (number == null) return '--';
    return (number < 0 ? '-¥' : '¥') + formatNumber(Math.abs(number), 2);
  }

  function currentPeriodValue() {
    try {
      if (global.pro && typeof global.pro.getPeriod === 'function') {
        var proPeriod = global.pro.getPeriod();
        if (proPeriod) return proPeriod;
      }
    } catch (e) {}
    return global.__board_ctx && global.__board_ctx.period || 'daily';
  }

  function normalizeRangePeriod(value) {
    var multiplier = 1;
    var timespan = '';
    if (value && typeof value === 'object') {
      multiplier = Math.max(1, Math.round(finite(value.multiplier) || 1));
      timespan = String(value.timespan || value.span || value.type || '').toLowerCase();
    } else {
      var text = String(value || 'daily').trim().toLowerCase();
      var intraday = text.match(/^(\d+)\s*(m|min|minute|h|hr|hour)$/);
      if (intraday) {
        multiplier = Math.max(1, Number(intraday[1]));
        timespan = intraday[2].charAt(0) === 'h' ? 'hour' : 'minute';
      } else if (/^\d+$/.test(text)) {
        multiplier = Math.max(1, Number(text));
        timespan = 'minute';
      } else {
        var aliases = {
          daily: 'day', day: 'day', d: 'day', '日': 'day',
          weekly: 'week', week: 'week', w: 'week', '周': 'week',
          monthly: 'month', month: 'month', mo: 'month', '月': 'month',
          quarterly: 'quarter', quarter: 'quarter', q: 'quarter', '季': 'quarter',
          yearly: 'year', year: 'year', y: 'year', '年': 'year',
        };
        timespan = aliases[text] || 'day';
      }
    }
    if (timespan === 'min' || timespan === 'm') timespan = 'minute';
    if (timespan === 'hr' || timespan === 'h') timespan = 'hour';
    if (timespan === 'daily') timespan = 'day';
    if (timespan === 'weekly') timespan = 'week';
    if (timespan === 'monthly') timespan = 'month';
    if (timespan === 'yearly') timespan = 'year';
    if (timespan === 'month' && multiplier === 3) timespan = 'quarter';
    if ((timespan === 'month' && multiplier === 12) || timespan === 'year') {
      timespan = 'year';
      multiplier = 1;
    }
    if (timespan === 'minute' && multiplier >= 60 && multiplier % 60 === 0) {
      timespan = 'hour';
      multiplier = multiplier / 60;
    }
    var validTimespans = ['minute', 'hour', 'day', 'week', 'month', 'quarter', 'year'];
    if (validTimespans.indexOf(timespan) < 0) {
      timespan = 'day';
      multiplier = 1;
    }
    var labels = {
      minute: { duration: '分钟', line: multiplier + '分钟线' },
      hour: { duration: '小时', line: multiplier + '小时线' },
      day: { duration: '天', line: multiplier === 1 ? '日线' : multiplier + '日线' },
      week: { duration: '周', line: multiplier === 1 ? '周线' : multiplier + '周线' },
      month: { duration: '个月', line: multiplier === 1 ? '月线' : multiplier + '月线' },
      quarter: { duration: '个季度', line: '季线' },
      year: { duration: '年', line: '年线' },
    };
    return {
      timespan: timespan,
      multiplier: multiplier,
      durationLabel: labels[timespan].duration,
      lineLabel: labels[timespan].line,
    };
  }

  function pointTimestamp(point) {
    if (!point) return null;
    var value = finite(point.timestamp != null ? point.timestamp : point.time);
    if (value == null) return null;
    return value < 10000000000 ? value * 1000 : value;
  }

  function formatRangeDate(value, intraday) {
    var stamp = pointTimestamp({ timestamp: value });
    if (stamp == null) return '';
    var date = new Date(stamp);
    if (isNaN(date.getTime())) return '';
    function pad(number) { return String(number).padStart(2, '0'); }
    var result = date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
    if (intraday) result += ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    return result;
  }

  function calendarMonthSpan(startTimestamp, endTimestamp) {
    var start = new Date(Math.min(startTimestamp, endTimestamp));
    var end = new Date(Math.max(startTimestamp, endTimestamp));
    return Math.abs((end.getFullYear() - start.getFullYear()) * 12 + end.getMonth() - start.getMonth());
  }

  function rangeDuration(startTimestamp, endTimestamp, period) {
    var difference = Math.abs(endTimestamp - startTimestamp);
    if (period.timespan === 'minute') return Math.round(difference / 60000);
    if (period.timespan === 'hour') return Math.round(difference / 3600000);
    if (period.timespan === 'day') return Math.round(difference / 86400000);
    if (period.timespan === 'week') return Math.round(difference / 604800000);
    if (period.timespan === 'month') return calendarMonthSpan(startTimestamp, endTimestamp);
    if (period.timespan === 'quarter') return Math.round(calendarMonthSpan(startTimestamp, endTimestamp) / 3);
    if (period.timespan === 'year') {
      return Math.abs(new Date(endTimestamp).getFullYear() - new Date(startTimestamp).getFullYear());
    }
    return Math.round(difference / 86400000);
  }

  function rangeKlineCount(points) {
    if (!points || points.length < 2) return null;
    var firstIndex = finite(points[0] && points[0].dataIndex);
    var secondIndex = finite(points[1] && points[1].dataIndex);
    if (firstIndex != null && secondIndex != null) {
      return Math.abs(Math.round(secondIndex) - Math.round(firstIndex)) + 1;
    }
    var startTimestamp = pointTimestamp(points[0]);
    var endTimestamp = pointTimestamp(points[1]);
    if (startTimestamp == null || endTimestamp == null) return null;
    var current = activeChart();
    var rows = [];
    try { rows = current && typeof current.getDataList === 'function' ? current.getDataList() || [] : []; }
    catch (e) { rows = []; }
    var left = Math.min(startTimestamp, endTimestamp);
    var right = Math.max(startTimestamp, endTimestamp);
    var count = rows.filter(function (row) {
      var stamp = pointTimestamp(row);
      return stamp != null && stamp >= left && stamp <= right;
    }).length;
    return count || null;
  }

  function priceRangeTimingText(points, periodValue) {
    if (!points || points.length < 2) return '';
    var period = normalizeRangePeriod(periodValue);
    var startTimestamp = pointTimestamp(points[0]);
    var endTimestamp = pointTimestamp(points[1]);
    var count = rangeKlineCount(points);
    var parts = [];
    if (startTimestamp != null && endTimestamp != null) {
      var intraday = period.timespan === 'minute' || period.timespan === 'hour';
      parts.push(formatRangeDate(startTimestamp, intraday) + ' → ' + formatRangeDate(endTimestamp, intraday));
      parts.push('跨度 ' + rangeDuration(startTimestamp, endTimestamp, period) + period.durationLabel);
    }
    if (count != null) parts.push(count + '根' + period.lineLabel);
    return parts.join(' · ');
  }

  function defaults(direction) {
    var normalizedDirection = direction === 'short' ? 'short' : 'long';
    return {
      direction: normalizedDirection,
      accountSize: 100000,
      riskMode: 'percent',
      risk: 1,
      lotSize: 1,
      leverage: 1,
      pointValue: 1,
      qtyPrecision: 0,
      investmentAmount: normalizedDirection === 'long' ? 10000 : null,
      positionNumber: null,
      alwaysShowStats: true,
    };
  }

  function readExtendData(overlay, direction) {
    var raw = overlay && overlay.extendData;
    if (typeof raw === 'function') {
      try { raw = raw(overlay); } catch (e) { raw = null; }
    }
    return Object.assign(defaults(direction), raw && typeof raw === 'object' ? raw : {});
  }

  function overlayPoints(overlay) {
    if (!overlay) return [];
    try {
      if (typeof overlay.getPoints === 'function') return overlay.getPoints() || [];
    } catch (e) {}
    return Array.isArray(overlay.points) ? overlay.points : [];
  }

  function directionFor(name, extendData) {
    if (extendData && extendData.direction === 'short') return 'short';
    return name === SHORT_NAME ? 'short' : 'long';
  }

  function normalizedPositionNumber(value) {
    var number = Math.round(Number(value));
    return isFinite(number) && number > 0 ? number : null;
  }

  function positionLabel(direction, settings) {
    return (direction === 'short' ? '空头' : '多头') + (normalizedPositionNumber(settings && settings.positionNumber) || 1);
  }

  function positionNumberForOverlay(overlay, direction, settings) {
    var explicit = normalizedPositionNumber(settings && settings.positionNumber);
    if (explicit) return explicit;
    var targetId = overlayIdOf(overlay);
    var ordinal = 0;
    var matches = collectPositionOverlays(activeChart());
    for (var index = 0; index < matches.length; index += 1) {
      var candidate = matches[index];
      var name = overlayName(candidate);
      var data = readExtendData(candidate, name === SHORT_NAME ? 'short' : 'long');
      if (directionFor(name, data) !== direction) continue;
      ordinal += 1;
      if (candidate === overlay || (targetId && overlayIdOf(candidate) === targetId)) return ordinal;
    }
    return 1;
  }

  function nextPositionNumber(direction, current) {
    var ordinal = 0;
    var maximum = 0;
    collectPositionOverlays(current).forEach(function (overlay) {
      var name = overlayName(overlay);
      var data = readExtendData(overlay, name === SHORT_NAME ? 'short' : 'long');
      if (directionFor(name, data) !== direction) return;
      ordinal += 1;
      maximum = Math.max(maximum, normalizedPositionNumber(data.positionNumber) || ordinal);
    });
    return maximum + 1;
  }

  function calculate(direction, points, settings) {
    if (!points || points.length < 3) return null;
    var input = Object.assign({}, settings, {
      direction: direction,
      entry: finite(points[0].value),
      target: finite(points[1].value),
      stop: finite(points[2].value),
    });
    var riskModel = model();
    if (riskModel && typeof riskModel.calculatePosition === 'function') {
      return riskModel.calculatePosition(input);
    }
    var entry = input.entry;
    var target = input.target;
    var stop = input.stop;
    if (!(entry > 0) || !(target > 0) || !(stop > 0)) return { ok: false };
    var profitDistance = direction === 'long' ? target - entry : entry - target;
    var stopDistance = direction === 'long' ? entry - stop : stop - entry;
    if (!(profitDistance > 0) || !(stopDistance > 0)) return { ok: false };
    return {
      ok: true,
      entry: entry,
      target: target,
      stop: stop,
      targetPct: profitDistance / entry * 100,
      stopPct: stopDistance / entry * 100,
      profitLossRatio: profitDistance / stopDistance,
      riskReward: profitDistance / stopDistance,
      qty: null,
      profitPnl: null,
      lossPnl: null,
    };
  }

  function overlayName(overlay) {
    if (!overlay) return '';
    if (typeof overlay.getName === 'function') {
      try { return overlay.getName() || ''; } catch (e) {}
    }
    return overlay.name || '';
  }

  function flattenOverlays(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) {
      return raw.reduce(function (items, value) { return items.concat(flattenOverlays(value)); }, []);
    }
    if (typeof raw.values === 'function') {
      try { return flattenOverlays(Array.from(raw.values())); } catch (e) {}
    }
    if (typeof raw === 'object' && !overlayName(raw) && !overlayPoints(raw).length) {
      return Object.keys(raw).reduce(function (items, key) {
        return items.concat(flattenOverlays(raw[key]));
      }, []);
    }
    return [raw];
  }

  function collectAllOverlays(current) {
    current = current || activeChart();
    if (!current) return [];
    var raw = [];
    try {
      if (typeof current.getOverlays === 'function') raw = current.getOverlays() || [];
      if ((!raw || !flattenOverlays(raw).length) && typeof current.getChartStore === 'function') {
        var store = current.getChartStore();
        store = store && typeof store.getOverlayStore === 'function' ? store.getOverlayStore() : null;
        if (store && typeof store.getInstances === 'function') raw = store.getInstances() || [];
      }
    } catch (e) { raw = []; }
    return flattenOverlays(raw);
  }

  function collectPositionOverlays(current) {
    return collectAllOverlays(current).filter(function (overlay) {
      var name = overlayName(overlay);
      return name === LONG_NAME || name === SHORT_NAME;
    });
  }

  function summarizePositions(overlays) {
    var counts = { long: 0, short: 0 };
    var summary = {
      items: [],
      long: { count: 0, profit: 0, loss: 0, quantity: 0, weightedCostValue: 0, weightedAverageCost: null },
      short: { count: 0, profit: 0, loss: 0, quantity: 0, weightedCostValue: 0, weightedAverageCost: null },
      total: { count: 0, profit: 0, loss: 0, ratio: null, quantity: 0, weightedCostValue: 0, weightedAverageCost: null },
    };
    flattenOverlays(overlays).forEach(function (overlay) {
      var name = overlayName(overlay);
      if (name !== LONG_NAME && name !== SHORT_NAME) return;
      var direction = directionFor(name, readExtendData(overlay, name === SHORT_NAME ? 'short' : 'long'));
      var settings = readExtendData(overlay, direction);
      var points = overlayPoints(overlay);
      var result = calculate(direction, points, settings);
      if (!result || !result.ok) return;
      var profit = finite(result.profitPnl);
      var loss = finite(result.lossPnl);
      if (profit == null) profit = 0;
      if (loss == null) loss = 0;
      var entry = finite(result.entry);
      var target = finite(result.target);
      var stop = finite(result.stop);
      var quantity = finite(result.qty);
      if (entry == null && points[0]) entry = finite(points[0].value);
      if (target == null && points[1]) target = finite(points[1].value);
      if (stop == null && points[2]) stop = finite(points[2].value);
      if (!(quantity > 0)) quantity = 0;
      counts[direction] += 1;
      var positionNumber = normalizedPositionNumber(settings.positionNumber) || counts[direction];
      var bucket = summary[direction];
      bucket.count += 1;
      bucket.profit += profit;
      bucket.loss += loss;
      if (entry != null && quantity > 0) {
        bucket.quantity += quantity;
        bucket.weightedCostValue += entry * quantity;
      }
      summary.total.count += 1;
      summary.total.profit += profit;
      summary.total.loss += loss;
      if (entry != null && quantity > 0) {
        summary.total.quantity += quantity;
        summary.total.weightedCostValue += entry * quantity;
      }
      summary.items.push({
        direction: direction,
        number: positionNumber,
        label: (direction === 'long' ? '多头' : '空头') + positionNumber,
        profit: profit,
        loss: loss,
        entry: entry,
        target: target,
        stop: stop,
        quantity: quantity,
        targetPct: finite(result.targetPct),
        stopPct: finite(result.stopPct),
        ratio: finite(result.profitLossRatio == null ? result.riskReward : result.profitLossRatio),
      });
    });
    ['long', 'short', 'total'].forEach(function (key) {
      var bucket = summary[key];
      if (bucket.quantity > 0) {
        bucket.weightedAverageCost = bucket.weightedCostValue / bucket.quantity;
      }
    });
    if (Math.abs(summary.total.loss) > 0) {
      summary.total.ratio = summary.total.profit / Math.abs(summary.total.loss);
    }
    return summary;
  }

  function mainChartDom(current) {
    current = current || activeChart();
    if (!current || typeof current.getDom !== 'function') return null;
    var position = global.klinecharts && global.klinecharts.DomPosition;
    try {
      var main = current.getDom('candle_pane', position && position.Main);
      if (main) return main;
    } catch (e) {}
    try { return current.getDom('candle_pane') || current.getDom(); } catch (e) { return null; }
  }

  function mainBounds() {
    var main = mainChartDom();
    if (!main) return null;
    var width = typeof main.clientWidth === 'number' ? main.clientWidth : main.offsetWidth;
    var height = typeof main.clientHeight === 'number' ? main.clientHeight : main.offsetHeight;
    width = finite(width);
    height = finite(height);
    if (width == null || height == null || width <= 0 || height <= 0) return null;
    return { width: width, height: height };
  }

  function summarySize() {
    if (!summaryDom) return null;
    var width = typeof summaryDom.offsetWidth === 'number' ? summaryDom.offsetWidth : null;
    var height = typeof summaryDom.offsetHeight === 'number' ? summaryDom.offsetHeight : null;
    if ((width == null || height == null) && typeof summaryDom.getBoundingClientRect === 'function') {
      try {
        var rect = summaryDom.getBoundingClientRect();
        if (width == null) width = rect && rect.width;
        if (height == null) height = rect && rect.height;
      } catch (e) {}
    }
    width = finite(width);
    height = finite(height);
    if (width == null || height == null || width < 0 || height < 0) return null;
    return { width: width, height: height };
  }

  function currentSummaryOffset() {
    var left = 0;
    var top = 0;
    var main = mainChartDom();
    if (summaryDom && main &&
        typeof summaryDom.getBoundingClientRect === 'function' &&
        typeof main.getBoundingClientRect === 'function') {
      try {
        var summaryRect = summaryDom.getBoundingClientRect();
        var mainRect = main.getBoundingClientRect();
        var rectLeft = finite(parseFloat(summaryRect && summaryRect.left) - parseFloat(mainRect && mainRect.left));
        var rectTop = finite(parseFloat(summaryRect && summaryRect.top) - parseFloat(mainRect && mainRect.top));
        if (rectLeft != null && rectTop != null) return { left: rectLeft, top: rectTop };
      } catch (e) {}
    }
    if (summaryDom && summaryDom.style) {
      var styleLeft = finite(summaryDom.style.left);
      var styleTop = finite(summaryDom.style.top);
      if (styleLeft != null) left = styleLeft;
      if (styleTop != null) top = styleTop;
    }
    return { left: left, top: top };
  }

  function applySummaryPosition() {
    if (!summaryDom || !summaryPosition) return;
    var bounds = mainBounds();
    var size = summarySize();
    var left = finite(summaryPosition.left);
    var top = finite(summaryPosition.top);
    if (left == null) left = 0;
    if (top == null) top = 0;
    if (bounds && size) {
      left = clamp(left, 0, Math.max(0, bounds.width - size.width));
      top = clamp(top, 0, Math.max(0, bounds.height - size.height));
    }
    summaryPosition = { left: left, top: top };
    summaryDom.style = summaryDom.style || {};
    summaryDom.style.left = left + 'px';
    summaryDom.style.top = top + 'px';
    summaryDom.style.right = 'auto';
    summaryDom.style.bottom = 'auto';
  }

  function summaryInteractive(node) {
    if (!node) return false;
    if (typeof node.closest === 'function') {
      try {
        return !!node.closest('button, input, select, textarea, a, [role="button"], [contenteditable="true"], [data-action]');
      } catch (e) {}
    }
    var tag = typeof node.tagName === 'string' ? node.tagName.toLowerCase() : '';
    if (tag === 'button' || tag === 'input' || tag === 'select' || tag === 'textarea' || tag === 'a') return true;
    if (typeof node.getAttribute === 'function') {
      try {
        if (node.getAttribute('role') === 'button') return true;
        if (node.getAttribute('contenteditable') === 'true') return true;
        if (node.getAttribute('data-action') != null) return true;
      } catch (e) {}
    }
    return false;
  }

  function summaryDragStartTarget(target) {
    var node = target;
    while (node && node !== summaryDom) {
      if (summaryInteractive(node)) return true;
      node = node.parentNode;
    }
    return false;
  }

  function endSummaryDrag(event) {
    if (!summaryDrag) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    summaryDrag = null;
    if (summaryDragCleanup) {
      var cleanup = summaryDragCleanup;
      summaryDragCleanup = null;
      try { cleanup(); } catch (e) {}
    }
  }

  function moveSummaryDrag(event) {
    if (!summaryDrag || !summaryDom) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    var left = summaryDrag.startLeft + (event.clientX - summaryDrag.startX);
    var top = summaryDrag.startTop + (event.clientY - summaryDrag.startY);
    var bounds = mainBounds();
    var size = summarySize();
    if (bounds && size) {
      left = clamp(left, 0, Math.max(0, bounds.width - size.width));
      top = clamp(top, 0, Math.max(0, bounds.height - size.height));
    }
    summaryPosition = { left: left, top: top };
    summaryDom.style = summaryDom.style || {};
    summaryDom.style.left = left + 'px';
    summaryDom.style.top = top + 'px';
    summaryDom.style.right = 'auto';
    summaryDom.style.bottom = 'auto';
  }

  function startSummaryDrag(event) {
    if (!summaryDom || summaryDrag) return;
    if (typeof event.clientX !== 'number' || typeof event.clientY !== 'number') return;
    if (event.button != null && event.button !== 0) return;
    if (summaryDragStartTarget(event.target)) return;
    var doc = global.document;
    if (!doc || typeof doc.addEventListener !== 'function' || typeof doc.removeEventListener !== 'function') return;
    var offset = currentSummaryOffset();
    summaryDrag = {
      startX: event.clientX,
      startY: event.clientY,
      startLeft: offset.left,
      startTop: offset.top,
    };
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    doc.addEventListener('mousemove', moveSummaryDrag, true);
    doc.addEventListener('mouseup', endSummaryDrag, true);
    summaryDragCleanup = function () {
      try { doc.removeEventListener('mousemove', moveSummaryDrag, true); } catch (e) {}
      try { doc.removeEventListener('mouseup', endSummaryDrag, true); } catch (e) {}
    };
  }

  function bindSummaryDrag(element) {
    if (!element || typeof element.addEventListener !== 'function') return;
    element.addEventListener('mousedown', startSummaryDrag, false);
  }

  function removeSummary() {
    endSummaryDrag();
    if (summaryDom && summaryDom.parentNode && typeof summaryDom.parentNode.removeChild === 'function') {
      summaryDom.parentNode.removeChild(summaryDom);
    }
    summaryDom = null;
  }

  function appendSummaryLine(parent, value, className) {
    var line = document.createElement('div');
    line.className = className || 'position-risk-summary-line';
    line.textContent = value;
    parent.appendChild(line);
    return line;
  }

  function appendPositionMetric(parent, label, priceLabel, price, percentage, amount, className) {
    var row = document.createElement('div');
    row.className = 'position-risk-summary-metric ' + className;
    appendSummaryLine(row, label, 'position-risk-summary-metric-label');
    appendSummaryLine(row, priceLabel + ' ' + formatNumber(price, 2), 'position-risk-summary-metric-price');
    appendSummaryLine(row, percentage, 'position-risk-summary-metric-percent');
    appendSummaryLine(row, amount, 'position-risk-summary-metric-amount');
    parent.appendChild(row);
  }

  function appendPositionSubtotal(parent, label, result) {
    var row = document.createElement('div');
    row.className = 'position-risk-summary-subtotal';
    appendSummaryLine(row, label, 'position-risk-summary-subtotal-label');
    appendSummaryLine(row, '盈利 +' + formatMoney(Math.abs(result.profit)), 'position-risk-summary-profit');
    appendSummaryLine(row, '亏损 -' + formatMoney(Math.abs(result.loss)), 'position-risk-summary-loss');
    parent.appendChild(row);
  }

  function renderPositionSummary() {
    summaryTimer = null;
    if (!global.document || typeof document.createElement !== 'function') return null;
    var main = mainChartDom();
    var result = summarizePositions(collectPositionOverlays());
    if (!main || !result.total.count) {
      removeSummary();
      return result;
    }
    if (main.classList && typeof main.classList.add === 'function') main.classList.add('position-risk-main');
    if (!summaryDom || summaryDom.parentNode !== main) {
      removeSummary();
      summaryDom = document.createElement('div');
      summaryDom.id = 'position-risk-summary';
      summaryDom.className = 'position-risk-summary';
      summaryDom.setAttribute('aria-live', 'polite');
      main.appendChild(summaryDom);
      bindSummaryDrag(summaryDom);
    }
    while (summaryDom.firstChild) summaryDom.removeChild(summaryDom.firstChild);
    appendSummaryLine(summaryDom, '仓位汇总', 'position-risk-summary-title');
    result.items.forEach(function (item) {
      var itemDom = document.createElement('div');
      itemDom.className = 'position-risk-summary-item';
      var head = document.createElement('div');
      head.className = 'position-risk-summary-item-head';
      appendSummaryLine(head, item.label, 'position-risk-summary-item-label');
      appendSummaryLine(head, '买入价 ' + formatNumber(item.entry, 2), 'position-risk-summary-entry');
      itemDom.appendChild(head);
      appendPositionMetric(itemDom, '盈利', '目标价', item.target,
        '+' + formatNumber(item.targetPct, 2) + '%', '+' + formatMoney(Math.abs(item.profit)),
        'position-risk-summary-profit');
      appendPositionMetric(itemDom, '亏损', '止损价', item.stop,
        '-' + formatNumber(item.stopPct, 2) + '%', '-' + formatMoney(Math.abs(item.loss)),
        'position-risk-summary-loss');
      summaryDom.appendChild(itemDom);
    });
    if (result.long.count && result.short.count) {
      var subtotals = document.createElement('div');
      subtotals.className = 'position-risk-summary-subtotals';
      appendPositionSubtotal(subtotals, '多头合计', result.long);
      appendPositionSubtotal(subtotals, '空头合计', result.short);
      summaryDom.appendChild(subtotals);
    }
    var totalLine = document.createElement('div');
    totalLine.className = 'position-risk-summary-total';
    var totalProfit = document.createElement('span');
    totalProfit.className = 'position-risk-summary-profit';
    totalProfit.textContent = '总盈利 +' + formatMoney(Math.abs(result.total.profit));
    var totalLoss = document.createElement('span');
    totalLoss.className = 'position-risk-summary-loss';
    totalLoss.textContent = '总亏损 -' + formatMoney(Math.abs(result.total.loss));
    totalLine.appendChild(totalProfit); totalLine.appendChild(totalLoss);
    summaryDom.appendChild(totalLine);
    var footer = document.createElement('div');
    footer.className = 'position-risk-summary-footer';
    appendSummaryLine(footer, '加权平均价格 ' + formatNumber(result.total.weightedAverageCost, 2), 'position-risk-summary-detail');
    appendSummaryLine(footer, '总盈亏比 ' + formatNumber(result.total.ratio, 2) + ':1', 'position-risk-summary-detail');
    summaryDom.appendChild(footer);
    applySummaryPosition();
    return result;
  }

  function scheduleSummary() {
    if (summaryTimer != null || typeof global.setTimeout !== 'function') return;
    summaryTimer = global.setTimeout(renderPositionSummary, 0);
  }

  function zonePolygon(left, right, top, bottom) {
    return [
      { x: left, y: top },
      { x: right, y: top },
      { x: right, y: bottom },
      { x: left, y: bottom },
    ];
  }

  function labelStyles(color, backgroundColor) {
    return {
      color: color,
      backgroundColor: backgroundColor,
      borderColor: 'transparent',
      borderSize: 0,
      borderRadius: 3,
      paddingLeft: 5,
      paddingRight: 5,
      paddingTop: 3,
      paddingBottom: 3,
      size: 12,
      weight: '600',
    };
  }

  function buildPositionFigures(direction, coordinates, points, settings, bounding) {
    if (!coordinates || !coordinates.length) return [];
    settings = Object.assign(defaults(direction), settings || {});
    var numberedLabel = positionLabel(direction, settings);
    var entry = coordinates[0];
    if (!entry) return [];
    var width = bounding && finite(bounding.width) != null ? bounding.width : entry.x + 160;
    var entryLine = {
      type: 'line',
      key: 'entry-level',
      attrs: { coordinates: [{ x: entry.x, y: entry.y }, { x: width, y: entry.y }] },
      styles: { color: '#2563eb', size: 1, style: 'dashed' },
    };
    var entryLabel = {
      type: 'text',
      key: 'entry-text',
      attrs: {
        x: entry.x + 8,
        y: entry.y - 6,
        text: (direction === 'long' ? '买入 · ' : '入场 · ') + numberedLabel,
        align: 'left',
        baseline: 'bottom',
      },
      styles: labelStyles('#ffffff', '#1d4ed8'),
      ignoreEvent: false,
    };
    if (coordinates.length < 2) return [entryLine, entryLabel];

    var target = coordinates[1];
    var horizontalBounds = function (endX) {
      var entryX = clamp(Number(entry.x), 0, width);
      var resolvedEnd = clamp(Number(endX), 0, width);
      if (Math.abs(resolvedEnd - entryX) < 24) {
        resolvedEnd = resolvedEnd < entryX
          ? Math.max(0, entryX - 24)
          : Math.min(width, entryX + 24);
      }
      return { left: Math.min(entryX, resolvedEnd), right: Math.max(entryX, resolvedEnd) };
    };
    var bounds = horizontalBounds(target.x);
    var left = bounds.left;
    var right = bounds.right;
    var result = calculate(direction, points, settings);
    var targetTop = Math.min(entry.y, target.y);
    var targetBottom = Math.max(entry.y, target.y);
    var targetMid = (targetTop + targetBottom) / 2;
    var targetPreviewText = '盈利';
    var targetPreview = [
      {
        key: 'target-preview-zone',
        type: 'polygon',
        attrs: { coordinates: zonePolygon(left, right, targetTop, targetBottom) },
        styles: { style: 'fill', color: 'rgba(239,68,68,.16)', borderColor: '#ef4444', borderSize: 1 },
      },
      {
        key: 'target-level',
        type: 'line',
        attrs: { coordinates: [{ x: left, y: target.y }, { x: right, y: target.y }] },
        styles: { color: '#ef4444', size: 1, style: 'solid' },
      },
      {
        key: 'target-text',
        type: 'text',
        attrs: { x: left + 8, y: targetMid, text: targetPreviewText, align: 'left', baseline: 'middle' },
        styles: labelStyles('#ffffff', '#dc2626'),
        ignoreEvent: true,
      },
    ];
    if (coordinates.length < 3) return [entryLine, entryLabel].concat(targetPreview);

    var stop = coordinates[2];
    bounds = horizontalBounds(finite(stop.x) == null ? target.x : stop.x);
    left = bounds.left;
    right = bounds.right;
    var valid = !!(result && result.ok);
    targetTop = Math.min(entry.y, target.y);
    targetBottom = Math.max(entry.y, target.y);
    var stopTop = Math.min(entry.y, stop.y);
    var stopBottom = Math.max(entry.y, stop.y);
    var centerX = left + 8;
    targetMid = (targetTop + targetBottom) / 2;
    var stopMid = (stopTop + stopBottom) / 2;
    var qtyText = valid && result.qty != null
      ? ' · ' + (direction === 'long' ? '买入 ' : '卖空 ') + formatNumber(result.qty, settings.qtyPrecision || 0) + ' 份'
      : '';
    var targetText = valid
      ? '盈利 ' + formatNumber(result.target, 2) + ' · +' + formatNumber(result.targetPct, 2) + '% · ' + formatMoney(result.profitPnl)
      : '价格层级无效，双击设置';
    var stopText = valid
      ? '亏损 ' + formatNumber(result.stop, 2) + ' · -' + formatNumber(result.stopPct, 2) + '% · ' + formatMoney(result.lossPnl)
      : '目标/入场/止损顺序不正确';
    var ratio = valid ? (result.profitLossRatio == null ? result.riskReward : result.profitLossRatio) : null;
    var investmentText = valid && settings.investmentAmount
      ? ' · 投入 ' + formatMoney(settings.investmentAmount)
      : '';
    var entryText = valid
      ? numberedLabel + ' · 盈亏比 ' + formatNumber(ratio, 2) + ':1' + investmentText + qtyText
      : numberedLabel + '仓位';

    return [
      {
        key: 'target-zone',
        type: 'polygon',
        attrs: { coordinates: zonePolygon(left, right, targetTop, targetBottom) },
        styles: { style: 'fill', color: valid ? 'rgba(239,68,68,.20)' : 'rgba(148,163,184,.14)' },
      },
      {
        key: 'stop-zone',
        type: 'polygon',
        attrs: { coordinates: zonePolygon(left, right, stopTop, stopBottom) },
        styles: { style: 'fill', color: valid ? 'rgba(16,185,129,.18)' : 'rgba(148,163,184,.14)' },
      },
      {
        key: 'levels',
        type: 'line',
        attrs: [
          { coordinates: [{ x: left, y: target.y }, { x: right, y: target.y }] },
          { coordinates: [{ x: left, y: entry.y }, { x: right, y: entry.y }] },
          { coordinates: [{ x: left, y: stop.y }, { x: right, y: stop.y }] },
        ],
        styles: { color: '#64748b', size: 1, style: 'solid' },
      },
      {
        key: 'target-text',
        type: 'text',
        attrs: { x: centerX, y: targetMid, text: targetText, align: 'left', baseline: 'middle' },
        styles: valid
          ? labelStyles('#ffffff', '#dc2626')
          : labelStyles('#ffffff', '#64748b'),
        ignoreEvent: true,
      },
      {
        key: 'entry-text',
        type: 'text',
        attrs: { x: centerX, y: entry.y - 6, text: entryText, align: 'left', baseline: 'bottom' },
        styles: labelStyles('#ffffff', '#1d4ed8'),
        ignoreEvent: false,
      },
      {
        key: 'stop-text',
        type: 'text',
        attrs: { x: centerX, y: stopMid, text: stopText, align: 'left', baseline: 'middle' },
        styles: valid
          ? labelStyles('#ffffff', '#059669')
          : labelStyles('#ffffff', '#64748b'),
        ignoreEvent: true,
      },
    ];
  }

  function calculatePriceRange(points, settings) {
    if (!points || points.length < 2) return null;
    var input = {
      startPrice: finite(points[0].value),
      endPrice: finite(points[1].value),
      tickSize: settings && settings.tickSize,
      pricePrecision: settings && settings.pricePrecision,
    };
    var calculator = rangeModel();
    if (calculator && typeof calculator.calculatePriceRange === 'function') {
      return calculator.calculatePriceRange(input);
    }
    if (!(input.startPrice > 0) || !(input.endPrice > 0)) return { ok: false };
    var difference = input.endPrice - input.startPrice;
    return {
      ok: true,
      startPrice: input.startPrice,
      endPrice: input.endPrice,
      difference: difference,
      absoluteDifference: Math.abs(difference),
      percent: difference / input.startPrice * 100,
      direction: difference > 0 ? 'up' : difference < 0 ? 'down' : 'flat',
    };
  }

  function readPriceRangeSettings(overlay) {
    var raw = overlay && overlay.extendData;
    if (typeof raw === 'function') {
      try { raw = raw(overlay); } catch (e) { raw = null; }
    }
    return Object.assign({ pricePrecision: 2, period: currentPeriodValue() }, raw && typeof raw === 'object' ? raw : {});
  }

  function buildPriceRangeFigures(coordinates, points, settings, bounding) {
    if (!coordinates || !coordinates.length) return [];
    var start = coordinates[0];
    if (coordinates.length < 2) {
      return [{
        type: 'line',
        attrs: { coordinates: [start, { x: Math.min(start.x + 100, (bounding && bounding.width) || start.x + 100), y: start.y }] },
        styles: { color: '#64748b', size: 1, style: 'dashed' },
      }];
    }
    var end = coordinates[1];
    var left = Math.min(start.x, end.x);
    var right = Math.max(start.x, end.x);
    if (right - left < 24) right = left + 24;
    var top = Math.min(start.y, end.y);
    var bottom = Math.max(start.y, end.y);
    if (bottom - top < 2) bottom = top + 2;
    var result = calculatePriceRange(points, settings || {});
    var valid = !!(result && result.ok);
    var direction = valid ? result.direction : 'flat';
    var precision = finite(settings && settings.pricePrecision);
    precision = precision == null ? 2 : clamp(Math.floor(precision), 0, 20);
    var stroke = direction === 'up' ? '#ef5350' : direction === 'down' ? '#26a69a' : '#64748b';
    var fill = direction === 'up'
      ? 'rgba(239,83,80,.14)'
      : direction === 'down'
        ? 'rgba(38,166,154,.14)'
        : 'rgba(100,116,139,.12)';
    var sign = valid && result.difference > 0 ? '+' : '';
    var text = valid
      ? sign + formatNumber(result.difference, precision) + '  (' + sign + formatNumber(result.percent, 2) + '%)'
        + ' · 起 ' + formatNumber(result.startPrice, precision) + '  终 ' + formatNumber(result.endPrice, precision)
      : '价格区间无效';
    var timingText = priceRangeTimingText(points, settings && settings.period || currentPeriodValue());
    var centerY = (top + bottom) / 2;
    var figures = [
      {
        key: 'price-range-zone',
        type: 'polygon',
        attrs: { coordinates: zonePolygon(left, right, top, bottom) },
        styles: { style: 'stroke_fill', color: fill, borderColor: stroke, borderSize: 1 },
      },
      {
        key: 'price-range-outline',
        type: 'line',
        attrs: { coordinates: [
          { x: left, y: top }, { x: right, y: top },
          { x: right, y: bottom }, { x: left, y: bottom },
          { x: left, y: top },
        ] },
        styles: { color: stroke, size: 1, style: 'solid' },
      },
      {
        key: 'price-range-text',
        type: 'text',
        attrs: { x: (left + right) / 2, y: centerY - (timingText ? 11 : 0), text: text, align: 'center', baseline: 'middle' },
        styles: labelStyles('#ffffff', valid ? stroke : '#64748b'),
        ignoreEvent: true,
      },
    ];
    if (timingText) {
      figures.push({
        key: 'price-range-period-text',
        type: 'text',
        attrs: { x: (left + right) / 2, y: centerY + 12, text: timingText, align: 'center', baseline: 'middle' },
        styles: Object.assign(labelStyles('#ffffff', valid ? stroke : '#64748b'), { size: 11, weight: '500' }),
        ignoreEvent: true,
      });
    }
    return figures;
  }

  function copyHorizontalPoint(target, source) {
    if (!target || !source) return;
    if (source.timestamp != null) target.timestamp = source.timestamp;
    if (source.dataIndex != null) target.dataIndex = source.dataIndex;
  }

  function openEntrySettings(event) {
    var key = event && event.figureKey;
    if (!key && event && event.figure) key = event.figure.key;
    if (!key && event) key = event.key;
    if (key !== 'entry-text') return false;
    var id = overlayIdOf(event && event.overlay);
    if (id) openSettings(id);
    return !!id;
  }

  function overlayIdOf(overlay) {
    if (!overlay) return null;
    return overlay.id || (typeof overlay.getId === 'function' ? overlay.getId() : null);
  }

  function templateFor(direction) {
    var name = direction === 'short' ? SHORT_NAME : LONG_NAME;
    return {
      name: name,
      pointRoles: ['entry', 'target', 'stop'],
      totalStep: 4,
      needDefaultPointFigure: true,
      needDefaultXAxisFigure: true,
      needDefaultYAxisFigure: true,
      mode: 'weak_magnet',
      modeSensitivity: 8,
      createPointFigures: function (params) {
        var overlay = params.overlay || {};
        var settings = readExtendData(overlay, direction);
        var actualDirection = directionFor(overlay.name || name, settings);
        settings.positionNumber = positionNumberForOverlay(overlay, actualDirection, settings);
        var figures = buildPositionFigures(
          actualDirection,
          params.coordinates || [],
          overlayPoints(overlay),
          settings,
          params.bounding || {}
        );
        scheduleSummary();
        return figures;
      },
      performEventMoveForDrawing: function (params) {
        var points = params.points || [];
        var moving = params.performPoint || {};
        if (params.currentStep === 2 && points[1]) {
          copyHorizontalPoint(points[1], moving);
        }
        if (params.currentStep === 3 && points[2]) {
          copyHorizontalPoint(points[2], points[1] || moving);
        }
      },
      performEventPressedMove: function (params) {
        var points = params.points || [];
        var moving = params.performPoint || {};
        if (params.performPointIndex === 1 && points[2]) copyHorizontalPoint(points[2], moving);
        if (params.performPointIndex === 2 && points[1]) copyHorizontalPoint(points[1], moving);
        scheduleSummary();
      },
      onDoubleClick: function (event) {
        var id = overlayIdOf(event && event.overlay);
        if (id) openSettings(id);
        return true;
      },
      onRightClick: function (event) {
        return deleteOverlayFromEvent(event);
      },
      onClick: function (event) {
        return openEntrySettings(event);
      },
      onSelected: function (event) {
        return openEntrySettings(event);
      },
    };
  }

  function priceRangeTemplate() {
    return {
      name: PRICE_RANGE_NAME,
      totalStep: 3,
      needDefaultPointFigure: true,
      needDefaultXAxisFigure: true,
      needDefaultYAxisFigure: true,
      mode: 'weak_magnet',
      modeSensitivity: 8,
      createPointFigures: function (params) {
        var overlay = params.overlay || {};
        return buildPriceRangeFigures(
          params.coordinates || [],
          overlayPoints(overlay),
          readPriceRangeSettings(overlay),
          params.bounding || {}
        );
      },
      onRightClick: function (event) {
        return deleteOverlayFromEvent(event);
      },
    };
  }

  function registerTemplates() {
    if (registered) return true;
    if (!global.klinecharts || typeof global.klinecharts.registerOverlay !== 'function') return false;
    global.klinecharts.registerOverlay(templateFor('long'));
    global.klinecharts.registerOverlay(templateFor('short'));
    global.klinecharts.registerOverlay(priceRangeTemplate());
    registered = true;
    return true;
  }

  function activeChart() {
    return chart || global.__kline_chart || null;
  }

  function createPosition(direction, points) {
    var current = activeChart();
    if (!current || typeof current.createOverlay !== 'function') return null;
    registerTemplates();
    var name = direction === 'short' ? SHORT_NAME : LONG_NAME;
    var config = {
      name: name,
      groupId: GROUP_ID,
      extendData: Object.assign(defaults(direction), { positionNumber: nextPositionNumber(direction, current) }),
      mode: 'weak_magnet',
      modeSensitivity: 8,
    };
    if (Array.isArray(points) && points.length >= 3) {
      config.points = points.map(function (point) { return Object.assign({}, point); });
    }
    lastCreatedId = current.createOverlay(config);
    scheduleSummary();
    return lastCreatedId;
  }

  function createPriceRange(points) {
    var current = activeChart();
    if (!current || typeof current.createOverlay !== 'function') return null;
    registerTemplates();
    var config = {
      name: PRICE_RANGE_NAME,
      groupId: GROUP_ID,
      extendData: { pricePrecision: 2 },
      mode: 'weak_magnet',
      modeSensitivity: 8,
    };
    if (Array.isArray(points) && points.length >= 2) {
      config.points = points.map(function (point) { return Object.assign({}, point); });
    }
    lastCreatedId = current.createOverlay(config);
    return lastCreatedId;
  }

  function overlayById(id) {
    var current = activeChart();
    if (!current || !id || typeof current.getOverlayById !== 'function') return null;
    try { return current.getOverlayById(id); } catch (e) { return null; }
  }

  function ensureModal() {
    if (modal && modal.isConnected) return modal;
    modal = document.createElement('div');
    modal.id = 'position-risk-modal';
    modal.hidden = true;
    modal.innerHTML = [
      '<div class="position-risk-dialog" role="dialog" aria-modal="true" aria-labelledby="position-risk-title">',
      '<div class="position-risk-head"><strong id="position-risk-title">仓位测算设置</strong><button type="button" data-position-close title="关闭">×</button></div>',
      '<div class="position-risk-grid">',
      '<label>入场价格<input name="entry" type="number" min="0" step="any"></label>',
      '<label>目标价格<input name="target" type="number" min="0" step="any"></label>',
      '<label>止损价格<input name="stop" type="number" min="0" step="any"></label>',
      '<label class="position-risk-investment">投入金额<input name="investmentAmount" type="number" min="0" step="any" placeholder="留空则按风险反推数量"><span class="position-risk-amount-presets"><button type="button" data-position-amount="10000">1万</button><button type="button" data-position-amount="100000">10万</button><button type="button" data-position-amount="1000000">100万</button></span></label>',
      '<label>账户规模<input name="accountSize" type="number" min="0" step="any"></label>',
      '<label>风险方式<select name="riskMode"><option value="percent">账户百分比</option><option value="amount">固定金额</option></select></label>',
      '<label>单笔风险<input name="risk" type="number" min="0" step="any"></label>',
      '<label>每手数量<input name="lotSize" type="number" min="0" step="any"></label>',
      '<label>杠杆倍数<input name="leverage" type="number" min="0" step="any"></label>',
      '<label>点值<input name="pointValue" type="number" min="0" step="any"></label>',
      '<label>数量精度<input name="qtyPrecision" type="number" min="0" max="8" step="1"></label>',
      '</div>',
      '<div class="position-risk-preview" data-position-preview></div>',
      '<div class="position-risk-error" data-position-error role="status"></div>',
      '<div class="position-risk-actions">',
      '<button type="button" data-position-delete class="danger">删除</button>',
      '<button type="button" data-position-reverse>反转方向</button>',
      '<span></span>',
      '<button type="button" data-position-cancel>取消</button>',
      '<button type="button" data-position-save class="primary">应用</button>',
      '</div></div>',
    ].join('');
    document.body.appendChild(modal);
    modal.addEventListener('click', function (event) {
      if (event.target === modal || event.target.closest('[data-position-close]') || event.target.closest('[data-position-cancel]')) {
        closeSettings();
      }
    });
    modal.querySelector('[data-position-save]').addEventListener('click', saveSettings);
    modal.querySelector('[data-position-delete]').addEventListener('click', deletePosition);
    modal.querySelector('[data-position-reverse]').addEventListener('click', reversePosition);
    modal.querySelectorAll('[data-position-amount]').forEach(function (button) {
      button.addEventListener('click', function () {
        var field = modal.querySelector('[name="investmentAmount"]');
        if (field) field.value = button.getAttribute('data-position-amount') || '';
        updatePreview();
      });
    });
    modal.querySelectorAll('input,select').forEach(function (field) {
      field.addEventListener('input', updatePreview);
      field.addEventListener('change', updatePreview);
    });
    return modal;
  }

  function fieldValue(name) {
    var field = modal && modal.querySelector('[name="' + name + '"]');
    return field ? field.value : '';
  }

  function settingsFromDialog(direction) {
    return {
      direction: direction,
      entry: finite(fieldValue('entry')),
      target: finite(fieldValue('target')),
      stop: finite(fieldValue('stop')),
      investmentAmount: fieldValue('investmentAmount') === '' ? null : finite(fieldValue('investmentAmount')),
      accountSize: finite(fieldValue('accountSize')),
      riskMode: fieldValue('riskMode') === 'amount' ? 'amount' : 'percent',
      risk: finite(fieldValue('risk')),
      lotSize: finite(fieldValue('lotSize')),
      leverage: finite(fieldValue('leverage')),
      pointValue: finite(fieldValue('pointValue')),
      qtyPrecision: Math.floor(finite(fieldValue('qtyPrecision')) || 0),
      alwaysShowStats: true,
    };
  }

  function editingContext() {
    var overlay = overlayById(editingId);
    if (!overlay) return null;
    var name = overlay.name || (typeof overlay.getName === 'function' ? overlay.getName() : '');
    var data = readExtendData(overlay, name === SHORT_NAME ? 'short' : 'long');
    var direction = directionFor(name, data);
    return { overlay: overlay, points: overlayPoints(overlay), settings: data, direction: direction };
  }

  function updatePreview() {
    var context = editingContext();
    if (!context || !modal) return;
    var input = settingsFromDialog(context.direction);
    var result = model() && typeof model().calculatePosition === 'function'
      ? model().calculatePosition(input)
      : calculate(context.direction, [
        { value: input.entry }, { value: input.target }, { value: input.stop },
      ], input);
    var preview = modal.querySelector('[data-position-preview]');
    var error = modal.querySelector('[data-position-error]');
    if (!result || !result.ok) {
      preview.textContent = '';
      error.textContent = result && result.error && result.error.message
        ? result.error.message
        : '请检查目标、入场和止损价格的顺序。';
      return;
    }
    error.textContent = '';
    preview.textContent = [
      '盈亏比 ' + formatNumber(result.profitLossRatio == null ? result.riskReward : result.profitLossRatio, 2) + ':1',
      input.investmentAmount ? '投入 ' + formatMoney(input.investmentAmount) : '按风险反推数量',
      (context.direction === 'long' ? '买入份数 ' : '卖空份数 ') + formatNumber(result.qty, input.qtyPrecision),
      '盈利 ' + formatNumber(result.target, 2) + ' / +' + formatNumber(result.targetPct, 2) + '% / ' + formatMoney(result.profitPnl),
      '亏损 ' + formatNumber(result.stop, 2) + ' / -' + formatNumber(result.stopPct, 2) + '% / ' + formatMoney(result.lossPnl),
    ].join(' · ');
  }

  function openSettings(id) {
    editingId = id;
    var context = editingContext();
    if (!context || context.points.length < 3) return false;
    ensureModal();
    var values = Object.assign({}, context.settings, {
      entry: context.points[0].value,
      target: context.points[1].value,
      stop: context.points[2].value,
    });
    modal.querySelectorAll('input').forEach(function (field) { field.value = ''; });
    Object.keys(values).forEach(function (key) {
      var field = modal.querySelector('[name="' + key + '"]');
      if (field && values[key] != null) field.value = values[key];
    });
    modal.querySelector('#position-risk-title').textContent = positionLabel(context.direction, context.settings) + '仓位测算设置';
    modal.hidden = false;
    updatePreview();
    return true;
  }

  function closeSettings() {
    if (modal) modal.hidden = true;
    editingId = null;
  }

  function saveSettings() {
    var context = editingContext();
    var current = activeChart();
    if (!context || !current || typeof current.overrideOverlay !== 'function') return;
    var values = settingsFromDialog(context.direction);
    var result = model() && typeof model().calculatePosition === 'function'
      ? model().calculatePosition(values)
      : calculate(context.direction, [
        { value: values.entry }, { value: values.target }, { value: values.stop },
      ], values);
    if (!result || !result.ok) {
      updatePreview();
      return;
    }
    var points = context.points.map(function (point) { return Object.assign({}, point); });
    points[0].value = values.entry;
    points[1].value = values.target;
    points[2].value = values.stop;
    var extendData = Object.assign({}, context.settings, values);
    delete extendData.entry;
    delete extendData.target;
    delete extendData.stop;
    current.overrideOverlay({ id: editingId, points: points, extendData: extendData });
    scheduleSummary();
    closeSettings();
  }

  function reversePosition() {
    var context = editingContext();
    var current = activeChart();
    if (!context || !current || typeof current.overrideOverlay !== 'function') return;
    var nextDirection = context.direction === 'long' ? 'short' : 'long';
    var values = settingsFromDialog(context.direction);
    var reversed;
    if (model() && typeof model().reversePosition === 'function') {
      reversed = model().reversePosition(values);
    } else {
      reversed = Object.assign({}, values, {
        direction: nextDirection,
        target: values.stop,
        stop: values.target,
      });
    }
    if (reversed && reversed.ok === false) return;
    reversed = reversed && reversed.value ? reversed.value : reversed;
    var points = context.points.map(function (point) { return Object.assign({}, point); });
    points[0].value = reversed.entry;
    points[1].value = reversed.target;
    points[2].value = reversed.stop;
    var extendData = Object.assign({}, context.settings, reversed, { direction: nextDirection });
    delete extendData.entry;
    delete extendData.target;
    delete extendData.stop;
    current.overrideOverlay({
      id: editingId,
      name: nextDirection === 'short' ? SHORT_NAME : LONG_NAME,
      points: points,
      extendData: extendData,
    });
    scheduleSummary();
    closeSettings();
  }

  function deletePosition() {
    var current = activeChart();
    if (current && editingId && typeof current.removeOverlay === 'function') {
      current.removeOverlay({ id: editingId });
    }
    scheduleSummary();
    closeSettings();
  }

  function removeOverlayById(id) {
    var current = activeChart();
    if (!current || !id || typeof current.removeOverlay !== 'function') return false;
    try {
      current.removeOverlay({ id: id });
    } catch (e) {
      return false;
    }
    scheduleSummary();
    return true;
  }

  function deleteOverlayFromEvent(event) {
    var id = overlayIdOf(event && event.overlay);
    if (!id) return false;
    if (editingId === id) closeSettings();
    return removeOverlayById(id);
  }

  function appendToolIcon(button, tone) {
    var icon = document.createElement('span');
    icon.className = 'position-risk-tool-icon position-risk-tool-icon-' + tone;
    icon.setAttribute('aria-hidden', 'true');
    ['top', 'middle', 'bottom'].forEach(function (part) {
      var rail = document.createElement('span');
      rail.className = 'position-risk-tool-icon-rail position-risk-tool-icon-rail-' + part;
      icon.appendChild(rail);
    });
    var stem = document.createElement('span');
    stem.className = 'position-risk-tool-icon-stem';
    icon.appendChild(stem);
    var start = document.createElement('span');
    start.className = 'position-risk-tool-icon-dot position-risk-tool-icon-dot-start';
    icon.appendChild(start);
    var end = document.createElement('span');
    end.className = 'position-risk-tool-icon-dot position-risk-tool-icon-dot-end';
    icon.appendChild(end);
    button.appendChild(icon);
  }

  function ensureToolButtons() {
    if (toolButtons.length === 3 && toolButtons.every(function (button) { return button.isConnected; })) return true;
    var bar = document.querySelector('.klinecharts-pro-drawing-bar');
    if (!bar) return false;
    toolButtons = [];
    var definitions = [
      { id: 'position-long-tool', label: '多头仓位', action: function () { createPosition('long'); }, tone: 'long' },
      { id: 'position-short-tool', label: '空头仓位', action: function () { createPosition('short'); }, tone: 'short' },
      { id: 'position-price-range-tool', label: '价格测量', action: createPriceRange, tone: 'range' },
    ];
    var firstSplit = bar.querySelector('.split-line');
    definitions.forEach(function (definition) {
      var button = document.createElement('div');
      button.id = definition.id;
      button.className = 'item board-position-tool-item ' + definition.tone;
      button.tabIndex = 0;
      button.title = definition.label;
      button.setAttribute('role', 'button');
      button.setAttribute('aria-label', definition.label);
      appendToolIcon(button, definition.tone);
      button.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        definition.action();
      });
      button.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        definition.action();
      });
      bar.insertBefore(button, firstSplit || null);
      toolButtons.push(button);
    });
    return true;
  }

  function bindSummaryActions(current) {
    chartUnbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    chartUnbinds = [];
    if (!current || typeof current.subscribeAction !== 'function') return;
    var actionTypes = global.klinecharts && global.klinecharts.ActionType;
    ['OnOverlayDraw', 'OnOverlayModify', 'OnOverlayRemove', 'OnPaneDrag', 'OnZoom', 'OnScroll'].forEach(function (key) {
      var action = actionTypes && actionTypes[key] ? actionTypes[key] : key;
      var handler = function () { scheduleSummary(); };
      try {
        current.subscribeAction(action, handler);
        chartUnbinds.push(function () {
          try { if (typeof current.unsubscribeAction === 'function') current.unsubscribeAction(action, handler); } catch (e) {}
        });
      } catch (e) {}
    });
  }

  function onChartReady(nextChart) {
    if (nextChart && typeof nextChart.createOverlay === 'function' && chart !== nextChart) {
      removeSummary();
      chart = nextChart;
      bindSummaryActions(chart);
    }
    registerTemplates();
    if (!ensureToolButtons()) setTimeout(ensureToolButtons, 100);
    scheduleSummary();
  }

  global.addEventListener('kline-chart-ready', function (event) {
    onChartReady(event && event.detail);
  });
  global.addEventListener('kline-loaded', scheduleSummary);
  global.addEventListener('bar-replay-cursor', scheduleSummary);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { onChartReady(global.__kline_chart); });
  } else {
    setTimeout(function () { onChartReady(global.__kline_chart); }, 0);
  }

  global.PositionRiskTool = {
    onChartReady: onChartReady,
    createPosition: createPosition,
    createPriceRange: createPriceRange,
    openSettings: openSettings,
    closeSettings: closeSettings,
    registerTemplates: registerTemplates,
    buildPositionFigures: buildPositionFigures,
    buildPriceRangeFigures: buildPriceRangeFigures,
    collectAllOverlays: collectAllOverlays,
    collectPositionOverlays: collectPositionOverlays,
    summarizePositions: summarizePositions,
    renderSummary: renderPositionSummary,
    templateFor: templateFor,
    priceRangeTemplate: priceRangeTemplate,
    defaults: defaults,
    names: { long: LONG_NAME, short: SHORT_NAME, priceRange: PRICE_RANGE_NAME },
    getChart: activeChart,
    getLastCreatedId: function () { return lastCreatedId; },
    ensureToolButtons: ensureToolButtons,
    deleteOverlayFromEvent: deleteOverlayFromEvent,
  };
})(typeof window !== 'undefined' ? window : globalThis);
