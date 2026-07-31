/**
 * chart-controller.js — 图表控制器
 *
 * 职责：
 * - 唯一图表加载入口
 * - 接收标准选择对象 { code, name, type }
 * - 调用 API 更新 K 线图 (通过 Pro 的 setSymbol)
 * - 协调 loading 状态和错误提示
 *
 * 禁止：
 * - 不直接识别类型（委托 SymbolRouter）
 * - 不管理全局状态（委托 UIState）
 * - 不处理选择事件来源（委托 UIState.setSymbol 之后的订阅回调）
 */
(function (global) {
  'use strict';

  var _pro = null;
  var _datafeed = null;
  var _pendingSymbol = null;
  var _loading = false;
  var _loadSeq = 0;

  /**
   * 初始化（在 Pro 加载完成后调用）。
   */
  function init() {
    _pro = global.pro || window.pro;
    _datafeed = global._datafeed || window._datafeed;
    if (_pendingSymbol) {
      var sym = _pendingSymbol;
      _pendingSymbol = null;
      applySymbol(sym);
    }
    // 消费 UnifiedSelector 在 ChartController 未就绪时排队的标的
    if (global.__pendingSelectSymbol) {
      var queued = global.__pendingSelectSymbol;
      global.__pendingSelectSymbol = null;
      applySymbol(queued);
    }
    return !!_pro;
  }

  /**
   * 应用标的到图表。
   * 这是切换标的的唯一入口。
   *
   * @param {Object} sel — 标准选择对象 { code, name, type }
   */
  function applySymbol(sel) {
    if (!sel || !sel.code) return;

    var pro = _pro || (window.pro);
    if (!pro || typeof pro.setSymbol !== 'function') {
      _pendingSymbol = sel;
      console.warn('[ChartController] Pro not ready, queued:', sel.code);
      return;
    }

    var seq = ++_loadSeq;

    // 清理旧请求
    if (_datafeed && typeof _datafeed.abortAllRequests === 'function') {
      _datafeed.abortAllRequests();
    }

    // 构造 Pro symbol 对象
    var symbol = {
      ticker: sel.code,
      name: sel.name || sel.code,
      shortName: sel.name || sel.code,
      type: sel.type || 'stock',
      market: _marketForType(sel.type),
      priceCurrency: _currencyForType(sel.type),
    };

    // 检查缓存
    var hasCache = _datafeed && typeof _datafeed.hasFreshDailyCache === 'function'
      && _datafeed.hasFreshDailyCache(sel.code);

    if (!hasCache) {
      if (global.UIState) global.UIState.setLoading(true, { code: sel.code });
    }

    try {
      pro.setSymbol(symbol);
    } catch (e) {
      console.error('[ChartController] setSymbol failed:', e);
      if (global.UIState) global.UIState.setLoading(false);
    }

    // 加载信号
    if (typeof window.loadSignals === 'function') {
      try { window.loadSignals(sel.code); } catch (_) {}
    }

    return symbol;
  }

  /**
   * 应用周期到图表。
   */
  function applyPeriod(periodObj) {
    var pro = _pro || (window.pro);
    if (!pro || typeof pro.setPeriod !== 'function') return;
    try {
      pro.setPeriod(periodObj);
    } catch (e) {
      console.warn('[ChartController] setPeriod failed:', e);
    }
  }

  /**
   * 重置/清空图表。
   */
  function clear() {
    _loadSeq++;
    _pendingSymbol = null;
    if (global.UIState) global.UIState.setLoading(false);
  }

  /**
   * 刷新当前标的（不切换标的，仅清除缓存并重载）。
   * 用于 SSE 推送、rt-kline-ready 等场景。
   */
  function refreshCurrent(meta) {
    var state = global.UIState ? global.UIState.snapshot() : null;
    var sel = state && state.symbol;
    if (!sel || !sel.code) return false;

    // 清除缓存
    if (_datafeed && typeof _datafeed.clearByTicker === 'function') {
      _datafeed.clearByTicker(sel.code);
    }

    // 重新 apply 当前 symbol（不切换）
    applySymbol(Object.assign({}, sel, {
      source: (meta && meta.source) || 'refresh-current-symbol',
      trigger: 'refresh'
    }));

    return true;
  }

  /**
   * 监听 kline-loaded / kline-error 事件以同步 loading 状态。
   */
  function bindLoadingSync() {
    window.addEventListener('kline-loaded', function (e) {
      if (!e.detail) return;
      var cur = global.UIState ? global.UIState.currentCode() : null;
      if (cur && e.detail.symbol === cur && e.detail.ok) {
        if (global.UIState) global.UIState.setLoading(false);
      }
    });

    window.addEventListener('kline-error', function (e) {
      if (!e.detail) return;
      var cur = global.UIState ? global.UIState.currentCode() : null;
      if (cur && e.detail.symbol === cur) {
        if (global.UIState) global.UIState.setLoading(false);
      }
    });

    // 监听统一刷新事件
    window.addEventListener('refresh-current-symbol', function (e) {
      refreshCurrent(e.detail || {});
    });
  }

  /**
   * 获取类型对应的市场标识。
   */
  function _marketForType(type) {
    if (type === 'hk_index') return 'HK';
    if (type === 'global_index') return 'US';
    return 'A';
  }

  /**
   * 获取类型对应的货币。
   */
  function _currencyForType(type) {
    if (type === 'hk_index') return 'HKD';
    if (type === 'global_index') return 'USD';
    return 'CNY';
  }

  // 导出
  global.ChartController = {
    init: init,
    applySymbol: applySymbol,
    applyPeriod: applyPeriod,
    refreshCurrent: refreshCurrent,
    clear: clear,
    bindLoadingSync: bindLoadingSync,
  };

  // 自动绑定 loading 同步
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindLoadingSync);
  } else {
    bindLoadingSync();
  }

})(window);
