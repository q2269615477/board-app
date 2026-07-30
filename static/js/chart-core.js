// ===== 3. KLineChart Pro Datafeed（带请求取消+超时控制） =====
const KLINE_FETCH_TIMEOUT_MS = 15000;
const KLINE_API_TIMEOUT_SEC = 5;
class BoardDatafeed {
  constructor(){
    this._timer=null;
    this._activeReqs=new Map();
    this._seq=0;
    this._activeSymbolKey='';
    // 全量序列缓存（带 TTL）：避免 Pro 多次 getHistory 重拉/拼接，同时防止永久缓存旧数据
    // 每条记录: { data: [], ts: Date.now() }，TTL = 5 分钟
    this._cache=new Map();
    this._CACHE_TTL = 60 * 1000; // 1分钟（日线另用更短 ttl）
    // LRU 热缓存顺序（最近使用的在末尾），切换回最近看过的标的优先内存命中
    this._hotOrder = [];
    this._HOT_MAX = 10;
  }
  // 清除某 ticker 下所有缓存条目（切换标的时调用）
  // 同时中止该 ticker 进行中的请求，避免旧响应覆盖新标的
  clearByTicker(ticker){
    for(const k of [...this._cache.keys()]){
      if(k.startsWith(ticker+':')) this._cache.delete(k);
    }
    for(const [k, ctrl] of [...this._activeReqs.entries()]){
      if(k.startsWith(ticker+':')){ ctrl.abort(); this._activeReqs.delete(k); }
    }
    // 同时清理 LRU 热缓存
    this._hotOrder = this._hotOrder.filter(k => !k.startsWith(ticker+':'));
  }
  // 更新 LRU 热缓存顺序（最近使用的移至末尾，超出容量时从头部淘汰）
  abortByTicker(ticker){
    for(const [k, ctrl] of [...this._activeReqs.entries()]){
      if(k.startsWith(ticker+':')){ ctrl.abort(); this._activeReqs.delete(k); }
    }
  }
  abortAllRequests(){
    for(const [k, ctrl] of [...this._activeReqs.entries()]){
      ctrl.abort();
      this._activeReqs.delete(k);
    }
  }
  hasFreshDailyCache(ticker){
    const cacheKey = ticker + ':day:1:daily';
    const cached = this._cache.get(cacheKey);
    if(!cached) return false;
    const ttl = cached.ttl != null ? cached.ttl : this._CACHE_TTL;
    return (Date.now() - cached.ts) < ttl && Array.isArray(cached.data) && cached.data.length > 0;
  }
  _updateHotOrder(key){
    const idx = this._hotOrder.indexOf(key);
    if (idx >= 0) this._hotOrder.splice(idx, 1);
    this._hotOrder.push(key);
    while (this._hotOrder.length > this._HOT_MAX) this._hotOrder.shift();
  }
  // 通知 UI 当前请求结果（成功/失败/空），供外部监听 kline-loaded / kline-error
  _notifyKlineResult(symbol, period, ok, count, error){
    try{
      const detail = { symbol: symbol.ticker, period, ok: !!ok, count: count || 0, ts: Date.now() };
      if(!ok && error) detail.error = String(error).slice(0,120);
      window.dispatchEvent(new CustomEvent('kline-loaded', { detail }));
      if(!ok && error){
        window.dispatchEvent(new CustomEvent('kline-error', { detail }));
      }
    }catch(e){}
  }
  async searchSymbols(search) {
    if(!store.categoryData) return [];
    const results = [];
    const seen = new Set();
    const pushBoard = (b) => {
      if (!b || !b.code || seen.has(b.code)) return;
      if (search && !String(b.name || '').includes(search) && !String(b.code).includes(search)) return;
      seen.add(b.code);
      results.push({
        ticker: b.code, name: b.name, shortName: b.name,
        type: b.type, market: 'A', exchange: '中国', priceCurrency: 'CNY'
      });
    };
    // v5 分类结构是 cat.subcategories[].boards；旧结构是 cat.boards。
    // 原实现只读 cat.boards，v5 下为 undefined → .forEach 抛错 → Pro 内置搜索全废。
    store.categoryData.forEach(cat => {
      if (Array.isArray(cat.boards)) cat.boards.forEach(pushBoard);
      if (Array.isArray(cat.subcategories)) {
        cat.subcategories.forEach(sub => {
          if (Array.isArray(sub.boards)) sub.boards.forEach(pushBoard);
        });
      }
    });
    return results;
  }
  _periodToApi(p) {
    const t=p.timespan, m=p.multiplier;
    if(t==='minute') return m+'m';
    if(t==='hour') return (m*60)+'m';
    if(t==='day') return 'daily';
    if(t==='week') return 'weekly';
    if(t==='month'){if(m===1)return 'monthly';if(m===3)return 'quarterly';if(m===12)return 'yearly';return 'monthly';}
    return 'daily';
  }
  _normalizeTs(v) {
    const n = Number(v);
    if (!isFinite(n) || n <= 0) return null;
    // 秒级 < 1e10，毫秒级 >= 1e10
    return n < 1e10 ? n * 1000 : n;
  }
  _dedupeSort(rows) {
    rows.sort((a, b) => a.timestamp - b.timestamp);
    const seen = new Set();
    return rows.filter(d => {
      if (seen.has(d.timestamp)) return false;
      seen.add(d.timestamp);
      return true;
    });
  }
  async getHistoryKLineData(symbol, period, from, to) {
    if (!symbol || !symbol.ticker) return [];
    let stype = symbol.type;
    if (!stype) {
      const tk = symbol.ticker || '';
      if (tk.startsWith('BK') || tk === '800000') {
        stype = 'concept';
      } else if (tk.startsWith('sh') || tk.startsWith('sz') || tk.startsWith('bj') || tk.startsWith('^') || tk === 'HSI' || tk === 'HSTECH' || tk === 'SPX' || tk === 'IXIC' || tk === 'DJI') {
        stype = 'index';
      } else {
        stype = 'stock';
      }
      symbol.type = stype;
    }
    // 取消同symbol+period的旧请求
    const symKey = symbol.ticker+':'+period.timespan+':'+period.multiplier;
    const symbolKey = symbol.ticker+':'+(symbol.type||'');
    this._activeSymbolKey = symbolKey;
    if(this._activeReqs.has(symKey)){this._activeReqs.get(symKey).abort();}
    const ctrl = new AbortController();
    this._activeReqs.set(symKey, ctrl);
    const p = this._periodToApi(period);
    const cacheKey = symKey + ':' + p;
    const tid = setTimeout(()=>ctrl.abort(), KLINE_FETCH_TIMEOUT_MS);
    try {
      let rows = [];
      // 优先缓存（带 TTL 检测）：Pro 会多次回调；无缓存/过期时重拉
      const cached = this._cache.get(cacheKey);
      var hitTtl = (cached && cached.ttl != null) ? cached.ttl : this._CACHE_TTL;
      if (cached && (Date.now() - cached.ts) < hitTtl) {
        rows = cached.data.slice();
        // 更新 LRU 热缓存
        this._updateHotOrder(cacheKey);
      } else {
        // 缓存失效/不存在 → 删除旧条目并重新请求后端
        // 缓存优先：cache_first → 优先返回旧数据、后台刷新
        this._cache.delete(cacheKey);
        const r = await fetch(API+'/api/kline/'+(symbol.type||'stock')+'/'+encodeURIComponent(symbol.ticker)
          +'?name='+encodeURIComponent(symbol.name||symbol.shortName||'')+'&period='+p
          +'&timeout='+KLINE_API_TIMEOUT_SEC+'&cache_first=1', {signal:ctrl.signal});
        clearTimeout(tid);
        this._activeReqs.delete(symKey);
        if(this._activeSymbolKey!==symbolKey) return [];  // 已切到其他标的，丢弃旧结果
        const j = await r.json();
        if(j.loading){ this._notifyKlineResult(symbol, period, false, 0, 'loading'); return []; }
        if(j.error){
          console.warn('kline data error:',j.error);
          this._notifyKlineResult(symbol, period, false, 0, j.error);
          // D. 错误提示：让用户知道失败
          try{
            if(typeof showToastBar==='function'){
              showToastBar('K线数据加载失败: '+ (j.error ? String(j.error).slice(0,60) : '未知错误'));
            } else if(typeof toast==='function'){
              toast('K线数据加载失败');
            }
          }catch(e){}
          return [];
        }
        rows = (j.data||[]).map(d=>({
          timestamp:d.timestamp,open:d.open,high:d.high,low:d.low,close:d.close,volume:d.volume||0
        }));
        // 盘中 OHLC overlay：用后端 intraday 覆盖末 bar（不写库）
        if(j.intraday && rows.length){
          var iv = j.intraday;
          var last = Object.assign({}, rows[rows.length-1]);
          if(iv.open!=null) last.open = Number(iv.open);
          if(iv.high!=null) last.high = Number(iv.high);
          if(iv.low!=null) last.low = Number(iv.low);
          if(iv.close!=null) last.close = Number(iv.close);
          rows[rows.length-1] = last;
        }
        rows = this._dedupeSort(rows);
        // TTL 按 symbol.type 区分：BK(industry/concept) 走快照，日内不变 → 60s；
        // stock/index 末 bar 实时 → 15s；其它周期保留 1min。
        var _symType = symbol.type || '';
        var _isBK = (_symType === 'industry' || _symType === 'concept');
        var _isIntradayLive = (_symType === 'stock' || _symType === 'index');
        var ttl;
        if (p === 'daily' || p === '1d') {
          ttl = _isBK ? 60000 : (_isIntradayLive ? 15000 : 30000);
        } else {
          ttl = this._CACHE_TTL;
        }
        this._cache.set(cacheKey, { data: rows.slice(), ts: Date.now(), ttl: ttl });
        // stale 后台刷新：静默更新缓存（不 setSymbol，避免 abort/加载中循环）
        if(j.stale && j.background_refresh_started && (symbol.type==='stock' || symbol.type==='index')){
          var self = this;
          var reSym = symbol; var reSymbolKey = symbolKey; var reKey = cacheKey; var reP = p;
          setTimeout(function(){
            if(self._activeSymbolKey !== reSymbolKey) return;
            var url2 = API+'/api/kline/'+(reSym.type||'industry')+'/'+reSym.ticker
              +'?name='+encodeURIComponent(reSym.name||reSym.shortName||'')+'&period='+reP
              +'&timeout='+KLINE_API_TIMEOUT_SEC;
            fetch(url2).then(function(r2){ return r2.json(); }).then(function(j2){
              if(self._activeSymbolKey !== reSymbolKey) return;
              if(!j2 || j2.error || !j2.data || !j2.data.length) return;
              var rows2 = j2.data.map(function(d){
                return {timestamp:d.timestamp,open:d.open,high:d.high,low:d.low,close:d.close,volume:d.volume||0};
              });
              if(j2.intraday && rows2.length){
                var iv2 = j2.intraday; var last2 = Object.assign({}, rows2[rows2.length-1]);
                if(iv2.open!=null) last2.open = Number(iv2.open);
                if(iv2.high!=null) last2.high = Number(iv2.high);
                if(iv2.low!=null) last2.low = Number(iv2.low);
                if(iv2.close!=null) last2.close = Number(iv2.close);
                rows2[rows2.length-1] = last2;
              }
              rows2 = self._dedupeSort(rows2);
              self._cache.set(reKey, { data: rows2.slice(), ts: Date.now(), ttl: ttl });
            }).catch(function(){});
          }, 1500);
        }
      }
      // from/to：Pro 多为秒级，数据为毫秒
      if (rows.length && from != null && to != null) {
        const f = this._normalizeTs(from);
        const t = this._normalizeTs(to);
        if (f !== null && t !== null) {
          const lo = Math.min(f, t);
          const hi = Math.max(f, t);
          rows = rows.filter(d => d.timestamp >= lo && d.timestamp <= hi);
        }
      }
      rows = this._dedupeSort(rows);
      this._notifyKlineResult(symbol, period, true, rows.length);
      return rows;
    } catch(e) {
      clearTimeout(tid);
      this._activeReqs.delete(symKey);
      // 被更新请求/切标的 abort：静默，禁止误报「超时/失败」
      if(e && e.name==='AbortError'){
        if(this._activeSymbolKey !== symbolKey) return [];
        console.warn('kline fetch timeout for', symbol.ticker);
        this._notifyKlineResult(symbol, period, false, 0, 'timeout');
        try{
          if(typeof showToastBar==='function'){
            showToastBar('K线数据加载超时: '+symbol.ticker);
          } else if(typeof toast==='function'){
            toast('K线数据加载超时');
          }
        }catch(_e){}
        return [];
      }
      if(this._activeSymbolKey !== symbolKey) return [];
      this._notifyKlineResult(symbol, period, false, 0, (e && e.message) || 'network error');
      try{
        if(typeof showToastBar==='function'){
          showToastBar('K线数据请求失败: '+symbol.ticker);
        } else if(typeof toast==='function'){
          toast('K线数据请求失败');
        }
      }catch(_e){}
      return [];
    }
  }
  subscribe(symbol, period, callback) {
    clearInterval(this._timer);
    let _lastPushedPrice = 0;       // 去重：仅在价格变化时推送
    let _lastTradingTimestamp = 0;  // 固定时间戳：始终用最新交易日

    const doFetch = async () => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(()=>ctrl.abort(), 5000);
        let stype = symbol.type;
        if (!stype) {
          const tk = symbol.ticker || '';
          if (tk.startsWith('BK') || tk === '800000') {
            stype = 'concept';
          } else if (tk.startsWith('sh') || tk.startsWith('sz') || tk.startsWith('bj') || tk.startsWith('^') || tk === 'HSI' || tk === 'HSTECH' || tk === 'SPX' || tk === 'IXIC' || tk === 'DJI') {
            stype = 'index';
          } else {
            stype = 'stock';
          }
        }
        const r = await fetch(API+'/api/spot/'+stype+'/'+symbol.ticker,{signal:ctrl.signal});
        clearTimeout(t);
        if(!r.ok) return;
        const j = await r.json();
        const data = j.data && j.data.price ? j.data : null;
        if(data) {
          if (!_lastTradingTimestamp) {
            const chart = pro && pro._chart;
            const lastData = chart && chart.getDataList && chart.getDataList();
            if (lastData && lastData.length) {
              _lastTradingTimestamp = lastData[lastData.length-1].timestamp;
            }
          }
          const ts = _lastTradingTimestamp;
          if (!ts) return; // 尚无有效交易日时间戳，跳过推送（避免产生虚假新Bar损坏图表）
          if (Math.abs(data.price - _lastPushedPrice) > 0.0001) {
            _lastPushedPrice = data.price;
            callback({timestamp:ts,close:data.price,open:data.open||data.price,high:data.high||data.price,low:data.low||data.price,volume:data.volume||0});
          }
        }
      } catch(e) { if(e.name!=='AbortError') console.warn('spot poll err:',e); }
    };

    doFetch();
    this._timer = setInterval(doFetch, 3000);
  }
  unsubscribe(symbol, period) { clearInterval(this._timer); }
}
const _datafeed = new BoardDatafeed();

// 监听 WebSocket K线就绪推送：仅清理当前标的缓存，后台静默更新
window.addEventListener('rt-kline-ready', function(e){
  if (!e.detail) return;
  var meta = e.detail;
  if (store.selected && store.selected.code === meta.code) {
    // 当前标的静默更新：发统一刷新事件，由 ChartController 处理
    window.dispatchEvent(new CustomEvent('refresh-current-symbol', {
      detail: { code: meta.code, source: 'rt-kline-ready', reason: 'data-refreshed' }
    }));
  }
});


// ===== 4. 初始化 KLineChart Pro（带CDN重试机制） =====
let _initProAttempts = 0;
const MAX_INIT_ATTEMPTS = 20; // 最多重试20次（约10秒）

function initPro() {
  const container = document.getElementById('pro-container');
  if (!container) return false;
  
  // CDN未加载 → 显示等待UI并自动重试
  if (!window.klinechartspro) {
    _initProAttempts++;
    if (_initProAttempts >= MAX_INIT_ATTEMPTS) {
      // 最终失败：显示错误UI + 重试按钮
      container.innerHTML = '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;color:#ef5350;font-size:13px">'+
        '<div style="font-size:28px;margin-bottom:12px">📊</div>'+
        '<div>KLineChart Pro 加载失败</div>'+
        '<div style="font-size:11px;color:#787b86;margin:8px 0">CDN资源可能被网络代理拦截</div>'+
        '<button onclick="forceReloadPro()" style="padding:6px 16px;border:1px solid #ef5350;border-radius:4px;background:transparent;color:#ef5350;cursor:pointer;font-size:12px">重新加载</button>'+
        '</div>';
      console.error('[Pro] CDN加载失败，已重试', MAX_INIT_ATTEMPTS, '次');
      return false;
    }
    // 显示等待状态
    if (_initProAttempts <= 3) {
      container.innerHTML = '<div class="placeholder" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#434651;font-size:14px;text-align:center">'+
        '<div style="font-size:24px;margin-bottom:8px">⏳</div>'+
        '正在加载KLineChart Pro...<br><span style="font-size:10px">('+_initProAttempts+'/'+MAX_INIT_ATTEMPTS+')</span>'+
        '</div>';
    }
    // 500ms后重试
    setTimeout(initPro, 500);
    return false;
  }
  
  // CDN已加载 → 初始化
  try {
    // 移除 placeholder
    const ph = container.querySelector('.placeholder');
    if (ph) ph.remove();

    // 劫持 klinecharts.init：Pro 内部会 init 真实 chart，供会话面板采集画线
    try {
      if (window.klinecharts && window.klinecharts.init && !window.__kline_init_patched) {
        const _origInit = window.klinecharts.init.bind(window.klinecharts);
        window.klinecharts.init = function(dom, opts) {
          const chart = _origInit(dom, opts);
          window.__kline_chart = chart;
          try {
            window.dispatchEvent(new CustomEvent('kline-chart-ready', { detail: chart }));
          } catch (e) {}
          return chart;
        };
        window.__kline_init_patched = true;
      }
    } catch (e) { console.warn('[Pro] init patch failed', e); }
    
    pro = new window.klinechartspro.KLineChartPro({
      container: container,
      theme: 'dark',
      locale: 'zh-CN',
      drawingBarVisible: true,
      watermark: null, // 去除水印
      // 蜡烛图：红涨绿跌（中国习惯）
      styles: {
        candle: {
          bar: {
            upColor: '#ef5350', upBorderColor: '#ef5350', upWickColor: '#ef5350',
            downColor: '#26a69a', downBorderColor: '#26a69a', downWickColor: '#26a69a'
          },
          tooltip: {
            showName: false,
            custom: (data) => {
              const current = data.current || {};
              const prev = data.prev || {};
              const prevClose = (prev.close != null && prev.close > 0) ? prev.close : (current.open || 0);
              const open = current.open || 0;
              const close = current.close || 0;
              const _safeFix = (v, d = 2) => (v != null && !isNaN(v)) ? Number(v).toFixed(d) : '0.00';
              const high = current.high || 0;
              const low = current.low || 0;
              const change = (close && prevClose) ? (close - prevClose) : 0;
              const changePct = (close && prevClose) ? ((change / prevClose) * 100) : 0;
              const changeSign = change > 0 ? '+' : '';
              const pctSign = changePct > 0 ? '+' : '';
              const color = change > 0 ? '#ef5350' : change < 0 ? '#26a69a' : '#787b86';

              const fmtDate = current.timestamp ? (new Date(current.timestamp).toLocaleDateString('zh-CN')) : '-';

              return [
                { title: '时间: ', value: fmtDate },
                { title: '开: ', value: open ? _safeFix(open, 2) : '-' },
                { title: '高: ', value: high ? _safeFix(high, 2) : '-' },
                { title: '低: ', value: low ? _safeFix(low, 2) : '-' },
                { title: '收: ', value: close ? _safeFix(close, 2) : '-' },
                { title: '涨跌: ', value: close ? `${pctSign}${_safeFix(changePct, 2)}% (${changeSign}${_safeFix(change, 2)})` : '-', color: color },
                { title: '量: ', value: current.volume ? current.volume.toLocaleString() : '-' }
              ];
            }
          }
        },
        // 等间距K线，避免周末/节假日断点
        timeAxis: { type: 'bar' },
        // 成交量颜色跟随K线：红涨绿跌
        indicator: {
          bars: [{ upColor: 'rgba(239,83,80,0.7)', downColor: 'rgba(38,166,154,0.7)' }]
        }
      },
      // 默认加载上证指数
      symbol: { ticker: 'sh000001', name: '上证指数', shortName: '上证指数', type: 'index', market: 'A', priceCurrency: 'CNY' },
      period: { multiplier: 1, timespan: 'day', text: '日' },
      periods: [
        { multiplier: 1, timespan: 'minute', text: '1m' },
        { multiplier: 5, timespan: 'minute', text: '5m' },
        { multiplier: 15, timespan: 'minute', text: '15m' },
        { multiplier: 1, timespan: 'hour', text: '1H' },
        { multiplier: 2, timespan: 'hour', text: '2H' },
        { multiplier: 4, timespan: 'hour', text: '4H' },
        { multiplier: 1, timespan: 'day', text: '日' },
        { multiplier: 1, timespan: 'week', text: '周' },
        { multiplier: 1, timespan: 'month', text: '月' },
        { multiplier: 3, timespan: 'month', text: '季' },
        { multiplier: 12, timespan: 'month', text: '年' },
      ],
      timezone: 'Asia/Shanghai',
      mainIndicators: ['MA'],
      subIndicators: ['VOL'],
      datafeed: _datafeed
    });
    window.pro = pro;
    // 若劫持后已拿到 chart，再挂一次 draw-end 钩子入口
    if (window.__kline_chart) {
      console.log('[Pro] underlying chart ready', window.__kline_chart.id || '');
    } else {
      console.warn('[Pro] __kline_chart 未捕获，会话画线采集可能失败');
    }
    // 默认标的写入 store / ctx，供标注 UI 使用（此前仅 Pro 内部默认，外层未同步）
    if (!store.selected) {
      store.selected = { name: '上证指数', code: 'sh000001', type: 'index' };
    }
    window.__board_ctx = window.__board_ctx || {
      code: 'sh000001', type: 'index', name: '上证指数', period: 'daily', range: ''
    };
    window.__board_ctx.symbol = window.__board_ctx.code;
    // 初始化时也同步到 setBoardCtx（若存在），保证 ctx 单一来源
    if (typeof window.setBoardCtx === 'function') {
      try { window.setBoardCtx(Object.assign({}, window.__board_ctx)); } catch(_) {}
    }
    toast('KLineChart Pro 就绪');
    // 右侧空白bar设为零，避免非交易日显示空区域
    try{ pro.setOffsetRightBarCount(0); }catch(e){}
    _initProAttempts = 0;
    
    // 监听 Pro 内置周期/标的切换 → 同步 __board_ctx（保证 hookCtxPoll 能检测到并 ensure_chart）
    _bindProContextSync();

    // 默认开局触发上证指数 (sh000001) 顶栏涨跌幅同步显示（避免需手动点击）
    setTimeout(() => {
      if (typeof window.updateChartHeaderChg === 'function') {
        const sel = (window.store && window.store.selected) || { name: '上证指数', code: 'sh000001', type: 'index' };
        window.updateChartHeaderChg(sel.name, sel.code, sel.type);
      }
    }, 150);

    // Pro 就绪后通知 ChartController（会触发 init→applyPending）
    if (window.ChartController && typeof window.ChartController.init === 'function') {
      setTimeout(function() { window.ChartController.init(); }, 200);
    }
    return true;
  } catch(e) {
    console.error('Pro init error:', e);
    toast('Pro 初始化失败: '+e.message);
    return false;
  }
}

// 将 Pro period 对象转为字符串
function _periodToCtx(p) {
  if (!p) return 'daily';
  if (p.timespan === 'day') return 'daily';
  if (p.timespan === 'week') return 'weekly';
  if (p.timespan === 'month') {
    if (p.multiplier === 3) return 'quarterly';
    if (p.multiplier === 12) return 'yearly';
    return 'monthly';
  }
  if (p.timespan === 'minute') return p.multiplier + 'm';
  if (p.timespan === 'hour') return p.multiplier * 60 + 'm';
  return 'daily';
}

// 将 Pro 内置的周期/标的切换同步到 __board_ctx
// KLineChart Pro 9.x 不支持 onPeriodChange/onSymbolChange → monkey-patch setSymbol/setPeriod
function _bindProContextSync() {
  if (!window.pro) return;
  try {
    const origSetSymbol = window.pro.setSymbol;
    if (origSetSymbol && !origSetSymbol.__ctx_patched) {
      window.pro.setSymbol = function(sym) {
        const result = origSetSymbol.apply(this, arguments);
        if (sym && window.__board_ctx) {
          window.__board_ctx.code = sym.ticker || '';
          window.__board_ctx.symbol = sym.ticker || '';
          window.__board_ctx.name = sym.name || sym.shortName || '';
          window.__board_ctx.type = sym.type || 'stock';
          try { window.__board_ctx.period = _periodToCtx(window.pro.getPeriod()); } catch(_) {}
          // 同步更新 store.selected（保持与顶部 bar 一致，避免双源漂移）
          if (window.store) { window.store.selected = { code: sym.ticker, name: sym.name || sym.shortName, type: sym.type || 'stock' }; }
          // 若外部提供 setBoardCtx 则优先使用，保证 ctx 单一来源
          if (typeof window.setBoardCtx === 'function') {
            try { window.setBoardCtx(Object.assign({}, window.__board_ctx)); } catch(_) {}
          }
          if (typeof window.updateChartHeaderChg === 'function') {
            try { window.updateChartHeaderChg(sym.name || sym.shortName, sym.ticker, sym.type || 'stock'); } catch(_) {}
          }
          console.log('[Pro patch] symbol sync:', sym.ticker, '→', window.__board_ctx.period);
        }
        return result;
      };
      window.pro.setSymbol.__ctx_patched = true;
    }
    
    const origSetPeriod = window.pro.setPeriod;
    if (origSetPeriod && !origSetPeriod.__ctx_patched) {
      window.pro.setPeriod = function(p) {
        const result = origSetPeriod.apply(this, arguments);
        if (p && window.__board_ctx) {
          window.__board_ctx.period = _periodToCtx(p);
          // 若外部提供 setBoardCtx 则优先使用，保证 ctx 单一来源
          if (typeof window.setBoardCtx === 'function') {
            try { window.setBoardCtx(Object.assign({}, window.__board_ctx)); } catch(_) {}
          }
          console.log('[Pro patch] period sync:', window.__board_ctx.period);
        }
        return result;
      };
      window.pro.setPeriod.__ctx_patched = true;
    }
  } catch(e) {
    console.warn('[Pro] context sync bind failed:', e);
  }
}

// 强制重新加载Pro（用户点击重试按钮）
function forceReloadPro() {
  _initProAttempts = 0;
  if (_datafeed && _datafeed._cache) _datafeed._cache.clear();
  // 尝试重新注入CDN脚本
  if (!window.klinechartspro) {
    injectCDNScripts();
  }
  setTimeout(initPro, 300);
}

// CDN脚本注入（带fallback）
function injectCDNScripts() {
  const cdnBase = 'https://cdn.jsdelivr.net/npm';
  const altCdnBase = 'https://unpkg.com';
  
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  
  // 尝试主CDN，失败则用备用
  loadScript(`${cdnBase}/klinecharts@9.6.0/dist/klinecharts.min.js`)
    .then(() => loadScript(`${cdnBase}/@klinecharts/pro/dist/klinecharts-pro.umd.js`))
    .catch(() => {
      console.warn('[CDN] 主CDN失败，尝试备用');
      return loadScript(`${altCdnBase}/klinecharts@9.6.0/dist/klinecharts.min.js`)
        .then(() => loadScript(`${altCdnBase}/@klinecharts/pro/dist/klinecharts-pro.umd.js`));
    })
    .then(() => { console.log('[CDN] 脚本加载成功'); })
    .catch((e) => { console.error('[CDN] 所有CDN失败:', e); });
}
