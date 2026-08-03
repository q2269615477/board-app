(function (global) {
  'use strict';

  /*
   * The candle series belongs to KLineCharts. This module deliberately draws
   * only comparison close-return paths in its own SVG layer. The main symbol
   * is never rendered a second time here.
   */
  var doc = global.document;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var comparisonModel = global.ChartComparisonModel;
  if (!comparisonModel) throw new Error('ChartComparisonModel must load before ChartComparisonController');
  var MAX_LOADING_RETRIES = 5;
  var MAX_OVERLAYS = 12;
  var SCALE_INDICATOR_NAME = 'BOARD_COMPARISON_SCALE';
  var finiteNumber = comparisonModel.finiteNumber;
  var stablePercentage = comparisonModel.stablePercentage;
  var toMillis = comparisonModel.toMillis;
  var periodToApi = comparisonModel.periodToApi;
  var periodBucket = comparisonModel.periodBucket;
  var validRows = comparisonModel.validRows;
  var alignSeries = comparisonModel.alignSeries;
  var computeReturns = comparisonModel.computeReturns;
  var normalizeSeries = comparisonModel.normalizeSeries;
  var computeComparison = comparisonModel.computeComparison;
  var rangeOhlc = comparisonModel.rangeOhlc;
  var exactPeriodRow = comparisonModel.exactPeriodRow;
  var commonRangeRows = comparisonModel.commonRangeRows;
  var computePercentScale = comparisonModel.computePercentScale;
  var mapPercentToY = comparisonModel.mapPercentToY;
  var returnPctToEquivalentPrice = comparisonModel.returnPctToEquivalentPrice;
  var computeEquivalentPriceExtent = comparisonModel.computeEquivalentPriceExtent;
  var computePriceScale = comparisonModel.computePriceScale;
  var mapPriceToY = comparisonModel.mapPriceToY;
  var countRangeBars = comparisonModel.countRangeBars;
  var countRangeTradingDays = comparisonModel.countRangeTradingDays;
  var naturalDayCount = comparisonModel.naturalDayCount;
  var clampEndpoint = comparisonModel.clampEndpoint;
  var symbolIdentity = comparisonModel.symbolIdentity;
  var pathFor = comparisonModel.pathFor;
  var formatPct = comparisonModel.formatPct;
  var formatPoints = comparisonModel.formatPoints;
  var formatPointMagnitude = comparisonModel.formatPointMagnitude;
  var rangeMovementStyle = comparisonModel.rangeMovementStyle;
  var formatDate = comparisonModel.formatDate;
  var pixelPoint = comparisonModel.pixelPoint;

  function computeRangeComparison(mainRows, overlayRows, period, startIndex, endIndex) {
    if (!Array.isArray(mainRows) || !Array.isArray(overlayRows) || !mainRows.length || !overlayRows.length) return null;
    var start = Number(startIndex);
    var end = Number(endIndex);
    if (!isFinite(start) || !isFinite(end)) return null;
    start = Math.round(start);
    end = Math.round(end);
    if (start > end) { var swap = start; start = end; end = swap; }
    if (start < 0 || end < 0 || start >= mainRows.length || end >= mainRows.length) return null;

    var apiPeriod = periodToApi(period);
    var common = commonRangeRows(mainRows, overlayRows, apiPeriod, start, end);
    if (!common.length) return null;
    var first = common[0];
    var last = common[common.length - 1];
    var mainStart = first.main;
    var mainEnd = last.main;
    var overlayStart = first.overlay;
    var overlayEnd = last.overlay;
    if (overlayStart.close === 0 || mainStart.close === 0) return null;

    var mainReturnPct = stablePercentage((mainEnd.close / mainStart.close - 1) * 100);
    var overlayReturnPct = stablePercentage((overlayEnd.close / overlayStart.close - 1) * 100);
    if (!isFinite(mainReturnPct) || !isFinite(overlayReturnPct)) return null;
    return {
      period: apiPeriod,
      startIndex: first.mainIndex,
      endIndex: last.mainIndex,
      startTimestamp: mainStart.timestamp,
      endTimestamp: mainEnd.timestamp,
      mainStart: mainStart,
      mainEnd: mainEnd,
      overlayStart: overlayStart,
      overlayEnd: overlayEnd,
      mainReturnPct: mainReturnPct,
      overlayReturnPct: overlayReturnPct,
      differencePct: stablePercentage(mainReturnPct - overlayReturnPct)
    };
  }

  function computeMainRange(mainRows, startIndex, endIndex) {
    if (!Array.isArray(mainRows) || !mainRows.length) return null;
    var start = Math.round(Number(startIndex));
    var end = Math.round(Number(endIndex));
    if (!isFinite(start) || !isFinite(end)) return null;
    if (start > end) { var swap = start; start = end; end = swap; }
    if (start < 0 || end < 0 || start >= mainRows.length || end >= mainRows.length) return null;
    var first = rangeOhlc(mainRows[start]);
    var last = rangeOhlc(mainRows[end]);
    if (!first || !last || first.close === 0) return null;
    return {
      startIndex: start,
      endIndex: end,
      startTimestamp: first.timestamp,
      endTimestamp: last.timestamp,
      start: first,
      end: last,
      returnPct: stablePercentage((last.close / first.close - 1) * 100)
    };
  }

  function computeRangeDetails(mainRows, overlayItems, period, startIndex, endIndex) {
    var main = computeMainRange(mainRows, startIndex, endIndex);
    if (!main) return null;
    var overlays = (Array.isArray(overlayItems) ? overlayItems : []).map(function (item) {
      var rows = Array.isArray(item) ? item : (item && item.rows);
      return {
        code: item && item.code ? item.code : '',
        name: item && (item.name || item.shortName) ? (item.name || item.shortName) : '',
        color: item && item.color ? item.color : null,
        result: computeRangeComparison(mainRows, rows, period, main.startIndex, main.endIndex)
      };
    });
    return {
      startIndex: main.startIndex,
      endIndex: main.endIndex,
      startTimestamp: main.startTimestamp,
      endTimestamp: main.endTimestamp,
      naturalDays: naturalDayCount(main.startTimestamp, main.endTimestamp),
      tradingDays: countRangeTradingDays(mainRows, main.startIndex, main.endIndex),
      barCount: countRangeBars(mainRows, main.startIndex, main.endIndex),
      main: main,
      overlays: overlays
    };
  }

  function createSearchUrl(query) {
    return '/api/search?q=' + encodeURIComponent(String(query == null ? '' : query));
  }

  function escapeText(value) {
    return String(value == null ? '' : value);
  }

  function element(tag, namespace) {
    if (!doc) return null;
    if (namespace && doc.createElementNS) return doc.createElementNS(SVG_NS, tag);
    return doc.createElement(tag);
  }

  function setStyle(node, values) {
    if (!node || !node.style) return;
    Object.keys(values).forEach(function (key) { node.style[key] = values[key]; });
  }

  function attr(node, name, value) {
    if (node && typeof node.setAttribute === 'function') node.setAttribute(name, String(value));
  }

  function appendText(node, value) {
    if (!node) return;
    if (typeof node.textContent !== 'undefined') node.textContent = escapeText(value);
  }

  function clearChildren(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
    if (node.children && typeof node.children.length === 'number') {
      while (node.children.length) node.removeChild(node.children[node.children.length - 1]);
    }
  }

  function ComparisonController() {
    this.state = {
      overlays: [],
      rangeSelections: [],
      rangeSelection: null,
      rangeSelecting: false,
      rangeDragging: false,
      rangeDraft: null
    };
    this.overlays = this.state.overlays;
    this.chart = null;
    this.endpoint = null;
    this.endpointTimestamp = null;
    this.endpointPinned = false;
    this.summaryPosition = null;
    this.summaryDrag = null;
    this.panel = null;
    this.button = null;
    this.rangeTool = null;
    this.svg = null;
    this.summary = null;
    this.legend = null;
    this.mainDom = null;
    this.searchResults = [];
    this.searchIndex = -1;
    this.searchTimer = null;
    this.searchAbort = null;
    this.loadTimer = null;
    this.renderTimer = null;
    this.requestSeq = 0;
    this.nextRangeId = 1;
    this.lastContextKey = '';
    this.replayHistory = null;
    this.dragging = false;
    this.initialized = false;
    this.listeners = [];
    this._chartUnbind = null;
    this.scaleIndicatorAttached = false;
    this.scaleIndicatorRegistered = false;
    this.scaleIndicatorSignature = '';
    this.syncingScaleIndicator = false;
  }

  ComparisonController.prototype._listen = function (target, event, handler, options) {
    if (!target || typeof target.addEventListener !== 'function') return;
    target.addEventListener(event, handler, options);
    this.listeners.push(function () {
      try { target.removeEventListener(event, handler, options); } catch (e) {}
    });
  };

  ComparisonController.prototype._getContext = function () {
    var context = global.__board_ctx || {};
    var symbol = null;
    try {
      symbol = global.pro && typeof global.pro.getSymbol === 'function' ? global.pro.getSymbol() : null;
    } catch (e) {}
    return {
      code: String(context.code || (symbol && (symbol.ticker || symbol.code)) || ''),
      name: context.name || (symbol && (symbol.name || symbol.shortName)) || '',
      type: context.type || (symbol && symbol.type) || 'index',
      period: (global.pro && typeof global.pro.getPeriod === 'function' ? global.pro.getPeriod() : context.period) || 'daily'
    };
  };

  ComparisonController.prototype._getRange = function () {
    var data = this.chart && typeof this.chart.getDataList === 'function' ? this.chart.getDataList() : [];
    var range = this.chart && typeof this.chart.getVisibleRange === 'function' ? this.chart.getVisibleRange() : null;
    if (!Array.isArray(data) || !data.length) return { data: [], from: 0, to: -1 };
    var from = range && finiteNumber(range.realFrom != null ? range.realFrom : range.from);
    var to = range && finiteNumber(range.realTo != null ? range.realTo : range.to);
    from = from == null ? 0 : Math.max(0, Math.floor(from));
    to = to == null ? data.length - 1 : Math.min(data.length - 1, Math.ceil(to));
    if (to < from) { from = 0; to = data.length - 1; }
    return { data: data, from: from, to: to };
  };

  ComparisonController.prototype._visibleMain = function () {
    var range = this._getRange();
    return { range: range, rows: range.data.slice(range.from, range.to + 1) };
  };

  ComparisonController.prototype._rangeSelectionForVisible = function (selection, visible) {
    if (!selection || !visible || !visible.rows || !visible.rows.length) return null;
    var count = visible.rows.length;
    var timestamps = visible.rows.map(function (row) { return toMillis(row && row.timestamp); });
    var startTimestamp = finiteNumber(selection.startTimestamp);
    var endTimestamp = finiteNumber(selection.endTimestamp);
    var start = null;
    var end = null;
    if (startTimestamp != null && endTimestamp != null) {
      if (startTimestamp > endTimestamp) {
        var timestampSwap = startTimestamp;
        startTimestamp = endTimestamp;
        endTimestamp = timestampSwap;
      }
      var visibleStart = timestamps[0];
      var visibleEnd = timestamps[timestamps.length - 1];
      if (endTimestamp < visibleStart || startTimestamp > visibleEnd) return null;
      for (var timestampIndex = 0; timestampIndex < timestamps.length; timestampIndex += 1) {
        if (start == null && timestamps[timestampIndex] >= startTimestamp) start = timestampIndex;
        if (timestamps[timestampIndex] <= endTimestamp) end = timestampIndex;
      }
    } else {
      var absoluteStart = finiteNumber(selection.absoluteStartIndex);
      var absoluteEnd = finiteNumber(selection.absoluteEndIndex);
      if (absoluteStart == null || absoluteEnd == null) {
        start = finiteNumber(selection.startIndex);
        end = finiteNumber(selection.endIndex);
      } else {
        start = absoluteStart - Number(visible.range.from || 0);
        end = absoluteEnd - Number(visible.range.from || 0);
        if (Math.max(start, end) < 0 || Math.min(start, end) >= count) return null;
      }
    }
    if (start == null || end == null) return null;
    start = clampEndpoint(start, 0, count - 1);
    end = clampEndpoint(end, 0, count - 1);
    if (start > end) { var swap = start; start = end; end = swap; }
    var startPriceField = selection.startPriceField || 'close';
    var endPriceField = selection.endPriceField || 'close';
    var startPrice = finiteNumber(visible.rows[start] && visible.rows[start][startPriceField]);
    var endPrice = finiteNumber(visible.rows[end] && visible.rows[end][endPriceField]);
    if (startPrice == null) startPrice = finiteNumber(selection.startPrice);
    if (endPrice == null) endPrice = finiteNumber(selection.endPrice);
    if (startPrice == null) startPrice = finiteNumber(visible.rows[start] && visible.rows[start].close);
    if (endPrice == null) endPrice = finiteNumber(visible.rows[end] && visible.rows[end].close);
    return {
      startIndex: start,
      endIndex: end,
      absoluteStartIndex: Number(visible.range.from || 0) + start,
      absoluteEndIndex: Number(visible.range.from || 0) + end,
      startTimestamp: toMillis(visible.rows[start].timestamp),
      endTimestamp: toMillis(visible.rows[end].timestamp),
      startPriceField: startPriceField,
      endPriceField: endPriceField,
      startPrice: startPrice,
      endPrice: endPrice,
      topRatio: Math.max(0, Math.min(1, finiteNumber(selection.topRatio) == null ? 0.14 : Number(selection.topRatio))),
      bottomRatio: Math.max(0, Math.min(1, finiteNumber(selection.bottomRatio) == null ? 0.86 : Number(selection.bottomRatio)))
    };
  };

  ComparisonController.prototype._rangesForVisible = function (visible) {
    var selections = Array.isArray(this.state.rangeSelections) ? this.state.rangeSelections : [];
    if (!selections.length && this.state.rangeSelection) selections = [this.state.rangeSelection];
    return selections.map(function (selection) {
      return this._rangeSelectionForVisible(selection, visible);
    }.bind(this)).filter(Boolean);
  };

  ComparisonController.prototype._rangeForVisible = function (visible) {
    var ranges = this._rangesForVisible(visible);
    return ranges.length ? ranges[ranges.length - 1] : null;
  };

  ComparisonController.prototype._copyRangeSelection = function (selection) {
    if (!selection) return null;
    var copy = {};
    Object.keys(selection).forEach(function (key) { copy[key] = selection[key]; });
    return copy;
  };

  ComparisonController.prototype.startRangeSelection = function () {
    if (!this._visibleMain().rows.length) return false;
    this.state.rangeSelecting = true;
    this.state.rangeDragging = false;
    this.state.rangeDraft = {
      startIndex: null,
      endIndex: null,
      startX: null,
      endX: null,
      startY: null,
      endY: null,
      moved: false,
      previousSelection: null
    };
    this._renderPanelOverlays();
    this._draw();
    return true;
  };

  ComparisonController.prototype._cancelRangeSelection = function () {
    var draft = this.state.rangeDraft;
    this.state.rangeSelecting = false;
    this.state.rangeDragging = false;
    this.state.rangeDraft = null;
    this._renderPanelOverlays();
    this._draw();
  };

  ComparisonController.prototype.clearRangeSelection = function () {
    this.state.rangeSelecting = false;
    this.state.rangeDragging = false;
    this.state.rangeDraft = null;
    this.state.rangeSelections = [];
    this.state.rangeSelection = null;
    this._renderPanelOverlays();
    this._draw();
    return true;
  };

  ComparisonController.prototype.removeRangeSelection = function (idOrIndex) {
    var selections = Array.isArray(this.state.rangeSelections) ? this.state.rangeSelections : [];
    var index = typeof idOrIndex === 'number'
      ? Math.floor(idOrIndex)
      : selections.findIndex(function (selection) { return selection && selection.id === idOrIndex; });
    if (index < 0 || index >= selections.length) return false;
    selections.splice(index, 1);
    this.state.rangeSelection = selections.length ? selections[selections.length - 1] : null;
    this._renderPanelOverlays();
    this._draw();
    return true;
  };

  ComparisonController.prototype.getRangeSelection = function () {
    var selections = Array.isArray(this.state.rangeSelections) ? this.state.rangeSelections : [];
    return this._copyRangeSelection(selections.length ? selections[selections.length - 1] : this.state.rangeSelection);
  };

  ComparisonController.prototype.getRangeSelections = function () {
    var selections = Array.isArray(this.state.rangeSelections) ? this.state.rangeSelections : [];
    return selections.map(this._copyRangeSelection.bind(this));
  };

  ComparisonController.prototype.setRangeSelectionIndices = function (startIndex, endIndex, anchors) {
    var visible = this._visibleMain();
    if (!visible.rows.length) return false;
    var start = Number(startIndex);
    var end = Number(endIndex);
    if (!isFinite(start) || !isFinite(end)) return false;
    start = clampEndpoint(start, 0, visible.rows.length - 1);
    end = clampEndpoint(end, 0, visible.rows.length - 1);
    anchors = anchors || {};
    if (start > end) {
      var swap = start; start = end; end = swap;
      var anchorSwap = anchors.start;
      anchors = { start: anchors.end, end: anchorSwap };
    }
    var startField = anchors.start && anchors.start.priceField || 'close';
    var endField = anchors.end && anchors.end.priceField || 'close';
    var startPrice = finiteNumber(visible.rows[start] && visible.rows[start][startField]);
    var endPrice = finiteNumber(visible.rows[end] && visible.rows[end][endField]);
    var selection = {
      id: 'range-' + this.nextRangeId++,
      startIndex: start,
      endIndex: end,
      absoluteStartIndex: Number(visible.range.from || 0) + start,
      absoluteEndIndex: Number(visible.range.from || 0) + end,
      startTimestamp: toMillis(visible.rows[start].timestamp),
      endTimestamp: toMillis(visible.rows[end].timestamp),
      startPriceField: startField,
      endPriceField: endField,
      startPrice: startPrice,
      endPrice: endPrice,
      topRatio: 0.14,
      bottomRatio: 0.86
    };
    if (!Array.isArray(this.state.rangeSelections)) this.state.rangeSelections = [];
    this.state.rangeSelections.push(selection);
    this.state.rangeSelection = selection;
    this.state.rangeSelecting = false;
    this.state.rangeDragging = false;
    this.state.rangeDraft = null;
    this._renderPanelOverlays();
    this._draw();
    return true;
  };

  ComparisonController.prototype._contextKey = function () {
    var context = this._getContext();
    return symbolIdentity(context.code) + '|' + String(context.type || '') + '|' + periodToApi(context.period);
  };

  ComparisonController.prototype._buildKlineUrl = function (item, mainRows) {
    var context = this._getContext();
    var rows = validRows(mainRows);
    var from = rows.length ? rows[0].timestamp : null;
    var to = rows.length ? rows[rows.length - 1].timestamp : null;
    var type = item.type || 'index';
    var code = encodeURIComponent(item.code || item.ticker || '');
    var params = [
      'name=' + encodeURIComponent(item.name || item.shortName || ''),
      'period=' + encodeURIComponent(periodToApi(context.period)),
      'cache_first=1'
    ];
    if (from != null) params.push('from=' + Math.trunc(from));
    if (to != null) params.push('to=' + Math.trunc(to));
    if (from == null && to == null) params.push('limit=2000');
    return (global.API || '') + '/api/kline/' + encodeURIComponent(type) + '/' + code + '?' + params.join('&');
  };

  ComparisonController.prototype._pickColor = function (code) {
    var used = {};
    this.overlays.forEach(function (item) { used[item.color] = true; });
    return comparisonModel.pickColor(used, code);
  };

  ComparisonController.prototype._createOverlay = function (item) {
    var code = String(item.code || item.ticker || item.symbol || item.display_code || '');
    return {
      code: code,
      name: item.name || item.shortName || code,
      type: item.type || 'index',
      color: this._pickColor(code),
      rows: [],
      comparison: [],
      loading: false,
      error: null,
      abort: null,
      requestSeq: 0,
      retryTimer: null,
      retryCount: 0,
      requestedFrom: null,
      requestedTo: null
    };
  };

  ComparisonController.prototype._findOverlay = function (code) {
    var identity = symbolIdentity(code);
    for (var index = 0; index < this.overlays.length; index += 1) {
      if (symbolIdentity(this.overlays[index].code) === identity) return this.overlays[index];
    }
    return null;
  };

  ComparisonController.prototype._ensureUi = function () {
    if (!doc) return;
    this._ensureRangeTool();
    if (this.button) return;
    var bar = doc.querySelector ? doc.querySelector('.klinecharts-pro-period-bar') : null;
    if (!bar) return;
    this.button = element('button');
    this.button.id = 'chart-comparison-button';
    this.button.type = 'button';
    appendText(this.button, '叠加');
    this.button.title = '添加一个或多个标的进行涨跌幅比较';
    setStyle(this.button, { marginLeft: '6px', padding: '4px 10px', border: '1px solid #8899aa', borderRadius: '4px', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: '12px' });
    var firstTool = bar.querySelector ? bar.querySelector('.item.tools') : null;
    if (firstTool && bar.insertBefore) bar.insertBefore(this.button, firstTool);
    else if (bar.appendChild) bar.appendChild(this.button);
    this._listen(this.button, 'click', this._togglePanel.bind(this));

    this.panel = element('div');
    this.panel.id = 'chart-comparison-panel';
    setStyle(this.panel, { position: 'absolute', zIndex: '30', minWidth: '280px', maxWidth: '340px', padding: '8px', border: '1px solid #8ca0b5', borderRadius: '5px', background: 'var(--board-panel-bg, #f7f9fc)', color: 'inherit', boxShadow: '0 4px 12px rgba(0,0,0,.18)', display: 'none' });
    var input = element('input');
    input.id = 'chart-comparison-input';
    input.type = 'search';
    input.placeholder = '输入标的、拼音或代码';
    input.autocomplete = 'off';
    setStyle(input, { width: '100%', boxSizing: 'border-box', padding: '6px 8px', border: '1px solid #9aaabc', borderRadius: '3px', background: 'transparent', color: 'inherit' });
    this.panel.appendChild(input);
    var results = element('div');
    results.id = 'chart-comparison-results';
    setStyle(results, { maxHeight: '220px', overflowY: 'auto', marginTop: '6px' });
    this.panel.appendChild(results);
    this.legend = element('div');
    this.legend.id = 'chart-comparison-overlays';
    setStyle(this.legend, { marginTop: '8px', paddingTop: '6px', borderTop: '1px solid rgba(128,128,128,.22)' });
    this.panel.appendChild(this.legend);
    var status = element('div');
    status.id = 'chart-comparison-status';
    setStyle(status, { marginTop: '5px', fontSize: '11px', opacity: '.75' });
    this.panel.appendChild(status);
    var clear = element('button');
    clear.id = 'chart-comparison-clear';
    clear.type = 'button';
    appendText(clear, '清空全部');
    clear.title = '移除全部叠加标的';
    setStyle(clear, { marginTop: '7px', padding: '4px 8px', border: '1px solid #8899aa', borderRadius: '3px', background: 'transparent', color: 'inherit', cursor: 'pointer' });
    this.panel.appendChild(clear);
    var host = bar.parentNode || bar;
    if (host && host.appendChild) host.appendChild(this.panel);
    this._listen(input, 'input', this._onSearchInput.bind(this));
    this._listen(input, 'focus', function () {
      if (!String(input.value || '').trim()) this._renderSearchHistory();
      if (typeof global.ensureBoardSearchHistory === 'function') {
        global.ensureBoardSearchHistory().then(function () {
          if (!String(input.value || '').trim()) this._renderSearchHistory();
        }.bind(this)).catch(function () {});
      }
    }.bind(this));
    this._listen(input, 'keydown', this._onSearchKeydown.bind(this));
    this._listen(results, 'click', this._onResultClick.bind(this));
    this._listen(clear, 'click', function () { this.clearOverlay(); }.bind(this));
    this._listen(doc, 'click', function (event) {
      if (this.panel && event.target !== this.button && !this.panel.contains(event.target)) this._hidePanel();
    }.bind(this));
    this._renderPanelOverlays();
  };

  ComparisonController.prototype._ensureRangeTool = function () {
    if (!doc || (this.rangeTool && this.rangeTool.parentNode)) return !!this.rangeTool;
    var drawingBar = doc.querySelector ? doc.querySelector('.klinecharts-pro-drawing-bar') : null;
    if (!drawingBar) return false;
    var tool = element('div');
    tool.id = 'chart-comparison-range-select';
    tool.className = 'item board-comparison-range-tool';
    tool.tabIndex = 0;
    tool.title = '框选区间（按左侧收盘价到右侧收盘价计算涨跌幅）';
    tool.setAttribute('role', 'button');
    tool.setAttribute('aria-label', '框选区间');
    var glyph = element('span');
    glyph.className = 'board-comparison-range-glyph';
    glyph.setAttribute('aria-hidden', 'true');
    ['frame', 'start', 'end', 'measure', 'baseline'].forEach(function (part) {
      var segment = element('span');
      segment.className = 'board-comparison-range-glyph-' + part;
      glyph.appendChild(segment);
    });
    tool.appendChild(glyph);
    var activate = function (event) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
      if (this.state.rangeSelecting) this._cancelRangeSelection();
      else this.startRangeSelection();
    }.bind(this);
    this._listen(tool, 'click', activate);
    this._listen(tool, 'keydown', function (event) {
      if (!event || (event.key !== 'Enter' && event.key !== ' ')) return;
      activate(event);
    });
    var split = drawingBar.querySelector ? drawingBar.querySelector('.split-line') : null;
    if (drawingBar.insertBefore) drawingBar.insertBefore(tool, split || null);
    else drawingBar.appendChild(tool);
    this.rangeTool = tool;
    return true;
  };

  ComparisonController.prototype._togglePanel = function () {
    if (!this.panel) return;
    if (this.panel.style.display === 'none') {
      var rect = this.button.getBoundingClientRect ? this.button.getBoundingClientRect() : { left: 0, bottom: 0 };
      setStyle(this.panel, { display: 'block', left: Math.max(0, rect.left - 180) + 'px', top: (rect.bottom + 4) + 'px' });
      var input = doc.getElementById('chart-comparison-input');
      if (input) {
        if (input.focus) input.focus();
        if (!String(input.value || '').trim()) this._renderSearchHistory();
      }
    } else this._hidePanel();
  };

  ComparisonController.prototype._hidePanel = function () {
    if (this.panel) this.panel.style.display = 'none';
  };

  ComparisonController.prototype._onSearchInput = function (event) {
    var query = String(event.target.value || '').trim();
    var results = doc.getElementById('chart-comparison-results');
    if (this.searchTimer) clearTimeout(this.searchTimer);
    if (this.searchAbort) { try { this.searchAbort.abort(); } catch (e) {} }
    if (!query) { this._renderSearchHistory(); return; }
    if (results) results.textContent = '搜索中...';
    this.searchTimer = setTimeout(function () { this._search(query); }.bind(this), 120);
  };

  ComparisonController.prototype._search = function (query) {
    var self = this;
    var controller = typeof global.AbortController !== 'undefined' ? new global.AbortController() : null;
    this.searchAbort = controller;
    var options = controller ? { signal: controller.signal } : undefined;
    var request = typeof global.fetch === 'function' ? global.fetch(createSearchUrl(query), options) : Promise.reject(new Error('fetch unavailable'));
    request.then(function (response) { return response.json(); }).then(function (payload) {
      if (controller && controller.signal.aborted) return;
      var values = payload && Array.isArray(payload.data) ? payload.data : (Array.isArray(payload) ? payload : []);
      self._renderSearchResults(values);
    }).catch(function (error) {
      if (error && error.name === 'AbortError') return;
      var results = doc.getElementById('chart-comparison-results');
      if (results) results.textContent = '搜索失败';
    });
  };

  ComparisonController.prototype._renderSearchResults = function (values) {
    var results = doc.getElementById('chart-comparison-results');
    if (!results) return;
    clearChildren(results);
    this.searchResults = (Array.isArray(values) ? values : []).slice(0, 20);
    this.searchIndex = this.searchResults.length ? 0 : -1;
    if (!this.searchResults.length) { results.textContent = '无匹配结果'; return; }
    this.searchResults.forEach(function (item, index) {
      var row = element('button');
      row.type = 'button';
      row.className = 'chart-comparison-result';
      if (!row.dataset) row.dataset = {};
      row.dataset.index = String(index);
      appendText(row, (item.name || item.shortName || item.code || item.display_code) + '  ' + (item.display_code || item.code || ''));
      setStyle(row, { display: 'block', width: '100%', padding: '6px', border: '0', borderBottom: '1px solid rgba(128,128,128,.2)', background: index === 0 ? 'rgba(64,128,255,.14)' : 'transparent', color: 'inherit', textAlign: 'left', cursor: 'pointer' });
      results.appendChild(row);
    });
  };

  ComparisonController.prototype._searchHistoryStore = function () {
    var store = global && global.BoardSearchHistory;
    return store && typeof store.list === 'function' && typeof store.add === 'function' ? store : null;
  };

  ComparisonController.prototype._renderSearchHistory = function () {
    var results = doc.getElementById('chart-comparison-results');
    if (!results) return;
    var store = this._searchHistoryStore();
    var values = [];
    try { values = store ? store.list().slice(0, 5) : []; } catch (e) { values = []; }
    clearChildren(results);
    this.searchResults = values;
    this.searchIndex = values.length ? 0 : -1;
    if (!values.length) return;
    var heading = element('div');
    heading.className = 'search-history-header';
    appendText(heading, '最近搜索');
    results.appendChild(heading);
    values.forEach(function (item, index) {
      var row = element('button');
      row.type = 'button';
      row.className = 'chart-comparison-result chart-comparison-history-item';
      if (!row.dataset) row.dataset = {};
      row.dataset.index = String(index);
      appendText(row, (item.name || item.value || item.code || '') + '  ' + (item.display_code || item.code || ''));
      setStyle(row, { display: 'block', width: '100%', padding: '6px', border: '0', borderBottom: '1px solid rgba(128,128,128,.2)', background: index === 0 ? 'rgba(64,128,255,.14)' : 'transparent', color: 'inherit', textAlign: 'left', cursor: 'pointer' });
      results.appendChild(row);
    });
  };

  ComparisonController.prototype._setSearchIndex = function (index) {
    var count = this.searchResults ? this.searchResults.length : 0;
    if (!count) { this.searchIndex = -1; return; }
    this.searchIndex = (index + count) % count;
    var results = doc.getElementById('chart-comparison-results');
    if (!results || !results.children) return;
    var rows = results.querySelectorAll ? results.querySelectorAll('.chart-comparison-result') : results.children;
    Array.prototype.forEach.call(rows, function (row, rowIndex) {
      row.style.background = rowIndex === this.searchIndex ? 'rgba(64,128,255,.14)' : 'transparent';
    }.bind(this));
  };

  ComparisonController.prototype._selectSearchResult = function (item) {
    if (!this.selectOverlay(item)) return;
    var input = doc.getElementById('chart-comparison-input');
    var query = input ? String(input.value || '').trim() : '';
    var store = this._searchHistoryStore();
    var historyItem = {
      code: item.code || item.ticker,
      value: query || item.value || item.name || item.shortName || item.code,
      name: item.name || item.shortName || '',
      type: item.type || '',
      category: item.category || '',
      display_code: item.display_code || item.code || '',
      time: Date.now()
    };
    if (store) {
      try {
        store.add(historyItem);
      } catch (e) {}
    }
    if (typeof global.recordBoardSearchHistory === 'function') global.recordBoardSearchHistory(historyItem);
    if (input) { input.value = ''; if (input.focus) input.focus(); }
    this._renderSearchHistory();
    /* Keep the popover open so another symbol can be added immediately. */
    this._renderPanelOverlays();
  };

  ComparisonController.prototype._onSearchKeydown = function (event) {
    if (event.key === 'Escape') { event.preventDefault(); this._hidePanel(); return; }
    if (event.key === 'ArrowDown') { event.preventDefault(); this._setSearchIndex(this.searchIndex + 1); return; }
    if (event.key === 'ArrowUp') { event.preventDefault(); this._setSearchIndex(this.searchIndex - 1); return; }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (this.searchResults && this.searchResults[this.searchIndex]) this._selectSearchResult(this.searchResults[this.searchIndex]);
    }
  };

  ComparisonController.prototype._onResultClick = function (event) {
    var node = event.target;
    while (node && node !== event.currentTarget && (!node.dataset || node.dataset.index == null)) node = node.parentNode;
    if (node && node.dataset && node.dataset.index != null && this.searchResults) this._selectSearchResult(this.searchResults[Number(node.dataset.index)]);
  };

  ComparisonController.prototype._ensureOverlayDom = function () {
    if (!this.mainDom || !doc) return;
    if (this.mainDom.style && !this.mainDom.style.position) this.mainDom.style.position = 'relative';
    if (!this.svg) {
      this.svg = element('svg', true);
      this.svg.id = 'chart-comparison-svg';
      attr(this.svg, 'aria-hidden', 'true');
      setStyle(this.svg, { position: 'absolute', inset: '0', width: '100%', height: '100%', zIndex: '5', pointerEvents: 'none', overflow: 'hidden' });
      if (this.mainDom.appendChild) this.mainDom.appendChild(this.svg);
    }
    if (!this.summary) {
      this.summary = element('div');
      this.summary.id = 'chart-comparison-summary';
      setStyle(this.summary, { position: 'absolute', left: '10px', bottom: '8px', zIndex: '6', padding: '5px 7px', border: '1px solid rgba(72,103,145,.45)', borderRadius: '3px', background: 'rgba(255,255,255,.92)', color: '#17263a', fontSize: '11px', lineHeight: '1.4', pointerEvents: 'auto', whiteSpace: 'nowrap' });
      if (typeof this.summary.addEventListener === 'function') {
        this.summary.addEventListener('mousedown', this._onSummaryMouseDown.bind(this));
      }
      if (this.mainDom.appendChild) this.mainDom.appendChild(this.summary);
    }
  };

  ComparisonController.prototype._summaryMetrics = function () {
    if (!this.mainDom || !this.summary) return null;
    var mainRect = this.mainDom.getBoundingClientRect ? this.mainDom.getBoundingClientRect() : {};
    var boxRect = this.summary.getBoundingClientRect ? this.summary.getBoundingClientRect() : {};
    var width = Number(this.mainDom.clientWidth || mainRect.width || 1);
    var height = Number(this.mainDom.clientHeight || mainRect.height || 1);
    var boxWidth = Math.min(width, Number(this.summary.offsetWidth || this.summary.clientWidth || boxRect.width || 1));
    var boxHeight = Math.min(height, Number(this.summary.offsetHeight || this.summary.clientHeight || boxRect.height || 1));
    return {
      left: Number(mainRect.left || 0),
      top: Number(mainRect.top || 0),
      width: Math.max(1, width),
      height: Math.max(1, height),
      boxWidth: Math.max(1, boxWidth),
      boxHeight: Math.max(1, boxHeight)
    };
  };

  ComparisonController.prototype._currentSummaryPosition = function (metrics) {
    if (!metrics) return { left: 0, top: 0 };
    if (this.summaryPosition) {
      return {
        left: Math.max(0, Math.min(metrics.width - metrics.boxWidth, Number(this.summaryPosition.left) || 0)),
        top: Math.max(0, Math.min(metrics.height - metrics.boxHeight, Number(this.summaryPosition.top) || 0))
      };
    }
    var style = this.summary && this.summary.style ? this.summary.style : {};
    var left = finiteNumber(parseFloat(style.left));
    var top = finiteNumber(parseFloat(style.top));
    var right = finiteNumber(parseFloat(style.right));
    var bottom = finiteNumber(parseFloat(style.bottom));
    if (left == null) left = right == null ? 10 : metrics.width - metrics.boxWidth - right;
    if (top == null) top = metrics.height - metrics.boxHeight - (bottom == null ? 8 : bottom);
    return {
      left: Math.max(0, Math.min(metrics.width - metrics.boxWidth, left)),
      top: Math.max(0, Math.min(metrics.height - metrics.boxHeight, top))
    };
  };

  ComparisonController.prototype._applySummaryPosition = function (position, metrics) {
    if (!this.summary || !position || !metrics) return;
    var next = {
      left: Math.max(0, Math.min(metrics.width - metrics.boxWidth, Number(position.left) || 0)),
      top: Math.max(0, Math.min(metrics.height - metrics.boxHeight, Number(position.top) || 0))
    };
    this.summaryPosition = next;
    setStyle(this.summary, { left: next.left + 'px', top: next.top + 'px', right: 'auto', bottom: 'auto' });
  };

  ComparisonController.prototype._onSummaryMouseDown = function (event) {
    if (!event || (event.button != null && event.button !== 0)) return;
    var tag = event.target && String(event.target.tagName || '').toUpperCase();
    if (tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    var metrics = this._summaryMetrics();
    if (!metrics) return;
    var position = this._currentSummaryPosition(metrics);
    this.summaryDrag = {
      offsetX: Number(event.clientX || 0) - metrics.left - position.left,
      offsetY: Number(event.clientY || 0) - metrics.top - position.top
    };
    this._applySummaryPosition(position, metrics);
    event.__boardDrawingHandled = true;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  };

  ComparisonController.prototype._moveSummary = function (event) {
    if (!this.summaryDrag) return false;
    var metrics = this._summaryMetrics();
    if (!metrics) return false;
    this._applySummaryPosition({
      left: Number(event.clientX || 0) - metrics.left - this.summaryDrag.offsetX,
      top: Number(event.clientY || 0) - metrics.top - this.summaryDrag.offsetY
    }, metrics);
    event.__boardDrawingHandled = true;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    return true;
  };

  ComparisonController.prototype._clearSvg = function () {
    clearChildren(this.svg);
  };

  ComparisonController.prototype._svgPoint = function (event) {
    if (!this.svg || !this.mainDom) return null;
    var bounds = this.svg.getBoundingClientRect ? this.svg.getBoundingClientRect() : { left: 0, top: 0, width: this.mainDom.clientWidth || 1, height: this.mainDom.clientHeight || 1 };
    var width = Number(bounds.width || this.mainDom.clientWidth || 1);
    var height = Number(bounds.height || this.mainDom.clientHeight || 1);
    var x = Number(event && event.clientX) - Number(bounds.left || 0);
    var y = Number(event && event.clientY) - Number(bounds.top || 0);
    if (!isFinite(x) || !isFinite(y)) return null;
    return {
      x: Math.max(0, Math.min(width, x)),
      y: Math.max(0, Math.min(height, y)),
      width: Math.max(1, width),
      height: Math.max(1, height)
    };
  };

  ComparisonController.prototype._convertMainX = function (row, absoluteIndex) {
    var chart = this.chart;
    if (!chart || typeof chart.convertToPixel !== 'function') return null;
    var timestamp = toMillis(row && row.timestamp);
    var attempts = [];
    if (finiteNumber(absoluteIndex) != null) {
      attempts.push([{ dataIndex: Number(absoluteIndex) }]);
      attempts.push({ dataIndex: Number(absoluteIndex) });
    }
    if (timestamp != null) {
      attempts.push([{ timestamp: timestamp }]);
      attempts.push({ timestamp: timestamp });
    }
    for (var index = 0; index < attempts.length; index += 1) {
      try {
        var point = pixelPoint(chart.convertToPixel(attempts[index], { paneId: 'candle_pane' }));
        if (point && finiteNumber(point.x) != null) return point.x;
      } catch (e) {}
    }
    return null;
  };

  ComparisonController.prototype._convertMainY = function (value) {
    var chart = this.chart;
    var price = finiteNumber(value);
    if (!chart || price == null || typeof chart.convertToPixel !== 'function') return null;
    var attempts = [[{ value: Number(price) }], { value: Number(price) }];
    for (var index = 0; index < attempts.length; index += 1) {
      try {
        var converted = chart.convertToPixel(attempts[index], { paneId: 'candle_pane' });
        var point = Array.isArray(converted) ? converted[0] : converted;
        if (Array.isArray(point)) point = point[0];
        if (point && finiteNumber(point.y) != null) return Number(point.y);
      } catch (e) {}
    }
    return null;
  };

  ComparisonController.prototype._scaleIndicatorDefinition = function (extent) {
    return {
      name: SCALE_INDICATOR_NAME,
      shortName: '',
      series: 'price',
      precision: 4,
      shouldOhlc: false,
      shouldFormatBigNumber: false,
      visible: true,
      figures: [],
      minValue: Number(extent.min),
      maxValue: Number(extent.max),
      calc: function (dataList) {
        return (Array.isArray(dataList) ? dataList : []).map(function () { return {}; });
      },
      createTooltipDataSource: function () { return { name: '', values: [] }; },
      draw: function () { return true; }
    };
  };

  ComparisonController.prototype._removeScaleIndicator = function (targetChart) {
    var chart = targetChart || this.chart;
    if (chart && this.scaleIndicatorAttached && typeof chart.removeIndicator === 'function') {
      try { chart.removeIndicator('candle_pane', SCALE_INDICATOR_NAME); } catch (e) {}
    }
    this.scaleIndicatorAttached = false;
    this.scaleIndicatorSignature = '';
  };

  ComparisonController.prototype._syncScaleIndicator = function (extent) {
    var chart = this.chart;
    if (!extent) {
      this._removeScaleIndicator(chart);
      return false;
    }
    if (!chart || this.syncingScaleIndicator) return false;
    var signature = Number(extent.min).toFixed(8) + '|' + Number(extent.max).toFixed(8);
    if (this.scaleIndicatorAttached && signature === this.scaleIndicatorSignature) return true;
    this.syncingScaleIndicator = true;
    try {
      var definition = this._scaleIndicatorDefinition(extent);
      if (!this.scaleIndicatorRegistered) {
        if (!global.klinecharts || typeof global.klinecharts.registerIndicator !== 'function') return false;
        global.klinecharts.registerIndicator(definition);
        this.scaleIndicatorRegistered = true;
      }
      if (!this.scaleIndicatorAttached) {
        if (typeof chart.createIndicator !== 'function') return false;
        var paneId = chart.createIndicator(definition, true, { id: 'candle_pane' });
        this.scaleIndicatorAttached = paneId != null;
        if (this.scaleIndicatorAttached) {
          setTimeout(function () { this._scheduleRender(); }.bind(this), 80);
        }
      } else if (typeof chart.overrideIndicator === 'function') {
        chart.overrideIndicator({
          name: SCALE_INDICATOR_NAME,
          minValue: definition.minValue,
          maxValue: definition.maxValue,
          visible: true
        }, 'candle_pane', function () { this._scheduleRender(); }.bind(this));
      }
      if (this.scaleIndicatorAttached) this.scaleIndicatorSignature = signature;
      return this.scaleIndicatorAttached;
    } catch (e) {
      return false;
    } finally {
      this.syncingScaleIndicator = false;
    }
  };

  ComparisonController.prototype._xMapper = function (visible, width) {
    var rows = visible && Array.isArray(visible.rows) ? visible.rows : [];
    var count = rows.length;
    var chartWidth = Math.max(1, Number(width) || 1);
    var fallback = function (index) {
      return 8 + (Number(index || 0) / Math.max(1, count - 1)) * Math.max(10, chartWidth - 16);
    };
    var converted = [];
    var canUseConverted = !!(this.chart && typeof this.chart.convertToPixel === 'function');
    if (canUseConverted) {
      for (var index = 0; index < count; index += 1) {
        var absoluteIndex = Number(visible.range && visible.range.from || 0) + index;
        converted.push(this._convertMainX(rows[index], absoluteIndex));
        if (converted[index] == null) { canUseConverted = false; break; }
      }
    }
    var values = canUseConverted ? converted : rows.map(function (_, index) { return fallback(index); });
    var x = function (index) {
      if (!values.length) return fallback(0);
      var resolved = clampEndpoint(index, 0, values.length - 1);
      return Number(values[resolved]);
    };
    var indexAtX = function (position) {
      if (!values.length) return -1;
      var target = Number(position);
      if (!isFinite(target)) return 0;
      var best = 0;
      var distance = Math.abs(values[0] - target);
      for (var offset = 1; offset < values.length; offset += 1) {
        var nextDistance = Math.abs(values[offset] - target);
        if (nextDistance < distance) { best = offset; distance = nextDistance; }
      }
      return best;
    };
    return {
      x: x,
      indexAtX: indexAtX,
      left: values.length ? Number(values[0]) : fallback(0),
      right: values.length ? Number(values[values.length - 1]) : fallback(0),
      values: values
    };
  };

  ComparisonController.prototype._rangeIndexAtX = function (x, width, count, mapper) {
    if (!count) return -1;
    if (mapper && typeof mapper.indexAtX === 'function') return mapper.indexAtX(x);
    var ratio = Math.max(0, Math.min(1, Number(x) / Math.max(1, Number(width))));
    return clampEndpoint(Math.round(ratio * (count - 1)), 0, count - 1);
  };

  ComparisonController.prototype._rangeIndexFromPoint = function (visible, point, mapper) {
    var rows = visible && Array.isArray(visible.rows) ? visible.rows : [];
    if (!rows.length || !point) return -1;
    var chart = this.chart;
    if (chart && typeof chart.convertFromPixel === 'function') {
      var attempts = [
        [{ x: Number(point.x), y: Number(point.y) }],
        { x: Number(point.x), y: Number(point.y) }
      ];
      for (var attemptIndex = 0; attemptIndex < attempts.length; attemptIndex += 1) {
        try {
          var converted = chart.convertFromPixel(attempts[attemptIndex], { paneId: 'candle_pane' });
          var resolved = Array.isArray(converted) ? converted[0] : converted;
          if (Array.isArray(resolved)) resolved = resolved[0];
          var absoluteIndex = finiteNumber(resolved && resolved.dataIndex);
          if (absoluteIndex != null) {
            return clampEndpoint(absoluteIndex - Number(visible.range && visible.range.from || 0), 0, rows.length - 1);
          }
          var timestamp = toMillis(resolved && resolved.timestamp);
          if (timestamp != null) {
            var closest = 0;
            var closestDistance = Math.abs(toMillis(rows[0].timestamp) - timestamp);
            for (var rowIndex = 1; rowIndex < rows.length; rowIndex += 1) {
              var distance = Math.abs(toMillis(rows[rowIndex].timestamp) - timestamp);
              if (distance < closestDistance) { closest = rowIndex; closestDistance = distance; }
            }
            return closest;
          }
        } catch (e) {}
      }
    }
    return this._rangeIndexAtX(point.x, point.width, rows.length, mapper);
  };

  ComparisonController.prototype._rangeDraftIndexForTimestamp = function (visible, timestamp, fallback) {
    var rows = visible && Array.isArray(visible.rows) ? visible.rows : [];
    var target = toMillis(timestamp);
    if (!rows.length || target == null) return clampEndpoint(fallback, 0, Math.max(0, rows.length - 1));
    var closest = 0;
    var closestDistance = Math.abs(toMillis(rows[0].timestamp) - target);
    for (var index = 1; index < rows.length; index += 1) {
      var distance = Math.abs(toMillis(rows[index].timestamp) - target);
      if (distance < closestDistance) { closest = index; closestDistance = distance; }
    }
    return closest;
  };

  ComparisonController.prototype._onRangeMouseDown = function (event) {
    /* Compatibility entry point: range selection is now completed by two clicks. */
    return this._onRangeClick(event);
  };

  ComparisonController.prototype._onRangeClick = function (event) {
    if (!this.state.rangeSelecting) return;
    if (event && event.button != null && Number(event.button) !== 0) return;
    var visible = this._visibleMain();
    var point = this._svgPoint(event);
    if (!point || !visible.rows.length) return;
    var mapper = this._xMapper(visible, point.width);
    var index = this._rangeIndexFromPoint(visible, point, mapper);
    if (!this.state.rangeDragging || !this.state.rangeDraft || this.state.rangeDraft.startIndex == null) {
      this.state.rangeDraft = {
        startIndex: index,
        endIndex: index,
        startTimestamp: toMillis(visible.rows[index].timestamp),
        endTimestamp: toMillis(visible.rows[index].timestamp),
        startPriceField: 'close',
        endPriceField: 'close',
        startPrice: finiteNumber(visible.rows[index].close),
        endPrice: finiteNumber(visible.rows[index].close),
        startX: point.x,
        endX: point.x,
        startY: point.y,
        endY: point.y,
        startYRatio: point.y / point.height,
        endYRatio: point.y / point.height,
        moved: false,
        previousSelection: null
      };
      this.state.rangeDragging = true;
      this._renderPanelOverlays();
      this._draw();
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
      return;
    }
    var draft = this.state.rangeDraft;
    draft.startIndex = this._rangeDraftIndexForTimestamp(visible, draft.startTimestamp, draft.startIndex);
    draft.endIndex = index;
    draft.endTimestamp = toMillis(visible.rows[index].timestamp);
    draft.endPriceField = 'close';
    draft.endPrice = finiteNumber(visible.rows[index].close);
    draft.endX = point.x;
    draft.endY = point.y;
    draft.endYRatio = point.y / point.height;
    var top = Math.min(Number(draft.startYRatio), Number(draft.endYRatio));
    var bottom = Math.max(Number(draft.startYRatio), Number(draft.endYRatio));
    var start = Math.min(draft.startIndex, draft.endIndex);
    var end = Math.max(draft.startIndex, draft.endIndex);
    this.state.rangeSelecting = false;
    this.state.rangeDragging = false;
    this.state.rangeDraft = null;
    var anchors = draft.startIndex <= draft.endIndex
      ? {
          start: { priceField: draft.startPriceField, price: draft.startPrice },
          end: { priceField: draft.endPriceField, price: draft.endPrice }
        }
      : {
          start: { priceField: draft.endPriceField, price: draft.endPrice },
          end: { priceField: draft.startPriceField, price: draft.startPrice }
        };
    if (this.setRangeSelectionIndices(start, end, anchors) && this.state.rangeSelection) {
      this.state.rangeSelection.topRatio = Math.max(0, Math.min(1, isFinite(top) ? top : 0.14));
      this.state.rangeSelection.bottomRatio = Math.max(0, Math.min(1, isFinite(bottom) ? bottom : 0.86));
      this._draw();
    }
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  };

  ComparisonController.prototype._onRangeMouseMove = function (event) {
    if (!this.state.rangeSelecting || !this.state.rangeDragging || !this.state.rangeDraft || this.state.rangeDraft.startIndex == null) return;
    var visible = this._visibleMain();
    var point = this._svgPoint(event);
    if (!point || !visible.rows.length) return;
    var draft = this.state.rangeDraft;
    var mapper = this._xMapper(visible, point.width);
    draft.startIndex = this._rangeDraftIndexForTimestamp(visible, draft.startTimestamp, draft.startIndex);
    draft.endIndex = this._rangeIndexFromPoint(visible, point, mapper);
    draft.endTimestamp = toMillis(visible.rows[draft.endIndex].timestamp);
    draft.endPriceField = 'close';
    draft.endPrice = finiteNumber(visible.rows[draft.endIndex].close);
    draft.endX = point.x;
    draft.endY = point.y;
    draft.endYRatio = point.y / point.height;
    draft.moved = draft.moved || draft.endIndex !== draft.startIndex || Math.abs(draft.endX - draft.startX) >= 3 || Math.abs(draft.endY - draft.startY) >= 3;
    this._draw();
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
  };

  ComparisonController.prototype._onRangeMouseUp = function (event) {
    /* Mouse release no longer commits a range; the second click does. */
    return event;
  };

  ComparisonController.prototype._onDocumentContextMenu = function (event) {
    if (!event || event.__boardDrawingHandled || this.state.rangeSelecting) return;
    var bounds = this.svg && this.svg.getBoundingClientRect ? this.svg.getBoundingClientRect() : null;
    var clientX = finiteNumber(event.clientX);
    var clientY = finiteNumber(event.clientY);
    if (bounds && clientX != null && clientY != null &&
        (clientX < Number(bounds.left || 0) || clientX > Number(bounds.left || 0) + Number(bounds.width || 0) ||
         clientY < Number(bounds.top || 0) || clientY > Number(bounds.top || 0) + Number(bounds.height || 0))) return;
    var point = this._svgPoint(event);
    var visible = this._visibleMain();
    var ranges = this._rangesForVisible(visible);
    if (!point || !visible.rows.length || !ranges.length) return;
    var mapper = this._xMapper(visible, point.width);
    for (var index = ranges.length - 1; index >= 0; index -= 1) {
      var range = ranges[index];
      var left = Math.min(mapper.x(range.startIndex), mapper.x(range.endIndex));
      var right = Math.max(mapper.x(range.startIndex), mapper.x(range.endIndex));
      var top = Math.max(0, Math.min(1, Number(range.topRatio))) * point.height;
      var bottom = Math.max(top, Math.max(0, Math.min(1, Number(range.bottomRatio))) * point.height);
      var tolerance = 10;
      if (point.x < left - tolerance || point.x > right + tolerance || point.y < top - tolerance || point.y > bottom + tolerance) continue;
      event.__boardDrawingHandled = true;
      if (typeof event.preventDefault === 'function') event.preventDefault();
      if (typeof event.stopPropagation === 'function') event.stopPropagation();
      this.removeRangeSelection(index);
      return;
    }
  };

  ComparisonController.prototype._comparisonAtEndpoint = function (item, endpointIndex) {
    var rows = Array.isArray(item.comparison) ? item.comparison : [];
    var before = rows.filter(function (row) { return row.mainIndex <= endpointIndex; });
    if (before.length) return before[before.length - 1];
    return null;
  };

  ComparisonController.prototype._currentEndpoint = function (visibleRows) {
    if (!visibleRows.length) return { index: -1, timestamp: null };
    var index = visibleRows.length - 1;
    if (this.endpointPinned && this.endpointTimestamp != null) {
      var bestDistance = Infinity;
      visibleRows.forEach(function (row, rowIndex) {
        var distance = Math.abs(toMillis(row.timestamp) - this.endpointTimestamp);
        if (distance < bestDistance) { bestDistance = distance; index = rowIndex; }
      }.bind(this));
    }
    this.endpoint = index;
    return { index: index, timestamp: toMillis(visibleRows[index].timestamp) };
  };

  ComparisonController.prototype._sharedEndpoint = function (visible, endpoint) {
    var requested = endpoint && endpoint.index >= 0 ? endpoint.index : -1;
    if (requested < 0 || !this.overlays.length) return endpoint;
    var shared = requested;
    for (var pass = 0; pass <= this.overlays.length; pass += 1) {
      var next = shared;
      for (var index = 0; index < this.overlays.length; index += 1) {
        var row = this._comparisonAtEndpoint(this.overlays[index], shared);
        if (!row || finiteNumber(row.mainIndex) == null) return { index: -1, timestamp: null };
        next = Math.min(next, Number(row.mainIndex));
      }
      if (next === shared) return { index: shared, timestamp: toMillis(visible.rows[shared].timestamp) };
      shared = next;
    }
    return { index: shared, timestamp: toMillis(visible.rows[shared].timestamp) };
  };

  ComparisonController.prototype._drawPercentAxis = function (scale, width, height) {
    var group = element('g', true);
    attr(group, 'data-comparison-percent-axis', 'true');
    attr(group, 'aria-label', '收益百分比比较轴');
    var axisX = Math.max(8, Number(width) - 6);
    var axis = element('line', true);
    attr(axis, 'x1', axisX); attr(axis, 'x2', axisX); attr(axis, 'y1', scale.top); attr(axis, 'y2', height - scale.bottom);
    attr(axis, 'stroke', '#64748b'); attr(axis, 'stroke-opacity', '.46'); attr(axis, 'stroke-width', '1');
    attr(axis, 'data-comparison-percent-axis-line', 'true');
    group.appendChild(axis);

    var title = element('text', true);
    attr(title, 'x', axisX - 3); attr(title, 'y', Math.max(11, scale.top + 10));
    attr(title, 'text-anchor', 'end'); attr(title, 'font-size', '10'); attr(title, 'font-weight', '600');
    attr(title, 'fill', '#475569');
    appendText(title, '收益 %');
    group.appendChild(title);

    var ticks = [scale.max, 0, scale.min];
    var seen = {};
    ticks.forEach(function (value) {
      var y = mapPercentToY(value, scale);
      if (y == null) return;
      var key = y.toFixed(3);
      if (seen[key]) return;
      seen[key] = true;
      var tick = element('line', true);
      attr(tick, 'x1', axisX - 5); attr(tick, 'x2', axisX); attr(tick, 'y1', y); attr(tick, 'y2', y);
      attr(tick, 'stroke', '#64748b'); attr(tick, 'stroke-opacity', '.56'); attr(tick, 'stroke-width', '1');
      attr(tick, 'data-comparison-percent-tick', 'true');
      group.appendChild(tick);
      var label = element('text', true);
      attr(label, 'x', axisX - 8); attr(label, 'y', y + 3); attr(label, 'text-anchor', 'end');
      attr(label, 'font-size', '10'); attr(label, 'fill', '#475569');
      appendText(label, formatPct(value));
      group.appendChild(label);
    });
    this.svg.appendChild(group);
  };

  ComparisonController.prototype._drawRangeObject = function (range, detail, xMapper, yMapper, width, height, rangeIndex, mainLabel) {
    if (!range || !detail) return;
    var left = Math.min(xMapper.x(range.startIndex), xMapper.x(range.endIndex));
    var right = Math.max(xMapper.x(range.startIndex), xMapper.x(range.endIndex));
    var visibleLeft = left;
    var visibleWidth = right - left;
    if (visibleWidth < 8) {
      visibleLeft = Math.max(0, Math.min(width - 8, left - 4));
      visibleWidth = Math.min(8, width);
    }
    var startY = finiteNumber(range.startPrice) == null ? null : yMapper(range.startPrice);
    var endY = finiteNumber(range.endPrice) == null ? null : yMapper(range.endPrice);
    var top = startY == null || endY == null
      ? Math.max(0, Math.min(1, Number(range.topRatio))) * height
      : Math.min(startY, endY);
    var bottom = startY == null || endY == null
      ? Math.max(top, Math.max(0, Math.min(1, Number(range.bottomRatio))) * height)
      : Math.max(startY, endY);
    top = Math.max(0, Math.min(height, top));
    bottom = Math.max(top, Math.min(height, bottom));
    var group = element('g', true);
    attr(group, 'data-comparison-range-object', range.id || ('range-' + rangeIndex));
    attr(group, 'data-comparison-range-index', rangeIndex);
    attr(group, 'data-comparison-range-kline-count', detail.barCount);
    attr(group, 'aria-label', '框选区间 ' + (rangeIndex + 1));
    var movement = rangeMovementStyle(detail.main && detail.main.returnPct);
    attr(group, 'data-comparison-range-direction', movement.direction);

    var rectangle = element('rect', true);
    attr(rectangle, 'x', visibleLeft); attr(rectangle, 'y', top);
    attr(rectangle, 'width', Math.max(2, visibleWidth)); attr(rectangle, 'height', Math.max(2, bottom - top));
    attr(rectangle, 'fill', movement.color); attr(rectangle, 'fill-opacity', '.09');
    attr(rectangle, 'stroke', movement.color); attr(rectangle, 'stroke-width', '1.5');
    attr(rectangle, 'data-comparison-range', 'true'); attr(rectangle, 'pointer-events', 'none');
    group.appendChild(rectangle);

    if (startY != null && endY != null) {
      [
        { role: 'start', x: xMapper.x(range.startIndex), y: startY },
        { role: 'end', x: xMapper.x(range.endIndex), y: endY }
      ].forEach(function (endpoint) {
        var dot = element('circle', true);
        attr(dot, 'cx', endpoint.x); attr(dot, 'cy', endpoint.y); attr(dot, 'r', '4.5');
        attr(dot, 'fill', movement.color); attr(dot, 'stroke', '#ffffff'); attr(dot, 'stroke-width', '1.5');
        attr(dot, 'data-comparison-range-anchor', endpoint.role); attr(dot, 'pointer-events', 'none');
        group.appendChild(dot);
      });
    }

    var lines = [
      '区间 ' + (rangeIndex + 1) + ' · ' + formatDate(detail.startTimestamp) + ' → ' + formatDate(detail.endTimestamp),
      '自然日 ' + detail.naturalDays + ' · 交易日 ' + detail.tradingDays + ' · K线数量 ' + detail.barCount + ' 根',
      mainLabel + ' ' + formatPct(detail.main.returnPct)
    ];
    detail.overlays.forEach(function (entry) {
      var result = entry.result;
      if (!result) {
        lines.push((entry.name || entry.code || '叠加标的') + ' 暂无共同数据');
        return;
      }
      var relative = stablePercentage(Number(result.overlayReturnPct) - Number(result.mainReturnPct));
      var relativeLabel = relative >= 0
        ? '相对涨幅 ' + formatPoints(relative)
        : '相对跌幅 ' + formatPointMagnitude(relative);
      lines.push((entry.name || entry.code || '叠加标的') + ' ' + formatPct(result.overlayReturnPct) + ' · ' + relativeLabel);
    });

    var maxCharacters = lines.reduce(function (max, line) { return Math.max(max, String(line).length); }, 0);
    var labelWidth = Math.max(120, maxCharacters * 6.1 + 14);
    labelWidth = Math.min(labelWidth, Math.max(40, width - 8));
    var labelHeight = lines.length * 14 + 8;
    var labelX = visibleLeft + 6;
    if (labelX + labelWidth > width - 4) labelX = Math.max(4, width - labelWidth - 4);
    var labelY = top + 4;
    if (labelY + labelHeight > height - 4) labelY = Math.max(4, bottom - labelHeight - 4);

    var labelBackground = element('rect', true);
    attr(labelBackground, 'x', labelX); attr(labelBackground, 'y', labelY);
    attr(labelBackground, 'width', labelWidth); attr(labelBackground, 'height', labelHeight);
    attr(labelBackground, 'rx', '3'); attr(labelBackground, 'fill', '#ffffff'); attr(labelBackground, 'fill-opacity', '.9');
    attr(labelBackground, 'stroke', movement.color); attr(labelBackground, 'stroke-opacity', '.65');
    attr(labelBackground, 'data-comparison-range-label-background', 'true'); attr(labelBackground, 'pointer-events', 'none');
    group.appendChild(labelBackground);

    lines.forEach(function (line, index) {
      var label = element('text', true);
      attr(label, 'x', labelX + 6); attr(label, 'y', labelY + 14 + index * 14);
      attr(label, 'font-size', '10'); attr(label, 'fill', index === 2 ? movement.color : (index === 0 ? '#17263a' : '#334155'));
      attr(label, 'font-weight', index === 0 || index === 2 ? '600' : '400');
      attr(label, 'data-comparison-range-label', 'true'); attr(label, 'pointer-events', 'none');
      appendText(label, line);
      group.appendChild(label);
    });
    this.svg.appendChild(group);
  };

  ComparisonController.prototype._draw = function () {
    if (!this.svg || !this.mainDom) return;
    this._clearSvg();
    setStyle(this.svg, {
      pointerEvents: this.state.rangeSelecting ? 'auto' : 'none',
      cursor: this.state.rangeSelecting ? 'crosshair' : 'default'
    });
    var visible = this._visibleMain();
    if (!visible.rows.length) return;
    var width = this.mainDom.clientWidth || (this.mainDom.getBoundingClientRect ? this.mainDom.getBoundingClientRect().width : 0) || 1;
    var height = this.mainDom.clientHeight || (this.mainDom.getBoundingClientRect ? this.mainDom.getBoundingClientRect().height : 0) || 1;
    attr(this.svg, 'viewBox', '0 0 ' + width + ' ' + height);
    var endpoint = this._currentEndpoint(visible.rows);
    var drawEndpoint = endpoint;
    var xMapper = this._xMapper(visible, width);
    var x = xMapper.x;
    var visibleIndex = {};
    visible.rows.forEach(function (row, index) { visibleIndex[toMillis(row.timestamp)] = index; });
    var extent = computeEquivalentPriceExtent(visible.rows, this.overlays);
    this._syncScaleIndicator(extent);
    var fallbackScale = computePriceScale(extent, height, { top: 14, bottom: 26, paddingRatio: 0.08 });
    var y = function (price) {
      var converted = this._convertMainY(price);
      return converted == null ? mapPriceToY(price, fallbackScale) : converted;
    }.bind(this);

    var ranges = this._rangesForVisible(visible);
    var mainContext = this._getContext();
    var mainLabel = mainContext.name || mainContext.code || '主图';
    var period = periodToApi(mainContext.period);
    ranges.forEach(function (range, index) {
      var details = computeRangeDetails(visible.rows, this.overlays, period, range.startIndex, range.endIndex);
      this._drawRangeObject(range, details, xMapper, y, width, height, index, mainLabel);
    }.bind(this));

    var draft = this.state.rangeSelecting ? this.state.rangeDraft : null;
    if (draft && draft.startIndex != null) {
      draft.startIndex = this._rangeDraftIndexForTimestamp(visible, draft.startTimestamp, draft.startIndex);
      draft.endIndex = this._rangeDraftIndexForTimestamp(visible, draft.endTimestamp, draft.endIndex);
      var rangeStart = Math.min(draft.startIndex, draft.endIndex);
      var rangeEnd = Math.max(draft.startIndex, draft.endIndex);
      var draftStartPrice = finiteNumber(visible.rows[draft.startIndex] && visible.rows[draft.startIndex][draft.startPriceField || 'close']);
      var draftEndPrice = finiteNumber(visible.rows[draft.endIndex] && visible.rows[draft.endIndex][draft.endPriceField || 'close']);
      var draftStartY = draftStartPrice == null ? Number(draft.startYRatio) * height : y(draftStartPrice);
      var draftEndY = draftEndPrice == null ? Number(draft.endYRatio) * height : y(draftEndPrice);
      var rangeTop = Math.max(0, Math.min(height, Math.min(draftStartY, draftEndY)));
      var rangeBottom = Math.max(rangeTop, Math.min(height, Math.max(draftStartY, draftEndY)));
      var rangeRect = element('rect', true);
      var draftDetails = computeRangeDetails(visible.rows, [], period, rangeStart, rangeEnd);
      var draftMovement = rangeMovementStyle(draftDetails && draftDetails.main && draftDetails.main.returnPct);
      var draftLeft = Math.min(x(rangeStart), x(rangeEnd));
      var draftWidth = Math.abs(x(rangeEnd) - x(rangeStart));
      if (draftWidth < 8) {
        draftLeft = Math.max(0, Math.min(width - 8, draftLeft - 4));
        draftWidth = Math.min(8, width);
      }
      attr(rangeRect, 'x', draftLeft);
      attr(rangeRect, 'y', rangeTop);
      attr(rangeRect, 'width', Math.max(2, draftWidth));
      attr(rangeRect, 'height', Math.max(2, rangeBottom - rangeTop));
      attr(rangeRect, 'fill', draftMovement.color);
      attr(rangeRect, 'fill-opacity', '.08');
      attr(rangeRect, 'stroke', draftMovement.color);
      attr(rangeRect, 'stroke-width', '1.5');
      attr(rangeRect, 'data-comparison-range', 'true');
      attr(rangeRect, 'pointer-events', 'none');
      this.svg.appendChild(rangeRect);
      [
        { role: 'start', x: x(draft.startIndex), y: draftStartY },
        { role: 'end', x: x(draft.endIndex), y: draftEndY }
      ].forEach(function (endpoint) {
        var dot = element('circle', true);
        attr(dot, 'cx', endpoint.x); attr(dot, 'cy', endpoint.y); attr(dot, 'r', '4.5');
        attr(dot, 'fill', draftMovement.color); attr(dot, 'stroke', '#ffffff'); attr(dot, 'stroke-width', '1.5');
        attr(dot, 'data-comparison-range-anchor', endpoint.role); attr(dot, 'pointer-events', 'none');
        this.svg.appendChild(dot);
      }.bind(this));
    }

    if (this.state.rangeSelecting) {
      var interaction = element('rect', true);
      attr(interaction, 'x', 0); attr(interaction, 'y', 0); attr(interaction, 'width', width); attr(interaction, 'height', height);
      attr(interaction, 'fill', 'transparent');
      attr(interaction, 'data-comparison-range-interaction', 'true');
      attr(interaction, 'pointer-events', 'all');
      if (typeof interaction.addEventListener === 'function') {
        interaction.addEventListener('click', this._onRangeClick.bind(this));
        interaction.addEventListener('wheel', function () {
          this._scheduleRender();
        }.bind(this), { passive: true });
      }
      this.svg.appendChild(interaction);
    }

    if (!this.overlays.length) {
      this._syncScaleIndicator(null);
      this._renderSummary(visible, -1, null);
      this._renderPanelOverlays();
      return;
    }

    var firstComparison = this.overlays.reduce(function (found, item) {
      if (found) return found;
      return item && Array.isArray(item.comparison) && item.comparison.length ? item.comparison[0] : null;
    }, null);
    var baselinePrice = finiteNumber(firstComparison && firstComparison.main && firstComparison.main.close);
    if (baselinePrice == null) baselinePrice = finiteNumber(visible.rows[0] && visible.rows[0].close);
    if (baselinePrice == null) return;

    var baseline = element('line', true);
    attr(baseline, 'x1', xMapper.left); attr(baseline, 'x2', xMapper.right); attr(baseline, 'y1', y(baselinePrice)); attr(baseline, 'y2', y(baselinePrice));
    attr(baseline, 'stroke', '#8794a8'); attr(baseline, 'stroke-dasharray', '4 4'); attr(baseline, 'stroke-width', '1');
    attr(baseline, 'data-comparison-baseline', 'true');
    attr(baseline, 'data-comparison-base-price', baselinePrice);
    attr(baseline, 'pointer-events', 'none');
    this.svg.appendChild(baseline);

    var selection = element('rect', true);
    attr(selection, 'x', x(0)); attr(selection, 'y', 0); attr(selection, 'width', Math.max(0, x(drawEndpoint.index) - x(0))); attr(selection, 'height', height);
    attr(selection, 'fill', '#5b8def'); attr(selection, 'fill-opacity', '.07'); attr(selection, 'pointer-events', 'none');
    this.svg.appendChild(selection);

    this.overlays.forEach(function (item) {
      var baseMainClose = finiteNumber(item.comparison && item.comparison[0] && item.comparison[0].main && item.comparison[0].main.close);
      if (baseMainClose == null || baseMainClose <= 0) return;
      var points = (item.comparison || []).map(function (row) {
        var index = finiteNumber(row.mainIndex);
        if (index == null) index = visibleIndex[row.timestamp];
        if (index == null) return null;
        var equivalentPrice = finiteNumber(row.equivalentPrice);
        if (equivalentPrice == null) equivalentPrice = returnPctToEquivalentPrice(baseMainClose, row.overlayReturnPct);
        if (equivalentPrice == null) return null;
        return { x: x(index), y: y(equivalentPrice), equivalentPrice: equivalentPrice };
      }).filter(Boolean);
      if (!points.length) return;
      var line = element('path', true);
      attr(line, 'd', pathFor(points));
      attr(line, 'fill', 'none');
      attr(line, 'stroke', item.color);
      attr(line, 'stroke-width', '1.9');
      attr(line, 'data-overlay-code', item.code);
      attr(line, 'data-series', 'overlay');
      attr(line, 'data-source', 'close-return-equivalent-price');
      attr(line, 'data-base-main-close', baseMainClose);
      attr(line, 'data-end-equivalent-price', points[points.length - 1].equivalentPrice);
      attr(line, 'pointer-events', 'none');
      this.svg.appendChild(line);
    }.bind(this));

    if (drawEndpoint.index >= 0) {
      var endLine = element('line', true);
      attr(endLine, 'x1', x(drawEndpoint.index)); attr(endLine, 'x2', x(drawEndpoint.index)); attr(endLine, 'y1', 0); attr(endLine, 'y2', height);
      attr(endLine, 'stroke', '#64748b'); attr(endLine, 'stroke-dasharray', '4 4'); attr(endLine, 'stroke-width', '1'); attr(endLine, 'pointer-events', 'none');
      attr(endLine, 'data-comparison-endpoint-line', 'true');
      this.svg.appendChild(endLine);
      var handle = element('circle', true);
      attr(handle, 'cx', x(drawEndpoint.index)); attr(handle, 'cy', y(baselinePrice)); attr(handle, 'r', '6'); attr(handle, 'fill', '#64748b'); attr(handle, 'stroke', '#fff'); attr(handle, 'stroke-width', '2'); attr(handle, 'pointer-events', this.state.rangeSelecting ? 'none' : 'auto'); attr(handle, 'data-comparison-endpoint', 'true');
      var hit = element('circle', true);
      attr(hit, 'cx', x(drawEndpoint.index)); attr(hit, 'cy', y(baselinePrice)); attr(hit, 'r', '14'); attr(hit, 'fill', 'transparent'); attr(hit, 'pointer-events', this.state.rangeSelecting ? 'none' : 'all'); attr(hit, 'data-comparison-endpoint-hit', 'true');
      var beginEndpointDrag = function (event) {
        if (event && event.button != null && event.button !== 0) return;
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
        if (event) event.__boardDrawingHandled = true;
        this.dragging = true;
      }.bind(this);
      if (typeof hit.addEventListener === 'function') hit.addEventListener('mousedown', beginEndpointDrag);
      if (typeof handle.addEventListener === 'function') {
        handle.addEventListener('click', function (event) { event.stopPropagation(); this._setEndpointFromEvent(event); }.bind(this));
        handle.addEventListener('mousedown', beginEndpointDrag);
      }
      this.svg.appendChild(hit);
      this.svg.appendChild(handle);
    }
    this._renderSummary(visible, endpoint.index, null);
    this._renderPanelOverlays();
    visibleIndex = null;
  };

  ComparisonController.prototype._renderSummary = function (visible, endpointIndex, rangeSelection) {
    if (!this.summary) return;
    clearChildren(this.summary);
    if (!this.overlays.length) return;
    var heading = element('div');
    var mainContext = this._getContext();
    var mainLabel = mainContext.name || mainContext.code || '主图';
    var endpoint = endpointIndex >= 0 && visible.rows[endpointIndex]
      ? { index: endpointIndex, timestamp: toMillis(visible.rows[endpointIndex].timestamp) }
      : { index: -1, timestamp: null };
    appendText(heading, '叠加对比 · 主图 ' + mainLabel + ' · 对比至 ' + (endpoint.index >= 0 ? formatDate(endpoint.timestamp) : '暂无数据'));
    setStyle(heading, { fontWeight: '600', marginBottom: '3px' });
    this.summary.appendChild(heading);
    this.overlays.forEach(function (item) {
      var row = element('div');
      setStyle(row, { display: 'flex', alignItems: 'center', gap: '5px', minHeight: '20px' });
      var swatch = element('span');
      setStyle(swatch, { display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', background: item.color, flex: '0 0 auto' });
      row.appendChild(swatch);
      var end = endpointIndex >= 0 ? this._comparisonAtEndpoint(item, endpointIndex) : null;
      var label = item.name || item.code;
      var relative = end ? stablePercentage(Number(end.overlayReturnPct) - Number(end.mainReturnPct)) : null;
      var relativeLabel = relative == null
        ? ''
        : (relative >= 0 ? '相对涨幅 ' + formatPoints(relative) : '相对跌幅 ' + formatPointMagnitude(relative));
      var endDate = end && end.timestamp != null ? end.timestamp : (end && end.endTimestamp);
      var text = end
        ? label + ' ' + formatPct(end.overlayReturnPct) + ' · 主图 ' + formatPct(end.mainReturnPct) + ' · ' + relativeLabel + (endDate != null ? ' · 截至 ' + formatDate(endDate) : '')
        : label + ' ' + (item.loading ? '加载中...' : (item.error || '暂无共同数据'));
      var content = element('span');
      appendText(content, text);
      setStyle(content, { color: '#17263a' });
      row.appendChild(content);
      var remove = element('button');
      remove.type = 'button';
      appendText(remove, '×');
      remove.title = '移除 ' + label;
      remove.setAttribute('aria-label', '移除 ' + label);
      setStyle(remove, { marginLeft: '4px', padding: '0 3px', border: '0', background: 'transparent', color: '#64748b', cursor: 'pointer' });
      if (typeof remove.addEventListener === 'function') remove.addEventListener('click', function (event) { event.stopPropagation(); this.clearOverlay(item.code); }.bind(this));
      row.appendChild(remove);
      this.summary.appendChild(row);
    }.bind(this));
  };

  ComparisonController.prototype._renderPanelOverlays = function () {
    if (!this.legend) return;
    var rangeSelect = this.rangeTool || (doc && doc.getElementById ? doc.getElementById('chart-comparison-range-select') : null);
    if (rangeSelect) {
      if (!rangeSelect.dataset) rangeSelect.dataset = {};
      rangeSelect.dataset.state = this.state.rangeSelecting ? 'selecting' : 'idle';
      rangeSelect.title = this.state.rangeSelecting
        ? (this.state.rangeDragging ? '移动鼠标预览，第二次点击确定终点；按 Esc 取消' : '第一次点击确定起点；按 Esc 取消')
        : '框选区间（按收盘价计算）';
    }
    clearChildren(this.legend);
    if (!this.overlays.length) {
      appendText(this.legend, '尚未添加叠加标的');
      return;
    }
    this.overlays.forEach(function (item) {
      var row = element('div');
      setStyle(row, { display: 'flex', alignItems: 'center', gap: '5px', minHeight: '24px' });
      var swatch = element('span');
      setStyle(swatch, { width: '9px', height: '9px', borderRadius: '50%', background: item.color, display: 'inline-block', flex: '0 0 auto' });
      row.appendChild(swatch);
      var name = element('span');
      appendText(name, item.name || item.code);
      setStyle(name, { flex: '1 1 auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
      row.appendChild(name);
      var state = element('span');
      appendText(state, item.loading ? '加载中' : (item.error ? '失败' : '已载入'));
      setStyle(state, { fontSize: '11px', opacity: '.7' });
      row.appendChild(state);
      var remove = element('button');
      remove.type = 'button';
      appendText(remove, '×');
      remove.title = '移除 ' + (item.name || item.code);
      remove.setAttribute('aria-label', '移除 ' + (item.name || item.code));
      setStyle(remove, { padding: '0 4px', border: '0', background: 'transparent', color: '#64748b', cursor: 'pointer' });
      if (typeof remove.addEventListener === 'function') remove.addEventListener('click', function () { this.clearOverlay(item.code); }.bind(this));
      row.appendChild(remove);
      this.legend.appendChild(row);
    }.bind(this));
  };

  ComparisonController.prototype._setStatus = function (message) {
    var status = doc && doc.getElementById ? doc.getElementById('chart-comparison-status') : null;
    if (status) appendText(status, message);
  };

  ComparisonController.prototype._setEndpointFromEvent = function (event) {
    if (!this.svg) return;
    var visible = this._visibleMain();
    if (!visible.rows.length) return;
    var rect = this.svg.getBoundingClientRect ? this.svg.getBoundingClientRect() : { left: 0, width: this.mainDom.clientWidth || 1 };
    var position = Number(event.clientX) - Number(rect.left || 0);
    var mapper = this._xMapper(visible, Number(rect.width || this.mainDom.clientWidth || 1));
    var index = mapper.indexAtX(position);
    index = clampEndpoint(index, 0, visible.rows.length - 1);
    this.endpoint = index;
    this.endpointTimestamp = toMillis(visible.rows[index].timestamp);
    this.endpointPinned = true;
    this._draw();
  };

  ComparisonController.prototype._onDocumentMouseMove = function (event) {
    if (this.summaryDrag) {
      this._moveSummary(event);
      return;
    }
    if (this.state.rangeSelecting) {
      this._onRangeMouseMove(event);
      return;
    }
    if (this.dragging) this._setEndpointFromEvent(event);
  };

  ComparisonController.prototype._onDocumentMouseUp = function (event) {
    if (this.summaryDrag) {
      if (event && finiteNumber(event.clientX) != null && finiteNumber(event.clientY) != null) {
        this._moveSummary(event);
      }
      this.summaryDrag = null;
      return;
    }
    if (this.state.rangeSelecting) {
      return;
    }
    if (this.dragging && event && finiteNumber(event.clientX) != null) this._setEndpointFromEvent(event);
    this.dragging = false;
  };

  ComparisonController.prototype._scheduleRender = function () {
    if (this.renderTimer) clearTimeout(this.renderTimer);
    this.renderTimer = setTimeout(function () { this.renderTimer = null; this._draw(); }.bind(this), 40);
  };

  ComparisonController.prototype._onVisibleRange = function () {
    if (!this.overlays.length) {
      if (this.state.rangeSelection || this.state.rangeSelecting) this._scheduleRender();
      return;
    }
    this._recomputeVisible();
    this._scheduleRender();
    var visible = this._visibleMain();
    if (!visible.rows.length) return;
    var visibleFrom = toMillis(visible.rows[0].timestamp);
    var visibleTo = toMillis(visible.rows[visible.rows.length - 1].timestamp);
    var needsExpandedWindow = this.overlays.some(function (item) {
      if (!item || item.loading) return false;
      var requestedFrom = finiteNumber(item.requestedFrom);
      var requestedTo = finiteNumber(item.requestedTo);
      return (requestedFrom != null && visibleFrom < requestedFrom)
        || (requestedTo != null && visibleTo > requestedTo);
    });
    if (needsExpandedWindow) this._scheduleLoad(80);
  };

  ComparisonController.prototype._recomputeVisible = function () {
    var visible = this._visibleMain();
    var context = this._getContext();
    var period = periodToApi(context.period);
    var indexByTimestamp = {};
    visible.rows.forEach(function (row, index) { indexByTimestamp[toMillis(row.timestamp)] = index; });
    this.overlays.forEach(function (item) {
      var aligned = alignSeries(visible.rows, item.rows, period);
      item.comparison = computeComparison(aligned).map(function (row) {
        var copy = {};
        Object.keys(row).forEach(function (key) { copy[key] = row[key]; });
        copy.mainIndex = indexByTimestamp[row.timestamp];
        return copy;
      });
    });
    return this.overlays.length ? this.overlays[0].comparison : [];
  };

  ComparisonController.prototype._scheduleLoad = function (delay) {
    if (this.loadTimer) clearTimeout(this.loadTimer);
    this.loadTimer = setTimeout(function () {
      this.loadTimer = null;
      this._loadAll();
    }.bind(this), delay == null ? 100 : delay);
  };

  ComparisonController.prototype._scheduleRetry = function (item, seq) {
    if (item.retryTimer) clearTimeout(item.retryTimer);
    item.retryTimer = setTimeout(function () {
      item.retryTimer = null;
      if (item.requestSeq === seq && this.overlays.indexOf(item) >= 0) this._loadOverlay(item, true);
    }.bind(this), 650);
  };

  ComparisonController.prototype._loadAll = function () {
    if (!this.overlays.length) return;
    this.lastContextKey = this._contextKey();
    this.overlays.slice().forEach(function (item) { this._loadOverlay(item); }.bind(this));
    this._renderPanelOverlays();
  };

  ComparisonController.prototype._loadOverlay = function (item, isRetry) {
    var self = this;
    if (!item || !this.chart) return;
    var data = this.replayHistory && this.replayHistory.length
      ? this.replayHistory
      : (typeof this.chart.getDataList === 'function' ? this.chart.getDataList() : []);
    var mainRows = Array.isArray(data) ? data : [];
    if (!mainRows.length) {
      item.loading = false;
      item.error = null;
      return;
    }
    if (item.abort) { try { item.abort.abort(); } catch (e) {} }
    if (item.retryTimer) { clearTimeout(item.retryTimer); item.retryTimer = null; }
    item.abort = typeof global.AbortController !== 'undefined' ? new global.AbortController() : null;
    var seq = ++item.requestSeq;
    this.requestSeq += 1;
    if (!isRetry) item.retryCount = 0;
    item.loading = true;
    item.error = null;
    var requestedRows = validRows(mainRows);
    item.requestedFrom = requestedRows.length ? requestedRows[0].timestamp : null;
    item.requestedTo = requestedRows.length ? requestedRows[requestedRows.length - 1].timestamp : null;
    this._setStatus('叠加数据加载中（' + this.overlays.length + ' 项）...');
    var options = item.abort ? { signal: item.abort.signal } : undefined;
    var request = typeof global.fetch === 'function' ? global.fetch(this._buildKlineUrl(item, mainRows), options) : Promise.reject(new Error('fetch unavailable'));
    request.then(function (response) {
      return response.json().then(function (payload) { return { response: response, payload: payload || {} }; });
    }).then(function (result) {
      if (seq !== item.requestSeq || (item.abort && item.abort.signal.aborted)) return;
      var payload = result.payload || {};
      if ((result.response && result.response.status === 202) || payload.loading) {
        item.retryCount += 1;
        if (item.retryCount <= MAX_LOADING_RETRIES) {
          self._setStatus('叠加数据正在更新，请稍候...');
          self._scheduleRetry(item, seq);
          return;
        }
        throw new Error('loading timeout');
      }
      if (payload.error) throw new Error(payload.error);
      item.rows = Array.isArray(payload.data) ? payload.data : (Array.isArray(payload.rows) ? payload.rows : []);
      item.loading = false;
      item.error = null;
      item.abort = null;
      item.retryCount = 0;
      self._recomputeVisible();
      self._draw();
      self._setStatus('已载入 ' + self.overlays.length + ' 个叠加标的');
    }).catch(function (error) {
      if (error && error.name === 'AbortError') return;
      if (seq !== item.requestSeq) return;
      item.loading = false;
      item.error = (error && error.message) || '加载失败';
      item.abort = null;
      item.comparison = [];
      self._renderPanelOverlays();
      self._draw();
      self._setStatus('部分叠加数据加载失败');
    });
  };

  ComparisonController.prototype.selectOverlay = function (item) {
    if (!item) return false;
    var context = this._getContext();
    var code = String(item.code || item.ticker || item.symbol || item.display_code || '');
    if (!code) return false;
    if (symbolIdentity(code) === symbolIdentity(context.code)) {
      this._setStatus('不能叠加当前主标的');
      return false;
    }
    if (this._findOverlay(code)) {
      this._setStatus('该标的已经叠加');
      return false;
    }
    if (this.overlays.length >= MAX_OVERLAYS) {
      this._setStatus('最多可同时叠加 ' + MAX_OVERLAYS + ' 个标的');
      return false;
    }
    this.overlays.push(this._createOverlay({ code: code, name: item.name || item.shortName || code, type: item.type || 'index' }));
    this.endpoint = null;
    this.endpointTimestamp = null;
    this.endpointPinned = false;
    this.lastContextKey = this._contextKey();
    if (this.button && this.button.classList) this.button.classList.add('active');
    if (this.button) {
      this.button.title = '已叠加 ' + this.overlays.length + ' 个标的';
      appendText(this.button, '叠加 ' + this.overlays.length);
    }
    this._renderPanelOverlays();
    this._loadOverlay(this.overlays[this.overlays.length - 1]);
    return true;
  };

  ComparisonController.prototype._cancelOverlay = function (item) {
    if (!item) return;
    item.requestSeq += 1;
    if (item.abort) { try { item.abort.abort(); } catch (e) {} }
    if (item.retryTimer) clearTimeout(item.retryTimer);
    item.abort = null;
    item.retryTimer = null;
    item.loading = false;
  };

  ComparisonController.prototype.clearOverlay = function (code) {
    this.clearRangeSelection();
    if (code == null || code === '') {
      this.overlays.slice().forEach(this._cancelOverlay.bind(this));
      this.overlays.length = 0;
    } else {
      var target = this._findOverlay(code);
      if (!target) return false;
      this._cancelOverlay(target);
      this.overlays.splice(this.overlays.indexOf(target), 1);
    }
    if (!this.overlays.length) {
      this.endpoint = null;
      this.endpointTimestamp = null;
      this.endpointPinned = false;
    }
    if (this.button && this.button.classList) {
      if (this.overlays.length) this.button.classList.add('active');
      else this.button.classList.remove('active');
    }
    if (this.button) {
      this.button.title = this.overlays.length ? '已叠加 ' + this.overlays.length + ' 个标的' : '添加一个或多个标的进行涨跌幅比较';
      appendText(this.button, this.overlays.length ? '叠加 ' + this.overlays.length : '叠加');
    }
    this._renderPanelOverlays();
    this._draw();
    return true;
  };

  ComparisonController.prototype.addOverlay = function (item) {
    return this.selectOverlay(item);
  };

  ComparisonController.prototype.removeOverlay = function (code) {
    return this.clearOverlay(code);
  };

  ComparisonController.prototype.clearOverlays = function () {
    return this.clearOverlay();
  };

  ComparisonController.prototype.setEndpointIndex = function (index) {
    var visible = this._visibleMain();
    if (!visible.rows.length) return false;
    var resolved = clampEndpoint(index, 0, visible.rows.length - 1);
    this.endpoint = resolved;
    this.endpointTimestamp = toMillis(visible.rows[resolved].timestamp);
    this.endpointPinned = true;
    this._draw();
    return true;
  };

  ComparisonController.prototype._bindChart = function (chart) {
    if (this.chart && chart !== this.chart) this._removeScaleIndicator(this.chart);
    if (this.chart && this._chartUnbind) this._chartUnbind();
    this.chart = chart;
    this._chartUnbind = null;
    if (!chart) return;
    var action = global.klinecharts && global.klinecharts.ActionType;
    var visibleAction = action && action.OnVisibleRangeChange ? action.OnVisibleRangeChange : 'onVisibleRangeChange';
    var handler = this._onVisibleRange.bind(this);
    if (typeof chart.subscribeAction === 'function') {
      chart.subscribeAction(visibleAction, handler);
      this._chartUnbind = function () { try { chart.unsubscribeAction(visibleAction, handler); } catch (e) {} };
    }
    try { this.mainDom = chart.getDom('candle_pane', global.klinecharts && global.klinecharts.DomPosition ? global.klinecharts.DomPosition.Main : undefined); } catch (e) { this.mainDom = null; }
    if (!this.mainDom) { try { this.mainDom = chart.getDom('candle_pane'); } catch (e) {} }
    this._ensureOverlayDom();
    this._ensureUi();
    this._recomputeVisible();
    this._scheduleRender();
  };

  ComparisonController.prototype.init = function (chart) {
    if (this.initialized && chart && chart !== this.chart) this._bindChart(chart);
    if (this.initialized) return this;
    this.initialized = true;
    this._bindChart(chart || global.__kline_chart || null);
    this._ensureUi();
    this._listen(global, 'kline-chart-ready', function (event) {
      this._bindChart(event.detail || global.__kline_chart);
      if (this.overlays.length) this._scheduleLoad(0);
    }.bind(this));
    this._listen(global, 'select-symbol', function () {
      this.clearRangeSelection();
      if (this.overlays.length) this._scheduleLoad(120);
    }.bind(this));
    this._listen(global, 'refresh-current-symbol', function () {
      if (this.overlays.length) this._scheduleLoad(0);
    }.bind(this));
    this._listen(global, 'period-change', function () {
      this.clearRangeSelection();
      if (this.overlays.length) this._scheduleLoad(0);
    }.bind(this));
    this._listen(global, 'kline-loaded', function () {
      if (!this.overlays.length) {
        if (this.state.rangeSelection || this.state.rangeSelecting) this._scheduleRender();
        return;
      }
      if (this._contextKey() !== this.lastContextKey) {
        this.clearRangeSelection();
        this._scheduleLoad(0);
      }
      else { this._recomputeVisible(); this._scheduleRender(); }
    }.bind(this));
    this._listen(global, 'bar-replay-start', function (event) {
      var detail = event && event.detail ? event.detail : {};
      this.replayHistory = validRows(detail.history || []);
      this._recomputeVisible();
      this._scheduleRender();
    }.bind(this));
    this._listen(global, 'bar-replay-cursor', function () {
      /* Replay mutates the chart data list before publishing the cursor event. */
      this._recomputeVisible();
      this._scheduleRender();
    }.bind(this));
    this._listen(global, 'bar-replay-exit', function () {
      /* Replay restores the full chart immediately after publishing EXIT. */
      setTimeout(function () {
        this.replayHistory = null;
        this._recomputeVisible();
        this._scheduleRender();
      }.bind(this), 0);
    }.bind(this));
    this._listen(doc, 'mousemove', this._onDocumentMouseMove.bind(this));
    this._listen(doc, 'mouseup', this._onDocumentMouseUp.bind(this));
    this._listen(doc, 'contextmenu', this._onDocumentContextMenu.bind(this), true);
    this._listen(doc, 'keydown', function (event) {
      if (event && event.key === 'Escape' && this.state.rangeSelecting) {
        if (typeof event.preventDefault === 'function') event.preventDefault();
        this._cancelRangeSelection();
      }
    }.bind(this));
    return this;
  };

  ComparisonController.prototype.destroy = function () {
    this.listeners.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    this.listeners = [];
    if (this._chartUnbind) this._chartUnbind();
    this._chartUnbind = null;
    if (this.searchAbort) { try { this.searchAbort.abort(); } catch (e) {} }
    this._removeScaleIndicator(this.chart);
    this.overlays.slice().forEach(this._cancelOverlay.bind(this));
    if (this.searchTimer) clearTimeout(this.searchTimer);
    if (this.loadTimer) clearTimeout(this.loadTimer);
    if (this.renderTimer) clearTimeout(this.renderTimer);
    if (this.button && this.button.parentNode) this.button.parentNode.removeChild(this.button);
    if (this.rangeTool && this.rangeTool.parentNode) this.rangeTool.parentNode.removeChild(this.rangeTool);
    if (this.panel && this.panel.parentNode) this.panel.parentNode.removeChild(this.panel);
    if (this.svg && this.svg.parentNode) this.svg.parentNode.removeChild(this.svg);
    if (this.summary && this.summary.parentNode) this.summary.parentNode.removeChild(this.summary);
    this.overlays.length = 0;
    this.replayHistory = null;
    this.button = this.rangeTool = this.panel = this.svg = this.summary = this.legend = null;
    this.initialized = false;
    this.chart = null;
    this.mainDom = null;
  };

  var controller = new ComparisonController();

  /* Public pure helpers are intentionally kept for the existing test surface. */
  controller.periodToApi = periodToApi;
  controller.alignSeries = alignSeries;
  controller.computeReturns = computeReturns;
  controller.normalizeSeries = normalizeSeries;
  controller.computeComparison = computeComparison;
  controller.computeRangeComparison = computeRangeComparison;
  controller.computeMainRange = computeMainRange;
  controller.computePercentScale = computePercentScale;
  controller.mapPercentToY = mapPercentToY;
  controller.returnPctToEquivalentPrice = returnPctToEquivalentPrice;
  controller.computeEquivalentPriceExtent = computeEquivalentPriceExtent;
  controller.computePriceScale = computePriceScale;
  controller.mapPriceToY = mapPriceToY;
  controller.computeRangeDetails = computeRangeDetails;
  controller.clampEndpoint = clampEndpoint;
  controller.createSearchUrl = createSearchUrl;
  controller.buildKlineUrl = function (item, rows) { return controller._buildKlineUrl(item, rows); };

  /* Compatibility getters: old callers see the first item, while state owns all items. */
  Object.defineProperties(controller, {
    overlay: {
      configurable: true,
      get: function () { return this.overlays[0] || null; },
      set: function (value) {
        if (value == null) { this.clearOverlay(); return; }
        var first = this.overlays[0];
        if (!first) { first = this._createOverlay(value); this.overlays.unshift(first); }
        else {
          first.code = String(value.code || value.ticker || first.code);
          first.name = value.name || value.shortName || first.name;
          first.type = value.type || first.type;
        }
      }
    },
    overlayRows: {
      configurable: true,
      get: function () { return this.overlays[0] ? this.overlays[0].rows : []; },
      set: function (rows) { if (this.overlays[0]) this.overlays[0].rows = Array.isArray(rows) ? rows : []; }
    },
    comparison: {
      configurable: true,
      get: function () { return this.overlays[0] ? this.overlays[0].comparison : []; },
      set: function (rows) { if (this.overlays[0]) this.overlays[0].comparison = Array.isArray(rows) ? rows : []; }
    },
    comparisons: {
      configurable: true,
      get: function () {
        return this.overlays.map(function (item) {
          return {
            code: item.code,
            name: item.name,
            type: item.type,
            color: item.color,
            rows: item.comparison,
            comparison: item.comparison,
            overlay: item
          };
        });
      }
    },
    fetchAbort: {
      configurable: true,
      get: function () { return this.overlays[0] ? this.overlays[0].abort : null; }
    },
    rangeSelection: {
      configurable: true,
      get: function () { return this.state.rangeSelection; }
    },
    rangeSelections: {
      configurable: true,
      get: function () { return this.state.rangeSelections; }
    },
    rangeSelecting: {
      configurable: true,
      get: function () { return !!this.state.rangeSelecting; }
    },
    rangeDraft: {
      configurable: true,
      get: function () { return this.state.rangeDraft; }
    }
  });

  global.ChartComparisonController = controller;

  if (doc && typeof doc.addEventListener === 'function') {
    var start = function () { controller.init(global.__kline_chart || null); };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start);
    else setTimeout(start, 0);
  }
}(typeof window !== 'undefined' ? window : globalThis));
