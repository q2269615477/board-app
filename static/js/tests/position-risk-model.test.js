const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const MODEL_FILE = path.resolve(__dirname, '..', 'position-risk-model.js');
const model = require(MODEL_FILE);

function input(overrides = {}) {
  return {
    direction: 'long',
    entry: 100,
    target: 120,
    stop: 90,
    accountSize: 10000,
    risk: { amount: 100 },
    lotSize: 1,
    leverage: 2,
    pointValue: 1,
    qtyPrecision: 2,
    ...overrides,
  };
}

function assertFiniteFields(result) {
  for (const key of [
    'targetPct', 'stopPct', 'profitLossRatio', 'riskReward', 'qtyRisk', 'qtyLeverage', 'qty',
    'profitPnl', 'lossPnl', 'targetBalance', 'stopBalance',
  ]) {
    assert.equal(Number.isFinite(result[key]), true, `${key} must be finite`);
  }
}

function assertClose(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} is not close to ${expected}`);
}

describe('PositionRiskModel UMD/CommonJS contract', () => {
  test('exports the pure calculation helpers without requiring a DOM', () => {
    assert.equal(typeof model.calculatePositionRisk, 'function');
    assert.equal(typeof model.calculatePosition, 'function');
    assert.equal(typeof model.calculate, 'function');
    assert.equal(typeof model.normalizePositionPoints, 'function');
    assert.equal(typeof model.reversePosition, 'function');
  });
});

describe('long position formulas', () => {
  test('calculates TradingView long values from amount risk', () => {
    const result = model.calculatePositionRisk(input());
    assert.equal(result.ok, true);
    assert.equal(result.direction, 'long');
    assert.equal(result.targetPct, 20);
    assert.equal(result.stopPct, 10);
    assert.equal(result.profitLossRatio, 2);
    assert.equal(result.riskReward, 2);
    assert.equal(result.riskReward, result.profitLossRatio);
    assert.equal(result.qtyRisk, 10);
    assert.equal(result.qtyLeverage, 200);
    assert.equal(result.qty, 10);
    assert.equal(result.profitPnl, 200);
    assert.equal(result.lossPnl, -100);
    assert.equal(result.targetBalance, 10200);
    assert.equal(result.stopBalance, 9900);
    assertFiniteFields(result);
  });

  test('uses the rounded quantity for PnL and balance values', () => {
    const result = model.calculatePositionRisk(input({
      entry: 100,
      target: 110,
      stop: 97,
      risk: { amount: 100 },
      leverage: 100,
      qtyPrecision: 2,
    }));
    assert.equal(result.qtyRisk, 100 / 3);
    assert.equal(result.qty, 33.33);
    assertClose(result.profitPnl, 333.3);
    assertClose(result.lossPnl, -99.99);
    assertClose(result.profitLossRatio, Math.abs(result.profitPnl) / Math.abs(result.lossPnl));
    assert.equal(result.riskReward, result.profitLossRatio);
    assertClose(result.stopBalance, 9900.01);
  });
});

describe('short position formulas', () => {
  test('calculates TradingView short values from percent risk', () => {
    const result = model.calculatePositionRisk(input({
      direction: 'short',
      entry: 100,
      target: 80,
      stop: 110,
      risk: { percent: 1 },
    }));
    assert.equal(result.ok, true);
    assert.equal(result.direction, 'short');
    assert.equal(result.riskMode, 'percent');
    assert.equal(result.riskSize, 100);
    assert.equal(result.targetPct, 20);
    assert.equal(result.stopPct, 10);
    assert.equal(result.profitLossRatio, 2);
    assert.equal(result.riskReward, 2);
    assert.equal(result.riskReward, result.profitLossRatio);
    assert.equal(result.qtyRisk, 10);
    assert.equal(result.qtyLeverage, 200);
    assert.equal(result.qty, 10);
    assert.equal(result.profitPnl, 200);
    assert.equal(result.lossPnl, -100);
    assert.equal(result.targetBalance, 10200);
    assert.equal(result.stopBalance, 9900);
    assertFiniteFields(result);
  });

  test('applies point value and lot size to risk, leverage, and PnL', () => {
    const result = model.calculatePositionRisk(input({
      entry: 50,
      target: 60,
      stop: 45,
      accountSize: 5000,
      risk: { amount: 100 },
      lotSize: 10,
      leverage: 2,
      pointValue: 5,
      qtyPrecision: 3,
    }));
    assert.equal(result.qtyRisk, 0.4);
    assert.equal(result.qtyLeverage, 100);
    assert.equal(result.qty, 0.4);
    assert.equal(result.profitPnl, 200);
    assert.equal(result.lossPnl, -100);
  });
});

describe('investment amount sizing', () => {
  test('sizes quantity from investment amount and calculates PnL from rounded quantity', () => {
    for (const investmentAmount of [100000, 1000000]) {
      const result = model.calculatePositionRisk(input({
        entry: 123.45,
        target: 130,
        stop: 120,
        accountSize: 2000000,
        risk: { amount: 1 },
        investmentAmount,
        lotSize: 10,
        pointValue: 2,
        leverage: 1,
        qtyPrecision: 2,
      }));
      const rawQty = investmentAmount / (123.45 * 2 * 10);
      const expectedQty = Number(rawQty.toFixed(2));
      const multiplier = expectedQty * 2 * 10;
      const expectedProfit = (130 - 123.45) * multiplier;
      const expectedLoss = (120 - 123.45) * multiplier;

      assert.equal(result.ok, true);
      assert.equal(result.sizingMode, 'investment');
      assert.equal(result.investmentAmount, investmentAmount);
      assertClose(result.qtyInvestment, rawQty);
      assert.equal(result.qty, expectedQty);
      assertClose(result.profitPnl, expectedProfit);
      assertClose(result.lossPnl, expectedLoss);
      assertClose(result.profitLossRatio, Math.abs(expectedProfit) / Math.abs(expectedLoss));
      assert.equal(result.riskReward, result.profitLossRatio);
    }
  });

  test('uses the investment amount with short positions as well', () => {
    const result = model.calculatePositionRisk(input({
      direction: 'short',
      entry: 200,
      target: 180,
      stop: 210,
      accountSize: 2000000,
      investmentAmount: 100000,
      lotSize: 5,
      pointValue: 2,
      leverage: 1,
      qtyPrecision: 1,
    }));
    const expectedQty = Number((100000 / (200 * 2 * 5)).toFixed(1));
    const multiplier = expectedQty * 2 * 5;

    assert.equal(result.ok, true);
    assert.equal(result.qty, expectedQty);
    assertClose(result.profitPnl, (200 - 180) * multiplier);
    assertClose(result.lossPnl, (200 - 210) * multiplier);
    assertClose(result.profitLossRatio, Math.abs(result.profitPnl) / Math.abs(result.lossPnl));
  });
});

describe('contract: default investment amount and price passthrough', () => {
  test('exposes entry, target, and stop prices for summary display', () => {
    const result = model.calculatePositionRisk(input());
    assert.equal(result.ok, true);
    assert.equal(result.entry, 100);
    assert.equal(result.target, 120);
    assert.equal(result.stop, 90);
  });

  test('long 10000 default investment uses investment sizing while short without investment stays risk-sized', () => {
    const long = model.calculatePositionRisk(input({
      direction: 'long',
      entry: 100,
      target: 120,
      stop: 90,
      accountSize: 100000,
      riskMode: 'percent',
      risk: 1,
      investmentAmount: 10000,
      leverage: 1,
      pointValue: 1,
      lotSize: 1,
      qtyPrecision: 0,
    }));
    assert.equal(long.ok, true);
    assert.equal(long.sizingMode, 'investment');
    assert.equal(long.investmentAmount, 10000);
    assert.equal(long.qty, 100);
    assert.equal(long.profitPnl, 2000);
    assert.equal(long.lossPnl, -1000);

    const short = model.calculatePositionRisk(input({
      direction: 'short',
      entry: 100,
      target: 80,
      stop: 110,
      accountSize: 100000,
      riskMode: 'percent',
      risk: 1,
      investmentAmount: null,
      qtyPrecision: 0,
    }));
    assert.equal(short.ok, true);
    assert.equal(short.sizingMode, 'risk');
    assert.equal(short.investmentAmount, null);
    assert.equal(short.qty, 100);
    assert.equal(short.profitPnl, 2000);
    assert.equal(short.lossPnl, -1000);
  });
});

describe('risk input and validation', () => {
  test('accepts numeric risk with an explicit amount or percent mode', () => {
    const amount = model.calculatePositionRisk(input({ risk: 100, riskMode: 'amount' }));
    const percent = model.calculatePositionRisk(input({ risk: 1, riskMode: 'percent' }));
    assert.equal(amount.riskSize, 100);
    assert.equal(percent.riskSize, 100);
  });

  test('returns structured errors for invalid direction and price order', () => {
    const direction = model.calculatePositionRisk(input({ direction: 'sideways' }));
    const order = model.calculatePositionRisk(input({ target: 90 }));
    const shortOrder = model.calculatePositionRisk(input({ direction: 'short', target: 110 }));
    assert.deepEqual(direction.error, {
      code: 'INVALID_DIRECTION',
      message: 'direction must be long or short',
      field: 'direction',
    });
    assert.equal(order.error.code, 'INVALID_PRICE_ORDER');
    assert.equal(shortOrder.error.code, 'INVALID_PRICE_ORDER');
  });

  test('returns structured errors instead of NaN or Infinity', () => {
    const cases = [
      input({ entry: 0 }),
      input({ accountSize: 0 }),
      input({ risk: { amount: 0 } }),
      input({ lotSize: 0 }),
      input({ leverage: 0 }),
      input({ pointValue: 0 }),
      input({ stop: 100 }),
      input({ entry: Number.POSITIVE_INFINITY }),
      input({ qtyPrecision: 1.5 }),
      input({ risk: { amount: Number.NaN } }),
    ];
    for (const value of cases) {
      const result = model.calculatePositionRisk(value);
      assert.equal(result.ok, false);
      assert.equal(typeof result.error.code, 'string');
      assert.equal(Object.values(result).some((item) => typeof item === 'number' && !Number.isFinite(item)), false);
    }
  });
});

describe('point normalization and reversal', () => {
  test('normalizes valid points and rejects reversed points', () => {
    assert.deepEqual(model.normalizePositionPoints('long', '100', '120', '90'), {
      ok: true,
      direction: 'long',
      entry: 100,
      target: 120,
      stop: 90,
    });
    assert.equal(model.normalizePositionPoints('short', 100, 80, 110).ok, true);
    assert.equal(model.normalizePositionPoints('long', 100, 90, 120).error.code, 'INVALID_PRICE_ORDER');
  });

  test('reverses a position without mutating the input', () => {
    const original = input({ direction: 'long', target: 120, stop: 90 });
    const snapshot = JSON.parse(JSON.stringify(original));
    const result = model.reversePosition(original);
    assert.equal(result.ok, true);
    assert.equal(result.direction, 'short');
    assert.equal(result.entry, 100);
    assert.equal(result.target, 90);
    assert.equal(result.stop, 120);
    assert.deepEqual(result.value, {
      ...snapshot,
      direction: 'short',
      target: 90,
      stop: 120,
    });
    assert.deepEqual(original, snapshot);
  });

  test('supports reversing only a direction and positional arguments', () => {
    assert.equal(model.reversePosition('long'), 'short');
    const result = model.reversePosition('short', 100, 80, 110);
    assert.equal(result.ok, true);
    assert.equal(result.direction, 'long');
    assert.equal(result.target, 110);
    assert.equal(result.stop, 80);
  });
});
