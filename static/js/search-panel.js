// ===== 8. 增强搜索（个股+指数+板块，拼音匹配，搜索历史） =====

// 共享搜索历史未通过入口脚本加载时，使用同一 localStorage 协议安全回退。
function _getSearchHistoryStore(){
  var host = typeof window !== 'undefined' ? window : globalThis;
  var shared = host && host.BoardSearchHistory;
  if(shared && typeof shared.list === 'function' && typeof shared.add === 'function') return shared;
  if(host && host.__boardSearchHistoryFallback) return host.__boardSearchHistoryFallback;

  var key = 'board_app_search_history';
  var maxItems = 5;
  var storage = null;
  try { storage = host && host.localStorage ? host.localStorage : null; } catch(_){ storage = null; }
  function text(value){ return value == null ? '' : String(value).trim(); }
  function normalize(item){
    if(!item || typeof item !== 'object' || Array.isArray(item)) return null;
    var code = text(item.code);
    var value = text(item.value != null ? item.value : item.query);
    if(!code && !value) return null;
    return {
      code: code,
      value: value,
      name: text(item.name),
      type: text(item.type),
      category: text(item.category),
      display_code: text(item.display_code || item.displayCode),
      initials: text(item.initials),
      time: Number.isFinite(Number(item.time)) ? Number(item.time) : Date.now()
    };
  }
  function same(a,b){
    var ac=text(a.code).toLowerCase(), bc=text(b.code).toLowerCase();
    var av=text(a.value).toLowerCase(), bv=text(b.value).toLowerCase();
    return (!!ac && ac===bc) || (!!av && av===bv);
  }
  function list(){
    if(!storage || typeof storage.getItem !== 'function') return [];
    try{
      var parsed = JSON.parse(storage.getItem(key) || '[]');
      if(!Array.isArray(parsed)) return [];
      var result=[];
      parsed.forEach(function(item){
        var normalized=normalize(item);
        if(normalized && !result.some(function(existing){ return same(existing, normalized); })) result.push(normalized);
      });
      var bounded = result.slice(0,maxItems);
      if(bounded.length !== parsed.length){
        try{ if(storage && typeof storage.setItem === 'function') storage.setItem(key, JSON.stringify(bounded)); }catch(_){ }
      }
      return bounded;
    }catch(_){ return []; }
  }
  function write(items){
    var result = (Array.isArray(items) ? items : []).slice(0,maxItems);
    try{ if(storage && typeof storage.setItem === 'function') storage.setItem(key, JSON.stringify(result)); }catch(_){ }
    return result;
  }
  var fallback = {
    list: list,
    load: list,
    add: function(item){
      var normalized=normalize(item), current=list();
      if(!normalized) return current;
      return write([normalized].concat(current.filter(function(existing){ return !same(existing, normalized); })));
    },
    clear: function(){ return write([]); }
  };
  if(host) host.__boardSearchHistoryFallback = fallback;
  return fallback;
}

var _searchHistory = [];

function persistSearchHistoryRemote(item){
  if(!item || !item.code) return;
  var options = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(item)
  };
  var request = typeof window.apiFetch === 'function'
    ? window.apiFetch('/api/search/history', Object.assign({timeout:5000}, options))
    : (typeof fetch === 'function' ? fetch('/api/search/history', options) : null);
  if(request && typeof request.catch === 'function') request.catch(function(){});
}

window.recordBoardSearchHistory = persistSearchHistoryRemote;

var _searchHistoryHydrationPromise = null;
var _searchHistoryHydrated = false;

function hydrateSearchHistoryFromRemote(){
  if(_searchHistoryHydrated) return Promise.resolve(loadSearchHistory());
  if(_searchHistoryHydrationPromise) return _searchHistoryHydrationPromise;
  var request = typeof window.apiFetch === 'function'
    ? window.apiFetch('/api/search/history', {timeout:5000, dedupeKey:'search-history-hydrate'})
    : (typeof fetch === 'function' ? fetch('/api/search/history').then(function(response){ return response.ok ? response.json() : {data:[]}; }) : null);
  if(!request) return Promise.resolve(loadSearchHistory());
  _searchHistoryHydrationPromise = request
    .then(function(payload){
      if(payload && payload.ok === true && payload.data) payload = payload.data;
      var remote = payload && Array.isArray(payload.data) ? payload.data.slice(0,5) : [];
      var store = _getSearchHistoryStore();
      if(typeof store.replace === 'function') store.replace(remote.concat(store.list()));
      else remote.slice().reverse().forEach(function(item){ store.add(item); });
      loadSearchHistory();
      _searchHistoryHydrated = true;
      window.dispatchEvent(new CustomEvent('board-search-history-ready'));
      return _searchHistory;
    })
    .catch(function(){ return loadSearchHistory(); })
    .finally(function(){ _searchHistoryHydrationPromise = null; });
  return _searchHistoryHydrationPromise;
}

window.ensureBoardSearchHistory = hydrateSearchHistoryFromRemote;

function loadSearchHistory(){
  try { _searchHistory = _getSearchHistoryStore().list(); }
  catch(e){ _searchHistory=[]; }
  return _searchHistory;
}
function addSearchHistory(code, name, type, category, value){
  var item = {
    code: code,
    value: value || name || code,
    name: name,
    type: type,
    category: category,
    time: Date.now()
  };
  try {
    _searchHistory = _getSearchHistoryStore().add(item);
  } catch(e) { loadSearchHistory(); }
  persistSearchHistoryRemote(item);
  return _searchHistory;
}

// 类型颜色/标签映射（“行”、“指”、“概”、“个”单字 Tag 提示）
const _typeStyles = {
  'stock':    {color:'#26a69a', label:'个'},
  'index':    {color:'#ab47bc', label:'指'},
  'hk_index': {color:'#ab47bc', label:'指'},
  'industry': {color:'#ff9800', label:'行'},
  'concept':  {color:'#00bcd4', label:'概'},
};

// 类型 → datafeed type 映射
function searchTypeToFeedType(type){
  return type || 'index';
}

// 类型 → selectBoard 所需 type
function searchTypeToBoardType(type, code){
  if(type === 'stock') return 'stock';
  if(type === 'hk_index') return 'hk_index';
  if(type === 'index') return 'index';
  // BK代码的板块
  if(code && code.startsWith('BK')) return type || 'concept';
  return type || 'concept';
}

const si=document.getElementById('search-input'), sr=document.getElementById('search-results');
let sIdx=-1, _searchTimer=null;
let _searchAbort = null;
let _searchRequestSeq = 0;
let _currentSeq = 0;
let _suppressSearchClickUntil = 0;
let _enterHandledAt = 0;

// 渲染搜索项 HTML
function renderSearchItem(item, i){
  const ts = _typeStyles[item.type] || {color:'#787b86', label:item.category||'其它'};
  const dc = item.display_code || item.code;
  const tags = Array.isArray(item.tags) ? item.tags.slice(0, 3).filter(Boolean) : [];
  const tagHtml = tags.length ? '<span class="search-mini-tags">' + tags.map(t => '<span class="search-mini-tag">#' + escHtml(String(t)) + '</span>').join('') + '</span>' : '';
  return '<div class="search-item" data-idx="'+i+'">'+
    '<span class="search-tag" style="display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 4px;border-radius:3px;font-size:11px;font-weight:bold;margin-right:6px;background:'+ts.color+';color:#ffffff;line-height:1;box-shadow:0 1px 2px rgba(0,0,0,0.2)">'+ts.label+'</span>'+
    '<span class="search-name">'+escHtml(item.name)+'</span>'+
    tagHtml+
    '<span class="search-code">'+escHtml(dc)+'</span>'+
    '<span class="search-pinyin" style="color:#434651;font-size:10px;margin-left:auto">'+escHtml(item.initials||'')+'</span>'+
    '</div>';
}

// 渲染搜索历史
function renderSearchHistory(){
  loadSearchHistory();
  if(!_searchHistory.length) return '';
  return '<div class="search-history-header">最近搜索</div>'+
    _searchHistory.slice(0,5).map((h,i) => {
      const ts = _typeStyles[h.type] || {color:'#787b86', label:h.category||'其它'};
      return '<div class="search-item search-history-item" data-history="'+i+'">'+
        '<span class="search-tag" style="background:'+ts.color+'20;color:'+ts.color+';border:1px solid '+ts.color+'40">'+ts.label+'</span>'+
        '<span class="search-name">'+escHtml(h.name)+'</span>'+
        '<span class="search-code">'+escHtml(h.display_code||h.code)+'</span>'+
        '<span style="color:#434651;font-size:10px;margin-left:auto">⌛</span></div>';
    }).join('');
}

function setSearchSelected(nextIdx){
  const items = sr.querySelectorAll('.search-item');
  items.forEach(item => item.classList.remove('selected'));
  if(!items.length){
    sIdx = -1;
    return;
  }
  sIdx = Math.max(0, Math.min(nextIdx, items.length - 1));
  items[sIdx].classList.add('selected');
  const item = items[sIdx];
  const t = item.offsetTop;
  const b = t + item.offsetHeight;
  if(b > sr.scrollTop + sr.clientHeight) sr.scrollTop = b - sr.clientHeight;
  else if(t < sr.scrollTop) sr.scrollTop = t;
}

function activateSelectedSearchItem(){
  const items = sr.querySelectorAll('.search-item');
  if(!sr.classList.contains('show') || items.length === 0) return false;
  const idx = sIdx >= 0 ? sIdx : 0;
  pickSearchElement(items[idx]);
  return true;
}

si.onfocus = function(){
  if(!si.value.trim()){
    // 显示搜索历史
    sr.innerHTML = renderSearchHistory();
    if(_searchHistory.length) {
      sr.classList.add('show');
      setSearchSelected(0);
    } else sr.classList.remove('show');
    hydrateSearchHistoryFromRemote().then(function(){
      if(si.value.trim()) return;
      sr.innerHTML = renderSearchHistory();
      if(_searchHistory.length){
        sr.classList.add('show');
        setSearchSelected(0);
      }
    });
  }
};

si.oninput = function(){
  const q = si.value.trim();

  // Every input change immediately retires the previous debounce/request and
  // its rendered matches.  In particular, history items must not remain
  // keyboard-selectable during the 150ms debounce window for a non-empty query.
  if(_searchTimer){ clearTimeout(_searchTimer); _searchTimer = null; }
  if(_searchAbort){ try{ _searchAbort.abort(); }catch(_){} _searchAbort = null; }
  const requestSeq = ++_searchRequestSeq;
  _currentSeq = requestSeq;
  window._sm = [];
  sIdx = -1;

  if(!q){
    _currentSeq = 0;
    sr.innerHTML = renderSearchHistory();
    if(_searchHistory.length) {
      sr.classList.add('show');
      setSearchSelected(0);
    } else {
      sr.classList.remove('show');
      sIdx = -1;
    }
    return;
  }

  // Replace any history/previous result items synchronously.  This placeholder
  // intentionally has no .search-item class, so Arrow/Enter are inert until
  // the query response has rendered indexed results.
  sr.innerHTML = '<div style="padding:10px;color:#666;font-size:14px;text-align:center">搜索中...</div>';
  sr.classList.add('show');

  // 防抖 150ms + Abort 旧请求
  _searchTimer = setTimeout(async () => {
    _searchTimer = null;
    if(requestSeq !== _searchRequestSeq || si.value.trim() !== q) return;

    _searchAbort = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const signal = _searchAbort ? _searchAbort.signal : undefined;

    try {
      const historyCodes = _searchHistory.map(h => h.code).join(',');
      const url = API+'/api/search?q='+encodeURIComponent(q)+
        (historyCodes ? '&history='+encodeURIComponent(historyCodes) : '');
      let resp = null;
      if(typeof window.apiFetch === 'function'){
        const res = await window.apiFetch(url, { signal: signal, timeout: 10000 });
        if(signal && signal.aborted) return;
        resp = res && res.data;
      } else {
        const r = await fetch(url, { signal: signal });
        if(signal && signal.aborted) return;
        try { resp = await r.json(); } catch(_){ resp = null; }
      }
      if(signal && signal.aborted) return;

      // Stale-response protection: ignore if not the latest request
      if(requestSeq !== _searchRequestSeq) return;

      const matches = (resp && resp.data) || (Array.isArray(resp) ? resp : []) || [];

      if(!matches.length){
        sr.innerHTML = '<div style="padding:10px;color:#434651;font-size:11px;text-align:center">无匹配结果</div>';
        sr.classList.add('show');
        sIdx = -1;
        window._sm = [];
        return;
      }

      sr.innerHTML = matches.map((m,i) => renderSearchItem(m,i)).join('');
      sr.classList.add('show');
      window._sm = matches;
      setSearchSelected(0);
    } catch(e) {
      // Ignore if stale or aborted
      if(e && e.name === 'AbortError') return;
      if(requestSeq !== _searchRequestSeq) return;
      console.warn('[搜索] 请求失败:', e);
      // Show failure message
      sr.innerHTML = '<div style="padding:10px;color:#dc3545;font-size:14px;text-align:center">搜索失败，请稍后重试</div>';
      sr.classList.add('show');
      _searchAbort = null;
      return;
    }
  }, 150);
};

function searchPick(i){
  const items = window._sm;
  if(!items || i < 0 || i >= items.length) return;
  const m = items[i];
  if(!m) return;
  
  // 记录搜索历史
  addSearchHistory(m.code, m.name, m.type, m.category, si.value);

  // 统一选择事件
  window.dispatchEvent(new CustomEvent('select-symbol', {
    detail: { code: m.code, name: m.name, type: m.type, source: 'bottom-search', trigger: 'enter' }
  }));

  sr.classList.remove('show');
  si.value = '';
}

// 点击历史项
function searchPickHistory(i){
  loadSearchHistory();
  const h = _searchHistory[i];
  if(!h) return;
  // 重新选择历史项也视为最近使用，确保顺序符合用户操作。
  try { _searchHistory = _getSearchHistoryStore().add(h); } catch(e) {}
  // 统一选择事件
  window.dispatchEvent(new CustomEvent('select-symbol', {
    detail: { code: h.code, name: h.name, type: h.type, source: 'search-history', trigger: 'click' }
  }));
  sr.classList.remove('show');
  si.value = '';
}

function pickSearchElement(item){
  if(!item) return;
  const idx = item.dataset.idx;
  const histIdx = item.dataset.history;
  if(idx !== undefined) searchPick(parseInt(idx, 10));
  else if(histIdx !== undefined) searchPickHistory(parseInt(histIdx, 10));
}

// 委托点击事件（搜索项和历史项）
sr.addEventListener('mousedown', function(e){
  const item = e.target.closest('.search-item');
  if(!item) return;
  e.preventDefault();
  _suppressSearchClickUntil = Date.now() + 250;
  pickSearchElement(item);
});

sr.addEventListener('click', function(e){
  const item = e.target.closest('.search-item');
  if(!item) return;
  e.preventDefault();
  if(Date.now() < _suppressSearchClickUntil) return;
  pickSearchElement(item);
});

si.onkeydown = function(e){
  const items = sr.querySelectorAll('.search-item');
  if(!sr.classList.contains('show') || items.length === 0) return;
  
  if(e.key === 'ArrowDown'){
    e.preventDefault();
    setSearchSelected(sIdx < 0 ? 0 : sIdx + 1);
  } else if(e.key === 'ArrowUp'){
    e.preventDefault();
    setSearchSelected(sIdx <= 0 ? 0 : sIdx - 1);
  } else if(e.key === 'Enter'){
    e.preventDefault();
    _enterHandledAt = Date.now();
    activateSelectedSearchItem();
  } else if(e.key === 'Escape'){
    sr.classList.remove('show');
    si.blur();
  }
};

si.onkeyup = function(e){
  if(e.key !== 'Enter') return;
  if(Date.now() - _enterHandledAt < 300) return;
  e.preventDefault();
  _enterHandledAt = Date.now();
  activateSelectedSearchItem();
};

document.addEventListener('click', function(e){
  if(!e.target.closest('#search-wrap')) sr.classList.remove('show');
});

// 初始化搜索历史
loadSearchHistory();
hydrateSearchHistoryFromRemote();
