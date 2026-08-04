// Behavioral tests for the force-refresh area status handling in sse-client.js.
//
// Loads the real browser script in a Node vm with minimal DOM/fetch/timer mocks
// (same pattern as bar-replay.test.js / replay-trade-*.test.js). These tests
// exercise the production `_forceAreaIssues` / `_showForceAreaIssues` /
// `pollUpdateTask` code paths directly; they are not string-contract tests.
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SSE_CLIENT = path.join(__dirname, '..', 'sse-client.js');

function flushMicrotasks() {
  return new Promise((resolve) => setImmediate(resolve));
}

function makeFakeTimers() {
  const intervals = new Map();
  const timeouts = new Map();
  let seq = 0;
  return {
    setInterval(fn) {
      const id = ++seq;
      intervals.set(id, fn);
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
    },
    setTimeout(fn) {
      const id = ++seq;
      timeouts.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      timeouts.delete(id);
    },
    intervalIds() {
      return Array.from(intervals.keys());
    },
    // Fires one interval tick and waits for the resulting promise chain.
    async tickInterval(id) {
      const fn = intervals.get(id);
      if (!fn) return false;
      fn();
      await flushMicrotasks();
      return true;
    },
  };
}

function loadSseClient(options = {}) {
  const calls = { fetch: [], toasts: [], toastBars: [] };
  const timers = makeFakeTimers();
  const fetchMock = options.fetch || (() => {
    calls.fetch.push(String(''));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, task: { status: 'success', detail: {} } }) });
  });

  const windowMock = {
    addEventListener() {},
    dispatchEvent() {},
    boardPollingLeader: undefined,
    apiFetch: undefined,
  };
  const documentMock = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => ({
      style: {},
      dataset: {},
      classList: { add() {}, remove() {} },
      setAttribute() {},
      appendChild() {},
      append() {},
      replaceChildren() {},
      addEventListener() {},
    }),
    body: { appendChild() {} },
  };

  const sandbox = {
    EventTarget: globalThis.EventTarget,
    console,
    window: windowMock,
    document: documentMock,
    fetch: (url) => {
      calls.fetch.push(String(url));
      return fetchMock(String(url));
    },
    API: 'http://test.local',
    setInterval: timers.setInterval,
    clearInterval: timers.clearInterval,
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    EventSource: function () { return { addEventListener() {}, close() {}, readyState: 0 }; },
    showToastBar: (html) => { calls.toastBars.push(String(html)); },
    toast: (msg) => { calls.toasts.push(String(msg)); },
    escHtml: (s) => String(s == null ? '' : s),
    escAttr: (s) => String(s == null ? '' : s),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    ...(options.globals || {}),
  };

  const context = vm.createContext(sandbox);
  const source = fs.readFileSync(SSE_CLIENT, 'utf8');
  vm.runInContext(source, context, { filename: 'sse-client.js' });

  const run = (code) => vm.runInContext(code, context);
  return { run, calls, timers };
}

// Build an array literal of area objects inside the vm context.
function areaList(run, areas) {
  return run('[' + areas.map((a) => JSON.stringify(a)).join(',') + ']');
}

describe('sse-client force-area issue reporting', () => {
  test('deferred areas are NOT classified as force-area issues', () => {
    const { run } = loadSseClient();
    const areas = areaList(run, [
      { label: '日线后台补齐', status: 'deferred', message: '按计划稍后执行' },
      { label: '顶部导航栏指数', status: 'fresh' },
    ]);
    const labels = run('_forceAreaIssues(' + JSON.stringify(areas) + ').map(function(a){return a.label})');
    assert.deepEqual(Array.from(labels), []);
  });

  test('failed/unavailable (and pending/canceled) areas are still reported', () => {
    const { run } = loadSseClient();
    const areas = areaList(run, [
      { label: 'failed-area', status: 'failed', message: 'boom' },
      { label: 'unavailable-area', status: 'unavailable' },
      { label: 'pending-area', status: 'pending' },
      { label: 'canceled-area', status: 'canceled' },
      { label: 'fresh-area', status: 'fresh' },
      { label: 'deferred-area', status: 'deferred' },
    ]);
    const labels = run('_forceAreaIssues(' + JSON.stringify(areas) + ').map(function(a){return a.label})');
    assert.deepEqual(Array.from(labels), [
      'failed-area',
      'unavailable-area',
      'pending-area',
      'canceled-area',
    ]);
  });

  test('_showForceAreaIssues reports failed areas but never deferred ones', () => {
    const { run, calls } = loadSseClient();
    const areas = areaList(run, [
      { label: 'deferred-area', status: 'deferred', message: '按时段等待' },
      { label: 'failed-area', status: 'failed', message: 'boom' },
    ]);
    const shown = run('_showForceAreaIssues("以下数据未及时更新：", ' + JSON.stringify(areas) + ')');
    assert.equal(shown, true);
    assert.equal(calls.toastBars.length, 1);
    assert.match(calls.toastBars[0], /failed-area/);
    assert.doesNotMatch(calls.toastBars[0], /deferred-area/);
    assert.match(calls.toastBars[0], /以下数据未及时更新：/);
  });

  test('_showForceAreaIssues is silent when only deferred/fresh areas exist', () => {
    const { run, calls } = loadSseClient();
    const areas = areaList(run, [
      { label: 'deferred-area', status: 'deferred', message: '按时段等待' },
      { label: 'fresh-area', status: 'fresh' },
    ]);
    const shown = run('_showForceAreaIssues("以下数据未及时更新：", ' + JSON.stringify(areas) + ')');
    assert.equal(shown, false);
    assert.equal(calls.toastBars.length, 0);
  });
});

describe('sse-client pollUpdateTask behavior', () => {
  test('3 consecutive status-API failures notify once and stop polling', async () => {
    const { run, calls, timers } = loadSseClient({
      fetch: () => Promise.reject(new Error('network down')),
    });

    const before = timers.intervalIds();
    run('pollUpdateTask("t-fail-1")');
    const pollIds = timers.intervalIds().filter((id) => before.indexOf(id) < 0);
    assert.equal(pollIds.length, 1, 'pollUpdateTask must start exactly one poll interval');
    const pollId = pollIds[0];

    // Three failed ticks: each failure is counted; the third must notify + stop.
    for (let i = 0; i < 3; i++) {
      assert.equal(await timers.tickInterval(pollId), true, `tick ${i + 1} must run`);
    }

    assert.equal(calls.fetch.length, 3, 'exactly 3 status fetches before stopping');
    assert.equal(timers.intervalIds().length, before.length, 'poll interval must be cleared after 3 failures');
    assert.equal(calls.toastBars.length, 1, 'notified exactly once');
    assert.match(calls.toastBars[0], /以下数据未及时更新：/);
    assert.match(calls.toastBars[0], /日线后台补齐/);

    // A further tick must be a no-op: no more fetches, no more notifications.
    assert.equal(await timers.tickInterval(pollId), false, 'cleared interval must not fire');
    assert.equal(calls.fetch.length, 3, 'polling truly stopped');
    assert.equal(calls.toastBars.length, 1, 'no duplicate notification');
  });
});
