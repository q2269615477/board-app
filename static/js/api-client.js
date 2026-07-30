const store = { activeCat:null, selected:null, categoryData:null, _seq:0, _expandedCats:{}, _catSortState:{} };
// 暴露给 session-ui / annotation-ui / MCP / 调试（脚本块内 const 默认不进 window）
window.store = store;
// 全局错误边界：防止单个JS错误打垮整个UI
window.addEventListener('error', function(e){console.warn('[ErrorBoundary]',e.message||e, e.filename, e.lineno, e.error && e.error.stack);e.preventDefault();});
window.addEventListener('unhandledrejection', function(e){console.warn('[ErrorBoundary]',e.reason);e.preventDefault();});
// 页面卸载时清理所有定时器（防止内存泄漏）
const _allTimers = new Set();
let _origSetInt = window.setInterval; window.setInterval = function(f,d,...a){const id=_origSetInt(f,d,...a);_allTimers.add(id);return id;};
let _origSetTO = window.setTimeout; window.setTimeout = function(f,d,...a){const id=_origSetTO(f,d,...a);_allTimers.add(id);return id;};
let _origClearInt = window.clearInterval; window.clearInterval = function(id){_allTimers.delete(id);return _origClearInt(id);};
window.addEventListener('beforeunload', function(){_allTimers.forEach(id=>clearInterval(id));_allTimers.clear();});
let pro = null;
const API = '';
window.API = API;
// 左侧板块导航栏收缩/展开
function toggleNavPanel(){
  document.getElementById('app').classList.toggle('nav-collapsed');
  var btn = document.getElementById('nav-panel-toggle');
  if (btn) btn.textContent = document.getElementById('app').classList.contains('nav-collapsed') ? '\u25B6' : '\u25C0';
  if (typeof smoothResizeChart === 'function') {
    smoothResizeChart(300);
  } else {
    var start = performance.now();
    function step(now) {
      window.dispatchEvent(new Event('resize'));
      if (now - start < 300) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
}
/* 预先声明所有可能被初始化函数引用的变量（避免TDZ） */
let _analysisHistory = [];
const _historyKey = 'ai_analysis_history';
let _lastClickBoard=null;
let _lastClickTime=0;
let _pendingSym=0;
let _preloadDone = {};
let _currentReqId = 0;
let _aiPanelCache = null;
let _navPanelScroll = 0;
let _pinyinData = {};
let _sm = null;
// 类型样式（顶部已定义，这里只引用）
const _chartLoadingTimers = {};

// 统一 board ctx 写入，避免多处漂移
window.setBoardCtx = function(partial) {
  const prev = window.__board_ctx || {};
  const next = Object.assign({}, prev, partial || {});
  window.__board_ctx = next;
  try {
    if (window.store) {
      if (!window.store.selected) window.store.selected = {};
      const s = window.store.selected;
      if (next.code != null) s.code = next.code;
      if (next.name != null) s.name = next.name;
      if (next.type != null) s.type = next.type;
      if (next.period != null) s.period = next.period;
      if (next.symbol != null && (s.code == null || s.code === '')) s.code = next.symbol;
    }
  } catch (_) {}
  return next;
};

// 统一 fetch：timeout / 可选 dedupe / JSON 防御
const _apiInflight = new Map();
window.apiFetch = async function(url, options) {
  options = options || {};
  const timeout = options.timeout != null ? options.timeout : 12000;
  const retries = options.retries != null ? options.retries : 0;
  const dedupeKey = options.dedupeKey || null;
  const externalSignal = options.signal;
  const fetchOpts = Object.assign({}, options);
  delete fetchOpts.timeout;
  delete fetchOpts.retries;
  delete fetchOpts.dedupeKey;

  if (dedupeKey && _apiInflight.has(dedupeKey)) {
    return _apiInflight.get(dedupeKey);
  }

  const run = async function() {
    let lastErr = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      let timer = null;
      if (ctrl) {
        timer = setTimeout(function() { try { ctrl.abort(); } catch(_){} }, timeout);
        if (externalSignal) {
          if (externalSignal.aborted) {
            try { ctrl.abort(); } catch(_){}
          } else {
            externalSignal.addEventListener('abort', function() { try { ctrl.abort(); } catch(_){} });
          }
          fetchOpts.signal = ctrl.signal;
        } else {
          fetchOpts.signal = ctrl.signal;
        }
      }
      try {
        const r = await fetch(url, fetchOpts);
        if (timer) clearTimeout(timer);
        const ct = (r.headers && r.headers.get && r.headers.get('content-type')) || '';
        let data = null;
        if (ct.indexOf('application/json') >= 0 || ct.indexOf('+json') >= 0) {
          try { data = await r.json(); } catch (e) { data = null; }
        } else {
          try {
            const text = await r.text();
            try { data = JSON.parse(text); } catch (_) { data = text; }
          } catch (_) { data = null; }
        }
        if (!r.ok) {
          const err = new Error((data && (data.error || data.message)) || ('HTTP ' + r.status));
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return { ok: true, status: r.status, data: data, response: r };
      } catch (e) {
        if (timer) clearTimeout(timer);
        lastErr = e;
        if (e && e.name === 'AbortError') throw e;
        if (externalSignal && externalSignal.aborted) throw e;
        if (attempt >= retries) throw e;
      }
    }
    throw lastErr || new Error('apiFetch failed');
  };

  const p = run().finally(function() {
    if (dedupeKey) _apiInflight.delete(dedupeKey);
  });
  if (dedupeKey) _apiInflight.set(dedupeKey, p);
  return p;
};
