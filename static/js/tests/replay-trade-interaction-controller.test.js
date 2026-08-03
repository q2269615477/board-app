const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const MODULE_FILE = path.resolve(__dirname, '..', 'replay-trade-interaction-controller.js');
const Controller = require(MODULE_FILE);

test('controller exposes UMD/CommonJS factory without owning chart or trade rules', () => {
  assert.equal(typeof Controller.create, 'function');
  assert.equal(Controller.createController, Controller.create);
  const source = fs.readFileSync(MODULE_FILE, 'utf8');
  for (const forbidden of [
    'convertFromPixel', 'convertToPixel', 'candle_pane', 'ReplayTradeEngine',
    'submitPresetAtPrice', 'showPricePicker', 'document.',
  ]) assert.equal(source.includes(forbidden), false, `${forbidden} must stay in ReplayTradeUI`);

  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  assert.equal(typeof sandbox.ReplayTradeInteractionController.create, 'function');
});

function makeTarget() {
  const listeners = new Map();
  return {
    classEvents: [],
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      listeners.set(type, (listeners.get(type) || []).filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    listenerCount(type) {
      return (listeners.get(type) || []).length;
    },
  };
}

function makeHarness() {
  const root = makeTarget();
  const mainDom = makeTarget();
  const actions = { trade: [], preset: [], status: [], redraws: 0, classes: [], closeKinds: [] };
  const controller = Controller.create({
    root,
    eventTarget: root,
    getMainDom: () => mainDom,
    isReplayActive: () => true,
    geometry: {
      pointFromEvent: (event) => event.point || null,
      priceFromEvent: (event) => event.pricePoint || null,
      eventCoordinates: (event) => ({ x: event.clientX, y: event.clientY }),
    },
    callbacks: {
      normalizeSide: (value) => value,
      normalizeRole: (value) => value,
      roleLabel: (value) => value,
      closePanels: (kind) => actions.closeKinds.push(kind),
      setSelectingClass: (dom, className, enabled) => actions.classes.push({ dom, className, enabled }),
      onTradePoint: (side, point) => actions.trade.push({ side, point }),
      onPresetPoint: (side, amount, point) => {
        actions.preset.push({ side, amount, point });
        return true;
      },
      setStatus: (message) => actions.status.push(message),
      redraw: () => { actions.redraws += 1; },
      updateControls: () => {},
    },
  });
  controller.init();
  return { root, mainDom, actions, controller };
}

test('manual B/S selection delegates chart points and unbinds on cancel', () => {
  const env = makeHarness();
  assert.equal(env.controller.enterSelection('buy'), true);
  assert.equal(env.mainDom.listenerCount('click'), 1);
  const point = { index: 2, row: { close: 10 }, x: 20, y: 30 };
  env.mainDom.dispatchEvent({ type: 'click', point, preventDefault() {} });
  assert.deepEqual(env.actions.trade, [{ side: 'buy', point }]);
  assert.equal(env.controller.getState().mode, 'buy');
  assert.deepEqual(env.actions.closeKinds, ['trade']);

  env.controller.cancelSelection();
  assert.equal(env.mainDom.listenerCount('click'), 0);
  env.mainDom.dispatchEvent({ type: 'click', point });
  assert.equal(env.actions.trade.length, 1);
  assert.ok(env.actions.classes.some((item) => item.className === 'replay-trade-selecting' && item.enabled === false));
});

test('preset selection owns preview session, commits through callback, and handles Escape', () => {
  const env = makeHarness();
  assert.equal(env.controller.enterPresetSelection('takeProfit', null), true);
  assert.ok(env.actions.closeKinds.includes('preset'));
  assert.equal(env.mainDom.listenerCount('mousemove'), 1);
  assert.equal(env.mainDom.listenerCount('click'), 1);
  const preview = { price: 11.25, x: 120, y: 220 };
  env.mainDom.dispatchEvent({ type: 'mousemove', pricePoint: preview, clientX: 120, clientY: 220 });
  let state = env.controller.getState().presetSelection;
  assert.equal(state.active, true);
  assert.equal(state.side, 'takeProfit');
  assert.equal(state.previewPrice, 11.25);
  env.mainDom.dispatchEvent({ type: 'click', clientX: 120, clientY: 220, preventDefault() {} });
  assert.deepEqual(env.actions.preset, [{ side: 'takeProfit', amount: null, point: preview }]);
  assert.equal(env.controller.getState().presetSelection.active, false);
  assert.equal(env.mainDom.listenerCount('mousemove'), 0);
  assert.equal(env.root.listenerCount('keydown'), 0);

  assert.equal(env.controller.enterPresetSelection('buy', 1500), true);
  env.root.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
  state = env.controller.getState().presetSelection;
  assert.equal(state.active, false);
  assert.equal(state.previewPrice, null);
  assert.ok(env.actions.status.includes('已取消预设选点'));
});

test('destroy is idempotent and releases selection/preset listeners', () => {
  const env = makeHarness();
  assert.equal(env.controller.enterSelection('sell'), true);
  env.controller.destroy();
  env.controller.destroy();
  assert.equal(env.mainDom.listenerCount('click'), 0);
  assert.equal(env.root.listenerCount('keydown'), 0);
  assert.equal(env.controller.getState().mode, null);
  assert.equal(env.controller.getState().presetSelection.active, false);
  assert.ok(env.actions.classes.some((item) =>
    item.className === 'replay-trade-selecting' && item.enabled === false));
});

test('listener setup failures roll back selection state instead of reporting success', () => {
  const root = makeTarget();
  const brokenDom = makeTarget();
  brokenDom.addEventListener = () => { throw new Error('bind failed'); };
  const controller = Controller.create({
    root,
    eventTarget: root,
    getMainDom: () => brokenDom,
    isReplayActive: () => true,
    callbacks: {
      normalizeSide: (value) => value,
      normalizeRole: (value) => value,
      closePanels: () => {},
      setSelectingClass: () => {},
      updateControls: () => {},
      redraw: () => {},
    },
  });
  controller.init();

  assert.equal(controller.enterSelection('buy'), false);
  assert.equal(controller.getState().mode, null);
  assert.equal(controller.enterPresetSelection('takeProfit', null), false);
  assert.equal(controller.getState().presetSelection.active, false);
});
