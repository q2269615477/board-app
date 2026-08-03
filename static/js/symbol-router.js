/**
 * symbol-router.js — 标的选择路由器
 *
 * 职责：
 * - 统一识别 stock / index / board / hk_index / global_index 类型
 * - 统一补全 code、name、type、source
 * - 不渲染图表，不直接触发任何 UI 更新
 *
 * 使用方式：
 *   importSymbol(input)  -> 标准化为 { code, name, type, source, trigger }
 *   classify(code)      -> 返回类型字符串
 */
(function (global) {
  'use strict';

  // 类型优先级从高到低：前缀匹配 > 特殊 ticker > 启发式
  var HK_INDEX_TICKERS = { HSI: 1, HSTECH: 1, HSCEI: 1, HSHKI: 1 };
  var US_INDEX_TICKERS = { SPX: 1, IXIC: 1, DJI: 1, '^N225': 1, '^KS11': 1, '^TWII': 1 };

  /**
   * 根据代码/标识符判断标的类型。
   * 返回: 'stock' | 'index' | 'industry' | 'concept' | 'hk_index' | 'global_index' | 'board'
   */
  function classify(code) {
    if (!code) return 'stock';
    var c = String(code).trim();

    // 东方财富全A — 必须在纯数字检测之前
    if (c === '800000') return 'index';

    // A 股指数。分类树中的指数使用 sh000xxx/sz399xxx 形式；不要用
    // 有限的白名单，否则新增指数（如 sz399811）会被识别成个股。
    if (/^(sh|sz|bj)\d{6}$/.test(c)) {
      if (/^sh000\d{3}$/.test(c) || /^sz399\d{3}$/.test(c) ||
          /^bj899\d{3}$/.test(c)) return 'index';
      return 'stock';
    }

    // BK 开头 = 板块 (industry or concept)
    if (/^BK\d{4,}$/.test(c)) return 'board';

    // 港股指数
    if (HK_INDEX_TICKERS[c]) return 'hk_index';

    // 全球/美股指数 (含 ^ 前缀)
    if (US_INDEX_TICKERS[c] || c.indexOf('^') === 0) return 'global_index';

    // 纯 6 位数字 = A 股个股
    if (/^\d{6}$/.test(c)) return 'stock';

    // 默认
    return 'stock';
  }

  /**
   * 标准化输入为统一选择对象。
   *
   * 接受多种输入形式：
   *   importSymbol({ code, name, type, source, trigger })
   *   importSymbol({ ticker, name, type, source })
   *   importSymbol(code, name, type, source)
   *
   * 返回：
   *   { code, name, type, source, trigger }  (type 为标准化类型)
   */
  function importSymbol(input, name, type, source) {
    var code, src, trig;

    if (input && typeof input === 'object') {
      code = input.code || input.ticker || '';
      name = input.name || name || '';
      type = input.type || type || '';
      src = input.source || source || 'unknown';
      trig = input.trigger || 'programmatic';
    } else {
      code = String(input || '');
      name = name || '';
      type = type || '';
      src = source || 'unknown';
      trig = 'programmatic';
    }

    if (!code) return null;

    // 自动识别类型
    var resolvedType = type || classify(code);

    // 归一化 code
    code = normalizeCode(code, resolvedType);

    return {
      code: code,
      name: name || code,
      type: resolvedType,
      source: src,
      trigger: trig,
    };
  }

  /**
   * 归一化代码格式。
   * 确保 A 股指数以 sh/sz 前缀存储，个股保持原始形式。
   */
  function normalizeCode(code, type) {
    var c = String(code).trim();
    // 东财 Choice 自有市场代码，官方 secid 为 47.800000；不能补成
    // 不存在的上交所代码 sh800000。
    if (c === '800000') return c;
    if (type === 'index' && /^\d{6}$/.test(c)) {
      if (c.indexOf('399') === 0) return 'sz' + c;
      if (c.indexOf('899') === 0) return 'bj' + c;
      return 'sh' + c;
    }
    // hk_index / global_index 保持原样
    return c;
  }

  /**
   * 从 DOM 元素提取选择对象。
   * 供事件处理器使用。
   */
  function fromElement(el) {
    if (!el) return null;
    var data = el.dataset;
    if (!data) return null;
    return importSymbol({
      code: data.code || data.ticker,
      name: data.name,
      type: data.type,
      source: 'dom',
    });
  }

  // 导出
  global.SymbolRouter = {
    classify: classify,
    importSymbol: importSymbol,
    normalizeCode: normalizeCode,
    fromElement: fromElement,
  };

})(window);
