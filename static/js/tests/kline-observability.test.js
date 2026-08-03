const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadDatafeed(fetchImpl) {
  const events = [];
  const listeners = new Map();
  const fakeSetTimeout = () => 1;
  const fakeClearTimeout = () => {};
  const window = {
    store: { selected: null },
    __board_ctx: { code: '600519', name: '测试股', type: 'stock', period: 'daily' },
    addEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      bucket.push(listener);
      listeners.set(type, bucket);
    },
    dispatchEvent(event) {
      events.push(event);
      (listeners.get(event.type) || []).forEach((listener) => listener(event));
      return true;
    },
  };
  function CustomEvent(type, init) {
    this.type = type;
    this.detail = init && init.detail;
  }
  const context = vm.createContext({
    window,
    document: {
      hidden: false,
      createElement: () => ({}),
      head: { appendChild() {} },
      getElementById: () => null,
    },
    CustomEvent,
    API: '/api',
    store: window.store,
    pro: null,
    console,
    fetch: fetchImpl,
    AbortController,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    setInterval: fakeSetTimeout,
    clearInterval: fakeClearTimeout,
  });
  const source = fs.readFileSync(require.resolve('../chart-core.js'), 'utf8') +
    '\nthis.__BoardDatafeed = BoardDatafeed; this.__datafeed = _datafeed;';
  vm.runInContext(source, context, { filename: 'chart-core.js' });
  return { context, window, events, feed: context.__BoardDatafeed ? new context.__BoardDatafeed() : null };
}

function response(payload) {
  return { ok: true, json: async () => payload };
}

describe('chart datafeed observability contract', () => {
  test('copies backend metadata into events/cache and marks client cache hits', async () => {
    let fetchCount = 0;
    const env = loadDatafeed(async () => {
      fetchCount += 1;
      return response({
        data: [{ timestamp: 1700000000000, open: 1, high: 2, low: 0.5, close: 1.5 }],
        source: 'qmt_http',
        stale: true,
        background_refresh_started: true,
        load_ms: 12.5,
        fallback_chain: ['qmt_http', 'qmt_xtdata'],
        secret_backend_field: 'must-not-cross-boundary',
      });
    });
    const symbol = { ticker: '600519', name: '测试股', type: 'stock' };
    const period = { timespan: 'day', multiplier: 1 };

    const firstRows = await env.feed.getHistoryKLineData(symbol, period);
    assert.equal(firstRows.length, 1);
    const first = env.events.find((event) => event.type === 'kline-loaded');
    assert.ok(first);
    assert.equal(first.detail.source, 'qmt_http');
    assert.equal(first.detail.stale, true);
    assert.equal(first.detail.background_refresh_started, true);
    assert.equal(first.detail.load_ms, 12.5);
    assert.deepEqual(Array.from(first.detail.fallback_chain), ['qmt_http', 'qmt_xtdata']);
    assert.equal(first.detail.client_cache_hit, false);
    assert.equal(first.detail.secret_backend_field, undefined);
    assert.deepEqual(env.window.__lastKlineObservability.fallback_chain, first.detail.fallback_chain);

    const key = '600519:day:1:daily';
    const cached = env.feed._cache.get(key);
    assert.deepEqual(JSON.parse(JSON.stringify(cached.observability)), {
      source: 'qmt_http',
      stale: true,
      background_refresh_started: true,
      load_ms: 12.5,
      fallback_chain: ['qmt_http', 'qmt_xtdata'],
    });

    const secondRows = await env.feed.getHistoryKLineData(symbol, period);
    assert.equal(secondRows.length, 1);
    assert.equal(fetchCount, 1);
    const loaded = env.events.filter((event) => event.type === 'kline-loaded');
    const second = loaded[loaded.length - 1];
    assert.equal(second.detail.source, 'qmt_http');
    assert.equal(second.detail.stale, true);
    assert.equal(second.detail.client_cache_hit, true);
    second.detail.fallback_chain.push('mutated-in-test');
    assert.deepEqual(Array.from(cached.observability.fallback_chain), ['qmt_http', 'qmt_xtdata']);
  });

  test('loading responses publish the same typed metadata contract', async () => {
    const env = loadDatafeed(async () => response({
      loading: true,
      source: 'pending',
      stale: false,
      background_refresh_started: false,
      load_ms: 4,
      fallback_chain: ['pending'],
    }));
    const symbol = { ticker: '600519', name: '测试股', type: 'stock' };
    await env.feed.getHistoryKLineData(symbol, { timespan: 'day', multiplier: 1 });
    const event = env.events.find((item) => item.type === 'kline-loaded');
    assert.ok(event);
    assert.equal(event.detail.source, 'pending');
    assert.equal(typeof event.detail.source, 'string');
    assert.equal(typeof event.detail.stale, 'boolean');
    assert.equal(typeof event.detail.background_refresh_started, 'boolean');
    assert.equal(typeof event.detail.load_ms, 'number');
    assert.ok(Array.isArray(event.detail.fallback_chain));
    assert.equal(typeof event.detail.client_cache_hit, 'boolean');
    assert.equal(event.detail.client_cache_hit, false);
  });

  test('error events retain stable metadata and update the现场 snapshot', async () => {
    const env = loadDatafeed(async () => response({
      error: 'backend unavailable',
      source: 'error',
      stale: false,
      background_refresh_started: false,
      load_ms: 7,
      fallback_chain: ['qmt_http', 'qmt_xtdata', 'sqlite'],
    }));
    const symbol = { ticker: '600519', name: '测试股', type: 'stock' };
    await env.feed.getHistoryKLineData(symbol, { timespan: 'day', multiplier: 1 });
    const loaded = env.events.find((event) => event.type === 'kline-loaded');
    const failed = env.events.find((event) => event.type === 'kline-error');
    assert.ok(loaded && failed);
    for (const event of [loaded, failed]) {
      assert.equal(event.detail.source, 'error');
      assert.equal(event.detail.stale, false);
      assert.equal(event.detail.background_refresh_started, false);
      assert.equal(event.detail.load_ms, 7);
      assert.deepEqual(Array.from(event.detail.fallback_chain), ['qmt_http', 'qmt_xtdata', 'sqlite']);
      assert.equal(event.detail.client_cache_hit, false);
    }
    assert.equal(env.window.__lastKlineObservability.error, 'backend unavailable');
  });
});
