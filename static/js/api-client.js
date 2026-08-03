const store = { activeCat:null, selected:null, categoryData:null, _seq:0, _expandedCats:{}, _catSortState:{} };
// 暴露给 session-ui / annotation-ui / MCP / 调试（脚本块内 const 默认不进 window）
window.store = store;
// 全局错误观测：保留浏览器原始错误传播，便于真实浏览器测试和开发者工具发现回归。
window.addEventListener('error', function(e){console.warn('[ErrorBoundary]',e.message||e, e.filename, e.lineno, e.error && e.error.stack);});
window.addEventListener('unhandledrejection', function(e){console.warn('[ErrorBoundary]',e.reason);});
let pro = null;
const API = '';
window.API = API;

// 多标签轮询租约：内置浏览器可能把所有标签都报告为 visible，
// 因此不能只依赖 document.hidden。同源标签共享一个短租约，只有持有者轮询。
(function installPollingLeader() {
  if (window.boardPollingLeader) return;
  const key = 'BOARD_APP_POLL_LEADER_V1';
  const tabId = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  const leaseMs = 8000;

  function readLease() {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); }
    catch (_) { return null; }
  }

  function isLeader() {
    const now = Date.now();
    let lease = readLease();
    if (!lease || !lease.id || Number(lease.expires || 0) <= now || lease.id === tabId) {
      try {
        localStorage.setItem(key, JSON.stringify({ id: tabId, expires: now + leaseMs }));
        lease = readLease();
      } catch (_) {
        lease = { id: tabId, expires: now + leaseMs };
      }
    }
    const leader = !!lease && lease.id === tabId;
    document.documentElement.dataset.pollLeader = leader ? '1' : '0';
    return leader;
  }

  function release() {
    try {
      const lease = readLease();
      if (lease && lease.id === tabId) localStorage.removeItem(key);
    } catch (_) {}
  }

  window.boardPollingLeader = { isLeader: isLeader, release: release, tabId: tabId };
  window.addEventListener('beforeunload', release);
})();
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
