(function (global) {
  'use strict';

  var STATUS_IDLE = 'idle';
  var STATUS_SELECTING = 'selecting';
  var STATUS_PAUSED = 'paused';
  var STATUS_PLAYING = 'playing';
  var DEFAULT_INTERVAL_MS = 800;
  var SPEEDS = [0.5, 1, 2, 4];

  var state = {
    status: STATUS_IDLE,
    chart: null,
    history: [],
    cursor: -1,
    speed: 1,
    intervalMs: DEFAULT_INTERVAL_MS,
    timer: null,
    selectionDom: null,
    selectionHandler: null,
    controls: null,
    eventsBound: false,
    refreshPending: false,
    dispatchingRefresh: false,
    period: null,
  };

  function getDocument() {
    return global.document || null;
  }

  function getChart(candidate) {
    if (candidate && typeof candidate.getDataList === 'function') return candidate;
    if (state.chart && typeof state.chart.getDataList === 'function') return state.chart;
    if (global.__kline_chart && typeof global.__kline_chart.getDataList === 'function') {
      return global.__kline_chart;
    }
    return null;
  }

  function copyBars(list) {
    return Array.prototype.map.call(list || [], function (bar) {
      if (!bar || typeof bar !== 'object') return bar;
      return Object.assign({}, bar);
    });
  }

  function finiteNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function normalizePeriod(value) {
    if (value && typeof value === 'object') {
      var text = value.text || value.label || value.period;
      if (text !== undefined && text !== null) {
        var fromText = normalizePeriod(text);
        if (fromText) return fromText;
      }

      var timespan = String(value.timespan || value.span || value.type || '').toLowerCase();
      var multiplier = finiteNumber(value.multiplier !== undefined ? value.multiplier : value.value);
      if (timespan === 'minute' || timespan === 'min' || timespan === 'm') {
        if (multiplier === 1) return '1m';
        if (multiplier === 5) return '5m';
        if (multiplier === 15) return '15m';
        return null;
      }
      if (timespan === 'hour' || timespan === 'hr' || timespan === 'h') {
        if (multiplier === 1) return '60m';
        if (multiplier === 2) return '120m';
        if (multiplier === 4) return '240m';
        return null;
      }
      if (timespan === 'day' || timespan === 'daily' || timespan === 'd') return 'daily';
      if (timespan === 'week' || timespan === 'weekly' || timespan === 'w') return 'weekly';
      if (timespan === 'month' || timespan === 'monthly' || timespan === 'mo') {
        if (multiplier === null || multiplier === 1) return 'monthly';
        if (multiplier === 3) return 'quarterly';
        if (multiplier === 12) return 'yearly';
        return null;
      }
      if (timespan === 'year' || timespan === 'yearly' || timespan === 'y') return 'yearly';
      return null;
    }

    if (typeof value !== 'string') return null;
    var raw = value.trim();
    if (!raw) return null;
    var period = raw.toLowerCase();
    var aliases = {
      '1m': '1m',
      '5m': '5m',
      '15m': '15m',
      '1min': '1m',
      '5min': '5m',
      '15min': '15m',
      '60m': '60m',
      '120m': '120m',
      '240m': '240m',
      '1h': '60m',
      '2h': '120m',
      '4h': '240m',
      'daily': 'daily',
      'day': 'daily',
      '1d': 'daily',
      'd': 'daily',
      'weekly': 'weekly',
      'week': 'weekly',
      'w': 'weekly',
      'monthly': 'monthly',
      'month': 'monthly',
      'mo': 'monthly',
      'quarterly': 'quarterly',
      'quarter': 'quarterly',
      'q': 'quarterly',
      'yearly': 'yearly',
      'year': 'yearly',
      'y': 'yearly',
      '日': 'daily',
      '周': 'weekly',
      '月': 'monthly',
      '季': 'quarterly',
      '年': 'yearly',
    };
    return aliases[period] || null;
  }

  function getCurrentPeriod() {
    var pro = global && global.pro;
    if (pro && typeof pro.getPeriod === 'function') {
      try {
        var proPeriod = normalizePeriod(pro.getPeriod());
        if (proPeriod) return proPeriod;
      } catch (e) {}
    }
    var context = global && global.__board_ctx;
    return normalizePeriod(context && context.period);
  }

  function copyValue(value, seen) {
    if (value === null || typeof value !== 'object') return value;
    if (!seen) seen = [];
    for (var i = 0; i < seen.length; i += 1) {
      if (seen[i][0] === value) return seen[i][1];
    }
    var result = Array.isArray(value) ? [] : {};
    seen.push([value, result]);
    Object.keys(value).forEach(function (key) {
      result[key] = copyValue(value[key], seen);
    });
    return result;
  }

  function ensureReplayEvents() {
    if (global.BarReplayEvents &&
        typeof global.BarReplayEvents.emit === 'function' &&
        typeof global.BarReplayEvents.on === 'function' &&
        typeof global.BarReplayEvents.off === 'function') {
      return global.BarReplayEvents;
    }

    var handlers = Object.create(null);
    var api = {
      START: 'bar-replay-start',
      CURSOR: 'bar-replay-cursor',
      STATUS: 'bar-replay-status',
      EXIT: 'bar-replay-exit',
      on: function (name, handler) {
        if (typeof name !== 'string' || typeof handler !== 'function') return handler;
        if (!handlers[name]) handlers[name] = [];
        if (handlers[name].indexOf(handler) < 0) handlers[name].push(handler);
        return function () { api.off(name, handler); };
      },
      off: function (name, handler) {
        if (!handlers[name]) return;
        if (typeof handler !== 'function') {
          handlers[name] = [];
          return;
        }
        handlers[name] = handlers[name].filter(function (item) { return item !== handler; });
      },
      emit: function (name, detail) {
        var listeners = (handlers[name] || []).slice();
        listeners.forEach(function (handler) {
          try { handler(copyValue(detail)); } catch (error) { /* isolate observers */ }
        });
        if (global && typeof global.dispatchEvent === 'function') {
          try {
            var event = typeof global.CustomEvent === 'function'
              ? new global.CustomEvent(name, { detail: copyValue(detail) })
              : { type: name, detail: copyValue(detail) };
            global.dispatchEvent(event);
          } catch (error) { /* DOM observers are optional */ }
        }
        return copyValue(detail);
      },
    };
    global.BarReplayEvents = api;
    return api;
  }

  var replayEvents = ensureReplayEvents();
  var replayEventNames = {
    START: 'bar-replay-start',
    CURSOR: 'bar-replay-cursor',
    STATUS: 'bar-replay-status',
    EXIT: 'bar-replay-exit',
  };

  function eventName(key) {
    return replayEvents && replayEvents[key] || replayEventNames[key];
  }

  function replayEventDetail(extra) {
    var cursor = state.cursor;
    var history = copyValue(state.history);
    var visibleBars = cursor < 0 ? [] : copyValue(state.history.slice(0, cursor + 1));
    var detail = {
      cursor: cursor,
      bar: cursor >= 0 && cursor < state.history.length
        ? copyValue(state.history[cursor]) : null,
      visibleBars: visibleBars,
      history: history,
      status: state.status,
      period: state.period,
      startPeriod: state.period,
    };
    Object.keys(extra || {}).forEach(function (key) {
      detail[key] = copyValue(extra[key]);
    });
    return detail;
  }

  function emitReplayEvent(name, extra) {
    if (!replayEvents || typeof replayEvents.emit !== 'function') return;
    try { replayEvents.emit(name, replayEventDetail(extra)); } catch (e) {}
  }

  function clearTimer() {
    if (state.timer === null) return;
    try {
      if (typeof global.clearInterval === 'function') global.clearInterval(state.timer);
      if (typeof global.clearTimeout === 'function') global.clearTimeout(state.timer);
    } catch (e) {}
    state.timer = null;
  }

  function applyVisibleData(list) {
    var chart = state.chart;
    if (!chart || typeof chart.applyNewData !== 'function') return false;
    // applyNewData is also the chart engine's indicator recalculation boundary.
    chart.applyNewData(copyBars(list));
    return true;
  }

  function setStatus(status) {
    state.status = status;
    updateControls();
  }

  function findElement(id) {
    var doc = getDocument();
    if (!doc || typeof doc.getElementById !== 'function') return null;
    try { return doc.getElementById(id); } catch (e) { return null; }
  }

  function append(parent, child) {
    if (parent && child && typeof parent.appendChild === 'function') parent.appendChild(child);
  }

  function createButton(doc, id, text, title) {
    var element = doc.createElement('button');
    element.id = id;
    element.type = 'button';
    element.textContent = text;
    if (title && typeof element.setAttribute === 'function') {
      element.title = title;
      element.setAttribute('aria-label', title);
    }
    return element;
  }

  function bindControl(element, eventName, handler) {
    if (!element || element._barReplayBound) return;
    if (typeof element.addEventListener === 'function') element.addEventListener(eventName, handler);
    element._barReplayBound = true;
  }

  function mountControlsInPriceBar(controls, candidate) {
    if (!controls) return false;
    var priceBar = candidate && candidate.id === 'kline-tooltip'
      ? candidate : findElement('kline-tooltip');
    if (!priceBar || typeof priceBar.appendChild !== 'function') return false;
    priceBar.appendChild(controls);
    if (!controls.dataset) controls.dataset = {};
    controls.dataset.layout = 'price-bar';
    return true;
  }

  function ensureControls() {
    var doc = getDocument();
    if (!doc || typeof doc.createElement !== 'function') return null;

    var replayButton = findElement('bar-replay-btn');
    var controls = findElement('bar-replay-controls');
    var periodBar = null;
    try {
      periodBar = typeof doc.querySelector === 'function'
        ? doc.querySelector('.klinecharts-pro-period-bar') : null;
    } catch (e) {}
    var chartContainer = findElement('pro-container');

    if (!replayButton && periodBar) {
      replayButton = createButton(doc, 'bar-replay-btn', '回放', '选择回放起点');
      replayButton.className = 'bar-replay-button';
      // 放在第一个工具按钮前，窄屏时也不会被全屏按钮之后的溢出裁掉。
      var firstTool = null;
      try { firstTool = periodBar.querySelector('.item.tools'); } catch (e) {}
      if (firstTool && typeof periodBar.insertBefore === 'function') {
        periodBar.insertBefore(replayButton, firstTool);
      } else {
        append(periodBar, replayButton);
      }
    }
    if (!controls && periodBar) {
      controls = doc.createElement('span');
      controls.id = 'bar-replay-controls';
      controls.className = 'bar-replay-controls';

      var play = createButton(doc, 'bar-replay-play', '播放', '播放或暂停回放');
      var stepButton = createButton(doc, 'bar-replay-step', '单步', '推进一根 K 线');
      var speed = doc.createElement('select');
      speed.id = 'bar-replay-speed';
      speed.title = '回放速度';
      speed.setAttribute('aria-label', '回放速度');
      SPEEDS.forEach(function (value) {
        var option = doc.createElement('option');
        option.value = String(value);
        option.textContent = String(value) + 'x';
        append(speed, option);
      });
      speed.value = String(state.speed);
      var status = doc.createElement('span');
      status.id = 'bar-replay-status';
      var exitButton = createButton(doc, 'bar-replay-exit', '退出', '退出 K 线回放');

      append(controls, play);
      append(controls, stepButton);
      append(controls, speed);
      append(controls, status);
      append(controls, exitButton);
      if (!mountControlsInPriceBar(controls)) append(chartContainer || periodBar, controls);
    }

    if (!replayButton || !controls) return null;
    state.controls = {
      button: replayButton,
      wrap: controls,
      play: findElement('bar-replay-play'),
      step: findElement('bar-replay-step'),
      speed: findElement('bar-replay-speed'),
      status: findElement('bar-replay-status'),
      exit: findElement('bar-replay-exit'),
    };

    bindControl(state.controls.button, 'click', function () { startSelection(); });
    bindControl(state.controls.play, 'click', function () { togglePlay(); });
    bindControl(state.controls.step, 'click', function () { step(); });
    bindControl(state.controls.exit, 'click', function () { exit(); });
    bindControl(state.controls.speed, 'change', function () {
      setSpeed(this.value);
    });
    updateControls();
    return state.controls;
  }

  function updateControls() {
    var controls = state.controls;
    if (!controls) return;
    try {
      controls.wrap.style.display = state.status === STATUS_IDLE ? 'none' : 'inline-flex';
      controls.button.textContent = state.status === STATUS_SELECTING ? '选择起点' : '回放';
      controls.play.textContent = state.status === STATUS_PLAYING ? '暂停' : '播放';
      controls.play.disabled = state.status !== STATUS_PAUSED && state.status !== STATUS_PLAYING;
      controls.step.disabled = state.status !== STATUS_PAUSED;
      controls.speed.value = String(state.speed);
      controls.status.textContent = state.status === STATUS_SELECTING
        ? '点击历史 K 线选择起点'
        : state.status === STATUS_IDLE
          ? ''
          : '回放 ' + (state.cursor + 1) + '/' + state.history.length;
      if (typeof controls.wrap.setAttribute === 'function') {
        controls.wrap.setAttribute('data-state', state.status);
      }
    } catch (e) {}
  }

  function getChartDom(chart) {
    if (!chart || typeof chart.getDom !== 'function') return null;
    var dom = null;
    try {
      var position = global.klinecharts && global.klinecharts.DomPosition
        ? global.klinecharts.DomPosition.Main : undefined;
      if (position !== undefined) dom = chart.getDom('candle_pane', position);
      if (!dom) dom = chart.getDom('candle_pane');
      if (!dom) dom = chart.getDom();
    } catch (e) {}
    return dom;
  }

  function detachSelection() {
    if (state.selectionDom && state.selectionHandler &&
        typeof state.selectionDom.removeEventListener === 'function') {
      try { state.selectionDom.removeEventListener('click', state.selectionHandler); } catch (e) {}
    }
    if (state.selectionDom && state.selectionDom.classList &&
        typeof state.selectionDom.classList.remove === 'function') {
      try { state.selectionDom.classList.remove('bar-replay-selecting'); } catch (e) {}
    }
    state.selectionDom = null;
    state.selectionHandler = null;
  }

  function validIndex(value) {
    var index = Number(value);
    if (!isFinite(index)) return -1;
    index = Math.round(index);
    return index >= 0 && index < state.history.length ? index : -1;
  }

  function eventIndex(event, chart, dom) {
    var detail = event && event.detail;
    var direct = event && (event.dataIndex !== undefined ? event.dataIndex : event.index);
    if (direct === undefined && detail && typeof detail === 'object') {
      direct = detail.dataIndex !== undefined ? detail.dataIndex : detail.index;
    }
    var index = validIndex(direct);
    if (index >= 0) return index;

    var x = event && (typeof event.offsetX === 'number' ? event.offsetX : null);
    if (x === null && event && typeof event.clientX === 'number' && dom &&
        typeof dom.getBoundingClientRect === 'function') {
      var rect = dom.getBoundingClientRect();
      x = event.clientX - rect.left;
    }
    var y = event && (typeof event.offsetY === 'number' ? event.offsetY : null);
    if (y === null && event && typeof event.clientY === 'number' && dom &&
        typeof dom.getBoundingClientRect === 'function') {
      var yRect = dom.getBoundingClientRect();
      y = event.clientY - yRect.top;
    }

    if (chart && typeof chart.convertFromPixel === 'function' && x !== null) {
      try {
        var converted = chart.convertFromPixel([{ x: x, y: y === null ? 0 : y }], {
          paneId: 'candle_pane',
        });
        var point = Array.isArray(converted) ? converted[0] : converted;
        index = validIndex(point && point.dataIndex);
        if (index >= 0) return index;
      } catch (e) {}
    }

    var target = event && event.target;
    if (target && target.dataset) {
      index = validIndex(target.dataset.dataIndex || target.dataset.index);
      if (index >= 0) return index;
    }
    if (x !== null && dom && dom.clientWidth) {
      return validIndex(Math.floor((x / dom.clientWidth) * state.history.length));
    }
    return -1;
  }

  function attachSelection(chart) {
    detachSelection();
    var dom = getChartDom(chart);
    if (!dom || typeof dom.addEventListener !== 'function') return false;
    state.selectionDom = dom;
    state.selectionHandler = function (event) {
      if (state.status !== STATUS_SELECTING) return;
      var index = eventIndex(event || {}, chart, dom);
      if (index >= 0) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        selectIndex(index);
      }
    };
    try { dom.addEventListener('click', state.selectionHandler); } catch (e) { return false; }
    if (dom.classList && typeof dom.classList.add === 'function') {
      try { dom.classList.add('bar-replay-selecting'); } catch (e) {}
    }
    return true;
  }

  function startSelection(options) {
    var chart = options && typeof options.getDataList === 'function'
      ? options : options && options.chart;
    chart = getChart(chart);
    if (!chart || state.status !== STATUS_IDLE) return false;

    var list;
    try { list = chart.getDataList(); } catch (e) { return false; }
    if (!Array.isArray(list) || !list.length) return false;

    state.chart = chart;
    state.history = copyBars(list);
    state.cursor = -1;
    state.refreshPending = false;
    state.period = getCurrentPeriod();
    clearTimer();
    setStatus(STATUS_SELECTING);
    attachSelection(chart);
    updateControls();
    return true;
  }

  function selectIndex(index) {
    if (state.status !== STATUS_SELECTING) return false;
    index = validIndex(index);
    if (index < 0) return false;
    var prefix = state.history.slice(0, index + 1);
    if (!applyVisibleData(prefix)) return false;
    state.cursor = index;
    detachSelection();
    setStatus(STATUS_PAUSED);
    emitReplayEvent(eventName('START'), { reason: 'start-selected' });
    emitReplayEvent(eventName('CURSOR'), { reason: 'start-selected' });
    return true;
  }

  function step() {
    if (state.status !== STATUS_PAUSED && state.status !== STATUS_PLAYING) return false;
    if (state.cursor >= state.history.length - 1) {
      clearTimer();
      setStatus(STATUS_PAUSED);
      return false;
    }

    var nextIndex = state.cursor + 1;
    var nextBar = copyBars([state.history[nextIndex]])[0];
    try {
      if (state.chart && typeof state.chart.updateData === 'function') {
        // Only this single next bar crosses the replay boundary.
        state.chart.updateData(nextBar);
      } else {
        applyVisibleData(state.history.slice(0, nextIndex + 1));
      }
    } catch (e) {
      applyVisibleData(state.history.slice(0, nextIndex + 1));
    }
    state.cursor = nextIndex;
    if (state.cursor >= state.history.length - 1) clearTimer();
    setStatus(STATUS_PLAYING === state.status && state.cursor < state.history.length - 1
      ? STATUS_PLAYING : STATUS_PAUSED);
    emitReplayEvent(eventName('CURSOR'), { reason: 'step' });
    return true;
  }

  function startTimer() {
    clearTimer();
    var delay = Math.max(20, state.intervalMs / state.speed);
    var tick = function () { step(); };
    if (typeof global.setInterval === 'function') {
      state.timer = global.setInterval(tick, delay);
    } else if (typeof global.setTimeout === 'function') {
      state.timer = global.setTimeout(tick, delay);
    }
  }

  function togglePlay() {
    if (state.status === STATUS_PLAYING) {
      clearTimer();
      setStatus(STATUS_PAUSED);
      emitReplayEvent(eventName('STATUS'), { reason: 'pause' });
      return false;
    }
    if (state.status !== STATUS_PAUSED || state.cursor >= state.history.length - 1) return false;
    setStatus(STATUS_PLAYING);
    emitReplayEvent(eventName('STATUS'), { reason: 'play' });
    startTimer();
    return true;
  }

  function setSpeed(value) {
    var speed = Number(value);
    if (SPEEDS.indexOf(speed) < 0) speed = 1;
    state.speed = speed;
    if (state.status === STATUS_PLAYING) startTimer();
    updateControls();
    return state.speed;
  }

  function makeEvent(name, detail) {
    try {
      if (typeof global.CustomEvent === 'function') return new global.CustomEvent(name, { detail: detail });
      if (typeof global.Event === 'function') return new global.Event(name);
    } catch (e) {}
    return { type: name, detail: detail };
  }

  function exit(options) {
    options = options || {};
    var hadReplay = state.history.length > 0;
    var shouldRefresh = state.refreshPending;
    var shouldRestore = options.restore !== false;
    var shouldDispatchRefresh = shouldRefresh && options.silent !== true && options.refresh !== false;
    clearTimer();
    detachSelection();
    emitReplayEvent(eventName('EXIT'), {
      reason: options.reason || 'user',
      restore: shouldRestore,
    });
    if (hadReplay && state.chart && shouldRestore) {
      try { applyVisibleData(state.history); } catch (e) {}
    }
    state.history = [];
    state.cursor = -1;
    state.refreshPending = false;
    state.period = null;
    setStatus(STATUS_IDLE);
    if (shouldDispatchRefresh && global && typeof global.dispatchEvent === 'function') {
      state.dispatchingRefresh = true;
      try {
        global.dispatchEvent(makeEvent('refresh-current-symbol', {
          source: 'bar-replay',
          reason: 'replay-exit',
        }));
      } catch (e) {}
      state.dispatchingRefresh = false;
    }
    return true;
  }

  function markRefreshPending(value) {
    state.refreshPending = value === undefined ? true : !!value;
    updateControls();
    return state.refreshPending;
  }

  function getState() {
    return {
      status: state.status,
      cursor: state.cursor,
      selectedIndex: state.cursor,
      total: state.history.length,
      visibleCount: state.cursor < 0 ? 0 : state.cursor + 1,
      speed: state.speed,
      refreshPending: state.refreshPending,
      timerActive: state.timer !== null,
      period: state.period,
      startPeriod: state.period,
    };
  }

  function onKeyDown(event) {
    if (!event || !event.shiftKey || state.status === STATUS_IDLE) return;
    var handled = false;
    if (event.key === 'ArrowDown') handled = true && togglePlay() !== undefined;
    if (event.key === 'ArrowRight') handled = true && step() !== undefined;
    if (handled && typeof event.preventDefault === 'function') event.preventDefault();
  }

  function exitWhenPeriodChanges(nextPeriod) {
    var normalized = normalizePeriod(nextPeriod);
    if (state.status === STATUS_IDLE || !state.period || !normalized || normalized === state.period) return false;
    return exit({ restore: false, silent: true, reason: 'period-change' });
  }

  function onPeriodButtonClick(event) {
    if (state.status === STATUS_IDLE || !event || !event.target) return;
    var target = event.target;
    var periodButton = typeof target.closest === 'function' ? target.closest('.item.period') : null;
    if (periodButton) exitWhenPeriodChanges(periodButton.textContent);
  }

  function bindEvents() {
    if (state.eventsBound) return;
    state.eventsBound = true;
    if (global && typeof global.addEventListener === 'function') {
      global.addEventListener('kline-chart-ready', function (event) {
        onChartReady(event && event.detail ? event.detail : global.__kline_chart);
      });
      global.addEventListener('kline-tooltip-ready', function (event) {
        var controls = state.controls && state.controls.wrap
          ? state.controls.wrap : findElement('bar-replay-controls');
        mountControlsInPriceBar(controls, event && event.detail);
      });
      global.addEventListener('keydown', onKeyDown);
      global.addEventListener('period-change', function (event) {
        exitWhenPeriodChanges(event && event.detail ? event.detail.period || event.detail.periodObject : null);
      });
      global.addEventListener('kline-loaded', function () {
        exitWhenPeriodChanges(getCurrentPeriod());
      });
      global.addEventListener('refresh-current-symbol', function () {
        if (!state.dispatchingRefresh && state.status !== STATUS_IDLE) markRefreshPending(true);
      });
    }
    var doc = getDocument();
    if (doc && typeof doc.addEventListener === 'function') {
      doc.addEventListener('click', onPeriodButtonClick, true);
    }
  }

  function onChartReady(chart) {
    var readyChart = getChart(chart);
    if (readyChart) state.chart = readyChart;
    ensureControls();
    if (state.status === STATUS_SELECTING && state.chart) attachSelection(state.chart);
    return state.chart;
  }

  function init(options) {
    if (options && typeof options.getDataList === 'function') options = { chart: options };
    options = options || {};
    if (options.chart) state.chart = getChart(options.chart) || options.chart;
    if (options.intervalMs != null && isFinite(Number(options.intervalMs))) {
      state.intervalMs = Math.max(20, Number(options.intervalMs));
    }
    if (options.speed != null) setSpeed(options.speed);
    bindEvents();
    if (global.__kline_chart) onChartReady(global.__kline_chart);
    else ensureControls();
    return controller;
  }

  var controller = {
    init: init,
    onChartReady: onChartReady,
    startSelection: startSelection,
    selectIndex: selectIndex,
    togglePlay: togglePlay,
    step: step,
    exit: exit,
    isActive: function () { return state.status !== STATUS_IDLE; },
    getState: getState,
    markRefreshPending: markRefreshPending,
    setSpeed: setSpeed,
    normalizePeriod: normalizePeriod,
    getCurrentPeriod: getCurrentPeriod,
  };

  global.BarReplayController = controller;
  init();
}(typeof window !== 'undefined' ? window : globalThis));
