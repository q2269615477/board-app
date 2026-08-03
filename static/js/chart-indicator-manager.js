(function (root, factory) {
  var exported = factory(root || {});
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root && root.document) {
    root.ChartIndicatorManager = exported.createManager(root);
    root.ChartIndicatorManager.start();
  }
})(typeof window !== 'undefined' ? window : globalThis, function (defaultRoot) {
  'use strict';

  var DEFAULT_MA_PERIODS = [5, 20, 60];
  var MA_STORAGE_KEY = 'board.chart.ma-periods.v1';
  var AMOUNT_STORAGE_KEY = 'board.chart.amount-enabled.v1';
  var MAX_MA_PERIODS = 8;

  function finiteNumber(value) {
    var number = Number(value);
    return isFinite(number) ? number : 0;
  }

  function normalizeMaPeriods(raw) {
    if (raw == null || raw === '') return DEFAULT_MA_PERIODS.slice();
    var values = Array.isArray(raw) ? raw.slice() : String(raw).split(/[\s,，/]+/);
    var periods = [];
    values.forEach(function (value) {
      if (value === '') return;
      var number = Number(value);
      if (!Number.isInteger(number) || number <= 0) {
        throw new Error('均线周期必须是正整数');
      }
      if (periods.indexOf(number) < 0) periods.push(number);
    });
    if (!periods.length) throw new Error('至少保留一个均线周期');
    if (periods.length > MAX_MA_PERIODS) {
      throw new Error('最多设置 ' + MAX_MA_PERIODS + ' 个均线周期');
    }
    return periods;
  }

  function buildAmountRows(dataList) {
    return (Array.isArray(dataList) ? dataList : []).map(function (bar) {
      var row = bar || {};
      var raw = row.amount != null ? row.amount : row.turnover;
      return { amount: finiteNumber(raw) };
    });
  }

  function createAmountIndicatorDefinition() {
    return {
      name: 'AMOUNT',
      shortName: '成交额',
      series: 'volume',
      precision: 2,
      shouldFormatBigNumber: true,
      minValue: 0,
      figures: [{
        key: 'amount',
        title: '成交额: ',
        type: 'bar',
        baseValue: 0,
        styles: function (data, indicator, defaults) {
          var current = data && data.current ? data.current : {};
          var bar = current.kLineData || current;
          var configured = indicator && indicator.styles && indicator.styles.bars
            ? indicator.styles.bars[0] : null;
          var fallback = defaults && defaults.bars ? defaults.bars[0] : null;
          var colors = configured || fallback || {};
          var color = colors.noChangeColor || '#76808f';
          if (finiteNumber(bar.close) > finiteNumber(bar.open)) {
            color = colors.upColor || '#ef5350';
          } else if (finiteNumber(bar.open) > finiteNumber(bar.close)) {
            color = colors.downColor || '#26a69a';
          }
          return { color: color };
        }
      }],
      calc: function (dataList) { return buildAmountRows(dataList); }
    };
  }

  function safeStorage(host) {
    try { return host.localStorage || null; } catch (_) { return null; }
  }

  function createManager(host) {
    host = host || defaultRoot || {};
    var document = host.document || null;
    var chart = null;
    var amountPaneId = null;
    var observer = null;
    var started = false;
    var registered = false;
    var schedule = typeof host.setTimeout === 'function'
      ? host.setTimeout.bind(host)
      : function (callback) { callback(); };

    function loadPeriods() {
      var storage = safeStorage(host);
      if (!storage) return DEFAULT_MA_PERIODS.slice();
      try { return normalizeMaPeriods(JSON.parse(storage.getItem(MA_STORAGE_KEY))); }
      catch (_) { return DEFAULT_MA_PERIODS.slice(); }
    }

    function savePeriods(periods) {
      var normalized = normalizeMaPeriods(periods);
      var storage = safeStorage(host);
      if (storage) storage.setItem(MA_STORAGE_KEY, JSON.stringify(normalized));
      return normalized;
    }

    function loadAmountEnabled() {
      var storage = safeStorage(host);
      return !!(storage && storage.getItem(AMOUNT_STORAGE_KEY) === '1');
    }

    function saveAmountEnabled(enabled) {
      var storage = safeStorage(host);
      if (storage) storage.setItem(AMOUNT_STORAGE_KEY, enabled ? '1' : '0');
    }

    function registerAmountIndicator() {
      if (registered || host.__boardAmountIndicatorRegistered) return true;
      if (!host.klinecharts || typeof host.klinecharts.registerIndicator !== 'function') return false;
      host.klinecharts.registerIndicator(createAmountIndicatorDefinition());
      registered = true;
      host.__boardAmountIndicatorRegistered = true;
      return true;
    }

    function applyMaPeriods(periods, targetChart) {
      var activeChart = targetChart || chart;
      var normalized = normalizeMaPeriods(periods == null ? loadPeriods() : periods);
      if (!activeChart || typeof activeChart.overrideIndicator !== 'function') return false;
      activeChart.overrideIndicator({ name: 'MA', calcParams: normalized }, 'candle_pane');
      return true;
    }

    function amountIndicatorExists() {
      if (!chart || !amountPaneId) return false;
      if (typeof chart.getIndicatorByPaneId !== 'function') return true;
      try { return !!chart.getIndicatorByPaneId(amountPaneId, 'AMOUNT'); }
      catch (_) { return false; }
    }

    function setAmountEnabled(enabled, options) {
      options = options || {};
      enabled = !!enabled;
      if (!chart) return false;
      registerAmountIndicator();
      if (enabled) {
        if (!amountIndicatorExists()) {
          if (typeof chart.createIndicator !== 'function') return false;
          amountPaneId = chart.createIndicator('AMOUNT', false, { height: 100 }) || null;
        }
      } else if (amountPaneId) {
        if (typeof chart.removeIndicator === 'function') {
          chart.removeIndicator(amountPaneId, 'AMOUNT');
        }
        amountPaneId = null;
      }
      if (options.persist !== false) saveAmountEnabled(enabled);
      refreshInjectedState();
      return enabled ? !!amountPaneId : true;
    }

    function stopRowEvent(event) {
      event.stopPropagation();
    }

    function button(text, title, className) {
      var element = document.createElement('button');
      element.type = 'button';
      element.className = className || '';
      element.textContent = text;
      element.title = title || text;
      return element;
    }

    function periodInput(value) {
      var wrap = document.createElement('span');
      wrap.className = 'board-ma-period-item';
      var input = document.createElement('input');
      input.type = 'number';
      input.min = '1';
      input.max = '1000';
      input.step = '1';
      input.value = String(value);
      function syncAriaLabel() {
        input.setAttribute('aria-label', input.value + ' 日均线周期');
      }
      syncAriaLabel();
      input.addEventListener('input', syncAriaLabel);
      var remove = button('×', '删除周期', 'board-ma-period-remove');
      remove.addEventListener('click', function (event) {
        stopRowEvent(event);
        var list = wrap.parentNode;
        if (list && list.children.length > 1) wrap.remove();
      });
      wrap.appendChild(input);
      wrap.appendChild(remove);
      return wrap;
    }

    function buildMaEditor() {
      var row = document.createElement('li');
      row.className = 'board-indicator-control-row board-ma-period-editor';
      row.setAttribute('data-board-indicator-control', 'ma-periods');
      ['click', 'mousedown', 'pointerdown'].forEach(function (name) {
        row.addEventListener(name, stopRowEvent);
      });

      var label = document.createElement('span');
      label.className = 'board-indicator-control-label';
      label.textContent = '均线周期';
      var list = document.createElement('span');
      list.className = 'board-ma-period-list';
      loadPeriods().forEach(function (period) { list.appendChild(periodInput(period)); });
      var add = button('+', '增加周期', 'board-ma-period-add');
      add.addEventListener('click', function (event) {
        stopRowEvent(event);
        if (list.children.length >= MAX_MA_PERIODS) return;
        list.appendChild(periodInput(''));
        list.lastChild.querySelector('input').focus();
      });
      var apply = button('应用', '应用均线周期', 'board-ma-period-apply');
      var feedback = document.createElement('span');
      feedback.className = 'board-indicator-feedback';
      function applyInputs(event) {
        if (event) stopRowEvent(event);
        try {
          var values = Array.from(list.querySelectorAll('input')).map(function (input) {
            return input.value;
          });
          var periods = savePeriods(values);
          applyMaPeriods(periods);
          feedback.textContent = '已应用';
          feedback.removeAttribute('data-error');
        } catch (error) {
          feedback.textContent = error.message || '周期无效';
          feedback.setAttribute('data-error', 'true');
        }
      }
      apply.addEventListener('click', applyInputs);
      list.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') applyInputs(event);
      });

      row.appendChild(label);
      row.appendChild(list);
      row.appendChild(add);
      row.appendChild(apply);
      row.appendChild(feedback);
      return row;
    }

    function buildAmountRow() {
      var row = document.createElement('li');
      row.className = 'row board-amount-indicator-row';
      row.setAttribute('data-board-indicator-control', 'amount');
      ['mousedown', 'pointerdown'].forEach(function (name) {
        row.addEventListener(name, stopRowEvent);
      });
      var label = document.createElement('label');
      label.className = 'board-amount-indicator-label';
      var input = document.createElement('input');
      input.type = 'checkbox';
      input.className = 'board-amount-indicator-checkbox';
      input.checked = amountIndicatorExists();
      var text = document.createElement('span');
      text.textContent = 'AMOUNT(成交额)';
      label.appendChild(input);
      label.appendChild(text);
      row.appendChild(label);
      row.addEventListener('click', function (event) {
        event.preventDefault();
        stopRowEvent(event);
        if (event.target !== input) input.checked = !input.checked;
        setAmountEnabled(input.checked);
      });
      return row;
    }

    function findSectionRow(children, titleIndex, nextTitleIndex, prefix) {
      for (var index = titleIndex + 1; index < nextTitleIndex; index += 1) {
        var text = String(children[index].textContent || '').trim();
        if (text === prefix || text.indexOf(prefix + '(') === 0) return children[index];
      }
      return null;
    }

    function injectControls(list) {
      if (!list || list.querySelector('[data-board-indicator-control]')) {
        refreshInjectedState();
        return false;
      }
      var children = Array.from(list.children);
      var titleIndexes = [];
      children.forEach(function (child, index) {
        if (child.classList.contains('title')) titleIndexes.push(index);
      });
      if (titleIndexes.length < 2) return false;
      var mainMa = findSectionRow(children, titleIndexes[0], titleIndexes[1], 'MA');
      var subVol = findSectionRow(children, titleIndexes[1], children.length, 'VOL');
      if (!mainMa || !subVol) return false;
      if (!mainMa.hasAttribute('data-board-ma-sync')) {
        mainMa.setAttribute('data-board-ma-sync', 'true');
        mainMa.addEventListener('click', function () {
          schedule(function () { applyMaPeriods(loadPeriods()); }, 0);
          schedule(function () { applyMaPeriods(loadPeriods()); }, 80);
        });
      }
      mainMa.insertAdjacentElement('afterend', buildMaEditor());
      subVol.insertAdjacentElement('afterend', buildAmountRow());
      return true;
    }

    function scanIndicatorModal() {
      if (!document) return false;
      var lists = document.querySelectorAll('.klinecharts-pro-indicator-modal-list');
      var injected = false;
      Array.from(lists).forEach(function (list) {
        injected = injectControls(list) || injected;
      });
      return injected;
    }

    function refreshInjectedState() {
      if (!document) return;
      Array.from(document.querySelectorAll('.board-amount-indicator-checkbox')).forEach(function (input) {
        input.checked = amountIndicatorExists();
      });
    }

    function onChartReady(nextChart) {
      if (!nextChart) return false;
      if (chart !== nextChart) amountPaneId = null;
      chart = nextChart;
      registerAmountIndicator();
      [0, 60, 180].forEach(function (delay) {
        schedule(function () { applyMaPeriods(loadPeriods()); }, delay);
      });
      if (loadAmountEnabled()) {
        schedule(function () { setAmountEnabled(true, { persist: false }); }, 80);
      }
      return true;
    }

    function start() {
      if (started) return api;
      started = true;
      registerAmountIndicator();
      if (host.addEventListener) {
        host.addEventListener('kline-chart-ready', function (event) {
          onChartReady(event && event.detail ? event.detail : host.__kline_chart);
        });
      }
      if (document && host.MutationObserver) {
        observer = new host.MutationObserver(scanIndicatorModal);
        observer.observe(document.documentElement || document.body, { childList: true, subtree: true });
      }
      if (host.__kline_chart) onChartReady(host.__kline_chart);
      scanIndicatorModal();
      return api;
    }

    function stop() {
      if (observer) observer.disconnect();
      observer = null;
      started = false;
    }

    var api = {
      start: start,
      stop: stop,
      onChartReady: onChartReady,
      applyMaPeriods: applyMaPeriods,
      getMaPeriods: loadPeriods,
      setMaPeriods: function (periods) {
        var normalized = savePeriods(periods);
        applyMaPeriods(normalized);
        return normalized;
      },
      setAmountEnabled: setAmountEnabled,
      isAmountEnabled: amountIndicatorExists,
      injectControls: injectControls,
      scanIndicatorModal: scanIndicatorModal
    };
    return api;
  }

  return {
    DEFAULT_MA_PERIODS: DEFAULT_MA_PERIODS.slice(),
    MA_STORAGE_KEY: MA_STORAGE_KEY,
    AMOUNT_STORAGE_KEY: AMOUNT_STORAGE_KEY,
    normalizeMaPeriods: normalizeMaPeriods,
    buildAmountRows: buildAmountRows,
    createAmountIndicatorDefinition: createAmountIndicatorDefinition,
    createManager: createManager
  };
});
