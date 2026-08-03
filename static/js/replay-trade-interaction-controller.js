(function (root, factory) {
  'use strict';
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.ReplayTradeInteractionController = exported;
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  /*
   * 3.3.1 owns only the short-lived chart selection sessions.  It deliberately
   * does not know how prices are converted, how a trade is executed, or how a
   * panel is rendered.  Those concerns are supplied by the UI adapter below.
   */

  var TRADE_SIDES = ['buy', 'sell'];

  function safeCall(callback) {
    if (typeof callback !== 'function') return undefined;
    try {
      return callback.apply(null, Array.prototype.slice.call(arguments, 1));
    } catch (error) {
      return undefined;
    }
  }

  function dispose(scope) {
    var disposers = Array.isArray(scope) ? scope.splice(0) : [];
    for (var index = disposers.length - 1; index >= 0; index -= 1) {
      try { if (typeof disposers[index] === 'function') disposers[index](); } catch (error) {}
    }
  }

  function addListener(scope, target, name, handler) {
    if (!target || typeof target.addEventListener !== 'function' ||
        typeof target.removeEventListener !== 'function' || typeof handler !== 'function') {
      return false;
    }
    try {
      target.addEventListener(name, handler);
      scope.push(function () { target.removeEventListener(name, handler); });
      return true;
    } catch (error) {
      return false;
    }
  }

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function normalizeSide(value, normalizer) {
    var result = typeof normalizer === 'function' ? safeCall(normalizer, value) : value;
    result = String(result == null ? '' : result).toLowerCase();
    if (result === 'buy' || result === 'b' || result === 'long' || result === 'entry' || result === 'in') return 'buy';
    if (result === 'sell' || result === 's' || result === 'short' || result === 'exit' || result === 'out') return 'sell';
    return '';
  }

  function create(options) {
    options = options || {};
    var rootTarget = options.root || (typeof globalThis !== 'undefined' ? globalThis : null);
    var eventTarget = options.eventTarget || rootTarget;
    var callbacks = options.callbacks || {};
    var geometry = options.geometry || {};
    var state = {
      initialized: false,
      mainDom: null,
      mode: null,
      selectedBar: null,
      presetSelection: {
        active: false,
        side: null,
        amount: null,
        previewPrice: null,
        previewY: null,
        previewX: null,
      },
    };
    var selectionScope = [];
    var presetScope = [];

    function getMainDom() {
      var dom = safeCall(options.getMainDom);
      return dom || options.mainDom || state.mainDom || null;
    }

    function isActive() {
      var active = safeCall(options.isReplayActive);
      if (active !== undefined) return !!active;
      return true;
    }

    function redraw() { safeCall(callbacks.redraw); }
    function updateControls() { safeCall(callbacks.updateControls); }
    function setStatus(message) { safeCall(callbacks.setStatus, message); }
    function roleLabel(side) {
      return safeCall(callbacks.roleLabel, side) || side;
    }

    function setSelectingClass(dom, className, enabled) {
      safeCall(callbacks.setSelectingClass, dom, className, !!enabled);
    }

    function closePanels(kind) {
      safeCall(callbacks.closePanels, kind);
    }

    function resolvePoint(event) {
      var point = safeCall(geometry.pointFromEvent, event);
      return point && typeof point === 'object' ? point : null;
    }

    function resolvePricePoint(event) {
      var point = safeCall(geometry.priceFromEvent, event);
      if (!point || typeof point !== 'object' || finite(point.price) == null || Number(point.price) <= 0) return null;
      return point;
    }

    function eventCoordinates(event) {
      var coordinates = safeCall(geometry.eventCoordinates, event);
      return coordinates && typeof coordinates === 'object' ? coordinates : {};
    }

    function consumeClickSuppression(event) {
      if (typeof callbacks.consumeClickSuppression !== 'function') return false;
      return !!safeCall(callbacks.consumeClickSuppression, event);
    }

    function unbindSelection() {
      dispose(selectionScope);
      if (state.mainDom) setSelectingClass(state.mainDom, 'replay-trade-selecting', false);
    }

    function unbindPresetSelection() {
      dispose(presetScope);
      if (state.mainDom) setSelectingClass(state.mainDom, 'replay-trade-preset-selecting', false);
    }

    function selectionHandler(event) {
      if (consumeClickSuppression(event)) return;
      if (!state.mode || !isActive()) return;
      var point = resolvePoint(event || {});
      if (!point) return;
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      safeCall(callbacks.onTradePoint, state.mode, point);
    }

    function bindSelection() {
      unbindSelection();
      var dom = getMainDom();
      if (!dom || !addListener(selectionScope, dom, 'click', selectionHandler)) return false;
      state.mainDom = dom;
      setSelectingClass(dom, 'replay-trade-selecting', true);
      return true;
    }

    function clearPresetPreview() {
      state.presetSelection.previewPrice = null;
      state.presetSelection.previewY = null;
      state.presetSelection.previewX = null;
    }

    function cancelPresetSelection(silent) {
      var wasActive = !!state.presetSelection.active;
      unbindPresetSelection();
      state.presetSelection.active = false;
      state.presetSelection.side = null;
      state.presetSelection.amount = null;
      clearPresetPreview();
      if (wasActive && !silent) setStatus('已取消预设选点');
      safeCall(callbacks.onPresetState, getState().presetSelection);
      updateControls();
      redraw();
      return wasActive;
    }

    function presetSelectionPoint(event) {
      var point = resolvePricePoint(event || {});
      if (!point) {
        cancelPresetSelection(true);
        setStatus('无法转换水平价格，已取消预设选点');
        return null;
      }
      return point;
    }

    function updatePresetPreview(event) {
      if (!state.presetSelection.active || !isActive()) return;
      var point = presetSelectionPoint(event);
      if (!point) return;
      state.presetSelection.previewPrice = Number(point.price);
      state.presetSelection.previewY = finite(point.y);
      state.presetSelection.previewX = finite(point.x);
      setStatus('移动鼠标选择水平价格，点击确认' + roleLabel(state.presetSelection.side));
      safeCall(callbacks.onPresetState, getState().presetSelection);
      redraw();
    }

    function commitPresetPreview(event) {
      if (!state.presetSelection.active || !isActive()) return;
      var coordinates = eventCoordinates(event || {});
      var preview = state.presetSelection;
      var sameHorizontalPixel = preview.previewPrice != null && preview.previewY != null &&
        (coordinates.y == null || Math.abs(Number(coordinates.y) - Number(preview.previewY)) <= 2);
      var point = sameHorizontalPixel
        ? { price: Number(preview.previewPrice), x: preview.previewX, y: Number(preview.previewY) }
        : presetSelectionPoint(event);
      if (!point) return;
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      var result = safeCall(callbacks.onPresetPoint, state.presetSelection.side,
        state.presetSelection.amount, point);
      /* The UI callback historically cancels after submit; this second guard
       * keeps the controller correct when an adapter only records the command. */
      if (result !== false && state.presetSelection.active) cancelPresetSelection(true);
    }

    function presetKeydown(event) {
      if (event && event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault();
        cancelPresetSelection(false);
      }
    }

    function bindPresetSelection() {
      unbindPresetSelection();
      var dom = getMainDom();
      if (!dom || !addListener(presetScope, dom, 'mousemove', updatePresetPreview) ||
          !addListener(presetScope, dom, 'click', commitPresetPreview)) {
        dispose(presetScope);
        return false;
      }
      state.mainDom = dom;
      if (eventTarget && !addListener(presetScope, eventTarget, 'keydown', presetKeydown)) {
        dispose(presetScope);
        return false;
      }
      setSelectingClass(dom, 'replay-trade-preset-selecting', true);
      return true;
    }

    function cancelSelection() {
      state.mode = null;
      state.selectedBar = null;
      unbindSelection();
      updateControls();
      return true;
    }

    function enterSelection(side) {
      side = normalizeSide(side, callbacks.normalizeSide);
      if (TRADE_SIDES.indexOf(side) < 0) return false;
      if (!isActive()) {
        setStatus('请先开启回放，再选择交易价格');
        return false;
      }
      closePanels('trade');
      cancelPresetSelection(true);
      state.mode = side;
      if (!bindSelection()) {
        state.mode = null;
        updateControls();
        return false;
      }
      updateControls();
      setStatus(side === 'buy' ? '点击当前可见 K 线选择买入价格' : '点击当前可见 K 线选择卖出价格');
      return true;
    }

    function enterPresetSelection(side, amount) {
      var normalizedRole = typeof callbacks.normalizeRole === 'function'
        ? safeCall(callbacks.normalizeRole, side) : null;
      side = normalizedRole || normalizeSide(side, callbacks.normalizeSide);
      if (!side) return false;
      if (!isActive()) {
        setStatus('请先开启回放，再选择预设价格');
        return false;
      }
      if (side === 'buy' && (amount == null || Number(amount) <= 0)) {
        setStatus('买入金额必须大于 0');
        return false;
      }
      closePanels('preset');
      cancelSelection();
      cancelPresetSelection(true);
      state.presetSelection.active = true;
      state.presetSelection.side = side;
      state.presetSelection.amount = side === 'buy' ? Number(amount) : null;
      if (!bindPresetSelection()) {
        state.presetSelection.active = false;
        state.presetSelection.side = null;
        state.presetSelection.amount = null;
        clearPresetPreview();
        updateControls();
        return false;
      }
      updateControls();
      safeCall(callbacks.onPresetState, getState().presetSelection);
      setStatus('移动鼠标选择水平价格，点击确认' + roleLabel(side));
      redraw();
      return true;
    }

    function attachMainDom(dom) {
      dom = dom || getMainDom();
      if (state.mainDom === dom) return !!dom;
      var mode = state.mode;
      var preset = state.presetSelection.active;
      unbindSelection();
      unbindPresetSelection();
      state.mainDom = dom || null;
      if (mode) bindSelection();
      if (preset) bindPresetSelection();
      return !!dom;
    }

    function init() {
      if (state.initialized) return api;
      state.initialized = true;
      attachMainDom();
      return api;
    }

    function destroy() {
      unbindSelection();
      unbindPresetSelection();
      state.initialized = false;
      state.mainDom = null;
      state.mode = null;
      state.selectedBar = null;
      state.presetSelection.active = false;
      state.presetSelection.side = null;
      state.presetSelection.amount = null;
      clearPresetPreview();
      updateControls();
      return true;
    }

    function getState() {
      return {
        mode: state.mode,
        selectedBar: state.selectedBar,
        presetSelection: Object.assign({}, state.presetSelection),
      };
    }

    var api = {
      init: init,
      destroy: destroy,
      attachMainDom: attachMainDom,
      enterSelection: enterSelection,
      cancelSelection: cancelSelection,
      enterPresetSelection: enterPresetSelection,
      cancelPresetSelection: cancelPresetSelection,
      getState: getState,
    };
    return api;
  }

  return { create: create, createController: create };
}));
