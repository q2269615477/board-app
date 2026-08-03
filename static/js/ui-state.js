/**
 * ui-state.js — 全局 UI 状态管理
 *
 * 职责：
 * - 保存当前选中标的 (symbol)
 * - 保存当前周期 (period)
 * - 保存当前图表加载状态 (loading)
 * - 提供只读快照
 * - 状态变更时触发 'ui-state-changed' 事件
 *
 * 禁止：
 * - 不直接操作 DOM
 * - 不直接调用 API
 * - 不承载业务逻辑
 */
(function (global) {
  'use strict';

  var _state = {
    symbol: null,       // { code, name, type }
    period: 'daily',    // daily / weekly / monthly / quarterly / yearly / 1m / 5m ...
    loading: false,     // 是否正在加载 K 线
    viewMode: 'kline',  // kline / empty
  };

  var _listeners = [];

  /**
   * 获取当前状态的只读快照。
   */
  function snapshot() {
    return {
      symbol: _state.symbol ? Object.assign({}, _state.symbol) : null,
      period: _state.period,
      loading: _state.loading,
      viewMode: _state.viewMode,
    };
  }

  /**
   * 更新选中标的。
   * @param {Object} symbol — { code, name, type }
   * @param {string} source — 触发来源
   */
  function setSymbol(symbol, source) {
    if (!symbol || !symbol.code) return false;

    var prev = _state.symbol;
    var changed = !prev ||
      prev.code !== symbol.code ||
      prev.type !== symbol.type;

    _state.symbol = {
      code: symbol.code,
      name: symbol.name || symbol.code,
      type: symbol.type || 'stock',
    };

    if (changed) {
      _notify({
        type: 'symbol-changed',
        symbol: snapshot().symbol,
        source: source || 'unknown',
      });
    }

    return changed;
  }

  /**
   * 更新周期。
   */
  function setPeriod(period) {
    if (!period || period === _state.period) return false;
    _state.period = period;
    _notify({ type: 'period-changed', period: period });
    return true;
  }

  /**
   * 设置加载状态。
   */
  function setLoading(loading, meta) {
    if (_state.loading === loading) return false;
    _state.loading = !!loading;
    _notify({ type: 'loading-changed', loading: _state.loading, meta: meta || null });
    return true;
  }

  /**
   * 获取选中标的代码（简化访问）。
   */
  function currentCode() {
    return _state.symbol ? _state.symbol.code : null;
  }

  /**
   * 检查是否为某个标的。
   */
  function isSelected(code, type) {
    if (!_state.symbol) return false;
    if (_state.symbol.code !== code) return false;
    if (type && _state.symbol.type !== type) return false;
    return true;
  }

  /**
   * 注册状态变更监听器。
   * @param {Function} fn — 回调函数，接收 { type, ...payload }
   * @returns {Function} 取消订阅函数
   */
  function subscribe(fn) {
    if (typeof fn !== 'function') return function () {};
    _listeners.push(fn);
    return function () {
      var idx = _listeners.indexOf(fn);
      if (idx >= 0) _listeners.splice(idx, 1);
    };
  }

  /**
   * 获取当前周期（Pro 格式）。
   */
  function periodAsPro() {
    var p = _state.period;
    if (p === 'daily') return { multiplier: 1, timespan: 'day' };
    if (p === 'weekly') return { multiplier: 1, timespan: 'week' };
    if (p === 'monthly') return { multiplier: 1, timespan: 'month' };
    if (p === 'quarterly') return { multiplier: 3, timespan: 'month' };
    if (p === 'yearly') return { multiplier: 1, timespan: 'year' };
    if (p === '1m') return { multiplier: 1, timespan: 'minute' };
    if (p === '5m') return { multiplier: 5, timespan: 'minute' };
    if (p === '15m') return { multiplier: 15, timespan: 'minute' };
    if (p === '30m') return { multiplier: 30, timespan: 'minute' };
    if (p === '60m' || p === '1H') return { multiplier: 1, timespan: 'hour' };
    return { multiplier: 1, timespan: 'day' };
  }

  function _notify(event) {
    for (var i = 0; i < _listeners.length; i++) {
      try { _listeners[i](event); } catch (e) { /* 静默 */ }
    }
  }

  // 导出
  global.UIState = {
    snapshot: snapshot,
    setSymbol: setSymbol,
    setPeriod: setPeriod,
    setLoading: setLoading,
    currentCode: currentCode,
    isSelected: isSelected,
    subscribe: subscribe,
    periodAsPro: periodAsPro,
  };

})(window);
