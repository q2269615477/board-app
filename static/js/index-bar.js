// ===== 5B. 鎸囨暟瀵艰埅鏍忥紙Plus鎸夐挳寮圭獥绠＄悊 + 鎼滅储娣诲姞 + 鎷栧姩鎺掑簭 + 绂荤嚎鎸佷箙鍖栵級 =====

if (typeof window.escHtml !== 'function') {
  window.escHtml = function(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  };
}
if (typeof window.escAttr !== 'function') {
  window.escAttr = function(s) {
    return (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  };
}

const DEFAULT_INDEX_ITEMS = [
  // A股
  { ticker: 'sh000001', type: 'index', name: '上证指数', price: '─', group: 'A股' },
  { ticker: 'sz399006', type: 'index', name: '创业板指', price: '─', group: 'A股' },
  { ticker: 'sh000688', type: 'index', name: '科创50', price: '─', group: 'A股' },
  { ticker: 'sh000300', type: 'index', name: '沪深300', price: '─', group: 'A股' },
  { ticker: 'sh000016', type: 'index', name: '上证50', price: '─', group: 'A股' },
  { ticker: 'sh000852', type: 'index', name: '中证1000', price: '─', group: 'A股' },
  { ticker: 'sh000853', type: 'index', name: '中证2000', price: '─', group: 'A股' },
  // 东财 & 宽基
  { ticker: '800000', type: 'index', name: '东方财富全A', price: '─', group: '东财' },
  { ticker: 'BK1158', type: 'concept', name: '微盘股', price: '─', group: '东财' },
  // 港股
  { ticker: 'HSI', type: 'hk_index', name: '恒生指数', price: '─', group: '港股' },
  { ticker: 'HSTECH', type: 'hk_index', name: '恒生科技', price: '─', group: '港股' },
  // 亚太
  { ticker: '^N225', type: 'us', name: '日经225', price: '─', group: '亚太' },
  { ticker: '^KS11', type: 'us', name: 'KOSPI', price: '─', group: '亚太' },
  { ticker: '^TWII', type: 'us', name: '台湾加权', price: '─', group: '亚太' },
  // 美股
  { ticker: 'SPX', type: 'us', name: '标普500', price: '─', group: '美股' },
  { ticker: 'IXIC', type: 'us', name: '纳斯达克', price: '─', group: '美股' },
  { ticker: 'DJI', type: 'us', name: '道琼斯', price: '─', group: '美股' }
];

var _currentIndexItems = [];
var _idxBarDragEl = null;
var _idxLastQuoteValues = new Map();

const INDEX_ITEM_CANONICAL = {
  sh000001: { type: 'index', name: '上证指数', group: 'A股' },
  sz399006: { type: 'index', name: '创业板指', group: 'A股' },
  sh000688: { type: 'index', name: '科创50', group: 'A股' },
  sh000300: { type: 'index', name: '沪深300', group: 'A股' },
  sh000016: { type: 'index', name: '上证50', group: 'A股' },
  sh000852: { type: 'index', name: '中证1000', group: 'A股' },
  sh000853: { type: 'index', name: '中证2000', group: 'A股' },
  800000: { type: 'index', name: '东方财富全A', group: '东财' },
  BK1158: { type: 'concept', name: '微盘股', group: '东财' },
  HSI: { type: 'hk_index', name: '恒生指数', group: '港股' },
  HSTECH: { type: 'hk_index', name: '恒生科技', group: '港股' },
  '^N225': { type: 'us', name: '日经225', group: '亚太' },
  '^KS11': { type: 'us', name: 'KOSPI', group: '亚太' },
  '^TWII': { type: 'us', name: '台湾加权', group: '亚太' },
  SPX: { type: 'us', name: '标普500', group: '美股' },
  IXIC: { type: 'us', name: '纳斯达克', group: '美股' },
  DJI: { type: 'us', name: '道琼斯', group: '美股' }
};

function normalizeIndexItem(it) {
  const canonical = INDEX_ITEM_CANONICAL[it && it.ticker];
  return canonical ? Object.assign({}, it, canonical) : it;
}

// 从 localStorage 恢复或读取默认配置
function loadSavedIndexItems() {
  try {
    const saved = localStorage.getItem('INDEX_BAR_SAVED_ITEMS');
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) {
        const valid = parsed.filter(it => it && it.ticker && it.name);
        if (valid.length > 0) {
          _currentIndexItems = valid.map(normalizeIndexItem);
          saveCurrentIndexItems();
          return;
        }
      }
    }
  } catch (e) {}
  _currentIndexItems = JSON.parse(JSON.stringify(DEFAULT_INDEX_ITEMS));
  saveCurrentIndexItems();
}

function saveCurrentIndexItems() {
  try {
    localStorage.setItem('INDEX_BAR_SAVED_ITEMS', JSON.stringify(_currentIndexItems));
  } catch (e) {
    console.warn('[IndexBar] save items failed', e);
  }
}

function renderIndexBar() {
  loadSavedIndexItems();
  const bar = document.getElementById('index-bar');
  if (!bar) return;

  // 按分组构建导航项
  const grouped = {};
  _currentIndexItems.forEach(it => {
    const g = it.group || '通用';
    if (!grouped[g]) grouped[g] = [];
    grouped[g].push(it);
  });

  let h = '';
  Object.keys(grouped).forEach(gName => {
    h += '<div class="idx-group" data-group="' + escAttr(gName) + '">';
    h += '<span class="idx-label">' + escHtml(gName) + '</span>';
    grouped[gName].forEach(it => {
      h += '<div class="idx-item" draggable="true" data-ticker="' + escAttr(it.ticker) + '" data-sym=\'' + escAttr(JSON.stringify(it)) + '\''
        + ' onclick="clickIdxItem(this)" oncontextmenu="ctxIdxItem(event,this)">'
        + '<span class="name">' + escHtml(it.name) + '</span>'
        + '<span class="price flat" id="prc_' + it.ticker + '">' + (it.price || '鈹€') + '</span>'
        + '</div>';
    });
    h += '</div>';
  });

  bar.innerHTML = h;

  const restoredCount = _currentIndexItems.filter(function(item) {
    return item.price && item.price !== '─';
  }).length;
  if (restoredCount) {
    bar.dataset.quoteState = 'ready';
    bar.dataset.quoteCount = String(restoredCount);
  }

  // 绑定拖动排序与滚轮横向滚动
  bindDragEvents(bar);
  bindIndexBarPan(bar);

  // 价格由实时监听统一启动，让图表/分类请求先完成。
}


function bindIndexBarPan(bar) {
  if (!bar || bar.__idx_pan_bound) return;
  bar.__idx_pan_bound = true;
  let isPanning = false;
  let startX = 0;
  let startScrollLeft = 0;

  bar.addEventListener('wheel', e => {
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    if (bar.scrollWidth <= bar.clientWidth) return;
    e.preventDefault();
    bar.scrollLeft += e.deltaY * 1.6;
  }, { passive: false });

  bar.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    if (e.target && e.target.closest && e.target.closest('.idx-item')) return;
    if (bar.scrollWidth <= bar.clientWidth) return;
    isPanning = true;
    startX = e.clientX;
    startScrollLeft = bar.scrollLeft;
    bar.classList.add('is-panning');
  });

  window.addEventListener('mousemove', e => {
    if (!isPanning) return;
    e.preventDefault();
    bar.scrollLeft = startScrollLeft - (e.clientX - startX);
  });

  window.addEventListener('mouseup', () => {
    if (!isPanning) return;
    isPanning = false;
    bar.classList.remove('is-panning');
  });
}

// 缁戝畾鎷栨嫿浜嬩欢 (宸﹀彸鎷栧姩璋冩暣椤哄簭)
function bindDragEvents(bar) {
  bar.querySelectorAll('.idx-item').forEach(it => {
    it.setAttribute('draggable', 'true');
    it.addEventListener('dragstart', e => {
      _idxBarDragEl = it;
      it.classList.add('is-dragging');
      try { e.dataTransfer.effectAllowed = 'move'; } catch (err) {}
      setTimeout(() => { if (it) it.style.opacity = '0.35'; }, 0);
    });
    it.addEventListener('dragend', () => {
      if (_idxBarDragEl) {
        _idxBarDragEl.style.opacity = '1';
        _idxBarDragEl.classList.remove('is-dragging');
      }
      _idxBarDragEl = null;
      syncItemsOrderFromDOM();
    });
    it.addEventListener('dragover', e => {
      e.preventDefault();
      if (!_idxBarDragEl || _idxBarDragEl === it) return;
      const r = it.getBoundingClientRect(), mid = r.left + r.width / 2;
      const p = it.parentNode;
      p.insertBefore(_idxBarDragEl, e.clientX < mid ? it : it.nextSibling);
    });
  });
}

// DOM 鎷栨嫿瀹屾垚鍚庨噸鏂板悓姝ラ『搴忓苟淇濆瓨
function syncItemsOrderFromDOM() {
  const bar = document.getElementById('index-bar');
  if (!bar) return;
  const newItems = [];
  bar.querySelectorAll('.idx-item').forEach(el => {
    try {
      const sym = JSON.parse(el.dataset.sym);
      newItems.push(sym);
    } catch (e) {}
  });
  if (newItems.length > 0) {
    _currentIndexItems = newItems;
    saveCurrentIndexItems();
  }
}

function clickIdxItem(el) {
  document.querySelectorAll('.idx-item.active').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  try {
    const sym = JSON.parse(el.dataset.sym);
    // 统一选择事件（不再直接调用 selectBoard）
    window.dispatchEvent(new CustomEvent('select-symbol', {
      detail: { code: sym.ticker, name: sym.name, type: sym.type, source: 'top-index-bar', trigger: 'click' }
    }));
  } catch (e) { console.warn('clickIdxItem error:', e); }
}

function ctxIdxItem(ev, el) {
  ev.preventDefault();
  try {
    const sym = JSON.parse(el.dataset.sym);
    window._ctxIdx = sym;
    showCtxMenu(ev.clientX, ev.clientY,
      '<div class="ctx-item" style="color:#8ab4ff;font-weight:bold;cursor:default">' + escHtml(sym.name) + ' (' + escHtml(sym.ticker) + ')</div>'
      + '<div class="ctx-sep"></div>'
      + '<div class="ctx-item" onclick="delIdxItemByTicker(null,\'' + escAttr(sym.ticker) + '\')">从导航栏移除</div>'
      + '<div class="ctx-item" onclick="closeCtx()">取消</div>');
  } catch (e) {}
}

// 鍒犻櫎鎸囧畾 ticker 鎸囨暟
function delIdxItemByTicker(ev, ticker) {
  if (ev) ev.stopPropagation();
  _currentIndexItems = _currentIndexItems.filter(it => it.ticker !== ticker);
  saveCurrentIndexItems();
  renderIndexBar();
  toast('已从导航栏移除');
  closeCtx();
  if (typeof renderIdxModalItems === 'function') renderIdxModalItems();
}

function _idxQuoteShouldFlash(code, priceKey, changeKey, marketOpen) {
  const previous = _idxLastQuoteValues.get(code);
  _idxLastQuoteValues.set(code, { price: priceKey, change: changeKey });
  if (!previous || marketOpen !== true) return false;
  return previous.price !== priceKey || previous.change !== changeKey;
}

// 搴旂敤鎸囨暟瀹炴椂浠锋牸 map锛堟寜鐓х敤鎴疯鍒欙細鍚嶇О鍦ㄥ墠锛屾定璺屽箙鍦ㄤ腑锛岀偣浣嶅湪鎷彿鍐呭湪鍚庯級
function applyIdxPrices(dataMap) {
  if (!dataMap) return;
  let pricesChanged = false;
  const safeFix = (num, d = 2) => (num != null && !isNaN(num)) ? Number(num).toFixed(d) : '0.00';
  Object.entries(dataMap).forEach(([code, v]) => {
    const el = document.getElementById('prc_' + code);
    if (!el || !v) return;
    const rawPrice = v.price != null ? v.price : (v.last != null ? v.last : (v.close != null ? v.close : 0));
    const priceNum = Number(rawPrice || 0);
    const priceStr = safeFix(priceNum, 2);
    const change = v.changePct != null ? v.changePct : (v.change_pct != null ? v.change_pct : 0);
    const chgNum = Number(change || 0);
    const chgSign = chgNum > 0 ? '+' : '';
    const chgStr = chgSign + safeFix(chgNum, 2) + '%';

    // 鏍煎紡锛氭定璺屽箙 (鐜颁环)
    const newContent = chgStr + ' (' + priceStr + ')';
    const oldContent = el.textContent;
    el.textContent = newContent;
    const colorClass = chgNum > 0 ? 'up' : (chgNum < 0 ? 'down' : 'flat');
    const flashClass = chgNum >= 0 ? 'flash-up' : 'flash-down';
    const shouldFlash = _idxQuoteShouldFlash(
      code,
      priceStr,
      safeFix(chgNum, 2),
      v.market_open === true
    );

    if (el.__idxFlashTimer) {
      clearTimeout(el.__idxFlashTimer);
      el.__idxFlashTimer = null;
    }
    el.className = 'price ' + colorClass + (shouldFlash ? ' ' + flashClass : '');
    if (shouldFlash) {
      el.__idxFlashTimer = setTimeout(() => {
        if (el) el.className = 'price ' + colorClass;
        el.__idxFlashTimer = null;
      }, 600);
    }

    // 同步到数组状态
    const itemObj = _currentIndexItems.find(it => it.ticker === code);
    if (itemObj) {
      itemObj.price = chgStr + ' (' + priceStr + ')';
      pricesChanged = pricesChanged || oldContent !== newContent;
    }

    const selected = window.store && window.store.selected;
    if (selected && selected.code === code && typeof window.updateChartHeaderChg === 'function') {
      setTimeout(() => window.updateChartHeaderChg(selected.name, selected.code, selected.type), 40);
    }
  });
  // 下次刷新立即展示上次成功快照，网络只负责更新而非首屏占位。
  if (pricesChanged) saveCurrentIndexItems();
}

// 鐩樹腑 10 绉掑钩婊戝埛鏂版満鍒讹紙娓╁拰棰戠巼锛岄槻姝?API 闄愭祦/椋庢帶寮曞彂鏁版嵁閫€鍖栵級
var _idxFallbackTimer = null;
var _idxRefreshPromise = null;
var _idxVisibilityBound = false;
var _idxRealtimeBound = false;
function _startIdxFallback(withInitialRefresh) {
  if (_idxFallbackTimer) return;
  if (withInitialRefresh !== false) setTimeout(refreshIdxPrices, 50);
  _idxFallbackTimer = setInterval(function() {
    if (!document.hidden) refreshIdxPrices();
  }, 10000);
  if (!_idxVisibilityBound) {
    _idxVisibilityBound = true;
    document.addEventListener('visibilitychange', function() {
      if (!document.hidden) refreshIdxPrices();
    });
  }
}
function _stopIdxFallback() {
  if (_idxFallbackTimer) { clearInterval(_idxFallbackTimer); _idxFallbackTimer = null; }
}

function _watchIdxRealtimeBus() {
  if (_idxRealtimeBound) return;
  _idxRealtimeBound = true;
  window.addEventListener('rt-indices', function(event) {
    const payload = event && event.detail;
    const data = payload && payload.data;
    if (data && typeof data === 'object') applyIdxPrices(data);
  });
  window.addEventListener('rt-status', function(event) {
    const connected = !!(event && event.detail && event.detail.connected);
    if (connected) {
      _stopIdxFallback();
    } else {
      refreshIdxPrices();
      _startIdxFallback(false);
    }
  });

  // 首次仍由 HTTP 建立可用快照；RealtimeBus 已连接时只取消后续兜底轮询。
  refreshIdxPrices();
  if (window.RealtimeBus && typeof window.RealtimeBus.isConnected === 'function' && window.RealtimeBus.isConnected()) {
    _stopIdxFallback();
  } else {
    _startIdxFallback(false);
  }
}

function refreshIdxPrices() {
  if (document.hidden) return Promise.resolve(null);
  if (window.boardPollingLeader && !window.boardPollingLeader.isLeader()) {
    return Promise.resolve(null);
  }
  if (_idxRefreshPromise) return _idxRefreshPromise;

  _idxRefreshPromise = (async function() {
    const bar = document.getElementById('index-bar');
    if (bar && !bar.dataset.quoteCount) bar.dataset.quoteState = 'loading';
    else if (bar) bar.dataset.quoteRefreshing = '1';
    const tickers = _currentIndexItems.map(it => it.ticker).filter(Boolean).join(',');
    const apiBase = (typeof API !== 'undefined' && API) ? API : '';
    const params = new URLSearchParams();
    if (tickers) params.set('tickers', tickers);
    const url = apiBase + '/api/spot/indices' + (tickers ? '?' + params.toString() : '');
    let j = null;

    if (typeof window.apiFetch === 'function') {
      const response = await window.apiFetch(url, {
        timeout: 7000,
        retries: 0,
        dedupeKey: 'top-index-quotes'
      });
      j = response && response.data;
    } else {
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      j = await r.json();
    }
    if (j && j.data) {
      applyIdxPrices(j.data);
      if (bar) {
        bar.dataset.quoteState = 'ready';
        bar.dataset.quoteCount = String(Object.keys(j.data).length);
      }
    } else if (bar) {
      bar.dataset.quoteState = 'empty';
    }
  })().catch(function(e) {
    const bar = document.getElementById('index-bar');
    if (bar && !bar.dataset.quoteCount) bar.dataset.quoteState = 'error';
    console.warn('[IndexBar] refresh prices failed', e);
  }).finally(function() {
    const bar = document.getElementById('index-bar');
    if (bar) delete bar.dataset.quoteRefreshing;
    _idxRefreshPromise = null;
  });

  return _idxRefreshPromise;
}

// ===== 5C. 鍔犲彿鎸夐挳涓庡脊绐楃鐞?鎼滅储娣诲姞 =====

function openIdxManageModal() {
  let modal = document.getElementById('idx-manage-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'idx-manage-modal';
    document.body.appendChild(modal);
  }

  modal.innerHTML = `
    <div class="idx-modal-hd">
      <span>鎸囨暟瀵艰埅鏍忕鐞?/span>
      <button class="idx-modal-close" onclick="closeIdxManageModal()">鉁?/button>
    </div>
    <div class="idx-modal-search-box">
      <input id="idx-modal-search-input" placeholder="杈撳叆浠ｇ爜鎴栧悕绉版悳绱㈡坊鍔?(濡?娌繁300 / 涓瘉鐧介厭 / 000300)..." oninput="onIdxModalSearchInput(this.value)">
    </div>
    <div id="idx-modal-search-results" class="idx-modal-results" style="display:none"></div>
    <div class="idx-modal-list-title">宸叉坊鍔犵殑鎸囨暟鏍囩殑 (榧犳爣鎸変綇鐩存帴鎷栧姩鍙寜浠绘剰椤哄簭鎺掑簭)锛?/div>
    <div id="idx-modal-items-grid" class="idx-modal-items-grid"></div>
  `;

  modal.classList.add('show');
  renderIdxModalItems();
  const input = document.getElementById('idx-modal-search-input');
  if (input) input.focus();
}

function closeIdxManageModal() {
  const modal = document.getElementById('idx-manage-modal');
  if (modal) modal.classList.remove('show');
}

function renderIdxModalItems() {
  const grid = document.getElementById('idx-modal-items-grid');
  if (!grid) return;
  let h = '';
  _currentIndexItems.forEach(it => {
    h += '<div class="idx-modal-chip" draggable="true" data-ticker="' + escAttr(it.ticker) + '" title="鎸変綇鎷栧姩鍙噸鎺掑簭">'
      + '<span class="drag-handle">鈮?/span> '
      + '<span>' + escHtml(it.name) + ' (' + escHtml(it.ticker) + ')</span>'
      + '<span class="chip-del" onclick="delIdxItemByTicker(event,\'' + escAttr(it.ticker) + '\')" title="鍒犻櫎">鉁?/span>'
      + '</div>';
  });
  grid.innerHTML = h || '<div style="color:#787b86;font-size:12px;padding:8px">鏆傛棤鎸囨暟鏍囩殑</div>';
  bindModalDragEvents(grid);
}

var _idxModalDragEl = null;
function bindModalDragEvents(grid) {
  grid.querySelectorAll('.idx-modal-chip').forEach(chip => {
    chip.addEventListener('dragstart', e => {
      _idxModalDragEl = chip;
      chip.style.opacity = '0.4';
      try { e.dataTransfer.effectAllowed = 'move'; } catch (err) {}
    });
    chip.addEventListener('dragend', () => {
      if (_idxModalDragEl) _idxModalDragEl.style.opacity = '1';
      _idxModalDragEl = null;
      syncItemsOrderFromModalGrid();
    });
    chip.addEventListener('dragover', e => {
      e.preventDefault();
      if (!_idxModalDragEl || _idxModalDragEl === chip) return;
      const r = chip.getBoundingClientRect();
      const mid = r.left + r.width / 2;
      grid.insertBefore(_idxModalDragEl, e.clientX < mid ? chip : chip.nextSibling);
    });
  });
}

function syncItemsOrderFromModalGrid() {
  const grid = document.getElementById('idx-modal-items-grid');
  if (!grid) return;
  const newOrderTickers = [];
  grid.querySelectorAll('.idx-modal-chip').forEach(el => {
    const t = el.dataset.ticker;
    if (t) newOrderTickers.push(t);
  });
  if (newOrderTickers.length > 0) {
    const itemMap = new Map(_currentIndexItems.map(it => [it.ticker, it]));
    const reordered = [];
    newOrderTickers.forEach(t => {
      if (itemMap.has(t)) reordered.push(itemMap.get(t));
    });
    _currentIndexItems.forEach(it => {
      if (!newOrderTickers.includes(it.ticker)) reordered.push(it);
    });
    _currentIndexItems = reordered;
    saveCurrentIndexItems();
    renderIndexBar();
  }
}

// 搜索框实时匹配
var _idxSearchDebounce = null;
function onIdxModalSearchInput(val) {
  clearTimeout(_idxSearchDebounce);
  _idxSearchDebounce = setTimeout(() => {
    performIdxSearch(val.trim());
  }, 200);
}

async function performIdxSearch(query) {
  const resContainer = document.getElementById('idx-modal-search-results');
  if (!resContainer) return;
  if (!query) {
    resContainer.style.display = 'none';
    resContainer.innerHTML = '';
    return;
  }

  // 1. 本地拼音/分类检索
  const allBoards = (store.allBoards || []);
  const lowerQ = query.toLowerCase();
  let matches = allBoards.filter(b => 
    b.name.toLowerCase().includes(lowerQ) || 
    b.code.toLowerCase().includes(lowerQ) ||
    (b.pinyin && b.pinyin.toLowerCase().includes(lowerQ))
  ).slice(0, 8);

  // 2. 鑻ユ湰鍦版棤鍖归厤锛岃皟鍚庣 API 鎼滅储
  if (matches.length === 0) {
    try {
      const r = await fetch(API + '/api/search?q=' + encodeURIComponent(query));
      const j = await r.json();
      if (j && Array.isArray(j.data)) {
        matches = j.data.slice(0, 8);
      }
    } catch (e) {}
  }

  if (matches.length === 0) {
    resContainer.style.display = 'block';
    resContainer.innerHTML = '<div style="padding:8px 12px;color:#787b86;font-size:12px">未找到匹配标的</div>';
    return;
  }

  resContainer.style.display = 'block';
  let h = '';
  matches.forEach(m => {
    const isAdded = _currentIndexItems.some(it => it.ticker === m.code);
    h += '<div class="idx-search-res-item">'
      + '<div><b>' + escHtml(m.name) + '</b> <span style="color:#787b86;margin-left:4px">' + escHtml(m.code) + '</span></div>'
      + (isAdded ? '<span style="color:#787b86;font-size:11px">已添加</span>' :
        '<button class="idx-add-act-btn" onclick="addIdxItemFromSearch(\'' + escAttr(m.name) + '\',\'' + escAttr(m.code) + '\',\'' + escAttr(m.type || 'index') + '\')">+ 添加</button>')
      + '</div>';
  });
  resContainer.innerHTML = h;
}

// 鎼滅储缁撴灉鐩存帴娣诲姞鍒板鑸爮
function addIdxItemFromSearch(name, code, type) {
  if (_currentIndexItems.some(it => it.ticker === code)) {
    toast('已在导航栏中');
    return;
  }
  const newItem = { ticker: code, type: type || 'index', name: name, price: '─', group: '自定义' };
  _currentIndexItems.push(newItem);
  saveCurrentIndexItems();
  renderIndexBar();
  renderIdxModalItems();
  toast('已添加 ' + name + ' 到导航栏');
  const input = document.getElementById('idx-modal-search-input');
  if (input) performIdxSearch(input.value.trim());
}

// 脚本加载后初始化指数栏与实时行情监听
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    renderIndexBar();
    _watchIdxRealtimeBus();
  });
} else {
  renderIndexBar();
  _watchIdxRealtimeBus();
}

