(function (global) {
  'use strict';

  var state = {
    chart: null,
    enabled: false,
    button: null,
    eventsBound: false,
    wheelDom: null,
    wheelHandler: null,
    zoomBaseRange: null,
  };

  var MIN_ZOOM_RATIO = 0.05;
  var MAX_ZOOM_RATIO = 20;
  var WHEEL_SENSITIVITY = 0.0015;

  function getDocument() {
    return global.document || null;
  }

  function getChart(candidate) {
    if (candidate && typeof candidate.getDrawPaneById === 'function') return candidate;
    if (state.chart && typeof state.chart.getDrawPaneById === 'function') return state.chart;
    if (global.__kline_chart && typeof global.__kline_chart.getDrawPaneById === 'function') {
      return global.__kline_chart;
    }
    return null;
  }

  function getAxis(chart) {
    var pane = null;
    try {
      pane = chart && chart.getDrawPaneById('candle_pane');
      if (!pane && chart && typeof chart.getCandlePane === 'function') pane = chart.getCandlePane();
    } catch (e) {}
    if (!pane || typeof pane.getAxisComponent !== 'function') return null;
    try { return pane.getAxisComponent(); } catch (e) { return null; }
  }

  function updateButton() {
    var button = state.button;
    if (!button) return;
    button.classList.toggle('active', state.enabled);
    button.setAttribute('aria-pressed', state.enabled ? 'true' : 'false');
    button.title = state.enabled
      ? '关闭二维平移缩放并恢复价格轴自动适配'
      : '开启二维平移缩放（拖动平移，滚轮同步缩放）';
    button.setAttribute('aria-label', button.title);
  }

  function redraw(chart, recalculateAxis) {
    try {
      if (typeof chart.adjustPaneViewport === 'function') {
        chart.adjustPaneViewport(false, true, false, recalculateAxis === true, recalculateAxis === true);
        if (typeof chart.updatePane === 'function') chart.updatePane(4);
      } else if (typeof chart.resize === 'function') {
        chart.resize();
      }
    } catch (e) {}
  }

  function getMainDom(chart) {
    if (!chart || typeof chart.getDom !== 'function') return null;
    try {
      var position = global.klinecharts && global.klinecharts.DomPosition
        ? global.klinecharts.DomPosition.Main : 'main';
      return chart.getDom('candle_pane', position) || chart.getDom('candle_pane');
    } catch (e) { return null; }
  }

  function unbindWheel() {
    if (state.wheelDom && state.wheelHandler && typeof state.wheelDom.removeEventListener === 'function') {
      state.wheelDom.removeEventListener('wheel', state.wheelHandler);
    }
    state.wheelDom = null;
    state.wheelHandler = null;
  }

  function getPointerY(event, dom) {
    if (event && typeof event.clientY === 'number' && dom &&
        typeof dom.getBoundingClientRect === 'function') {
      var rect = dom.getBoundingClientRect();
      return event.clientY - rect.top;
    }
    return event && typeof event.offsetY === 'number' ? event.offsetY : null;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function zoomPriceAxis(event) {
    if (!state.enabled || !state.chart) return;
    var chart = state.chart;
    var axis = getAxis(chart);
    var dom = state.wheelDom;
    if (!axis || !dom || typeof axis.getExtremum !== 'function' ||
        typeof axis.setExtremum !== 'function') return;

    var extremum;
    try { extremum = axis.getExtremum(); } catch (e) { return; }
    if (!extremum || !Number.isFinite(extremum.range) || extremum.range <= 0) return;

    var y = getPointerY(event, dom);
    var height = dom.clientHeight || (typeof dom.getBoundingClientRect === 'function'
      ? dom.getBoundingClientRect().height : 0);
    if (!Number.isFinite(y) || !Number.isFinite(height) || height <= 0) return;
    y = clamp(y, 0, height);

    var baseRange = state.zoomBaseRange || extremum.range;
    var minRange = Math.max(Number.EPSILON, baseRange * MIN_ZOOM_RATIO);
    var maxRange = Math.max(minRange, baseRange * MAX_ZOOM_RATIO);
    var factor = Math.exp((Number(event.deltaY) || 0) * WHEEL_SENSITIVITY);
    var nextRange = clamp(extremum.range * factor, minRange, maxRange);
    if (Math.abs(nextRange - extremum.range) < Number.EPSILON) return;

    var pointerRatio = y / height;
    var reversed = false;
    try { reversed = typeof axis.isReverse === 'function' && axis.isReverse(); } catch (e) {}
    var anchorRatio = reversed ? pointerRatio : 1 - pointerRatio;
    var anchor = extremum.min + anchorRatio * extremum.range;
    var nextMin = anchor - anchorRatio * nextRange;
    var nextMax = nextMin + nextRange;
    var realMin = typeof axis.convertToRealValue === 'function' ? axis.convertToRealValue(nextMin) : nextMin;
    var realMax = typeof axis.convertToRealValue === 'function' ? axis.convertToRealValue(nextMax) : nextMax;
    var next = Object.assign({}, extremum, {
      min: nextMin,
      max: nextMax,
      range: nextRange,
      realMin: realMin,
      realMax: realMax,
      realRange: realMax - realMin,
    });
    try {
      axis.setExtremum(next);
      redraw(chart, false);
    } catch (e) {}
  }

  function bindWheel(chart) {
    var dom = getMainDom(chart);
    if (!dom || typeof dom.addEventListener !== 'function') return;
    if (state.wheelDom === dom) return;
    unbindWheel();
    state.wheelDom = dom;
    state.wheelHandler = zoomPriceAxis;
    // Do not cancel the event: KLineCharts keeps ownership of native X zoom.
    dom.addEventListener('wheel', state.wheelHandler, { passive: true });
  }

  function setEnabled(enabled, options) {
    var chart = getChart(options && options.chart);
    var axis = getAxis(chart);
    if (!chart || !axis || typeof axis.setAutoCalcTickFlag !== 'function') {
      state.enabled = false;
      updateButton();
      return false;
    }

    var next = enabled === true;
    try {
      if (next) {
        // Preserve the currently visible range, then let KLineCharts' native
        // main-pane drag path move both the time axis and the price axis.
        var extremum = typeof axis.getExtremum === 'function' ? axis.getExtremum() : null;
        if (!extremum) return false;
        state.zoomBaseRange = Number.isFinite(extremum.range) && extremum.range > 0 ? extremum.range : null;
        if (typeof chart.setPaneOptions === 'function') {
          chart.setPaneOptions({
            id: 'candle_pane',
            dragEnabled: true,
            axisOptions: { scrollZoomEnabled: true },
          });
        }
        axis.setAutoCalcTickFlag(false);
        if (typeof axis.setExtremum === 'function') axis.setExtremum(Object.assign({}, extremum));
      } else {
        unbindWheel();
        state.zoomBaseRange = null;
        axis.setAutoCalcTickFlag(true);
        if (typeof axis.buildTicks === 'function') axis.buildTicks(true);
      }
      state.enabled = next;
      if (next) bindWheel(chart);
      redraw(chart, !next);
      updateButton();
      return true;
    } catch (e) {
      state.enabled = false;
      updateButton();
      return false;
    }
  }

  function toggle() {
    return setEnabled(!state.enabled);
  }

  function reset(options) {
    var axis = getAxis(getChart(options && options.chart));
    if (!state.enabled && axis && typeof axis.getAutoCalcTickFlag === 'function') {
      try {
        if (axis.getAutoCalcTickFlag()) {
          updateButton();
          return true;
        }
      } catch (e) {}
    }
    return setEnabled(false, options || {});
  }

  function syncFromAxis() {
    var axis = getAxis(getChart());
    if (!axis || typeof axis.getAutoCalcTickFlag !== 'function') return;
    try {
      state.enabled = !axis.getAutoCalcTickFlag();
      updateButton();
    } catch (e) {}
  }

  function mountButton() {
    var doc = getDocument();
    if (!doc || typeof doc.querySelector !== 'function') return null;
    var periodBar = doc.querySelector('.klinecharts-pro-period-bar');
    if (!periodBar) return null;

    var button = doc.getElementById('chart-vertical-pan-btn');
    if (!button) {
      button = doc.createElement('button');
      button.id = 'chart-vertical-pan-btn';
      button.type = 'button';
      button.className = 'chart-vertical-pan-button';
      button.textContent = '↕';
      var firstTool = periodBar.querySelector('.item.tools');
      if (firstTool) periodBar.insertBefore(button, firstTool);
      else periodBar.appendChild(button);
    }
    if (!button._chartVerticalPanBound) {
      button.addEventListener('click', toggle);
      button._chartVerticalPanBound = true;
    }
    state.button = button;
    updateButton();
    return button;
  }

  function bindEvents() {
    if (state.eventsBound || typeof global.addEventListener !== 'function') return;
    state.eventsBound = true;
    global.addEventListener('kline-chart-ready', function (event) {
      init({ chart: event && event.detail });
    });
    global.addEventListener('dblclick', function () {
      // KLineCharts restores auto scale when its y-axis is double-clicked.
      global.setTimeout(syncFromAxis, 0);
    });
    var doc = getDocument();
    if (doc && typeof doc.addEventListener === 'function') {
      doc.addEventListener('click', function (event) {
        var target = event && event.target;
        var period = target && typeof target.closest === 'function'
          ? target.closest('.klinecharts-pro-period-bar .item.period') : null;
        if (period) reset({ silent: true });
      }, true);
    }
  }

  function init(options) {
    state.chart = getChart(options && options.chart);
    mountButton();
    bindEvents();
    syncFromAxis();
    return !!state.chart;
  }

  var controller = {
    init: init,
    onChartReady: function (chart) { return init({ chart: chart }); },
    setEnabled: setEnabled,
    toggle: toggle,
    reset: reset,
    isEnabled: function () { return state.enabled; },
    syncFromAxis: syncFromAxis,
  };

  global.ChartVerticalPanController = controller;

  var doc = getDocument();
  if (doc) {
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', function () { init({}); });
    else init({});
    if (typeof global.MutationObserver === 'function') {
      try {
        new global.MutationObserver(mountButton).observe(doc.documentElement, {
          childList: true,
          subtree: true,
        });
      } catch (e) {}
    }
  }
})(typeof window !== 'undefined' ? window : globalThis);
