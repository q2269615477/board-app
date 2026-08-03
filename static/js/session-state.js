(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.SessionState = exported;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  /**
   * Pure projections used by the session panel.
   *
   * This module deliberately has no knowledge of DOM, charts, network calls,
   * timers, or the mutable session object owned by session-ui.js.  Inputs are
   * supplied by the caller and every exported function returns a new value.
   */

  function normalizePeriod(period) {
    if (!period || typeof period !== 'object') return period || 'daily';
    var timespan = period.timespan;
    var multiplier = period.multiplier;
    if (timespan === 'minute') return multiplier + 'm';
    if (timespan === 'hour') return multiplier * 60 + 'm';
    if (timespan === 'day') return 'daily';
    if (timespan === 'week') return 'weekly';
    if (timespan === 'month') {
      return multiplier === 3 ? 'quarterly' : multiplier === 12 ? 'yearly' : 'monthly';
    }
    if (timespan === 'year') return 'yearly';
    return 'daily';
  }

  function projectPanelContext(boardContext, selected) {
    var ctx = boardContext || {};
    var selectedSymbol = selected || {};
    var code = ctx.symbol || ctx.code || selectedSymbol.code || 'sh000001';
    var name = ctx.name || selectedSymbol.name || '';
    var assetType = ctx.type || selectedSymbol.type || 'index';
    return {
      symbol: code,
      symbol_name: name,
      asset_type: assetType,
      // __board_ctx is the source of truth for the active Pro period.  Do not
      // fall back to store.selected.period, which may describe a stale row.
      period: normalizePeriod(ctx.period || 'daily'),
    };
  }

  function normalizeOverlayInstance(overlay) {
    if (!overlay) return null;
    // Temporary highlight overlays are never persisted as session elements.
    try {
      var rawId = overlay.id || (typeof overlay.getId === 'function' ? overlay.getId() : '');
      if (rawId && String(rawId).indexOf('sess_hl') === 0) return null;
    } catch (e) {}

    var points = [];
    try {
      if (typeof overlay.getPoints === 'function') points = overlay.getPoints() || [];
      else if (Array.isArray(overlay.points)) points = overlay.points;
      else if (overlay._points) points = overlay._points;
    } catch (e) {
      points = [];
    }
    var normalizedPoints = (points || []).map(function (point) {
      point = point || {};
      return {
        timestamp: point.timestamp != null
          ? point.timestamp
          : point.time != null ? point.time : point.dataIndex,
        value: point.value != null ? point.value : point.price != null ? point.price : point.y,
      };
    });
    var type = overlay.name ||
      (typeof overlay.getName === 'function' ? overlay.getName() : null) ||
      overlay.totalOverlayName || overlay.type || 'overlay';
    var id = overlay.id || (typeof overlay.getId === 'function' ? overlay.getId() : null);
    // A stable content hash prevents repeated flushes from creating duplicate
    // elements when the chart implementation has not assigned an id yet.
    if (!id) {
      var key = String(type) + '|' + normalizedPoints.map(function (point) {
        return String(point.timestamp != null ? point.timestamp : '') + ':' +
          String(point.value != null ? point.value : '');
      }).join(';');
      var hash = 0;
      for (var i = 0; i < key.length; i += 1) {
        hash = (Math.imul(31, hash) + key.charCodeAt(i)) | 0;
      }
      id = 'ovh_' + (hash >>> 0).toString(16);
    }
    var normalized = {
      id: String(id),
      type: String(type),
      points: normalizedPoints,
      styles: overlay.styles || {},
    };
    if (overlay.extendData !== undefined) normalized.extendData = overlay.extendData;
    return normalized;
  }

  function snapPriceElement(bar, price) {
    if (!bar || price == null || !Number.isFinite(Number(price))) {
      return { price_element: null, price: price };
    }
    var candidates = [
      ['open', bar.open],
      ['high', bar.high],
      ['low', bar.low],
      ['close', bar.close],
    ].filter(function (candidate) {
      return candidate[1] != null && Number.isFinite(Number(candidate[1]));
    });
    if (!candidates.length) return { price_element: 'custom', price: price };
    var best = candidates[0];
    var bestDistance = Math.abs(Number(best[1]) - Number(price));
    candidates.forEach(function (candidate) {
      var distance = Math.abs(Number(candidate[1]) - Number(price));
      if (distance < bestDistance) {
        bestDistance = distance;
        best = candidate;
      }
    });
    var scale = Math.max(Math.abs(Number(best[1])), 1e-6);
    if (bestDistance / scale > 0.002 && bestDistance > 0.01) {
      return { price_element: 'custom', price: price };
    }
    return { price_element: best[0], price: best[1] };
  }

  function projectBarToKbar(bar, price, context, chartId) {
    var row = bar || {};
    var ctx = context || {};
    var snapped = snapPriceElement(row, price != null ? price : row.close);
    var timestamp = row.timestamp;
    var date = row.date ||
      (timestamp ? new Date(timestamp < 1e12 ? timestamp * 1000 : timestamp).toISOString().slice(0, 10) : '');
    var volume = row.volume != null
      ? row.volume
      : row.vol != null ? row.vol : row.turnover != null ? row.turnover : null;
    var amount = row.amount != null
      ? row.amount
      : row.turnover != null && row.volume == null ? null : row.amount;
    return {
      timestamp: timestamp,
      date: date,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: volume,
      amount: amount != null ? amount : row.amount,
      price_element: snapped.price_element,
      price: snapped.price,
      symbol: ctx.symbol,
      period: ctx.period,
      chart_id: chartId,
    };
  }

  return {
    normalizePeriod: normalizePeriod,
    projectPanelContext: projectPanelContext,
    normalizeOverlayInstance: normalizeOverlayInstance,
    snapPriceElement: snapPriceElement,
    projectBarToKbar: projectBarToKbar,
  };
}));
