"""tests/test_session_api_js.py — session-api.js 模块测试

验证从 session-ui.js 抽取的 API 通信层：
  - api() 函数正确封装 fetch + JSON 解析 + 错误处理
  - 各业务函数（getActiveSession, createSession 等）构造正确的请求
  - 错误时抛出带 code/status 的 Error
  - session-ui.js 中的 api() 委托给 SessionAPI.api
"""
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"


def _node_eval(js_code: str) -> str:
    result = subprocess.run(
        ["node", "-e", js_code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"Node error:\n{result.stderr}")
    return result.stdout.strip()


def _run_js_with_mock(js_files: list, test_code: str) -> str:
    """Load JS files with a mocked window/fetch, then run test_code."""
    mock_setup = """
var _fetchCalls = [];
global.window = {
  __board_ctx: null,
};
global.document = { readyState: 'complete', addEventListener: function(){}, dispatchEvent: function(){} };
global.fetch = function(url, opts) {
  _fetchCalls.push({ url: url, method: opts && opts.method || 'GET', body: opts && opts.body });
  var urlStr = String(url);
  // Order matters: specific paths before generic /api/sessions
  if (urlStr.indexOf('/api/sessions/active') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's1', status: 'drafting', title: 'Test' } }); }
    });
  }
  if (urlStr.indexOf('/commit') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's1', status: 'committed' } }); }
    });
  }
  if (urlStr.indexOf('/clone') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's3', status: 'drafting' } }); }
    });
  }
  if (urlStr.indexOf('/activate') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's1', status: 'drafting' } }); }
    });
  }
  if (urlStr.indexOf('/replay') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { chart: { symbol: '600519', period: 'daily' } } }); }
    });
  }
  if (urlStr.indexOf('/actions') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's1', status: 'drafting', causes: [] } }); }
    });
  }
  if (urlStr.indexOf('/api/annotations') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: [] }); }
    });
  }
  if (urlStr.indexOf('/api/levels/propose') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { candidates: [] } }); }
    });
  }
  if (urlStr.indexOf('/api/resonance/matrix') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { is_resonance: false, cells: [] } }); }
    });
  }
  // Generic POST /api/sessions (create new session)
  if (urlStr.indexOf('/api/sessions') >= 0 && (opts && opts.method === 'POST')) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's2', status: 'drafting', title: 'New' } }); }
    });
  }
  // Generic PUT /api/sessions/:id (update session)
  if (urlStr.indexOf('/api/sessions') >= 0 && (opts && opts.method === 'PUT')) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: { id: 's1', status: 'drafting', rev: 2 } }); }
    });
  }
  // Generic GET /api/sessions (list sessions)
  if (urlStr.indexOf('/api/sessions') >= 0) {
    return Promise.resolve({
      ok: true,
      json: function() { return Promise.resolve({ ok: true, data: [] }); }
    });
  }
  // Default
  return Promise.resolve({
    ok: true,
    json: function() { return Promise.resolve({ ok: true, data: {} }); }
  });
};
global.console = { log: function(){}, warn: function(){}, error: function(){}, dir: function(){} };
"""
    file_loads = ""
    for f in sorted(js_files):
        code = (JS_DIR / f).read_text(encoding="utf-8")
        file_loads += "\n// === " + f + " ===\n" + code + "\n"

    full = mock_setup + file_loads + "\n" + test_code + "\n"
    return _node_eval(full)


# ---------------------------------------------------------------------------
# session-api.js module tests
# ---------------------------------------------------------------------------

def test_session_api_exports_all_functions():
    """SessionAPI 必须导出所有通信函数。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var names = Object.keys(window.SessionAPI).sort().join(',');
process.stdout.write(names);
"""
    )
    expected = [
        "activateSession", "api", "cloneSession", "commitSession",
        "createSession", "getActiveSession", "getAnnotations",
        "getProposedLevels", "listSessions", "postSessionAction",
        "replaySession", "scanResonance", "updateSession",
    ]
    actual = out.split(",")
    for name in expected:
        assert name in actual, f"SessionAPI 缺少导出: {name}"


def test_api_function_success():
    """api() 成功时返回 JSON。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.api('/api/sessions/active').then(function(j) {
  process.stdout.write(JSON.stringify(j));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["data"]["id"] == "s1"


def test_api_function_error_throws():
    """api() 在 HTTP 错误时抛出带 status 的 Error。"""
    mock_setup = """
global.window = {};
global.fetch = function(url, opts) {
  return Promise.resolve({
    ok: false,
    status: 500,
    json: function() { return Promise.resolve({ error: 'Internal error' }); }
  });
};
global.console = { log: function(){}, warn: function(){}, error: function(){} };
"""
    code = (JS_DIR / "session-api.js").read_text(encoding="utf-8")
    test_code = """
window.SessionAPI.api('/test').then(function() {
  process.stdout.write('NO_ERROR');
}).catch(function(e) {
  process.stdout.write(JSON.stringify({ message: e.message, status: e.status }));
});
"""
    out = _node_eval(mock_setup + code + "\n" + test_code + "\n")
    data = json.loads(out)
    assert data["message"] == "Internal error"
    assert data["status"] == 500


def test_api_function_business_error_throws():
    """api() 在 j.ok === false 时抛出带 code 的 Error。"""
    mock_setup = """
global.window = {};
global.fetch = function(url, opts) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function() { return Promise.resolve({ ok: false, error: 'Conflict', code: 'REVISION_CONFLICT', current_rev: 3 }); }
  });
};
global.console = { log: function(){}, warn: function(){}, error: function(){} };
"""
    code = (JS_DIR / "session-api.js").read_text(encoding="utf-8")
    test_code = """
window.SessionAPI.api('/test').then(function() {
  process.stdout.write('NO_ERROR');
}).catch(function(e) {
  process.stdout.write(JSON.stringify({ message: e.message, code: e.code, current_rev: e.current_rev }));
});
"""
    out = _node_eval(mock_setup + code + "\n" + test_code + "\n")
    data = json.loads(out)
    assert data["message"] == "Conflict"
    assert data["code"] == "REVISION_CONFLICT"
    assert data["current_rev"] == 3


def test_get_active_session_calls_correct_url():
    """getActiveSession 调用 GET /api/sessions/active。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.getActiveSession().then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok, id: j.data.id }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["id"] == "s1"


def test_create_session_posts_correct_body():
    """createSession 发送 POST 带 body。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.createSession({ title: 'Test', save_payload: { foo: 1 } }).then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok, id: j.data.id }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True


def test_update_session_puts_with_id():
    """updateSession 发送 PUT 到 /api/sessions/:id。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.updateSession('s1', { status: 'drafting', base_rev: 1 }).then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok, rev: j.data.rev }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["rev"] == 2


def test_commit_session_posts():
    """commitSession 发送 POST 到 /api/sessions/:id/commit。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.commitSession('s1', { session: { id: 's1' } }).then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok, status: j.data.status }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["status"] == "committed"


def test_clone_session_posts():
    """cloneSession 发送 POST 到 /api/sessions/:id/clone。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.cloneSession('s1').then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok, id: j.data.id }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["id"] == "s3"


def test_list_sessions_calls_correct_url():
    """listSessions 调用 GET /api/sessions?limit=40。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
window.SessionAPI.listSessions(40).then(function(j) {
  process.stdout.write(JSON.stringify({ ok: j.ok }));
});
"""
    )
    data = json.loads(out)
    assert data["ok"] is True


def test_get_annotations_builds_query_string():
    """getAnnotations 构造正确的查询参数。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var _fetchUrl = '';
var _origFetch = global.fetch;
global.fetch = function(url, opts) {
  _fetchUrl = String(url);
  return _origFetch(url, opts);
};
window.SessionAPI.getAnnotations('600519', 'daily', 'level_origin', 100).then(function(j) {
  process.stdout.write(JSON.stringify({ url: _fetchUrl }));
});
"""
    )
    data = json.loads(out)
    assert "symbol=600519" in data["url"]
    assert "period=daily" in data["url"]
    assert "type=level_origin" in data["url"]
    assert "limit=100" in data["url"]


def test_get_proposed_levels_builds_query():
    """getProposedLevels 构造正确的查询参数。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var _fetchUrl = '';
var _origFetch = global.fetch;
global.fetch = function(url, opts) {
  _fetchUrl = String(url);
  return _origFetch(url, opts);
};
window.SessionAPI.getProposedLevels('600519', 'daily', 5).then(function(j) {
  process.stdout.write(JSON.stringify({ url: _fetchUrl }));
});
"""
    )
    data = json.loads(out)
    assert "symbol=600519" in data["url"]
    assert "period=daily" in data["url"]
    assert "top_n=5" in data["url"]


def test_scan_resonance_posts_body():
    """scanResonance 发送 POST 带成员和周期列表。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var _fetchBody = '';
var _origFetch = global.fetch;
global.fetch = function(url, opts) {
  if (opts && opts.body) _fetchBody = opts.body;
  return _origFetch(url, opts);
};
window.SessionAPI.scanResonance(
  [{ symbol: '600519', period: 'daily' }],
  ['daily']
).then(function(j) {
  var body = JSON.parse(_fetchBody);
  process.stdout.write(JSON.stringify({ hasMembers: !!body.members, hasPeriods: !!body.periods }));
});
"""
    )
    data = json.loads(out)
    assert data["hasMembers"] is True
    assert data["hasPeriods"] is True


def test_post_session_action_posts_body():
    """postSessionAction 发送 POST 带动作体。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var _fetchBody = '';
var _origFetch = global.fetch;
global.fetch = function(url, opts) {
  if (opts && opts.body) _fetchBody = opts.body;
  return _origFetch(url, opts);
};
window.SessionAPI.postSessionAction('s1', { action: 'root_cause', title: 'Test' }).then(function(j) {
  var body = JSON.parse(_fetchBody);
  process.stdout.write(JSON.stringify({ action: body.action }));
});
"""
    )
    data = json.loads(out)
    assert data["action"] == "root_cause"


def test_replay_session_builds_url():
    """replaySession 构造正确的 URL 含 chart_id 和 event_id。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var _fetchUrl = '';
var _origFetch = global.fetch;
global.fetch = function(url, opts) {
  _fetchUrl = String(url);
  return _origFetch(url, opts);
};
window.SessionAPI.replaySession('s1', 'ch1', 'ev1').then(function(j) {
  process.stdout.write(JSON.stringify({ url: _fetchUrl }));
});
"""
    )
    data = json.loads(out)
    assert "chart_id=ch1" in data["url"]
    assert "event_id=ev1" in data["url"]


# ---------------------------------------------------------------------------
# Integration: session-ui.js delegates to SessionAPI
# ---------------------------------------------------------------------------

def test_session_ui_delegates_api_to_session_api():
    """session-ui.js 的内部 api() 函数必须委托给 SessionAPI.api。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
// Read session-ui.js source and check delegation
var src = require('fs').readFileSync('static/js/session-ui.js', 'utf-8');
var hasDelegation = src.indexOf('global.SessionAPI.api') >= 0;
var noDirectFetch = src.indexOf('await fetch(API') < 0;
process.stdout.write(JSON.stringify({ hasDelegation: hasDelegation, noDirectFetch: noDirectFetch }));
"""
    )
    data = json.loads(out)
    assert data["hasDelegation"] is True, "session-ui.js 未委托给 SessionAPI.api"
    assert data["noDirectFetch"] is True, "session-ui.js 仍含直接 fetch 调用"


def test_session_ui_still_has_all_action_functions():
    """session-ui.js 必须保留所有交互函数。"""
    out = _run_js_with_mock(
        ["session-api.js"],
        """
var src = require('fs').readFileSync('static/js/session-ui.js', 'utf-8');
var fns = ['startDrawLevel', 'startReactionMode', 'openLevelManager', 'openResonancePanel', 'openProposalPanel', 'bindHotkeys', 'ensureSession', 'onSaveCommit', 'onNewSession', 'cloneCurrentSession', 'flushOverlaysLocal', 'openList', 'render'];
var missing = [];
for (var i = 0; i < fns.length; i++) {
  if (src.indexOf('function ' + fns[i]) < 0) missing.push(fns[i]);
}
process.stdout.write(JSON.stringify({ missing: missing }));
"""
    )
    data = json.loads(out)
    assert not data["missing"], f"session-ui.js 缺少函数: {data['missing']}"
