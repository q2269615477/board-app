/**
 * session-render.js — pure HTML projection for the session panel.
 *
 * The renderer only consumes the supplied session snapshot, panel context,
 * chart overlays, and render options.  It never reads browser state, binds
 * events, performs I/O, or retains mutable session state.
 */
(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.SessionRender = exported;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function formatVolume(v) {
    if (v == null || v === '') return '—';
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return String(Math.round(n * 100) / 100);
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/"/g, '&quot;');
  }

  function renderSessionBody(session, context, chartOverlays, options) {
    if (!session) return '<div class="empty">无会话</div>';

    var snapshot = session;
    var ui = snapshot.ui || {};
    var ctx = context || {};
    var chartOvs = Array.isArray(chartOverlays) ? chartOverlays : [];
    var opts = options || {};
    var pickKActive = !!opts.pickKActive;
    var causes = Array.isArray(snapshot.causes) ? snapshot.causes : [];
    var effects = Array.isArray(snapshot.effects) ? snapshot.effects : [];
    var events = Array.isArray(snapshot.events) ? snapshot.events : [];
    var activeC = causes.find(function (c) { return c.id === ui.active_cause_id; });
    var activeE = effects.find(function (e) { return e.id === ui.active_effect_id; });
    var causeMap = {};
    causes.forEach(function (c) { causeMap[c.id] = c; });
    var eventMap = {};
    events.forEach(function (ev) { eventMap[ev.id] = ev; });

    function effectOf(causeId) {
      return effects.find(function (e) { return e.cause_id === causeId; });
    }

    function indentHtml(depth) {
      if (depth <= 0) return '';
      var h = '<span class="ol-indent">';
      for (var i = 0; i < depth; i += 1) h += '<span class="col"></span>';
      return h + '</span>';
    }

    function renderChain(causeId, depth) {
      var c = causeMap[causeId];
      if (!c) return '';
      var ef = effectOf(c.id);
      var phase = (ef && ef.phase) || 'idle';
      var isChain = c.id === ui.active_cause_id;
      var causeActive = isChain && !ui.active_event_id && (ui.side || 'cause') === 'cause';
      var effectActive = isChain && !ui.active_event_id && ui.side === 'effect';
      var collapsed = ((ui.collapsed_chains || {})[c.id]) === true;
      var childChains = causes.filter(function (ch) { return ch.parent_id === c.id; });
      var childEvents = events.filter(function (ev) { return ev.cause_id === c.id && ev.id; });
      var childCount = childChains.length + childEvents.length;
      var causeCls = 'ol-node ol-cause' + (causeActive ? ' active' : '');
      if (c.state === 'closed') causeCls += ' closed';
      var effectCls =
        'ol-node ol-effect' +
        (effectActive ? ' active' : '') +
        (phase === 'collecting' ? ' collecting' : '') +
        (phase === 'closed' ? ' closed' : '');

      var wrapCls = 'ol-chain-wrap' + (isChain ? ' focused' : '') + (collapsed ? ' collapsed' : '');
      var html = '<div class="' + wrapCls + '" data-depth="' + depth + '" data-chain="' +
        escapeHtml(c.id) + '">';
      var collapseBtn = childCount > 0
        ? '<span class="ol-collapse" data-toggle-collapse="' + escapeHtml(c.id) +
          '" title="' + (collapsed ? '展开' : '折叠') + '子项">' +
          (collapsed ? '▶' : '▼') + '</span>'
        : '';
      var childCountBadge = collapsed && childCount > 0
        ? '<span class="ol-collapse-count" title="已折叠 ' + childCount + ' 个子项">+' +
          childCount + '</span>'
        : '';
      html += '<div class="ol-line" data-depth="' + depth + '" data-toggle-line="1">\n' +
        '  ' + indentHtml(depth) + '\n' +
        '  <div class="ol-line-inner">\n' +
        '  <div class="' + causeCls + '" data-cause="' + escapeHtml(c.id) +
          '" title="选中此因果链 · 因侧（点因≠事件） · 双击重命名">\n' +
        '    <div class="ol-ttl">\n' + '      ' + collapseBtn + '\n' +
        '      <span class="tag">因</span>\n' +
        '      <span class="ol-title" data-rename-cause="' + escapeHtml(c.id) + '">' +
          escapeHtml(c.title || (depth > 0 ? '因-L' + depth : '因')) + '</span>\n' +
        '      <span class="ol-meta">L' + depth +
          (isChain && !ui.active_event_id && (ui.side || 'cause') === 'cause' ? ' · 选中' : '') +
          '</span>\n' + '      ' + childCountBadge + '\n' +
        '    </div>\n' + '    <div class="ol-sub">\n' +
        '      <span class="ol-chip">' + escapeHtml(c.state || 'open') + '</span>\n' +
        '      <span class="ol-chip">K ' + (Array.isArray(c.kbars) ? c.kbars.length : 0) + '</span>\n' +
        '      <span class="ol-chip">线 ' + (Array.isArray(c.overlays) ? c.overlays.length : 0) + '</span>\n' +
        '    </div>\n' + '  </div>\n' +
        '  <button type="button" class="ol-del" data-del-cause="' + escapeHtml(c.id) +
          '" title="删除此因果链（含子链与事件）">×</button>\n' +
        '  </div>\n' + '</div>';

      var order = Array.isArray(c.children_order) ? c.children_order.slice() : [];
      var items = order.filter(function (x) {
        return x && x.id && (x.type === 'event' || x.type === 'chain');
      });
      if (!items.length) {
        events
          .filter(function (ev) { return ev.cause_id === c.id && ev.id; })
          .sort(function (a, b) {
            return String(a.created_at || '').localeCompare(String(b.created_at || ''));
          })
          .forEach(function (ev) { items.push({ type: 'event', id: ev.id }); });
        causes
          .filter(function (ch) { return ch.parent_id === c.id && ch.id; })
          .forEach(function (ch) { items.push({ type: 'chain', id: ch.id }); });
      }
      items.forEach(function (item) {
        if (item.type === 'event') {
          var ev = eventMap[item.id];
          if (!ev) return;
          var act = ev.id === ui.active_event_id ? ' active' : '';
          var t = (ev.created_at || '').slice(11, 16) || '';
          html += '<div class="ol-line" data-depth="' + (depth + 1) + '" data-collapse-body="1">\n' +
            '  ' + indentHtml(depth + 1) + '\n' +
            '  <div class="ol-line-inner">\n' +
            '  <div class="ol-node ol-event' + act + '" data-event="' + escapeHtml(ev.id) +
              '" title="属于链 L' + depth + ' · 选中后：画线/选K/备注均归入此事件 · 双击重命名">\n' +
            '    <div class="ol-ttl">\n' + '      <span class="tag">事件</span>\n' +
            '      <span class="ol-title" data-rename-event="' + escapeHtml(ev.id) + '">' +
              escapeHtml(ev.title || ('事件' + (t ? '·' + t : ''))) + '</span>\n' +
            '      <span class="ol-meta">∈L' + depth + (t ? ' · ' + escapeHtml(t) : '') + '</span>\n' +
            '    </div>\n' + '    <div class="ol-sub">\n' +
            '      <span class="ol-chip">元素 ' +
              (Array.isArray(ev.elements) && ev.elements.length
                ? ev.elements.length
                : (Array.isArray(ev.kbars) ? ev.kbars.length : 0) +
                  (Array.isArray(ev.overlays) ? ev.overlays.length : 0) +
                  (Array.isArray(ev.notes) ? ev.notes.length : 0)) + '</span>\n' +
            '    </div>\n' + '  </div>\n' +
            '  <button type="button" class="ol-del" data-del-event="' + escapeHtml(ev.id) +
              '" title="删除此事件">×</button>\n' +
            '  </div>\n' + '</div>';
        } else if (item.type === 'chain') {
          if (!causeMap[item.id]) return;
          html += '<div data-collapse-body="1">' + renderChain(item.id, depth + 1) + '</div>';
        }
      });

      var phaseLabel = phase === 'collecting' ? '采集中' : phase === 'closed' ? '已闭合' : '待验证';
      html += '<div class="ol-line" data-depth="' + depth + '">\n' +
        '  ' + indentHtml(depth) + '\n' +
        '  <div class="' + effectCls + '" data-effect="' + escapeHtml(ef ? ef.id : '') +
          '" data-cause-for-effect="' + escapeHtml(c.id) +
          '" title="点果：进果侧 / 再点闭合（点果≠事件）">\n' +
        '    <div class="ol-ttl">\n' + '      <span class="tag">果</span>\n' +
        '      <span class="ol-meta">' + escapeHtml(phaseLabel) + '</span>\n' +
        '    </div>\n' + '    <div class="ol-sub">\n' +
        '      <span class="ol-chip">K ' + (ef && Array.isArray(ef.kbars) ? ef.kbars.length : 0) + '</span>\n' +
        '      <span class="ol-chip">线 ' + (ef && Array.isArray(ef.overlays) ? ef.overlays.length : 0) + '</span>\n' +
        '    </div>\n' + '  </div>\n' + '</div>';
      return html + '</div>';
    }

    var rootItems = Array.isArray(snapshot.root_order) ? snapshot.root_order.slice() : [];
    if (!rootItems.length) {
      causes
        .filter(function (c) { return !c.parent_id; })
        .forEach(function (c) { rootItems.push({ type: 'chain', id: c.id }); });
    }
    var treeParts = [];
    rootItems.forEach(function (item) {
      if (item && item.type === 'chain' && item.id) treeParts.push(renderChain(item.id, 0));
    });
    var treeHtml = treeParts.length
      ? '<div class="chain-outline">' + treeParts.join('') + '</div>'
      : '<div class="hint-box">\n' +
        '        空白大纲 — 不预置任何节点。<br/>\n' +
        '        <b>因</b>：无选中时建根链；选中某链后再点「因」= 其下子链（缩进）<br/>\n' +
        '        <b>事件</b>：仅主动添加，归属当前选中的因果链<br/>\n' +
        '        <b>果</b>：第1次进果侧，第2次闭合 · 点因/果都不是事件<br/>\n' +
        '        点标题「会话分析」可取消选中，再点「因」可另建根链\n' +
        '      </div>';

    var liveEv = events.find(function (e) { return e.id === ui.active_event_id; });
    var liveSide = ui.side || 'cause';
    var liveNode = liveSide === 'effect' ? activeE : activeC;
    var liveLabel;
    var liveTargetCls = 'cause';
    if (liveEv) {
      liveLabel = '事件 ∈L' +
        (activeC && activeC.depth != null ? activeC.depth : '?') + ' · ' +
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
      return arr.map(function (k, i) {
        var ohlc = k.open != null
          ? ' O' + escapeHtml(k.open) + ' H' + escapeHtml(k.high) +
            ' L' + escapeHtml(k.low) + ' C' + escapeHtml(k.close)
          : '';
        return '<div class="list-line">#' + (i + 1) + ' ' +
          escapeHtml(k.date || k.timestamp) + ' ' + escapeHtml(k.symbol || '') + ' ' +
          escapeHtml(k.period || '') + ' ' + escapeHtml(k.price_element || '') + '@' +
          escapeHtml(k.price != null ? k.price : '') + ohlc + ' 量' +
          escapeHtml(formatVolume(k.volume)) +
          (k.amount != null ? ' 额' + escapeHtml(formatVolume(k.amount)) : '') +
          '</div>';
      }).join('');
    }

    function linesOvs(arr) {
      if (!arr || !arr.length) return '<div class="empty">无画线</div>';
      return arr.map(function (o, i) {
        var pts = (o.points || []).slice(0, 2).map(function (p) { return p.value; }).join(',');
        return '<div class="list-line">#' + (i + 1) + ' ' + escapeHtml(o.type) +
          ' ' + escapeHtml(pts) + '</div>';
      }).join('');
    }

    function linesNotes(arr) {
      if (!arr || !arr.length) return '<div class="empty">无备注</div>';
      return arr.map(function (n) {
        var t = typeof n === 'string' ? n : n.text || JSON.stringify(n);
        return '<div class="list-line">' + escapeHtml(t) + '</div>';
      }).join('');
    }

    var depthNow = activeC ? (activeC.depth != null ? activeC.depth : 0) : 0;
    var activeElId = ui.active_element_id;
    var elementsHtml = '';
    if (liveEv) {
      var els = Array.isArray(liveEv.elements) ? liveEv.elements : [];
      if (!els.length) {
        elementsHtml = '<div class="empty">暂无元素 · 选K / 画线 / 写备注 将各自新增并列元素</div>';
      } else {
        elementsHtml = '<div class="el-list">' + els.map(function (el, idx) {
          var kind = el.kind || 'kbar';
          var d = el.data || {};
          var act = el.id === activeElId ? ' active' : '';
          var title = '';
          var sub = '';
          if (kind === 'kbar') {
            title = (d.date || d.timestamp || 'K') + ' ' + (d.price_element || '') + '@' +
              (d.price != null ? d.price : '');
            sub = '量' + formatVolume(d.volume) +
              (d.open != null ? ' · O' + d.open + ' H' + d.high + ' L' + d.low + ' C' + d.close : '');
          } else if (kind === 'overlay') {
            var pts = (d.points || []).slice(0, 2).map(function (p) {
              return p && p.value != null ? p.value : '';
            }).join(',');
            title = (d.type || '画线') + (pts ? ' · ' + pts : '');
            sub = 'id ' + String(d.id || '').slice(-8);
          } else if (kind === 'note') {
            title = d.text || '';
            sub = (d.at || el.created_at || '').toString().slice(0, 19);
          }
          var kindLabel = kind === 'kbar' ? 'K线' : kind === 'overlay' ? '画线' : '备注';
          return '<div class="el-item' + act + '" data-element="' + escapeHtml(el.id) +
            '" data-event-for-el="' + escapeHtml(liveEv.id) + '">\n' +
            '  <div class="el-body">\n' +
            '    <span class="el-kind ' + escapeHtml(kind) + '">#' + (idx + 1) + ' ' +
              kindLabel + '</span>\n' +
            '    <div class="el-title">' + escapeHtml(title) + '</div>\n' +
            '    <div class="el-sub">' + escapeHtml(sub) + '</div>\n' +
            '  </div>\n' +
            '  <button type="button" class="el-del" data-del-element="' + escapeHtml(el.id) +
              '" data-event-for-el="' + escapeHtml(liveEv.id) + '" title="删除此元素">×</button>\n' +
            '</div>';
        }).join('') + '</div>';
      }
    } else {
      var liveK = (liveNode && liveNode.kbars) || [];
      var liveOv = (liveNode && liveNode.overlays) || [];
      var liveNotes = (liveNode && liveNode.notes) || [];
      elementsHtml = '<div class="muted" style="margin-bottom:6px">未选事件 · 显示因/果汇总（点「事件」后可建并列元素）</div>' +
        '<div class="card"><div style="color:#f39c12;margin-bottom:4px">选K ' + liveK.length +
        '</div>' + linesKbars(liveK) + '</div>' +
        '<div class="card"><div style="color:#f39c12;margin-bottom:4px">画线 ' + liveOv.length +
        '</div>' + linesOvs(liveOv) + '</div>' +
        '<div class="card"><div style="color:#f39c12;margin-bottom:4px">备注 ' + liveNotes.length +
        '</div>' + linesNotes(liveNotes) + '</div>';
    }

    return '<div class="sec">\n' +
      '  <h4>图表</h4>\n' +
      '  <div class="card">\n' +
      '    <div class="kv"><span>标的</span><span>' + escapeHtml(ctx.symbol_name || '') + ' ' +
        escapeHtml(ctx.symbol) + '</span></div>\n' +
      '    <div class="kv"><span>周期</span><span>' + escapeHtml(ctx.period) + '</span></div>\n' +
      '    <div class="kv"><span>图上画线</span><span>' + chartOvs.length + '</span></div>\n' +
      '    <div class="kv"><span>当前链深度</span><span>L' + depthNow + '</span></div>\n' +
      '  </div>\n' + '</div>\n' +
      '<div class="sec">\n' + '  <h4>大纲 · 列式层级</h4>\n' +
      '  ' + treeHtml + '\n' + '</div>\n' +
      '<div class="sec">\n' + '  <h4>元素 <span class="live-target ' + liveTargetCls + '">' +
        escapeHtml(liveLabel) + '</span></h4>\n' +
      '  <div class="card active">\n' +
      '    <div class="kv"><span>当前链深度</span><span>L' + depthNow +
        (activeC ? '' : ' · 未选中') + '</span></div>\n' +
      '    <div class="kv"><span>链 / 事件</span><span>' +
        escapeHtml((ui.active_cause_id || '—').toString().slice(-8)) + ' / ' +
        escapeHtml((ui.active_event_id || '—').toString().slice(-8)) + '</span></div>\n' +
      '    <div class="kv"><span>工具</span><span>' + escapeHtml(ui.tool || 'browse') +
        (pickKActive ? ' ·选K' : '') + '</span></div>\n' +
      '    <div class="kv"><span>写入目标</span><span>' +
        (liveEv ? '事件内并列元素' : liveSide === 'effect' ? '果汇总' : '因汇总') +
        '</span></div>\n' + '  </div>\n' +
      '  <div class="card">\n' +
      '    <div style="color:#f39c12;margin-bottom:6px">元素列表 ' +
        (liveEv ? (liveEv.elements || []).length : '—') + ' · 点击高亮图表</div>\n' +
      '    ' + elementsHtml + '\n' + '  </div>\n' +
      '  <div class="card"><div style="color:#787b86;margin-bottom:4px">图上实时画线 ' +
        chartOvs.length + '</div>' + linesOvs(chartOvs) + '</div>\n' +
      '</div>';
  }

  return {
    renderSessionBody: renderSessionBody,
    escapeHtml: escapeHtml,
    formatVolume: formatVolume,
  };
}));
