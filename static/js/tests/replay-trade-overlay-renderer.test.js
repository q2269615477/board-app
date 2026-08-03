const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const MODULE_FILE = path.resolve(__dirname, '..', 'replay-trade-overlay-renderer.js');
const renderer = require(MODULE_FILE);

function makeNode(tag, namespace) {
  return { tag, namespace, attrs: {}, children: [], textContent: '' };
}

function makeAdapter() {
  return {
    create(tag, namespace) { return makeNode(tag, namespace); },
    append(parent, child) { parent.children.push(child); return child; },
    attr(node, name, value) { node.attrs[name] = String(value); },
    text(node, value) { node.textContent = String(value == null ? '' : value); },
  };
}

function child(node, tag, className) {
  return node.children.find((item) => item.tag === tag &&
    (!className || String(item.attrs.class || '').split(/\s+/).includes(className)));
}

describe('ReplayTradeOverlayRenderer contract', () => {
  test('exports render-only helpers and attaches the browser global without a DOM', () => {
    for (const name of [
      'renderBracketLevel', 'renderExecutionMarker', 'renderRiskZone',
      'renderPresetOrder', 'renderPresetPreview', 'renderHistoryGhost',
    ]) assert.equal(typeof renderer[name], 'function', `${name} must be exported`);

    const code = fs.readFileSync(MODULE_FILE, 'utf8');
    const sandbox = {};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
    assert.equal(typeof sandbox.ReplayTradeOverlayRenderer.renderPresetOrder, 'function');
  });

  test('does not own chart, trade state, document or event lifecycles', () => {
    const source = fs.readFileSync(MODULE_FILE, 'utf8');
    for (const forbidden of [
      'convertToPixel', 'convertFromPixel', 'candle_pane',
      'ReplayTradeEngine', 'addEventListener', 'removeEventListener', 'document.',
    ]) assert.equal(source.includes(forbidden), false, `${forbidden} must stay outside renderer`);
  });
});

describe('passive overlay trees', () => {
  test('renders bracket levels with the historical child order and attributes', () => {
    const adapter = makeAdapter();
    const svg = makeNode('svg', true);
    const result = renderer.renderBracketLevel(adapter, svg, {
      colorClass: 'takeProfit', role: 'takeProfit', width: 800, y: 120,
      labelX: 500, orderText: '1,2', labelText: '止盈 1,2 · +5.00% · +¥500.00',
    });
    assert.equal(svg.children[0], result.group);
    assert.equal(result.group.attrs.class, 'replay-trade-bracket-draft replay-trade-bracket-takeProfit');
    assert.equal(result.group.attrs['data-bracket-role'], 'takeProfit');
    assert.deepEqual(result.group.children.map((item) => item.tag), ['line', 'line', 'text']);
    assert.equal(result.group.children[0].attrs['stroke-width'], '16');
    assert.equal(result.label.attrs['data-bracket-orders'], '1,2');
    assert.equal(result.label.textContent, '止盈 1,2 · +5.00% · +¥500.00');
  });

  test('renders execution levels, HTML entry labels, arrows and marker labels', () => {
    const adapter = makeAdapter();
    const svg = makeNode('svg', true);
    const result = renderer.renderExecutionMarker(adapter, svg, {
      side: 'buy', label: 'B1', title: '买入 B1', width: 640, y: 80, markerX: 90,
      pairColor: '#ef4444', arrowPoints: ['90,80', '85,87'],
      entryLabel: { x: 400, y: 69, width: 200, color: '#ef4444', text: 'B1买入 3,800.00 · 盈亏比 2:1' },
    });
    const group = result.group;
    assert.equal(group.attrs['data-trade-side'], 'buy');
    assert.equal(group.attrs['aria-label'], '买入 B1');
    const level = child(group, 'line', 'replay-trade-execution-level');
    assert.equal(level.attrs.stroke, '#ef4444');
    assert.equal(level.attrs['data-trade-label'], 'B1');
    const host = child(group, 'foreignObject');
    assert.equal(host.children[0].namespace, false);
    assert.equal(host.children[0].attrs['data-order-entry-label'], 'B1');
    assert.equal(host.children[0].textContent, 'B1买入 3,800.00 · 盈亏比 2:1');
    assert.equal(child(group, 'polygon').attrs.points, '90,80 85,87');
    assert.equal(child(group, 'text', 'replay-trade-marker-label').textContent, 'B1');
  });

  test('renders risk zones, preset previews and history ghosts with stable anchors', () => {
    const adapter = makeAdapter();
    const svg = makeNode('svg', true);
    const zone = renderer.renderRiskZone(adapter, svg, {
      side: 'stopLoss', orderNumber: 2, x: 100, y: 200, width: 500, height: 40,
    });
    assert.equal(zone.attrs.class, 'replay-trade-risk-zone replay-trade-risk-zone-stopLoss');
    assert.equal(zone.attrs['data-order-number'], '2');
    assert.equal(zone.attrs.width, '500');

    const preview = renderer.renderPresetPreview(adapter, svg, {
      side: 'sell', width: 600, y: 150, labelX: 590,
      ariaLabel: '预设卖出水平价格 3,900.00', labelText: '卖出 3,900.00',
    });
    assert.equal(preview.line.attrs['data-trade-side'], 'sell');
    assert.equal(preview.label.attrs['text-anchor'], 'end');
    assert.equal(preview.label.attrs['aria-label'], '预设卖出水平价格 3,900.00');

    const ghost = renderer.renderHistoryGhost(adapter, svg, {
      side: 'takeProfit', width: 600, y: 90, labelX: 350,
      labelText: 'B2 历史止盈 4,100.00',
    });
    assert.equal(ghost.group.attrs.class, 'replay-trade-history-ghost replay-trade-history-ghost-takeProfit');
    assert.equal(child(ghost.group, 'text').textContent, 'B2 历史止盈 4,100.00');
  });
});

describe('interactive overlay structure without event ownership', () => {
  test('renders preset order nodes and returns interaction targets to the UI', () => {
    const adapter = makeAdapter();
    const svg = makeNode('svg', true);
    const result = renderer.renderPresetOrder(adapter, svg, {
      side: 'takeProfit', orderId: 'tp-1', width: 720, y: 100,
      labelText: 'B1止盈 4,000.00 +5.00% · +¥500.00',
      labelWidth: 260, labelX: 452, labelY: 89,
      ariaLabel: 'B1止盈 4,000.00 +5.00% · +¥500.00，上下拖动修改',
      deleteAriaLabel: '删除止盈预设',
    });
    assert.equal(result.group.attrs['data-preset-role'], 'takeProfit');
    assert.equal(result.group.attrs['data-preset-order-id'], 'tp-1');
    assert.equal(result.group.attrs.tabindex, '0');
    assert.deepEqual(result.group.children.map((item) => item.tag), ['line', 'line', 'rect', 'text', 'text']);
    assert.equal(child(result.group, 'line', 'replay-trade-preset-hit').attrs.x2, '720');
    assert.equal(child(result.group, 'text', 'replay-trade-preset-label').attrs.style, 'font-weight:400!important;');
    assert.equal(result.remove.attrs['aria-label'], '删除止盈预设');
    assert.equal(result.remove.textContent, '×');
  });
});
