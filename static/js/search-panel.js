// ===== 8. 增强搜索（个股+指数+板块，拼音匹配，搜索历史） =====

// 搜索历史（localStorage）
const _searchHistoryKey = 'board_app_search_history';
let _searchHistory = [];

function loadSearchHistory(){
  try{
    const s = localStorage.getItem(_searchHistoryKey);
    _searchHistory = s ? JSON.parse(s) : [];
  }catch(e){ _searchHistory=[]; }
}
function saveSearchHistory(){
  try{
    localStorage.setItem(_searchHistoryKey, JSON.stringify(_searchHistory.slice(0,30)));
  }catch(e){}
}
function addSearchHistory(code, name, type, category){
  _searchHistory = _searchHistory.filter(h => h.code !== code);
  _searchHistory.unshift({code, name, type, category, time: Date.now()});
  _searchHistory = _searchHistory.slice(0,30);
  saveSearchHistory();
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
    _searchHistory.slice(0,8).map((h,i) => {
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
  }
};

si.oninput = function(){
  const q = si.value.trim();
  if(!q){
    if(_searchAbort){ try{ _searchAbort.abort(); }catch(_){} _searchAbort = null; }
    _searchRequestSeq++;
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

  // 清除旧定时器
  if(_searchTimer) clearTimeout(_searchTimer);

  // 防抖 150ms + Abort 旧请求
  _searchTimer = setTimeout(async () => {
    // Abort previous request and increment sequence for stale-response protection
    if(_searchAbort){ try{ _searchAbort.abort(); }catch(_){} }
    _searchAbort = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const signal = _searchAbort ? _searchAbort.signal : undefined;

    // Increment sequence to identify stale responses
    _searchRequestSeq++;
    _currentSeq = _searchRequestSeq;

    // Show loading indicator
    sr.innerHTML = '<div style="padding:10px;color:#666;font-size:14px;text-align:center">搜索中...</div>';
    sr.classList.add('show');

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
      if(_currentSeq !== _searchRequestSeq) return;

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
      if(_currentSeq !== _searchRequestSeq) return;
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
  addSearchHistory(m.code, m.name, m.type, m.category);

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
