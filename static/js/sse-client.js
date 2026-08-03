// ===== 全局变量声明 =====
let _sseRefreshTimer;
let _aiLastAnnotations = null;

// ===== 6. 信号 =====
let _signalsReqId = 0;
let _signalsAbort = null;
async function loadSignals(code){
  const myId = ++_signalsReqId;
  const list=document.getElementById('signal-list');
  if(!list) return;
  if(_signalsAbort){ try{ _signalsAbort.abort(); }catch(_){ } }
  _signalsAbort = (typeof AbortController!=='undefined') ? new AbortController() : null;
  try{
    let s = null;
    if(typeof window.apiFetch === 'function'){
      const res = await window.apiFetch(API+'/api/signals/'+code, {
        signal: _signalsAbort ? _signalsAbort.signal : undefined,
        timeout: 10000,
        dedupeKey: 'signals:'+code
      });
      if(myId !== _signalsReqId) return;
      s = res && res.data;
    } else {
      const r = await fetch(API+'/api/signals/'+code, { signal: _signalsAbort ? _signalsAbort.signal : undefined });
      if(myId !== _signalsReqId) return;
      try { s = await r.json(); } catch(_){ s = null; }
    }
    if(myId !== _signalsReqId) return;
    if(!s || typeof s !== 'object'){
      list.innerHTML='<span style="color:#434651">暂无信号</span>';
      return;
    }
    let h='';
    if(Array.isArray(s)){
      s.slice(-10).forEach(function(sg){
        const c=(sg.type||'').toLowerCase().includes('sos')?'buy':'sell';
        h+='<div class="signal-row"><span class="signal-date">'+(sg.date||'').slice(5)+'</span><span class="signal-badge '+c+'">'+(sg.type||'?')+'</span>'+(sg.note||'')+'</div>';
      });
    } else {
      Object.entries(s).forEach(function(pair){
        const sigs = pair[1];
        if(!Array.isArray(sigs))return;
        sigs.slice(-5).forEach(function(sg){
          const c=(sg.type||'').toLowerCase().includes('sos')?'buy':'sell';
          h+='<div class="signal-row"><span class="signal-date">'+(sg.date||'').slice(5)+'</span><span class="signal-badge '+c+'">'+(sg.type||'?')+'</span>'+(sg.note||'')+'</div>';
        });
      });
    }
    list.innerHTML=h||'<span style="color:#434651">暂无信号</span>';
  }catch(e){
    if(e && e.name==='AbortError') return;
    if(myId !== _signalsReqId) return;
    list.innerHTML='<span style="color:#ef5350">加载失败</span>';
  }
}

// WorkBuddy Hook 可用性检测
async function detectWorkBuddyHook(){
  try{
    // 尝试调用 WorkBuddy Hook 配置检测端点
    // WorkBuddy Hooks 通过 HTTP 推送到面板的 /api/ai/result
    // 这里检测本地5000端口是否可达（实际部署时WorkBuddy和面板在同一台机器）
    const r = await fetch('/api/health', {method:'GET'});
    if(r.ok){
      console.log('[WorkBuddy] 面板运行正常');
      // 尝试读取WorkBuddy配置文件中的hooks设置
      try{
        const hooksResp = await fetch('/api/hooks/status', {method:'GET'});
        if(hooksResp.ok){
          const hooksData = await hooksResp.json();
          if(hooksData.hook_url){
            console.log('[WorkBuddy] Hook目标:', hooksData.hook_url);
            return { available: true, hookUrl: hooksData.hook_url };
          }
        }
      }catch(e){}
      return { available: true, hookUrl: null };
    }
  }catch(e){
    console.warn('[WorkBuddy] 面板连接失败:', e);
  }
  return { available: false, hookUrl: null };
}

// 分析历史记录
function loadAnalysisHistory(){
  try{
    const s = localStorage.getItem(_historyKey);
    _analysisHistory = s ? JSON.parse(s) : [];
  }catch(e){ _analysisHistory = []; }
}

function renderAnalysisHistory(){
  const container = document.getElementById('ai-history-list');
  if(!container) return;
  if(_analysisHistory.length === 0){
    container.innerHTML = '<div style="padding:6px 10px;color:#434651;font-size:11px">暂无分析记录</div>';
    return;
  }
  let h = '<div style="font-size:9px;color:#434651;margin-bottom:4px">最近分析: ' + _analysisHistory.length + '条</div>';
  _analysisHistory.slice(0, 10).forEach(r => {
    const dir = r.direction || 'neutral';
    const dirIcon = dir === 'bullish' ? '🐂' : dir === 'bearish' ? '🐻' : '➡️';
    const conf = Math.round((r.confidence || 0) * 100);
    h += '<div class="ai-history-item" style="padding:4px 10px;border-bottom:1px solid #1e2230;cursor:pointer" onclick="replayAnalysisRecord(\''+escAttr(r.id)+'\')">'+
      '<span style="font-size:10px">'+dirIcon+' '+escHtml(r.board_name)+'</span>'+
      '<span style="font-size:9px;color:#787b86;float:right">'+r.createdAt.slice(5,16)+'</span>'+
      '<span style="font-size:9px;color:'+(dir==='bullish'?'#ef5350':dir==='bearish'?'#26a69a':'#787b86')+'">'+conf+'%</span>'+
      '</div>';
  });
  container.innerHTML = h;
}

function replayAnalysisRecord(id){
  const record = _analysisHistory.find(r => r.id === id);
  if(!record) return;
  // 复制记录并标记为历史回放，避免 saveAnalysisRecord 重复保存
  const replayRecord = Object.assign({}, record, { _fromHistory: true });
  // 重新渲染结论
  renderAiResult(replayRecord);
  // 如果有K线数据，加载对应板块
  if(record.board_code && store.selected && store.selected.code === record.board_code){
    drawAiAnnotations(record.annotations || []);
  }
}

// ===== 搜索相关变量定义（不重复声明，在顶部已定义） =====

// ==== SSE 自动刷新 + 断线轮询降级 ====
(function(){
  let _sse = null;
  let _sseRetryCount = 0;
  const MAX_SSE_RETRY = 30;
  let _sseCompensating = false;
  const _SSE_EVENT_NAMES = [
    'data.refreshed', 'data.frozen', 'data_updated', 'data_update_incomplete',
    'data_updating', 'ai_result', 'signals_updated', 'task_progress'
  ];
  const _sseRelayTarget = new EventTarget();
  let _sseRelay = null;

  if (typeof BroadcastChannel !== 'undefined') {
    _sseRelay = new BroadcastChannel('BOARD_APP_SSE_RELAY_V1');
    _sseRelay.onmessage = function(e) {
      var packet = e && e.data;
      if (!packet || _SSE_EVENT_NAMES.indexOf(packet.type) < 0) return;
      _sseRelayTarget.dispatchEvent(new MessageEvent(packet.type, { data: packet.data }));
    };
  }

  // --- 冻结状态 ---
  let _sseFrozen = false;

  // --- 断线轮询降级 ---
  let _fallbackTimer = null;
  const FALLBACK_INTERVAL = 30000; // 30s 轮询 /api/system/status

  // --- 状态指示器 ---
  const _STATUS_LABELS = { connected: '已连接', shared: '共享', reconnecting: '连接中', fallback: '轮询', frozen: '已冻结', disconnected: '断开' };
  const _DATA_SOURCE_LABELS = ['顶部导航栏', '东财概念板块', '行业板块', '指数', '个股数据源'];
  let _dataSourcePopover = null;
  let _dataSourceHideTimer = null;
  let _dataSourceHealthCache = null;
  let _dataSourceHealthAt = 0;

  function _formatHealthTime(value) {
    if (!value) return '';
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', { hour12: false });
  }

  function _healthFallbackItems(statusText) {
    return _DATA_SOURCE_LABELS.map(function(label, index) {
      return {
        id: 'fallback-' + index,
        label: label,
        status: 'unavailable',
        status_text: statusText,
        source: '',
        detail: '',
        last_updated: ''
      };
    });
  }

  function _renderDataSourceHealth(items) {
    if (!_dataSourcePopover) return;
    _dataSourcePopover.replaceChildren();
    var header = document.createElement('div');
    header.className = 'data-source-health-title';
    header.textContent = '数据更新状态';
    _dataSourcePopover.appendChild(header);

    (items || []).forEach(function(item) {
      var row = document.createElement('div');
      var state = ['healthy', 'warning', 'unavailable', 'idle'].indexOf(item.status) >= 0 ? item.status : 'unavailable';
      row.className = 'data-source-health-row is-' + state;

      var dot = document.createElement('span');
      dot.className = 'data-source-health-dot';
      dot.setAttribute('aria-hidden', 'true');

      var content = document.createElement('div');
      content.className = 'data-source-health-content';
      var top = document.createElement('div');
      top.className = 'data-source-health-topline';
      var label = document.createElement('span');
      label.className = 'data-source-health-label';
      label.textContent = item.label || '';
      var status = document.createElement('span');
      status.className = 'data-source-health-state';
      status.textContent = item.status_text || '未知';
      top.append(label, status);

      var meta = document.createElement('div');
      meta.className = 'data-source-health-meta';
      var parts = [];
      if (item.source) parts.push(item.source);
      if (item.detail) parts.push(item.detail);
      var updated = _formatHealthTime(item.last_updated);
      if (updated) parts.push('更新 ' + updated);
      meta.textContent = parts.join(' · ') || '暂无状态详情';
      content.append(top, meta);
      row.append(dot, content);
      _dataSourcePopover.appendChild(row);
    });
  }

  function _positionDataSourcePopover() {
    var anchor = document.getElementById('sse-status-indicator');
    if (!anchor || !_dataSourcePopover || !_dataSourcePopover.classList.contains('show')) return;
    var rect = anchor.getBoundingClientRect();
    var width = _dataSourcePopover.offsetWidth || 350;
    var left = Math.min(window.innerWidth - width - 8, Math.max(8, rect.right - width));
    _dataSourcePopover.style.left = left + 'px';
    _dataSourcePopover.style.top = Math.min(window.innerHeight - _dataSourcePopover.offsetHeight - 8, rect.bottom + 7) + 'px';
  }

  function _ensureDataSourcePopover() {
    if (_dataSourcePopover) return _dataSourcePopover;
    var popover = document.createElement('div');
    popover.id = 'sse-data-source-popover';
    popover.className = 'sse-data-source-popover';
    popover.setAttribute('role', 'tooltip');
    popover.setAttribute('aria-hidden', 'true');
    popover.addEventListener('mouseenter', function() { clearTimeout(_dataSourceHideTimer); });
    popover.addEventListener('mouseleave', _hideDataSourcePopoverSoon);
    document.body.appendChild(popover);
    _dataSourcePopover = popover;
    return popover;
  }

  function _loadDataSourceHealth() {
    if (_dataSourceHealthCache && Date.now() - _dataSourceHealthAt < 15000) {
      _renderDataSourceHealth(_dataSourceHealthCache);
      return Promise.resolve();
    }
    _renderDataSourceHealth(_healthFallbackItems('读取中'));
    return fetch((typeof API === 'string' ? API : '') + '/api/system/data-source-health', { headers: { 'Accept': 'application/json' } })
      .then(function(response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function(payload) {
        var items = payload && Array.isArray(payload.items) ? payload.items : _healthFallbackItems('状态不可用');
        _dataSourceHealthCache = items;
        _dataSourceHealthAt = Date.now();
        _renderDataSourceHealth(items);
        _positionDataSourcePopover();
      })
      .catch(function() {
        _renderDataSourceHealth(_healthFallbackItems('状态不可用'));
        _positionDataSourcePopover();
      });
  }

  function _showDataSourcePopover() {
    clearTimeout(_dataSourceHideTimer);
    var popover = _ensureDataSourcePopover();
    popover.classList.add('show');
    popover.setAttribute('aria-hidden', 'false');
    var anchor = document.getElementById('sse-status-indicator');
    if (anchor) anchor.setAttribute('aria-expanded', 'true');
    _renderDataSourceHealth(_dataSourceHealthCache || _healthFallbackItems('读取中'));
    _positionDataSourcePopover();
    _loadDataSourceHealth();
  }

  function _hideDataSourcePopoverSoon() {
    clearTimeout(_dataSourceHideTimer);
    _dataSourceHideTimer = setTimeout(function() {
      if (_dataSourcePopover) {
        _dataSourcePopover.classList.remove('show');
        _dataSourcePopover.setAttribute('aria-hidden', 'true');
      }
      var anchor = document.getElementById('sse-status-indicator');
      if (anchor) anchor.setAttribute('aria-expanded', 'false');
    }, 140);
  }

  function _initStatusIndicator() {
    const tb = document.getElementById('toolbar');
    if (!tb) return;
    if (document.getElementById('sse-status-indicator')) return;
    const el = document.createElement('span');
    el.id = 'sse-status-indicator';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-haspopup', 'true');
    el.setAttribute('aria-expanded', 'false');
    el.setAttribute('aria-describedby', 'sse-data-source-popover');
    el.style.cssText = 'cursor:default;user-select:none;flex-shrink:0';
    el.textContent = '';
    el.dataset.state = '';
    el.addEventListener('mouseenter', _showDataSourcePopover);
    el.addEventListener('mouseleave', _hideDataSourcePopoverSoon);
    el.addEventListener('focus', _showDataSourcePopover);
    el.addEventListener('blur', _hideDataSourcePopoverSoon);
    tb.appendChild(el);
    window.addEventListener('resize', _positionDataSourcePopover);
  }

  function _setSseStatus(state) {
    const el = document.getElementById('sse-status-indicator');
    if (!el) return;
    const label = _STATUS_LABELS[state] || '';
    if (el.dataset.state === state) return;
    el.dataset.state = state;
    el.textContent = label;
    el.setAttribute('aria-label', '实时推送：' + (label || '未知'));
    if (state === 'connected') el.style.color = '#26a69a';
    else if (state === 'frozen') el.style.color = '#ff9800';
    else if (state === 'disconnected') el.style.color = '#ef5350';
    else el.style.color = '#787b86';
  }

  // --- 按 scope 路由刷新 ---
  function _handleDataRefreshed(payload) {
    if (!payload || typeof payload !== 'object') return;
    var scope = payload.scope || 'all';
    var frozen = !!payload.frozen;

    // 处理 frozen：停止轮询，显示冻结状态
    if (frozen) {
      _sseFrozen = true;
      _setSseStatus('frozen');
      _stopFallbackPolling();
      if (payload.message) toast(payload.message);
      else toast('数据已冻结（盘后）');
      // 仍然刷新一次当前图，确保盘后数据到位
      _refreshChartByScope(scope, payload);
      return;
    }

    _sseFrozen = false;

    // 按 scope 决定刷新动作
    _refreshChartByScope(scope, payload);
  }

  function _refreshChartByScope(scope, payload) {
    // scope === "all" 或无 scope → 刷新 K 线图
    if (scope === 'all' || scope === 'indices' || scope === 'stocks') {
      _refreshCurrentChart();
    } else if (scope === 'boards') {
      // 板块列表页：重载分类数据并刷新导航树
      _refreshBoardList();
      // 同时也刷新当前图（如果正在看板块）
      if (store.selected && (store.selected.type === 'industry' || store.selected.type === 'concept')) {
        _refreshCurrentChart();
      }
    } else {
      // 不匹配的 scope → 仅更新状态指示（已在调用方设置）
      console.log('[SSE] scope=' + scope + ' 不匹配当前面板，跳过刷新');
    }
  }

  function _refreshCurrentChart() {
    clearTimeout(_sseRefreshTimer);
    _sseRefreshTimer = setTimeout(function() {
      if (_sseCompensating) return;
      // SSE 刷新：发统一刷新事件，由 ChartController 处理
      window.dispatchEvent(new CustomEvent('refresh-current-symbol', {
        detail: { source: 'sse', reason: 'data-refreshed' }
      }));
    }, 300);
  }

  function _refreshBoardList() {
    try {
      if (typeof loadClassification === 'function') {
        loadClassification().then(function() {
          if (typeof refreshAnnCounts === 'function') refreshAnnCounts();
        });
      }
      if (typeof loadBoardChanges === 'function') loadBoardChanges();
    } catch(e) { console.warn('[SSE] refreshBoardList error:', e); }
  }

  // --- 断线轮询降级 ---
  function _startFallbackPolling() {
    if (_fallbackTimer) return;
    _setSseStatus('fallback');
    console.log('[SSE] 启动降级轮询 (每' + (FALLBACK_INTERVAL/1000) + 's)');
    _fallbackTimer = setInterval(function() {
      if (_sseFrozen) return; // 冻结期间不轮询
      _pollSystemStatus();
    }, FALLBACK_INTERVAL);
  }

  function _stopFallbackPolling() {
    if (_fallbackTimer) {
      clearInterval(_fallbackTimer);
      _fallbackTimer = null;
    }
  }

  async function _pollSystemStatus() {
    try {
      var r = await fetch(API + '/api/system/status', { signal: AbortSignal.timeout ? AbortSignal.timeout(8000) : undefined });
      if (!r.ok) return;
      var j = await r.json();
      // 检查 frozen 状态
      var data = j.data || j;
      if (data.frozen) {
        if (!_sseFrozen) {
          _sseFrozen = true;
          _setSseStatus('frozen');
          toast('系统已冻结');
        }
        return;
      }
      // 轮询成功 → 尝试重新建立 SSE
      if (_sse && _sse.readyState === EventSource.CLOSED) {
        console.log('[SSE] 轮询检测到服务可用，尝试重连 SSE');
        _stopFallbackPolling();
        _sseRetryCount = 0;
        connectSSE();
      }
    } catch(e) {
      // 轮询失败静默
    }
  }

  // --- SSE 事件绑定 ---
  function _bindSseHandlers(es) {

    // === 新协议：data.refreshed（T04 契约，含 scope/frozen/updated_codes） ===
    es.addEventListener('data.refreshed', function(e) {
      console.log('[SSE] data.refreshed');
      _setSseStatus('connected');
      try {
        var payload = JSON.parse(e.data);
        if (payload.message) toast(payload.message);
        _handleDataRefreshed(payload);
      } catch(err) {
        console.warn('[SSE] data.refreshed parse error:', err);
        _refreshCurrentChart();
      }
    });

    // === data.frozen（单独推送冻结信号） ===
    es.addEventListener('data.frozen', function(e) {
      console.log('[SSE] data.frozen');
      _sseFrozen = true;
      _setSseStatus('frozen');
      _stopFallbackPolling();
      try {
        var payload = JSON.parse(e.data);
        toast((payload && payload.message) || '数据已冻结');
      } catch(err) {
        toast('数据已冻结');
      }
    });

    // === 旧协议兼容：data_updated ===
    es.addEventListener('data_updated', function(e) {
      console.log('[SSE] 数据已更新 (legacy)');
      _setSseStatus('connected');
      try {
        var payload = JSON.parse(e.data);
        if (payload && payload.message) {
          toast(payload.message);
        }
        var shouldRefresh = false;
        if (payload && store.selected) {
          if (payload.refresh_chart === true) {
            shouldRefresh = true;
          } else if (Object.prototype.hasOwnProperty.call(payload, 'codes')) {
            if (Array.isArray(payload.codes) && payload.codes.includes(store.selected.code)) {
              shouldRefresh = true;
            }
          } else {
            shouldRefresh = true;
          }
        }
        if (shouldRefresh) {
          _refreshCurrentChart();
        }
      } catch (err) {
        toast('数据已更新');
        _refreshCurrentChart();
      }
    });
    es.addEventListener('data_update_incomplete', function(e) {
      console.log('[SSE] 数据更新未完成');
      try {
        var payload = JSON.parse(e.data);
        toast((payload && payload.message) || '数据更新未完成，将重试');
      } catch (err) {
        toast('数据更新未完成，将重试');
      }
    });
    es.addEventListener('data_updating', function() { toast('数据更新中...'); });

    // === AI 事件 ===
    es.addEventListener('ai_result', function(e) {
      try {
        var d = JSON.parse(e.data);
        renderAiResult(d);
      } catch (err) { console.warn('ai_result parse error:', err); }
    });
    es.addEventListener('signals_updated', function(e) {
      try {
        var d = JSON.parse(e.data);
        if (store.selected && store.selected.code === d.board_code) {
          loadSignals(d.board_code);
        }
        if (d && d.signals && Array.isArray(d.signals) && d.board_code && store.selected && store.selected.code === d.board_code) {
          var list = document.getElementById('signal-list');
          if (list) {
            var h = '';
            d.signals.slice(-10).forEach(function(sg){
              var c = (sg.type||'').toLowerCase().includes('sos') ? 'buy' : 'sell';
              h += '<div class="signal-row"><span class="signal-date">'+(sg.date||'').slice(5)+'</span><span class="signal-badge '+c+'">'+(sg.type||'?')+'</span>'+(sg.note||'')+'</div>';
            });
            list.innerHTML = h || '<span style="color:#434651">暂无信号</span>';
          }
        }
      } catch (_) {}
    });
    es.addEventListener('task_progress', function(e) {
      try {
        var task = JSON.parse(e.data);
        if (task && task.status) {
          var msg = task.message || (task.status === 'finished' ? '任务完成' : task.status);
          if (task.status === 'failed') {
            showToastBar('任务失败: ' + msg);
          } else if (task.status === 'finished') {
            toast(msg);
          } else if (task.progress != null) {
            var stage = (task.detail && task.detail.stage) ? '[' + escHtml(task.detail.stage) + '] ' : '';
            showToastBar(stage + escHtml(msg) + ' ' + Math.round((task.progress||0)*100) + '%');
          }
        }
      } catch (err) {
        console.warn('[SSE] task_progress parse error:', err);
      }
    });
  }

  function _forwardSseEvents(es) {
    if (!_sseRelay) return;
    _SSE_EVENT_NAMES.forEach(function(eventName) {
      es.addEventListener(eventName, function(e) {
        try { _sseRelay.postMessage({ type: eventName, data: e.data }); } catch (_) {}
      });
    });
  }

  function _isSseLeader() {
    return !window.boardPollingLeader || window.boardPollingLeader.isLeader();
  }

  function _closeSse() {
    try { if (_sse) _sse.close(); } catch (_) {}
    _sse = null;
  }

  // 监听 WebSocket 任务推送（与 SSE 互补）
  window.addEventListener('rt-task', function(e){
    if (e.detail) {
      var task = e.detail;
      if (task.status === 'finished') {
        toast(task.message || '任务完成');
      } else if (task.status === 'failed') {
        showToastBar('任务失败: ' + (task.message || '未知错误'));
      }
    }
  });

  // --- SSE 连接/重连 ---
  function connectSSE() {
    if (!_isSseLeader()) {
      _closeSse();
      _stopFallbackPolling();
      _setSseStatus('shared');
      return;
    }
    if (_sse && _sse.readyState !== EventSource.CLOSED) return;
    _closeSse();
    var clientId = window.boardPollingLeader ? window.boardPollingLeader.tabId : '';
    _sse = new EventSource('/api/events?client_id=' + encodeURIComponent(clientId));
    _forwardSseEvents(_sse);
    _bindSseHandlers(_sse);
    _setSseStatus('reconnecting');

    _sse.onopen = function() {
      _setSseStatus('connected');
      _stopFallbackPolling();
      if (_sseRetryCount > 0) {
        console.log('[SSE] 重新连接成功');
        _sseCompensating = true;
        try {
          if (store.selected && store.selected.code) loadSignals(store.selected.code);
        } catch (_) {}
        setTimeout(function(){ _sseCompensating = false; }, 800);
      }
      _sseRetryCount = 0;
    };

    _sse.onerror = function() {
      console.warn('[SSE] 连接断开，尝试重连...');
      _closeSse();
      _setSseStatus('reconnecting');
      if (_sseRetryCount >= MAX_SSE_RETRY) {
        console.error('[SSE] 重试次数过多，降级到轮询');
        _setSseStatus('fallback');
        _startFallbackPolling();
        return;
      }
      var base = Math.min(1000 * Math.pow(2, _sseRetryCount), 30000);
      var delay = Math.floor(base * (0.5 + Math.random()));
      _sseRetryCount++;
      console.log('[SSE] ' + delay + 'ms后第' + _sseRetryCount + '次重连');
      setTimeout(connectSSE, delay);
    };
  }

  // --- 启动 ---
  _initStatusIndicator();
  _bindSseHandlers(_sseRelayTarget);
  connectSSE();
  setInterval(function() {
    if (_isSseLeader()) {
      if (!_sse || _sse.readyState === EventSource.CLOSED) connectSSE();
    } else if (_sse) {
      _closeSse();
      _stopFallbackPolling();
      _setSseStatus('shared');
    }
  }, 2000);
  window.addEventListener('beforeunload', function() {
    _closeSse();
    try { if (_sseRelay) _sseRelay.close(); } catch (_) {}
  });
  setTimeout(function() { if (store.selected) checkDataFreshness(); }, 2000);
})();

// ==== 数据新鲜度检查 ====
function _localYmd(d){
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  return y+'-'+m+'-'+day;
}
async function checkDataFreshness(){
  if (!store.selected) return;
  try {
    const r = await fetch(API+'/api/kline/'+store.selected.type+'/'+store.selected.code
      +'?name='+encodeURIComponent(store.selected.name)+'&period=daily&timeout=5&cache_first=1');
    const j = await r.json();
    if (j.last_date) {
      const today = new Date();
      const dow = today.getDay(); // 0=日 … 6=六
      const isWeekend = dow === 0 || dow === 6;
      const lastDate = new Date(j.last_date.replace(/-/g,'/') + ' 00:00:00');
      const diffDays = (today - lastDate) / 86400000;
      // 周末/节假日用日历「今天」比对会误报；仅工作日收盘后提示
      if (diffDays > 5) {
        toast('⚠️ 数据可能已过期（最后更新：'+j.last_date+'）');
      } else if (!isWeekend && j.last_date !== _localYmd(today) && today.getHours() >= 15) {
        showToastBar('数据可能不是最新的，点击 <a href="#" onclick="forceRefresh()">强制刷新</a>');
      }
    }
  } catch(e) {}
}

// ==== 强制刷新轮询（推送为主 + 8秒兜底轮询） ====
function _truncDebtSummary(s, maxLen){
  s = String(s == null ? '' : s);
  maxLen = maxLen || 120;
  if(s.length > maxLen) return s.slice(0, maxLen - 3) + '...';
  return s;
}

function _reloadSelectedChart(){
  try {
    // 强制刷新：发统一刷新事件，由 ChartController 处理
    window.dispatchEvent(new CustomEvent('refresh-current-symbol', {
      detail: { source: 'sse-reload', reason: 'force-reload' }
    }));
  } catch(e) {}
  // 指数条即时重拉
  try {
    if(typeof refreshIdxPrices === 'function') refreshIdxPrices();
  } catch(e) {}
}

function pollUpdateTask(taskId){
  var pollCount = 0;
  var MAX_POLL = 15; // 即时 force 数秒内结束
  var POLL_INTERVAL = 800;
  var timer = setInterval(function(){
    pollCount++;
    if(pollCount > MAX_POLL){
      clearInterval(timer);
      return;
    }
    fetch(API+'/api/tasks/'+taskId)
      .then(function(r){return r.json()})
      .then(function(j){
        if(!j.ok || !j.task) { clearInterval(timer); return; }
        var t = j.task;
        if(t.status === 'running' || t.status === 'pending'){
          return;
        }
        clearInterval(timer);
        if(t.status === 'success' || t.status === 'done'){
          var d = t.detail || {};
          var msg = t.message || '界面已即时刷新';
          if(d.background_catchup){
            toast(_truncDebtSummary(msg, 140) || '已即时刷新；后台补齐欠更中');
          } else if(d.full_already_running){
            toast('后台日更进行中，界面已即时刷新');
          } else {
            toast(_truncDebtSummary(msg, 120) || '界面已即时刷新');
          }
          _reloadSelectedChart();
        } else if(t.status === 'canceled'){
          showToastBar('数据更新已取消');
        } else if(t.status === 'failed'){
          showToastBar('数据更新失败：'+escHtml(t.error||t.message||t.status));
        }
      })
      .catch(function(){});
  }, POLL_INTERVAL);
}

// 工具栏 ↻：即时刷新（清缓存+现价），欠更时后台补齐，不阻塞 UI
function forceRefresh(){
  if(typeof closeCtx==='function') closeCtx();
  if (_datafeed && _datafeed._cache) _datafeed._cache.clear();
  // 同时清成分缓存，让左侧板块涨幅和成分列表立即重拉
  try { if (typeof _consCache !== 'undefined' && _consCache && _consCache.clear) _consCache.clear(); } catch(_){}
  toast('正在即时刷新...');
  // 1) 板块快照强制刷新（盘中立即拉东财实时；盘后空操作）
  var snapP = fetch(API+'/api/snapshot/refresh', {method:'POST'})
    .then(function(r){
      return r.json().then(function(j){
        if(!r.ok || !j || !j.ok) throw new Error((j && j.message) || ('HTTP '+r.status));
        return j;
      });
    })
    .then(function(j){
      if(j && j.ok){
        // 板块涨幅刷新：触发左侧 board-changes 重拉
        try { if (typeof loadBoardChanges === 'function') loadBoardChanges(); } catch(_){}
        // 若当前正展开成分面板 → 重拉成分
        try { if (typeof consDoRefresh === 'function') consDoRefresh(); } catch(_){}
      }
    }).catch(function(e){
      showToastBar('板块数据刷新失败：'+escHtml((e && e.message) || '未知错误'));
    }).finally(function(){
      // 即使快照端点暂时不可用，也必须重新读取现有服务端数据，不能把
      // 左侧面板永久留在旧快照。
      try { if (typeof loadBoardChanges === 'function') loadBoardChanges(); } catch(_){}
      try { if (typeof consDoRefresh === 'function') consDoRefresh(); } catch(_){}
    });
  if(_taskCenterOpen){
    setTimeout(function(){ try{ renderTaskCenter(); }catch(e){} }, 400);
  }
  // 2) 触发原有后台 force update（个股历史补齐）
  var taskP = fetch(API+'/api/tasks/update/force', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if(!j.ok || !j.task){
        return;
      }
      var t = j.task;
      if(t.status === 'success' || t.status === 'done'){
        var d = t.detail || {};
        toast(_truncDebtSummary(t.message || '界面已即时刷新', 140));
        _reloadSelectedChart();
        if(d.background_catchup){
          // 后台补齐不阻塞
        }
      } else {
        pollUpdateTask(t.id);
      }
    })
    .catch(function(){});
  // 等两个都触发后，立即重绘当前图（不等 task 完成）
  Promise.all([snapP, taskP]).then(function(){
    _reloadSelectedChart();
  }).catch(function(){});
  return snapP;
}

(function(){
  const tb = document.getElementById('toolbar');
  if(!tb) return;
  const btn = document.createElement('button');
  btn.id = 'refresh-btn';
  btn.innerHTML = '↻';
  btn.title = '强制刷新数据';
  btn.style.cssText = 'padding:0 8px;border:none;background:transparent;color:#787b86;font-size:14px;cursor:pointer;flex-shrink:0';
  btn.onclick = forceRefresh;
  btn.onmouseover = function(){this.style.color='#d1d4dc'};
  btn.onmouseout = function(){this.style.color='#787b86'};
  tb.appendChild(btn);
})();

// 页面加载后静默检查欠更（当天只提示一次）
(function(){
  setTimeout(function(){
    var d = new Date();
    var m = d.getMonth() + 1, day = d.getDate();
    var key = 'debt_hint_' + d.getFullYear() +
      (m < 10 ? '0' : '') + m + (day < 10 ? '0' : '') + day;
    try {
      if(sessionStorage.getItem(key)) return;
    } catch(e) { return; }
    fetch(API+'/api/update/debt')
      .then(function(r){
        if(!r.ok) throw new Error('debt ' + r.status);
        return r.json();
      })
      .then(function(resp){
        var debt = (resp && resp.debt) || {};
        if(!debt.needs_catchup) return;
        var stocks = debt.stocks || {};
        var lagging = stocks.lagging || 0;
        if(lagging <= 50) return;
        var summary = _truncDebtSummary(debt.summary || ('stocks 欠更 ' + lagging), 80);
        toast('数据有欠更：' + summary + '（点 ↻ 即时刷新，后台自动补齐）');
        try { sessionStorage.setItem(key, '1'); } catch(e) {}
      })
      .catch(function(){});
  }, 3000);
})();

// ===== AI 分析结果面板 =====
function renderAiResult(r){
  if(!r || typeof r !== 'object') return;
  const panel = document.getElementById('ai-result-panel');
  const content = document.getElementById('ai-result-content');
  if(!panel || !content) return;
  const dir = r.direction || r.dir || 'neutral';
  const dirClass = dir === 'bullish' ? 'bullish' : dir === 'bearish' ? 'bearish' : 'neutral';
  const dirText = dir === 'bullish' ? '看多' : dir === 'bearish' ? '看空' : '中性';
  const conf = Math.round((r.confidence != null ? r.confidence : 0) * 100);
  const summary = r.summary || r.reasoning || r.text || '';
  const supports = Array.isArray(r.supports) ? r.supports : (Array.isArray(r.support) ? r.support : []);
  const resistances = Array.isArray(r.resistances) ? r.resistances : (Array.isArray(r.resistance) ? r.resistance : []);
  const suggestion = r.suggestion || r.recommendation || r.action || '';
  const anns = Array.isArray(r.annotations) ? r.annotations : [];
  const boardName = r.board_name || r.name || (store.selected ? store.selected.name : '');
  const boardCode = r.board_code || r.code || (store.selected ? store.selected.code : '');
  let h = '<div class="ai-result-row"><span>标的</span><span>' + (escHtml(boardName) || '—') + ' <span class="ai-result-dir ' + dirClass + '">' + dirText + '</span></span></div>';
  h += '<div class="ai-result-row"><span>置信度</span><span>' + conf + '%</span></div>';
  if(summary) h += '<div class="ai-result-label">摘要</div><div class="ai-result-summary">' + escHtml(summary) + '</div>';
  if(supports.length){ h += '<div class="ai-result-label">支撑位</div><div class="ai-result-levels">' + supports.map(v=>'<span class="ai-result-level">' + escHtml(String(v)) + '</span>').join('') + '</div>'; }
  if(resistances.length){ h += '<div class="ai-result-label">阻力位</div><div class="ai-result-levels">' + resistances.map(v=>'<span class="ai-result-level">' + escHtml(String(v)) + '</span>').join('') + '</div>'; }
  if(suggestion) h += '<div class="ai-result-label">建议</div><div class="ai-result-summary">' + escHtml(String(suggestion)) + '</div>';
  h += '<div class="ai-result-row"><span>标注数</span><span>' + anns.length + '</span></div>';
  h += '<div class="ai-result-actions"><button class="ai-result-btn" onclick="clearAiResult()">清除结果</button>';
  if(anns.length){ _aiLastAnnotations = anns; h += '<button class="ai-result-btn" data-action="draw-annotations">绘制标注</button>'; }
  h += '</div>';
  content.innerHTML = h;
  panel.classList.add('show');
  // 保存记录
  saveAnalysisRecord(r);
  // 自动绘制标注
  if(anns.length){ try{ drawAiAnnotations(anns); }catch(e){ console.warn('auto draw annotations failed:', e); } }
}

function _clearAiAnnOverlays(chart){
  if(!chart) return;
  try{
    const ids = [];
    const tryCollect = function(store){
      if(!store || typeof store.getInstances !== 'function') return;
      const insts = store.getInstances() || [];
      for(let i=0;i<insts.length;i++){
        const o = insts[i];
        const id = o && (o.id || (typeof o.getId==='function' ? o.getId() : '') || '');
        if(String(id).indexOf('ai_ann_') === 0) ids.push(id);
      }
    };
    if(typeof chart.getOverlayStore === 'function') tryCollect(chart.getOverlayStore());
    if(typeof chart.getChartStore === 'function'){
      const cs = chart.getChartStore();
      if(cs && typeof cs.getOverlayStore === 'function') tryCollect(cs.getOverlayStore());
    }
    for(let i=0;i<ids.length;i++){
      try{
        if(typeof chart.removeOverlay === 'function') chart.removeOverlay({ id: ids[i] });
      }catch(_){}
    }
  }catch(e){ console.warn('[AI] clear ai overlays failed:', e); }
}

function drawAiAnnotations(annotations){
  if(!Array.isArray(annotations) || !annotations.length) return;
  if(!pro){ console.warn('[AI] pro not ready, skip annotations'); return; }
  let chart = null;
  try{ chart = window.__kline_chart || (pro._chart ? pro._chart : null); }catch(e){}
  if(!chart){ console.warn('[AI] underlying chart not available'); return; }
  _clearAiAnnOverlays(chart);
  let idx = 0;
  for(const ann of annotations){
    try{
      if(!ann || !ann.type) continue;
      const pts = [];
      if(ann.timestamp && ann.value != null) pts.push({ timestamp: Number(ann.timestamp), value: Number(ann.value) });
      else if(ann.time && ann.price != null) pts.push({ timestamp: Number(ann.time), value: Number(ann.price) });
      else if(ann.timestamps && ann.values && ann.timestamps.length === ann.values.length){
        for(let i=0;i<ann.timestamps.length;i++) pts.push({ timestamp: Number(ann.timestamps[i]), value: Number(ann.values[i]) });
      }
      if(!pts.length) continue;
      const oid = 'ai_ann_' + (ann.id != null ? String(ann.id) : String(idx));
      idx++;
      const overlayOpts = { name: ann.type, points: pts, id: oid };
      if(ann.styles) overlayOpts.styles = ann.styles;
      if(typeof chart.createOverlay === 'function'){
        chart.createOverlay(overlayOpts);
      }else if(pro && typeof pro.createOverlay === 'function'){
        pro.createOverlay(overlayOpts);
      }
    }catch(e){ console.warn('[AI] draw annotation failed:', e); }
  }
}

function saveAnalysisRecord(record){
  if(!record || typeof record !== 'object') return;
  // 防护：replay 触发的记录不重复保存
  if(record._fromHistory) return;
  try{
    // 防护：通过 id 去重
    const rid = record.id || record.record_id;
    if(rid && _analysisHistory.some(r => r.id === rid)) return;
    const entry = Object.assign({}, record, { _fromHistory: false, savedAt: new Date().toISOString() });
    if(!rid) entry.id = 'ai_' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
    _analysisHistory.unshift(entry);
    if(_analysisHistory.length > 50) _analysisHistory = _analysisHistory.slice(0, 50);
    localStorage.setItem(_historyKey, JSON.stringify(_analysisHistory));
  }catch(e){ console.warn('[AI] save record failed:', e); }
}

function clearAiResult(){
  const panel = document.getElementById('ai-result-panel');
  if(panel) panel.classList.remove('show');
  // 移除 AI 标注 overlay
  try{
    const chart = window.__kline_chart || (pro && pro._chart ? pro._chart : null);
    if(chart){
      const store = (typeof chart.getChartStore === 'function') ? chart.getChartStore() : null;
      const ovStore = store && typeof store.getOverlayStore === 'function' ? store.getOverlayStore() : null;
      if(ovStore && typeof ovStore.getInstances === 'function'){
        const insts = ovStore.getInstances() || [];
        for(const o of insts){
          const nm = o.name || (typeof o.getName === 'function' ? o.getName() : '') || '';
          if(String(nm).indexOf('ai_') === 0 || String(nm).indexOf('annotation') === 0){
            try{ if(typeof ovStore.removeOverlay === 'function') ovStore.removeOverlay(o.id || o.getId()); }catch(e){}
          }
        }
      }
    }
  }catch(e){ console.warn('[AI] remove overlay failed:', e); }
  // 通知后端清除
  if(store.selected && store.selected.code){
    fetch(API + '/api/ai/result/' + encodeURIComponent(store.selected.code) + '/clear', { method: 'POST' }).catch(()=>{});
  }
}

// AI 结果面板关闭按钮
(function(){
  const btn = document.getElementById('ai-result-close');
  if(btn && !btn.dataset.bound){ btn.dataset.bound = '1'; btn.addEventListener('click', clearAiResult); }
})();

// AI 结果面板绘制标注按钮（事件委托，避免内联 onclick 注入风险）
(function(){
  var content = document.getElementById('ai-result-content');
  if(content && !content.dataset.drawBound){
    content.dataset.drawBound = '1';
    content.addEventListener('click', function(e){
      var btn = e.target.closest('[data-action="draw-annotations"]');
      if(btn && _aiLastAnnotations){ try{ drawAiAnnotations(_aiLastAnnotations); }catch(e){ console.warn('draw annotations failed:', e); } }
    });
  }
})();

// ===== 数据更新任务中心 =====
let _taskCenterOpen = false;

function renderTaskCenter(){
  const panel = document.getElementById('task-center');
  const body = document.getElementById('task-center-body');
  if(!panel || !body) return;
  _taskCenterOpen = panel.classList.contains('show');
  body.innerHTML = '<div class="task-empty">加载中...</div>';
  panel.classList.add('show');
  _taskCenterOpen = true;
  fetch(API + '/api/tasks?limit=10')
    .then(r=>r.json())
    .then(j=>{
      if(!j || !j.ok){ body.innerHTML = '<div class="task-empty">加载失败</div>'; return; }
      const tasks = j.tasks || [];
      if(!tasks.length){ body.innerHTML = '<div class="task-empty">暂无任务</div>'; return; }
      let h = '';
      tasks.forEach(t => {
        const st = t.status || 'unknown';
        const stText = st === 'running' ? '运行中' : st === 'pending' ? '等待中' : st === 'success' ? '已完成' : st === 'canceled' ? '已取消' : st === 'failed' ? '失败' : st;
        const pct = Math.round((t.progress || 0) * 100);
        const msg = t.message || t.detail && t.detail.description || '';
        const canCancel = (st === 'running' || st === 'pending');
        h += '<div class="task-item">';
        const stCls = ['running','pending','success','canceled','failed'].includes(st) ? st : 'unknown';
        h += '<div class="task-top"><span class="task-id">' + escHtml(t.id ? t.id.slice(0,12) : '?') + '</span><span class="task-status ' + stCls + '">' + stText + '</span></div>';
        if(msg) h += '<div class="task-msg">' + escHtml(String(msg)) + '</div>';
        h += '<div class="task-progress"><div class="task-progress-bar"><div class="task-progress-fill" style="width:' + pct + '%"></div></div><span class="task-progress-pct">' + pct + '%</span></div>';
        if(canCancel) h += '<div class="task-actions"><button class="task-btn cancel" onclick="cancelTask(\'' + escAttr(t.id) + '\')">取消</button></div>';
        h += '</div>';
      });
      body.innerHTML = h;
    })
    .catch(()=>{ body.innerHTML = '<div class="task-empty">加载失败</div>'; });
}

function cancelTask(taskId){
  if(!taskId) return;
  fetch(API + '/api/tasks/' + encodeURIComponent(taskId) + '/cancel', { method: 'POST' })
    .then(r=>r.json())
    .then(j=>{
      if(j && j.ok){ toast('任务已取消'); renderTaskCenter(); }
      else toast(j && j.message || '取消失败');
    })
    .catch(()=>toast('取消请求失败'));
}

// 任务中心关闭按钮
(function(){
  const btn = document.getElementById('task-center-close');
  if(btn && !btn.dataset.bound){ btn.dataset.bound = '1'; btn.addEventListener('click', function(){ const p=document.getElementById('task-center'); if(p) p.classList.remove('show'); _taskCenterOpen = false; }); }
})();

// 工具栏添加任务中心按钮
(function(){
  const tb = document.getElementById('toolbar');
  if(!tb) return;
  if(document.getElementById('task-center-btn')) return;
  const btn = document.createElement('button');
  btn.id = 'task-center-btn';
  btn.innerHTML = '☰';
  btn.title = '数据更新任务中心';
  btn.style.cssText = 'padding:0 8px;border:none;background:transparent;color:#787b86;font-size:14px;cursor:pointer;flex-shrink:0';
  btn.onclick = renderTaskCenter;
  btn.onmouseover = function(){this.style.color='#d1d4dc'};
  btn.onmouseout = function(){this.style.color='#787b86'};
  tb.appendChild(btn);
})();

// HTML 转义辅助（toast-modal.js 已定义 escHtml/escAttr，此处不再重复）
