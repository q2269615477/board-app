/**
 * session-ui.js — 右侧会话/因果面板
 * - 任意深度因/果树（策略乙）
 * - 实时展示已采集关键数据（与后端 payload 同步）
 * - 选K / 画线最终态拾取
 */
(function (global) {
  'use strict';
  const API = global.API || '';

  let S = null;
  let pickKActive = false;
  let chartClickBound = false;
  let overlayHookBound = false;
  let saveTimer = null;
  let lastKey = '';

  function toast(msg, ms) {
    if (typeof global.toast === 'function') {
      global.toast(msg);
      return;
    }
    console.log('[SessionUI]', msg);
  }

  async function api(path, opts) {
    const r = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false || j.success === false) {
      const err = new Error(j.error || 'HTTP ' + r.status);
      err.status = r.status;
      if (j.code) err.code = j.code;
      if (j.current_rev !== undefined) err.current_rev = j.current_rev;
      if (j.current_session) err.current_session = j.current_session;
      throw err;
    }
    return j;
  }

  function panelCtx() {
    const s = (global.store && global.store.selected) || {};
    const ctx = global.__board_ctx || {};
    let period = ctx.period || 'daily';
    if (period && typeof period === 'object') {
      const t = period.timespan, m = period.multiplier;
      if (t === 'minute') period = m + 'm';
      else if (t === 'hour') period = m * 60 + 'm';
      else if (t === 'day') period = 'daily';
      else if (t === 'week') period = 'weekly';
      else if (t === 'month')
        period = m === 3 ? 'quarterly' : m === 12 ? 'yearly' : 'monthly';
      else period = 'daily';
    }
    return {
      symbol: s.code || ctx.symbol || ctx.code || 'sh000001',
      symbol_name: s.name || ctx.name || '',
      asset_type: s.type || ctx.type || 'index',
      period,
    };
  }

  function getChart() {
    // 优先：index.html 劫持 klinecharts.init 后挂到 window.__kline_chart
    if (global.__kline_chart) return global.__kline_chart;
    const pro = global.pro;
    if (!pro) return null;
    // 兼容错误猜测路径
    if (pro._chart && typeof pro._chart.getDataList === 'function') return pro._chart;
    if (pro.chart && typeof pro.chart.getDataList === 'function') return pro.chart;
    if (typeof pro.getChart === 'function') {
      try {
        const c = pro.getChart();
        if (c && typeof c.getDataList === 'function') return c;
      } catch (e) {}
    }
    return null;
  }

  function normalizeOverlayInstance(o) {
    if (!o) return null;
    // 跳过高亮临时线，避免写回事件元素
    try {
      const rawId = o.id || (typeof o.getId === 'function' ? o.getId() : '');
      if (rawId && String(rawId).indexOf('sess_hl') === 0) return null;
    } catch (e) {}
    let points = [];
    try {
      if (typeof o.getPoints === 'function') points = o.getPoints() || [];
      else if (Array.isArray(o.points)) points = o.points;
      else if (o._points) points = o._points;
    } catch (e) {
      points = [];
    }
    const pts = (points || []).map((p) => ({
      timestamp: p.timestamp != null ? p.timestamp : p.time != null ? p.time : p.dataIndex,
      value: p.value != null ? p.value : p.price != null ? p.price : p.y,
    }));
    const type =
      o.name ||
      (typeof o.getName === 'function' ? o.getName() : null) ||
      o.totalOverlayName ||
      o.type ||
      'overlay';
    let id = o.id || (typeof o.getId === 'function' ? o.getId() : null);
    // 无稳定 id 时用 type+点位哈希，避免每次 flush 随机 id 导致元素爆炸
    if (!id) {
      const key =
        String(type) +
        '|' +
        pts
          .map((p) => String(p.timestamp != null ? p.timestamp : '') + ':' + String(p.value != null ? p.value : ''))
          .join(';');
      let h = 0;
      for (let i = 0; i < key.length; i++) h = (Math.imul(31, h) + key.charCodeAt(i)) | 0;
      id = 'ovh_' + (h >>> 0).toString(16);
    }
    return {
      id: String(id),
      type: String(type),
      points: pts,
      styles: o.styles || {},
    };
  }

  function getOverlayStore(chart) {
    chart = chart || getChart();
    if (!chart) return null;
    try {
      // klinecharts 9.6 公开：getChartStore().getOverlayStore()
      if (typeof chart.getChartStore === 'function') {
        const cs = chart.getChartStore();
        if (cs && typeof cs.getOverlayStore === 'function') return cs.getOverlayStore();
      }
      if (chart._chartStore && typeof chart._chartStore.getOverlayStore === 'function') {
        return chart._chartStore.getOverlayStore();
      }
      if (typeof chart.getOverlayStore === 'function') return chart.getOverlayStore();
    } catch (e) {
      console.warn('[SessionUI] getOverlayStore', e);
    }
    return null;
  }

  function collectOverlays() {
    const chart = getChart();
    if (!chart) return [];
    let raw = [];
    try {
      const store = getOverlayStore(chart);
      if (store && typeof store.getInstances === 'function') {
        // 无参 = 全部 pane 的已完成 overlay 拼成数组
        raw = store.getInstances() || [];
      }
      if ((!raw || !raw.length) && typeof chart.getOverlays === 'function') {
        raw = chart.getOverlays() || [];
      }
      // 绘制中的那条
      if (store && typeof store.getProgressInstanceInfo === 'function') {
        const prog = store.getProgressInstanceInfo();
        if (prog && prog.instance) raw = (raw || []).concat([prog.instance]);
      }
    } catch (e) {
      console.warn('[SessionUI] collectOverlays', e);
      return [];
    }
    if (!Array.isArray(raw)) {
      try {
        raw = Array.from(raw.values ? raw.values() : raw);
      } catch (e) {
        raw = [];
      }
    }
    const seen = new Set();
    const out = [];
    raw.forEach((o) => {
      const n = normalizeOverlayInstance(o);
      if (!n || seen.has(n.id)) return;
      seen.add(n.id);
      out.push(n);
    });
    return out;
  }

  function getVisibleRange() {
    const chart = getChart();
    if (!chart) return null;
    try {
      if (typeof chart.getVisibleRange === 'function') {
        const r = chart.getVisibleRange();
        if (r) return { from: r.from, to: r.to };
      }
      const list = chart.getDataList && chart.getDataList();
      if (list && list.length)
        return { from_ts: list[0].timestamp, to_ts: list[list.length - 1].timestamp };
    } catch (e) {}
    return null;
  }

  function getDataList() {
    const chart = getChart();
    try {
      return (chart && chart.getDataList && chart.getDataList()) || [];
    } catch (e) {
      return [];
    }
  }

  function findNearestBar(ts) {
    const list = getDataList();
    if (!list.length || ts == null) return null;
    let best = list[0], bestD = Math.abs((list[0].timestamp || 0) - ts);
    for (let i = 1; i < list.length; i++) {
      const d = Math.abs((list[i].timestamp || 0) - ts);
      if (d < bestD) {
        bestD = d;
        best = list[i];
      }
    }
    return best;
  }

  function snapPriceElement(bar, price) {
    if (!bar || price == null || !isFinite(Number(price)))
      return { price_element: null, price };
    const cand = [
      ['open', bar.open],
      ['high', bar.high],
      ['low', bar.low],
      ['close', bar.close],
    ];
    let best = cand[0], bestD = Math.abs(cand[0][1] - price);
    for (const c of cand) {
      const d = Math.abs(c[1] - price);
      if (d < bestD) {
        bestD = d;
        best = c;
      }
    }
    const scale = Math.max(Math.abs(best[1]), 1e-6);
    if (bestD / scale > 0.002 && bestD > 0.01)
      return { price_element: 'custom', price };
    return { price_element: best[0], price: best[1] };
  }

  function barToKbar(bar, price) {
    const ctx = panelCtx();
    const s = snapPriceElement(bar, price != null ? price : bar.close);
    const ts = bar.timestamp;
    const date =
      bar.date ||
      (ts ? new Date(ts < 1e12 ? ts * 1000 : ts).toISOString().slice(0, 10) : '');
    const volume =
      bar.volume != null
        ? bar.volume
        : bar.vol != null
          ? bar.vol
          : bar.turnover != null
            ? bar.turnover
            : null;
    const amount =
      bar.amount != null
        ? bar.amount
        : bar.turnover != null && bar.volume == null
          ? null
          : bar.amount;
    return {
      timestamp: ts,
      date,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume,
      amount: amount != null ? amount : bar.amount,
      price_element: s.price_element,
      price: s.price,
      symbol: ctx.symbol,
      period: ctx.period,
      chart_id: S && S.current_chart_id,
    };
  }

  function fmtVol(v) {
    if (v == null || v === '') return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return String(Math.round(n * 100) / 100);
  }

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/"/g, '&quot;');
  }

  // ---------- Right panel UI ----------
  function panelCss() {
    return `
#sess-side{position:fixed;top:48px;right:0;bottom:0;width:372px;z-index:420;
  background:linear-gradient(180deg,#12151e 0%,#0e1118 100%);border-left:1px solid #2a2e39;
  display:flex;flex-direction:column;font-size:11px;color:#d1d4dc;
  box-shadow:-6px 0 24px rgba(0,0,0,.4)}
#sess-side .hd{padding:11px 14px;background:linear-gradient(90deg,#1a1e29,#161b28);
  border-bottom:1px solid #2a2e39;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
#sess-side .hd b{font-size:13px;color:#fff;cursor:pointer;letter-spacing:.02em}
#sess-side .hd b:hover{color:#8ab4ff}
#sess-side .tools{padding:8px 10px;border-bottom:1px solid #2a2e39;display:flex;flex-wrap:wrap;gap:5px;flex-shrink:0;
  background:#141822}
#sess-side .btn{padding:5px 9px;border-radius:4px;border:1px solid #2a2e39;
  background:#222733;color:#d1d4dc;cursor:pointer;font-size:11px;transition:border-color .12s,background .12s}
#sess-side .btn:hover{border-color:#2962ff;background:#2a3144}
#sess-side .btn.pri{background:#2962ff;border-color:#2962ff;color:#fff}
#sess-side .btn.pri:hover{background:#3d72ff}
#sess-side .btn.warn{background:#c9840e;border-color:#c9840e;color:#111;font-weight:600}
#sess-side .btn.on{outline:2px solid #f39c12;outline-offset:1px}
#sess-side .btn.ok{background:#26a69a;border-color:#26a69a;color:#fff}
#sess-side .body{flex:1;overflow:auto;padding:10px 12px}
#sess-side .sec{margin-bottom:14px}
#sess-side .sec h4{margin:0 0 8px;font-size:11px;color:#787b86;font-weight:600;letter-spacing:.04em;
  display:flex;align-items:center;gap:6px}
#sess-side .sec h4::before{content:'';width:3px;height:11px;border-radius:2px;background:#2962ff;display:inline-block}
#sess-side .card{background:linear-gradient(180deg,#1a1e29 0%,#161a24 100%);
  border:1px solid #2a2e39;border-radius:8px;padding:10px;margin-bottom:8px;
  box-shadow:0 1px 0 rgba(255,255,255,.02)}
#sess-side .card.active{border-color:#3d6df088;box-shadow:0 0 0 1px rgba(41,98,255,.12)}
#sess-side .kv{display:flex;justify-content:space-between;gap:8px;margin:3px 0;color:#b2b5be}
#sess-side .kv span:last-child{color:#d1d4dc;text-align:right;word-break:break-all}
#sess-side .muted{color:#565a64}
#sess-side .row-btns{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
#sess-side textarea{width:100%;min-height:52px;box-sizing:border-box;background:#0f131a;
  color:#d1d4dc;border:1px solid #2a2e39;border-radius:6px;padding:8px;font-size:11px;resize:vertical}
#sess-side textarea:focus{outline:none;border-color:#2962ff}
#sess-side .list-line{padding:3px 0;border-bottom:1px solid #1e2230;font-size:10px;color:#a0a3ad}
#sess-side .empty{color:#434651;font-size:10px;padding:4px 0}
#sess-side .foot{padding:10px;border-top:1px solid #2a2e39;flex-shrink:0;background:#141822}
#sess-side .chain-outline{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:11px;line-height:1.4;padding:2px 0 8px}
#sess-side .ol-line{display:flex;align-items:stretch;min-height:34px;margin:0;position:relative}
#sess-side .ol-indent{flex-shrink:0;display:flex;position:relative}
#sess-side .ol-indent .col{width:16px;position:relative}
#sess-side .ol-indent .col::before{
  content:'';position:absolute;left:7px;top:0;bottom:0;width:1px;
  background:linear-gradient(180deg,#3d4454 0%,#2a2e39 100%);opacity:.85}
#sess-side .ol-indent .col:last-child::after{
  content:'';position:absolute;left:7px;top:50%;width:8px;height:1px;background:#3d4454}
#sess-side .ol-node{flex:1;min-width:0;padding:7px 10px;margin:2px 0;border-radius:6px;
  border:1px solid transparent;cursor:pointer;transition:border-color .12s,background .12s,box-shadow .12s}
#sess-side .ol-node:hover{filter:brightness(1.08)}
#sess-side .ol-node.active{box-shadow:0 0 0 1px rgba(41,98,255,.45),inset 3px 0 0 #2962ff}
#sess-side .ol-node.closed{opacity:.78}
#sess-side .ol-cause{background:linear-gradient(135deg,#1a2235 0%,#151b2a 100%);border-color:#2a3550}
#sess-side .ol-cause.active{background:linear-gradient(135deg,#1e2d4d 0%,#182540 100%);border-color:#3d6df0}
#sess-side .ol-effect{background:linear-gradient(135deg,#221c14 0%,#1a1610 100%);border-color:#3d3428}
#sess-side .ol-effect.active{background:linear-gradient(135deg,#3a2a14 0%,#2a1e10 100%);border-color:#c9a227}
#sess-side .ol-effect.collecting{
  border-color:#f39c12;box-shadow:0 0 0 1px rgba(243,156,18,.35),inset 3px 0 0 #f39c12;
  animation:sess-pulse 1.6s ease-in-out infinite}
#sess-side .ol-effect.closed{border-color:#26a69a66;background:linear-gradient(135deg,#142420 0%,#101a18 100%)}
#sess-side .ol-event{background:rgba(15,19,26,.92);border:1px dashed #3a4155;border-radius:6px;margin-left:2px}
#sess-side .ol-event.active{border-style:solid;border-color:#f39c12;background:rgba(243,156,18,.08);box-shadow:inset 3px 0 0 #f39c12}
#sess-side .ol-ttl{font-weight:600;color:#e8eaef;display:flex;justify-content:space-between;align-items:center;gap:6px}
#sess-side .ol-ttl .tag{display:inline-flex;align-items:center;font-size:10px;letter-spacing:.04em;
  padding:1px 6px;border-radius:3px;font-weight:700}
#sess-side .ol-cause .tag{background:#2962ff33;color:#8ab4ff}
#sess-side .ol-effect .tag{background:#f39c1233;color:#f0c674}
#sess-side .ol-effect.closed .tag{background:#26a69a33;color:#7dcec4}
#sess-side .ol-event .tag{background:#565a6433;color:#b0b4be;font-weight:600}
#sess-side .ol-meta{font-size:9px;color:#6b7080}
#sess-side .ol-sub{font-size:9px;color:#6b7080;margin-top:3px;display:flex;flex-wrap:wrap;gap:6px}
#sess-side .ol-chip{display:inline-block;padding:0 5px;border-radius:8px;background:#0f131a88;
  border:1px solid #2a2e39;color:#9aa0ac;font-size:9px}
#sess-side .ol-chain-wrap[data-depth="0"]{margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #1e2230}
#sess-side .ol-del{flex-shrink:0;width:22px;height:22px;margin:2px 0 2px 4px;border:1px solid #3a2030;
  border-radius:4px;background:#1a1218;color:#e57373;cursor:pointer;font-size:12px;line-height:20px;
  text-align:center;padding:0;opacity:.55;transition:opacity .12s,background .12s}
#sess-side .ol-del:hover{opacity:1;background:#3a1820;border-color:#e57373}
#sess-side .ol-line-inner{display:flex;align-items:stretch;flex:1;min-width:0}
#sess-side .el-list{display:flex;flex-direction:column;gap:4px}
#sess-side .el-item{display:flex;align-items:stretch;gap:4px;border:1px solid #2a2e39;border-radius:6px;
  background:#141822;cursor:pointer;transition:border-color .12s,background .12s}
#sess-side .el-item:hover{border-color:#3d6df0;background:#1a2235}
#sess-side .el-item.active{border-color:#f39c12;box-shadow:inset 3px 0 0 #f39c12;background:#1c1810}
#sess-side .el-body{flex:1;min-width:0;padding:6px 8px}
#sess-side .el-kind{font-size:10px;font-weight:700;letter-spacing:.04em;padding:1px 6px;border-radius:3px;display:inline-block}
#sess-side .el-kind.kbar{background:#2962ff33;color:#8ab4ff}
#sess-side .el-kind.overlay{background:#26a69a33;color:#7dcec4}
#sess-side .el-kind.note{background:#f39c1233;color:#f0c674}
#sess-side .el-title{font-size:11px;color:#e0e3eb;margin-top:3px;word-break:break-all}
#sess-side .el-sub{font-size:9px;color:#6b7080;margin-top:2px}
#sess-side .el-del{width:22px;border:none;background:transparent;color:#e57373;cursor:pointer;opacity:.5}
#sess-side .el-del:hover{opacity:1}
#sess-side .hint-box{padding:12px;border:1px dashed #2a3550;border-radius:8px;color:#6b7080;font-size:11px;line-height:1.6;
  background:linear-gradient(180deg,#161b28 0%,#131722 100%)}
#sess-side .hint-box b{color:#a8b0c0}
#sess-side .live-target{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
#sess-side .live-target.ev{background:#f39c1222;color:#f0c674}
#sess-side .live-target.cause{background:#2962ff22;color:#8ab4ff}
#sess-side .live-target.effect{background:#e67e2222;color:#e8a87c}
@keyframes sess-pulse{0%,100%{opacity:1}50%{opacity:.88}}
body.sess-side-on #right-panel, body.sess-side-on .right-col{margin-right:0}
`;
  }

  function applyPanelCss() {
    let style = document.getElementById('sess-side-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'sess-side-style';
      document.head.appendChild(style);
    }
    style.textContent = panelCss();
  }

  function ensureUI() {
    applyPanelCss();
    document.body.classList.add('sess-side-on');
    if (document.getElementById('sess-side')) {
      // 已有面板：确保标题可取消选中
      const title = document.querySelector('#sess-side .hd b');
      if (title && !title.dataset.bindClear) {
        title.dataset.bindClear = '1';
        title.title = '点击取消链/事件选中（再点「因」可建新根链）';
        title.addEventListener('click', () => clearChainFocus());
      }
      return;
    }
    const af = document.getElementById('ann-fab');
    if (af) af.style.display = 'none';
    const ap = document.getElementById('ann-panel');
    if (ap) ap.style.display = 'none';
    const oldBar = document.getElementById('sess-bar');
    if (oldBar) oldBar.remove();

    const side = document.createElement('div');
    side.id = 'sess-side';
    side.innerHTML = `
<div class="hd">
  <b title="点击取消链/事件选中（再点「因」可建新根链）">会话分析</b>
  <span class="muted" id="sess-hdr-status">—</span>
</div>
<div class="tools" id="sess-tools">
  <button class="btn" type="button" data-act="list">列表</button>
  <button class="btn pri" type="button" data-act="new">新会话</button>
  <button class="btn warn" type="button" data-act="save">保存</button>
  <button class="btn" type="button" data-act="cause" title="无选中：新建根链；已选中链：在其下嵌套子链（层级缩进）">因</button>
  <button class="btn" type="button" data-act="indent" title="在当前选中链下嵌套子链">⇥ 缩进</button>
  <button class="btn" type="button" data-act="effect" title="果：第1次进果侧，第2次闭合（不是事件）">果</button>
  <button class="btn" type="button" data-act="event" title="仅主动添加：挂到当前选中的因果链">事件</button>
  <button class="btn" type="button" data-act="browse">浏览</button>
  <button class="btn" type="button" data-act="pick_k" title="开关：开启后点图采集一根K，再点关闭；采完自动关">选K</button>
</div>
<div class="body" id="sess-body"></div>
<div class="foot">
  <div class="sec">
    <h4>备注</h4>
    <textarea id="sess-note" placeholder="需要时写语义备注…"></textarea>
    <div class="row-btns">
      <button class="btn pri" type="button" data-act="note">写入</button>
      <button class="btn" type="button" data-act="refresh">刷新</button>
    </div>
  </div>
</div>
<div id="sess-list-drawer" style="display:none;position:absolute;inset:48px 0 0 0;background:#131722;z-index:2;overflow:auto;padding:10px"></div>
`;
    document.body.appendChild(side);

    side.querySelector('#sess-side .hd b')?.addEventListener('click', () => clearChainFocus());
    side.querySelectorAll('[data-act]').forEach((btn) => {
      btn.addEventListener('click', () => onTool(btn.getAttribute('data-act')));
    });
  }

  async function clearChainFocus() {
    if (!S) return;
    try {
      await actApi('set_ui', {
        active_cause_id: null,
        active_effect_id: null,
        active_event_id: null,
        side: 'cause',
      });
      // P0-4: 取消事件过滤，恢复全显
      filterOverlaysByEvent(null);
      toast('已取消选中 · 再点「因」可建根链');
    } catch (e) {
      toast(e.message);
    }
  }

  function render() {
    ensureUI();
    const body = document.getElementById('sess-body');
    const hdr = document.getElementById('sess-hdr-status');
    if (!body) return;
    if (!S) {
      body.innerHTML = '<div class="empty">无会话</div>';
      return;
    }
    const ui = S.ui || {};
    const ctx = panelCtx();
    const focusTag = ui.active_event_id
      ? '事件'
      : ui.side === 'effect'
        ? '果'
        : '因';
    hdr.textContent = (S.status || '') + ' · ' + focusTag;

    // 与后端 tool 同步选K开关状态
    pickKActive = ui.tool === 'pick_k';
    document.querySelectorAll('#sess-tools .btn').forEach((b) => {
      const a = b.getAttribute('data-act');
      b.classList.toggle(
        'on',
        (a === 'pick_k' && ui.tool === 'pick_k') || (a === 'browse' && ui.tool === 'browse')
      );
    });

    const activeC = (S.causes || []).find((c) => c.id === ui.active_cause_id);
    const activeE = (S.effects || []).find((e) => e.id === ui.active_effect_id);
    const causes = S.causes || [];
    const effects = S.effects || [];
    const events = S.events || [];
    const causeMap = {};
    causes.forEach((c) => {
      causeMap[c.id] = c;
    });
    const eventMap = {};
    events.forEach((ev) => {
      eventMap[ev.id] = ev;
    });

    function effectOf(causeId) {
      return effects.find((e) => e.cause_id === causeId);
    }
    function indentHtml(depth) {
      if (depth <= 0) return '';
      let h = '<span class="ol-indent">';
      for (let i = 0; i < depth; i++) h += '<span class="col"></span>';
      return h + '</span>';
    }

    /**
     * 列式渲染一条因果链：
     *   因
     *   [事件 | 子链 …]  （children_order 顺序）
     *   果
     */
    function renderChain(causeId, depth) {
      const c = causeMap[causeId];
      if (!c) return '';
      const ef = effectOf(c.id);
      const phase = (ef && ef.phase) || 'idle';
      const isChain = c.id === ui.active_cause_id;
      const causeActive = isChain && !ui.active_event_id && (ui.side || 'cause') === 'cause';
      const effectActive = isChain && !ui.active_event_id && ui.side === 'effect';
      let causeCls = 'ol-node ol-cause' + (causeActive ? ' active' : '');
      if (c.state === 'closed') causeCls += ' closed';
      let effectCls =
        'ol-node ol-effect' +
        (effectActive ? ' active' : '') +
        (phase === 'collecting' ? ' collecting' : '') +
        (phase === 'closed' ? ' closed' : '');

      let html = `<div class="ol-chain-wrap" data-depth="${depth}" data-chain="${esc(c.id)}">`;
      // 因行（链顶）+ 删除
      html += `<div class="ol-line" data-depth="${depth}">
  ${indentHtml(depth)}
  <div class="ol-line-inner">
  <div class="${causeCls}" data-cause="${esc(c.id)}" title="选中此因果链 · 因侧（点因≠事件）">
    <div class="ol-ttl">
      <span class="tag">因</span>
      <span class="ol-meta">L${depth}${isChain && !ui.active_event_id && (ui.side || 'cause') === 'cause' ? ' · 选中' : ''}</span>
    </div>
    <div class="ol-sub">
      <span class="ol-chip">${esc(c.state || 'open')}</span>
      <span class="ol-chip">K ${(c.kbars || []).length}</span>
      <span class="ol-chip">线 ${(c.overlays || []).length}</span>
    </div>
  </div>
  <button type="button" class="ol-del" data-del-cause="${esc(c.id)}" title="删除此因果链（含子链与事件）">×</button>
  </div>
</div>`;

      // 中间：仅 children_order 中真实存在的 事件 / 子链（不预制事件）
      const order = Array.isArray(c.children_order) ? c.children_order : [];
      let items = order.filter((x) => x && x.id && (x.type === 'event' || x.type === 'chain'));
      if (!items.length) {
        // 兼容旧数据：只列真实事件 + 真实子链，绝不捏造空事件
        events
          .filter((ev) => ev.cause_id === c.id && ev.id)
          .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))
          .forEach((ev) => items.push({ type: 'event', id: ev.id }));
        causes
          .filter((ch) => ch.parent_id === c.id && ch.id)
          .forEach((ch) => items.push({ type: 'chain', id: ch.id }));
      }
      items.forEach((item) => {
        if (item.type === 'event') {
          const ev = eventMap[item.id];
          if (!ev) return; // 无实体则不渲染
          const act = ev.id === ui.active_event_id ? ' active' : '';
          const t = (ev.created_at || '').slice(11, 16) || '';
          html += `<div class="ol-line" data-depth="${depth + 1}">
  ${indentHtml(depth + 1)}
  <div class="ol-line-inner">
  <div class="ol-node ol-event${act}" data-event="${esc(ev.id)}" title="属于链 L${depth} · 选中后：画线/选K/备注均归入此事件">
    <div class="ol-ttl">
      <span class="tag">事件</span>
      <span class="ol-meta">∈L${depth}${t ? ' · ' + esc(t) : ''}</span>
    </div>
    <div class="ol-sub">
      <span class="ol-chip">元素 ${(ev.elements || []).length || (ev.kbars || []).length + (ev.overlays || []).length + (ev.notes || []).length}</span>
    </div>
  </div>
  <button type="button" class="ol-del" data-del-event="${esc(ev.id)}" title="删除此事件">×</button>
  </div>
</div>`;
        } else if (item.type === 'chain') {
          if (!causeMap[item.id]) return;
          html += renderChain(item.id, depth + 1);
        }
      });

      // 果行（链底，与因同列）
      const phaseLabel =
        phase === 'collecting' ? '采集中' : phase === 'closed' ? '已闭合' : '待验证';
      html += `<div class="ol-line" data-depth="${depth}">
  ${indentHtml(depth)}
  <div class="${effectCls}" data-effect="${esc(ef ? ef.id : '')}" data-cause-for-effect="${esc(
        c.id
      )}" title="点果：进果侧 / 再点闭合（点果≠事件）">
    <div class="ol-ttl">
      <span class="tag">果</span>
      <span class="ol-meta">${esc(phaseLabel)}</span>
    </div>
    <div class="ol-sub">
      <span class="ol-chip">K ${(ef && ef.kbars ? ef.kbars.length : 0)}</span>
      <span class="ol-chip">线 ${(ef && ef.overlays ? ef.overlays.length : 0)}</span>
    </div>
  </div>
</div>`;
      html += `</div>`;
      return html;
    }

    // 根层顺序
    let rootItems = (S.root_order || []).slice();
    if (!rootItems.length) {
      causes
        .filter((c) => !c.parent_id)
        .forEach((c) => rootItems.push({ type: 'chain', id: c.id }));
    }
    const treeParts = [];
    rootItems.forEach((item) => {
      if (item && item.type === 'chain' && item.id) treeParts.push(renderChain(item.id, 0));
    });
    const treeHtml = treeParts.length
      ? `<div class="chain-outline">${treeParts.join('')}</div>`
      : `<div class="hint-box">
        空白大纲 — 不预置任何节点。<br/>
        <b>因</b>：无选中时建根链；选中某链后再点「因」= 其下子链（缩进）<br/>
        <b>事件</b>：仅主动添加，归属当前选中的因果链<br/>
        <b>果</b>：第1次进果侧，第2次闭合 · 点因/果都不是事件<br/>
        点标题「会话分析」可取消选中，再点「因」可另建根链
      </div>`;

    const liveEv = events.find((e) => e.id === ui.active_event_id);
    const liveSide = ui.side || 'cause';
    const liveNode = liveSide === 'effect' ? activeE : activeC;
    let liveLabel;
    let liveTargetCls = 'cause';
    if (liveEv) {
      liveLabel =
        '事件 ∈L' +
        (activeC && activeC.depth != null ? activeC.depth : '?') +
        ' · ' +
        ((liveEv.created_at || '').slice(11, 16) || liveEv.id.slice(-6));
      liveTargetCls = 'ev';
    } else if (liveSide === 'effect') {
      liveLabel = '果侧汇总 · ' + ((activeE && activeE.phase) || '');
      liveTargetCls = 'effect';
    } else {
      liveLabel = '因侧汇总 · ' + ((activeC && activeC.state) || '未选链');
      liveTargetCls = 'cause';
    }

    function linesKbars(arr) {
      if (!arr || !arr.length) return '<div class="empty">尚未选K</div>';
      return arr
        .map((k, i) => {
          const ohlc =
            k.open != null
              ? ` O${esc(k.open)} H${esc(k.high)} L${esc(k.low)} C${esc(k.close)}`
              : '';
          return `<div class="list-line">#${i + 1} ${esc(k.date || k.timestamp)} ${esc(
            k.symbol || ''
          )} ${esc(k.period || '')} ${esc(k.price_element || '')}@${esc(
            k.price != null ? k.price : ''
          )}${ohlc} 量${esc(fmtVol(k.volume))}${
            k.amount != null ? ' 额' + esc(fmtVol(k.amount)) : ''
          }</div>`;
        })
        .join('');
    }
    function linesOvs(arr) {
      if (!arr || !arr.length) return '<div class="empty">无画线</div>';
      return arr
        .map((o, i) => {
          const pts = (o.points || [])
            .slice(0, 2)
            .map((p) => p.value)
            .join(',');
          return `<div class="list-line">#${i + 1} ${esc(o.type)} ${esc(pts)}</div>`;
        })
        .join('');
    }
    function linesNotes(arr) {
      if (!arr || !arr.length) return '<div class="empty">无备注</div>';
      return arr
        .map((n) => {
          const t = typeof n === 'string' ? n : n.text || JSON.stringify(n);
          return `<div class="list-line">${esc(t)}</div>`;
        })
        .join('');
    }

    // 元素区：事件内 elements 并列列表；无事件时退回因/果汇总
    const chartOvs = collectOverlays();
    const depthNow = activeC ? (activeC.depth != null ? activeC.depth : 0) : 0;
    const activeElId = ui.active_element_id;
    let elementsHtml = '';
    if (liveEv) {
      const els = liveEv.elements || [];
      if (!els.length) {
        elementsHtml =
          '<div class="empty">暂无元素 · 选K / 画线 / 写备注 将各自新增并列元素</div>';
      } else {
        elementsHtml =
          '<div class="el-list">' +
          els
            .map((el, idx) => {
              const kind = el.kind || 'kbar';
              const d = el.data || {};
              const act = el.id === activeElId ? ' active' : '';
              let title = '';
              let sub = '';
              if (kind === 'kbar') {
                title =
                  (d.date || d.timestamp || 'K') +
                  ' ' +
                  (d.price_element || '') +
                  '@' +
                  (d.price != null ? d.price : '');
                sub =
                  '量' +
                  fmtVol(d.volume) +
                  (d.open != null
                    ? ' · O' + d.open + ' H' + d.high + ' L' + d.low + ' C' + d.close
                    : '');
              } else if (kind === 'overlay') {
                const pts = (d.points || [])
                  .slice(0, 2)
                  .map((p) => (p && p.value != null ? p.value : ''))
                  .join(',');
                title = (d.type || '画线') + (pts ? ' · ' + pts : '');
                sub = 'id ' + String(d.id || '').slice(-8);
              } else if (kind === 'note') {
                title = d.text || '';
                sub = (d.at || el.created_at || '').toString().slice(0, 19);
              }
              const kindLabel =
                kind === 'kbar' ? 'K线' : kind === 'overlay' ? '画线' : '备注';
              return `<div class="el-item${act}" data-element="${esc(el.id)}" data-event-for-el="${esc(
                liveEv.id
              )}">
  <div class="el-body">
    <span class="el-kind ${esc(kind)}">#${idx + 1} ${kindLabel}</span>
    <div class="el-title">${esc(title)}</div>
    <div class="el-sub">${esc(sub)}</div>
  </div>
  <button type="button" class="el-del" data-del-element="${esc(el.id)}" data-event-for-el="${esc(
                liveEv.id
              )}" title="删除此元素">×</button>
</div>`;
            })
            .join('') +
          '</div>';
      }
    } else {
      const liveK = (liveNode && liveNode.kbars) || [];
      const liveOv = (liveNode && liveNode.overlays) || [];
      const liveNotes = (liveNode && liveNode.notes) || [];
      elementsHtml =
        '<div class="muted" style="margin-bottom:6px">未选事件 · 显示因/果汇总（点「事件」后可建并列元素）</div>' +
        `<div class="card"><div style="color:#f39c12;margin-bottom:4px">选K ${liveK.length}</div>${linesKbars(
          liveK
        )}</div>` +
        `<div class="card"><div style="color:#f39c12;margin-bottom:4px">画线 ${liveOv.length}</div>${linesOvs(
          liveOv
        )}</div>` +
        `<div class="card"><div style="color:#f39c12;margin-bottom:4px">备注 ${liveNotes.length}</div>${linesNotes(
          liveNotes
        )}</div>`;
    }

    body.innerHTML = `
<div class="sec">
  <h4>图表</h4>
  <div class="card">
    <div class="kv"><span>标的</span><span>${esc(ctx.symbol_name || '')} ${esc(ctx.symbol)}</span></div>
    <div class="kv"><span>周期</span><span>${esc(ctx.period)}</span></div>
    <div class="kv"><span>图上画线</span><span>${chartOvs.length}</span></div>
    <div class="kv"><span>当前链深度</span><span>L${depthNow}</span></div>
  </div>
</div>
<div class="sec">
  <h4>大纲 · 列式层级</h4>
  ${treeHtml}
</div>
<div class="sec">
  <h4>元素 <span class="live-target ${liveTargetCls}">${esc(liveLabel)}</span></h4>
  <div class="card active">
    <div class="kv"><span>当前链深度</span><span>L${depthNow}${
      activeC ? '' : ' · 未选中'
    }</span></div>
    <div class="kv"><span>链 / 事件</span><span>${esc(
      (ui.active_cause_id || '—').toString().slice(-8)
    )} / ${esc((ui.active_event_id || '—').toString().slice(-8))}</span></div>
    <div class="kv"><span>工具</span><span>${esc(ui.tool || 'browse')}${
      pickKActive ? ' ·选K' : ''
    }</span></div>
    <div class="kv"><span>写入目标</span><span>${
      liveEv ? '事件内并列元素' : liveSide === 'effect' ? '果汇总' : '因汇总'
    }</span></div>
  </div>
  <div class="card">
    <div style="color:#f39c12;margin-bottom:6px">元素列表 ${
      liveEv ? (liveEv.elements || []).length : '—'
    } · 点击高亮图表</div>
    ${elementsHtml}
  </div>
  <div class="card"><div style="color:#787b86;margin-bottom:4px">图上实时画线 ${
    chartOvs.length
  }</div>${linesOvs(chartOvs)}</div>
</div>`;

    body.querySelectorAll('[data-cause]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        focusCause(el.getAttribute('data-cause'));
      });
    });
    body.querySelectorAll('[data-effect]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const eid = el.getAttribute('data-effect');
        const cid = el.getAttribute('data-cause-for-effect');
        if (eid) onClickEffectId(eid, cid);
      });
    });
    body.querySelectorAll('[data-event]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        focusEvent(el.getAttribute('data-event'));
      });
    });
    body.querySelectorAll('[data-del-event]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteEvent(el.getAttribute('data-del-event'));
      });
    });
    body.querySelectorAll('[data-del-cause]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteCause(el.getAttribute('data-del-cause'));
      });
    });
    body.querySelectorAll('[data-element]').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target.closest && e.target.closest('[data-del-element]')) return;
        e.stopPropagation();
        focusElement(
          el.getAttribute('data-element'),
          el.getAttribute('data-event-for-el')
        );
      });
    });
    body.querySelectorAll('[data-del-element]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteElement(
          el.getAttribute('data-del-element'),
          el.getAttribute('data-event-for-el')
        );
      });
    });
  }

  let _hlOverlayIds = [];
  let _hlOverrideId = null;
  let _hlOverrideStyles = null;

  function _removeOverlaySafe(chart, id) {
    if (!chart || !id || typeof chart.removeOverlay !== 'function') return;
    try {
      chart.removeOverlay({ id: id });
    } catch (e) {
      try {
        chart.removeOverlay(id);
      } catch (e2) {}
    }
  }

  function clearChartHighlight() {
    const chart = getChart();
    if (!chart) {
      _hlOverlayIds = [];
      _hlOverrideId = null;
      _hlOverrideStyles = null;
      return;
    }
    _hlOverlayIds.forEach((id) => _removeOverlaySafe(chart, id));
    _hlOverlayIds = [];
    // 恢复被 override 的画线样式
    if (_hlOverrideId) {
      try {
        if (typeof chart.overrideOverlay === 'function') {
          chart.overrideOverlay({
            id: _hlOverrideId,
            styles: _hlOverrideStyles || {},
          });
        }
      } catch (e) {}
      _hlOverrideId = null;
      _hlOverrideStyles = null;
    }
  }

  /**
   * 以某时间戳对应 K 为中心展开可视区。
   * scrollToDataIndex 会把 index 放到可见区左侧，故 from = idx - span*0.5。
   * @param expand 若 true，保证至少约 120 根 K 的窗口（画线起点用）
   */
  function centerChartOnTimestamp(chart, ts, expand) {
    chart = chart || getChart();
    const list = getDataList();
    if (!chart || !list.length || ts == null) return null;
    const bar = findNearestBar(ts) || list[list.length - 1];
    let idx = -1;
    for (let i = 0; i < list.length; i++) {
      if (list[i].timestamp === bar.timestamp) {
        idx = i;
        break;
      }
    }
    if (idx < 0) idx = list.length - 1;
    try {
      const vr = (typeof chart.getVisibleRange === 'function' && chart.getVisibleRange()) || {
        from: 0,
        to: 120,
      };
      let span = Math.max(60, Math.floor((vr.to || 120) - (vr.from || 0)) || 120);
      if (expand) span = Math.max(span, 120);
      // 目标落在可见区中心
      const from = Math.max(0, idx - Math.floor(span * 0.5));
      if (typeof chart.scrollToDataIndex === 'function') chart.scrollToDataIndex(from);
      else if (typeof chart.scrollToTimestamp === 'function') chart.scrollToTimestamp(bar.timestamp);
    } catch (e) {
      console.warn('[SessionUI] centerChart', e);
    }
    return { bar, idx };
  }

  function isHorizontalOverlayName(name) {
    const n = String(name || '').toLowerCase();
    return (
      n.indexOf('horizontal') >= 0 ||
      n === 'priceline' ||
      n === 'price_line' ||
      n === 'horizontalline'
    );
  }

  function overlayFirstTimestamp(ov) {
    if (!ov) return null;
    let pts = [];
    try {
      if (typeof ov.getPoints === 'function') pts = ov.getPoints() || [];
      else if (Array.isArray(ov.points)) pts = ov.points;
    } catch (e) {
      pts = [];
    }
    if (!pts.length) return null;
    const p = pts[0];
    return p.timestamp != null ? p.timestamp : p.time != null ? p.time : null;
  }

  /**
   * 水平线：仅在「首点已落定」时居中一次。
   * 禁止在 currentStep===0（鼠标跟随时）调用，否则会乱跳。
   */
  function centerOnHorizontalDrawStart(ov, opts) {
    opts = opts || {};
    if (!ov) return false;
    const name =
      ov.name ||
      (typeof ov.getName === 'function' ? ov.getName() : null) ||
      ov.totalOverlayName ||
      ov.type ||
      '';
    if (!isHorizontalOverlayName(name)) return false;

    // 仍在放置第 1 个点（跟随十字线 / step 未知）→ 绝不滚动（防乱跳）
    // 允许：step>=1（多点工具已点下第一点）或 step===-1（已完成）或 fromDrawEnd
    const step = ov.currentStep;
    if (!opts.fromDrawEnd) {
      if (step === 0 || step === '0' || step == null || step === undefined) return false;
      if (step !== -1 && Number(step) < 1) return false;
    }

    const ts = overlayFirstTimestamp(ov);
    if (ts == null || !isFinite(Number(ts))) return false;

    // 必须能对齐到真实 K，避免半成品点乱跳
    const bar = findNearestBar(ts);
    if (!bar || bar.timestamp == null) return false;
    // 日线容差：超过 2 根就不算「这根 K」
    const list = getDataList();
    let idx = -1;
    for (let i = 0; i < list.length; i++) {
      if (list[i].timestamp === bar.timestamp) {
        idx = i;
        break;
      }
    }
    if (idx < 0) return false;
    if (idx + 1 < list.length) {
      const gap = Math.abs((list[idx + 1].timestamp || 0) - (list[idx].timestamp || 0)) || 1;
      if (Math.abs((bar.timestamp || 0) - ts) > gap * 1.5) return false;
    }

    const oid =
      ov.id || (typeof ov.getId === 'function' ? ov.getId() : null) || name;
    // 同一轮绘制只居中一次（用绘制会话 id，不用变动的 ts）
    const sessionKey = String(oid || name) + '::draw';
    if (global.__sess_draw_center_key === sessionKey) return false;
    global.__sess_draw_center_key = sessionKey;

    const chart = getChart();
    if (!chart) return false;
    // 用对齐后的 bar 时间戳居中
    centerChartOnTimestamp(chart, bar.timestamp, true);
    return true;
  }

  function _mkHlId(tag) {
    return 'sess_hl_' + tag + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 5);
  }

  function _createHlOverlay(chart, cfg) {
    if (!chart || typeof chart.createOverlay !== 'function') return null;
    const id = cfg.id || _mkHlId('x');
    try {
      const ret = chart.createOverlay(
        Object.assign(
          {
            lock: true,
            zLevel: 999,
            visible: true,
          },
          cfg,
          { id: id }
        )
      );
      const finalId = ret || id;
      _hlOverlayIds.push(finalId);
      // 校验点位是否写入
      try {
        const o =
          typeof chart.getOverlayById === 'function' ? chart.getOverlayById(finalId) : null;
        if (o && (!o.points || !o.points.length) && cfg.points) {
          if (typeof chart.overrideOverlay === 'function') {
            chart.overrideOverlay({ id: finalId, points: cfg.points, styles: cfg.styles });
          }
        }
      } catch (e) {}
      return finalId;
    } catch (e) {
      console.warn('[SessionUI] createHl', cfg.name, e);
      return null;
    }
  }

  function _forcePaint(chart) {
    try {
      if (typeof chart.resize === 'function') chart.resize();
    } catch (e) {}
  }

  function highlightElementOnChart(el) {
    clearChartHighlight();
    if (!el) {
      toast('元素数据缺失');
      return;
    }
    const chart = getChart();
    if (!chart) {
      toast('图表未就绪');
      return;
    }
    const kind = el.kind;
    const d = el.data || {};
    const list = getDataList();

    if (kind === 'kbar') {
      const ts = d.timestamp;
      if (ts == null) {
        toast('该K元素无时间戳，无法高亮');
        return;
      }
      const located = centerChartOnTimestamp(chart, ts);
      const bar = located && located.bar ? located.bar : findNearestBar(ts);
      if (!bar) {
        toast('图上找不到对应K线');
        return;
      }
      const price = Number(
        d.price != null
          ? d.price
          : d.close != null
            ? d.close
            : bar.close
      );
      const hi = Number(d.high != null ? d.high : bar.high);
      const lo = Number(d.low != null ? d.low : bar.low);
      const idx = located && located.idx >= 0 ? located.idx : list.length - 1;
      const left = list[Math.max(0, idx - 2)] || bar;
      const right = list[Math.min(list.length - 1, idx + 2)] || bar;
      const lineStyles = { line: { style: 'solid', color: '#FF6D00', size: 2 } };
      const markStyles = { line: { style: 'solid', color: '#FFD600', size: 3 } };

      // 1) 价格水平线（最醒目、klinecharts 稳定支持）
      _createHlOverlay(chart, {
        id: _mkHlId('price'),
        name: 'horizontalStraightLine',
        points: [{ timestamp: bar.timestamp, value: price }],
        styles: lineStyles,
      });
      // 2) 顶/底横档 + 左右竖档 → 框住该K
      _createHlOverlay(chart, {
        id: _mkHlId('top'),
        name: 'segment',
        points: [
          { timestamp: left.timestamp, value: hi },
          { timestamp: right.timestamp, value: hi },
        ],
        styles: markStyles,
      });
      _createHlOverlay(chart, {
        id: _mkHlId('bot'),
        name: 'segment',
        points: [
          { timestamp: left.timestamp, value: lo },
          { timestamp: right.timestamp, value: lo },
        ],
        styles: markStyles,
      });
      _createHlOverlay(chart, {
        id: _mkHlId('left'),
        name: 'segment',
        points: [
          { timestamp: left.timestamp, value: lo },
          { timestamp: left.timestamp, value: hi },
        ],
        styles: markStyles,
      });
      _createHlOverlay(chart, {
        id: _mkHlId('right'),
        name: 'segment',
        points: [
          { timestamp: right.timestamp, value: lo },
          { timestamp: right.timestamp, value: hi },
        ],
        styles: markStyles,
      });
      // 3) 标注文字
      _createHlOverlay(chart, {
        id: _mkHlId('ann'),
        name: 'simpleAnnotation',
        points: [{ timestamp: bar.timestamp, value: hi }],
        extendData: String(d.date || d.price_element || 'K'),
        styles: {
          point: { color: '#FF6D00', borderColor: '#fff', borderSize: 2, radius: 6 },
          text: {
            color: '#fff',
            size: 12,
            backgroundColor: '#FF6D00',
            borderRadius: 2,
            paddingLeft: 4,
            paddingRight: 4,
            paddingTop: 2,
            paddingBottom: 2,
          },
        },
      });

      _forcePaint(chart);
      const ok = _hlOverlayIds.length > 0;
      toast(
        ok
          ? '已高亮 K ' + (d.date || '') + ' @' + price
          : '高亮创建失败，请检查图表'
      );
      return;
    }

    if (kind === 'overlay') {
      const oid = d.id != null ? String(d.id) : '';
      const ptsRaw = d.points || [];
      const pts = ptsRaw
        .map((p) => ({
          timestamp: p.timestamp != null ? p.timestamp : p.time,
          value: p.value != null ? p.value : p.price,
        }))
        .filter((p) => p.timestamp != null && p.value != null && isFinite(Number(p.value)));

      if (pts.length) {
        centerChartOnTimestamp(chart, pts[0].timestamp);
      }

      let highlighted = false;
      // 优先：图上已有同 id 画线 → overrideOverlay 加粗变色
      if (oid && typeof chart.getOverlayById === 'function') {
        try {
          const inst = chart.getOverlayById(oid);
          if (inst) {
            _hlOverrideStyles = inst.styles ? JSON.parse(JSON.stringify(inst.styles)) : {};
            _hlOverrideId = oid;
            if (typeof chart.overrideOverlay === 'function') {
              chart.overrideOverlay({
                id: oid,
                styles: {
                  line: { style: 'solid', color: '#FFD600', size: 4 },
                  point: {
                    color: '#FFD600',
                    borderColor: '#fff',
                    borderSize: 2,
                    radius: 6,
                  },
                },
              });
              highlighted = true;
            }
          }
        } catch (e) {
          console.warn('[SessionUI] overrideOverlay', e);
        }
      }

      // 回退：用快照重绘高亮线（粗黄）
      if (!highlighted && pts.length) {
        const name = d.type || d.name || 'segment';
        // 单点线型
        const onePoint = ['horizontalStraightLine', 'horizontalRayLine', 'horizontalSegment', 'priceLine', 'simpleAnnotation'];
        const usePts =
          onePoint.indexOf(name) >= 0
            ? pts.slice(0, 1)
            : pts.length >= 2
              ? pts
              : pts.concat(pts); // 复制一点凑 segment
        _createHlOverlay(chart, {
          id: _mkHlId('ov'),
          name: pts.length >= 2 || onePoint.indexOf(name) >= 0 ? name : 'segment',
          points: usePts.length >= 2 || onePoint.indexOf(name) >= 0 ? usePts : [
            usePts[0],
            {
              timestamp: usePts[0].timestamp,
              value: Number(usePts[0].value) * 1.001,
            },
          ],
          styles: { line: { style: 'solid', color: '#FFD600', size: 4 } },
        });
        // 再加价格水平参考
        _createHlOverlay(chart, {
          id: _mkHlId('ovp'),
          name: 'horizontalStraightLine',
          points: [{ timestamp: pts[0].timestamp, value: Number(pts[0].value) }],
          styles: { line: { style: 'solid', color: '#FF6D00', size: 2 } },
        });
        highlighted = _hlOverlayIds.length > 0;
      }

      _forcePaint(chart);
      toast(
        highlighted
          ? '已高亮画线 ' + (d.type || oid || '')
          : '无法高亮：画线无有效坐标点'
      );
      return;
    }

    if (kind === 'note') {
      toast('备注：' + String((d.text || '').slice(0, 60)));
      return;
    }
    toast('未知元素类型: ' + kind);
  }

  async function focusElement(elementId, eventId) {
    if (!S || !elementId) return;
    try {
      // 先在本地找到元素（避免 actApi 后状态竞态）
      const eid = eventId || (S.ui || {}).active_event_id;
      let ev = (S.events || []).find((e) => e.id === eid);
      let el = ((ev && ev.elements) || []).find((x) => x.id === elementId);
      if (!el) {
        // 全局搜
        for (const e of S.events || []) {
          const found = (e.elements || []).find((x) => x.id === elementId);
          if (found) {
            el = found;
            ev = e;
            break;
          }
        }
      }
      // 先高亮（不等待网络），再同步焦点到后端
      if (el) {
        highlightElementOnChart(el);
      }
      await actApi(
        'focus_element',
        {
          element_id: elementId,
          event_id: (ev && ev.id) || eid,
        },
        { skipFlush: true }
      );
      // actApi 会 render，高亮 overlay 仍保留在 chart 上；若被清则再刷一次
      const el2 = (() => {
        for (const e of S.events || []) {
          const f = (e.elements || []).find((x) => x.id === elementId);
          if (f) return f;
        }
        return el;
      })();
      if (el2 && !_hlOverlayIds.length && !_hlOverrideId) {
        highlightElementOnChart(el2);
      }
    } catch (e) {
      toast(e.message);
    }
  }

  async function deleteElement(elementId, eventId) {
    if (!S || !elementId) return;
    if (!confirm('删除此元素？')) return;
    try {
      clearChartHighlight();
      await actApi('delete_element', {
        element_id: elementId,
        event_id: eventId || (S.ui || {}).active_event_id,
      });
      toast('元素已删除');
    } catch (e) {
      toast(e.message);
    }
  }

  async function deleteEvent(eventId) {
    if (!S || !eventId) return;
    if (!confirm('删除此事件？其下全部元素（K/画线/备注）将一并移除。')) return;
    try {
      await actApi('delete_event', { event_id: eventId });
      toast('事件已删除');
    } catch (e) {
      toast(e.message);
    }
  }

  async function deleteCause(causeId) {
    if (!S || !causeId) return;
    const c = (S.causes || []).find((x) => x.id === causeId);
    const depth = c && c.depth != null ? c.depth : '?';
    if (
      !confirm(
        '删除因果链 L' +
          depth +
          '？\n将同时删除其子链、配对果，以及挂在这些链上的全部事件与采集数据。'
      )
    )
      return;
    try {
      await actApi('delete_cause', { cause_id: causeId, recursive: true });
      toast('因果链已删除');
    } catch (e) {
      toast(e.message);
    }
  }

  async function focusCause(causeId) {
    if (!S) return;
    try {
      await actApi('focus_cause', { cause_id: causeId });
      // P0-4: 取消事件过滤，恢复全显
      filterOverlaysByEvent(null);
      toast('已聚焦因侧（非事件）');
    } catch (e) {
      toast(e.message);
      render();
    }
  }

  async function focusEvent(eventId) {
    if (!S) return;
    try {
      await actApi('focus_event', { event_id: eventId });
      // P0-4: 画线按事件显示
      filterOverlaysByEvent(eventId);
      toast('已聚焦事件');
    } catch (e) {
      toast(e.message);
      render();
    }
  }

  // P0-4: 画线按事件显示
  // 聚焦事件时，仅高亮该事件的 overlays；其他 events / chain 汇总的 overlays 变灰
  // eventId=null 时恢复全显
  function filterOverlaysByEvent(eventId) {
    const chart = window.__kline_chart;
    if (!chart || typeof chart.overrideOverlay !== 'function') return;
    const store = chart.getChartStore && chart.getChartStore();
    if (!store || typeof store.getOverlayStore !== 'function') return;
    const overlayStore = store.getOverlayStore();
    if (!overlayStore || typeof overlayStore.getInstances !== 'function') return;
    const all = overlayStore.getInstances() || [];

    // 当前事件的 overlay id 集合
    let eventOverlayIds = new Set();
    if (eventId) {
      const ev = (S.events || []).find((e) => e.id === eventId);
      if (ev) {
        for (const el of ev.elements || []) {
          if (el.kind === 'overlay' && el.data && el.data.id) {
            eventOverlayIds.add(String(el.data.id));
          }
        }
      }
    }

    all.forEach((inst) => {
      if (!inst || !inst.id) return;
      const id = String(inst.id);
      // 高亮线 sess_hl_* 永远显示
      if (id.indexOf('sess_hl_') === 0) return;
      // 事件聚焦：仅该事件的 overlays 满色，其他降透明度
      if (eventId) {
        if (eventOverlayIds.has(id)) {
          // 恢复默认
          if (_dimmedOverlayIds && _dimmedOverlayIds.has(id)) {
            _restoreOverlayStyle(chart, inst);
            _dimmedOverlayIds.delete(id);
          }
        } else {
          if (!_dimmedOverlayIds) _dimmedOverlayIds = new Set();
          if (!_dimmedOverlayIds.has(id)) {
            _dimOverlayStyle(chart, inst);
            _dimmedOverlayIds.add(id);
          }
        }
      } else {
        // 恢复全显
        if (_dimmedOverlayIds && _dimmedOverlayIds.has(id)) {
          _restoreOverlayStyle(chart, inst);
          _dimmedOverlayIds.delete(id);
        }
      }
    });
  }

  // 内部状态：被变灰的 overlay id
  let _dimmedOverlayIds = new Set();
  // 内部状态：原 styles 备份（用于恢复）
  const _origStyles = new Map();

  function _dimOverlayStyle(chart, inst) {
    if (!inst) return;
    const id = String(inst.id);
    // 备份原 styles（用于恢复）
    if (!_origStyles.has(id)) {
      _origStyles.set(id, JSON.parse(JSON.stringify(inst.styles || {})));
    }
    // 构造降透明度样式
    const dimStyles = JSON.parse(JSON.stringify(inst.styles || {}));
    const dimLine = { ...(dimStyles.line || {}), opacity: 0.15, size: 1 };
    dimStyles.line = dimLine;
    try {
      chart.overrideOverlay({ id, styles: dimStyles });
    } catch (_) {}
  }

  function _restoreOverlayStyle(chart, inst) {
    if (!inst) return;
    const id = String(inst.id);
    const orig = _origStyles.get(id);
    if (!orig) return;
    try {
      chart.overrideOverlay({ id, styles: orig });
    } catch (_) {}
    _origStyles.delete(id);
  }

  async function onClickEffectId(effectId, causeId) {
    if (!S) return;
    try {
      if (causeId && (S.ui || {}).active_cause_id !== causeId) {
        await actApi('focus_cause', { cause_id: causeId }, { skipFlush: true });
      }
      await actApi('click_effect', { effect_id: effectId });
      const now = (S.effects || []).find((e) => e.id === effectId);
      if (now && now.phase === 'collecting') toast('果侧采集中（再点该果闭合）');
      else if (now && now.phase === 'closed') toast('该层因果链已闭合');
    } catch (e) {
      toast(e.message);
    }
  }

  /** 无选中→根链；有选中→成为该链的子链（子集，带缩进） */
  async function addCauseNested() {
    const ui = S.ui || {};
    const curId = ui.active_cause_id;
    const cur = (S.causes || []).find((c) => c.id === curId);
    if (!cur) {
      await actApi('root_cause', { title: '因' });
      toast('新增根层因果链 L0');
      return;
    }
    await actApi('child_cause', { parent_id: cur.id, title: '因' });
    const d = (cur.depth != null ? cur.depth : 0) + 1;
    toast('已在当前链下嵌套子链 L' + d);
  }

  async function onTool(act) {
    try {
      if (act === 'list') return openList();
      if (act === 'new') return onNewSession();
      if (act === 'save') return onSaveCommit();
      // 「因」：无选中 → 根链；已选中某链 → 在其下嵌套子链（层级缩进）
      if (act === 'cause') {
        await ensureSession();
        return addCauseNested();
      }
      // 「缩进」：必须先选中链，再嵌套子链
      if (act === 'indent') {
        await ensureSession();
        if (!(S.ui || {}).active_cause_id) {
          toast('先点大纲中的「因」选中链，再缩进');
          return;
        }
        return addCauseNested();
      }
      if (act === 'effect') return onClickEffect();
      if (act === 'event') {
        await ensureSession();
        const cid = (S.ui || {}).active_cause_id;
        if (!cid) {
          toast('先点大纲中的「因」选中因果链，再添加事件');
          return;
        }
        const chain = (S.causes || []).find((c) => c.id === cid);
        const depth = chain && chain.depth != null ? chain.depth : 0;
        await actApi('start_event', { cause_id: cid });
        toast('已在 L' + depth + ' 链添加事件（仅主动添加）');
        return;
      }
      if (act === 'browse') return setTool('browse');
      if (act === 'pick_k') return togglePickK();
      if (act === 'note') return onNote();
      if (act === 'refresh') {
        if (S) await flushOverlaysLocal();
        render();
        toast('已刷新');
      }
    } catch (e) {
      console.error('[SessionUI] onTool', act, e);
      toast(e.message || String(e));
    }
  }

  let _flushing = false;
  async function actApi(action, extra, opts) {
    await ensureSession();
    // 避免 flushOverlaysLocal → root_cause → actApi → flush 死循环
    if (!(opts && opts.skipFlush) && !_flushing) {
      await flushOverlaysLocal();
    }
    const body = Object.assign({ action, session: S }, extra || {});
    const j = await api('/api/sessions/' + S.id + '/actions', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    S = j.data;
    render();
    scheduleSave(false);
    return S;
  }

  // alias used by older code paths
  async function act(action, extra) {
    return actApi(action, extra);
  }

  async function onClickEffect() {
    await ensureSession();
    const ui = S.ui || {};
    let ef = (S.effects || []).find((e) => e.id === ui.active_effect_id);
    if (!ef && ui.active_cause_id) {
      ef = (S.effects || []).find((e) => e.cause_id === ui.active_cause_id);
    }
    if (!ef) {
      toast('请先点「因」或「缩进」');
      return;
    }
    try {
      await actApi('click_effect', { effect_id: ef.id });
      const now = (S.effects || []).find((e) => e.id === ef.id);
      if (now && now.phase === 'collecting') toast('果侧采集中（再点「果」闭合）');
      else if (now && now.phase === 'closed') toast('该层因果已闭合');
    } catch (e) {
      toast(e.message, 4000);
    }
  }

  async function ensureSession(opts) {
    // 已提交会话不可再改：需要可写会话时自动新开
    const needWritable = !(opts && opts.allowCommitted);
    if (S) {
      if (!needWritable || S.status !== 'committed') return S;
      // fall through → 新建
    } else {
      const j = await api('/api/sessions/active');
      // 已提交 / 无活跃 → 新开干净会话（大纲为空，点按钮才长）
      if (j.data && (j.data.status === 'drafting' || j.data.status === 'paused')) {
        S = j.data;
        render();
        await ensureCurrentChart();
        return S;
      }
    }
    const c = await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        title: '会话',
        save_payload: S || undefined,
      }),
    });
    S = c.data;
    render();
    await ensureCurrentChart();
    return S;
  }

  async function ensureCurrentChart() {
    if (!S) return;
    const ctx = panelCtx();
    const j = await api('/api/sessions/' + S.id + '/actions', {
      method: 'POST',
      body: JSON.stringify({
        action: 'ensure_chart',
        session: S,
        symbol: ctx.symbol,
        period: ctx.period,
        symbol_name: ctx.symbol_name,
        asset_type: ctx.asset_type,
      }),
    });
    S = j.data;
    render();
  }

  async function onNewSession() {
    // 有可写会话则先落盘画线；已提交则直接开新会话
    if (S && S.status !== 'committed') {
      try {
        await flushOverlaysLocal();
      } catch (e) {
        console.warn('[SessionUI] flush before new', e);
      }
    }
    const j = await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ save_payload: S || undefined, title: '会话' }),
    });
    S = j.data;
    toast(S ? '已开新会话' : '新会话');
    render();
    await ensureCurrentChart();
  }

  async function onSaveCommit() {
    await ensureSession({ allowCommitted: true });
    if (!S) {
      toast('无会话可保存');
      return;
    }
    if (S.status === 'committed') {
      if (confirm('该会话已定稿（只读）。是否克隆为可编辑的新会话？')) {
        await cloneCurrentSession();
        return;
      }
      return;
    }
    await flushOverlaysLocal();
    try {
      const r = await saveSessionWithRevCheck();
      S = r.data;
    } catch (e) {
      if (e && e.code === 'REVISION_CONFLICT') return handleRevisionConflict(e);
      if (e && e.code === 'COMMITTED_READONLY') {
        toast('会话已定稿：请先克隆为新会话');
        return;
      }
      throw e;
    }
    const j = await api('/api/sessions/' + S.id + '/commit', {
      method: 'POST',
      body: JSON.stringify({ session: S }),
    });
    S = j.data;
    render();
    toast('已写入 Obsidian');
    if (S.vault && S.vault.obsidian_uri && confirm('在 Obsidian 打开？')) {
      global.location.href = S.vault.obsidian_uri;
    }
  }

  // 克隆当前会话（committed 时常用）
  async function cloneCurrentSession() {
    if (!S || !S.id) {
      toast('无会话可克隆');
      return;
    }
    try {
      const r = await api('/api/sessions/' + S.id + '/clone', { method: 'POST' });
      S = r.data;
      render();
      toast('已克隆为新会话：' + (S.title || ''));
    } catch (e) {
      toast('克隆失败：' + e.message);
    }
  }

  // 会话回放：切到会话当前 chart 的 symbol/period（K 线自动重载）
  async function applySessionReplay(sessionId, opts) {
    opts = opts || {};
    const r = await api(
      '/api/sessions/' + sessionId + '/replay' +
        (opts.chart_id ? '?chart_id=' + encodeURIComponent(opts.chart_id) : '') +
        (opts.event_id ? (opts.chart_id ? '&' : '?') + 'event_id=' + encodeURIComponent(opts.event_id) : '')
    );
    const data = (r && r.data) || {};
    const chart = data.chart;
    if (!chart) {
      // 会话无 chart：跳过 replay（前端可走 ensure_chart）
      return { skipped: true, reason: 'no chart in session' };
    }
    const pro = window.pro;
    if (!pro || typeof pro.setSymbol !== 'function') {
      return { skipped: true, reason: 'pro not ready' };
    }
    // 已显示同 symbol+period → 跳过（避免无谓重载）
    const cur = window.__board_ctx || {};
    if (cur.symbol === chart.symbol && cur.period === chart.period) {
      return { skipped: true, reason: 'same chart' };
    }
    // setSymbol 触发 K 线重载；overlays 走 collectOverlays 自动同步到 session
    pro.setSymbol({
      ticker: chart.symbol,
      name: chart.symbol_name || chart.symbol,
      type: chart.asset_type || (chart.symbol && chart.symbol.startsWith('sh') ? 'index' : 'stock'),
      market: 'A',
      priceCurrency: 'CNY',
    });
    // 切周期（如果 pro 支持）
    if (chart.period && pro.setPeriod && cur.period !== chart.period) {
      try { pro.setPeriod(chart.period); } catch (_) {}
    }
    window.__board_ctx = Object.assign({}, window.__board_ctx, {
      symbol: chart.symbol,
      period: chart.period,
    });
    toast('已回放到：' + (chart.symbol_name || chart.symbol) + ' ' + (chart.period || ''));
    return { ok: true, chart: chart, overlays: data.overlays || [] };
  }

  // 把当前 S 同步到服务端，带 base_rev；遇 409 抛 RevisionConflictError 给上层处理
  async function saveSessionWithRevCheck() {
    return await api('/api/sessions/' + S.id, {
      method: 'PUT',
      body: JSON.stringify(
        Object.assign({}, S, { base_rev: S.rev || 0, write_vault: true })
      ),
    });
  }

  // 处理 REVISION_CONFLICT：弹窗让用户选择「应用服务端版本」/「取消」
  function handleRevisionConflict(conflict) {
    const cur = conflict.current_session || {};
    const curRev = conflict.current_rev;
    const localRev = (S && S.rev) || 0;
    const msg = `会话已被另一端修改（rev ${localRev}→${curRev}）。` +
      `点击「应用服务端版本」刷新本地；或「取消」稍后重试。`;
    if (confirm(msg)) {
      S = cur;
      render();
      toast('已应用服务端版本，请重新编辑后保存');
    } else {
      toast('已取消：本地版本可能与他端冲突，请手动合并');
    }
  }

  async function onNote() {
    const text = ((document.getElementById('sess-note') || {}).value || '').trim();
    if (!text) {
      toast('请先输入备注');
      return;
    }
    try {
      await ensureSession();
      const ui = S.ui || {};
      if (!ui.active_cause_id && !ui.active_event_id) {
        toast('先选中因果链或事件，再写备注');
        return;
      }
      await actApi('note', { text });
      const el = document.getElementById('sess-note');
      if (el) el.value = '';
      toast(ui.active_event_id ? '备注已追加为元素' : '备注已写入因/果汇总');
    } catch (e) {
      toast(e.message);
    }
  }

  async function setTool(tool) {
    await ensureSession();
    pickKActive = tool === 'pick_k';
    await actApi('set_ui', { tool }, { skipFlush: true });
    toast(tool === 'pick_k' ? '选K已开启：点图采集一根K，再点「选K」可关闭' : '浏览');
    bindChartClick();
  }

  /** 选K：开关切换；开启后单次采集成功会自动关闭，避免连续触发 */
  async function togglePickK() {
    await ensureSession();
    const on = pickKActive || ((S.ui || {}).tool === 'pick_k');
    if (on) {
      pickKActive = false;
      await actApi('set_ui', { tool: 'browse' }, { skipFlush: true });
      toast('已关闭选K');
      return;
    }
    pickKActive = true;
    await actApi('set_ui', { tool: 'pick_k' }, { skipFlush: true });
    toast('选K已开启：点击图上K线采集（完成一根后自动关闭）');
    bindChartClick();
  }

  async function turnOffPickK(silent) {
    if (!pickKActive && ((S && S.ui) || {}).tool !== 'pick_k') return;
    pickKActive = false;
    try {
      if (S) await actApi('set_ui', { tool: 'browse' }, { skipFlush: true });
    } catch (e) {}
    if (!silent) toast('选K已关闭');
  }

  async function openList() {
    const drawer = document.getElementById('sess-list-drawer');
    if (!drawer) return;
    drawer.style.display = 'block';
    const j = await api('/api/sessions?limit=40');
    const items = j.data || [];
    drawer.innerHTML =
      '<div style="display:flex;justify-content:space-between;margin-bottom:8px"><b>会话列表</b><button class="btn" id="sess-list-close">关闭</button></div>' +
      items
        .map((s) => {
          const ro = s.status === 'committed';
          return `<div class="tree-item" data-sid="${esc(s.id)}">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b>${esc(s.title || s.id)}</b>
            ${ro ? '<span style="background:#7f8c8d;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px">只读</span>' : ''}
          </div>
          <div class="muted">${esc(s.status)} · 图${s.chart_count} · 因${s.cause_count} · 未闭合果${s.open_effects}</div>
          ${ro ? '<button class="btn small sess-clone" data-sid="' + esc(s.id) + '" style="margin-top:4px">另存为新会话</button>' : ''}
        </div>`;
        })
        .join('') || '<div class="empty">暂无</div>';
    document.getElementById('sess-list-close').onclick = () => {
      drawer.style.display = 'none';
    };
    drawer.querySelectorAll('[data-sid]').forEach((el) => {
      el.onclick = async () => {
        await flushOverlaysLocal();
        const j2 = await api(
          '/api/sessions/' + el.getAttribute('data-sid') + '/activate',
          { method: 'POST', body: JSON.stringify({ save_payload: S }) }
        );
        S = j2.data;
        drawer.style.display = 'none';
        render();
        toast(S.status === 'committed' ? '已进入只读会话' : '已切换会话');
        // 回放：自动切到会话记录的图表
        try {
          await applySessionReplay(S.id);
        } catch (e) {
          console.warn('[SessionUI] replay', e);
        }
      };
    });
    // 「另存为新会话」按钮：clone
    drawer.querySelectorAll('.sess-clone').forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const sid = btn.getAttribute('data-sid');
        try {
          const r = await api('/api/sessions/' + sid + '/clone', { method: 'POST' });
          drawer.style.display = 'none';
          S = r.data;
          render();
          toast('已克隆为新会话（可编辑）');
        } catch (e) {
          toast('克隆失败：' + e.message);
        }
      };
    });
  }

  function scheduleSave(writeVault) {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      if (!S) return;
      if (S.status === 'committed') return; // 只读，跳过自动保存
      try {
        await flushOverlaysLocal();
        const j = await api('/api/sessions/' + S.id, {
          method: 'PUT',
          body: JSON.stringify(
            Object.assign({}, S, {
              base_rev: S.rev || 0,
              write_vault: !!writeVault,
            })
          ),
        });
        S = j.data;
        render();
      } catch (e) {
        if (e && e.code === 'REVISION_CONFLICT') {
          handleRevisionConflict(e);
          return;
        }
        if (e && e.code === 'COMMITTED_READONLY') {
          return;
        }
        console.warn('[SessionUI] save', e);
      }
    }, 600);
  }

  async function flushOverlaysLocal() {
    if (!S || _flushing) return;
    _flushing = true;
    try {
      const ovs = collectOverlays();
      const vr = getVisibleRange();
      const cid = S.current_chart_id;
      if (cid) {
        const ch = (S.charts || []).find((c) => c.id === cid);
        if (ch) {
          ch.overlays = ovs;
          if (vr) ch.visible_range = vr;
        }
      }
      let ui = S.ui || {};
      // 图上已有画线但还没有因：自动建根因，避免「画了却采不到」
      if (ovs.length && !ui.active_cause_id) {
        try {
          await actApi('root_cause', { title: '根因(自动)' }, { skipFlush: true });
          ui = (S && S.ui) || {};
          toast('已自动创建根因并绑定画线');
        } catch (e) {
          console.warn('[SessionUI] auto root_cause', e);
        }
      }
      // 有链 / 果 / 事件焦点时同步画线（事件焦点时归入事件）
      if (ui.active_cause_id || ui.active_effect_id || ui.active_event_id) {
        try {
          const j = await api('/api/sessions/' + S.id + '/actions', {
            method: 'POST',
            body: JSON.stringify({
              action: 'overlays',
              session: S,
              overlays: ovs,
              chart_id: cid,
            }),
          });
          S = j.data;
        } catch (e) {
          console.warn('[SessionUI] sync overlays', e);
        }
      }
    } finally {
      _flushing = false;
    }
  }

  function bindChartClick() {
    if (chartClickBound) return;
    const wrap =
      document.querySelector('.klinecharts-pro') ||
      document.getElementById('chart') ||
      document.body;
    wrap.addEventListener(
      'click',
      async (ev) => {
        if (!pickKActive || !S) return;
        if (ev.target.closest && ev.target.closest('#sess-side')) return;
        let ts = null, price = null;
        if (global.__sess_crosshair) {
          ts = global.__sess_crosshair.timestamp;
          price = global.__sess_crosshair.value;
        }
        const list = getDataList();
        if (!list.length) return;
        if (ts == null) {
          const last = list[list.length - 1];
          ts = last.timestamp;
          price = last.close;
        }
        const bar = findNearestBar(ts);
        if (!bar) return;
        const kb = barToKbar(bar, price);
        try {
          await actApi('kbars', { kbars: [kb] });
          const into = (S.ui || {}).active_event_id ? '→元素·K' : '→因/果';
          toast(
            '选K ' +
              (kb.date || '') +
              ' ' +
              (kb.price_element || '') +
              '@' +
              kb.price +
              ' 量' +
              fmtVol(kb.volume) +
              ' ' +
              into
          );
          // 单次采集后自动关闭选K，避免连续触发
          await turnOffPickK(true);
          // 新元素自动高亮
          if ((S.ui || {}).active_element_id) {
            const sev = (S.events || []).find((e) => e.id === S.ui.active_event_id);
            const el = ((sev && sev.elements) || []).find(
              (x) => x.id === S.ui.active_element_id
            );
            if (el) highlightElementOnChart(el);
          }
        } catch (e) {
          toast(e.message);
        }
      },
      true
    );
    chartClickBound = true;
    const chart = getChart();
    if (chart && typeof chart.subscribeAction === 'function') {
      try {
        chart.subscribeAction('onCrosshairChange', (data) => {
          if (!data) return;
          global.__sess_crosshair = {
            timestamp: data.timestamp || (data.kLineData && data.kLineData.timestamp),
            value: data.value != null ? data.value : data.price,
          };
        });
      } catch (e) {}
    }
  }

  function bindOverlayHooks() {
    const chart = getChart();
    if (!chart || overlayHookBound) return;
    const flush = (tag) => {
      flushOverlaysLocal().then(() => {
        const n = collectOverlays().length;
        if (tag) toast((tag || '画线') + ' · 当前 ' + n + ' 条');
        render();
        scheduleSave(false);
      });
    };
    try {
      const store = getOverlayStore(chart);
      if (store) {
        const bindCb = (names, fn) => {
          for (const n of names) {
            if (typeof store[n] === 'function') {
              try {
                store[n](fn);
                return n;
              } catch (e) {}
            }
          }
          return null;
        };
        // 注意：不要绑 setOnDrawing* —— 鼠标移动会狂触发 scroll 导致乱跳
        // 新开一笔绘制时重置「本轮已居中」标记
        bindCb(['setOnDrawStartCallback', 'setOnDrawStart'], function () {
          global.__sess_draw_center_key = '';
        });
        const endName = bindCb(
          ['setOnDrawEndCallback', 'setOnDrawEnd'],
          function (ov) {
            console.log('[SessionUI] onDrawEnd', ov && (ov.id || ov.name));
            // 水平线画完：按起点 K 居中一次（本轮仅一次）
            try {
              centerOnHorizontalDrawStart(ov, { fromDrawEnd: true });
            } catch (e) {}
            // 下一笔可再居中
            setTimeout(function () {
              global.__sess_draw_center_key = '';
            }, 80);
            flush('画线完成');
          }
        );
        bindCb(['setOnRemovedCallback', 'setOnRemoved'], function () {
          flush('画线删除');
        });
        console.log('[SessionUI] drawEnd callback via', endName || 'poll-only');
      }
      // 轮询：绘制中不 flush/render；仅在首点落定后居中一次
      if (!global.__sess_ov_poll) {
        let lastSig = '';
        global.__sess_ov_poll = setInterval(() => {
          const c = getChart();
          if (!c) return;
          const st = getOverlayStore(c);
          let drawing = false;
          try {
            drawing = !!(st && typeof st.isDrawing === 'function' && st.isDrawing());
          } catch (e) {}

          if (drawing) {
            try {
              if (st && typeof st.getProgressInstanceInfo === 'function') {
                const prog = st.getProgressInstanceInfo();
                const inst = prog && prog.instance;
                // 仅当首点已落定（step>=1）才居中；跟手阶段不滚
                if (inst) centerOnHorizontalDrawStart(inst, { fromDrawEnd: false });
              }
            } catch (e) {}
            // 绘制过程中禁止同步/重绘面板，避免跳动与抢焦点
            return;
          }

          const ovs = collectOverlays();
          const sig = ovs
            .map((o) => o.id + ':' + (o.points && o.points[0] && o.points[0].value))
            .join('|');
          if (sig !== lastSig) {
            lastSig = sig;
            flushOverlaysLocal().then(() => render());
          }
        }, 600);
      }
      overlayHookBound = true;
      console.log(
        '[SessionUI] overlay hooks bound; sample count=',
        collectOverlays().length
      );
    } catch (e) {
      console.warn('[SessionUI] bindOverlayHooks', e);
    }
  }

  function hookCtxPoll() {
    setInterval(async () => {
      if (!S) return;
      const ctx = panelCtx();
      const key = ctx.symbol + '|' + ctx.period;
      if (key !== lastKey) {
        lastKey = key;
        try {
          await ensureCurrentChart();
          bindOverlayHooks();
          bindChartClick();
          render();
        } catch (e) {}
      } else {
        // 轻量刷新图上画线计数
        const el = document.getElementById('sess-body');
        if (el && collectOverlays().length >= 0) {
          /* keep live panel in sync without full re-render every second if expensive */
        }
      }
    }, 1200);
    // 定时刷新采集面板上的「图上当前画线」
    setInterval(() => {
      if (S && document.getElementById('sess-side')) render();
    }, 2500);
  }

  async function boot() {
    ensureUI();
    // 若 chart 已在模块加载前 init，直接用；否则等事件 / 轮询
    global.addEventListener('kline-chart-ready', () => {
      overlayHookBound = false;
      bindOverlayHooks();
      bindChartClick();
      render();
    });
    try {
      await ensureSession();
      bindChartClick();
      bindOverlayHooks();
      hookCtxPoll();
      let n = 0;
      const t = setInterval(() => {
        n++;
        if (getChart()) {
          bindChartClick();
          bindOverlayHooks();
          render();
          clearInterval(t);
        } else if (n > 80) {
          console.warn('[SessionUI] 未找到底层 chart，画线采集不可用。请硬刷新页面。');
          clearInterval(t);
        }
      }, 400);
    } catch (e) {
      toast('会话模块: ' + e.message);
    }
  }

  global.SessionUI = {
    getSession: () => S,
    ensureSession,
    render,
    save: onSaveCommit,
    newSession: onNewSession,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
