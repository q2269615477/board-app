/**
 * unified-selector.js — 统一选择事件总线
 *
 * 职责：
 * - 监听全局的 'select-symbol' 事件
 * - 标准化输入（通过 SymbolRouter.importSymbol）
 * - 更新 UIState
 * - 调用 ChartController.applySymbol
 * - 同步上下文到后端
 *
 * 这是连接所有选择入口的核心枢纽。
 *
 * 事件协议：
 *   window.dispatchEvent(new CustomEvent('select-symbol', {
 *     detail: { code, name, type, source, trigger }
 *   }));
 */
(function (global) {
  'use strict';

  var _enabled = true;

  /**
   * 处理选择事件的主入口。
   */
  function handleSelect(detail) {
    if (!_enabled || !detail) return;
    var code = detail.code || detail.ticker;
    if (!code) return;

    // 标准化
    var sel;
    if (global.SymbolRouter) {
      sel = global.SymbolRouter.importSymbol(detail);
    } else {
      sel = {
        code: code,
        name: detail.name || code,
        type: detail.type || 'stock',
        source: detail.source || 'unknown',
        trigger: detail.trigger || 'programmatic',
      };
    }
    if (!sel) return;

    // 更新 UI 状态
    if (global.UIState) {
      global.UIState.setSymbol(sel, sel.source);
    }

    // 应用到图表 — 唯一入口：ChartController
    if (global.ChartController) {
      global.ChartController.applySymbol(sel);
    } else {
      // ChartController 不可用时排队，init() 会消费
      global.__pendingSelectSymbol = sel;
      console.warn('[UnifiedSelector] ChartController not ready, queued:', sel.code);
    }

    // 同步上下文到后端
    _syncContext(sel);

    // 更新顶部价格栏
    if (typeof window.updateChartHeaderChg === 'function') {
      try { window.updateChartHeaderChg(sel.name, sel.code, sel.type); } catch (_) {}
    }
  }

  /**
   * 启用/禁用选择处理。
   */
  function setEnabled(v) {
    _enabled = !!v;
  }

  /**
   * 监听 select-symbol 事件。
   */
  function bind() {
    window.addEventListener('select-symbol', function (e) {
      if (e && e.detail) handleSelect(e.detail);
    });

    // UIState 周期变化 → update pro.setPeriod
    if (global.UIState && global.UIState.subscribe) {
      global.UIState.subscribe(function (event) {
        if (event.type === 'period-changed' && global.ChartController) {
          var proPeriod = global.UIState.periodAsPro();
          global.ChartController.applyPeriod(proPeriod);
        }
      });
    }
  }

  /**
   * 同步面板上下文到后端。
   */
  function _syncContext(sel) {
    var ctx = {
      code: sel.code,
      type: sel.type,
      name: sel.name,
      symbol: sel.code,
      period: global.UIState ? global.UIState.snapshot().period : 'daily',
      range: '',
    };

    if (typeof window.setBoardCtx === 'function') {
      try { window.setBoardCtx(ctx); } catch (_) {}
    }
    window.__board_ctx = ctx;

    // fire-and-forget 同步到后端
    fetch('/api/ctx', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ctx),
    }).catch(function () {});
  }

  // 自动绑定
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  // 导出
  global.UnifiedSelector = {
    handleSelect: handleSelect,
    bind: bind,
    setEnabled: setEnabled,
  };

})(window);
