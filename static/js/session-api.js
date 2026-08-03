/**
 * session-api.js — 会话面板 API 通信层
 *
 * 职责：
 * - 封装所有对后端 /api/sessions/* 的 HTTP 请求
 * - 统一错误处理（HTTP 状态码、业务错误码、revision 冲突）
 * - 不操作 DOM、不管理 UI 状态
 *
 * 从 session-ui.js 中抽取，保持行为不变。
 * session-ui.js 通过 window.SessionAPI.* 调用这些函数。
 */
(function (global) {
  'use strict';

  var API_BASE = global.API || '';

  /**
   * 底层请求函数。
   * 统一处理 JSON 解析和错误抛出。
   */
  async function api(path, opts) {
    var r = await fetch(API_BASE + path, Object.assign({
      headers: { 'Content-Type': 'application/json' },
    }, opts || {}));
    var j;
    try { j = await r.json(); } catch (e) { j = {}; }
    if (!r.ok || j.ok === false || j.success === false) {
      var err = new Error(j.error || 'HTTP ' + r.status);
      err.status = r.status;
      if (j.code) err.code = j.code;
      if (j.current_rev !== undefined) err.current_rev = j.current_rev;
      if (j.current_session) err.current_session = j.current_session;
      throw err;
    }
    return j;
  }

  /**
   * 获取活跃会话。
   * GET /api/sessions/active
   */
  async function getActiveSession() {
    return api('/api/sessions/active');
  }

  /**
   * 创建新会话。
   * POST /api/sessions
   */
  async function createSession(payload) {
    return api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  }

  /**
   * 更新会话（带 base_rev 乐观锁）。
   * PUT /api/sessions/:id
   */
  async function updateSession(sessionId, payload) {
    return api('/api/sessions/' + sessionId, {
      method: 'PUT',
      body: JSON.stringify(payload || {}),
    });
  }

  /**
   * 提交会话（定稿写入 Obsidian）。
   * POST /api/sessions/:id/commit
   */
  async function commitSession(sessionId, payload) {
    return api('/api/sessions/' + sessionId + '/commit', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  }

  /**
   * 克隆会话。
   * POST /api/sessions/:id/clone
   */
  async function cloneSession(sessionId) {
    return api('/api/sessions/' + sessionId + '/clone', { method: 'POST' });
  }

  /**
   * 激活会话（切换到指定会话）。
   * POST /api/sessions/:id/activate
   */
  async function activateSession(sessionId, payload) {
    return api('/api/sessions/' + sessionId + '/activate', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  }

  /**
   * 回放会话（获取会话图表信息）。
   * GET /api/sessions/:id/replay
   */
  async function replaySession(sessionId, chartId, eventId) {
    var url = '/api/sessions/' + sessionId + '/replay';
    var params = [];
    if (chartId) params.push('chart_id=' + encodeURIComponent(chartId));
    if (eventId) params.push('event_id=' + encodeURIComponent(eventId));
    if (params.length) url += '?' + params.join('&');
    return api(url);
  }

  /**
   * 执行会话动作（root_cause, click_effect, overlays 等）。
   * POST /api/sessions/:id/actions
   */
  async function postSessionAction(sessionId, body) {
    return api('/api/sessions/' + sessionId + '/actions', {
      method: 'POST',
      body: JSON.stringify(body || {}),
    });
  }

  /**
   * 列出会话。
   * GET /api/sessions?limit=40
   */
  async function listSessions(limit) {
    return api('/api/sessions?limit=' + (limit || 40));
  }

  /**
   * 获取标注列表。
   * GET /api/annotations?symbol=...&period=...&type=...&limit=...
   */
  async function getAnnotations(symbol, period, type, limit) {
    var url = '/api/annotations?symbol=' + encodeURIComponent(symbol) +
      '&period=' + encodeURIComponent(period) +
      '&type=' + encodeURIComponent(type) +
      '&limit=' + (limit || 100);
    return api(url);
  }

  /**
   * 获取候选支撑位提议。
   * GET /api/levels/propose?symbol=...&period=...&top_n=...
   */
  async function getProposedLevels(symbol, period, topN) {
    var url = '/api/levels/propose?symbol=' + encodeURIComponent(symbol) +
      '&period=' + encodeURIComponent(period) +
      '&top_n=' + (topN || 5);
    return api(url);
  }

  /**
   * 扫描共振矩阵。
   * POST /api/resonance/matrix
   */
  async function scanResonance(members, periods) {
    return api('/api/resonance/matrix', {
      method: 'POST',
      body: JSON.stringify({ members: members, periods: periods }),
    });
  }

  // 导出
  global.SessionAPI = {
    api: api,
    getActiveSession: getActiveSession,
    createSession: createSession,
    updateSession: updateSession,
    commitSession: commitSession,
    cloneSession: cloneSession,
    activateSession: activateSession,
    replaySession: replaySession,
    postSessionAction: postSessionAction,
    listSessions: listSessions,
    getAnnotations: getAnnotations,
    getProposedLevels: getProposedLevels,
    scanResonance: scanResonance,
  };

})(window);
