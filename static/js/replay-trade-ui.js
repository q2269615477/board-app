(function (global) {
  'use strict';

  /*
   * Replay trade UI is deliberately isolated from the replay controller and
   * the trade engine.  The controller owns bars/cursor, the engine owns
   * orders and P&L, while this module owns only DOM interaction and drawing.
   */
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var POLL_MS = 250;
  var DEFAULT_AMOUNT = 10000;
  var tradeStateModel = global.ReplayTradeStateModel;
  if (!tradeStateModel) throw new Error('ReplayTradeStateModel must load before ReplayTradeUI');
  var replayGeometry = global.ReplayTradeGeometry;
  if (!replayGeometry) throw new Error('ReplayTradeGeometry must load before ReplayTradeUI');
  var overlayRenderer = global.ReplayTradeOverlayRenderer;
  if (!overlayRenderer) throw new Error('ReplayTradeOverlayRenderer must load before ReplayTradeUI');
  var EVENT_NAMES = [
    'bar-replay-state',
    'bar-replay-start',
    'bar-replay-cursor',
    'bar-replay-exit',
    'replay-trade-state',
    'kline-chart-ready',
    'kline-loaded',
  ];
  var PRICE_FIELDS = [
    { key: 'open', label: '开盘' },
    { key: 'high', label: '最高' },
    { key: 'low', label: '最低' },
    { key: 'close', label: '收盘' },
  ];

  var state = {
    initialized: false,
    active: false,
    mode: null,
    chart: null,
    mainDom: null,
    controls: null,
    overlaySvg: null,
    summarySvg: null,
    picker: null,
    presetPanel: null,
    recordsPanel: null,
    editPanel: null,
    bracketOrdersPanel: null,
    bracketConfirmBar: null,
    engine: null,
    engineSource: null,
    eventsSource: null,
    eventUnbinds: [],
    chartUnbinds: [],
    pollTimer: null,
    selectedBar: null,
    presetSelection: {
      active: false,
      side: null,
      amount: null,
      previewPrice: null,
      previewY: null,
      previewX: null,
    },
    presetSelectionUnbinds: [],
    presetDrag: {
      active: false,
      role: null,
      orderId: null,
      price: null,
      amount: null,
      quantity: null,
      unbinds: [],
    },
    bracketDraft: null,
    bracketDrafts: [],
    bracketDraftedExecutionIds: Object.create(null),
    bracketDraftDrag: {
      active: false,
      role: null,
      price: null,
      unbinds: [],
    },
    executionDrag: {
      active: false,
      record: null,
      price: null,
      amount: null,
      startY: null,
      startX: null,
      barIndex: null,
      timestamp: null,
      moved: false,
      suppressClickId: null,
      unbinds: [],
    },
    lastReplayDetail: null,
    tradeState: null,
    localRecords: [],
    localPresets: { buy: null, sell: null, takeProfit: null, stopLoss: null },
    lastControllerStatus: '',
    lastCursor: -1,
    summaryPosition: { x: null, y: null },
    summaryGeometry: null,
    ghostOrders: Object.create(null),
    summaryExpandedOrders: Object.create(null),
    summaryDrag: {
      active: false,
      startX: null,
      startY: null,
      originX: null,
      originY: null,
      moved: false,
      suppressClick: false,
      unbinds: [],
    },
  };

  function getDocument() {
    return global.document || null;
  }

  function getController() {
    return global.BarReplayController || null;
  }

  function getChart(candidate) {
    if (candidate && typeof candidate.getDataList === 'function') return candidate;
    if (state.chart && typeof state.chart.getDataList === 'function') return state.chart;
    if (global.__kline_chart && typeof global.__kline_chart.getDataList === 'function') {
      return global.__kline_chart;
    }
    return null;
  }

  function getEngine() {
    if (state.engine) return state.engine;
    var candidate = global.ReplayTradeEngine;
    if (!candidate) return null;

    if (typeof candidate === 'object') {
      state.engine = candidate;
      state.engineSource = candidate;
      return state.engine;
    }
    if (typeof candidate !== 'function') return null;

    var factory = null;
    try {
      if (typeof candidate.getInstance === 'function') factory = candidate.getInstance;
      else if (candidate.instance) {
        state.engine = candidate.instance;
        state.engineSource = candidate;
        return state.engine;
      }
    } catch (e) {}
    if (factory) {
      try {
        state.engine = factory.call(candidate, {
          chart: getChart(),
          replayController: getController(),
          events: global.BarReplayEvents || null,
        }) || null;
      } catch (e) { state.engine = null; }
    }
    if (!state.engine) {
      try {
        state.engine = new candidate({
          chart: getChart(),
          replayController: getController(),
          events: global.BarReplayEvents || null,
        });
      } catch (e) { state.engine = null; }
    }
    state.engineSource = candidate;
    return state.engine;
  }

  function byId(id) {
    var document = getDocument();
    if (!document || typeof document.getElementById !== 'function') return null;
    try { return document.getElementById(id); } catch (e) { return null; }
  }

  function create(tag, namespace) {
    var document = getDocument();
    if (!document || typeof document.createElement !== 'function') return null;
    try {
      return namespace && document.createElementNS
        ? document.createElementNS(SVG_NS, tag)
        : document.createElement(tag);
    } catch (e) { return null; }
  }

  function append(parent, child) {
    if (parent && child && typeof parent.appendChild === 'function') parent.appendChild(child);
    return child;
  }

  function attr(node, name, value) {
    if (node && typeof node.setAttribute === 'function') node.setAttribute(name, String(value));
  }

  function text(node, value) {
    if (node && typeof node.textContent !== 'undefined') node.textContent = String(value == null ? '' : value);
  }

  var rendererAdapter = {
    create: create,
    append: append,
    attr: attr,
    text: text,
  };

  function clear(node) {
    if (!node) return;
    while (node.firstChild && typeof node.removeChild === 'function') node.removeChild(node.firstChild);
    if (node.children && typeof node.children.length === 'number' && typeof node.removeChild === 'function') {
      while (node.children.length) node.removeChild(node.children[node.children.length - 1]);
    }
  }

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function timestamp(value) {
    var number = finite(value);
    if (number == null) return null;
    return number < 10000000000 ? number * 1000 : number;
  }

  function dateText(value) {
    var date = new Date(timestamp(value));
    if (!isFinite(date.getTime())) return '--';
    var y = date.getFullYear();
    var m = String(date.getMonth() + 1);
    var d = String(date.getDate());
    return y + '-' + (m.length < 2 ? '0' : '') + m + '-' + (d.length < 2 ? '0' : '') + d;
  }

  function numberText(value, digits) {
    var number = finite(value);
    if (number == null) return '--';
    return number.toLocaleString ? number.toLocaleString('zh-CN', {
      minimumFractionDigits: digits == null ? 2 : digits,
      maximumFractionDigits: digits == null ? 2 : digits,
    }) : number.toFixed(digits == null ? 2 : digits);
  }

  function moneyText(value) {
    var number = finite(value);
    if (number == null) return '--';
    return (number >= 0 ? '+' : '') + numberText(number, 2);
  }

  function currencyText(value, signed) {
    var number = finite(value);
    if (number == null) return '--';
    var sign = number < 0 ? '-' : (signed && number > 0 ? '+' : '');
    return sign + '¥' + numberText(Math.abs(number), 2);
  }

  function pctText(value) {
    var number = finite(value);
    if (number == null) return '--';
    return (number >= 0 ? '+' : '') + number.toFixed(2) + '%';
  }

  function sideOf(value) {
    var side = String(value == null ? '' : value).toLowerCase();
    if (side === 'buy' || side === 'b' || side === 'long' || side === 'entry' || side === 'in') return 'buy';
    if (side === 'sell' || side === 's' || side === 'short' || side === 'exit' || side === 'out') return 'sell';
    return '';
  }

  function presetRole(value) {
    var role = String(value == null ? '' : value).replace(/[\s_-]/g, '').toLowerCase();
    if (role === 'takeprofit' || role === 'tp' || role === 'profit') return 'takeProfit';
    if (role === 'stoploss' || role === 'sl' || role === 'stop') return 'stopLoss';
    return sideOf(role);
  }

  function presetRoleLabel(role) {
    role = presetRole(role);
    if (role === 'takeProfit') return '止盈';
    if (role === 'stopLoss') return '止损';
    return role === 'buy' ? '买入' : '卖出';
  }

  function fieldLabel(key) {
    var item = PRICE_FIELDS.filter(function (entry) { return entry.key === key; })[0];
    return item ? item.label : (key || '价格');
  }

  function safeClassList(node, method, value) {
    try {
      if (node && node.classList && typeof node.classList[method] === 'function') {
        node.classList[method](value);
      }
    } catch (e) {}
  }

  function setStyle(node, values) {
    if (!node || !node.style) return;
    Object.keys(values).forEach(function (key) {
      try { node.style[key] = values[key]; } catch (e) {}
    });
  }

  function makeEvent(name, detail) {
    try {
      if (typeof global.CustomEvent === 'function') return new global.CustomEvent(name, { detail: detail });
      if (typeof global.Event === 'function') {
        var event = new global.Event(name);
        event.detail = detail;
        return event;
      }
    } catch (e) {}
    return { type: name, detail: detail };
  }

  function emit(name, detail) {
    var events = global.BarReplayEvents;
    var emittedByBus = false;
    if (events) {
      try {
        if (typeof events.emit === 'function') { events.emit(name, detail); emittedByBus = true; }
        else if (typeof events.dispatch === 'function') { events.dispatch(name, detail); emittedByBus = true; }
        else if (typeof events.trigger === 'function') { events.trigger(name, detail); emittedByBus = true; }
      } catch (e) {}
    }
    if (!emittedByBus && global && typeof global.dispatchEvent === 'function') {
      try { global.dispatchEvent(makeEvent(name, detail)); } catch (e) {}
    }
  }

  function addUnbind(unbind) {
    if (typeof unbind === 'function') state.eventUnbinds.push(unbind);
  }

  function subscribeBus(bus, name, handler) {
    if (!bus) return false;
    var subscribed = false;
    try {
      if (typeof bus.on === 'function') {
        bus.on(name, handler);
        addUnbind(function () {
          try {
            if (typeof bus.off === 'function') bus.off(name, handler);
            else if (typeof bus.unsubscribe === 'function') bus.unsubscribe(name, handler);
          } catch (e) {}
        });
        subscribed = true;
      } else if (typeof bus.addEventListener === 'function') {
        bus.addEventListener(name, handler);
        addUnbind(function () { try { bus.removeEventListener(name, handler); } catch (e) {} });
        subscribed = true;
      } else if (typeof bus.subscribe === 'function') {
        var result = bus.subscribe(name, handler);
        addUnbind(function () {
          try {
            if (typeof result === 'function') result();
            else if (typeof bus.unsubscribe === 'function') bus.unsubscribe(name, handler);
          } catch (e) {}
        });
        subscribed = true;
      } else if (typeof bus.listen === 'function') {
        bus.listen(name, handler);
        addUnbind(function () { try { if (typeof bus.unlisten === 'function') bus.unlisten(name, handler); } catch (e) {} });
        subscribed = true;
      }
    } catch (e) {}
    return subscribed;
  }

  function bindEvents() {
    if (!global || typeof global.addEventListener !== 'function') return;
    if (state.eventUnbinds._domBound) return;
    state.eventUnbinds._domBound = true;
    EVENT_NAMES.forEach(function (name) {
      var handler = function (event) { onEvent(name, event); };
      try {
        global.addEventListener(name, handler);
        addUnbind(function () { try { global.removeEventListener(name, handler); } catch (e) {} });
      } catch (e) {}
    });
  }

  function detailOf(event) {
    if (!event) return {};
    if (event.detail && typeof event.detail === 'object') return event.detail;
    if (event.state && typeof event.state === 'object') return event.state;
    return event;
  }

  function controllerState() {
    var controller = getController();
    if (!controller) return null;
    try {
      if (typeof controller.getState === 'function') return controller.getState() || null;
    } catch (e) {}
    return null;
  }

  function replayActive(detail) {
    var controller = getController();
    try {
      if (controller && typeof controller.isActive === 'function') return !!controller.isActive();
    } catch (e) {}
    detail = detail || {};
    if (detail.active != null) return !!detail.active;
    var status = detail.status || detail.state;
    return status === 'selecting' || status === 'paused' || status === 'playing' || status === 'active';
  }

  function replayRows(detail) {
    var chart = getChart();
    var rows = null;
    if (chart && typeof chart.getDataList === 'function') {
      try { rows = chart.getDataList(); } catch (e) {}
    }
    if (Array.isArray(rows) && rows.length) return rows;
    detail = detail || state.lastReplayDetail || {};
    rows = detail.visibleBars || detail.visibleRows || detail.rows || detail.data;
    if (Array.isArray(rows)) return rows;
    rows = detail.history;
    return Array.isArray(rows) ? rows.slice(0, Number(detail.cursor) + 1) : [];
  }

  function getMainDom(chart) {
    chart = getChart(chart);
    if (chart && typeof chart.getDom === 'function') {
      try {
        var position = global.klinecharts && global.klinecharts.DomPosition
          ? global.klinecharts.DomPosition.Main : undefined;
        var dom = chart.getDom('candle_pane', position);
        if (dom) return dom;
        dom = chart.getDom('candle_pane');
        if (dom) return dom;
      } catch (e) {}
    }
    var document = getDocument();
    if (!document || typeof document.querySelector !== 'function') return byId('pro-container');
    try {
      return document.querySelector('.klinecharts-pro-content') || byId('pro-container');
    } catch (e) { return byId('pro-container'); }
  }

  function injectStyles() {
    var document = getDocument();
    if (!document || typeof document.createElement !== 'function' || byId('replay-trade-ui-style')) return;
    var style = create('style');
    if (!style) return;
    style.id = 'replay-trade-ui-style';
    style.textContent = [
      '#bar-replay-controls .replay-trade-action{min-width:38px;padding:0 7px;}',
      '#bar-replay-controls .replay-trade-action[data-active="true"]{border-color:#d99a2b;color:#ffd078;background:#211a10;}',
      '.replay-trade-main{position:relative;}',
      '#replay-trade-overlay,#replay-trade-summary{position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none;z-index:18;}',
      '#replay-trade-summary{z-index:19;}',
      '#replay-trade-overlay .replay-trade-buy-marker{fill:var(--replay-buy-color,#ef4444);stroke:var(--replay-trade-surface,#fff);}',
      '#replay-trade-overlay .replay-trade-sell-marker{fill:var(--replay-sell-color,#7c3aed);stroke:var(--replay-trade-surface,#fff);}',
      '#replay-trade-overlay .replay-trade-marker-label{font:600 9px sans-serif;fill:var(--replay-trade-surface,#fff);text-anchor:middle;dominant-baseline:central;}',
      '#replay-trade-overlay .replay-trade-marker-arrow{stroke:var(--replay-trade-surface,#fff);stroke-width:1;stroke-linejoin:round;}',
      '#replay-trade-overlay .replay-trade-execution-level{fill:none;stroke-width:1.1;stroke-dasharray:5 4;opacity:.9;pointer-events:none;}',
      '#replay-trade-overlay .replay-trade-execution-label-bg{fill:var(--replay-trade-panel-bg,#fff);stroke-width:1;opacity:.96;}',
      '#replay-trade-overlay .replay-trade-execution-label{display:flex;align-items:center;height:20px;box-sizing:border-box;padding:0 7px;color:#dc2626;background:var(--replay-trade-panel-bg,#fff);border:1px solid currentColor;border-radius:6px;font:400 10px/20px sans-serif!important;font-weight:400!important;white-space:nowrap;pointer-events:none;}',
      '#replay-trade-overlay .replay-trade-marker{pointer-events:all;cursor:pointer;}',
      '#replay-trade-overlay .replay-trade-buy-marker{cursor:move;}',
      '#replay-trade-overlay .replay-trade-buy-stem{stroke:var(--replay-buy-color,#ef4444);}',
      '#replay-trade-overlay .replay-trade-sell-stem{stroke:var(--replay-sell-color,#7c3aed);}',
      '#replay-trade-overlay .replay-trade-preset{fill:none;stroke-width:1.2;stroke-dasharray:6 4;opacity:.82;}',
      '#replay-trade-overlay .replay-trade-preset-order{pointer-events:all;cursor:ns-resize;}',
      '#replay-trade-overlay .replay-trade-preset-hit{stroke:transparent;stroke-width:16;pointer-events:stroke;}',
      '#replay-trade-overlay .replay-trade-preset-buy{stroke:var(--replay-buy-color,#2563eb);}',
      '#replay-trade-overlay .replay-trade-preset-sell{stroke:var(--replay-sell-color,#7c3aed);}',
      '#replay-trade-overlay .replay-trade-preset-takeProfit{stroke:#ef4444;}',
      '#replay-trade-overlay .replay-trade-preset-stopLoss{stroke:#10b981;}',
      '#replay-trade-overlay .replay-trade-risk-zone{pointer-events:none;stroke:none;}',
      '#replay-trade-overlay .replay-trade-risk-zone-takeProfit{fill:#ef4444;opacity:.075;}',
      '#replay-trade-overlay .replay-trade-risk-zone-stopLoss{fill:#10b981;opacity:.075;}',
      '#replay-trade-overlay .replay-trade-preset-preview{stroke-width:2;stroke-dasharray:8 4;opacity:.96;}',
      '#replay-trade-overlay .replay-trade-preset-preview-buy{stroke:var(--replay-buy-color,#2563eb);}',
      '#replay-trade-overlay .replay-trade-preset-preview-sell{stroke:var(--replay-sell-color,#7c3aed);}',
      '#replay-trade-overlay .replay-trade-preset-label{font:400 10px sans-serif!important;font-weight:400!important;dominant-baseline:central;}',
      '#replay-trade-overlay .replay-trade-preset-label-buy{fill:var(--replay-buy-color,#2563eb);}',
      '#replay-trade-overlay .replay-trade-preset-label-sell{fill:var(--replay-sell-color,#7c3aed);}',
      '#replay-trade-overlay .replay-trade-preset-label-takeProfit{fill:#dc2626;}',
      '#replay-trade-overlay .replay-trade-preset-label-stopLoss{fill:#059669;}',
      '#replay-trade-overlay .replay-trade-preset-label-bg{fill:var(--replay-trade-panel,#111827);stroke-width:1;rx:3;}',
      '#replay-trade-overlay .replay-trade-preset-delete{font:400 11px sans-serif;cursor:pointer;pointer-events:all;}',
      '#replay-trade-overlay .replay-trade-history-ghost{opacity:.34;pointer-events:none;}',
      '#replay-trade-overlay .replay-trade-bracket-draft{pointer-events:all;cursor:ns-resize;}',
      '#replay-trade-overlay .replay-trade-bracket-entry{stroke:#2563eb;}',
      '#replay-trade-overlay .replay-trade-bracket-takeProfit{stroke:#ef4444;}',
      '#replay-trade-overlay .replay-trade-bracket-stopLoss{stroke:#10b981;}',
      '#replay-trade-overlay .replay-trade-bracket-label{font:400 10px sans-serif!important;font-weight:400!important;dominant-baseline:central;pointer-events:all;cursor:pointer;}',
      '#replay-trade-overlay .replay-trade-bracket-label-entry{fill:#1d4ed8;}',
      '#replay-trade-overlay .replay-trade-bracket-label-takeProfit{fill:#dc2626;}',
      '#replay-trade-overlay .replay-trade-bracket-label-stopLoss{fill:#059669;}',
      '#replay-trade-summary .replay-trade-summary-box{fill:var(--replay-trade-panel,#111827);stroke:var(--replay-trade-border,#64748b);stroke-width:1;opacity:.94;}',
      '#replay-trade-summary .replay-trade-summary-title{font:600 12px sans-serif;fill:var(--replay-trade-text,#e5edf8);}',
      '#replay-trade-summary .replay-trade-summary-line{font:11px sans-serif;fill:var(--replay-trade-muted,#a8b4c5);}',
      '#replay-trade-summary .replay-trade-summary-detail{font:10px sans-serif;fill:var(--replay-trade-muted,#a8b4c5);}',
      '#replay-trade-summary .replay-trade-summary-toggle{cursor:pointer;pointer-events:all;font-weight:600;}',
      '#replay-trade-summary .replay-trade-positive{fill:var(--replay-positive-color,#ef4444);}',
      '#replay-trade-summary .replay-trade-negative{fill:var(--replay-negative-color,#10b981);}',
      '#replay-trade-overlay .replay-trade-order-rail-row{pointer-events:all;}',
      '#replay-trade-overlay .replay-trade-order-strip-bg{fill:var(--replay-trade-panel-bg,#fff);stroke:var(--replay-trade-border,#cbd5e1);stroke-width:1;}',
      '#replay-trade-overlay .replay-trade-order-rail-bg{stroke-width:1;stroke-linejoin:round;cursor:pointer;}',
      '#replay-trade-overlay .replay-trade-order-rail-buy{fill:#fff;stroke:#ef4444;stroke-width:0;font:600 11px sans-serif;dominant-baseline:central;text-anchor:middle;cursor:pointer;}',
      '#replay-trade-overlay .replay-trade-order-rail-sell{fill:#fff;stroke:#2563eb;stroke-width:0;font:600 11px sans-serif;dominant-baseline:central;text-anchor:middle;cursor:pointer;}',
      '#replay-trade-overlay .replay-trade-order-rail-buy-bg{fill:#ef4444;stroke:#b91c1c;}',
      '#replay-trade-overlay .replay-trade-order-rail-sell-bg{fill:#2563eb;stroke:#1d4ed8;}',
      '#replay-trade-overlay .replay-trade-order-rail-action{fill:#fff;font:400 9px sans-serif;text-anchor:middle;pointer-events:none;}',
      '#replay-trade-overlay .replay-trade-order-rail-price{fill:#fff;font:400 8px sans-serif;text-anchor:middle;pointer-events:none;}',
      '#replay-trade-overlay .replay-trade-order-rail-pnl{font:500 9px sans-serif;text-anchor:middle;dominant-baseline:central;}',
      '#replay-trade-overlay .replay-trade-order-rail-pnl-positive{fill:#ef4444;}',
      '#replay-trade-overlay .replay-trade-order-rail-pnl-negative{fill:#10b981;}',
      '#replay-trade-overlay .replay-trade-order-rail-pnl-flat{fill:var(--replay-trade-text,#64748b);}',
      '#replay-trade-overlay .replay-trade-order-rail-disabled{opacity:.45;cursor:default;}',
      '#replay-trade-summary .replay-trade-summary-separator{stroke:var(--replay-trade-border,#64748b);stroke-width:1;opacity:.7;}',
      '#replay-trade-summary .replay-trade-summary-order{pointer-events:all;cursor:pointer;}',
      '#replay-trade-summary .replay-trade-summary-column-positive{fill:#ef4444;opacity:.055;}',
      '#replay-trade-summary .replay-trade-summary-column-negative{fill:#10b981;opacity:.055;}',
      '.replay-trade-picker,.replay-trade-panel{position:fixed;z-index:2400;width:236px;padding:10px;border:1px solid #49617d;border-radius:6px;background:#111827;color:#e5edf8;box-shadow:0 12px 28px rgba(0,0,0,.34);font:12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;}',
      '.replay-trade-picker[hidden],.replay-trade-panel[hidden]{display:none!important;}',
      '.replay-trade-picker .replay-trade-heading,.replay-trade-panel .replay-trade-heading{display:flex;align-items:center;justify-content:space-between;font-weight:600;margin-bottom:6px;}',
      '.replay-trade-picker .replay-trade-date,.replay-trade-panel .replay-trade-note{color:#a8b4c5;margin-bottom:7px;}',
      '.replay-trade-price-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;margin-top:7px;}',
      '.replay-trade-price-grid button,.replay-trade-panel button{min-height:27px;padding:3px 7px;border:1px solid #49617d;border-radius:4px;background:#18253a;color:#e5edf8;cursor:pointer;font:inherit;}',
      '.replay-trade-price-grid button:hover,.replay-trade-panel button:hover,.replay-trade-panel button:focus-visible{border-color:#79a7f8;background:#21365a;outline:none;}',
      '.replay-trade-picker input,.replay-trade-panel input{box-sizing:border-box;width:100%;height:29px;padding:0 7px;border:1px solid #49617d;border-radius:4px;background:#0d1522;color:#e5edf8;font:inherit;}',
      '.replay-trade-field{display:block;margin-top:6px;color:#a8b4c5;}',
      '.replay-trade-field input{display:block;margin-top:3px;}',
      '.replay-trade-panel{width:300px;max-height:min(520px,calc(100vh - 24px));overflow:auto;}',
      '.replay-trade-panel .replay-trade-section{padding:8px 0;border-top:1px solid rgba(148,163,184,.22);}',
      '.replay-trade-panel .replay-trade-row{display:grid;grid-template-columns:1fr 1fr;gap:6px;align-items:end;margin-top:6px;}',
      '.replay-trade-panel .replay-trade-row > *{min-width:0;}',
      '.replay-trade-panel .replay-trade-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}',
      '.replay-trade-panel .replay-trade-list{display:grid;gap:5px;}',
      '.replay-trade-panel .replay-trade-record{padding:6px;border:1px solid rgba(148,163,184,.24);border-radius:4px;background:rgba(30,41,59,.55);}',
      '.replay-trade-panel .replay-trade-muted{color:#a8b4c5;}',
      '.replay-trade-preset-select{width:100%;margin-top:6px;text-align:left;}',
      '.replay-trade-preset-selection{color:#79a7f8;font-size:11px;margin-top:5px;}',
      '.replay-trade-main.replay-trade-preset-selecting{cursor:crosshair;}',
      'body[data-board-theme="light"] #bar-replay-controls .replay-trade-action[data-active="true"]{border-color:#b77916;color:#8a5a00;background:#fff7e6;}',
      'body[data-board-theme="light"] #replay-trade-overlay,body[data-board-theme="light"] #replay-trade-summary{--replay-trade-surface:#fff;--replay-trade-panel:#fff;--replay-trade-border:#91a4ba;--replay-trade-text:#17263a;--replay-trade-muted:#526274;}',
      'body[data-board-theme="light"] .replay-trade-picker,body[data-board-theme="light"] .replay-trade-panel{border-color:#b9c8d9;background:#fff;color:#17263a;box-shadow:0 12px 28px rgba(45,67,92,.2);}',
      'body[data-board-theme="light"] .replay-trade-picker .replay-trade-date,body[data-board-theme="light"] .replay-trade-panel .replay-trade-note,body[data-board-theme="light"] .replay-trade-field,body[data-board-theme="light"] .replay-trade-panel .replay-trade-muted{color:#526274;}',
      'body[data-board-theme="light"] .replay-trade-price-grid button,body[data-board-theme="light"] .replay-trade-panel button{border-color:#b9c8d9;background:#f4f7fb;color:#25364a;}',
      'body[data-board-theme="light"] .replay-trade-picker input,body[data-board-theme="light"] .replay-trade-panel input{border-color:#b9c8d9;background:#f8fafc;color:#17263a;}',
      'body[data-board-theme="light"] .replay-trade-panel .replay-trade-record{border-color:#d5dfe9;background:#f8fafc;}',
    ].join('');
    var head = document.head || document.getElementsByTagName && document.getElementsByTagName('head')[0] || document.body;
    append(head, style);
  }

  function createActionButton(id, label, title, handler) {
    var button = byId(id);
    if (!button) {
      button = create('button');
      if (!button) return null;
      button.id = id;
      button.type = 'button';
      button.className = 'replay-trade-action';
      text(button, label);
      attr(button, 'aria-label', title);
      button.title = title;
      append(state.controls, button);
    }
    safeClassList(button, 'add', 'replay-trade-action');
    if (!button._replayTradeBound && typeof button.addEventListener === 'function') {
      button.addEventListener('click', handler);
      button._replayTradeBound = true;
    }
    return button;
  }

  function ensureControls() {
    var controls = byId('bar-replay-controls');
    if (!controls) return null;
    state.controls = controls;
    createActionButton('replay-trade-buy', '买入', '选择 K 线价格执行买入', function () { enterSelection('buy'); });
    createActionButton('replay-trade-sell', '卖出', '选择 K 线价格执行卖出', function () { enterSelection('sell'); });
    createActionButton('replay-trade-preset', '预设', '设置或取消自动买卖预设', openPresetPanel);
    createActionButton('replay-trade-records', '交易记录', '查看回放交易记录', openRecordsPanel);
    updateControls();
    return controls;
  }

  function updateControls() {
    var controls = state.controls || byId('bar-replay-controls');
    if (!controls) return;
    state.controls = controls;
    var active = replayActive();
    ['replay-trade-buy', 'replay-trade-sell', 'replay-trade-preset', 'replay-trade-records'].forEach(function (id) {
      var button = byId(id);
      if (!button) return;
      button.hidden = !active;
      button.disabled = !active;
      var ordinaryActive = state.mode && id === 'replay-trade-' + state.mode;
      var presetActive = state.presetSelection.active && id === 'replay-trade-preset';
      attr(button, 'data-active', ordinaryActive || presetActive ? 'true' : 'false');
    });
    if (controls.style && controls.style.display === 'none') {
      ['replay-trade-buy', 'replay-trade-sell', 'replay-trade-preset', 'replay-trade-records'].forEach(function (id) {
        var button = byId(id);
        if (button) button.hidden = true;
      });
    }
  }

  function ensureOverlayDom(chart) {
    chart = getChart(chart);
    var mainDom = getMainDom(chart);
    if (!mainDom || typeof mainDom.appendChild !== 'function') return false;
    if (state.mainDom !== mainDom) {
      state.mainDom = mainDom;
      safeClassList(mainDom, 'add', 'replay-trade-main');
      state.overlaySvg = null;
      state.summarySvg = null;
      state.summaryPosition = { x: null, y: null };
      state.summaryGeometry = null;
    }
    if (!state.overlaySvg || !byId('replay-trade-overlay')) {
      state.overlaySvg = byId('replay-trade-overlay') || create('svg', true);
      if (state.overlaySvg) {
        attr(state.overlaySvg, 'id', 'replay-trade-overlay');
        attr(state.overlaySvg, 'class', 'replay-trade-layer');
        attr(state.overlaySvg, 'aria-hidden', 'false');
        append(mainDom, state.overlaySvg);
      }
    }
    if (!state.summarySvg || !byId('replay-trade-summary')) {
      state.summarySvg = byId('replay-trade-summary') || create('svg', true);
      if (state.summarySvg) {
        attr(state.summarySvg, 'id', 'replay-trade-summary');
        attr(state.summarySvg, 'class', 'replay-trade-layer');
        attr(state.summarySvg, 'aria-hidden', 'true');
        append(mainDom, state.summarySvg);
      }
    }
    var obsoleteConfirm = byId('replay-trade-bracket-confirm');
    if (obsoleteConfirm && obsoleteConfirm.parentNode) obsoleteConfirm.parentNode.removeChild(obsoleteConfirm);
    state.bracketConfirmBar = null;
    return !!(state.overlaySvg && state.summarySvg);
  }

  function unbindChart() {
    state.chartUnbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    state.chartUnbinds = [];
  }

  function bindChart(chart) {
    chart = getChart(chart);
    if (!chart) return false;
    if (state.chart === chart && state.mainDom) return true;
    if (state.presetSelection.active) cancelPresetSelection(true);
    unbindChart();
    state.chart = chart;
    ensureOverlayDom(chart);
    if (typeof chart.subscribeAction === 'function') {
      var actionTypes = global.klinecharts && global.klinecharts.ActionType;
      ['OnVisibleRangeChange', 'OnZoom', 'OnScroll', 'OnPaneDrag'].forEach(function (key) {
        var action = actionTypes && actionTypes[key] ? actionTypes[key] : key;
        var handler = function () { redraw(); };
        try {
          chart.subscribeAction(action, handler);
          state.chartUnbinds.push(function () {
            try { if (typeof chart.unsubscribeAction === 'function') chart.unsubscribeAction(action, handler); } catch (e) {}
          });
        } catch (e) {}
      });
    }
    return true;
  }

  function rowsForChart() {
    return replayRows(state.lastReplayDetail).filter(function (row) {
      return row && finite(row.close) != null;
    });
  }

  function indexFromValue(value, rows) {
    return replayGeometry.indexFromValue(value, rows);
  }

  function eventCoordinates(event, dom) {
    var rect = dom && typeof dom.getBoundingClientRect === 'function'
      ? dom.getBoundingClientRect() : { left: 0, top: 0 };
    return replayGeometry.eventCoordinates(event, rect);
  }

  function convertPixelToPrice(event, chart, dom) {
    var coordinates = eventCoordinates(event, dom);
    var width = dom && (dom.clientWidth || (dom.getBoundingClientRect && dom.getBoundingClientRect().width));
    var input = replayGeometry.pixelConversionInput(coordinates, width);
    if (!input || !chart || typeof chart.convertFromPixel !== 'function') return null;
    for (var attemptIndex = 0; attemptIndex < input.attempts.length; attemptIndex += 1) {
      try {
        var price = replayGeometry.convertedPrice(
          chart.convertFromPixel(input.attempts[attemptIndex], { paneId: 'candle_pane' })
        );
        if (price != null && price > 0) return { price: price, x: input.x, y: input.y };
      } catch (e) {}
    }
    return null;
  }

  function getPointFromEvent(event, chart, dom) {
    var detail = detailOf(event);
    var direct = event && (event.dataIndex != null ? event.dataIndex : event.index);
    if (direct == null && detail) direct = detail.dataIndex != null ? detail.dataIndex : detail.index;
    var rows = rowsForChart();
    var index = indexFromValue(direct, rows);
    var coordinates = eventCoordinates(event, dom);
    var x = coordinates.x;
    var y = coordinates.y;
    var width = dom && (dom.clientWidth || (dom.getBoundingClientRect && dom.getBoundingClientRect().width));
    if (index < 0 && chart && typeof chart.convertFromPixel === 'function') {
      var indexAttempts = replayGeometry.indexConversionInputs(coordinates);
      var converted = null;
      try {
        if (indexAttempts.length) converted = chart.convertFromPixel(indexAttempts[0], { paneId: 'candle_pane' });
      } catch (e) {
        try {
          if (indexAttempts.length > 1) converted = chart.convertFromPixel(indexAttempts[1], { paneId: 'candle_pane' });
        } catch (ignore) {}
      }
      index = replayGeometry.convertedIndex(converted, rows);
    }
    if (index < 0 && event && event.target && event.target.dataset) {
      index = indexFromValue(event.target.dataset.dataIndex || event.target.dataset.index, rows);
    }
    if (index < 0 && x != null) {
      index = replayGeometry.proportionalIndexFromX(x, width, rows.length);
    }
    if (index < 0 || index >= rows.length) return null;
    return { index: index, row: rows[index], x: x, y: y };
  }

  function selectionHandler(event) {
    if (state.summaryDrag.suppressClick) {
      state.summaryDrag.suppressClick = false;
      return;
    }
    if (!state.mode || !replayActive()) return;
    var chart = getChart();
    var dom = state.mainDom || getMainDom(chart);
    var point = getPointFromEvent(event || {}, chart, dom);
    if (!point) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    showPricePicker(point, state.mode);
  }

  function bindSelection() {
    if (!state.mainDom || typeof state.mainDom.addEventListener !== 'function') return false;
    if (state.mainDom._replayTradeSelectionBound) return true;
    state.mainDom.addEventListener('click', selectionHandler);
    state.mainDom._replayTradeSelectionBound = true;
    return true;
  }

  function unbindSelection() {
    if (!state.mainDom || !state.mainDom._replayTradeSelectionBound) return;
    try { state.mainDom.removeEventListener('click', selectionHandler); } catch (e) {}
    state.mainDom._replayTradeSelectionBound = false;
  }

  function unbindPresetSelection() {
    state.presetSelectionUnbinds.forEach(function (unbind) {
      try { unbind(); } catch (e) {}
    });
    state.presetSelectionUnbinds = [];
  }

  function clearPresetPreview() {
    state.presetSelection.previewPrice = null;
    state.presetSelection.previewY = null;
    state.presetSelection.previewX = null;
    redraw();
  }

  function cancelPresetSelection(silent) {
    var wasActive = !!state.presetSelection.active;
    unbindPresetSelection();
    state.presetSelection.active = false;
    state.presetSelection.side = null;
    state.presetSelection.amount = null;
    state.presetSelection.previewPrice = null;
    state.presetSelection.previewY = null;
    state.presetSelection.previewX = null;
    if (state.mainDom) safeClassList(state.mainDom, 'remove', 'replay-trade-preset-selecting');
    if (wasActive && !silent) setStatus('已取消预设选点');
    updateControls();
    redraw();
  }

  function presetSelectionPoint(event) {
    var chart = getChart();
    var dom = state.mainDom || getMainDom(chart);
    var point = convertPixelToPrice(event || {}, chart, dom);
    if (!point) {
      cancelPresetSelection(true);
      setStatus('无法转换水平价格，已取消预设选点');
      return null;
    }
    return point;
  }

  function updatePresetPreview(event) {
    if (!state.presetSelection.active || !replayActive()) return;
    var point = presetSelectionPoint(event);
    if (!point) return;
    state.presetSelection.previewPrice = point.price;
    state.presetSelection.previewY = point.y;
    state.presetSelection.previewX = point.x;
    setStatus('移动鼠标选择水平价格，点击确认' + presetRoleLabel(state.presetSelection.side));
    redraw();
  }

  function commitPresetPreview(event) {
    if (!state.presetSelection.active || !replayActive()) return;
    var dom = state.mainDom || getMainDom(getChart());
    var coordinates = eventCoordinates(event || {}, dom);
    var preview = state.presetSelection;
    var sameHorizontalPixel = preview.previewPrice != null && preview.previewY != null &&
      (coordinates.y == null || Math.abs(Number(coordinates.y) - Number(preview.previewY)) <= 2);
    var point = sameHorizontalPixel
      ? { price: Number(preview.previewPrice), x: preview.previewX, y: Number(preview.previewY) }
      : presetSelectionPoint(event);
    if (!point) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    var side = state.presetSelection.side;
    var amount = state.presetSelection.amount;
    submitPresetAtPrice(side, point.price, amount, point);
  }

  function presetKeydown(event) {
    if (event && event.key === 'Escape') {
      if (event.preventDefault) event.preventDefault();
      cancelPresetSelection(false);
    }
  }

  function bindPresetSelection() {
    if (!state.mainDom || typeof state.mainDom.addEventListener !== 'function') return false;
    unbindPresetSelection();
    var moveHandler = function (event) { updatePresetPreview(event); };
    var clickHandler = function (event) { commitPresetPreview(event); };
    state.mainDom.addEventListener('mousemove', moveHandler);
    state.mainDom.addEventListener('click', clickHandler);
    state.presetSelectionUnbinds.push(function () { state.mainDom.removeEventListener('mousemove', moveHandler); });
    state.presetSelectionUnbinds.push(function () { state.mainDom.removeEventListener('click', clickHandler); });
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('keydown', presetKeydown);
      state.presetSelectionUnbinds.push(function () { global.removeEventListener('keydown', presetKeydown); });
    }
    return true;
  }

  function enterPresetSelection(side, amount) {
    side = presetRole(side);
    if (!side) return false;
    if (!replayActive()) {
      setStatus('请先开启回放，再选择预设价格');
      return false;
    }
    if (side === 'buy' && (amount == null || amount <= 0)) {
      setStatus('买入金额必须大于 0');
      return false;
    }
    cancelSelection();
    cancelPresetSelection(true);
    closePanel(state.presetPanel);
    closePanel(state.recordsPanel);
    state.presetSelection.active = true;
    state.presetSelection.side = side;
    state.presetSelection.amount = side === 'buy' ? Number(amount) : null;
    bindPresetSelection();
    if (state.mainDom) safeClassList(state.mainDom, 'add', 'replay-trade-preset-selecting');
    updateControls();
    setStatus('移动鼠标选择水平价格，点击确认' + presetRoleLabel(side));
    redraw();
    return true;
  }

  function positionPanel(panel, x, y, width) {
    if (!panel || !panel.style) return;
    var left = Number(x);
    var top = Number(y);
    if (!isFinite(left)) left = 20;
    if (!isFinite(top)) top = 80;
    var document = getDocument();
    var viewportWidth = global.innerWidth || (document && document.documentElement && document.documentElement.clientWidth) || 1200;
    var viewportHeight = global.innerHeight || (document && document.documentElement && document.documentElement.clientHeight) || 800;
    var panelWidth = width || 246;
    left = Math.max(8, Math.min(viewportWidth - panelWidth - 8, left));
    top = Math.max(8, Math.min(viewportHeight - 260, top));
    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
  }

  function closePanel(panel) {
    if (panel) panel.hidden = true;
  }

  function closeTransientPanels() {
    closePanel(state.picker);
    closePanel(state.presetPanel);
    closePanel(state.recordsPanel);
    closePanel(state.editPanel);
    closePanel(state.bracketOrdersPanel);
    if (state.presetSelection.active) cancelPresetSelection(true);
    clearPresetDrag();
  }

  function inputValue(id, fallback) {
    var input = byId(id);
    var value = input && input.value != null ? input.value : fallback;
    return value;
  }

  function createCloseButton(parent, label, handler) {
    var button = create('button');
    if (!button) return null;
    button.type = 'button';
    text(button, label || '关闭');
    attr(button, 'aria-label', label || '关闭');
    button.title = label || '关闭';
    if (typeof button.addEventListener === 'function') button.addEventListener('click', handler);
    append(parent, button);
    return button;
  }

  function currentPositionQuantity() {
    var normalized = currentTradeState();
    var aggregate = normalized && normalized.positionSummary;
    var position = normalized && normalized.position;
    var quantity = finite(aggregate && aggregate.quantity);
    if (quantity == null && position && Array.isArray(position.lots)) {
      quantity = position.lots.reduce(function (total, lot) {
        var value = finite(lot && (lot.remainingQuantity != null ? lot.remainingQuantity :
          lot.quantity != null ? lot.quantity : lot.shares));
        return total + (value == null ? 0 : value);
      }, 0);
    }
    if (quantity == null && position) {
      quantity = finite(position.remainingQuantity != null ? position.remainingQuantity :
        position.quantity != null ? position.quantity : position.shares);
    }
    return quantity != null && quantity > 0 ? quantity : null;
  }

  function orderPositionQuantity(orderNumber) {
    var normalized = currentTradeState();
    var position = normalized && normalized.position;
    var lots = position && Array.isArray(position.lots) ? position.lots : [];
    var requested = Number(orderNumber);
    var quantity = lots.reduce(function (total, lot) {
      if (Number(lot && lot.orderNumber) !== requested) return total;
      var value = finite(lot && (lot.remainingQuantity != null ? lot.remainingQuantity :
        lot.quantity != null ? lot.quantity : lot.shares));
      return total + (value == null ? 0 : value);
    }, 0);
    return quantity > 0 ? quantity : null;
  }

  function showPricePicker(point, side, options) {
    closePanel(state.presetPanel);
    closePanel(state.recordsPanel);
    options = options || {};
    var orderNumber = finite(options.orderNumber);
    var boundOrderNumbers = orderNumber == null ? null : [Number(orderNumber)];
    var document = getDocument();
    if (!document || !point || !point.row) return false;
    var panel = state.picker || create('div');
    if (!panel) return false;
    state.picker = panel;
    panel.id = 'replay-trade-price-picker';
    panel.className = 'replay-trade-picker';
    panel.hidden = false;
    clear(panel);

    var heading = create('div');
    heading.className = 'replay-trade-heading';
    var title = create('span');
    text(title, side === 'buy' ? '选择买入价格' :
      (orderNumber == null ? '选择卖出价格' : '选择 S' + orderNumber + ' 卖出价格'));
    append(heading, title);
    createCloseButton(heading, '关闭', function () { closePanel(panel); });
    append(panel, heading);

    var date = create('div');
    date.className = 'replay-trade-date';
    text(date, dateText(point.row.timestamp) + ' · 第 ' + (point.index + 1) + ' 根 K 线');
    append(panel, date);

    if (side === 'buy') {
      var label = create('label');
      label.className = 'replay-trade-field';
      text(label, '买入金额');
      var amount = create('input');
      amount.id = 'replay-trade-buy-amount';
      amount.type = 'number';
      amount.min = '0.01';
      amount.step = '0.01';
      amount.value = String(state.pendingAmount || DEFAULT_AMOUNT);
      amount.setAttribute('aria-label', '买入金额');
      append(label, amount);
      append(panel, label);
    } else if (orderNumber == null) {
      var quantityLabel = create('label');
      quantityLabel.className = 'replay-trade-field';
      text(quantityLabel, '卖出数量');
      var quantity = create('input');
      quantity.id = 'replay-trade-sell-quantity';
      quantity.type = 'number';
      quantity.min = '0.000001';
      quantity.step = '0.000001';
      var availableQuantity = currentPositionQuantity();
      quantity.value = availableQuantity == null ? '' : String(availableQuantity);
      quantity.setAttribute('aria-label', '卖出数量');
      append(quantityLabel, quantity);
      append(panel, quantityLabel);
    }

    var grid = create('div');
    grid.className = 'replay-trade-price-grid';
    PRICE_FIELDS.forEach(function (entry) {
      var value = finite(point.row[entry.key]);
      if (value == null) return;
      var button = create('button');
      button.type = 'button';
      text(button, entry.label + ' ' + numberText(value));
      button.title = '以' + entry.label + '执行' + (side === 'buy' ? '买入' : '卖出');
      button.setAttribute('aria-label', button.title);
      if (typeof button.addEventListener === 'function') {
        button.addEventListener('click', function () {
          var amount = side === 'buy' ? finite(inputValue('replay-trade-buy-amount', DEFAULT_AMOUNT)) : null;
          var quantity = side === 'sell' ? (orderNumber == null
            ? finite(inputValue('replay-trade-sell-quantity', currentPositionQuantity()))
            : orderPositionQuantity(orderNumber)) : null;
          if (side === 'buy' && (amount == null || amount <= 0)) {
            if (global.alert) global.alert('买入金额必须大于 0');
            return;
          }
          if (side === 'sell' && (quantity == null || quantity <= 0)) {
            if (global.alert) global.alert('卖出数量必须大于 0');
            return;
          }
          state.pendingAmount = amount || state.pendingAmount || DEFAULT_AMOUNT;
          executeTrade(side, point, entry.key, value, amount, quantity, {
            orderNumbers: boundOrderNumbers,
          });
        });
      }
      append(grid, button);
    });
    append(panel, grid);
    if (!panel.parentNode) append(document.body || document.documentElement, panel);
    var rect = state.mainDom && typeof state.mainDom.getBoundingClientRect === 'function'
      ? state.mainDom.getBoundingClientRect() : { left: 20, top: 80 };
    positionPanel(panel, (point.x == null ? rect.left + 20 : rect.left + point.x + 12),
      (point.y == null ? rect.top + 80 : rect.top + point.y + 12), 246);
    return true;
  }

  function enterSelection(side) {
    side = sideOf(side);
    if (!side) return false;
    if (!replayActive()) {
      setStatus('请先开启回放，再选择交易价格');
      return false;
    }
    closeTransientPanels();
    state.mode = side;
    bindSelection();
    ['replay-trade-buy', 'replay-trade-sell'].forEach(function (id) {
      var button = byId(id);
      if (button) attr(button, 'data-active', id.indexOf(side) >= 0 ? 'true' : 'false');
    });
    if (state.mainDom) safeClassList(state.mainDom, 'add', 'replay-trade-selecting');
    setStatus(side === 'buy' ? '点击当前可见 K 线选择买入价格' : '点击当前可见 K 线选择卖出价格');
    return true;
  }

  function cancelSelection() {
    state.mode = null;
    state.selectedBar = null;
    unbindSelection();
    if (state.mainDom) safeClassList(state.mainDom, 'remove', 'replay-trade-selecting');
    updateControls();
  }

  function invokeEngine(names, payload) {
    var engine = getEngine();
    var method = null;
    if (engine) {
      for (var index = 0; index < names.length; index += 1) {
        if (typeof engine[names[index]] === 'function') {
          method = engine[names[index]];
          break;
        }
      }
      if (!method && typeof engine.execute === 'function') method = engine.execute;
      if (method) {
        try { return { called: true, value: method.call(engine, payload) }; } catch (e) {
          setStatus(e && e.message ? e.message : '交易操作失败');
          return { called: true, value: null, error: e };
        }
      }
    }
    return { called: false, value: null };
  }

  function normalizeTradePayload(side, point, field, price, amount, quantity, options) {
    var controller = getController();
    var replay = controllerState() || {};
    var context = global.UIState && typeof global.UIState.snapshot === 'function'
      ? global.UIState.snapshot() : {};
    var payload = {
      side: side,
      action: side,
      index: point.index,
      dataIndex: point.index,
      barIndex: point.index,
      cursor: replay.cursor != null ? replay.cursor : point.index,
      timestamp: timestamp(point.row.timestamp),
      price: Number(price),
      priceField: field,
      field: field,
      amount: amount == null ? undefined : Number(amount),
      quantity: quantity == null ? undefined : Number(quantity),
      symbol: context.symbol || context.selected || null,
      replayState: replay,
      source: 'replay-trade-ui',
    };
    options = options || {};
    if (Array.isArray(options.orderNumbers) && options.orderNumbers.length) {
      payload.orderNumbers = options.orderNumbers.map(Number);
    }
    return payload;
  }

  function addLocalRecord(payload) {
    var record = Object.assign({}, payload, {
      id: 'local-' + Date.now() + '-' + state.localRecords.length,
      status: 'filled',
    });
    state.localRecords.push(record);
    return record;
  }

  function executeTrade(side, point, field, price, amount, quantity, options) {
    var payload = normalizeTradePayload(side, point, field, price, amount, quantity, options);
    var names = side === 'buy'
      ? ['openManual', 'manualBuy', 'buy', 'executeBuy', 'openPosition', 'placeBuy']
      : ['closeManual', 'manualSell', 'sell', 'executeSell', 'closePosition', 'placeSell'];
    var result = invokeEngine(names, payload);
    if (!result.called) {
      addLocalRecord(payload);
      emit('replay-trade-command', payload);
    } else if (result.value === false) {
      var failedState = null;
      try { failedState = getEngine().getState(); } catch (e) {}
      setStatus(failedState && failedState.lastError ? failedState.lastError : '交易操作未执行');
    } else if (side === 'buy') {
      var rows = rowsForChart();
      var currentIndex = rows.length - 1;
      var engine = getEngine();
      if (engine && currentIndex >= 0 && typeof engine.markToMarket === 'function') {
        try { engine.markToMarket(rows[currentIndex], currentIndex); } catch (e) {}
      }
    }
    if (result.called && result.value !== false) {
      state.localPresets[side] = null;
      if (side === 'sell') {
        state.localPresets.takeProfit = null;
        state.localPresets.stopLoss = null;
      }
      try { state.tradeState = getEngine().getState(); } catch (e) {}
      var completedSummary = performance(rowsForChart(), flattenTradeState(state.tradeState || {}));
      if (side === 'buy') {
        setStatus('已买入，播放中按每根 K 线收盘价同步计算获利');
      } else if (completedSummary && completedSummary.kind === 'settled') {
        setStatus('已统一结算：' + pctText(completedSummary.pct) + ' · ' +
          currencyText(completedSummary.amount, true));
      } else {
        setStatus('卖出完成，持仓已结算');
      }
    }
    cancelSelection();
    closePanel(state.picker);
    consumeEngineResult(result.value);
    redraw();
    renderRecordsPanel();
    return result.value;
  }

  function consumeEngineResult(result) {
    if (!result) return;
    if (typeof result.then === 'function') {
      result.then(function (value) { if (value) { state.tradeState = value; redraw(); renderRecordsPanel(); } })
        .catch(function () {});
    } else if (typeof result === 'object') {
      state.tradeState = result;
    }
  }

  function setStatus(message) {
    var node = byId('bar-replay-status');
    if (node && message) {
      text(node, message);
      node.title = message;
    }
  }

  function panelBase(id, titleText, width) {
    var document = getDocument();
    if (!document) return null;
    var panel = byId(id) || create('div');
    if (!panel) return null;
    panel.id = id;
    panel.className = 'replay-trade-panel';
    panel.hidden = false;
    setStyle(panel, { width: (width || 300) + 'px' });
    clear(panel);
    var heading = create('div');
    heading.className = 'replay-trade-heading';
    var title = create('span');
    text(title, titleText);
    append(heading, title);
    createCloseButton(heading, '关闭', function () {
      closePanel(panel);
      if (id === 'replay-trade-presets') cancelPresetSelection(false);
    });
    append(panel, heading);
    if (!panel.parentNode) append(document.body || document.documentElement, panel);
    positionPanel(panel, (global.innerWidth || 1000) - (width || 300) - 20, 72, width || 300);
    return panel;
  }

  function openPresetPanel() {
    if (!replayActive()) { setStatus('请先开启回放'); return false; }
    cancelPresetSelection(true);
    closePanel(state.picker);
    closePanel(state.recordsPanel);
    var panel = panelBase('replay-trade-presets', '交易预设', 312);
    if (!panel) return false;
    state.presetPanel = panel;
    var note = create('div');
    note.className = 'replay-trade-note';
    text(note, '在图表上选择任意水平价格，播放到该价格区间时自动触发。');
    append(panel, note);

    var buySection = create('div');
    buySection.className = 'replay-trade-section';
    var buyTitle = create('strong');
    text(buyTitle, '预设买入');
    append(buySection, buyTitle);
    var buyRow = create('div');
    buyRow.className = 'replay-trade-row';
    var buyAmountLabel = create('label');
    text(buyAmountLabel, '金额');
    var buyAmount = create('input');
    buyAmount.id = 'replay-trade-preset-buy-amount';
    buyAmount.type = 'number';
    buyAmount.min = '0.01';
    buyAmount.step = '0.01';
    var activePresets = currentTradeState().presets || {};
    var activeBuyPreset = state.localPresets.buy || activePresets.buy || null;
    var activeSellPreset = state.localPresets.sell || activePresets.sell || null;
    buyAmount.value = activeBuyPreset && activeBuyPreset.amount ? String(activeBuyPreset.amount) : String(DEFAULT_AMOUNT);
    append(buyAmountLabel, buyAmount);
    append(buyRow, buyAmountLabel);
    append(buySection, buyRow);
    var buySelection = create('div');
    buySelection.className = 'replay-trade-preset-selection';
    text(buySelection, activeBuyPreset
      ? '当前预设：¥' + numberText(activeBuyPreset.price)
      : '尚未选择买入价格');
    append(buySection, buySelection);
    var buyActions = create('div');
    buyActions.className = 'replay-trade-actions';
    var buySubmit = create('button');
    buySubmit.id = 'replay-trade-preset-buy-select';
    buySubmit.type = 'button';
    buySubmit.className = 'replay-trade-preset-select';
    text(buySubmit, '在图表选择买入点');
    buySubmit.setAttribute('aria-label', '在图表选择买入点');
    buySubmit.addEventListener('click', function () {
      var amount = finite(inputValue('replay-trade-preset-buy-amount', DEFAULT_AMOUNT));
      enterPresetSelection('buy', amount);
    });
    var buyCancel = create('button');
    buyCancel.type = 'button';
    text(buyCancel, '取消买入');
    buyCancel.addEventListener('click', function () { cancelPreset('buy'); });
    append(buyActions, buySubmit);
    append(buyActions, buyCancel);
    append(buySection, buyActions);
    append(panel, buySection);

    var sellSection = create('div');
    sellSection.className = 'replay-trade-section';
    var sellTitle = create('strong');
    text(sellTitle, '预设卖出');
    append(sellSection, sellTitle);
    var sellSelection = create('div');
    sellSelection.className = 'replay-trade-preset-selection';
    text(sellSelection, activeSellPreset
      ? '当前预设：¥' + numberText(activeSellPreset.price)
      : '尚未选择卖出价格');
    append(sellSection, sellSelection);
    var sellActions = create('div');
    sellActions.className = 'replay-trade-actions';
    var sellSubmit = create('button');
    sellSubmit.id = 'replay-trade-preset-sell-select';
    sellSubmit.type = 'button';
    sellSubmit.className = 'replay-trade-preset-select';
    text(sellSubmit, '在图表选择卖出点');
    sellSubmit.setAttribute('aria-label', '在图表选择卖出点');
    sellSubmit.addEventListener('click', function () { enterPresetSelection('sell', null); });
    var sellCancel = create('button');
    sellCancel.type = 'button';
    text(sellCancel, '取消卖出');
    sellCancel.addEventListener('click', function () { cancelPreset('sell'); });
    append(sellActions, sellSubmit);
    append(sellActions, sellCancel);
    append(sellSection, sellActions);
    append(panel, sellSection);

    appendBracketPresetSection(panel, 'takeProfit', '止盈卖出', '在图表选择止盈点',
      state.localPresets.takeProfit || activePresets.takeProfit || activePresets.take_profit || null);
    appendBracketPresetSection(panel, 'stopLoss', '止损卖出', '在图表选择止损点',
      state.localPresets.stopLoss || activePresets.stopLoss || activePresets.stop_loss || null);
    return true;
  }

  function appendBracketPresetSection(panel, role, titleText, actionText, order) {
    var section = create('div');
    section.className = 'replay-trade-section';
    var title = create('strong');
    text(title, titleText);
    append(section, title);
    var selection = create('div');
    selection.className = 'replay-trade-preset-selection';
    text(selection, order && finite(order.price) != null
      ? '当前预设：¥' + numberText(order.price)
      : '尚未选择' + presetRoleLabel(role) + '价格');
    append(section, selection);
    var actions = create('div');
    actions.className = 'replay-trade-actions';
    var select = create('button');
    select.id = 'replay-trade-preset-' + role + '-select';
    select.type = 'button';
    select.className = 'replay-trade-preset-select';
    text(select, actionText);
    select.setAttribute('aria-label', actionText);
    select.addEventListener('click', function () { enterPresetSelection(role, null); });
    var cancel = create('button');
    cancel.type = 'button';
    text(cancel, '取消' + presetRoleLabel(role));
    cancel.addEventListener('click', function () { cancelPreset(role); });
    append(actions, select);
    append(actions, cancel);
    append(section, actions);
    append(panel, section);
  }

  function submitPresetAtPrice(side, price, amount, point, quantity) {
    side = presetRole(side);
    price = finite(price);
    if (!side || price == null || price <= 0 || (side === 'buy' && (amount == null || amount <= 0))) {
      setStatus(side === 'buy' ? '买入预设需要有效价格和金额' : presetRoleLabel(side) + '预设需要有效价格');
      cancelPresetSelection(true);
      return false;
    }
    var payload = {
      side: side,
      role: side,
      orderRole: side,
      action: 'preset-' + side,
      price: price,
      amount: side === 'buy' ? Number(amount) : undefined,
      quantity: side === 'buy' ? undefined : finite(quantity),
      priceField: 'preset',
      field: null,
      cursor: (controllerState() || {}).cursor,
      selection: 'chart-horizontal-price',
      anchorY: point && point.y != null ? Number(point.y) : undefined,
      source: 'replay-trade-ui',
    };
    var names = side === 'buy'
      ? ['setPendingOrder', 'setBuyPreset', 'presetBuy', 'setPresetBuy']
      : side === 'sell'
        ? ['setPendingOrder', 'setSellPreset', 'presetSell', 'setPresetSell']
        : ['setPendingOrder', 'updatePendingOrder'];
    var result = invokeEngine(names, payload);
    state.localPresets[side] = result.called ? null : {
      price: price,
      amount: side === 'buy' ? Number(amount) : null,
      quantity: side === 'buy' ? null : finite(quantity),
      side: side,
      priceField: 'preset',
      anchorX: point && point.x != null ? Number(point.x) : null,
      anchorY: point && point.y != null ? Number(point.y) : null,
      source: 'ui',
    };
    if (!result.called) emit('replay-trade-preset', payload);
    consumeEngineResult(result.value);
    setStatus('已设置预设' + presetRoleLabel(side));
    cancelPresetSelection(true);
    closePanel(state.presetPanel);
    redraw();
    return true;
  }

  function cancelPreset(side) {
    var request = side && typeof side === 'object' ? side : null;
    side = presetRole(request ? (request.side || request.role || request.type) : side);
    if (state.presetSelection.active && state.presetSelection.side === side) cancelPresetSelection(true);
    var payload = { side: side, action: 'cancel-preset-' + side, source: 'replay-trade-ui' };
    var engine = getEngine();
    var result = { called: false, value: null };
    if (engine && typeof engine.cancelPending === 'function') {
      try { result = { called: true, value: engine.cancelPending(request ? Object.assign({}, request, { side: side }) : side) }; }
      catch (e) { result = { called: true, value: null, error: e }; }
    }
    var names = side === 'buy'
      ? ['cancelBuyPreset', 'clearBuyPreset']
      : side === 'sell'
        ? ['cancelSellPreset', 'clearSellPreset']
        : [];
    if (!result.called) result = invokeEngine(names, payload);
    if (!result.called) {
      var generic = invokeEngine(['cancelPreset', 'clearPreset'], payload);
      result = generic;
      if (!generic.called) emit('replay-trade-preset-cancel', payload);
    }
    state.localPresets[side] = null;
    setStatus('已取消预设' + presetRoleLabel(side));
    closePanel(state.presetPanel);
    redraw();
    return result.value;
  }

  function readRecords() {
    var normalized = currentTradeState();
    return normalized.records.length ? normalized.records : state.localRecords;
  }

  function renderRecordsPanel() {
    if (!state.recordsPanel || state.recordsPanel.hidden) return;
    var panel = panelBase('replay-trade-records-panel', '交易记录', 340);
    if (!panel) return;
    state.recordsPanel = panel;
    var list = create('div');
    list.className = 'replay-trade-list';
    var records = readRecords();
    if (!records.length) {
      var empty = create('div');
      empty.className = 'replay-trade-muted';
      text(empty, '暂无成交记录');
      append(list, empty);
    } else {
      records.forEach(function (record) {
        var row = create('div');
        row.className = 'replay-trade-record';
        var side = sideOf(record.side || record.action || record.type);
        var price = finite(record.price != null ? record.price : record.executionPrice);
        var amount = finite(record.amount != null ? record.amount : record.value);
        var quantity = finite(record.quantity != null ? record.quantity : record.shares);
        var pnl = finite(record.pnl != null ? record.pnl : record.profit);
        var recordNumbers = side === 'sell' && Array.isArray(record.orderNumbers)
          ? record.orderNumbers : [Math.max(1, Math.round(finite(record.orderNumber) || 1))];
        var recordLabel = side === 'buy'
          ? 'B' + recordNumbers.join('/B') : side === 'sell' ? 'S' + recordNumbers.join('/S') : '交易';
        text(row, recordLabel + ' ' + (side === 'buy' ? '买入' : side === 'sell' ? '卖出' : '') +
          ' · ' + dateText(record.timestamp || record.time || record.date) +
           ' · ' + fieldLabel(record.priceField || record.field) +
           ' ' + numberText(price) +
           (quantity != null ? ' · 数量 ' + numberText(quantity, 4) : '') +
           (amount != null ? ' · 金额 ' + numberText(amount) : '') +
          (pnl != null ? ' · 盈亏 ' + moneyText(pnl) : ''));
        append(list, row);
      });
    }
    append(panel, list);
  }

  function openRecordsPanel() {
    if (!replayActive()) { setStatus('请先开启回放'); return false; }
    closePanel(state.picker);
    closePanel(state.presetPanel);
    var panel = panelBase('replay-trade-records-panel', '交易记录', 340);
    if (!panel) return false;
    state.recordsPanel = panel;
    renderRecordsPanel();
    return true;
  }

  function positionAggregate(position) {
    return tradeStateModel.positionAggregate(position);
  }

  function flattenTradeState(raw) {
    return tradeStateModel.flattenTradeState(raw);
  }

  function currentTradeState() {
    var engine = getEngine();
    var raw = state.tradeState;
    if (engine) {
      try {
        if (typeof engine.getState === 'function') raw = engine.getState() || raw;
        else if (typeof engine.snapshot === 'function') raw = engine.snapshot() || raw;
        else if (engine.state) raw = engine.state;
      } catch (e) {}
    }
    return flattenTradeState(raw || {});
  }

  function rowForRecord(record, rows) {
    var index = indexFromValue(record.index, rows);
    if (index >= 0) return { index: index, row: rows[index] };
    var ts = timestamp(record.timestamp);
    if (ts != null) {
      for (var i = 0; i < rows.length; i += 1) {
        if (timestamp(rows[i].timestamp) === ts) return { index: i, row: rows[i] };
      }
    }
    return null;
  }

  function convertPrice(index, price, rows, width, height) {
    var chart = getChart();
    if (chart && typeof chart.convertToPixel === 'function') {
      var attempts = replayGeometry.priceConversionInputs(index, price, rows);
      for (var attemptIndex = 0; attemptIndex < attempts.length; attemptIndex += 1) {
        try {
          var point = replayGeometry.convertedPixelPoint(
            chart.convertToPixel(attempts[attemptIndex], { paneId: 'candle_pane' })
          );
          if (point) return point;
        } catch (e) {}
      }
    }
    return replayGeometry.legacyPriceToPixel(index, price, rows, width, height);
  }

  function openBuyRecords(normalized) {
    normalized = normalized || currentTradeState();
    var records = normalized.records || [];
    var lots = Array.isArray(normalized.openLots) ? normalized.openLots : [];
    var openIds = Object.create(null);
    lots.forEach(function (lot) {
      var id = lot && (lot.executionId || lot.id);
      if (id) openIds[String(id)] = lot;
    });
    return records.filter(function (record) {
      if (!record || record.side !== 'buy' || !record.id) return false;
      if (Object.keys(openIds).length) return !!openIds[String(record.id)];
      var remaining = finite(record.remainingQuantity != null ? record.remainingQuantity : record.quantity);
      return remaining == null || remaining > 1e-10;
    }).map(function (record) {
      var lot = openIds[String(record.id)] || {};
      return Object.assign({}, record, {
        orderNumber: finite(lot.orderNumber) || finite(record.orderNumber),
        remainingQuantity: finite(lot.remainingQuantity != null ? lot.remainingQuantity : lot.quantity) ||
          finite(record.remainingQuantity != null ? record.remainingQuantity : record.quantity),
        amount: finite(lot.remainingAmount != null ? lot.remainingAmount : lot.cost) || finite(record.amount),
      });
    });
  }

  function ensureBracketDrafts(normalized) {
    var opens = openBuyRecords(normalized);
    var brackets = Array.isArray(normalized && normalized.bracketOrders)
      ? normalized.bracketOrders : [];
    var engine = getEngine();
    opens.forEach(function (record, index) {
      var id = String(record.id);
      var entryPrice = finite(record.price);
      if (entryPrice == null || entryPrice <= 0) return;
      var orderNumber = Math.max(1, Math.round(finite(record.orderNumber) || index + 1));
      var created = state.bracketDraftedExecutionIds[id];
      if (!created || created === true) created = state.bracketDraftedExecutionIds[id] = {};
      ['takeProfit', 'stopLoss'].forEach(function (side) {
        var exists = brackets.some(function (order) {
          return presetRole(order && (order.side || order.role || order.type)) === side &&
            (order.executionIds || []).some(function (executionId) {
              return String(executionId) === id;
            });
        });
        if (exists) {
          created[side] = true;
          return;
        }
        if (created[side] || !engine || typeof engine.setPendingOrder !== 'function') return;
        created[side] = true;
        try {
          engine.setPendingOrder({
            side: side,
            price: side === 'takeProfit' ? entryPrice * 1.05 : entryPrice * 0.95,
            executionIds: [id],
            orderNumbers: [orderNumber],
            source: 'replay-auto-bracket',
          });
        } catch (error) {
          created[side] = false;
        }
      });
    });
    state.bracketDraft = null;
    state.bracketDrafts = [];
  }

  function bracketDraftLots(draft, normalized) {
    if (!draft) return [];
    var selected = Object.create(null);
    (draft.executionIds || []).forEach(function (id) { selected[String(id)] = true; });
    return openBuyRecords(normalized).filter(function (record) { return selected[String(record.id)]; });
  }

  function bracketExpected(draft, role, normalized) {
    var price = role === 'takeProfit' ? finite(draft.takeProfitPrice) : finite(draft.stopLossPrice);
    var lots = bracketDraftLots(draft, normalized);
    var invested = 0;
    var pnl = 0;
    lots.forEach(function (lot) {
      var entry = finite(lot.price);
      var quantity = finite(lot.remainingQuantity != null ? lot.remainingQuantity : lot.quantity);
      if (entry == null || quantity == null || price == null) return;
      invested += entry * quantity;
      pnl += (price - entry) * quantity;
    });
    var fallbackAmount = finite(draft.amount);
    var fallbackQuantity = finite(draft.quantity);
    if (!lots.length && price != null && finite(draft.entryPrice) != null && fallbackQuantity != null) {
      invested = fallbackAmount != null ? fallbackAmount : draft.entryPrice * fallbackQuantity;
      pnl = (price - draft.entryPrice) * fallbackQuantity;
    }
    return {
      amount: pnl,
      percent: invested > 0 ? pnl / invested * 100 : null,
      quantity: lots.reduce(function (sum, lot) {
        return sum + Number(finite(lot.remainingQuantity != null ? lot.remainingQuantity : lot.quantity) || 0);
      }, 0) || fallbackQuantity || 0,
    };
  }

  function clearBracketDraftDrag() {
    state.bracketDraftDrag.unbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    state.bracketDraftDrag.unbinds = [];
    state.bracketDraftDrag.active = false;
    state.bracketDraftDrag.role = null;
    state.bracketDraftDrag.price = null;
  }

  function updateBracketDraftDrag(event) {
    var drag = state.bracketDraftDrag;
    var draft = state.bracketDraft;
    if (!drag.active || !draft) return;
    var point = convertPixelToPrice(event || {}, getChart(), state.mainDom);
    var price = point && finite(point.price);
    if (price == null || price <= 0) return;
    if (drag.role === 'entry') {
      var delta = price - Number(draft.entryPrice);
      draft.entryPrice = price;
      draft.takeProfitPrice = Math.max(price * 1.0001, Number(draft.takeProfitPrice) + delta);
      draft.stopLossPrice = Math.min(price * 0.9999, Number(draft.stopLossPrice) + delta);
    } else if (drag.role === 'takeProfit') draft.takeProfitPrice = price;
    else if (drag.role === 'stopLoss') draft.stopLossPrice = price;
    drag.price = price;
    redraw();
  }

  function finishBracketDraftDrag(event) {
    var drag = state.bracketDraftDrag;
    var draft = state.bracketDraft;
    if (!drag.active || !draft) return;
    updateBracketDraftDrag(event);
    var role = drag.role;
    clearBracketDraftDrag();
    if (role === 'entry') {
      var record = openBuyRecords(currentTradeState()).filter(function (item) {
        return String(item.id) === String(draft.primaryExecutionId);
      })[0];
      var engine = getEngine();
      if (record && engine && typeof engine.updateExecution === 'function') {
        try { engine.updateExecution(record.id, { price: draft.entryPrice, amount: record.amount }); }
        catch (e) { setStatus('买入点修改失败'); }
      }
    }
    setStatus('止盈止损点已调整，点击确认后生效');
    redraw();
  }

  function beginBracketDraftDrag(event, draft, role) {
    if (!draft) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    clearBracketDraftDrag();
    state.bracketDraft = draft;
    state.bracketDraftDrag.active = true;
    state.bracketDraftDrag.role = role;
    var move = function (moveEvent) { updateBracketDraftDrag(moveEvent); };
    var up = function (upEvent) { finishBracketDraftDrag(upEvent); };
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('mousemove', move);
      global.addEventListener('mouseup', up);
      state.bracketDraftDrag.unbinds.push(function () { global.removeEventListener('mousemove', move); });
      state.bracketDraftDrag.unbinds.push(function () { global.removeEventListener('mouseup', up); });
    }
    if (state.bracketConfirmBar) state.bracketConfirmBar.hidden = false;
    setStatus(role === 'entry' ? '上下拖动修改买入点' : role === 'takeProfit' ? '向上拖动设置止盈点' : '向下拖动设置止损点');
  }

  function removeBracketDraft(draft) {
    state.bracketDrafts = state.bracketDrafts.filter(function (item) { return item !== draft; });
    state.bracketDraft = state.bracketDrafts.length ? state.bracketDrafts[state.bracketDrafts.length - 1] : null;
    if (state.bracketConfirmBar) state.bracketConfirmBar.hidden = !state.bracketDraft;
  }

  function discardBracketDraft() {
    if (!state.bracketDraft) return false;
    removeBracketDraft(state.bracketDraft);
    setStatus('已放弃本笔止盈止损设置');
    redraw();
    return true;
  }

  function confirmBracketDraft() {
    var draft = state.bracketDraft;
    if (!draft) return false;
    var entry = finite(draft.entryPrice);
    var target = finite(draft.takeProfitPrice);
    var stop = finite(draft.stopLossPrice);
    if (!(target > entry && stop < entry && stop > 0)) {
      setStatus('多头止盈必须高于买入价，止损必须低于买入价');
      return false;
    }
    var normalized = currentTradeState();
    var expectedTarget = bracketExpected(draft, 'takeProfit', normalized);
    var expectedStop = bracketExpected(draft, 'stopLoss', normalized);
    var shared = {
      executionIds: (draft.executionIds || []).slice(),
      orderNumbers: (draft.orderNumbers || []).slice(),
      quantity: expectedTarget.quantity || undefined,
      source: 'replay-bracket-confirm',
    };
    var engine = getEngine();
    var ok = !!engine;
    if (engine && typeof engine.setPendingOrder === 'function') {
      ok = engine.setPendingOrder(Object.assign({ side: 'takeProfit', price: target }, shared)) !== false && ok;
      ok = engine.setPendingOrder(Object.assign({ side: 'stopLoss', price: stop, quantity: expectedStop.quantity || undefined }, shared)) !== false && ok;
    } else ok = false;
    if (!ok) {
      setStatus('交易引擎未能保存止盈止损设置');
      return false;
    }
    removeBracketDraft(draft);
    try { state.tradeState = engine.getState(); } catch (e) {}
    setStatus('止盈止损已确认并生效');
    redraw();
    return true;
  }

  function openBracketOrdersPanel(draft) {
    if (!draft) return false;
    state.bracketDraft = draft;
    var panel = panelBase('replay-trade-bracket-orders-panel', '选择止盈止损订单', 300);
    if (!panel) return false;
    state.bracketOrdersPanel = panel;
    var note = create('div');
    note.className = 'replay-trade-note';
    text(note, '勾选需要由这组止盈止损管理的买入订单。');
    append(panel, note);
    var selected = Object.create(null);
    (draft.executionIds || []).forEach(function (id) { selected[String(id)] = true; });
    var checks = [];
    openBuyRecords(currentTradeState()).forEach(function (record, index) {
      var label = create('label');
      label.className = 'replay-trade-field replay-trade-order-choice';
      var checkbox = create('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !!selected[String(record.id)];
      checkbox.value = String(record.id);
      checkbox._orderNumber = Math.max(1, Math.round(finite(record.orderNumber) || index + 1));
      checks.push(checkbox);
      append(label, checkbox);
      var labelText = create('span');
      text(labelText, 'B' + checkbox._orderNumber + ' · 投入 ' + currencyText(record.amount) + ' · 买入 ' + numberText(record.price));
      append(label, labelText);
      append(panel, label);
    });
    var actions = create('div');
    actions.className = 'replay-trade-actions';
    var apply = create('button');
    apply.type = 'button';
    text(apply, '应用');
    apply.addEventListener('click', function () {
      var picked = checks.filter(function (item) { return !!item.checked; });
      if (!picked.length) { setStatus('至少选择一笔订单'); return; }
      draft.executionIds = picked.map(function (item) { return item.value; });
      draft.orderNumbers = picked.map(function (item) { return item._orderNumber; });
      closePanel(panel);
      redraw();
    });
    append(actions, apply);
    append(panel, actions);
    return true;
  }

  function drawBracketDraft(svg, draft, width, height, rows, normalized) {
    if (!draft) return;
    var roles = [
      { role: 'takeProfit', price: draft.takeProfitPrice, colorClass: 'takeProfit' },
      { role: 'entry', price: draft.entryPrice, colorClass: 'entry' },
      { role: 'stopLoss', price: draft.stopLossPrice, colorClass: 'stopLoss' },
    ];
    roles.forEach(function (item) {
      var price = finite(item.price);
      if (price == null) return;
      var point = convertPrice(null, price, rows, width, height);
      var expected = item.role === 'entry' ? null : bracketExpected(draft, item.role, normalized);
      var orderText = (draft.orderNumbers || []).join(',');
      var labelText = item.role === 'entry'
        ? 'B' + orderText + ' 买入 ' + numberText(price)
        : (item.role === 'takeProfit' ? '止盈 ' : '止损 ') + orderText + ' · ' +
          pctText(expected && expected.percent) + ' · ' + currencyText(expected && expected.amount, true);
      var rendered = overlayRenderer.renderBracketLevel(rendererAdapter, svg, {
        colorClass: item.colorClass,
        role: item.role,
        width: width,
        y: point.y,
        labelX: Math.max(8, width - 300),
        orderText: orderText,
        labelText: labelText,
      });
      if (!rendered) return;
      var group = rendered.group;
      var label = rendered.label;
      if (label && typeof label.addEventListener === 'function') label.addEventListener('click', function (event) {
        if (event && event.stopPropagation) event.stopPropagation();
        openBracketOrdersPanel(draft);
      });
      if (typeof group.addEventListener === 'function') group.addEventListener('mousedown', function (event) {
        beginBracketDraftDrag(event, draft, item.role);
      });
    });
  }

  function marker(svg, point, side, label, title, record, width, meta) {
    var pairColor = record && orderPairColor(record.orderNumber || (record.orderNumbers || [])[0]);
    var markerX = finite(point.x);
    var availableWidth = Math.max(0, finite(width) || 0);
    if (markerX == null) markerX = availableWidth / 2;
    if (availableWidth > 20) markerX = Math.max(10, Math.min(availableWidth - 10, markerX));
    var entryLabel = null;
    if (side === 'buy' && record) {
      var entryPrice = finite(record.price);
      var riskRatio = meta && meta.rewardRisk;
      var levelText = (label || 'B') + '买入 ' + numberText(entryPrice) +
        (riskRatio ? ' · 盈亏比 ' + riskRatio : '');
      var levelLabelWidth = Math.max(150, Math.min(292, 24 + levelText.length * 7));
      var levelLabelX = Math.max(4, availableWidth - levelLabelWidth - 8);
      var levelLabelY = Math.max(2, point.y - 11);
      entryLabel = {
        x: levelLabelX + 8,
        y: levelLabelY,
        width: levelLabelWidth - 12,
        color: pairColor || '#ef4444',
        text: levelText,
      };
    }
    var arrowPoints = side === 'buy' ? [
      markerX + ',' + point.y,
      (markerX - 5) + ',' + (point.y + 7),
      (markerX - 9) + ',' + (point.y + 7),
      (markerX - 9) + ',' + (point.y + 24),
      (markerX + 9) + ',' + (point.y + 24),
      (markerX + 9) + ',' + (point.y + 7),
      (markerX + 5) + ',' + (point.y + 7)
    ] : [
      markerX + ',' + point.y,
      (markerX - 5) + ',' + (point.y - 7),
      (markerX - 9) + ',' + (point.y - 7),
      (markerX - 9) + ',' + (point.y - 24),
      (markerX + 9) + ',' + (point.y - 24),
      (markerX + 9) + ',' + (point.y - 7),
      (markerX + 5) + ',' + (point.y - 7)
    ];
    var rendered = overlayRenderer.renderExecutionMarker(rendererAdapter, svg, {
      side: side,
      label: label,
      title: title,
      width: availableWidth,
      y: point.y,
      markerX: markerX,
      pairColor: pairColor,
      arrowPoints: arrowPoints,
      entryLabel: entryLabel,
    });
    if (!rendered) return;
    var group = rendered.group;
    if (record && record.id && side === 'buy') {
      attr(group, 'role', 'button');
      attr(group, 'tabindex', '0');
      attr(group, 'data-execution-id', record.id);
      var openEditor = function (event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
        if (state.executionDrag.suppressClickId === record.id) {
          state.executionDrag.suppressClickId = null;
          return;
        }
        openExecutionEditor(record, point);
      };
      if (typeof group.addEventListener === 'function') {
        group.addEventListener('mousedown', function (event) {
          beginExecutionDrag(event, record);
        });
        group.addEventListener('click', openEditor);
        group.addEventListener('keydown', function (event) {
          if (event && (event.key === 'Enter' || event.key === ' ')) openEditor(event);
        });
      }
    }
  }

  var ORDER_PAIR_COLORS = ['#ef4444', '#2563eb', '#f59e0b', '#7c3aed', '#0891b2', '#db2777'];

  function orderPairColor(orderNumber) {
    var number = Math.max(1, Math.round(finite(orderNumber) || 1));
    return ORDER_PAIR_COLORS[(number - 1) % ORDER_PAIR_COLORS.length];
  }

  function executionAmount(record) {
    if (!record) return null;
    var candidates = [record.amount, record.cost, record.originalAmount, record.remainingAmount, record.value];
    for (var index = 0; index < candidates.length; index += 1) {
      var amount = finite(candidates[index]);
      if (amount != null && amount > 0) return amount;
    }
    return null;
  }

  function clearExecutionDrag() {
    state.executionDrag.unbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    state.executionDrag.unbinds = [];
    state.executionDrag.active = false;
    state.executionDrag.record = null;
    state.executionDrag.price = null;
    state.executionDrag.amount = null;
    state.executionDrag.startY = null;
    state.executionDrag.startX = null;
    state.executionDrag.barIndex = null;
    state.executionDrag.timestamp = null;
    state.executionDrag.moved = false;
  }

  function updateExecutionDrag(event) {
    if (!state.executionDrag.active) return;
    var coordinates = eventCoordinates(event || {}, state.mainDom);
    if ((coordinates.y != null && state.executionDrag.startY != null &&
        Math.abs(Number(coordinates.y) - Number(state.executionDrag.startY)) >= 2) ||
        (coordinates.x != null && state.executionDrag.startX != null &&
        Math.abs(Number(coordinates.x) - Number(state.executionDrag.startX)) >= 2)) {
      state.executionDrag.moved = true;
    }
    var point = convertPixelToPrice(event || {}, getChart(), state.mainDom);
    var rowPoint = getPointFromEvent(event || {}, getChart(), state.mainDom);
    if (!point || finite(point.price) == null || point.price <= 0) return;
    state.executionDrag.price = Number(point.price);
    if (rowPoint) {
      state.executionDrag.barIndex = rowPoint.index;
      state.executionDrag.timestamp = timestamp(rowPoint.row && rowPoint.row.timestamp);
    }
    if (state.executionDrag.moved) redraw();
  }

  function finishExecutionDrag(event) {
    if (!state.executionDrag.active) return;
    updateExecutionDrag(event);
    var record = state.executionDrag.record;
    var price = state.executionDrag.price;
    var amount = state.executionDrag.amount;
    var barIndex = state.executionDrag.barIndex;
    var executionTimestamp = state.executionDrag.timestamp;
    var moved = state.executionDrag.moved;
    clearExecutionDrag();
    if (!moved || !record || price == null || amount == null) return;
    state.executionDrag.suppressClickId = record.id;
    updateExecution(record, price, amount, {
      barIndex: barIndex,
      timestamp: executionTimestamp,
      field: record.priceField || record.field || 'close',
    });
  }

  function beginExecutionDrag(event, record) {
    if (!record || record.side !== 'buy' || !record.id) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    clearExecutionDrag();
    var coordinates = eventCoordinates(event || {}, state.mainDom);
    state.executionDrag.active = true;
    state.executionDrag.record = record;
    state.executionDrag.price = finite(record.price);
    state.executionDrag.amount = executionAmount(record);
    state.executionDrag.startY = coordinates.y;
    state.executionDrag.startX = coordinates.x;
    state.executionDrag.barIndex = finite(record.index != null ? record.index : record.barIndex);
    state.executionDrag.timestamp = timestamp(record.timestamp);
    var move = function (moveEvent) { updateExecutionDrag(moveEvent); };
    var up = function (upEvent) { finishExecutionDrag(upEvent); };
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('mousemove', move);
      global.addEventListener('mouseup', up);
      state.executionDrag.unbinds.push(function () { global.removeEventListener('mousemove', move); });
      state.executionDrag.unbinds.push(function () { global.removeEventListener('mouseup', up); });
    }
    setStatus('上下左右拖动修改 B' + Math.max(1, Math.round(finite(record.orderNumber) || 1)) + ' 的日期和价格');
  }

  function updateExecution(record, price, amount, placement) {
    price = finite(price);
    amount = finite(amount);
    if (!record || !record.id || price == null || price <= 0 || amount == null || amount <= 0) {
      setStatus('买入价格和金额必须大于 0');
      return false;
    }
    var oldPrice = finite(record.price);
    var engine = getEngine();
    var result = null;
    var normalizedBefore = currentTradeState();
    var linkedBrackets = Array.isArray(normalizedBefore.bracketOrders)
      ? normalizedBefore.bracketOrders.filter(function (order) {
        return (order.executionIds || []).some(function (executionId) {
          return String(executionId) === String(record.id);
        });
      }) : [];
    if (engine && typeof engine.updateExecution === 'function') {
      try {
        result = engine.updateExecution(record.id, Object.assign({
          price: Number(price), amount: Number(amount),
        }, placement || {}));
      } catch (error) {
        setStatus(error && error.message ? error.message : '买入标记修改失败');
        return false;
      }
      if (result === false) {
        setStatus('买入标记修改失败');
        return false;
      }
      consumeEngineResult(result);
      try { state.tradeState = engine.getState ? engine.getState() : state.tradeState; } catch (e) {}
    } else {
      var local = state.localRecords.filter(function (item) { return item && item.id === record.id; })[0];
      if (!local) {
        setStatus('当前引擎不支持修改买入记录');
        return false;
      }
      local.price = Number(price);
      local.amount = Number(amount);
      state.tradeState = null;
    }
    var delta = oldPrice == null ? 0 : Number(price) - oldPrice;
    if (engine && typeof engine.updatePendingOrder === 'function' && delta) {
      linkedBrackets.forEach(function (order) {
        var orderPrice = finite(order && order.price);
        if (!order || !order.id || orderPrice == null) return;
        try {
          engine.updatePendingOrder({
            id: order.id,
            side: order.side,
            price: orderPrice + delta,
          });
        } catch (error) {}
      });
    }
    state.bracketDrafts.forEach(function (draft) {
      if (!draft) return;
      var recordId = String(record.id);
      var includesExecution = String(draft.primaryExecutionId) === recordId ||
        (draft.executionIds || []).some(function (id) { return String(id) === recordId; });
      if (!includesExecution) return;
      draft.entryPrice = Number(price);
      if (finite(amount) != null) draft.amount = Number(amount);
      if (Number(price) > 0 && finite(amount) != null) draft.quantity = Number(amount) / Number(price);
      if (finite(draft.takeProfitPrice) != null) draft.takeProfitPrice = Number(draft.takeProfitPrice) + delta;
      if (finite(draft.stopLossPrice) != null) draft.stopLossPrice = Number(draft.stopLossPrice) + delta;
    });
    setStatus('买入标记已更新');
    closePanel(state.editPanel);
    redraw();
    renderRecordsPanel();
    return result === undefined ? true : result;
  }

  function openExecutionEditor(record, point) {
    if (!record || record.side !== 'buy') return false;
    closePanel(state.picker);
    closePanel(state.presetPanel);
    closePanel(state.recordsPanel);
    var panel = panelBase('replay-trade-execution-editor', '编辑买入', 280);
    if (!panel) return false;
    state.editPanel = panel;
    var note = create('div');
    note.className = 'replay-trade-note';
    text(note, '修改后会同步更新成交记录与持仓成本。');
    append(panel, note);

    var priceLabel = create('label');
    priceLabel.className = 'replay-trade-field';
    text(priceLabel, '价格');
    var priceInput = create('input');
    priceInput.id = 'replay-trade-edit-price';
    priceInput.type = 'number';
    priceInput.min = '0.000001';
    priceInput.step = '0.000001';
    priceInput.value = String(record.price == null ? '' : record.price);
    priceInput.setAttribute('aria-label', '买入价格');
    append(priceLabel, priceInput);
    append(panel, priceLabel);

    var amountLabel = create('label');
    amountLabel.className = 'replay-trade-field';
    text(amountLabel, '金额');
    var amountInput = create('input');
    amountInput.id = 'replay-trade-edit-amount';
    amountInput.type = 'number';
    amountInput.min = '0.01';
    amountInput.step = '0.01';
    amountInput.value = String(record.amount != null ? record.amount : record.value != null ? record.value : '');
    amountInput.setAttribute('aria-label', '买入金额');
    append(amountLabel, amountInput);
    append(panel, amountLabel);

    var actions = create('div');
    actions.className = 'replay-trade-actions';
    var save = create('button');
    save.type = 'button';
    text(save, '保存');
    save.setAttribute('aria-label', '保存买入标记');
    save.addEventListener('click', function () {
      updateExecution(record, inputValue('replay-trade-edit-price', record.price),
        inputValue('replay-trade-edit-amount', record.amount));
    });
    var cancel = create('button');
    cancel.type = 'button';
    text(cancel, '取消');
    cancel.addEventListener('click', function () { closePanel(panel); });
    append(actions, save);
    append(actions, cancel);
    append(panel, actions);

    if (point && state.mainDom && typeof state.mainDom.getBoundingClientRect === 'function') {
      var rect = state.mainDom.getBoundingClientRect();
      positionPanel(panel, rect.left + Math.max(8, Number(rect.width || 0) - 300), rect.top + 96, 280);
    }
    return true;
  }

  function clearPresetDrag() {
    state.presetDrag.unbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    state.presetDrag.unbinds = [];
    state.presetDrag.active = false;
    state.presetDrag.role = null;
    state.presetDrag.orderId = null;
    state.presetDrag.price = null;
    state.presetDrag.amount = null;
    state.presetDrag.quantity = null;
  }

  function updatePresetDrag(event) {
    if (!state.presetDrag.active) return;
    var point = convertPixelToPrice(event || {}, getChart(), state.mainDom);
    if (!point || finite(point.price) == null || point.price <= 0) return;
    state.presetDrag.price = Number(point.price);
    redraw();
  }

  function finishPresetDrag(event) {
    if (!state.presetDrag.active) return;
    updatePresetDrag(event);
    var role = state.presetDrag.role;
    var orderId = state.presetDrag.orderId;
    var price = state.presetDrag.price;
    var amount = state.presetDrag.amount;
    var quantity = state.presetDrag.quantity;
    clearPresetDrag();
    if (price == null) return;
    var engine = getEngine();
    if (orderId && engine && typeof engine.updatePendingOrder === 'function') {
      var updated = engine.updatePendingOrder({ id: orderId, side: role, price: price });
      if (updated !== false) {
        try { state.tradeState = engine.getState(); } catch (e) {}
        setStatus('已更新' + presetRoleLabel(role) + '价格');
        redraw();
        return;
      }
    }
    submitPresetAtPrice(role, price, amount, null, quantity);
  }

  function beginPresetDrag(event, role, order) {
    role = presetRole(role);
    if (!role || !order) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    clearPresetDrag();
    state.presetDrag.active = true;
    state.presetDrag.role = role;
    state.presetDrag.orderId = order.id || order.bracketId || null;
    state.presetDrag.price = finite(order.price);
    state.presetDrag.amount = finite(order.amount);
    state.presetDrag.quantity = finite(order.quantity);
    var move = function (moveEvent) { updatePresetDrag(moveEvent); };
    var up = function (upEvent) { finishPresetDrag(upEvent); };
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('mousemove', move);
      global.addEventListener('mouseup', up);
      state.presetDrag.unbinds.push(function () { global.removeEventListener('mousemove', move); });
      state.presetDrag.unbinds.push(function () { global.removeEventListener('mouseup', up); });
    }
    setStatus('上下拖动修改' + presetRoleLabel(role) + '价格');
  }

  function drawRiskZones(svg, normalized, width, height, rows) {
    if (!svg || !normalized || !rows || !rows.length) return;
    var latest = finite(rows[rows.length - 1] && rows[rows.length - 1].close);
    orderTimeline(normalized, latest).forEach(function (item) {
      if (!item || item.closed || !item.buy) return;
      var entryPrice = finite(item.buy.price);
      var located = rowForRecord(item.buy, rows);
      if (entryPrice == null || !located) return;
      var entryPoint = convertPrice(located.index, entryPrice, rows, width, height);
      var startX = Math.max(0, Math.min(width, finite(entryPoint.x) || 0));
      bracketsForOrder(normalized, item.orderNumber).forEach(function (order) {
        var side = presetRole(order && (order.side || order.role || order.type));
        var price = finite(order && order.price);
        if ((side !== 'takeProfit' && side !== 'stopLoss') || price == null) return;
        var targetPoint = convertPrice(null, price, rows, width, height);
        var top = Math.max(0, Math.min(entryPoint.y, targetPoint.y));
        var bottom = Math.min(height, Math.max(entryPoint.y, targetPoint.y));
        if (bottom - top < 1 || width - startX < 1) return;
        overlayRenderer.renderRiskZone(rendererAdapter, svg, {
          side: side,
          orderNumber: item.orderNumber,
          x: startX,
          y: top,
          width: width - startX,
          height: bottom - top,
        });
      });
    });
  }

  function drawPreset(svg, order, side, width, height, rows, referencePrice) {
    if (!order) return;
    var orderId = order.id || order.bracketId || null;
    var draggingThis = state.presetDrag.active && state.presetDrag.role === side &&
      (!state.presetDrag.orderId || state.presetDrag.orderId === orderId);
    var price = draggingThis
      ? finite(state.presetDrag.price)
      : finite(order.price != null ? order.price : order.value);
    if (price == null) return;
    var point = convertPrice(null, price, rows && rows.length ? rows : [{ high: price, low: price }], width, height);
    var relativePct = finite(referencePrice) > 0 && (side === 'takeProfit' || side === 'stopLoss')
      ? (price / Number(referencePrice) - 1) * 100 : null;
    var orderNumbers = Array.isArray(order.orderNumbers) ? order.orderNumbers : [];
    var bindingText = orderNumbers.length ? 'B' + orderNumbers.join('/B') : '';
    var expectedAmount = finite(order.expectedAmount != null ? order.expectedAmount : order.expectedPnlAmount);
    var expectedText = expectedAmount == null ? '' : ' · ' + currencyText(expectedAmount, true);
    var actionText = presetRoleLabel(side);
    var labelPrefix = (side === 'takeProfit' || side === 'stopLoss') && bindingText
      ? bindingText + actionText
      : actionText + (bindingText ? ' ' + bindingText : '');
    var labelText = labelPrefix + ' ' + numberText(price) +
      (relativePct == null ? '' : ' ' + pctText(relativePct)) + expectedText;
    var labelWidth = Math.max(relativePct == null ? 132 : 174, Math.min(310, 28 + labelText.length * 7));
    var labelX = Math.max(4, width - labelWidth - 8);
    var labelY = Math.max(2, Math.min(height - 22, point.y - 11));
    var rendered = overlayRenderer.renderPresetOrder(rendererAdapter, svg, {
      side: side,
      orderId: orderId,
      width: width,
      y: point.y,
      labelText: labelText,
      labelWidth: labelWidth,
      labelX: labelX,
      labelY: labelY,
      ariaLabel: labelText + '，上下拖动修改',
      deleteAriaLabel: '删除' + presetRoleLabel(side) + '预设',
    });
    if (!rendered) return;
    var group = rendered.group;
    var remove = rendered.remove;
    if (remove && typeof remove.addEventListener === 'function') remove.addEventListener('mousedown', function (removeEvent) {
      if (removeEvent && typeof removeEvent.preventDefault === 'function') removeEvent.preventDefault();
      if (removeEvent && typeof removeEvent.stopPropagation === 'function') removeEvent.stopPropagation();
      cancelPreset(orderId ? { side: side, id: orderId } : side);
    });
    if (typeof group.addEventListener === 'function') {
      group.addEventListener('mousedown', function (dragEvent) { beginPresetDrag(dragEvent, side, order); });
    }
  }

  function drawPresetPreview(svg, selection, width, height, rows) {
    if (!selection || !selection.active || selection.previewPrice == null || selection.previewY == null) return;
    var side = presetRole(selection.side);
    var converted = convertPrice(null, selection.previewPrice,
      rows && rows.length ? rows : [{ high: selection.previewPrice, low: selection.previewPrice }], width, height);
    var resolvedY = converted && finite(converted.y) != null ? Number(converted.y) : Number(selection.previewY);
    var y = Math.max(0, Math.min(height, resolvedY));
    overlayRenderer.renderPresetPreview(rendererAdapter, svg, {
      side: side,
      width: width,
      y: y,
      labelX: Math.max(8, width - 10),
      ariaLabel: '预设' + presetRoleLabel(side) + '水平价格 ' + numberText(selection.previewPrice),
      labelText: presetRoleLabel(side) + ' ' + numberText(selection.previewPrice),
    });
  }

  function drawCompletedBracketGhosts(svg, normalized, width, height, rows) {
    var seen = Object.create(null);
    (normalized.completedTrades || []).forEach(function (trade) {
      (trade && trade.plannedBrackets || []).forEach(function (order) {
        var orderNumbers = order.orderNumbers || order.closedOrderNumbers || [];
        orderNumbers.forEach(function (orderNumber) {
          var key = String(orderNumber);
          if (!state.ghostOrders[key]) return;
          var side = presetRole(order.side || order.role || order.type);
          var price = finite(order.price);
          var identity = key + '|' + side + '|' + price;
          if ((side !== 'takeProfit' && side !== 'stopLoss') || price == null || seen[identity]) return;
          seen[identity] = true;
          var point = convertPrice(null, price, rows, width, height);
          overlayRenderer.renderHistoryGhost(rendererAdapter, svg, {
            side: side,
            width: width,
            y: point.y,
            labelX: Math.max(8, width - 250),
            labelText: 'B' + orderNumber + ' 历史' + presetRoleLabel(side) + ' ' + numberText(price),
          });
        });
      });
    });
  }

  function pairRecords(records) {
    var lots = [];
    var pairs = [];
    records.forEach(function (record) {
      if (record.side === 'buy') {
        var quantity = finite(record.quantity);
        if (quantity == null && finite(record.amount) != null && finite(record.price) > 0) {
          quantity = finite(record.amount) / finite(record.price);
        }
        lots.push({ buy: record, remaining: quantity == null ? 1 : quantity });
        return;
      }
      if (record.side !== 'sell' || !lots.length) return;
      var sellQuantity = finite(record.quantity != null ? record.quantity : record.shares);
      if (sellQuantity == null) sellQuantity = lots.reduce(function (total, lot) { return total + lot.remaining; }, 0);
      lots.some(function (lot) {
        if (sellQuantity <= 0 || lot.remaining <= 0) return false;
        var matched = Math.min(lot.remaining, sellQuantity);
        pairs.push({ buy: lot.buy, sell: record, quantity: matched });
        lot.remaining -= matched;
        sellQuantity -= matched;
        return false;
      });
      lots = lots.filter(function (lot) { return lot.remaining > 1e-10; });
    });
    var opens = lots.map(function (lot) { return { record: lot.buy, quantity: lot.remaining }; });
    return { pairs: pairs, opens: opens, open: opens.length ? opens[opens.length - 1].record : null };
  }

  function aggregateOpenRecords(opens) {
    if (!opens || !opens.length) return null;
    return positionAggregate({ lots: opens.map(function (item) {
      var record = item.record || item;
      return { quantity: item.quantity, entryPrice: record.price, amount: record.amount };
    }) });
  }

  function closedPerformance(pairs, pnl) {
    var cost = 0;
    var amount = 0;
    pairs.forEach(function (pair) {
      var buy = finite(pair.buy.price);
      var sell = finite(pair.sell.price);
      var quantity = finite(pair.quantity);
      if (buy == null || sell == null || quantity == null) return;
      cost += buy * quantity;
      amount += (sell - buy) * quantity;
    });
    return {
      cost: cost,
      amount: cost > 0 ? amount : pnl && pnl.realizedAmount != null ? pnl.realizedAmount : amount,
      pct: cost > 0 ? amount / cost * 100 : pnl && pnl.realizedPct != null ? pnl.realizedPct : null,
    };
  }

  function perLotPerformanceDetails(normalized, currentPrice, paired) {
    var lots = Array.isArray(normalized.openLots) ? normalized.openLots : [];
    if (!lots.length && paired && Array.isArray(paired.opens)) {
      lots = paired.opens.map(function (item) {
        var record = item.record || item;
        return Object.assign({}, record, { quantity: item.quantity });
      });
    }
    return lots.map(function (lot, index) {
      var entry = finite(lot.entryPrice != null ? lot.entryPrice : lot.price);
      var quantity = finite(lot.remainingQuantity != null ? lot.remainingQuantity : lot.quantity);
      if (entry == null || quantity == null || quantity <= 0 || currentPrice == null) return null;
      var invested = finite(lot.remainingAmount != null ? lot.remainingAmount : lot.cost != null ? lot.cost : lot.amount);
      if (invested == null) invested = entry * quantity;
      var marketValue = currentPrice * quantity;
      var pnl = marketValue - invested;
      var pct = invested > 0 ? pnl / invested * 100 : null;
      var number = Math.max(1, Math.round(finite(lot.orderNumber) || index + 1));
      return 'B' + number + ' · 投入 ' + currencyText(invested) + ' · ' +
        (pnl >= 0 ? '获利 ' : '亏损 ') + pctText(pct) + ' · ' + currencyText(pnl, true);
    }).filter(Boolean);
  }

  function bracketsForOrder(normalized, orderNumber) {
    return (Array.isArray(normalized.bracketOrders) ? normalized.bracketOrders : []).filter(function (order) {
      return (order.orderNumbers || []).some(function (number) {
        return Number(number) === Number(orderNumber);
      });
    });
  }

  function rewardRiskForOrder(normalized, orderNumber, entryPrice) {
    entryPrice = finite(entryPrice);
    if (entryPrice == null || entryPrice <= 0) return '';
    var plans = bracketsForOrder(normalized, orderNumber);
    var takeProfit = plans.filter(function (order) {
      return presetRole(order && (order.side || order.role || order.type)) === 'takeProfit';
    })[0] || null;
    var stopLoss = plans.filter(function (order) {
      return presetRole(order && (order.side || order.role || order.type)) === 'stopLoss';
    })[0] || null;
    var targetPrice = finite(takeProfit && takeProfit.price);
    var stopPrice = finite(stopLoss && stopLoss.price);
    if (targetPrice == null || stopPrice == null) return '';
    var reward = Math.abs(targetPrice - entryPrice);
    var risk = Math.abs(entryPrice - stopPrice);
    if (!(risk > 0)) return '';
    var ratio = reward / risk;
    if (!Number.isFinite(ratio)) return '';
    return ratio.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1') + ':1';
  }

  function orderTimeline(normalized, currentPrice) {
    var records = (normalized.records || []).slice();
    var buys = records.filter(function (record) { return record && record.side === 'buy'; });
    buys.sort(function (left, right) {
      var leftTime = timestamp(left.timestamp);
      var rightTime = timestamp(right.timestamp);
      if (leftTime !== rightTime) return (leftTime == null ? Infinity : leftTime) - (rightTime == null ? Infinity : rightTime);
      var indexDelta = Number(finite(left.index) || 0) - Number(finite(right.index) || 0);
      if (indexDelta) return indexDelta;
      return Number(finite(left.orderNumber) || 0) - Number(finite(right.orderNumber) || 0);
    });
    return buys.map(function (buy, index) {
      var orderNumber = Math.max(1, Math.round(finite(buy.orderNumber) || index + 1));
      var originalQuantity = finite(buy.originalQuantity != null ? buy.originalQuantity : buy.quantity);
      if (originalQuantity == null && finite(buy.amount) != null && finite(buy.price) > 0) {
        originalQuantity = finite(buy.amount) / finite(buy.price);
      }
      var invested = finite(buy.originalAmount != null ? buy.originalAmount : buy.amount);
      if (invested == null && originalQuantity != null) invested = Number(buy.price) * originalQuantity;
      var openLot = (normalized.openLots || []).filter(function (lot) {
        return Number(lot.orderNumber) === orderNumber || String(lot.executionId) === String(buy.id);
      })[0] || null;
      var remainingQuantity = openLot
        ? finite(openLot.remainingQuantity != null ? openLot.remainingQuantity : openLot.quantity)
        : finite(buy.remainingQuantity);
      if (remainingQuantity == null) remainingQuantity = originalQuantity;
      var settlements = [];
      records.filter(function (record) { return record && record.side === 'sell'; }).forEach(function (sell) {
        (sell.lotSettlements || []).forEach(function (lot) {
          if (Number(lot.orderNumber) === orderNumber || String(lot.executionId) === String(buy.id)) {
            settlements.push({ sell: sell, lot: lot });
          }
        });
      });
      settlements.sort(function (left, right) {
        return Number(timestamp(left.sell.timestamp) || 0) - Number(timestamp(right.sell.timestamp) || 0);
      });
      var realizedAmount = settlements.reduce(function (total, item) {
        return total + Number(finite(item.lot.profitAmount != null ? item.lot.profitAmount : item.lot.pnlAmount) || 0);
      }, 0);
      var proceeds = settlements.reduce(function (total, item) {
        return total + Number(finite(item.lot.proceeds) || 0);
      }, 0);
      var openCost = openLot ? finite(openLot.remainingAmount != null ? openLot.remainingAmount : openLot.cost) : 0;
      if (openCost == null) openCost = Number(remainingQuantity || 0) * Number(buy.price || 0);
      var unrealizedAmount = remainingQuantity > 1e-10 && currentPrice != null
        ? Number(currentPrice) * Number(remainingQuantity) - Number(openCost || 0) : 0;
      var plans = bracketsForOrder(normalized, orderNumber);
      var takeProfit = plans.filter(function (order) { return presetRole(order.side) === 'takeProfit'; })[0] || null;
      var stopLoss = plans.filter(function (order) { return presetRole(order.side) === 'stopLoss'; })[0] || null;
      var lastSettlement = settlements.length ? settlements[settlements.length - 1] : null;
      var trigger = lastSettlement ? String(lastSettlement.sell.trigger || '') : '';
      return {
        orderNumber: orderNumber,
        buy: buy,
        invested: Number(invested || 0),
        originalQuantity: Number(originalQuantity || 0),
        remainingQuantity: Number(remainingQuantity || 0),
        openCost: Number(openCost || 0),
        currentPrice: currentPrice,
        realizedAmount: realizedAmount,
        unrealizedAmount: unrealizedAmount,
        totalAmount: realizedAmount + unrealizedAmount,
        proceeds: proceeds,
        settlements: settlements,
        sell: lastSettlement ? lastSettlement.sell : null,
        sellLot: lastSettlement ? lastSettlement.lot : null,
        takeProfit: takeProfit,
        stopLoss: stopLoss,
        closed: Number(remainingQuantity || 0) <= 1e-10,
        exitReason: trigger.indexOf('stopLoss') >= 0 ? '止损' : trigger.indexOf('takeProfit') >= 0 ? '止盈' : '卖出',
      };
    });
  }

  function timelineDetail(item) {
    var number = item.orderNumber;
    var totalPct = item.invested > 0 ? item.totalAmount / item.invested * 100 : null;
    var summary = item.closed
      ? 'B' + number + ' → S' + number + ' · 已' + item.exitReason + ' · ' + pctText(totalPct) + ' · ' + currencyText(item.totalAmount, true)
      : 'B' + number + ' · 持仓中 · ' + pctText(totalPct) + ' · ' + currencyText(item.totalAmount, true);
    var lines = [
      'B' + number + ' 买入 · ' + dateText(item.buy.timestamp) + ' · 价格 ' + numberText(item.buy.price) + ' · 投入 ' + currencyText(item.invested),
    ];
    if (item.closed && item.sell) {
      lines.push('S' + number + ' 实际' + item.exitReason + ' · ' + dateText(item.sell.timestamp) +
        ' · 价格 ' + numberText(item.sell.price) + ' · 收回 ' + currencyText(item.proceeds));
      lines.push('实际盈亏 · ' + pctText(totalPct) + ' · ' + currencyText(item.totalAmount, true));
    } else {
      if (item.takeProfit) lines.push('预期止盈 · 价格 ' + numberText(item.takeProfit.price) +
        ' · ' + pctText(item.takeProfit.expectedPercent) + ' · ' + currencyText(item.takeProfit.expectedAmount, true));
      if (item.stopLoss) lines.push('预期止损 · 价格 ' + numberText(item.stopLoss.price) +
        ' · ' + pctText(item.stopLoss.expectedPercent) + ' · ' + currencyText(item.stopLoss.expectedAmount, true));
      lines.push('当前盈亏 · ' + pctText(totalPct) + ' · ' + currencyText(item.totalAmount, true));
    }
    var detail = {
      orderNumber: number,
      closed: item.closed,
      summary: summary,
      lines: lines,
    };
    detail.toString = function () { return [summary].concat(lines).join(' '); };
    return detail;
  }

  function performance(rows, normalized) {
    var paired = pairRecords(normalized.records);
    var latest = rows.length ? finite(rows[rows.length - 1].close) : null;
    var pnl = normalized.pnl || {};
    var aggregate = normalized.positionSummary || aggregateOpenRecords(paired.opens);
    var timeline = orderTimeline(normalized, latest);
    var timelineDetails = timeline.map(timelineDetail);
    var totalInvested = timeline.reduce(function (total, item) { return total + Number(item.invested || 0); }, 0);
    if (aggregate && latest != null) {
      var entryPrice = finite(aggregate.weightedEntryPrice);
      var amount = finite(aggregate.investedAmount);
      var quantity = finite(aggregate.quantity);
      var currentPrice = finite(aggregate.currentPrice);
      if (currentPrice == null) currentPrice = latest;
      var marketValue = finite(aggregate.marketValue);
      if (marketValue == null) marketValue = currentPrice * quantity;
      var unrealizedAmount = pnl.unrealizedAmount != null ? pnl.unrealizedAmount : marketValue - amount;
      var unrealizedPct = pnl.unrealizedPct != null ? pnl.unrealizedPct : amount > 0 ? unrealizedAmount / amount * 100 : null;
      var realizedAmount = pnl.realizedAmount != null ? pnl.realizedAmount : 0;
      var totalAmount = realizedAmount + unrealizedAmount;
      return {
        kind: 'holding',
        label: Number(totalAmount) >= 0 ? '总持仓获利' : '总持仓亏损',
        pct: totalInvested > 0 ? totalAmount / totalInvested * 100 : unrealizedPct,
        amount: totalAmount,
        realizedAmount: realizedAmount,
        unrealizedAmount: unrealizedAmount,
        timeline: timeline,
        details: timelineDetails.concat([
          '持仓 ' + (aggregate.lotCount || 1) + ' 笔 · 加权价格 ' + numberText(entryPrice) +
            ' · 剩余 ' + numberText(quantity, 4),
          '已实现 ' + currencyText(realizedAmount, true) + ' · 未实现 ' + currencyText(unrealizedAmount, true),
          '投入 ' + currencyText(amount) + ' · 市值 ' + currencyText(marketValue) +
            ' · 现价 ' + numberText(currentPrice),
        ]),
      };
    }
    var settlement = normalized.settlement;
    if (settlement) {
      var closed = closedPerformance(paired.pairs, pnl);
      var cost = finite(settlement.investedAmount != null ? settlement.investedAmount :
        (settlement.cost != null ? settlement.cost : settlement.amount));
      var proceeds = finite(settlement.proceeds != null ? settlement.proceeds : settlement.exitAmount);
      var profitAmount = finite(settlement.profitAmount != null ? settlement.profitAmount : settlement.pnlAmount);
      var profitPct = finite(settlement.profitPercent != null ? settlement.profitPercent : settlement.pnlPercent);
      if (closed.cost > 0) {
        cost = closed.cost;
        profitAmount = closed.amount;
        profitPct = closed.pct;
      } else {
        if (profitAmount == null) profitAmount = closed.amount;
        if (profitPct == null) profitPct = closed.pct;
      }
      if (cost != null && profitAmount != null) proceeds = cost + profitAmount;
      return {
        kind: 'settled',
        label: Number(profitAmount) >= 0 ? '总交易获利（已结算）' : '总交易亏损（已结算）',
        pct: profitPct,
        amount: profitAmount,
        timeline: timeline,
        details: timelineDetails.concat([
          '成交 ' + timeline.length + ' 笔 · 投入 ' + currencyText(totalInvested || cost) +
            ' · 卖出 ' + currencyText(proceeds),
          '累计已实现 ' + currencyText(profitAmount, true) + ' · 剩余 0',
        ]),
      };
    }
    if (paired.pairs.length) {
      var realized = closedPerformance(paired.pairs, pnl);
      return {
        kind: 'settled',
        label: '已实现盈亏',
        pct: realized.pct,
        amount: realized.amount,
        timeline: timeline,
      };
    }
    return null;
  }

  function summaryText(node, value, x, y, className) {
    var item = create('text', true);
    attr(item, 'class', className || 'replay-trade-summary-line');
    attr(item, 'x', x); attr(item, 'y', y);
    text(item, value);
    append(node, item);
    return item;
  }

  function openOrderSellPicker(orderNumber, event) {
    var engine = getEngine();
    if (!engine || typeof engine.closeManual !== 'function') {
      setStatus('交易引擎不支持逐笔卖出');
      return false;
    }
    var replay = controllerState() || {};
    var rows = rowsForChart();
    var index = finite(replay.cursor);
    if (index == null) index = rows.length - 1;
    var detailBar = state.lastReplayDetail && state.lastReplayDetail.bar;
    var bar = detailBar || rows[Math.min(rows.length - 1, Math.max(0, Number(index)))] || null;
    if (!bar || index < 0) {
      setStatus('当前回放 K 线没有可用价格');
      return false;
    }
    if (orderPositionQuantity(orderNumber) == null) {
      setStatus('B' + orderNumber + ' 当前没有可卖持仓');
      return false;
    }
    cancelSelection();
    closeTransientPanels();
    var rect = state.mainDom && typeof state.mainDom.getBoundingClientRect === 'function'
      ? state.mainDom.getBoundingClientRect() : { left: 0, top: 0 };
    var clientX = finite(event && event.clientX);
    var clientY = finite(event && event.clientY);
    var opened = showPricePicker({
      index: Number(index),
      row: bar,
      x: clientX == null ? null : clientX - rect.left,
      y: clientY == null ? null : clientY - rect.top,
    }, 'sell', { orderNumber: Number(orderNumber) });
    if (opened) setStatus('请选择 S' + orderNumber + ' 的卖出价格');
    return opened;
  }

  function drawOrderRail(svg, normalized, rows) {
    if (!svg) return;
    var latest = rows.length ? finite(rows[rows.length - 1].close) : null;
    var timeline = orderTimeline(normalized, latest);
    if (!timeline.length) return;
    var x = 12;
    var top = 12;
    var priceBar = byId('kline-tooltip');
    if (priceBar && state.mainDom && typeof priceBar.getBoundingClientRect === 'function' &&
        typeof state.mainDom.getBoundingClientRect === 'function') {
      try {
        var priceBarRect = priceBar.getBoundingClientRect();
        var mainRect = state.mainDom.getBoundingClientRect();
        var priceBarBottom = finite(priceBarRect && (priceBarRect.bottom != null
          ? priceBarRect.bottom : Number(priceBarRect.top || 0) + Number(priceBarRect.height || 0)));
        var mainTop = finite(mainRect && mainRect.top);
        if (priceBarBottom != null && mainTop != null) top = Math.max(top, priceBarBottom - mainTop + 8);
      } catch (e) {}
    }
    var buttonWidth = 58;
    var pnlWidth = 92;
    var rowWidth = buttonWidth * 2 + pnlWidth;
    var rowHeight = 34;
    timeline.forEach(function (item, index) {
      var y = top + index * (rowHeight + 4);
      var row = create('g', true);
      attr(row, 'class', 'replay-trade-order-rail-row');
      attr(row, 'data-order-number', item.orderNumber);
      var pnlAmount = Number(item.totalAmount || 0);
      var pnlPercent = item.invested > 0 ? pnlAmount / item.invested * 100 : 0;
      var pnlClass = pnlAmount > 0 ? 'positive' : pnlAmount < 0 ? 'negative' : 'flat';
      attr(row, 'data-pnl-amount', pnlAmount);
      attr(row, 'data-pnl-percent', pnlPercent);
      attr(row, 'data-buy-price', finite(item.buy && item.buy.price));
      attr(row, 'data-order-closed', item.closed ? 'true' : 'false');
      attr(row, 'aria-label', 'B' + item.orderNumber + ' 买入价格 ' + numberText(item.buy && item.buy.price) +
        (item.closed ? ' 已结算盈亏 ' : ' 当前盈亏 ') +
        pctText(pnlPercent) + ' ' + currencyText(pnlAmount, true));
      var stripBackground = create('rect', true);
      attr(stripBackground, 'class', 'replay-trade-order-strip-bg');
      attr(stripBackground, 'x', x); attr(stripBackground, 'y', y);
      attr(stripBackground, 'width', rowWidth); attr(stripBackground, 'height', rowHeight); attr(stripBackground, 'rx', 7);
      append(row, stripBackground);
      var buyBackground = create('rect', true);
      attr(buyBackground, 'class', 'replay-trade-order-rail-bg replay-trade-order-rail-buy-bg');
      attr(buyBackground, 'x', x); attr(buyBackground, 'y', y);
      attr(buyBackground, 'width', buttonWidth - 5); attr(buyBackground, 'height', rowHeight);
      attr(buyBackground, 'rx', 6);
      append(row, buyBackground);
      var buyPointer = create('path', true);
      attr(buyPointer, 'class', 'replay-trade-order-rail-pointer replay-trade-order-rail-buy-bg');
      attr(buyPointer, 'd', 'M ' + (x + buttonWidth - 6) + ' ' + (y + 7) +
        ' L ' + (x + buttonWidth) + ' ' + (y + rowHeight / 2) +
        ' L ' + (x + buttonWidth - 6) + ' ' + (y + rowHeight - 7) + ' Z');
      append(row, buyPointer);
      var sellX = x + buttonWidth + pnlWidth;
      var sellBackground = create('rect', true);
      attr(sellBackground, 'class', 'replay-trade-order-rail-bg replay-trade-order-rail-sell-bg');
      attr(sellBackground, 'x', sellX + 5); attr(sellBackground, 'y', y);
      attr(sellBackground, 'width', buttonWidth - 5); attr(sellBackground, 'height', rowHeight);
      attr(sellBackground, 'rx', 6);
      append(row, sellBackground);
      var sellPointer = create('path', true);
      attr(sellPointer, 'class', 'replay-trade-order-rail-pointer replay-trade-order-rail-sell-bg');
      attr(sellPointer, 'd', 'M ' + (sellX + 6) + ' ' + (y + 7) +
        ' L ' + sellX + ' ' + (y + rowHeight / 2) +
        ' L ' + (sellX + 6) + ' ' + (y + rowHeight - 7) + ' Z');
      append(row, sellPointer);
      var buy = create('text', true);
      attr(buy, 'class', 'replay-trade-order-rail-buy');
      attr(buy, 'x', x + buttonWidth / 2 - 2); attr(buy, 'y', y + 11);
      text(buy, 'B' + item.orderNumber);
      if (!item.closed && item.buy) {
        var editBuy = function (event) {
          if (event && typeof event.preventDefault === 'function') event.preventDefault();
          if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
          openExecutionEditor(item.buy, { x: x + rowWidth + 8, y: y });
        };
        [buyBackground, buy].forEach(function (target) {
          if (!target || typeof target.addEventListener !== 'function') return;
          attr(target, 'role', 'button');
          attr(target, 'tabindex', '0');
          attr(target, 'aria-label', '编辑 B' + item.orderNumber);
          target.addEventListener('mousedown', function (event) {
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
          });
          target.addEventListener('click', editBuy);
          target.addEventListener('keydown', function (event) {
            if (event && (event.key === 'Enter' || event.key === ' ')) editBuy(event);
          });
        });
      }
      append(row, buy);
      var buyPrice = create('text', true);
      attr(buyPrice, 'class', 'replay-trade-order-rail-price');
      attr(buyPrice, 'x', x + buttonWidth / 2 - 2); attr(buyPrice, 'y', y + 24);
      text(buyPrice, numberText(item.buy && item.buy.price));
      append(row, buyPrice);
      var pnlPct = create('text', true);
      attr(pnlPct, 'class', 'replay-trade-order-rail-pnl replay-trade-order-rail-pnl-' + pnlClass);
      attr(pnlPct, 'x', x + buttonWidth + pnlWidth / 2); attr(pnlPct, 'y', y + 10);
      text(pnlPct, pctText(pnlPercent));
      append(row, pnlPct);
      var pnlMoney = create('text', true);
      attr(pnlMoney, 'class', 'replay-trade-order-rail-pnl replay-trade-order-rail-pnl-' + pnlClass);
      attr(pnlMoney, 'x', x + buttonWidth + pnlWidth / 2); attr(pnlMoney, 'y', y + 24);
      text(pnlMoney, currencyText(pnlAmount, true));
      append(row, pnlMoney);
      var sell = create('text', true);
      attr(sell, 'class', 'replay-trade-order-rail-sell');
      attr(sell, 'x', sellX + buttonWidth / 2 + 2); attr(sell, 'y', y + 11);
      attr(sell, 'data-sell-order-number', item.orderNumber);
      attr(sell, 'data-order-state', item.closed ? 'closed' : 'open');
      text(sell, 'S' + item.orderNumber);
      if (!item.closed) {
        var activate = function (event) {
          if (event && typeof event.preventDefault === 'function') event.preventDefault();
          if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
          openOrderSellPicker(item.orderNumber, event);
        };
        [sellBackground, sell].forEach(function (target) {
          if (!target || typeof target.addEventListener !== 'function') return;
          attr(target, 'role', 'button');
          attr(target, 'tabindex', '0');
          attr(target, 'aria-label', '卖出 B' + item.orderNumber);
          target.addEventListener('mousedown', function (event) {
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
          });
          target.addEventListener('click', activate);
          target.addEventListener('keydown', function (event) {
            if (event && (event.key === 'Enter' || event.key === ' ')) activate(event);
          });
        });
      }
      append(row, sell);
      append(svg, row);
    });
  }

  function visibleSummaryDetails(details) {
    var rows = [];
    (details || []).forEach(function (detail) {
      if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
        rows.push({ text: String(detail == null ? '' : detail), orderNumber: null, toggle: false });
        return;
      }
      var key = String(detail.orderNumber);
      var expanded = state.summaryExpandedOrders[key];
      if (expanded === undefined) expanded = !detail.closed;
      rows.push({
        text: (expanded ? '▾ ' : '▸ ') + detail.summary,
        orderNumber: detail.orderNumber,
        toggle: true,
        closed: !!detail.closed,
      });
      if (expanded) {
        (detail.lines || []).forEach(function (line) {
          rows.push({ text: '   ' + line, orderNumber: detail.orderNumber, toggle: false });
        });
      }
    });
    return rows;
  }

  function toggleSummaryOrder(orderNumber, closed) {
    var key = String(orderNumber);
    var current = state.summaryExpandedOrders[key];
    if (current === undefined) current = !closed;
    state.summaryExpandedOrders[key] = !current;
    if (closed) state.ghostOrders[key] = !state.ghostOrders[key];
    redraw();
  }

  function clearSummaryDrag() {
    state.summaryDrag.unbinds.forEach(function (unbind) { try { unbind(); } catch (e) {} });
    state.summaryDrag.unbinds = [];
    state.summaryDrag.active = false;
    state.summaryDrag.startX = null;
    state.summaryDrag.startY = null;
    state.summaryDrag.originX = null;
    state.summaryDrag.originY = null;
    state.summaryDrag.moved = false;
  }

  function clampNumber(value, min, max) {
    return Math.min(Math.max(min, Number(value)), Math.max(min, max));
  }

  function updateSummaryDrag(event) {
    if (!state.summaryDrag.active) return;
    var coordinates = eventCoordinates(event || {}, state.mainDom);
    if (coordinates.x == null || coordinates.y == null) return;
    state.summaryDrag.moved = true;
    state.summaryPosition.x = Number(state.summaryDrag.originX) +
      (Number(coordinates.x) - Number(state.summaryDrag.startX));
    state.summaryPosition.y = Number(state.summaryDrag.originY) +
      (Number(coordinates.y) - Number(state.summaryDrag.startY));
    redraw();
  }

  function finishSummaryDrag(event) {
    if (!state.summaryDrag.active) return;
    updateSummaryDrag(event);
    var moved = state.summaryDrag.moved;
    clearSummaryDrag();
    if (!moved) return;
    state.summaryDrag.suppressClick = true;
    var suppressTimer = null;
    var clearSuppress = function () {
      state.summaryDrag.suppressClick = false;
      if (suppressTimer != null && typeof global.clearTimeout === 'function') {
        try { global.clearTimeout(suppressTimer); } catch (e) {}
      }
      suppressTimer = null;
      try { global.removeEventListener('click', clearSuppress); } catch (e) {}
    };
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('click', clearSuppress);
    }
    if (global && typeof global.setTimeout === 'function') {
      suppressTimer = global.setTimeout(clearSuppress, 300);
    }
  }

  function beginSummaryDrag(event) {
    if (state.summaryDrag.active) return;
    if (event && event.button != null && Number(event.button) !== 0) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    var coordinates = eventCoordinates(event || {}, state.mainDom);
    if (coordinates.x == null || coordinates.y == null) return;
    state.summaryDrag.suppressClick = false;
    clearSummaryDrag();
    var geometry = state.summaryGeometry || {};
    state.summaryDrag.active = true;
    state.summaryDrag.startX = Number(coordinates.x);
    state.summaryDrag.startY = Number(coordinates.y);
    state.summaryDrag.originX = finite(geometry.x);
    state.summaryDrag.originY = finite(geometry.y);
    if (state.summaryDrag.originX == null) {
      var dom = state.mainDom;
      var width = Number(dom && dom.clientWidth) || 1;
      state.summaryDrag.originX = Math.max(12, width - (Number(geometry.width) || 0) - 12);
    }
    if (state.summaryDrag.originY == null) {
      var mainDom = state.mainDom;
      var height = Number(mainDom && mainDom.clientHeight) || 1;
      state.summaryDrag.originY = Math.max(12, height - (Number(geometry.height) || 0) - 12);
    }
    var move = function (moveEvent) { updateSummaryDrag(moveEvent); };
    var up = function (upEvent) { finishSummaryDrag(upEvent); };
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('mousemove', move);
      global.addEventListener('mouseup', up);
      state.summaryDrag.unbinds.push(function () { global.removeEventListener('mousemove', move); });
      state.summaryDrag.unbinds.push(function () { global.removeEventListener('mouseup', up); });
    }
  }

  function handleSummaryClick(event) {
    state.summaryDrag.suppressClick = false;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  }

  function bindSummaryDrag(group) {
    if (!group || typeof group.addEventListener !== 'function') return;
    group.addEventListener('mousedown', beginSummaryDrag);
    group.addEventListener('click', handleSummaryClick);
  }

  function drawSummary(width, height, rows, normalized) {
    var svg = state.summarySvg;
    if (!svg) return;
    clear(svg);
    var perf = performance(rows, normalized);
    if (!perf) {
      state.summaryGeometry = null;
      return;
    }
    var timeline = Array.isArray(perf.timeline) ? perf.timeline : [];
    var totalColumnWidth = 145;
    var desiredOrderWidth = 158;
    var minimumBoxWidth = Math.min(360, Math.max(0, width - 24));
    var boxWidth = Math.max(0, Math.min(width - 24, Math.max(minimumBoxWidth,
      totalColumnWidth + Math.max(0, timeline.length) * desiredOrderWidth)));
    var orderWidth = timeline.length
      ? Math.max(104, (boxWidth - totalColumnWidth) / timeline.length) : 0;
    if (timeline.length && orderWidth * timeline.length + totalColumnWidth > boxWidth) {
      totalColumnWidth = Math.max(112, boxWidth - orderWidth * timeline.length);
    }
    var boxHeight = 84;
    var defaultX = Math.max(12, width - boxWidth - 12);
    var defaultY = Math.max(12, height - boxHeight - 12);
    var positioned = state.summaryPosition && finite(state.summaryPosition.x) != null &&
      finite(state.summaryPosition.y) != null;
    var boxX = positioned
      ? clampNumber(state.summaryPosition.x, 12, width - boxWidth - 12)
      : defaultX;
    var boxY = positioned
      ? clampNumber(state.summaryPosition.y, 12, height - boxHeight - 12)
      : defaultY;
    if (positioned) {
      state.summaryPosition.x = boxX;
      state.summaryPosition.y = boxY;
    }
    state.summaryGeometry = { x: boxX, y: boxY, width: boxWidth, height: boxHeight };
    var group = create('g', true);
    if (!group) return;
    attr(group, 'class', 'replay-trade-summary-group');
    attr(group, 'pointer-events', 'all');
    append(svg, group);
    var box = create('rect', true);
    attr(box, 'class', 'replay-trade-summary-box');
    attr(box, 'x', boxX); attr(box, 'y', boxY); attr(box, 'width', boxWidth); attr(box, 'height', boxHeight); attr(box, 'rx', 4);
    append(group, box);
    timeline.forEach(function (item, index) {
      var columnX = boxX + index * orderWidth;
      var columnBackground = create('rect', true);
      attr(columnBackground, 'class', Number(item.totalAmount) >= 0
        ? 'replay-trade-summary-column-positive' : 'replay-trade-summary-column-negative');
      attr(columnBackground, 'x', columnX + 1); attr(columnBackground, 'y', boxY + 1);
      attr(columnBackground, 'width', Math.max(0, orderWidth - 2)); attr(columnBackground, 'height', boxHeight - 2);
      append(group, columnBackground);
      if (index > 0) {
        var separator = create('line', true);
        attr(separator, 'class', 'replay-trade-summary-separator');
        attr(separator, 'x1', columnX); attr(separator, 'x2', columnX);
        attr(separator, 'y1', boxY + 8); attr(separator, 'y2', boxY + boxHeight - 8);
        append(group, separator);
      }
      var totalPct = item.invested > 0 ? item.totalAmount / item.invested * 100 : null;
      var valueClass = Number(item.totalAmount) >= 0 ? ' replay-trade-positive' : ' replay-trade-negative';
      var heading = item.closed
        ? 'B' + item.orderNumber + ' → S' + item.orderNumber
        : 'B' + item.orderNumber + ' 持仓';
      var headingNode = summaryText(group, heading, columnX + 9, boxY + 18,
        'replay-trade-summary-title replay-trade-summary-order' + valueClass);
      attr(headingNode, 'data-order-number', item.orderNumber);
      var status = item.closed ? '实际' + item.exitReason : '当前盈亏';
      summaryText(group, status + ' ' + pctText(totalPct), columnX + 9, boxY + 36,
        'replay-trade-summary-line' + valueClass);
      summaryText(group, currencyText(item.totalAmount, true) + ' · 投入 ' + currencyText(item.invested),
        columnX + 9, boxY + 53, 'replay-trade-summary-detail' + valueClass);
      var finalLine = item.closed && item.sell
        ? '卖 ' + numberText(item.sell.price) + ' · ' + dateText(item.sell.timestamp)
        : '买 ' + numberText(item.buy.price) + ' · ' + dateText(item.buy.timestamp);
      summaryText(group, finalLine, columnX + 9, boxY + 70, 'replay-trade-summary-detail' + valueClass);
      if (!item.closed || !headingNode || typeof headingNode.addEventListener !== 'function') return;
      attr(headingNode, 'role', 'button');
      attr(headingNode, 'tabindex', '0');
      var activate = function (event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
        toggleSummaryOrder(item.orderNumber, true);
      };
      headingNode.addEventListener('mousedown', function (event) {
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
      });
      headingNode.addEventListener('click', activate);
      headingNode.addEventListener('keydown', function (event) {
        if (event && (event.key === 'Enter' || event.key === ' ')) activate(event);
      });
    });
    var totalX = boxX + timeline.length * orderWidth;
    if (timeline.length) {
      var totalSeparator = create('line', true);
      attr(totalSeparator, 'class', 'replay-trade-summary-separator');
      attr(totalSeparator, 'x1', totalX); attr(totalSeparator, 'x2', totalX);
      attr(totalSeparator, 'y1', boxY + 8); attr(totalSeparator, 'y2', boxY + boxHeight - 8);
      append(group, totalSeparator);
    }
    var totalClass = Number(perf.amount) >= 0 ? ' replay-trade-positive' : ' replay-trade-negative';
    summaryText(group, '总计', totalX + 10, boxY + 18, 'replay-trade-summary-title');
    summaryText(group, perf.label, totalX + 10, boxY + 36, 'replay-trade-summary-line' + totalClass);
    var value = perf.pct == null ? '--' : pctText(perf.pct) + ' · ' + currencyText(perf.amount, true);
    summaryText(group, value, totalX + 10, boxY + 54, 'replay-trade-summary-line' + totalClass);
    summaryText(group, '共 ' + timeline.length + ' 笔', totalX + 10, boxY + 71, 'replay-trade-summary-detail');
    bindSummaryDrag(group);
  }

  function redraw() {
    if (!state.active) {
      if (state.overlaySvg) clear(state.overlaySvg);
      if (state.summarySvg) clear(state.summarySvg);
      return;
    }
    var chart = getChart();
    if (!bindChart(chart) || !ensureOverlayDom(chart)) return;
    var rows = rowsForChart();
    if (!rows.length) return;
    var dom = state.mainDom;
    var rect = dom && typeof dom.getBoundingClientRect === 'function' ? dom.getBoundingClientRect() : {};
    var width = Number(dom && dom.clientWidth) || Number(rect.width) || 1;
    var height = Number(dom && dom.clientHeight) || Number(rect.height) || 1;
    attr(state.overlaySvg, 'viewBox', '0 0 ' + width + ' ' + height);
    attr(state.summarySvg, 'viewBox', '0 0 ' + width + ' ' + height);
    clear(state.overlaySvg);
    var normalized = currentTradeState();
    ensureBracketDrafts(normalized);
    var presets = normalized.presets || {};
    var buyPreset = state.localPresets.buy || presets.buy || presets.long || null;
    var sellPreset = state.localPresets.sell || presets.sell || presets.exit || null;
    var takeProfitPreset = state.localPresets.takeProfit || presets.takeProfit || presets.take_profit || null;
    var stopLossPreset = state.localPresets.stopLoss || presets.stopLoss || presets.stop_loss || null;
    var bracketOrders = Array.isArray(normalized.bracketOrders) ? normalized.bracketOrders.filter(function (order) {
      var role = presetRole(order && (order.side || order.role || order.type));
      return role === 'takeProfit' || role === 'stopLoss';
    }) : [];
    var entryReference = normalized.positionSummary && finite(normalized.positionSummary.weightedEntryPrice);
    if (entryReference == null && buyPreset) entryReference = finite(buyPreset.price);
    drawRiskZones(state.overlaySvg, normalized, width, height, rows);
    drawPreset(state.overlaySvg, buyPreset, 'buy', width, height, rows, entryReference);
    drawPreset(state.overlaySvg, sellPreset, 'sell', width, height, rows, entryReference);
    if (bracketOrders.length) {
      bracketOrders.forEach(function (order) {
        drawPreset(state.overlaySvg, order, presetRole(order.side || order.role || order.type), width, height, rows, entryReference);
      });
    } else {
      drawPreset(state.overlaySvg, takeProfitPreset, 'takeProfit', width, height, rows, entryReference);
      drawPreset(state.overlaySvg, stopLossPreset, 'stopLoss', width, height, rows, entryReference);
    }
    drawPresetPreview(state.overlaySvg, state.presetSelection, width, height, rows);
    drawCompletedBracketGhosts(state.overlaySvg, normalized, width, height, rows);
    state.bracketDrafts.forEach(function (draft) {
      drawBracketDraft(state.overlaySvg, draft, width, height, rows, normalized);
    });
    normalized.records.forEach(function (record) {
      var draggingThis = state.executionDrag.active && state.executionDrag.record &&
        state.executionDrag.record.id === record.id;
      var located = draggingThis && finite(state.executionDrag.barIndex) != null
        ? { index: Number(state.executionDrag.barIndex), row: rows[Number(state.executionDrag.barIndex)] }
        : rowForRecord(record, rows);
      var price = draggingThis ? finite(state.executionDrag.price) : finite(record.price);
      if (!located || price == null) return;
      var point = convertPrice(located.index, price, rows, width, height);
      var orderNumbers = record.side === 'sell' && Array.isArray(record.orderNumbers)
        ? record.orderNumbers : [Math.max(1, Math.round(finite(record.orderNumber) || 1))];
      var markerLabel = (record.side === 'buy' ? 'B' : 'S') + orderNumbers.join('/' + (record.side === 'buy' ? 'B' : 'S'));
      var markerMeta = record.side === 'buy'
        ? { rewardRisk: rewardRiskForOrder(normalized, orderNumbers[0], price) }
        : null;
      marker(state.overlaySvg, point, record.side, markerLabel,
        (record.side === 'buy' ? '买入 ' : '卖出 ') + markerLabel + ' · ' + dateText(record.timestamp) + ' · ' + numberText(price), record, width, markerMeta);
    });
    drawOrderRail(state.overlaySvg, normalized, rows);
    drawSummary(width, height, rows, normalized);
  }

  function handleReplayCursor(detail) {
    detail = detailOf(detail);
    state.lastReplayDetail = detail;
    var engine = getEngine();
    if (engine && typeof engine.syncReplay === 'function') {
      try { engine.syncReplay(detail); } catch (error) {}
    }
    var controller = getController();
    var cursor = detail.cursor != null ? Number(detail.cursor) : ((controllerState() || {}).cursor);
    if (cursor != null && cursor !== state.lastCursor) {
      state.lastCursor = cursor;
      autoTriggerLocalPresets();
    }
    syncLifecycle(detail);
    redraw();
  }

  function autoTriggerLocalPresets() {
    if (getEngine()) return;
    var rows = rowsForChart();
    var current = rows.length ? rows[rows.length - 1] : null;
    if (!current) return;
    var records = state.localRecords;
    var paired = pairRecords(records);
    if (!paired.open && state.localPresets.buy && finite(state.localPresets.buy.price) != null &&
        finite(current.low) != null && finite(current.high) != null &&
        current.low <= state.localPresets.buy.price && current.high >= state.localPresets.buy.price) {
      addLocalRecord({ side: 'buy', action: 'buy', index: rows.length - 1, timestamp: current.timestamp,
        price: state.localPresets.buy.price, priceField: 'preset', amount: state.localPresets.buy.amount, status: 'filled' });
      state.localPresets.buy = null;
    }
    paired = pairRecords(records);
    var exitRole = null;
    ['stopLoss', 'sell', 'takeProfit'].some(function (role) {
      var order = state.localPresets[role];
      if (!paired.open || !order || finite(order.price) == null || finite(current.low) == null || finite(current.high) == null) return false;
      if (current.low <= order.price && current.high >= order.price) {
        exitRole = role;
        return true;
      }
      return false;
    });
    if (exitRole) {
      var exitOrder = state.localPresets[exitRole];
      addLocalRecord({ side: 'sell', action: 'sell', index: rows.length - 1, timestamp: current.timestamp,
        price: exitOrder.price, priceField: exitRole, status: 'filled' });
      state.localPresets.sell = null;
      state.localPresets.takeProfit = null;
      state.localPresets.stopLoss = null;
    }
  }

  function syncLifecycle(detail) {
    var active = replayActive(detail);
    state.active = active;
    if (active) {
      bindChart(getChart());
      ensureControls();
      ensureOverlayDom(getChart());
      updateControls();
    } else {
      cancelSelection();
      clearBracketDraftDrag();
      clearExecutionDrag();
      state.bracketDraft = null;
      state.bracketDrafts = [];
      state.bracketDraftedExecutionIds = Object.create(null);
      if (state.bracketConfirmBar) state.bracketConfirmBar.hidden = true;
      closeTransientPanels();
      updateControls();
    }
  }

  function onEvent(name, event) {
    var detail = detailOf(event);
    if (name === 'kline-chart-ready') {
      bindChart(detail && typeof detail.getDataList === 'function' ? detail : getChart());
      ensureControls();
      return;
    }
    if (name === 'replay-trade-state') {
      state.tradeState = detail.state && typeof detail.state === 'object' ? detail.state : detail;
      redraw();
      renderRecordsPanel();
      return;
    }
    if (name === 'bar-replay-cursor') {
      handleReplayCursor(detail);
      return;
    }
    if (name === 'bar-replay-exit') {
      state.lastReplayDetail = detail;
      state.active = false;
      syncLifecycle(detail);
      redraw();
      return;
    }
    if (name === 'bar-replay-start' || name === 'bar-replay-state') {
      if (name === 'bar-replay-start') {
        state.ghostOrders = Object.create(null);
        state.summaryExpandedOrders = Object.create(null);
      }
      state.lastReplayDetail = detail;
      syncLifecycle(detail);
      redraw();
    }
    if (name === 'kline-loaded') redraw();
  }

  function poll() {
    bindEvents();
    var detail = state.lastReplayDetail || {};
    var current = controllerState();
    if (current) {
      if (current.status !== state.lastControllerStatus || current.cursor !== state.lastCursor) {
        state.lastControllerStatus = current.status || '';
        state.lastReplayDetail = Object.assign({}, detail, current);
        handleReplayCursor(state.lastReplayDetail);
      }
    }
    syncLifecycle(detail);
    if (state.active) {
      ensureControls();
      ensureOverlayDom(getChart());
      redraw();
    }
  }

  function init() {
    if (state.initialized) return api;
    state.initialized = true;
    injectStyles();
    bindEvents();
    bindChart(getChart());
    ensureControls();
    poll();
    if (typeof global.setInterval === 'function') state.pollTimer = global.setInterval(poll, POLL_MS);
    return api;
  }

  function destroy() {
    if (state.pollTimer != null && typeof global.clearInterval === 'function') global.clearInterval(state.pollTimer);
    state.pollTimer = null;
    state.eventUnbinds.forEach(function (unbind) { if (typeof unbind === 'function') { try { unbind(); } catch (e) {} } });
    state.eventUnbinds = [];
    state.eventUnbinds._domBound = false;
    unbindChart();
    unbindSelection();
    clearExecutionDrag();
    clearSummaryDrag();
    closeTransientPanels();
    if (state.overlaySvg && state.overlaySvg.parentNode) state.overlaySvg.parentNode.removeChild(state.overlaySvg);
    if (state.summarySvg && state.summarySvg.parentNode) state.summarySvg.parentNode.removeChild(state.summarySvg);
    if (state.bracketConfirmBar && state.bracketConfirmBar.parentNode) state.bracketConfirmBar.parentNode.removeChild(state.bracketConfirmBar);
    state.initialized = false;
    state.active = false;
    state.mode = null;
    state.chart = null;
    state.mainDom = null;
    state.overlaySvg = null;
    state.summarySvg = null;
    state.bracketConfirmBar = null;
    state.bracketDraft = null;
    state.bracketDrafts = [];
    state.bracketDraftedExecutionIds = Object.create(null);
    return true;
  }

  var api = {
    init: init,
    destroy: destroy,
    redraw: redraw,
    enterSelection: enterSelection,
    cancelSelection: cancelSelection,
    openPresetPanel: openPresetPanel,
    openRecordsPanel: openRecordsPanel,
    selectPrice: executeTrade,
    getPerformanceSummary: function () {
      return performance(rowsForChart(), currentTradeState());
    },
    getState: function () {
      return {
        active: state.active,
        mode: state.mode,
        presetSelection: Object.assign({}, state.presetSelection),
        selectedBar: state.selectedBar,
        records: readRecords(),
        presets: state.localPresets,
        bracketDraft: state.bracketDraft ? Object.assign({}, state.bracketDraft) : null,
        bracketDrafts: state.bracketDrafts.map(function (draft) { return Object.assign({}, draft); }),
        ghostOrders: Object.assign({}, state.ghostOrders),
        summaryExpandedOrders: Object.assign({}, state.summaryExpandedOrders),
        summaryPosition: Object.assign({}, state.summaryPosition),
        summaryGeometry: state.summaryGeometry ? Object.assign({}, state.summaryGeometry) : null,
      };
    },
  };

  global.ReplayTradeUI = api;
  init();
}(typeof window !== 'undefined' ? window : globalThis));
