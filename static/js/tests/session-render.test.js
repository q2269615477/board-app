const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const render = require('../session-render.js');

function sampleSession(overrides = {}) {
  return {
    status: 'active',
    title: '复盘',
    ui: {
      side: 'cause',
      tool: 'browse',
      active_cause_id: 'root',
      active_event_id: 'event-1',
      active_element_id: 'el-1',
      collapsed_chains: {},
    },
    root_order: [{ type: 'chain', id: 'root' }],
    causes: [
      {
        id: 'root',
        title: '根链',
        state: 'open',
        depth: 0,
        children_order: [
          { type: 'event', id: 'event-1' },
          { type: 'chain', id: 'child' },
        ],
        kbars: [],
        overlays: [],
      },
      {
        id: 'child',
        parent_id: 'root',
        title: '子链',
        state: 'closed',
        depth: 1,
        children_order: [],
        kbars: [],
        overlays: [],
      },
    ],
    effects: [
      { id: 'effect-root', cause_id: 'root', phase: 'collecting', kbars: [], overlays: [] },
      { id: 'effect-child', cause_id: 'child', phase: 'closed', kbars: [], overlays: [] },
    ],
    events: [
      {
        id: 'event-1',
        cause_id: 'root',
        title: '事件一',
        created_at: '2026-08-04T09:31:00',
        elements: [
          { id: 'el-1', kind: 'note', data: { text: '备注' } },
        ],
      },
    ],
    ...overrides,
  };
}

describe('SessionRender pure body projection', () => {
  test('renders nested chains and children_order in declared order', () => {
    const html = render.renderSessionBody(
      sampleSession(),
      { symbol: '603259', symbol_name: '药明康德', period: 'daily' },
      [{ id: 'overlay-1', type: 'segment', points: [] }],
      { pickKActive: false }
    );

    assert.match(html, /class="chain-outline"/);
    assert.ok(html.indexOf('data-event="event-1"') < html.indexOf('data-chain="child"'));
    assert.match(html, /data-cause="root"/);
    assert.match(html, /data-cause-for-effect="child"/);
    assert.match(html, /图上画线<\/span><span>1/);
  });

  test('renders event elements with stable event and element targets', () => {
    const html = render.renderSessionBody(
      sampleSession(),
      { symbol: '603259', symbol_name: '药明康德', period: 'daily' },
      [],
      { pickKActive: false }
    );

    assert.match(html, /class="el-list"/);
    assert.match(html, /class="el-item active" data-element="el-1" data-event-for-el="event-1"/);
    assert.match(html, /data-del-element="el-1" data-event-for-el="event-1"/);
    assert.match(html, /备注/);
  });

  test('renders collapsed chain state and child count without mutating input', () => {
    const session = sampleSession();
    session.ui.collapsed_chains = { root: true };
    const before = JSON.stringify(session);
    const html = render.renderSessionBody(session, {}, [], {});

    assert.match(html, /class="ol-chain-wrap focused collapsed"/);
    assert.match(html, /data-toggle-collapse="root" title="展开子项">▶<\/span>/);
    assert.match(html, /class="ol-collapse-count" title="已折叠 2 个子项">\+2<\/span>/);
    assert.equal(JSON.stringify(session), before);
  });

  test('projects the explicitly active effect without inferring it from the cause', () => {
    const session = sampleSession();
    session.ui = {
      side: 'effect',
      active_cause_id: 'root',
      active_effect_id: 'effect-child',
      collapsed_chains: {},
    };
    const html = render.renderSessionBody(session, {}, [], {});

    assert.match(html, /class="live-target effect">果侧汇总 · closed<\/span>/);
  });

  test('escapes user text and supports the empty state', () => {
    const html = render.renderSessionBody(
      sampleSession({
        title: '<unsafe>',
        causes: [{
          id: 'root',
          title: '<root&"">',
          state: 'open',
          children_order: [],
          kbars: [],
          overlays: [],
        }],
        events: [],
        effects: [],
        ui: { side: 'cause', collapsed_chains: {} },
      }),
      { symbol: '<symbol>', symbol_name: '<name>', period: 'daily' },
      [],
      {}
    );

    assert.match(html, /&lt;root&amp;&quot;&quot;>/);
    assert.match(html, /&lt;name> &lt;symbol>/);
    assert.doesNotMatch(html, /<root&/);
    assert.equal(render.renderSessionBody(null, {}, [], {}), '<div class="empty">无会话</div>');
  });
});
