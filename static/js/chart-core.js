// ===== 3. KLineChart Pro Datafeed（带请求取消+超时控制） =====
const KLINE_FETCH_TIMEOUT_MS = 15000;
const KLINE_API_TIMEOUT_SEC = 5;

function _normalizeChartBar(data) {
  var d = data || {};
  var amount = Number(d.amount != null ? d.amount : (d.turnover != null ? d.turnover : 0));
  if (!isFinite(amount)) amount = 0;
  return {
    timestamp: d.timestamp,
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
    volume: d.volume || 0,
    amount: amount,
    turnover: amount
  };
}
function _barReplayIsActive() {
  return !!(window.BarReplayController
    && typeof window.BarReplayController.isActive === 'function'
    && window.BarReplayController.isActive());
}

function _exitBarReplayForContextChange(reason) {
  const replay = window.BarReplayController;
  if (!replay || typeof replay.exit !== 'function' || !_barReplayIsActive()) return false;
  try {
    replay.exit({ restore: false, silent: true, reason: reason || 'context-change' });
    return true;
  } catch (error) {
    console.warn('[Pro] replay context cleanup failed:', error);
    return false;
  }
}

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
    this._servedRanges = new Map();
    this._spotSymbolKey = '';
    this._spotSymbol = null;
    this._spotCallback = null;
    this._spotInFlight = null;
    this._spotLastAttemptAt = 0;
    this._spotLastPushedPrice = 0;
    this._spotLastTradingTimestamp = 0;
    this._spotStaticPhase = '';
  }
  // 清除某 ticker 下所有缓存条目（切换标的时调用）
  // 同时中止该 ticker 进行中的请求，避免旧响应覆盖新标的
  clearByTicker(ticker){
    for(const k of [...this._cache.keys()]){
      if(k.startsWith(ticker+':')) this._cache.delete(k);
    }
    for(const k of [...this._servedRanges.keys()]){
      if(k.startsWith(ticker+':')) this._servedRanges.delete(k);
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
    if(t==='year') return 'yearly';
    return 'daily';
  }
  _normalizeTs(v) {
    const n = Number(v);
    if (!isFinite(n)) return null;
    // Pro 的年/季线会向前计算到 1970 年以前；本系统最早数据晚于 1970，
    // 将负时间戳夹到 Unix 起点，避免被误判成“未提供 from”。
    if (n <= 0) return 1;
    // 秒级 < 1e10，毫秒级 >= 1e10
    return n < 1e10 ? n * 1000 : n;
  }
  _expandHistoryWindow(apiPeriod, low, high) {
    if (low == null || high == null) return { low, high };
    // Pro 以 Asia/Shanghai 的本地周期起点生成时间戳；先平移 8 小时再按 UTC
    // 读取年月日，避免“1 月 1 日 00:00”落到 UTC 的上一年。
    const calendarOffset = 8 * 60 * 60 * 1000;
    const start = new Date(low + calendarOffset);
    const end = new Date(high + calendarOffset);
    if (apiPeriod === 'weekly') {
      const startDay = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate());
      const endDay = Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate());
      low = startDay - ((start.getUTCDay() + 6) % 7) * 86400000;
      high = endDay + (6 - ((end.getUTCDay() + 6) % 7)) * 86400000 + 86399999;
    } else if (apiPeriod === 'monthly') {
      low = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1);
      high = Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 1) - 1;
    } else if (apiPeriod === 'quarterly') {
      const startMonth = Math.floor(start.getUTCMonth() / 3) * 3;
      const endMonth = Math.floor(end.getUTCMonth() / 3) * 3;
      low = Date.UTC(start.getUTCFullYear(), startMonth, 1);
      high = Date.UTC(end.getUTCFullYear(), endMonth + 3, 1) - 1;
    } else if (apiPeriod === 'yearly') {
      low = Date.UTC(start.getUTCFullYear(), 0, 1);
      high = Date.UTC(end.getUTCFullYear() + 1, 0, 1) - 1;
    }
    return { low: low != null ? Math.max(1, low) : low, high };
  }
  _onlyUnseenOlderRows(rows, served, olderPage) {
    if (!olderPage || !served) return rows;
    return rows.filter(d => Number(d.timestamp) < served.min);
  }
  _recordServedRange(cacheKey, rows, olderPage) {
    if (!rows.length) return;
    const current = this._servedRanges.get(cacheKey);
    const next = {
      min: Number(rows[0].timestamp),
      max: Number(rows[rows.length - 1].timestamp)
    };
    if (olderPage && current) {
      next.min = Math.min(current.min, next.min);
      next.max = Math.max(current.max, next.max);
    }
    this._servedRanges.set(cacheKey, next);
  }
  _periodBucket(timestamp, apiPeriod) {
    const ts = Number(timestamp);
    if (!isFinite(ts)) return 'invalid:' + String(timestamp);
    const d = new Date(ts);
    const year = d.getUTCFullYear();
    const month = d.getUTCMonth();
    if (apiPeriod === 'monthly') return year + '-M' + month;
    if (apiPeriod === 'quarterly') return year + '-Q' + Math.floor(month / 3);
    if (apiPeriod === 'yearly') return year + '-Y';
    if (apiPeriod === 'weekly') {
      const dayStart = Date.UTC(year, month, d.getUTCDate());
      const monday = dayStart - ((d.getUTCDay() + 6) % 7) * 86400000;
      return 'W' + monday;
    }
    return 'T' + ts;
  }
  _dedupeSort(rows, apiPeriod) {
    const byPeriod = new Map();
    rows.forEach(d => {
      const key = this._periodBucket(d.timestamp, apiPeriod);
      const previous = byPeriod.get(key);
      // 同一逻辑周期保留时间戳较晚的数据；同时间戳时由新响应覆盖旧缓存。
      if (!previous || Number(d.timestamp) >= Number(previous.timestamp)) {
        byPeriod.set(key, d);
      }
    });
    return Array.from(byPeriod.values()).sort((a, b) => a.timestamp - b.timestamp);
  }
  async getHistoryKLineData(symbol, period, from, to) {
    if (!symbol || !symbol.ticker) return [];
    let stype = symbol.type;
    if (!stype) {
      const tk = symbol.ticker || '';
      if (tk.startsWith('BK')) {
        stype = 'concept';
      } else if (tk === '800000' || tk.startsWith('sh') || tk.startsWith('sz') || tk.startsWith('bj') || tk.startsWith('^') || tk === 'HSI' || tk === 'HSTECH' || tk === 'SPX' || tk === 'IXIC' || tk === 'DJI') {
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
    const fromMs = this._normalizeTs(from);
    const toMs = this._normalizeTs(to);
    const hasWindow = fromMs !== null && toMs !== null;
    const rawLow = hasWindow ? Math.min(fromMs, toMs) : null;
    const rawHigh = hasWindow ? Math.max(fromMs, toMs) : null;
    const expandedWindow = this._expandHistoryWindow(p, rawLow, rawHigh);
    const windowLow = expandedWindow.low;
    const windowHigh = expandedWindow.high;
    const servedRange = this._servedRanges.get(cacheKey);
    const olderPage = !!(hasWindow && servedRange && windowHigh < servedRange.min);
    const windowQuery = hasWindow
      ? '&from=' + Math.trunc(windowLow) + '&to=' + Math.trunc(windowHigh) + '&limit=600'
      : '&limit=800';
    const tid = setTimeout(()=>ctrl.abort(), KLINE_FETCH_TIMEOUT_MS);
    try {
      let rows = [];
      // 优先缓存（带 TTL 检测）：Pro 会多次回调；无缓存/过期时重拉
      const cached = this._cache.get(cacheKey);
      var hitTtl = (cached && cached.ttl != null) ? cached.ttl : this._CACHE_TTL;
      var cachedRange = cached && Array.isArray(cached.ranges) ? cached.ranges : [];
      var windowCovered = !hasWindow || cachedRange.some(function(r){
        return r.low <= windowLow && r.high >= windowHigh;
      });
      if (cached && (Date.now() - cached.ts) < hitTtl && windowCovered) {
        rows = cached.data.slice();
        // 更新 LRU 热缓存
        this._updateHotOrder(cacheKey);
      } else {
        // 缓存失效/不存在 → 删除旧条目并重新请求后端
        // 缓存优先：cache_first → 优先返回旧数据、后台刷新
        this._cache.delete(cacheKey);
        const r = await fetch(API+'/api/kline/'+(symbol.type||'stock')+'/'+encodeURIComponent(symbol.ticker)
          +'?name='+encodeURIComponent(symbol.name||symbol.shortName||'')+'&period='+p
          +'&timeout='+KLINE_API_TIMEOUT_SEC+'&cache_first=1'+windowQuery, {signal:ctrl.signal});
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
        var fetchedRows = (j.data||[]).map(_normalizeChartBar);
        // 盘中 OHLC overlay：用后端 intraday 覆盖末 bar（不写库）
        if(j.intraday && fetchedRows.length){
          var iv = j.intraday;
          var last = Object.assign({}, fetchedRows[fetchedRows.length-1]);
          if(iv.open!=null) last.open = Number(iv.open);
          if(iv.high!=null) last.high = Number(iv.high);
          if(iv.low!=null) last.low = Number(iv.low);
          if(iv.close!=null) last.close = Number(iv.close);
          if(iv.amount!=null || iv.turnover!=null) {
            last.amount = Number(iv.amount != null ? iv.amount : iv.turnover) || 0;
            last.turnover = last.amount;
          }
          fetchedRows[fetchedRows.length-1] = last;
        }
        rows = this._dedupeSort((cached && cached.data ? cached.data : []).concat(fetchedRows), p);
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
        var fetchedRanges = cachedRange.slice();
        if(hasWindow) fetchedRanges.push({ low: windowLow, high: windowHigh });
        this._cache.set(cacheKey, {
          data: rows.slice(), ts: Date.now(), ttl: ttl,
          ranges: fetchedRanges, complete: !hasWindow
        });
        // stale 后台刷新：海外指数也必须在历史尾部修复完成后重新绘图。
        var _canBackgroundRefresh = ['stock','index','industry','concept','hk_index','global_index','us'].indexOf(symbol.type) >= 0;
        if(j.stale && j.background_refresh_started && _canBackgroundRefresh){
          var self = this;
          var reSym = symbol; var reSymbolKey = symbolKey; var reKey = cacheKey; var reP = p;
          setTimeout(function(){
            if(self._activeSymbolKey !== reSymbolKey) return;
            var url2 = API+'/api/kline/'+(reSym.type||'industry')+'/'+encodeURIComponent(reSym.ticker)
              +'?name='+encodeURIComponent(reSym.name||reSym.shortName||'')+'&period='+reP
              +'&timeout='+KLINE_API_TIMEOUT_SEC+windowQuery;
            fetch(url2).then(function(r2){ return r2.json(); }).then(function(j2){
              if(self._activeSymbolKey !== reSymbolKey) return;
              if(!j2 || j2.error || !j2.data || !j2.data.length) return;
              var rows2 = j2.data.map(_normalizeChartBar);
              if(j2.intraday && rows2.length){
                var iv2 = j2.intraday; var last2 = Object.assign({}, rows2[rows2.length-1]);
                if(iv2.open!=null) last2.open = Number(iv2.open);
                if(iv2.high!=null) last2.high = Number(iv2.high);
                if(iv2.low!=null) last2.low = Number(iv2.low);
                if(iv2.close!=null) last2.close = Number(iv2.close);
                if(iv2.amount!=null || iv2.turnover!=null) {
                  last2.amount = Number(iv2.amount != null ? iv2.amount : iv2.turnover) || 0;
                  last2.turnover = last2.amount;
                }
                rows2[rows2.length-1] = last2;
              }
              var existing = self._cache.get(reKey);
              rows2 = self._dedupeSort(((existing && existing.data) || []).concat(rows2), reP);
              self._cache.set(reKey, {
                data: rows2.slice(), ts: Date.now(), ttl: ttl,
                ranges: (existing && existing.ranges) || fetchedRanges,
                complete: (existing && existing.complete) || !hasWindow
              });
              window.dispatchEvent(new CustomEvent('refresh-current-symbol', {
                detail: {
                  code: reSym.ticker,
                  source: 'kline-background-refresh',
                  reason: 'history-tail-refreshed'
                }
              }));
            }).catch(function(){});
          }, 5500);
        }
      }
      // from/to：Pro 多为秒级，数据为毫秒
      if (rows.length && hasWindow) {
        rows = rows.filter(d => d.timestamp >= windowLow && d.timestamp <= windowHigh);
      }
      rows = this._dedupeSort(rows, p);
      rows = this._onlyUnseenOlderRows(rows, servedRange, olderPage);
      this._recordServedRange(cacheKey, rows, olderPage);
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
    } finally {
      clearTimeout(tid);
      if(this._activeReqs.get(symKey) === ctrl) this._activeReqs.delete(symKey);
    }
  }
  subscribe(symbol, period, callback) {
    clearInterval(this._timer);
    // 回放期间暂停 Pro 的实时 spot 推送，避免改写回放中的中间 Bar。
    if (_barReplayIsActive()) return;
    const nextKey = String(symbol.ticker || '') + '|' + String(symbol.type || '');
    if (this._spotSymbolKey !== nextKey) {
      this._spotLastAttemptAt = 0;
      this._spotLastPushedPrice = 0;
      this._spotLastTradingTimestamp = 0;
      this._spotStaticPhase = '';
    }
    this._spotSymbolKey = nextKey;
    this._spotSymbol = symbol;
    this._spotCallback = callback;

    const doFetch = async () => {
      if (_barReplayIsActive()) return;
      if (this._spotInFlight) return this._spotInFlight;
      const requestSymbol = this._spotSymbol;
      const requestKey = this._spotSymbolKey;
      if (!requestSymbol || !requestKey) return;
      if (Date.now() - this._spotLastAttemptAt < 1000) return;
      this._spotLastAttemptAt = Date.now();

      this._spotInFlight = (async () => {
      try {
        if (document.hidden) return;
        if (window.boardPollingLeader && !window.boardPollingLeader.isLeader()) return;
        var now = new Date();
        var hhmm = now.getHours() * 100 + now.getMinutes();
        var weekday = now.getDay();
        var ticker = String(requestSymbol.ticker || '').toLowerCase();
        var symbolType = requestSymbol.type || '';
        var domestic = symbolType === 'stock'
          || (symbolType === 'index' && /^(sh|sz|bj)/.test(ticker));
        var phase = '';
        if (domestic) {
          if (weekday === 0 || weekday === 6) phase = 'closed';
          else if (hhmm < 915) phase = 'preopen';
          else if (hhmm < 1130) phase = 'live';
          else if (hhmm < 1300) phase = 'lunch';
          else if (hhmm < 1500) phase = 'live';
          else phase = 'closed';
          if (phase !== 'live' && this._spotStaticPhase === requestKey + '|' + phase) return;
          if (phase === 'live') this._spotStaticPhase = '';
        }
        const ctrl = new AbortController();
        const t = setTimeout(()=>ctrl.abort(), 5000);
        let stype = requestSymbol.type;
        if (!stype) {
          const tk = requestSymbol.ticker || '';
          if (tk.startsWith('BK')) {
            stype = 'concept';
          } else if (tk === '800000' || tk.startsWith('sh') || tk.startsWith('sz') || tk.startsWith('bj') || tk.startsWith('^') || tk === 'HSI' || tk === 'HSTECH' || tk === 'SPX' || tk === 'IXIC' || tk === 'DJI') {
            stype = 'index';
          } else {
            stype = 'stock';
          }
        }
        const r = await fetch(API+'/api/spot/'+stype+'/'+requestSymbol.ticker,{signal:ctrl.signal});
        clearTimeout(t);
        if(!r.ok) return;
        const j = await r.json();
        if (_barReplayIsActive()) return;
        if (this._spotSymbolKey !== requestKey) return;
        const data = j.data && j.data.price ? j.data : null;
        if(data) {
          if (!this._spotLastTradingTimestamp) {
            const chart = pro && pro._chart;
            const lastData = chart && chart.getDataList && chart.getDataList();
            if (lastData && lastData.length) {
              this._spotLastTradingTimestamp = lastData[lastData.length-1].timestamp;
            }
          }
          const ts = this._spotLastTradingTimestamp;
          if (domestic && phase !== 'live') {
            this._spotStaticPhase = requestKey + '|' + phase;
          }
          if (!ts) return; // 尚无有效交易日时间戳，跳过推送（避免产生虚假新Bar损坏图表）
          if (Math.abs(data.price - this._spotLastPushedPrice) > 0.0001) {
            this._spotLastPushedPrice = data.price;
            if (!_barReplayIsActive() && typeof this._spotCallback === 'function') {
              var amount = Number(data.amount != null ? data.amount : (data.turnover != null ? data.turnover : 0)) || 0;
              this._spotCallback({timestamp:ts,close:data.price,open:data.open||data.price,high:data.high||data.price,low:data.low||data.price,volume:data.volume||0,amount:amount,turnover:amount});
            }
          }
        }
      } catch(e) { if(e.name!=='AbortError') console.warn('spot poll err:',e); }
      })().finally(() => {
        this._spotInFlight = null;
      });
      return this._spotInFlight;
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
let _barReplayReadyChart = null;

function _notifyBarReplayChartReady(chart) {
  const replay = window.BarReplayController;
  if (!chart || !replay || typeof replay.onChartReady !== 'function'
      || _barReplayReadyChart === chart) return;
  _barReplayReadyChart = chart;
  try { replay.onChartReady(chart); } catch (e) {
    console.warn('[Pro] replay chart-ready hook failed:', e);
  }
}

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
          _notifyBarReplayChartReady(chart);
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
      theme: typeof getBoardChartTheme === 'function' ? getBoardChartTheme() : 'dark',
      locale: 'zh-CN',
      drawingBarVisible: true,
      watermark: '', // 显式空值，避免 KLineChart Pro 将 null 回退为默认水印
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
        { multiplier: 1, timespan: 'year', text: '年' },
      ],
      timezone: 'Asia/Shanghai',
      mainIndicators: ['MA'],
      subIndicators: ['VOL'],
      datafeed: _datafeed
    });
    window.pro = pro;
    _notifyBarReplayChartReady(window.__kline_chart);
    if (window.ChartIndicatorManager && typeof window.ChartIndicatorManager.onChartReady === 'function') {
      window.ChartIndicatorManager.onChartReady(window.__kline_chart);
    }
    if (typeof applyBoardChartTheme === 'function') {
      applyBoardChartTheme(typeof getBoardChartTheme === 'function' ? getBoardChartTheme() : 'dark', false);
    }
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
  if (p.timespan === 'year') return 'yearly';
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
        _exitBarReplayForContextChange('symbol-change');
        if (window.ChartVerticalPanController && typeof window.ChartVerticalPanController.reset === 'function') {
          window.ChartVerticalPanController.reset({ silent: true });
        }
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
        _exitBarReplayForContextChange('period-change');
        if (window.ChartVerticalPanController && typeof window.ChartVerticalPanController.reset === 'function') {
          window.ChartVerticalPanController.reset({ silent: true });
        }
        const result = origSetPeriod.apply(this, arguments);
        if (p && window.__board_ctx) {
          window.__board_ctx.period = _periodToCtx(p);
          // 若外部提供 setBoardCtx 则优先使用，保证 ctx 单一来源
          if (typeof window.setBoardCtx === 'function') {
            try { window.setBoardCtx(Object.assign({}, window.__board_ctx)); } catch(_) {}
          }
          try {
            window.dispatchEvent(new CustomEvent('period-change', {
              detail: { period: window.__board_ctx.period, periodObject: p, source: 'pro-setPeriod' }
            }));
          } catch(_) {}
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
