const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_ROOT = path.resolve(__dirname, '..');
const ENGINE_FILE = path.join(JS_ROOT, 'replay-trade-engine.js');

function loadEngine(options = {}) {
  assert.ok(fs.existsSync(ENGINE_FILE), 'missing ReplayTradeEngine production module; the contract must not be weakened');
  const listeners = new Map();
  let networkCalls = 0;
  const window = {
    addEventListener(name, handler) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      const values = listeners.get(name) || [];
      listeners.set(name, values.filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    fetch() {
      networkCalls += 1;
      throw new Error('network access is forbidden in replay trade tests');
    },
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    Event: function Event(type) { this.type = type; },
  };
  window.window = window;
  const sandbox = {
    window,
    console,
    fetch: window.fetch,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(ENGINE_FILE, 'utf8'), context, { filename: ENGINE_FILE });
  const engine = window.ReplayTradeEngine || context.ReplayTradeEngine;
  assert.ok(engine, 'ReplayTradeEngine must be exported to the browser global');
  [
    'openManual',
    'closeManual',
    'setPendingOrder',
    'cancelPending',
    'updatePendingOrder',
    'updateExecution',
    'processBar',
    'reset',
    'getState',
  ].forEach((method) => assert.equal(typeof engine[method], 'function', `ReplayTradeEngine.${method} is required`));
  engine.reset(options);
  return {
    engine,
    window,
    get networkCalls() { return networkCalls; },
  };
}

function makeBars() {
  const base = 1704067200000;
  return [
    { timestamp: base, open: 10, high: 12, low: 9, close: 11 },
    { timestamp: base + 86400000, open: 11, high: 11.5, low: 7.5, close: 8 },
    { timestamp: base + 2 * 86400000, open: 8, high: 14, low: 7, close: 13 },
    { timestamp: base + 3 * 86400000, open: 13, high: 15, low: 12, close: 14 },
  ];
}

function closeEnough(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(Number(actual) - expected) <= epsilon, `${actual} is not close to ${expected}`);
}

function nativeValue(value) {
  return JSON.parse(JSON.stringify(value));
}

function positionOf(state) {
  assert.ok(state.position, 'an open position is required');
  return state.position;
}

describe('ReplayTradeEngine manual trade contract', () => {
  test('manual OHLC buy accepts the selected field and calculates amount/shares', () => {
    const bars = makeBars();
    for (const field of ['open', 'high', 'low', 'close']) {
      const { engine, networkCalls } = loadEngine({ defaultAmount: 1000 });
      const price = bars[0][field];
      assert.equal(engine.openManual({
        barIndex: 0,
        timestamp: bars[0].timestamp,
        field,
        price,
        amount: 900,
      }), true, `manual ${field} buy must be accepted`);

      const position = positionOf(engine.getState());
      assert.equal(position.entry.barIndex, 0);
      assert.equal(position.entry.field, field);
      assert.equal(position.entry.price, price);
      assert.equal(position.amount, 900);
      assert.equal(position.cost, 900);
      closeEnough(position.shares, 900 / price);
      closeEnough(position.quantity, 900 / price);
      assert.equal(networkCalls, 0);
    }
  });

  test('each next bar marks the open position at close and exposes loss amount/percent', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'low',
      price: bars[0].low,
      amount: 900,
    }), true);
    assert.equal(engine.processBar(bars[0], 0), true);
    assert.equal(engine.processBar(bars[1], 1), true);

    const state = engine.getState();
    assert.equal(state.cursor, 1);
    assert.equal(state.lastBar.close, 8);
    assert.equal(state.unrealizedPnl.amount, -100);
    closeEnough(state.unrealizedPnl.percent, -100 / 9);
    assert.equal(state.holdingPerformance.profitAmount, -100);
    closeEnough(state.holdingPerformance.profitPercent, -100 / 9);
    assert.equal(state.holdingPerformance.marketValue, 800);
    assert.equal(state.holdingPerformance.currentPrice, 8);
    assert.equal(state.realizedPnl.amount, 0);
    assert.equal(state.realizedPnl.percent, 0);
    assert.equal(positionOf(state).currentPrice, 8);
    assert.equal(positionOf(state).currentBarIndex, 1);
  });

  test('each processBar marks a holding at the close and exposes positive performance', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'open',
      price: bars[0].open,
      amount: 1000,
    }), true);

    engine.processBar(bars[0], 0);
    let state = engine.getState();
    assert.equal(state.holding, true);
    assert.equal(state.holdingPerformance.currentPrice, 11);
    assert.equal(state.holdingPerformance.marketValue, 1100);
    assert.equal(state.holdingPerformance.profitAmount, 100);
    assert.equal(state.holdingPerformance.profitPercent, 10);
    assert.equal(state.unrealizedPnl.amount, 100, 'legacy unrealized amount remains compatible');
    closeEnough(state.unrealizedPnl.percent, 10);

    engine.processBar(bars[2], 2);
    state = engine.getState();
    assert.equal(state.holdingPerformance.currentPrice, 13);
    assert.equal(state.holdingPerformance.marketValue, 1300);
    assert.equal(state.holdingPerformance.profitAmount, 300);
    assert.equal(state.holdingPerformance.profitPercent, 30);
    assert.equal(state.holdingPerformance.barIndex, 2);
  });

  test('manual OHLC sell records realized profit from the selected price field', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'low',
      price: bars[0].low,
      amount: 900,
    }), true);
    engine.processBar(bars[0], 0);
    engine.processBar(bars[1], 1);
    engine.processBar(bars[2], 2);
    assert.equal(engine.closeManual({
      barIndex: 2,
      timestamp: bars[2].timestamp,
      field: 'high',
      price: bars[2].high,
    }), true);

    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.position, null);
    assert.equal(state.realizedPnl.amount, 500);
    closeEnough(state.realizedPnl.percent, 500 / 900 * 100);
    assert.equal(state.completedTrades.length, 1);
    const trade = state.completedTrades[0];
    assert.equal(trade.entry.field, 'low');
    assert.equal(trade.exit.field, 'high');
    assert.equal(trade.exit.price, 14);
    assert.equal(trade.pnlAmount, 500);
    closeEnough(trade.pnlPercent, 500 / 900 * 100);
    const settlement = state.lastSettlement;
    assert.equal(state.settlement.exitPrice, 14);
    assert.equal(settlement.entryPrice, 9);
    assert.equal(settlement.exitPrice, 14);
    assert.equal(settlement.investedAmount, 900);
    assert.equal(settlement.cost, 900);
    assert.equal(settlement.proceeds, 1400);
    assert.equal(settlement.quantity, 100);
    assert.equal(settlement.shares, 100);
    assert.equal(settlement.profitAmount, 500);
    closeEnough(settlement.profitPercent, 500 / 900 * 100);
    assert.equal(settlement.remainingQuantity, 0);
    assert.equal(settlement.entryBarIndex, 0);
    assert.equal(settlement.entryTimestamp, bars[0].timestamp);
    assert.equal(settlement.entryField, 'low');
    assert.equal(settlement.exitBarIndex, 2);
    assert.equal(settlement.exitTimestamp, bars[2].timestamp);
    assert.equal(settlement.exitField, 'high');
    assert.equal(state.holdingPerformance, null);
  });

  test('state and executed snapshots expose the same manual settlement', () => {
    const { engine, window } = loadEngine();
    const bars = makeBars();
    const stateEvents = [];
    const executionEvents = [];
    window.addEventListener('replay-trade-state', (event) => stateEvents.push(event.detail));
    window.addEventListener('replay-trade-executed', (event) => executionEvents.push(event.detail));

    engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'open',
      price: bars[0].open,
      amount: 1000,
    });
    engine.processBar(bars[1], 1);
    engine.closeManual({
      barIndex: 2,
      timestamp: bars[2].timestamp,
      field: 'close',
      price: bars[2].close,
    });

    const latestState = stateEvents[stateEvents.length - 1];
    const latestExecution = executionEvents[executionEvents.length - 1];
    assert.ok(latestState.lastSettlement);
    assert.ok(latestExecution.settlement);
    assert.deepEqual(nativeValue(latestExecution.settlement), nativeValue(latestState.lastSettlement));
    assert.deepEqual(nativeValue(latestExecution.state.lastSettlement), nativeValue(latestState.lastSettlement));
  });

  test('manual buy and sell cannot cross on one bar, but the next bar can close it', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.processBar(bars[1], 1);
    assert.equal(engine.openManual({
      barIndex: 1,
      timestamp: bars[1].timestamp,
      field: 'low',
      price: bars[1].low,
      amount: 750,
    }), true);

    assert.equal(engine.closeManual({
      barIndex: 1,
      timestamp: bars[1].timestamp,
      field: 'high',
      price: bars[1].high,
    }), false, 'same-bar manual sell must be rejected');
    assert.equal(engine.getState().holding, true);

    engine.processBar(bars[2], 2);
    assert.equal(engine.closeManual({
      barIndex: 2,
      timestamp: bars[2].timestamp,
      field: 'close',
      price: bars[2].close,
    }), true, 'next-bar manual sell must be accepted');
    assert.equal(engine.getState().holding, false);
  });
});

describe('ReplayTradeEngine preset-order contract', () => {
  test('allows repeated preset buys while holding and accumulates weighted lots', () => {
    const { engine } = loadEngine({ defaultAmount: 1000 });
    const bars = makeBars();
    assert.equal(engine.setPendingOrder({ side: 'buy', price: 10, amount: 1000 }), true);
    engine.processBar(bars[0], 0);
    assert.equal(engine.setPendingOrder({ side: 'buy', price: 8, amount: 800 }), true);
    engine.processBar(bars[1], 1);

    const state = engine.getState();
    assert.equal(state.position.lots.length, 2);
    closeEnough(state.position.entryPrice, 9);
    closeEnough(state.position.shares, 200);
    closeEnough(state.position.quantity, 200);
    closeEnough(state.position.amount, 1800);
    closeEnough(state.position.cost, 1800);
    assert.equal(state.executions.length, 2);
    assert.equal(state.records.length, 2);
    assert.equal(state.executions[0].side, 'buy');
    assert.equal(state.executions[1].side, 'buy');
  });

  test('partial sells use weighted cost, retain the position, then the next sell clears it', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.openManual({ barIndex: 1, timestamp: bars[1].timestamp, field: 'close', price: 8, amount: 800 });

    assert.equal(engine.closeManual({
      barIndex: 2,
      timestamp: bars[2].timestamp,
      field: 'close',
      price: 12,
      quantity: 50,
    }), true);
    let state = engine.getState();
    assert.equal(state.holding, true);
    closeEnough(state.lastSettlement.cost, 450);
    closeEnough(state.lastSettlement.proceeds, 600);
    closeEnough(state.lastSettlement.profitAmount, 150);
    closeEnough(state.lastSettlement.profitPercent, 150 / 450 * 100);
    closeEnough(state.lastSettlement.remainingQuantity, 150);
    closeEnough(state.position.entryPrice, 9);
    closeEnough(state.position.cost, 1350);
    closeEnough(state.realizedAmount, 150);
    closeEnough(state.realizedCost, 450);

    assert.equal(engine.closeManual({
      barIndex: 3,
      timestamp: bars[3].timestamp,
      field: 'close',
      price: 11,
    }), true);
    state = engine.getState();
    assert.equal(state.status, 'flat');
    assert.equal(state.position, null);
    closeEnough(state.lastSettlement.cost, 1350);
    closeEnough(state.lastSettlement.proceeds, 1650);
    closeEnough(state.lastSettlement.profitAmount, 300);
    closeEnough(state.realizedAmount, 450);
    closeEnough(state.realizedCost, 1800);
    assert.equal(state.completedTrades.length, 2);
    assert.equal(state.executions.length, 4);
  });

  test('updateExecution recalculates an open buy and rejects settled history edits', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.openManual({ barIndex: 1, timestamp: bars[1].timestamp, field: 'close', price: 20, amount: 1000 });
    engine.processBar(bars[2], 2);
    const secondBuyId = engine.getState().executions[1].id;

    assert.equal(engine.updateExecution(secondBuyId, { price: 25, amount: 2000 }), true);
    let state = engine.getState();
    closeEnough(state.position.entryPrice, 3000 / 180);
    closeEnough(state.position.quantity, 180);
    closeEnough(state.position.amount, 3000);
    closeEnough(state.position.marketValue, 13 * 180);
    assert.equal(state.lastError, null);

    assert.equal(engine.closeManual({
      barIndex: 3,
      timestamp: bars[3].timestamp,
      field: 'close',
      price: 14,
      quantity: 10,
    }), true);
    const firstBuyId = state.executions[0].id;
    assert.equal(engine.updateExecution(firstBuyId, { price: 11 }), false);
    state = engine.getState();
    assert.match(state.lastError, /settled|结算/);
  });

  test('low-high range triggers preset buy and sell on later bars', () => {
    const { engine } = loadEngine({ defaultAmount: 850 });
    const bars = makeBars();
    assert.equal(engine.setPendingOrder({ side: 'buy', price: 8.5, amount: 850 }), true);
    assert.equal(engine.setPendingOrder({ side: 'sell', price: 12 }), true);

    engine.processBar(bars[0], 0);
    assert.equal(engine.getState().holding, false, 'bar 0 does not reach the buy range');
    engine.processBar(bars[1], 1);
    const holding = positionOf(engine.getState());
    assert.equal(holding.entry.price, 8.5);
    assert.equal(holding.entry.field, null, 'preset execution is not a manual OHLC selection');
    closeEnough(holding.shares, 100);
    assert.equal(engine.getState().pending.buy, null);
    assert.equal(engine.getState().pending.sell.price, 12);

    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.lastExecution.trigger, 'pending');
    assert.equal(state.lastExecution.price, 12);
    assert.equal(state.realizedPnl.amount, 350);
    closeEnough(state.realizedPnl.percent, 350 / 850 * 100);
    assert.equal(state.lastSettlement.entryPrice, 8.5);
    assert.equal(state.lastSettlement.exitPrice, 12);
    assert.equal(state.lastSettlement.investedAmount, 850);
    assert.equal(state.lastSettlement.cost, 850);
    assert.equal(state.lastSettlement.proceeds, 1200);
    assert.equal(state.lastSettlement.quantity, 100);
    assert.equal(state.lastSettlement.remainingQuantity, 0);
    assert.equal(state.lastSettlement.entryBarIndex, 1);
    assert.equal(state.lastSettlement.exitBarIndex, 2);
    assert.equal(state.lastExecution.settlement.profitAmount, 350);
  });

  test('preset buy and sell cannot trigger on the same bar, then sell on the next bar', () => {
    const { engine } = loadEngine({ defaultAmount: 850 });
    const bars = makeBars();
    assert.equal(engine.setPendingOrder({ side: 'buy', price: 8.5, amount: 850 }), true);
    assert.equal(engine.setPendingOrder({ side: 'sell', price: 10 }), true);

    engine.processBar(bars[1], 1);
    assert.equal(engine.getState().holding, true);
    assert.equal(engine.getState().lastExecution.side, 'buy');
    assert.equal(engine.getState().pending.sell.price, 10);
    engine.processBar(bars[2], 2);
    assert.equal(engine.getState().holding, false);
    assert.equal(engine.getState().lastExecution.side, 'sell');
  });

  test('bracket orders are stored independently and accept side, type, and role aliases', () => {
    const { engine } = loadEngine();
    assert.equal(engine.setPendingOrder({ side: 'takeProfit', price: 14, quantity: 40 }), true);
    assert.equal(engine.setPendingOrder({ type: 'stop-loss', price: 7, quantity: 25 }), true);

    const state = engine.getState();
    assert.equal(state.pendingOrders.takeProfit.price, 14);
    assert.equal(state.pendingOrders.takeProfit.quantity, 40);
    assert.equal(state.pendingOrders.stopLoss.price, 7);
    assert.equal(state.pendingOrders.stopLoss.quantity, 25);
    assert.equal(state.bracketOrders.takeProfit.type, 'takeProfit');
    assert.equal(state.bracketOrders.stopLoss.role, 'stopLoss');
    assert.equal(state.pending.buy, null);
    assert.equal(state.pending.sell, null);

    assert.equal(engine.setPendingOrder({ role: 'take_profit', price: 13, quantity: 30 }), true);
    assert.equal(engine.getState().pendingOrders.takeProfit.price, 13);
    assert.equal(engine.getState().pendingOrders.takeProfit.quantity, 30);
  });

  test('bracket pending prices can be updated without losing quantity', () => {
    const { engine } = loadEngine();
    assert.equal(engine.setPendingOrder({ side: 'takeProfit', price: 12, quantity: 50 }), true);
    assert.equal(engine.updatePendingOrder('take-profit', { price: 13 }), true);
    let state = engine.getState();
    assert.equal(state.pendingOrders.takeProfit.price, 13);
    assert.equal(state.pendingOrders.takeProfit.quantity, 50);

    assert.equal(engine.updatePendingOrder({ role: 'stopLoss', price: 8, quantity: 20 }), false);
    assert.match(engine.getState().lastError, /not found|不存在/);
    assert.equal(engine.setPendingOrder({ side: 'stopLoss', price: 8, quantity: 20 }), true);
    assert.equal(engine.updatePendingOrder({ type: 'stop_loss', price: 7 }), true);
    state = engine.getState();
    assert.equal(state.pendingOrders.stopLoss.price, 7);
    assert.equal(state.pendingOrders.stopLoss.quantity, 20);
  });

  test('cancelPending removes one bracket or all pending order types', () => {
    const { engine } = loadEngine();
    engine.setPendingOrder({ side: 'buy', price: 8, amount: 800 });
    engine.setPendingOrder({ side: 'takeProfit', price: 12, quantity: 80 });
    engine.setPendingOrder({ role: 'stop_loss', price: 7, quantity: 80 });

    assert.equal(engine.cancelPending({ type: 'stop-loss' }), true);
    assert.equal(engine.getState().pendingOrders.stopLoss, null);
    assert.equal(engine.getState().pendingOrders.takeProfit.price, 12);
    assert.equal(engine.cancelPending('takeProfit'), true);
    assert.equal(engine.getState().pendingOrders.takeProfit, null);
    assert.equal(engine.getState().pendingOrders.buy.price, 8);
    assert.equal(engine.cancelPending(), true);
    assert.deepEqual(nativeValue(engine.getState().pending), {
      buy: null,
      sell: null,
      takeProfit: null,
      stopLoss: null,
    });
  });

  test('take-profit triggers on a later bar and clears both bracket exits', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.processBar(bars[0], 0);
    engine.setPendingOrder({ type: 'take_profit', price: 12 });
    engine.setPendingOrder({ side: 'stopLoss', price: 6 });

    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.lastExecution.trigger, 'pending-takeProfit');
    assert.equal(state.lastExecution.price, 12);
    assert.equal(state.realizedPnl.amount, 200);
    assert.equal(state.pendingOrders.takeProfit, null);
    assert.equal(state.pendingOrders.stopLoss, null);
  });

  test('stop-loss wins when take-profit and stop-loss are both hit on one bar', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.processBar(bars[0], 0);
    engine.setPendingOrder({ side: 'takeProfit', price: 12 });
    engine.setPendingOrder({ side: 'stopLoss', price: 9 });

    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.lastExecution.trigger, 'pending-stopLoss');
    assert.equal(state.lastExecution.price, 8, 'a gap below the stop fills at the worse opening price');
    assert.equal(state.realizedPnl.amount, -200);
    assert.equal(state.pendingOrders.takeProfit, null);
    assert.equal(state.pendingOrders.stopLoss, null);
  });

  test('high and low crossings execute bound take-profit and stop-loss orders', () => {
    const cases = [
      {
        side: 'takeProfit',
        target: 10.5,
        bar: { timestamp: 1704153600000, open: 10, high: 10.5, low: 9.8, close: 10.2 },
        expectedProfit: 50,
      },
      {
        side: 'stopLoss',
        target: 9.5,
        bar: { timestamp: 1704153600000, open: 10, high: 10.2, low: 9.5, close: 9.8 },
        expectedProfit: -50,
      },
    ];

    cases.forEach(({ side, target, bar, expectedProfit }) => {
      const { engine } = loadEngine();
      assert.equal(engine.openManual({
        barIndex: 0,
        timestamp: 1704067200000,
        field: 'close',
        price: 10,
        amount: 1000,
      }), true);
      assert.ok(bar.low <= target && target <= bar.high, `${side} target must be inside the K-line range`);
      assert.equal(engine.setPendingOrder({ side, price: target, orderNumbers: [1] }), true);
      assert.equal(engine.processBar(bar, 1), true);

      const state = engine.getState();
      assert.equal(state.holding, false);
      assert.equal(state.lastExecution.trigger, `pending-${side}`);
      assert.equal(state.lastExecution.label, 'S1');
      assert.equal(state.lastExecution.price, target);
      assert.equal(state.lastSettlement.exitPrice, target);
      closeEnough(state.lastSettlement.profitAmount, expectedProfit);
      assert.equal(state.completedTrades[0].plannedBrackets[0].side, side);
      assert.equal(state.pendingOrders.takeProfit, null);
      assert.equal(state.pendingOrders.stopLoss, null);
    });
  });

  test('bracket exits do not trigger on the same bar as a pending buy', () => {
    const { engine } = loadEngine({ defaultAmount: 850 });
    const bars = makeBars();
    engine.setPendingOrder({ side: 'buy', price: 8.5 });
    engine.setPendingOrder({ side: 'takeProfit', price: 10 });
    engine.setPendingOrder({ side: 'stopLoss', price: 8 });

    engine.processBar(bars[1], 1);
    const state = engine.getState();
    assert.equal(state.holding, true);
    assert.equal(state.lastExecution.side, 'buy');
    assert.equal(state.pendingOrders.takeProfit.price, 10);
    assert.equal(state.pendingOrders.stopLoss.price, 8);
  });

  test('an existing B1 still stops out when the same bar also fills pending B2', () => {
    const { engine } = loadEngine({ defaultAmount: 850 });
    const bars = makeBars();

    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'close',
      price: 10,
      amount: 1000,
    }), true);
    engine.processBar(bars[0], 0);

    assert.equal(engine.setPendingOrder({
      side: 'stopLoss',
      price: 9,
      orderNumbers: [1],
    }), true);
    assert.equal(engine.setPendingOrder({
      side: 'buy',
      price: 8.5,
      amount: 850,
    }), true);

    // Bar 1 reaches B1's stop at 9 and also reaches B2's pending buy at 8.5.
    assert.equal(engine.processBar(bars[1], 1), true);

    const state = engine.getState();
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [2]);
    assert.equal(state.lastExecution.side, 'sell');
    assert.equal(state.lastExecution.trigger, 'pending-stopLoss');
    assert.equal(state.lastExecution.label, 'S1');
    assert.equal(state.lastExecution.price, 9);
    assert.deepEqual(nativeValue(state.lastExecution.orderNumbers), [1]);
    assert.deepEqual(nativeValue(state.lastSettlement.closedOrderNumbers), [1]);
    assert.equal(state.bracketOrders.length, 0, 'B1 stop-loss must not remain active after B1 is closed');
    assert.equal(state.position.lots[0].label, 'B2');
  });

  test('a stop-loss triggers after price gaps completely below the stop', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.setPendingOrder({ side: 'stopLoss', price: 9, orderNumbers: [1] });
    engine.processBar({ timestamp: bars[1].timestamp, open: 8, high: 8.5, low: 7.5, close: 8.2 }, 1);
    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.lastExecution.price, 8);
    assert.equal(state.lastExecution.label, 'S1');
    assert.deepEqual(nativeValue(state.lastExecution.orderNumbers), [1]);
  });

  test('a take-profit triggers after price gaps completely above the target', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.setPendingOrder({ side: 'takeProfit', price: 12, orderNumbers: [1] });
    engine.processBar({ timestamp: bars[1].timestamp, open: 13, high: 14, low: 12.5, close: 13.5 }, 1);
    const state = engine.getState();
    assert.equal(state.lastExecution.price, 13);
    assert.equal(state.lastExecution.label, 'S1');
    assert.equal(state.lastSettlement.exit.price, 13);
  });

  test('a manual exit cancels stale take-profit and stop-loss orders', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    engine.setPendingOrder({ side: 'takeProfit', price: 12 });
    engine.setPendingOrder({ side: 'stopLoss', price: 8 });
    assert.equal(engine.closeManual({
      barIndex: 1,
      timestamp: bars[1].timestamp,
      field: 'close',
      price: 9,
    }), true);
    const state = engine.getState();
    assert.equal(state.pendingOrders.takeProfit, null);
    assert.equal(state.pendingOrders.stopLoss, null);
  });

  test('cancelPending removes one preset or all presets without touching a position', () => {
    const { engine } = loadEngine();
    assert.equal(engine.setPendingOrder({ side: 'buy', price: 8, amount: 800 }), true);
    assert.equal(engine.setPendingOrder({ side: 'sell', price: 12 }), true);
    assert.equal(engine.cancelPending('buy'), true);
    assert.equal(engine.getState().pending.buy, null);
    assert.equal(engine.getState().pending.sell.price, 12);
    assert.equal(engine.cancelPending(), true);
    assert.deepEqual(nativeValue(engine.getState().pending), {
      buy: null,
      sell: null,
      takeProfit: null,
      stopLoss: null,
    });
  });

  test('reset clears position, presets, cursor, realized and floating results', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.setPendingOrder({ side: 'buy', price: 8.5, amount: 850 });
    engine.processBar(bars[1], 1);
    engine.processBar(bars[2], 2);
    engine.setPendingOrder({ side: 'sell', price: 13 });
    assert.equal(engine.getState().holding, true);
    engine.processBar(bars[3], 3);
    assert.equal(engine.getState().holding, false);
    assert.ok(engine.getState().lastSettlement);

    assert.equal(engine.reset(), true);
    const state = engine.getState();
    assert.equal(state.status, 'flat');
    assert.equal(state.holding, false);
    assert.equal(state.position, null);
    assert.deepEqual(nativeValue(state.pending), {
      buy: null,
      sell: null,
      takeProfit: null,
      stopLoss: null,
    });
    assert.equal(state.cursor, -1);
    assert.equal(state.realizedPnl.amount, 0);
    assert.equal(state.realizedPnl.percent, 0);
    assert.equal(state.unrealizedPnl.amount, 0);
    assert.equal(state.unrealizedPnl.percent, 0);
    assert.equal(state.holdingPerformance, null);
    assert.equal(state.lastSettlement, null);
    assert.equal(state.settlement, null);
    assert.deepEqual(Array.from(state.completedTrades), []);
  });
});

describe('ReplayTradeEngine lot-bound bracket contract', () => {
  function openTwoLots(engine, bars = makeBars()) {
    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'close',
      price: 10,
      amount: 1000,
    }), true);
    assert.equal(engine.openManual({
      barIndex: 1,
      timestamp: bars[1].timestamp,
      field: 'close',
      price: 8,
      amount: 800,
    }), true);
    return bars;
  }

  test('assigns stable one-based order numbers and exposes per-lot performance', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    engine.processBar(bars[1], 1);
    const state = engine.getState();
    assert.deepEqual(nativeValue(state.executions.slice(0, 2).map((execution) => ({
      orderNumber: execution.orderNumber,
      label: execution.label,
    }))), [
      { orderNumber: 1, label: 'B1' },
      { orderNumber: 2, label: 'B2' },
    ]);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [1, 2]);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.label)), ['B1', 'B2']);
    const firstLot = state.position.lots[0];
    assert.equal(firstLot.investedAmount, 1000);
    closeEnough(firstLot.remainingQuantity, 100);
    closeEnough(firstLot.currentMarketValue, 800);
    closeEnough(firstLot.unrealizedAmount, -200);
    closeEnough(firstLot.unrealizedPercent, -20);
  });

  test('take-profit bound only to B1 closes B1 while B2 remains open', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    const firstExecutionId = engine.getState().executions[0].id;
    engine.setPendingOrder({
      side: 'takeProfit',
      price: 12,
      executionIds: [firstExecutionId],
    });
    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, true);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [2]);
    assert.equal(state.lastExecution.trigger, 'pending-takeProfit');
    assert.deepEqual(nativeValue(state.lastExecution.orderNumbers), [1]);
    closeEnough(state.lastSettlement.profitAmount, 200);
    closeEnough(state.lastSettlement.profitPercent, 20);
    assert.equal(state.bracketOrders.length, 0);
    assert.equal(state.lastExecution.label, 'S1');
    assert.equal(state.completedTrades[0].plannedBrackets[0].side, 'takeProfit');
  });

  test('moving an open B point updates both its date and price', () => {
    const { engine } = loadEngine();
    const bars = makeBars();
    engine.openManual({ barIndex: 0, timestamp: bars[0].timestamp, field: 'close', price: 10, amount: 1000 });
    const execution = engine.getState().executions[0];
    assert.equal(engine.updateExecution(execution.id, {
      barIndex: 1,
      timestamp: bars[1].timestamp,
      field: 'close',
      price: 8,
      amount: 1000,
    }), true);
    const state = engine.getState();
    assert.equal(state.executions[0].barIndex, 1);
    assert.equal(state.executions[0].timestamp, bars[1].timestamp);
    assert.equal(state.position.lots[0].barIndex, 1);
    assert.equal(state.position.entryPrice, 8);
  });

  test('an unbound bracket defaults to all currently open lots', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    engine.setPendingOrder({ side: 'takeProfit', price: 12 });
    let state = engine.getState();
    assert.deepEqual(nativeValue(state.bracketOrders[0].orderNumbers), [1, 2]);
    engine.processBar(bars[2], 2);
    state = engine.getState();
    assert.equal(state.holding, false);
    assert.deepEqual(nativeValue(state.lastSettlement.closedOrderNumbers), [1, 2]);
  });

  test('stop-loss bound only to B2 closes B2 and keeps B1 floating', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    engine.setPendingOrder({
      side: 'stopLoss',
      price: 7,
      orderNumbers: [2],
    });
    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, true);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [1]);
    assert.equal(state.lastExecution.trigger, 'pending-stopLoss');
    assert.deepEqual(nativeValue(state.lastExecution.orderNumbers), [2]);
    closeEnough(state.lastSettlement.profitAmount, -100);
    closeEnough(state.lastSettlement.profitPercent, -12.5);
    closeEnough(state.position.lots[0].unrealizedAmount, 300);
  });

  test('same-type brackets coexist and expose expected profit or loss amounts', () => {
    const { engine } = loadEngine();
    openTwoLots(engine);
    engine.setPendingOrder({ side: 'takeProfit', price: 12, orderNumbers: [1] });
    engine.setPendingOrder({ side: 'takeProfit', price: 10, orderNumbers: [2] });
    engine.setPendingOrder({ side: 'stopLoss', price: 7, orderNumbers: [2] });
    const state = engine.getState();
    assert.equal(state.bracketOrders.length, 3);
    const firstTakeProfit = state.bracketOrders.find((order) => (
      order.side === 'takeProfit' && order.orderNumbers.includes(1)
    ));
    const stopLoss = state.bracketOrders.find((order) => order.side === 'stopLoss');
    closeEnough(firstTakeProfit.expectedAmount, 200);
    closeEnough(firstTakeProfit.expectedProfitAmount, 200);
    closeEnough(stopLoss.expectedAmount, -100);
    closeEnough(stopLoss.expectedLossAmount, -100);
    assert.equal(state.pendingOrders.takeProfit.price, 10, 'legacy view exposes the latest plan');
  });

  test('partial bracket execution sells only the selected lots proportionally', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    engine.setPendingOrder({
      side: 'takeProfit',
      price: 12,
      quantity: 50,
      orderNumbers: [1, 2],
    });
    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.holding, true);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [1, 2]);
    closeEnough(state.position.lots[0].remainingQuantity, 75);
    closeEnough(state.position.lots[1].remainingQuantity, 75);
    closeEnough(state.lastSettlement.quantity, 50);
    closeEnough(state.lastSettlement.profitAmount, 150);
  });

  test('closing B1 removes overlapping opposite bindings but preserves B2 plans', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    engine.setPendingOrder({ side: 'takeProfit', price: 12, orderNumbers: [1] });
    engine.setPendingOrder({ side: 'stopLoss', price: 7, orderNumbers: [1] });
    engine.setPendingOrder({ side: 'takeProfit', price: 20, orderNumbers: [2] });
    engine.processBar(bars[2], 2);
    const state = engine.getState();
    assert.equal(state.position.lots.length, 1);
    assert.equal(state.position.lots[0].orderNumber, 2);
    assert.equal(state.bracketOrders.length, 1);
    assert.equal(state.bracketOrders[0].side, 'takeProfit');
    assert.deepEqual(nativeValue(state.bracketOrders[0].orderNumbers), [2]);
  });

  test('manual S1 closes only B1 while B2 remains open', () => {
    const { engine } = loadEngine();
    const bars = openTwoLots(engine);
    assert.equal(engine.closeManual({
      barIndex: 2,
      timestamp: bars[2].timestamp,
      field: 'close',
      price: 12,
      orderNumbers: [1],
    }), true);
    const state = engine.getState();
    assert.equal(state.holding, true);
    assert.deepEqual(nativeValue(state.position.lots.map((lot) => lot.orderNumber)), [2]);
    assert.equal(state.lastExecution.label, 'S1');
    assert.deepEqual(nativeValue(state.lastExecution.orderNumbers), [1]);
    assert.deepEqual(nativeValue(state.lastSettlement.closedOrderNumbers), [1]);
    closeEnough(state.lastSettlement.profitAmount, 200);
  });

  test('a jumped replay cursor still evaluates every skipped bar for bracket exits', () => {
    const { engine, window } = loadEngine();
    const bars = makeBars();
    assert.equal(engine.openManual({
      barIndex: 0,
      timestamp: bars[0].timestamp,
      field: 'close',
      price: 10,
      amount: 1000,
    }), true);
    assert.equal(engine.setPendingOrder({
      side: 'takeProfit',
      price: 11.5,
      orderNumbers: [1],
    }), true);

    window.dispatchEvent(new window.CustomEvent('bar-replay-cursor', {
      detail: { cursor: 2, index: 2, bar: bars[2], history: bars },
    }));

    const state = engine.getState();
    assert.equal(state.holding, false);
    assert.equal(state.lastExecution.label, 'S1');
    assert.equal(state.lastExecution.trigger, 'pending-takeProfit');
    assert.equal(state.lastSettlement.exitPrice, 11.5);
    assert.equal(state.lastSettlement.exitBarIndex, 1);
  });
});
