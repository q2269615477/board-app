const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const MODEL_FILE = path.resolve(__dirname, '..', 'replay-trade-state-model.js');
const model = require(MODEL_FILE);

function buyRecord(overrides = {}) {
  return Object.assign({
    side: 'buy',
    timestamp: 1000,
    index: 1,
    price: 10,
    amount: 100,
    quantity: 10,
  }, overrides);
}

describe('ReplayTradeStateModel UMD/CommonJS contract', () => {
  test('exports the required pure helpers without requiring a DOM', () => {
    for (const name of [
      'flattenTradeState', 'positionAggregate', 'normalizeRecord', 'recordIdentity',
      'finite', 'timestamp', 'sideOf', 'assignOrderNumbers', 'appendUniqueRecord',
    ]) {
      assert.equal(typeof model[name], 'function', `${name} must be exported`);
    }
  });

  test('attaches itself to the browser global as ReplayTradeStateModel', () => {
    const code = fs.readFileSync(MODEL_FILE, 'utf8');
    const sandbox = {};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
    assert.equal(typeof sandbox.ReplayTradeStateModel.flattenTradeState, 'function');
    assert.equal(typeof sandbox.ReplayTradeStateModel.positionAggregate, 'function');
    assert.equal(typeof sandbox.ReplayTradeStateModel.sideOf, 'function');
  });
});

describe('finite / timestamp / sideOf', () => {
  test('finite accepts numeric strings and rejects nullish, empty and non-finite values', () => {
    assert.equal(model.finite(null), null);
    assert.equal(model.finite(undefined), null);
    assert.equal(model.finite(''), null);
    assert.equal(model.finite('12.5'), 12.5);
    assert.equal(model.finite(0), 0);
    assert.equal(model.finite('abc'), null);
    assert.equal(model.finite(Infinity), null);
  });

  test('timestamp converts seconds to milliseconds and leaves milliseconds untouched', () => {
    assert.equal(model.timestamp(1600000000), 1600000000000);
    assert.equal(model.timestamp(1600000000000), 1600000000000);
    assert.equal(model.timestamp('1600000000'), 1600000000000);
    assert.equal(model.timestamp(null), null);
    assert.equal(model.timestamp(''), null);
    assert.equal(model.timestamp('nope'), null);
  });

  test('sideOf maps buy/sell synonyms and rejects unknown sides', () => {
    assert.equal(model.sideOf('buy'), 'buy');
    assert.equal(model.sideOf('b'), 'buy');
    assert.equal(model.sideOf('long'), 'buy');
    assert.equal(model.sideOf('entry'), 'buy');
    assert.equal(model.sideOf('in'), 'buy');
    assert.equal(model.sideOf('sell'), 'sell');
    assert.equal(model.sideOf('s'), 'sell');
    assert.equal(model.sideOf('short'), 'sell');
    assert.equal(model.sideOf('exit'), 'sell');
    assert.equal(model.sideOf('out'), 'sell');
    assert.equal(model.sideOf('hold'), '');
    assert.equal(model.sideOf(null), '');
  });
});

describe('normalizeRecord field compatibility', () => {
  test('keeps the modern field names and preserves extra properties', () => {
    const record = buyRecord({ orderNumber: 7, priceField: 'open', orderId: 'o-1' });
    const normalized = model.normalizeRecord(record);
    assert.equal(normalized.side, 'buy');
    assert.equal(normalized.index, 1);
    assert.equal(normalized.timestamp, 1000000);
    assert.equal(normalized.price, 10);
    assert.equal(normalized.amount, 100);
    assert.equal(normalized.quantity, 10);
    assert.equal(normalized.orderNumber, 7);
    assert.equal(normalized.priceField, 'open');
    assert.equal(normalized.orderId, 'o-1');
  });

  test('falls back to legacy field names (action/type/direction, dataIndex/barIndex, time/date, executionPrice/fillPrice, value/cost, shares/qty/remainingQuantity, orderNo, field/element)', () => {
    const normalized = model.normalizeRecord({
      action: 'b',
      dataIndex: 3,
      time: 1600000000,
      executionPrice: 10.5,
      value: 105,
      shares: 10,
      orderNo: '4',
      field: 'high',
    });
    assert.equal(normalized.side, 'buy');
    assert.equal(normalized.index, 3);
    assert.equal(normalized.timestamp, 1600000000000);
    assert.equal(normalized.price, 10.5);
    assert.equal(normalized.amount, 105);
    assert.equal(normalized.quantity, 10);
    assert.equal(normalized.orderNumber, 4);
    assert.equal(normalized.priceField, 'high');

    const second = model.normalizeRecord({
      type: 'sell', barIndex: 8, date: '20260101', fillPrice: 9, cost: 90, qty: 9, element: 'low',
    });
    assert.equal(second.side, 'sell');
    assert.equal(second.index, 8);
    assert.equal(second.quantity, 9);
    assert.equal(second.priceField, 'low');

    const third = model.normalizeRecord({ direction: 'long', remainingQuantity: 12 });
    assert.equal(third.side, 'buy');
    assert.equal(third.quantity, 12);
  });

  test('derives buy quantity from amount/price when quantity is absent', () => {
    const normalized = model.normalizeRecord({ side: 'buy', price: 20, amount: 100 });
    assert.equal(normalized.quantity, 5);
    const notBuy = model.normalizeRecord({ side: 'sell', price: 20, amount: 100 });
    assert.equal(notBuy.quantity, null);
  });

  test('defaults priceField to close and returns null for invalid records', () => {
    assert.equal(model.normalizeRecord(null), null);
    assert.equal(model.normalizeRecord('buy'), null);
    assert.equal(model.normalizeRecord({}), null);
    assert.equal(model.normalizeRecord({ side: 'hold', price: 10 }), null);
    assert.equal(model.normalizeRecord({ side: 'buy', price: 10 }).priceField, 'close');
  });
});

describe('recordIdentity and dedup', () => {
  test('prefers id and falls back to the normalized field tuple', () => {
    assert.equal(model.recordIdentity({ id: 'abc' }), 'id:abc');
    assert.equal(model.recordIdentity({ id: '0' }), 'id:0');
    assert.equal(model.recordIdentity(buyRecord()), [
      'buy', 1000000, 1, 10, 100, 10,
    ].join('|'));
    assert.equal(model.recordIdentity(null), '');
  });

  test('appendUniqueRecord skips duplicates and returns whether it appended', () => {
    const records = [];
    const seen = Object.create(null);
    assert.equal(model.appendUniqueRecord(records, seen, buyRecord()), true);
    assert.equal(model.appendUniqueRecord(records, seen, buyRecord()), false);
    assert.equal(model.appendUniqueRecord(records, seen, buyRecord({ id: 'x' })), true);
    assert.equal(model.appendUniqueRecord(records, seen, { side: 'hold' }), false);
    assert.equal(records.length, 2);
  });
});

describe('assignOrderNumbers', () => {
  test('numbers buys in order, honors explicit order numbers and skips sells', () => {
    const records = [
      model.normalizeRecord(buyRecord({ timestamp: 100, orderNumber: 5 })),
      model.normalizeRecord({ side: 'sell', timestamp: 200, price: 12, amount: 120, quantity: 10 }),
      model.normalizeRecord(buyRecord({ timestamp: 300, orderNumber: 2 })),
      model.normalizeRecord(buyRecord({ timestamp: 400 })),
    ];
    const result = model.assignOrderNumbers(records);
    assert.equal(result, records);
    assert.deepEqual(records.map((record) => record.orderNumber), [5, null, 2, 6]);
  });

  test('returns the input array untouched for empty input', () => {
    assert.deepEqual(model.assignOrderNumbers([]), []);
    assert.equal(model.assignOrderNumbers(null), null);
  });
});

describe('positionAggregate', () => {
  test('aggregates lots with weighted entry price and market value', () => {
    const result = model.positionAggregate({
      lots: [
        { remainingQuantity: 100, entryPrice: 10, investedAmount: 1000 },
        { quantity: 50, price: 12, amount: 650 },
      ],
      currentPrice: 11,
    });
    assert.equal(result.quantity, 150);
    assert.equal(result.investedAmount, 1650);
    assert.equal(result.weightedEntryPrice, 11);
    assert.equal(result.lotCount, 2);
    assert.equal(result.currentPrice, 11);
    assert.equal(result.marketValue, 1650);
  });

  test('accepts legacy lot fields (shares/qty, cost/amount) and skips invalid lots', () => {
    const result = model.positionAggregate({
      lots: [
        { shares: 20, price: 5, cost: 110 },
        { qty: 30, entryPrice: 6, amount: 180 },
        { quantity: -5, price: 1 },
        { quantity: 0, price: 1 },
        { quantity: 10, price: null },
      ],
      marketPrice: 7,
    });
    assert.equal(result.quantity, 50);
    assert.equal(result.investedAmount, 290);
    assert.equal(result.weightedEntryPrice, 5.8);
    assert.equal(result.lotCount, 2);
    assert.equal(result.marketValue, 350);
  });

  test('flattens an array of positions into one lot set', () => {
    const result = model.positionAggregate([
      { lots: [{ quantity: 10, entryPrice: 10, amount: 100 }] },
      { lots: [{ quantity: 10, entryPrice: 12, amount: 120 }] },
    ]);
    assert.equal(result.quantity, 20);
    assert.equal(result.investedAmount, 220);
    assert.equal(result.weightedEntryPrice, 11);
    assert.equal(result.lotCount, 2);
  });

  test('falls back to single-position fields and derives cost from weightedEntryPrice', () => {
    const result = model.positionAggregate({
      remainingQuantity: 200,
      entryPrice: 9.5,
      investedAmount: 1900,
      lotsCount: 3,
      currentPrice: 10,
    });
    assert.equal(result.quantity, 200);
    assert.equal(result.investedAmount, 1900);
    assert.equal(result.weightedEntryPrice, 9.5);
    assert.equal(result.lotCount, 3);
    assert.equal(result.marketValue, 2000);

    const derived = model.positionAggregate({ quantity: 100, weightedEntryPrice: 10, marketValue: 1100, lotCount: 2 });
    assert.equal(derived.investedAmount, 1000);
    assert.equal(derived.weightedEntryPrice, 10);
    assert.equal(derived.lotCount, 2);
    assert.equal(derived.marketValue, 1100);
  });

  test('returns null for empty, invalid or non-positive positions', () => {
    assert.equal(model.positionAggregate(null), null);
    assert.equal(model.positionAggregate(undefined), null);
    assert.equal(model.positionAggregate({}), null);
    assert.equal(model.positionAggregate([]), null);
    assert.equal(model.positionAggregate({ lots: [] }), null);
    assert.equal(model.positionAggregate({ quantity: 0, entryPrice: 10 }), null);
    assert.equal(model.positionAggregate({ quantity: 10 }), null);
  });
});

describe('flattenTradeState field compatibility', () => {
  test('merges executions and records, deduplicating by id and by normalized identity', () => {
    const state = model.flattenTradeState({
      executions: [
        buyRecord({ id: 'e1', orderNumber: 1 }),
        buyRecord({ timestamp: 2000, index: 2, price: 11, amount: 110, quantity: 10 }),
      ],
      records: [
        { id: 'e1', action: 'buy', time: 1, dataIndex: 1, executionPrice: 10, value: 100, shares: 10 },
        { side: 'buy', timestamp: 3000, index: 3, price: 12, amount: 120, quantity: 10 },
      ],
    });
    assert.equal(state.records.length, 3);
    assert.equal(state.records[0].orderNumber, 1);
    assert.equal(state.records[0].priceField, 'close');
  });

  test('sorts by timestamp then index, with null timestamps and indexes last', () => {
    const state = model.flattenTradeState({
      records: [
        buyRecord({ timestamp: 3000, index: 2, quantity: 10 }),
        { side: 'buy', timestamp: 1000, index: 1, price: 10, amount: 100, quantity: 10 },
        { side: 'buy', timestamp: 1000, index: 0, price: 9, amount: 90, quantity: 10 },
        { side: 'buy', price: 8, amount: 80, quantity: 10 },
      ],
    });
    assert.deepEqual(
      state.records.map((record) => [record.timestamp, record.index]),
      [[1000000, 0], [1000000, 1], [3000000, 2], [null, null]],
    );
  });

  test('falls back to transactions when neither executions nor records exist', () => {
    const state = model.flattenTradeState({
      transactions: [
        { side: 'buy', timestamp: 1000, index: 0, price: 10, amount: 100, quantity: 10 },
        { side: 'sell', timestamp: 2000, index: 1, price: 12, amount: 120, quantity: 10 },
      ],
    });
    assert.equal(state.records.length, 2);
    assert.equal(state.records[0].side, 'buy');
    assert.equal(state.records[1].side, 'sell');
  });

  test('uses completedTrades (and the trades alias) only when no execution records exist', () => {
    const state = model.flattenTradeState({
      completedTrades: [
        {
          entry: { timestamp: 1000, index: 0, price: 10, amount: 100, quantity: 10 },
          exit: { timestamp: 2000, index: 1, price: 12, amount: 120, quantity: 10 },
        },
        { entry: null, exit: null },
      ],
    });
    assert.deepEqual(state.records.map((record) => record.side), ['buy', 'sell']);
    assert.equal(state.records[0].timestamp, 1000000);
    assert.equal(state.records[0].orderNumber, 1);
    assert.equal(state.records[1].orderNumber, null);
    assert.equal(state.completedTrades.length, 2);

    const alias = model.flattenTradeState({
      trades: [
        {
          entry: { timestamp: 1000, index: 0, price: 10, amount: 100, quantity: 10 },
          exit: { timestamp: 2000, index: 1, price: 12, amount: 120, quantity: 10 },
        },
      ],
    });
    assert.equal(alias.records.length, 2);
    assert.equal(alias.completedTrades.length, 1);

    const withExecutions = model.flattenTradeState({
      executions: [buyRecord()],
      completedTrades: [
        {
          entry: { timestamp: 500, index: 0, price: 5, amount: 50, quantity: 10 },
          exit: { timestamp: 600, index: 1, price: 6, amount: 60, quantity: 10 },
        },
      ],
    });
    assert.equal(withExecutions.records.length, 1);
  });

  test('falls back to lastExecution and to the single open position record', () => {
    const last = model.flattenTradeState({ lastExecution: buyRecord() });
    assert.equal(last.records.length, 1);
    assert.equal(last.records[0].side, 'buy');

    const position = model.flattenTradeState({
      position: { quantity: 10, price: 10, amount: 100, timestamp: 1000 },
    });
    assert.equal(position.records.length, 1);
    assert.equal(position.records[0].side, 'buy');
    assert.equal(position.records[0].quantity, 10);
    assert.equal(position.records[0].timestamp, 1000000);
  });

  test('builds positionSummary, openLots, presets, bracketOrders and settlement from aliases', () => {
    const settlementTrade = { entry: { timestamp: 1000 }, exit: { timestamp: 2000 } };
    const rawPosition = {
      lots: [{ quantity: 100, entryPrice: 10, amount: 1000 }, { quantity: 100, entryPrice: 12, amount: 1200 }],
      currentPrice: 11,
    };
    const state = model.flattenTradeState({
      position: rawPosition,
      pendingOrders: [{ id: 'p1' }],
      bracketOrders: [{ id: 'b1' }],
      trades: [settlementTrade],
    });
    assert.equal(state.positionSummary.quantity, 200);
    assert.equal(state.positionSummary.investedAmount, 2200);
    assert.equal(state.positionSummary.weightedEntryPrice, 11);
    assert.equal(state.positionSummary.marketValue, 2200);
    assert.equal(state.positionSummary.lotCount, 2);
    assert.equal(state.position, rawPosition);
    assert.deepEqual(state.openLots, rawPosition.lots);
    assert.notEqual(state.openLots, rawPosition.lots);
    assert.deepEqual(state.presets, [{ id: 'p1' }]);
    assert.deepEqual(state.bracketOrders, [{ id: 'b1' }]);
    assert.equal(state.settlement, settlementTrade);
    assert.equal(state.position, state.position);
  });

  test('uses currentPosition for state.position and aggregates raw.position', () => {
    const current = { quantity: 5, price: 20, amount: 100, lots: [] };
    const state = model.flattenTradeState({
      currentPosition: current,
      position: [{ quantity: 99, price: 1, amount: 99 }],
    });
    assert.equal(state.position, current);
    assert.equal(state.positionSummary.quantity, 99);
  });

  test('computes pnl from holdingPerformance, position performance, legacy objects and top-level aliases', () => {
    const fromPerformance = model.flattenTradeState({
      holdingPerformance: { percent: 2.5, amount: 25 },
      realized: { percent: 3, amount: 30 },
    });
    assert.deepEqual(fromPerformance.pnl, {
      unrealizedPct: 2.5, unrealizedAmount: 25, realizedPct: 3, realizedAmount: 30,
    });

    const fromPosition = model.flattenTradeState({
      currentPosition: { performance: { percent: '1.5', amount: '15' }, lots: [] },
      realizedPnl: { percent: '4', amount: '40' },
    });
    assert.deepEqual(fromPosition.pnl, {
      unrealizedPct: 1.5, unrealizedAmount: 15, realizedPct: 4, realizedAmount: 40,
    });

    const fromTopLevel = model.flattenTradeState({
      unrealizedPnl: { percent: '2.5' },
      unrealizedPct: '2.5',
      unrealizedAmount: '25',
      floatingPct: 99,
      realizedPct: 3,
      realizedAmount: 30,
    });
    assert.deepEqual(fromTopLevel.pnl, {
      unrealizedPct: 2.5, unrealizedAmount: 25, realizedPct: 3, realizedAmount: 30,
    });

    const fromFloating = model.flattenTradeState({ floatingPnl: { percent: 1, amount: 10 } });
    assert.deepEqual(fromFloating.pnl, {
      unrealizedPct: 1, unrealizedAmount: 10, realizedPct: null, realizedAmount: null,
    });

    const fromTopLevelFloatingPct = model.flattenTradeState({
      unrealizedPnl: {},
      floatingPct: 99,
      floatingPnl: { percent: 1, amount: 1 },
    });
    assert.equal(fromTopLevelFloatingPct.pnl.unrealizedPct, 99);
  });
});

describe('flattenTradeState empty input', () => {
  test('returns the minimal empty shape for falsy or non-object input', () => {
    assert.deepEqual(model.flattenTradeState(null), { records: [], presets: {}, pnl: {} });
    assert.deepEqual(model.flattenTradeState(undefined), { records: [], presets: {}, pnl: {} });
    assert.deepEqual(model.flattenTradeState(42), { records: [], presets: {}, pnl: {} });
  });

  test('returns a fully normalized empty state for an empty object', () => {
    const state = model.flattenTradeState({});
    assert.deepEqual(state.records, []);
    assert.deepEqual(state.presets, {});
    assert.equal(state.position, null);
    assert.equal(state.positionSummary, null);
    assert.deepEqual(state.openLots, []);
    assert.equal(state.bracketOrders, null);
    assert.deepEqual(state.completedTrades, []);
    assert.equal(state.settlement, null);
    assert.deepEqual(state.pnl, {
      unrealizedPct: null, unrealizedAmount: null, realizedPct: null, realizedAmount: null,
    });
  });
});
