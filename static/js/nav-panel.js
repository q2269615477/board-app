// ===== 1. 分类（支持持久化+拼音排序） =====
async function loadClassification() {
  const p=document.getElementById('nav-panel');
  let retryCount = 0;
  const maxAttempts = 3;
  const retryDelayMs = 3000;

  async function attemptLoad() {
    try {
      const r=await fetch(API+'/api/classification/load');
      const doc = await r.json();
      store.classificationDoc = doc;
      store.categoryData = doc.categories;
      store.allBoards = [];
      (store.categoryData || []).forEach(c => {
        (c.subcategories || []).forEach(sub => (sub.boards || []).forEach(b => store.allBoards.push(b)));
        (c.boards || []).forEach(b => store.allBoards.push(b));
      });
      renderNav();
      refreshAnnCounts();   // 拉取「已画几条支撑位」用于左栏进度标记
    } catch(e){
      console.warn('分类加载失败', e);
      retryCount++;
      if (retryCount < maxAttempts) {
        console.log('Retrying loadClassification (attempt ' + retryCount + '/' + maxAttempts + ') in ' + retryDelayMs + 'ms');
        setTimeout(attemptLoad, retryDelayMs);
      } else {
        store.classificationDoc=null;
        store.categoryData=[];
        p.innerHTML='<div class="nav-header" style="color:#ef5350">加载失败</div><div class="nav-item" onclick="loadClassification()">点此重试</div>';
      }
    }
  }

  return attemptLoad();
}

// ===== 增强：标注进度 / 自选 / 最近访问 =====
var _annCounts = {};        // {symbol: 已画支撑位数}  ← 教学进度一眼可见
var _favBoards = null;      // 自选集合（localStorage 持久化）
var _recentBoards = null;   // 最近访问（localStorage 持久化，上限8）

function _loadFav() {
  if (_favBoards) return _favBoards;
  try { _favBoards = new Set(JSON.parse(localStorage.getItem('fav_boards') || '[]')); }
  catch (e) { _favBoards = new Set(); }
  return _favBoards;
}
function _saveFav() {
  try { localStorage.setItem('fav_boards', JSON.stringify(Array.from(_loadFav()))); } catch (e) {}
}
function toggleFavBoard(code, name, type, ev) {
  if (ev) ev.stopPropagation();
  var f = _loadFav();
  if (f.has(code)) { f.delete(code); toast('已取消自选 ' + name); }
  else { f.add(code); toast('已加入自选 ' + name); }
  _saveFav(); renderNav(); closeCtx();
}
function _loadRecent() {
  if (_recentBoards) return _recentBoards;
  try { _recentBoards = JSON.parse(localStorage.getItem('recent_boards') || '[]'); }
  catch (e) { _recentBoards = []; }
  return _recentBoards;
}
function _pushRecent(code, name, type) {
  var r = _loadRecent().filter(function (x) { return x.code !== code; });
  r.unshift({ code: code, name: name, type: type });
  _recentBoards = r.slice(0, 8);
  try { localStorage.setItem('recent_boards', JSON.stringify(_recentBoards)); } catch (e) {}
  // 就地更新这一条，避免为了刷新最近访问而重建 1100+ 项的整棵树
  try {
    var bar = document.getElementById('nav-recent-bar');
    if (bar) {
      var tmp = document.createElement('div');
      tmp.innerHTML = renderRecentBar();
      var fresh = tmp.firstElementChild;
      if (fresh) bar.replaceWith(fresh); else bar.remove();
    } else {
      var tabs = document.querySelector('.nav-tabs-bar');
      if (tabs) {
        var t2 = document.createElement('div');
        t2.innerHTML = renderRecentBar();
        if (t2.firstElementChild) tabs.insertAdjacentElement('afterend', t2.firstElementChild);
      }
    }
  } catch (e) {}
}
// 拉取各标的已画支撑位数量（失败静默，不影响导航）
async function refreshAnnCounts() {
  try {
    var r = await fetch(API + '/api/annotations/counts');
    var j = await r.json();
    _annCounts = (j && j.data) || {};
    renderNav();
  } catch (e) {}
}
// 右键「加入共振组」：直接写共振草稿，免去切标的再手动添加
function addBoardToResonance(code, name, type, ev) {
  if (ev) ev.stopPropagation();
  var g;
  try { g = JSON.parse(localStorage.getItem('resonance_draft') || 'null'); } catch (e) {}
  if (!g) g = { theme: '', threshold_pct: 3, min_aligned: 2,
                periods: ['daily', 'weekly', 'monthly'], require_periods: [], members: [] };
  if (!Array.isArray(g.members)) g.members = [];
  var period = (window.__board_ctx && window.__board_ctx.period) || 'daily';
  if (g.members.some(function (m) { return m.symbol === code && m.period === period; })) {
    toast('该标的已在共振组中');
  } else {
    g.members.push({ symbol: code, symbol_name: name, asset_type: type, period: period, role: '' });
    try { localStorage.setItem('resonance_draft', JSON.stringify(g)); } catch (e) {}
    toast('已加入共振组：' + name + '（共 ' + g.members.length + ' 项）');
  }
  closeCtx();
}

// 标的类型 Tag 勋章提示 (行/指/概/个)
function _typeTag(type, code) {
  let label = '个';
  let color = '#26a69a';
  if (type === 'industry') { label = '行'; color = '#ff9800'; }
  else if (type === 'concept') { label = '概'; color = '#00bcd4'; }
  else if (type === 'index' || type === 'hk_index' || (code && (code.startsWith('sh') || code.startsWith('sz399') || code.startsWith('BK1158')))) { label = '指'; color = '#ab47bc'; }
  else { label = '个'; color = '#26a69a'; }
  
  return '<span class="nav-type-tag" style="display:inline-flex;align-items:center;justify-content:center;min-width:16px;height:16px;padding:0 3px;border-radius:3px;font-size:10px;font-weight:bold;margin-right:5px;background:'+color+';color:#ffffff;line-height:1;vertical-align:middle;box-shadow:0 1px 2px rgba(0,0,0,0.3)">'+label+'</span>';
}

// 获取板块拼音排序键：取每个字的拼音首字母拼接
function _boardSortKey(name){
  if(!_pinyinData) return name;
  const found = window._sm ? window._sm.find(s => s.name === name) : null;
  if(found && found.initials) return found.initials;
  for(const info of Object.values(_pinyinData)){
    if(info.name===name){
      return (info.initials||[]).join('');
    }
  }
  return name;
}

// ===== 1. 三级树形导航 (1级大类 -> 2级子类 -> 3级板块) =====
let _navSearchQuery = '';
let _navFilterType = 'all';
let _navActiveTags = new Set();

function filterNavTree(q) {
  _navSearchQuery = (q || '').trim().toLowerCase();
  renderNav();
}

function setNavFilter(type) {
  _navFilterType = type;
  renderNav();
}

function getBoardTags(board) {
  return (board && Array.isArray(board.tags) ? board.tags : [])
    .map(t => String(t).trim())
    .filter(Boolean);
}

function boardMatchesSearch(board, q) {
  const query = (q || '').trim().toLowerCase();
  if (!query) return true;
  const tags = getBoardTags(board);
  if (query.startsWith('#')) {
    const tagQuery = query.slice(1).trim();
    return !!tagQuery && tags.some(t => t.toLowerCase() === tagQuery);
  }
  const n = (board.name || '').toLowerCase();
  const c = (board.code || '').toLowerCase();
  const sk = _boardSortKey(board.name || '').toLowerCase();
  const py = getStrPinyinInitials(board.name || '').toLowerCase();
  const primary = (board.primary_category || '').toLowerCase();
  const secondary = (board.secondary_category || '').toLowerCase();
  return n.includes(query) || c.includes(query) || sk.includes(query) || py.includes(query) ||
    primary.includes(query) || secondary.includes(query) ||
    tags.some(t => t.toLowerCase().includes(query));
}

function boardMatchesTags(board) {
  if (!_navActiveTags.size) return true;
  const tags = getBoardTags(board).map(t => t.toLowerCase());
  return Array.from(_navActiveTags).every(t => tags.includes(t.toLowerCase()));
}

function toggleNavTag(tag, event) {
  if (event) event.stopPropagation();
  if (!tag) return;
  if (_navActiveTags.has(tag)) _navActiveTags.delete(tag);
  else _navActiveTags.add(tag);
  renderNav();
}

function clearNavTags(event) {
  if (event) event.stopPropagation();
  _navActiveTags.clear();
  renderNav();
}

function renderBoardTagChips(board) {
  return '';
}

function getHotTags(limit) {
  if (limit == null) limit = 12;
  const counts = new Map();
  const cats = store.categoryData || [];
  for (const cat of cats) {
    for (const sub of (cat.subcategories || [])) {
      for (const b of (sub.boards || [])) {
        const tags = getBoardTags(b);
        for (const t of tags) counts.set(t, (counts.get(t) || 0) + 1);
      }
    }
    for (const b of (cat.boards || [])) {
      const tags = getBoardTags(b);
      for (const t of tags) counts.set(t, (counts.get(t) || 0) + 1);
    }
  }
  const exclude = new Set(['行业', '概念']);
  const sorted = Array.from(counts.entries())
    .filter(([tag]) => !exclude.has(tag))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return sorted.slice(0, limit).map(([tag, count]) => ({ tag, count }));
}

// 最近访问的标的（本机持久化，点击直达）
function renderRecentBar() {
  var r = _loadRecent();
  if (!r.length) return '';
  var h = '<div class="nav-recent-bar" id="nav-recent-bar"><span class="nav-recent-label">最近</span>';
  r.forEach(function (x) {
    var args = [x.name, x.code, x.type].map(function (v) { return "'" + escAttr(v) + "'"; }).join(',');
    h += '<button type="button" class="nav-recent-item" title="' + escAttr(x.code) +
      '" onclick="selectBoard(' + args + ',event)">' + escHtml(x.name) + '</button>';
  });
  return h + '</div>';
}

function renderHotTagsBar() {
  const hot = getHotTags(12);
  if (!hot.length) return '';
  let h = '<div class="nav-hot-tags" id="nav-hot-tags">';
  hot.forEach(({ tag, count }) => {
    h += '<button type="button" class="nav-hot-tag' + (_navActiveTags.has(tag) ? ' active' : '') + '" onclick="toggleNavTag(\'' + escAttr(tag) + '\',event)">' + escHtml(tag) + ' <span style="color:#434651">' + count + '</span></button>';
  });
  h += '</div>';
  return h;
}

// 板块类型徽标：行(蓝)/概(橙)/指(紫)。CSS 类早已存在(app.css .board-item-type-tag)，
// 但渲染时没输出这个 span，导致三类板块在左栏无法一眼区分。
// 板块名后的附加标记：●N=已画支撑位数（教学进度）；★=自选
function _boardExtras(b) {
  var h = '';
  var n = _annCounts[b.code];
  if (n) h += '<span class="board-ann" title="已画 ' + n + ' 条支撑/阻力位">●' + n + '</span>';
  if (_loadFav().has(b.code)) h += '<span class="board-fav" title="自选">★</span>';
  return h;
}

function _typeTag(t) {
  var m = {
    industry: ['type-i', '行'],
    concept: ['type-c', '概'],
    index: ['type-idx', '指'],
    hk_index: ['type-idx', '港']
  };
  var v = m[t];
  if (!v) return '';
  return '<span class="board-item-type-tag ' + v[0] + '">' + v[1] + '</span>';
}

function renderNav() {
  const p = document.getElementById('nav-panel');
  if (!store.categoryData) {
    p.innerHTML = '<div class="nav-header">暂无数据</div><div class="nav-item" onclick="loadClassification()">点击重试</div>';
    return;
  }
  
  let totalBoards = 0;
  store.categoryData.forEach(c => {
    if (c.subcategories && c.subcategories.length > 0) {
      c.subcategories.forEach(s => { totalBoards += (s.boards || []).length; });
    }
    if (c.boards) {
      totalBoards += c.boards.length;
    }
  });

  let h = '';
  // 顶部卡片搜索框
  h += '<div class="nav-header-box">';
  h += '  <div class="nav-header-title"><span>产业链板块 (' + totalBoards + '个)</span></div>';
  h += '  <input id="nav-search-input" class="nav-search-input" value="' + escAttr(_navSearchQuery) + '" placeholder="🔍 搜板块 / 拼音 / 代码 / tag / #tag" oninput="filterNavTree(this.value)">';
  h += '</div>';

  // 快捷分类切换 Tabs
  h += '<div class="nav-tabs-bar">';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'all' ? 'active' : '') + '" onclick="setNavFilter(\'all\')">全量</button>';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'industry' ? 'active' : '') + '" onclick="setNavFilter(\'industry\')">行业[行]</button>';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'concept' ? 'active' : '') + '" onclick="setNavFilter(\'concept\')">概念[概]</button>';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'index' ? 'active' : '') + '" onclick="setNavFilter(\'index\')">指数[指]</button>';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'annotated' ? 'active' : '') + '" title="只看我已画过支撑位的标的（教学进度）" onclick="setNavFilter(\'annotated\')">已标注●</button>';
  h += '  <button class="nav-tab-btn ' + (_navFilterType === 'fav' ? 'active' : '') + '" title="自选（右键板块可加入）" onclick="setNavFilter(\'fav\')">自选★</button>';
  h += '</div>';

  if (_navActiveTags.size) {
    h += '<div class="nav-active-tags">';
    Array.from(_navActiveTags).forEach(tag => {
      h += '<button type="button" class="nav-active-tag" onclick="toggleNavTag(\'' + escAttr(tag) + '\',event)">#' + escHtml(tag) + ' ×</button>';
    });
    h += '<button type="button" class="nav-tags-clear" onclick="clearNavTags(event)">清除</button>';
    h += '</div>';
  }

  h += renderRecentBar();
  h += renderHotTagsBar();

  h += '<div id="nav-tree-scroll" style="flex:1; overflow-y:auto;">';

  store.categoryData.forEach(cat => {
    const catId = 'cat_' + cat.name.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_');
    const isCatExpanded = store._expandedCats ? (store._expandedCats[cat.name] !== false) : true;
    
    let catBoardCount = 0;
    let subHtml = '';

    if (cat.subcategories && cat.subcategories.length > 0) {
      cat.subcategories.forEach((sub, sidx) => {
        const subId = catId + '_sub_' + sidx;
        const isSubExpanded = store._expandedCats ? (store._expandedCats[subId] !== false) : true;

        let boards = sub.boards || [];
        if (_navFilterType === 'industry') boards = boards.filter(b => b.type === 'industry');
        if (_navFilterType === 'concept') boards = boards.filter(b => b.type === 'concept');
        if (_navFilterType === 'index') boards = boards.filter(b => b.type === 'index' || b.type === 'hk_index');
        if (_navFilterType === 'fav') boards = boards.filter(b => _loadFav().has(b.code));
        if (_navFilterType === 'annotated') boards = boards.filter(b => !!_annCounts[b.code]);

        boards = boards.filter(b => boardMatchesSearch(b, _navSearchQuery) && boardMatchesTags(b));

        if (boards.length === 0 && (_navSearchQuery || _navActiveTags.size || _navFilterType !== 'all')) return;

        catBoardCount += boards.length;

        const subSortState = (store._sortStates && store._sortStates[subId]) || null;
        const subSortIcon = subSortState === 'asc' ? '↑ 低→高' : subSortState === 'desc' ? '↓ 高→低' : '↕ 排名';
        const subSortCls = subSortState ? 'active ' + subSortState : '';

        subHtml += '<div class="sub-cat-group" data-orig-idx="' + sidx + '">';
        subHtml += '<div class="sub-cat-item" onclick="toggleSubCat(\'' + subId + '\', event)">';
        subHtml += '  <span><span class="sub-arrow">' + (isSubExpanded ? '▼' : '▶') + '</span> ' + escHtml(sub.name) + '</span>';
        subHtml += '  <div class="sub-hdr-right">';
        subHtml += '    <button type="button" class="nav-sort-btn ' + subSortCls + '" title="按涨幅排名（点击: 低→高 / 高→低 / 默认）" onclick="toggleSubSort(\'' + subId + '\', event)">' + subSortIcon + '</button>';
        subHtml += '    <span class="sub-count">' + boards.length + '</span>';
        subHtml += '  </div>';
        subHtml += '</div>';

        subHtml += '<div id="' + subId + '" class="sub-boards-wrap' + (isSubExpanded ? '' : ' collapsed') + '">';
        boards.forEach((b, bidx) => {
          subHtml += '<div class="board-item" data-orig-idx="' + bidx + '" data-code="' + escAttr(b.code) + '" data-type="' + escAttr(b.type) + '" data-name="' + escAttr(b.name) + '" onclick="selectBoard(\'' + escAttr(b.name) + '\',\'' + escAttr(b.code) + '\',\'' + escAttr(b.type) + '\',event)" oncontextmenu="showBoardCtx(event,\'' + escAttr(b.name) + '\',\'' + escAttr(b.code) + '\',\'' + escAttr(b.type) + '\')">';
          subHtml += '  <span class="board-main">' + _typeTag(b.type, b.code) + '<span class="board-name">' + escHtml(b.name) + '</span>' + _boardExtras(b) + renderBoardTagChips(b) + '</span>';
          subHtml += '  <span class="board-chg" data-bchg="' + escAttr(b.code) + '">--</span>';
          subHtml += '</div>';
        });
        subHtml += '</div>';
        subHtml += '</div>';
      });
      if (cat.boards) {
        // 旧版数据备用支持（兼容混合结构）
        let boards = cat.boards || [];
        if (_navFilterType === 'industry') boards = boards.filter(b => b.type === 'industry');
        if (_navFilterType === 'concept') boards = boards.filter(b => b.type === 'concept');
        if (_navFilterType === 'index') boards = boards.filter(b => b.type === 'index' || b.type === 'hk_index');
        if (_navFilterType === 'fav') boards = boards.filter(b => _loadFav().has(b.code));
        if (_navFilterType === 'annotated') boards = boards.filter(b => !!_annCounts[b.code]);
        boards = boards.filter(b => boardMatchesSearch(b, _navSearchQuery) && boardMatchesTags(b));
        catBoardCount += boards.length;
        boards.forEach((b, bidx) => {
          subHtml += '<div class="board-item" data-orig-idx="' + (1000 + bidx) + '" data-code="' + escAttr(b.code) + '" data-type="' + escAttr(b.type) + '" data-name="' + escAttr(b.name) + '" onclick="selectBoard(\'' + escAttr(b.name) + '\',\'' + escAttr(b.code) + '\',\'' + escAttr(b.type) + '\',event)" oncontextmenu="showBoardCtx(event,\'' + escAttr(b.name) + '\',\'' + escAttr(b.code) + '\',\'' + escAttr(b.type) + '\')">';
          subHtml += '  <span class="board-main">' + _typeTag(b.type, b.code) + '<span class="board-name">' + escHtml(b.name) + '</span>' + _boardExtras(b) + renderBoardTagChips(b) + '</span>';
          subHtml += '  <span class="board-chg" data-bchg="' + escAttr(b.code) + '">--</span>';
          subHtml += '</div>';
        });
      }
    }

    if (catBoardCount === 0 && (_navSearchQuery || _navActiveTags.size || _navFilterType !== 'all')) return;

    const catArrowIcon = isCatExpanded ? '▼' : '▶';
    const catSortState = (store._sortStates && store._sortStates[catId]) || null;
    const catSortIcon = catSortState === 'asc' ? '↑ 低→高' : catSortState === 'desc' ? '↓ 高→低' : '↕ 排名';
    const catSortCls = catSortState ? 'active ' + catSortState : '';

    h += '<div class="cat-item ' + (store.activeCat === cat.name ? 'active' : '') + '" onclick="toggleCat(\'' + catId + '\',\'' + escAttr(cat.name) + '\')">';
    h += '  <span class="arrow">' + catArrowIcon + '</span> ' + escHtml(cat.name);
    h += '  <div class="cat-hdr-right">';
    h += '    <button type="button" class="nav-sort-btn ' + catSortCls + '" title="按涨幅排名（点击: 低→高 / 高→低 / 默认）" onclick="toggleCatSort(\'' + catId + '\', event)">' + catSortIcon + '</button>';
    h += '    <span class="count">' + catBoardCount + '</span>';
    h += '  </div>';
    h += '</div>';
    h += '<div id="' + catId + '" class="cat-content' + (isCatExpanded ? '' : ' collapsed') + '">';
    h += subHtml;
    h += '</div>';
  });

  h += '</div>';

  // 搜索输入框本身也在这段 HTML 里，innerHTML 重建会销毁它 →
  // 每敲一个字就失焦，实测一次只能输入一个字符。这里先记录焦点/光标/滚动，
  // 重建后立即还原。
  var _act = document.activeElement;
  var _wasSearch = !!(_act && _act.id === 'nav-search-input');
  var _caretStart = _wasSearch ? _act.selectionStart : null;
  var _caretEnd = _wasSearch ? _act.selectionEnd : null;
  var _oldScroll = document.getElementById('nav-tree-scroll');
  var _scrollTop = _oldScroll ? _oldScroll.scrollTop : 0;

  p.innerHTML = h;

  if (_wasSearch) {
    var _si = document.getElementById('nav-search-input');
    if (_si) {
      _si.focus();
      try { _si.setSelectionRange(_caretStart, _caretEnd); } catch (e) {}
    }
  }
  var _newScroll = document.getElementById('nav-tree-scroll');
  if (_newScroll && _scrollTop) _newScroll.scrollTop = _scrollTop;

  // 确保折叠面板按钮存在
  if (!document.getElementById('nav-panel-toggle')) {
    var tgl = document.createElement('button');
    tgl.id = 'nav-panel-toggle';
    tgl.title = '折叠板块导航';
    tgl.textContent = '\u25C0';
    tgl.onclick = function() { toggleNavPanel(); };
    p.appendChild(tgl);
  }
}

function toggleCat(id, name) {
  const el = document.getElementById(id); if (!el) return;
  const arrow = el.previousElementSibling.querySelector('.arrow');
  el.classList.toggle('collapsed');
  const isExpanded = !el.classList.contains('collapsed');
  if (arrow) arrow.textContent = isExpanded ? '▼' : '▶';
  if (!store._expandedCats) store._expandedCats = {};
  store._expandedCats[name] = isExpanded;
  if (isExpanded) {
    store.activeCat = name;
    document.querySelectorAll('.cat-item.active').forEach(e => e.classList.remove('active'));
    el.previousElementSibling.classList.add('active');
  } else {
    store.activeCat = null;
  }
}

function toggleSubCat(subId, e) {
  if (e) e.stopPropagation();
  const el = document.getElementById(subId);
  if (!el) return;
  const arrow = el.previousElementSibling.querySelector('.sub-arrow');
  el.classList.toggle('collapsed');
  const isExpanded = !el.classList.contains('collapsed');
  if (arrow) arrow.textContent = isExpanded ? '▼' : '▶';
  if (!store._expandedCats) store._expandedCats = {};
  store._expandedCats[subId] = isExpanded;
}

// ===== 2. 右键菜单：板块分类 =====
let _ctxBoard=null;
function showBoardCtx(e,name,code,type){
  e.preventDefault();
  _ctxBoard={name,code,type};
  const cats=store.categoryData.filter(c=>c.name!=='其他');
  let h='<div class="ctx-item" style="color:#434651;cursor:default">移到二级分类:</div>';
  cats.forEach(c=>{
    (c.subcategories||[]).forEach(s=>{
      h+='<div class="ctx-item" onclick="moveBoardToSubCat(\''+escAttr(c.name)+'\',\''+escAttr(s.name)+'\')">'+escHtml(c.name)+' / '+escHtml(s.name)+'</div>';
    });
  });
  h+='<div class="ctx-sep"></div>';
  // 直接从左栏把标的丢进共振组 / 加自选，省去「切标的→开面板→加当前」三步
  const _a = "'"+escAttr(code)+"','"+escAttr(name)+"','"+escAttr(type)+"'";
  h+='<div class="ctx-item" onclick="addBoardToResonance('+_a+',event)">⚡ 加入共振组</div>';
  h+='<div class="ctx-item" onclick="toggleFavBoard('+_a+',event)">'+(_loadFav().has(code)?'☆ 取消自选':'★ 加入自选')+'</div>';
  h+='<div class="ctx-sep"></div>';
  h+='<div class="ctx-item" onclick="closeCtx();addCurrentToIndexBar()">添加到指数导航栏</div>';
  h+='<div class="ctx-sep"></div>';
  h+='<div class="ctx-item" onclick="closeCtx()">取消</div>';
  showCtxMenu(e.clientX,e.clientY,h);
}
// ===== 板块 tags 推导（移动分类后重建 tags） =====
const _PRIMARY_SHORT_TAG = {
  'AI 与数字科技': 'AI科技',
  '智能终端与电子制造': '电子制造',
  '先进制造与军工装备': '先进制造',
  '新能源与电力设备': '新能源',
  '周期资源与材料': '周期资源',
  '医药生物与健康': '医药健康',
  '消费与服务': '消费服务',
  '金融地产与基建': '金融地产',
  '公用事业与交通运输': '公用交通',
  '农业与乡村振兴': '农业',
  '主题、风格与事件': '主题风格',
};
const _SECONDARY_SHORT_TAG = {
  'AI 模型与应用': 'AI应用', '算力基础设施': '算力', '芯片半导体': '半导体',
  '数据与安全': '数据安全', '软件与IT服务': '软件服务', '通信网络': '通信',
  '消费电子终端': '消费电子', '显示与光学': '显示光学', '安防与传感': '安防传感',
  'PCB 与电子元件': 'PCB元件', '智能硬件生态': '智能硬件',
  '创新药与 CXO': '创新药', '细胞治疗与前沿生物': '前沿生物',
  '中药与化学制药': '化学制药', '医疗器械与诊断': '医疗器械',
  '消费医疗与医美': '医美健康', '医疗服务与大健康': '大健康',
  '光伏产业链': '光伏', '新能源汽车': '新能源车', '储能与电池': '储能电池',
  '电池材料': '电池材料', '汽车零部件与电动化': '汽车零部件',
  '风电氢能核电': '风电氢能', '食品加工与调味品': '食品加工',
  '酒类与饮料': '酒类饮料', '化妆品与饰品': '美妆饰品', '纺织服装': '纺织',
  '包装造纸轻工': '包装造纸', '家电家居': '家电', '电商与新零售': '电商零售',
  '商贸零售': '商贸零售', '社会服务': '社会服务', '旅游酒店餐饮': '旅游餐饮',
  '游戏影视': '游戏影视', '体育与文娱': '体育文娱', '传媒出版广告': '传媒广告',
  '教育服务': '教育', '低空与无人系统': '低空经济', '机器人与自动化': '机器人',
  '军工航天': '军工', '工业母机与通用设备': '工业母机', '船舶与海工': '船舶海工',
  '工程机械与重装': '工程机械', '交通物流': '交通物流', '电力运营': '电力运营',
  '环保水务': '环保水务', '电网设备': '电网设备', '钢铁建材': '钢铁建材',
  '贵金属与有色': '有色贵金属', '稀土小金属与能源金属': '稀土小金属',
  '化纤塑料与新材料': '化纤材料', '基础化工': '基础化工', '煤炭油气': '煤炭油气',
  '风格因子': '风格因子', '特殊标签': '特殊标签', '指数成分股': '指数成分',
  '短期热点': '短期热点', '平台与大厂映射': '大厂映射', '资金偏好': '资金偏好',
  '改革与资本运作': '资本运作', '区域主题': '区域主题', '财报事件': '财报事件',
  '养殖与饲料': '养殖饲料', '种植与种业': '种植种业', '农产品加工': '农产品加工',
  '银行': '银行', '券商与非银': '券商非银', '金融科技': '金融科技',
  '房地产': '房地产', '建筑基建': '建筑基建',
};
const _DOMAIN_TAGS = {
  'AI 模型与应用': ['AI', 'AIGC'], '算力基础设施': ['算力', '数据中心'],
  '芯片半导体': ['半导体', '芯片'], '数据与安全': ['数据', '网络安全'],
  '软件与IT服务': ['软件', 'IT服务'], '通信网络': ['通信', '5G'],
  '消费电子终端': ['消费电子', '智能终端'], '显示与光学': ['显示', '光学'],
  '安防与传感': ['安防', '传感器'], 'PCB 与电子元件': ['PCB', '电子元件'],
  '智能硬件生态': ['智能硬件', '消费电子'], '创新药与 CXO': ['创新药', 'CXO'],
  '细胞治疗与前沿生物': ['细胞治疗', '生物制药'], '中药与化学制药': ['制药', '中药'],
  '医疗器械与诊断': ['医疗器械', '体外诊断'], '消费医疗与医美': ['医美', '消费医疗'],
  '医疗服务与大健康': ['医疗服务', '养老'], '光伏产业链': ['光伏', '硅料'],
  '新能源汽车': ['新能源车', '汽车'], '储能与电池': ['储能', '锂电池'],
  '电池材料': ['电池材料', '锂电材料'], '汽车零部件与电动化': ['汽车零部件', '充电桩'],
  '风电氢能核电': ['风电', '氢能'], '食品加工与调味品': ['食品', '调味品'],
  '酒类与饮料': ['白酒', '饮料'], '化妆品与饰品': ['化妆品', '饰品'],
  '纺织服装': ['纺织', '服装'], '包装造纸轻工': ['造纸', '包装'],
  '家电家居': ['家电', '智能家居'], '电商与新零售': ['电商', '新零售'],
  '商贸零售': ['零售', '商贸'], '社会服务': ['社会服务', '消费'],
  '旅游酒店餐饮': ['旅游', '酒店'], '游戏影视': ['游戏', '影视'],
  '体育与文娱': ['体育', '文娱'], '传媒出版广告': ['传媒', '广告'],
  '教育服务': ['教育', '在线教育'], '低空与无人系统': ['低空经济', '无人机'],
  '机器人与自动化': ['机器人', '自动化'], '军工航天': ['军工', '航天'],
  '工业母机与通用设备': ['工业母机', '机床'], '船舶与海工': ['船舶', '海工装备'],
  '工程机械与重装': ['工程机械', '重装'], '交通物流': ['物流', '航运'],
  '电力运营': ['电力', '火电'], '环保水务': ['环保', '水务'],
  '电网设备': ['电网', '电力设备'], '钢铁建材': ['钢铁', '建材'],
  '贵金属与有色': ['有色', '黄金'], '稀土小金属与能源金属': ['稀土', '小金属'],
  '化纤塑料与新材料': ['化工', '化纤'], '基础化工': ['化工', '化肥'],
  '煤炭油气': ['煤炭', '油气'], '风格因子': ['风格', '因子投资'],
  '特殊标签': ['特殊标签', '综合'], '指数成分股': ['指数', '成分股'],
  '短期热点': ['热点', '涨停'], '平台与大厂映射': ['平台经济', '大厂'],
  '资金偏好': ['资金', '机构'], '改革与资本运作': ['改革', '资本运作'],
  '区域主题': ['区域', '主题'], '财报事件': ['财报', '业绩'],
  '养殖与饲料': ['养殖', '饲料'], '种植与种业': ['种植', '种业'],
  '农产品加工': ['农产品', '加工'], '银行': ['银行', '国有银行'],
  '券商与非银': ['券商', '保险'], '金融科技': ['金融科技', '支付'],
  '房地产': ['房地产', '物业'], '建筑基建': ['基建', '建筑'],
};
function deriveBoardTagsAfterMove(board, primaryName, secondaryName){
  const tags=[]; const seen=new Set();
  const add=(t)=>{ if(t && typeof t==='string'){ const v=t.trim(); if(v && !seen.has(v)){ seen.add(v); tags.push(v); } } };
  // 1. 一级主题短标签
  add(_PRIMARY_SHORT_TAG[primaryName] || primaryName);
  // 2. 二级分类短标签
  add(_SECONDARY_SHORT_TAG[secondaryName] || secondaryName);
  // 3. 行业/概念类型标签
  if(board.type==='industry') add('行业');
  else if(board.type==='concept') add('概念');
  // 4. 领域维度标签
  const domains=_DOMAIN_TAGS[secondaryName] || [];
  for(const d of domains){ if(tags.length>=6) break; add(d); }
  // 5. 兜底：确保至少 2 个
  if(tags.length<2) add('股票');
  return tags.slice(0,6);
}
function moveBoardToSubCat(catName, subName){
  if(!_ctxBoard)return;
  const cats=store.categoryData;
  let srcBoards=null,srcIdx=-1;
  for(const c of cats){
    for(const s of (c.subcategories||[])){
      const boards = s.boards || [];
      const i=boards.findIndex(b=>b.code===_ctxBoard.code&&b.type===_ctxBoard.type);
      if(i>=0){srcBoards=boards;srcIdx=i;break;}
    }
    if(srcBoards)break;
  }
  if(!srcBoards)return;
  const dst=cats.find(c=>c.name===catName);
  const dstSub=dst ? (dst.subcategories||[]).find(s=>s.name===subName) : null;
  if(!dstSub)return;
  const board=srcBoards.splice(srcIdx,1)[0];
  board.primary_category=catName;
  board.secondary_category=subName;
  // 重建 tags，使其与目标分类一致
  board.tags = deriveBoardTagsAfterMove(board, catName, subName);
  if(!dstSub.boards)dstSub.boards=[];
  dstSub.boards.push(board);
  renderNav();closeCtx();toast('已移到「'+catName+' / '+subName+'」');
  // 持久化到服务器
  _saveClassification();
}

async function _saveClassification(){
  try{
    const doc = store.classificationDoc || {};
    const payload = {
      version: doc.version || '5.0',
      updated_at: new Date().toISOString().slice(0,10),
      taxonomy: doc.taxonomy || {},
      categories: store.categoryData || []
    };
    store.classificationDoc = payload;
    await fetch(API+'/api/classification/save', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
  }catch(e){console.warn('分类保存失败',e);}
}

function toggleCat(id, name) {
  const el = document.getElementById(id); if (!el) return;
  const arrow = el.previousElementSibling.querySelector('.arrow');
  el.classList.toggle('collapsed');
  const isExpanded = !el.classList.contains('collapsed');
  if (arrow) arrow.textContent = isExpanded ? '▼' : '▶';
  if (!store._expandedCats) store._expandedCats = {};
  store._expandedCats[name] = isExpanded;
  if (isExpanded) {
    store.activeCat = name;
    document.querySelectorAll('.cat-item.active').forEach(e => e.classList.remove('active'));
    el.previousElementSibling.classList.add('active');
  } else {
    store.activeCat = null;
  }
}

function toggleSubCat(subId, e) {
  if (e) e.stopPropagation();
  const el = document.getElementById(subId);
  if (!el) return;
  const arrow = el.previousElementSibling.querySelector('.sub-arrow');
  el.classList.toggle('collapsed');
  const isExpanded = !el.classList.contains('collapsed');
  if (arrow) arrow.textContent = isExpanded ? '▼' : '▶';
  if (!store._expandedCats) store._expandedCats = {};
  store._expandedCats[subId] = isExpanded;
}

// ===== 5. 板块选择 -> 交给 Pro（带防卡死保护+预加载+请求状态管理） =====

// loading overlay 竞态保护
const LOADING_MIN_MS = 300;
const LOADING_MAX_MS = 8000;
const LOADING_DELAY_MS = 220;
let _loadingSeq = 0;
let _loadingTimer = null;
let _loadingDelayTimer = null;

// 成分股面板竞态保护
let _consSeq = 0;
let _consCache = new Map();
const CONS_TTL = 10000;
let _consAbort = null;
let _loadingShownAt = 0;
let _loadingMaxTimer = null;

// 页面加载后预加载第一个板块
function preloadFirstBoard(){
  const first = document.querySelector('.board-item');
  if(first){
    const name = first.dataset.name;
    const code = first.dataset.code;
    const type = first.dataset.type;
    if(!_preloadDone[code+type]){
      _preloadDone[code+type] = true;
      fetch(API+'/api/kline/'+type+'/'+code+'?name='+encodeURIComponent(name)+'&period=daily&timeout=5&cache_first=1').catch(()=>{});
    }
  }
}
setTimeout(preloadFirstBoard, 500);

// 初始化：加载分析历史（由 app-init.js 在 sse-client.js 加载后统一调用）
function selectBoard(name,code,type,ev){
  // 点击去重：200ms内重复点击忽略
  if(_lastClickBoard && _lastClickBoard.code===code && _lastClickBoard.type===type
     && Date.now()-_lastClickTime < 200) return;
  _lastClickTime = Date.now();
  const reqId = ++_currentReqId;

  // A. 即时 active 状态：每次都先清除，再定位目标（不依赖 ev）
  document.querySelectorAll('.board-item.active').forEach(e=>e.classList.remove('active'));
  let targetEl = null;
  if(ev && ev.target && ev.target.closest('.board-item')){
    targetEl = ev.target.closest('.board-item');
  } else {
    targetEl = document.querySelector('.board-item[data-code="'+escAttr(code)+'"][data-type="'+escAttr(type)+'"]');
  }
  if(targetEl){
    targetEl.classList.add('active');
    // pressing 短暂按下态，提升即时反馈
    targetEl.classList.add('pressing');
    (function(el){
      setTimeout(function(){ el.classList.remove('pressing'); }, 200);
    })(targetEl);
    try { targetEl.scrollIntoView({block:'nearest'}); } catch(e){}
  }

  store.selected={name,code,type};
  try{ _pushRecent(code,name,type); }catch(e){}   // 最近访问留痕（左栏顶部快捷条）
  window.store = store;
  _lastClickBoard={name,code,type};

  // E. 立即更新 price-bar（不依赖网络）
  _updatePriceBar(name, code, type);

  // 统一选择事件 - 图表更新委托给 unified-selector
  window.dispatchEvent(new CustomEvent('select-symbol', {
    detail: { code: code, name: name, type: type, source: 'nav-panel', trigger: 'click' }
  }));

  // loading overlay 和 pro.setSymbol 现在由 unified-selector → chart-controller 统一处理
  // 保留 loadSignals 调用
  loadSignals(code);

  // 点击板块 → 展开成分股面板（仅板块类型，个股点击不触发）
  if(type!=='stock'){
    showConsPanel(name, code, type, ev);
  }
}

// E. 更新 price-bar（即时反馈，不依赖网络）
var _priceBarSeq = 0;

let _lastHeaderTarget = { name: '上证指数', code: 'sh000001', type: 'index' };
let _headerObserver = null;

function _symbolStripTypeLabel(type, code) {
  if (type === 'industry') return '行';
  if (type === 'concept') return '概';
  if (type === 'stock') return '股';
  return '指';
}

function _symbolStripPeriodLabel(period) {
  var p = period || ((window.__board_ctx && window.__board_ctx.period) || 'daily');
  if (p && typeof p === 'object') {
    p = p.value || p.key || p.id || p.type || p.name || p.text || 'daily';
  }
  var map = {
    daily: '日线',
    weekly: '周线',
    monthly: '月线',
    minute: '分时',
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '30m': '30m',
    '60m': '60m'
  };
  return map[p] || p;
}

function _symbolStripLastDateFromChart() {
  try {
    var list = window.__kline_chart && window.__kline_chart.getDataList ? window.__kline_chart.getDataList() : [];
    if (!list || !list.length) return '';
    var last = list[list.length - 1] || {};
    var ts = last.timestamp || last.time || last.date;
    if (!ts) return '';
    if (typeof ts === 'number') {
      var d = new Date(ts);
      if (!isNaN(d.getTime())) return d.toISOString().slice(0, 10);
    }
    return String(ts).slice(0, 10);
  } catch (e) {
    return '';
  }
}

function updateSymbolStrip(name, code, type, meta) {
  var strip = document.getElementById('symbol-strip');
  if (!strip) return;
  var badge = document.getElementById('sym-type-badge');
  var nameEl = document.getElementById('sym-name');
  var codeEl = document.getElementById('sym-code');
  var periodEl = document.getElementById('sym-period');
  var sourceEl = document.getElementById('sym-source');
  var barsEl = document.getElementById('sym-bars');
  var lastEl = document.getElementById('sym-last-date');
  var cur = _lastHeaderTarget || {};
  var nextName = name || cur.name || '';
  var nextCode = code || cur.code || '';
  var nextType = type || cur.type || 'index';
  meta = meta || {};

  strip.dataset.type = nextType;
  strip.dataset.state = meta.state || strip.dataset.state || 'ready';
  if (badge) badge.textContent = _symbolStripTypeLabel(nextType, nextCode);
  if (nameEl) nameEl.textContent = nextName;
  if (codeEl) codeEl.textContent = nextCode;
  if (periodEl) periodEl.textContent = _symbolStripPeriodLabel(meta.period);
  if (sourceEl) sourceEl.textContent = meta.source || 'cache_first';
  if (barsEl && meta.count != null) barsEl.textContent = meta.count + ' bars';
  if (lastEl) lastEl.textContent = meta.lastDate || _symbolStripLastDateFromChart() || lastEl.textContent || '--';
}

function _updatePriceBar(name, code, type){
  _lastHeaderTarget = { name: name, code: code, type: type };
  updateSymbolStrip(name, code, type, { state: 'loading' });
  setTimeout(function() { updateChartHeaderChg(name, code, type); }, 50);
}

function updateChartHeaderChg(name, code, type){
  name = name || (_lastHeaderTarget && _lastHeaderTarget.name) || '上证指数';
  code = code || (_lastHeaderTarget && _lastHeaderTarget.code) || 'sh000001';
  type = type || (_lastHeaderTarget && _lastHeaderTarget.type) || 'index';
  _lastHeaderTarget = { name: name, code: code, type: type };

  var container = document.getElementById('pro-container');
  if (!container) return;
  var periodBar = container.querySelector('.klinecharts-pro-period-bar');
  if (!periodBar) return;

  // 绑定 MutationObserver 监听 Pro 顶栏 DOM 重绘，自动补建 #header-chg-tag
  if (!_headerObserver && window.MutationObserver) {
    _headerObserver = new MutationObserver(function() {
      var pb = container.querySelector('.klinecharts-pro-period-bar');
      if (pb && !pb.querySelector('#header-chg-tag')) {
        if (_lastHeaderTarget) {
          updateChartHeaderChg(_lastHeaderTarget.name, _lastHeaderTarget.code, _lastHeaderTarget.type);
        }
      }
    });
    _headerObserver.observe(periodBar, { childList: true, subtree: false });
  }

  function renderChgHTML(pct, price) {
    var pb = container.querySelector('.klinecharts-pro-period-bar');
    if (!pb) return;
    var symEl = pb.querySelector('.symbol') || pb.querySelector('button') || pb.querySelector('.klinecharts-pro-symbol');
    if (!symEl) return;

    var tag = symEl.querySelector('#header-chg-tag') || pb.querySelector('#header-chg-tag');
    if (!tag) {
      tag = document.createElement('span');
      tag.id = 'header-chg-tag';
      tag.style.cssText = 'display:inline-flex;align-items:center;margin-left:8px;margin-right:12px;user-select:none;font-weight:bold;';
      symEl.appendChild(tag);
    }

    var numPct = (pct != null && pct !== '' && !isNaN(Number(pct))) ? Number(pct) : null;
    var numPrice = (price != null && price !== '' && !isNaN(Number(price))) ? Number(price) : null;

    var cls = numPct == null || !isFinite(numPct) || numPct === 0 ? 'flat' : (numPct > 0 ? 'up' : 'down');
    var sign = isFinite(numPct) && numPct > 0 ? '+' : '';
    var pctText = numPct != null && isFinite(numPct) ? sign + numPct.toFixed(2) + '%' : '--';
    var priceText = numPrice != null && isFinite(numPrice) ? ' (' + numPrice.toFixed(2) + ')' : '';

    tag.innerHTML = '<span class="pro-sym-pct ' + cls + '">' + pctText + '</span>' +
      (priceText ? '<span class="pro-sym-pts ' + cls + '">' + priceText + '</span>' : '');
  }

  var numChg = null;
  if (_boardChgData) {
    var key = type + ':' + code;
    var chg = _boardChgData[key] !== undefined ? _boardChgData[key] : _boardChgData[code];
    if (chg !== undefined && chg !== null) {
      numChg = Number(chg);
    }
  }

  renderChgHTML(numChg, null);
  var idxPriceEl = document.getElementById('prc_' + code);
  if (idxPriceEl) {
    var idxText = idxPriceEl.textContent || '';
    var idxMatch = idxText.match(/([+-]?\d+(?:\.\d+)?)%\s*\((\d+(?:\.\d+)?)\)/);
    if (idxMatch) {
      renderChgHTML(Number(idxMatch[1]), Number(idxMatch[2]));
    }
  }

  var isIndex = (type === 'index' || type === 'hk_index' || type === 'us' || code.startsWith('sh') || code.startsWith('sz'));
  var reqType = isIndex ? 'index' : (type || 'stock');
  if (isIndex || reqType === 'stock') {
    var apiType = isIndex ? type : 'stock';
    var mySeq = ++_priceBarSeq;
    fetch(API + '/api/spot/' + apiType + '/' + encodeURIComponent(code))
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (mySeq !== _priceBarSeq) return;
        var d = (j && j.data) || {};
        if (d.price == null) return;
        var num = Number(d.change_pct);
        renderChgHTML(num, d.price);
      })
      .catch(function(){});
  } else if (type === 'industry' || type === 'concept') {
    if (numChg == null) {
      fetch(API + '/api/board-changes')
        .then(function(r){ return r.json(); })
        .then(function(j){
          var bd = (j && j.data) || {};
          _boardChgData = bd;
          var key = type + ':' + code;
          var chg = bd[key] !== undefined ? bd[key] : bd[code];
          if (chg !== undefined && chg !== null) {
            renderChgHTML(Number(chg), null);
          }
        })
        .catch(function(){});
    }
  }
}

window.addEventListener('kline-loaded', function(ev) {
  var d = (ev && ev.detail) || {};
  var cur = _lastHeaderTarget || {};
  if (d.symbol && cur.code && String(d.symbol) !== String(cur.code)) return;
  updateSymbolStrip(cur.name, cur.code, cur.type, {
    state: d.ok === false ? 'error' : 'ready',
    period: d.period,
    source: 'cache_first',
    count: d.count,
    lastDate: _symbolStripLastDateFromChart()
  });
  setTimeout(function(){ updateChartHeaderChg(cur.name, cur.code, cur.type); }, 120);
});

window.addEventListener('kline-error', function(ev) {
  var cur = _lastHeaderTarget || {};
  updateSymbolStrip(cur.name, cur.code, cur.type, {
    state: 'error',
    source: 'error',
    lastDate: _symbolStripLastDateFromChart()
  });
});

setTimeout(function(){ updateChartHeaderChg(); }, 600);
setTimeout(function(){ updateChartHeaderChg(); }, 1500);
setTimeout(function(){ updateChartHeaderChg(); }, 3000);
window.addEventListener('kline-chart-ready', function() {
  setTimeout(function(){ updateChartHeaderChg(); }, 180);
  setTimeout(function(){ updateChartHeaderChg(); }, 900);
});

// 二级导航栏：悬浮显示成分股
let _consPanel = null;
let _consPanelTimer = null;

function _getConsPanel(){
  if(!_consPanel) _consPanel = document.getElementById('floating-cons');
  return _consPanel;
}

function showConsPanel(name, code, type, ev){
  clearTimeout(_consPanelTimer);
  
  const panel = _getConsPanel();
  if(!panel) return;
  
  // C. 成分股面板竞态保护
  const myConsSeq = ++_consSeq;
  
  let item = null;
  if(ev && ev.target) {
    item = ev.target.closest('.board-item');
  }
  if(!item) {
    item = document.querySelector('.board-item[data-code="'+escAttr(code)+'"][data-type="'+escAttr(type)+'"]');
  }
  
  const boardType = type === 'industry' ? 'industry' : 'concept';
  
  let _sortMode = 0;  // 0=市值, 1=涨幅↓, 2=涨幅↑
  let _consData = [];
  
  function sortData(){
    if(_sortMode===0) _consData.sort((a,b)=>(b.mkt_cap||0)-(a.mkt_cap||0));
    else if(_sortMode===1) _consData.sort((a,b)=>(b.change_pct||0)-(a.change_pct||0));
    else _consData.sort((a,b)=>(a.change_pct||0)-(b.change_pct||0));
  }
  
  // 仅更新body内容区
  function renderBody(){
    const body = panel.querySelector('.fcons-body');
    if(!body) return;
    let h = '';
    _consData.forEach(function(s){
      const capNum = Number(s.mkt_cap) || 0;
      const capStr = capNum >= 10000 ? (capNum/10000).toFixed(2)+'万亿' : capNum.toFixed(0)+'亿';
      const chg = Number(s.change_pct || 0);
      const chgNum = isNaN(chg) ? 0 : chg;
      const chgCls = chgNum >= 0 ? 'up' : 'down';
      const chgStr = (chgNum > 0 ? '+' : '') + chgNum.toFixed(2) + '%';
      const closeNum = Number(s.close);
      const closeStr = !isNaN(closeNum) && closeNum > 0 ? closeNum.toFixed(2) : '-';
      h += '<div class="fcons-item" onclick="closeConsPanel();selectBoard(\''+escAttr(s.name)+'\',\''+escAttr(s.code)+'\',\'stock\',null)">'+
        '<span class="fcons-code">'+escHtml(s.code)+'</span>'+
        '<span class="fcons-name">'+escHtml(s.name)+'</span>'+
        '<span class="fcons-cap">'+capStr+'</span>'+
        '<span class="fcons-pct '+chgCls+'" title="收盘 '+closeStr+'">'+chgStr+'</span>'+
        '</div>';
    });
    if(!h) h = '<div class="fcons-item" style="color:#434651">无数据</div>';
    body.innerHTML = h;
  }
  
  function refreshUI(){
    const hdr = panel.querySelector('.fcons-header');
    if(hdr){
      hdr.querySelector('.fcons-title').innerHTML = escHtml(name)+' <span style="color:#434651">'+_consData.length+'只</span>';
      const ind = hdr.querySelector('.fcons-sort-indicator');
      if(ind) ind.textContent = _sortMode ? (_sortMode===1?'↓':'↑') : '';
      const btn = hdr.querySelector('.fcons-btn-sort');
      if(btn) btn.className = 'fcons-btn fcons-btn-sort' + (_sortMode?' active':'');
    }
    renderBody();
  }

  // 构建面板结构（header + body，后续只更新body和header局部属性和文本）
  panel.innerHTML = 
    '<div class="fcons-header">'+
      '<span class="fcons-title">'+escHtml(name)+' 成分股 <span style="color:#434651;font-size:10px">加载中...</span></span>'+
      '<button class="fcons-btn fcons-btn-refresh" id="_csRef">↻</button>'+
      '<button class="fcons-btn fcons-btn-sort" id="_csSort">⇅<span class="fcons-sort-indicator"></span></button>'+
    '</div>'+
    '<div class="fcons-body"><div class="fcons-item" style="color:#434651">加载中...</div></div>';
  
  // 绑定事件（用addEventListener避免inline onclick触发时DOM已被替换导致closest失败）
  var rfBtn = panel.querySelector('#_csRef');
  var srBtn = panel.querySelector('#_csSort');
  rfBtn.onclick = function(){ consDoRefresh(); };
  srBtn.onclick = function(){ consDoSort(); };

  const rect = item ? item.getBoundingClientRect() : { top: 100 };
  const navPanel = document.getElementById('nav-panel');
  const navWidth = navPanel ? navPanel.offsetWidth : 280;
  panel.style.left = (navWidth + 8) + 'px';
  panel.style.top = Math.max(36, rect.top) + 'px';
  panel.style.display = 'block';
  panel.classList.add('show');
  
  function consDoRefresh(){
    if(myConsSeq !== _consSeq) return; // 旧面板的刷新忽略
    rfBtn.style.opacity = '0.5';
    _consCache.delete(boardType + ':' + code);
    if(_consAbort){ try{ _consAbort.abort(); }catch(_){} }
    _consAbort = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var rsignal = _consAbort ? _consAbort.signal : undefined;
    var rurl = API+'/api/board-cons-sorted/'+boardType+'/'+code+'?refresh=1';
    var rp = (typeof window.apiFetch === 'function')
      ? window.apiFetch(rurl, { signal: rsignal, timeout: 15000 }).then(function(res){ return res.data; })
      : fetch(rurl, { signal: rsignal }).then(function(r){ return r.json(); });
    rp.then(function(resp){
      if(myConsSeq !== _consSeq) return;
      _consCache.set(boardType + ':' + code, { t: Date.now(), data: resp });
      _consData = (resp && resp.data) || [];
      sortData();
      refreshUI();
      rfBtn.style.opacity = '1';
    }).catch(function(e){
      if(e && e.name === 'AbortError') return;
      if(myConsSeq !== _consSeq) return;
      rfBtn.style.opacity = '1';
      _showConsError();
    });
  }
  function consDoSort(){
    _sortMode = (_sortMode + 1) % 3;
    sortData();
    refreshUI();
  }
  // D. 成分股加载失败时保留面板结构，显示失败和重试按钮
  function _showConsError(){
    var body = panel.querySelector('.fcons-body');
    if(!body) return;
    body.innerHTML = '';
    var errDiv = document.createElement('div');
    errDiv.className = 'fcons-item fcons-err';
    errDiv.style.color = '#ef5350';
    errDiv.textContent = '加载失败 ';
    var retryBtn = document.createElement('button');
    retryBtn.className = 'fcons-btn';
    retryBtn.textContent = '↻ 重试';
    retryBtn.onclick = function(){ consDoRefresh(); };
    errDiv.appendChild(retryBtn);
    body.appendChild(errDiv);
  }

  function _applyConsResp(resp){
    if(myConsSeq !== _consSeq) return;
    _consData = (resp && resp.data) || [];
    sortData();
    refreshUI();
  }
  var ckey = boardType + ':' + code;
  var cached = _consCache.get(ckey);
  // 盘中前端成分缓存 10s；非盘中 60s
  var _nowCons = new Date();
  var _hhmmCons = _nowCons.getHours()*100 + _nowCons.getMinutes();
  var _tradingCons = _nowCons.getDay()>=1 && _nowCons.getDay()<=5 && _hhmmCons>=915 && _hhmmCons<=1505;
  var consTtl = _tradingCons ? CONS_TTL : 60000;
  if(cached && (Date.now() - cached.t) < consTtl){
    _applyConsResp(cached.data);
  } else {
    if(_consAbort){ try{ _consAbort.abort(); }catch(_){} }
    _consAbort = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var csignal = _consAbort ? _consAbort.signal : undefined;
    var curl = API+'/api/board-cons-sorted/'+boardType+'/'+code;
    var cp = (typeof window.apiFetch === 'function')
      ? window.apiFetch(curl, { signal: csignal, timeout: 15000, dedupeKey: 'cons:'+ckey }).then(function(res){ return res.data; })
      : fetch(curl, { signal: csignal }).then(function(r){ return r.json(); });
    cp.then(function(resp){
      if(myConsSeq !== _consSeq) return;
      if(resp && resp.error && !(resp.data && resp.data.length)){
        _showConsError();
        return;
      }
      _consCache.set(ckey, { t: Date.now(), data: resp });
      _applyConsResp(resp);
    }).catch(function(e){
      if(e && e.name === 'AbortError') return;
      if(myConsSeq !== _consSeq) return;
      _showConsError();
    });
  }
}

function closeConsPanel(){
  const panel = _getConsPanel();
  if(!panel) return;
  panel.classList.remove('show');
  panel.style.display = 'none';
}

// 点击面板外部关闭
document.addEventListener('click', function(e){
  if(!e.target.closest('#floating-cons') && !e.target.closest('.board-item')){
    closeConsPanel();
  }
});

// ===== 板块涨跌幅排序逻辑（支持一级/二级分类 3 态循环切换） =====
// 3 态循环顺序: null (默认) -> 'asc' (低到高) -> 'desc' (高到低) -> null (恢复原样)

function toggleSubSort(subId, event) {
  if (event) event.stopPropagation(); // 不触发展开/折叠
  if (!store._sortStates) store._sortStates = {};
  const current = store._sortStates[subId] || null;
  const next = current === null ? 'asc' : current === 'asc' ? 'desc' : null;
  if (next) store._sortStates[subId] = next;
  else delete store._sortStates[subId];

  sortSubBoards(subId, next);
  updateSortBtnUI(subId, next);
}

function toggleCatSort(catId, event) {
  if (event) event.stopPropagation(); // 不触发展开/折叠
  if (!store._sortStates) store._sortStates = {};
  const current = store._sortStates[catId] || null;
  const next = current === null ? 'asc' : current === 'asc' ? 'desc' : null;
  if (next) store._sortStates[catId] = next;
  else delete store._sortStates[catId];

  sortCatAll(catId, next);
  updateSortBtnUI(catId, next);
}

function updateSortBtnUI(targetId, direction) {
  const container = document.getElementById(targetId);
  if (!container) return;
  const prev = container.previousElementSibling;
  if (!prev) return;
  const btn = prev.querySelector('.nav-sort-btn');
  if (btn) {
    btn.textContent = direction === 'asc' ? '↑ 低→高' : direction === 'desc' ? '↓ 高→低' : '↕ 排名';
    btn.className = 'nav-sort-btn' + (direction ? ' active ' + direction : '');
  }
}

function sortSubBoards(subId, direction) {
  const col = document.getElementById(subId);
  if (!col) return;
  const items = Array.from(col.querySelectorAll('.board-item'));
  if (!direction) {
    // 恢复原始 0-indexed 初始位置
    items.sort((a, b) => (parseInt(a.dataset.origIdx) || 0) - (parseInt(b.dataset.origIdx) || 0));
  } else {
    items.sort((a, b) => {
      const ca = parseFloat(a.dataset.chg) || 0;
      const cb = parseFloat(b.dataset.chg) || 0;
      return direction === 'desc' ? cb - ca : ca - cb;
    });
  }
  items.forEach(el => col.appendChild(el));
}

function sortCatAll(catId, direction) {
  const col = document.getElementById(catId);
  if (!col) return;

  // 1. 递归对一级分类内部的所有二级子分类也应用相同方向排序
  const groups = Array.from(col.querySelectorAll('.sub-cat-group'));
  groups.forEach(g => {
    const wrap = g.querySelector('.sub-boards-wrap');
    if (wrap) sortSubBoards(wrap.id, direction);
  });

  // 2. 计算各个二级分类组的平均涨跌幅，并排序二级分类组本身
  if (!direction) {
    groups.sort((a, b) => (parseInt(a.dataset.origIdx) || 0) - (parseInt(b.dataset.origIdx) || 0));
  } else {
    groups.sort((a, b) => {
      const itemsA = Array.from(a.querySelectorAll('.board-item'));
      const itemsB = Array.from(b.querySelectorAll('.board-item'));

      const avgA = itemsA.length ? (itemsA.reduce((sum, el) => sum + (parseFloat(el.dataset.chg) || 0), 0) / itemsA.length) : 0;
      const avgB = itemsB.length ? (itemsB.reduce((sum, el) => sum + (parseFloat(el.dataset.chg) || 0), 0) / itemsB.length) : 0;

      return direction === 'desc' ? avgB - avgA : avgA - avgB;
    });
  }
  groups.forEach(g => col.appendChild(g));
}

// ===== 板块涨跌幅数据加载与渲染 =====
let _boardChgData = null;

function _boardChgNumber(raw){
  if(raw == null) return null;
  if(typeof raw === 'object'){
    var v = raw.change_pct != null ? raw.change_pct : raw.changePct;
    if(v == null) return null;
    var n = Number(v);
    return isNaN(n) ? null : n;
  }
  var n2 = Number(raw);
  return isNaN(n2) ? null : n2;
}

function applyBoardChgToDOM(){
  if(!_boardChgData) return;
  document.querySelectorAll('.board-item').forEach(function(el){
    const code = el.dataset.code;
    const type = el.dataset.type;
    const key = type + ':' + code;
    let chg = _boardChgData[key];
    if (chg === undefined) chg = _boardChgData[code];
    const numChg = _boardChgNumber(chg);
    const span = el.querySelector('.board-chg');
    if(span && numChg != null){
      const cls = numChg >= 0 ? 'up' : 'down';
      span.className = 'board-chg ' + cls;
      span.textContent = (numChg > 0 ? '+' : '') + numChg.toFixed(2) + '%';
      el.dataset.chg = numChg;
    }
  });

  // 重新应用所有已激活的一级与二级分类排序
  if (store._sortStates) {
    Object.entries(store._sortStates).forEach(([targetId, dir]) => {
      if (targetId.includes('_sub_')) {
        sortSubBoards(targetId, dir);
      } else {
        sortCatAll(targetId, dir);
      }
    });
  }
}

async function loadBoardChanges(){
  try{
    const r = await fetch(API+'/api/board-changes');
    const resp = await r.json();
    _boardChgData = resp.data || {};
    applyBoardChgToDOM();
  } catch(e){}
}

// 初始化加载涨跌幅
setTimeout(loadBoardChanges, 1500);

// B. loading overlay（真正可见的遮罩层）
function showLoading(name, code){
  const container = document.getElementById('pro-container');
  if(!container) return;
  let overlay = document.getElementById('chart-loading-overlay');
  if(!overlay){
    overlay = document.createElement('div');
    overlay.id = 'chart-loading-overlay';
    overlay.innerHTML = '<div class="chart-loading-inner"><div class="spinner"></div><div class="chart-loading-text">加载中...</div><div class="chart-loading-sub"></div></div>';
    container.appendChild(overlay);
  }
  const text = overlay.querySelector('.chart-loading-text');
  const sub = overlay.querySelector('.chart-loading-sub');
  if(text) text.textContent = '加载中...';
  if(sub) sub.textContent = name ? (code ? name + ' (' + code + ')' : name) : '';
  overlay.classList.add('show');
  _loadingShownAt = Date.now();
}
function hideLoading(){
  if (_loadingTimer) { clearTimeout(_loadingTimer); _loadingTimer = null; }
  if (_loadingMaxTimer) { clearTimeout(_loadingMaxTimer); _loadingMaxTimer = null; }
  if (_loadingDelayTimer) { clearTimeout(_loadingDelayTimer); _loadingDelayTimer = null; }
  const overlay = document.getElementById('chart-loading-overlay');
  if(overlay) overlay.classList.remove('show');
}

// K 线到达后关闭 loading（最小显示 LOADING_MIN_MS 防闪）
if (!window.__klineLoadedBound) {
  window.__klineLoadedBound = true;
  window.addEventListener('kline-loaded', function(){
    try {
      const seqAt = _loadingSeq;
      if (_loadingDelayTimer) { clearTimeout(_loadingDelayTimer); _loadingDelayTimer = null; }
      const elapsed = Date.now() - (_loadingShownAt || 0);
      const wait = Math.max(0, LOADING_MIN_MS - elapsed);
      if (_loadingTimer) { clearTimeout(_loadingTimer); _loadingTimer = null; }
      _loadingTimer = setTimeout(function(){
        if (_loadingSeq === seqAt) hideLoading();
      }, wait);
    } catch(_){}
  });
  window.addEventListener('kline-error', function(){
    try {
      const seqAt = _loadingSeq;
      if (_loadingDelayTimer) { clearTimeout(_loadingDelayTimer); _loadingDelayTimer = null; }
      if (_loadingTimer) { clearTimeout(_loadingTimer); _loadingTimer = null; }
      if (_loadingSeq === seqAt) hideLoading();
    } catch(_){}
  });
}

// ===== WebSocket 板块涨跌幅实时消费 =====

// 将 WS 推送的局部变化应用到 DOM（仅更新 .board-chg，不全量重绘）
// 板块涨幅午后冻结（snapshot），重拉不会变；轮询保持但无害
function applyBoardChangesToDOM(changes) {
  if (!changes) return;
  if (!_boardChgData) _boardChgData = {};
  Object.entries(changes).forEach(function([rawKey, change]) {
    var boardCode = rawKey.includes(':') ? rawKey.split(':').slice(-1)[0] : rawKey;
    var val = _boardChgNumber(change);
    if (val == null) return;
    // 写缓存：同时保留 type:code 与裸 code
    _boardChgData[rawKey] = val;
    _boardChgData[boardCode] = val;
    document.querySelectorAll('.board-item[data-code="' + boardCode + '"]').forEach(function(el) {
      var type = el.dataset.type;
      if (type) _boardChgData[type + ':' + boardCode] = val;
      var item = el.querySelector('.board-chg');
      if (!item) return;
      item.textContent = (val > 0 ? '+' : '') + val.toFixed(2) + '%';
      item.classList.remove('up', 'down', 'flat');
      if (val > 0.01) item.classList.add('up');
      else if (val < -0.01) item.classList.add('down');
      else item.classList.add('flat');
      el.dataset.chg = val;
    });
  });
}

// 监听 WebSocket 板块涨跌幅推送
if (window.RealtimeBus) {
  window.addEventListener('rt-board-changes', function(e) {
    if (e.detail && e.detail.data) {
      applyBoardChangesToDOM(e.detail.data);
    }
  });
}

// 板块行情轮询：盘中 60s，非盘中 5 分钟（此前 5 分钟导致「涨幅不更新」体感）
function startBoardFallbackPolling() {
  if (typeof loadBoardChanges === 'function') loadBoardChanges();
  var now = new Date();
  var hhmm = now.getHours() * 100 + now.getMinutes();
  var trading = now.getDay() >= 1 && now.getDay() <= 5 && hhmm >= 915 && hhmm <= 1505;
  setTimeout(startBoardFallbackPolling, trading ? 60000 : 300000);
}
setTimeout(startBoardFallbackPolling, 8000);

