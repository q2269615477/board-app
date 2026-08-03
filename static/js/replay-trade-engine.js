(function (global) {
  'use strict';

  /*
   * Pure in-memory trading state for bar replay.  The chart and replay UI are
   * intentionally not dependencies of this module; they communicate through
   * the small event bridge at the bottom of the file.
   */
  var FIELDS = ['open', 'high', 'low', 'close'];
  var SIDES = ['buy', 'sell', 'takeProfit', 'stopLoss'];
  var BRACKET_SIDES = ['takeProfit', 'stopLoss'];
  var DEFAULT_AMOUNT = 10000;
  var EPSILON = 1e-9;
  var subscribers = [];
  var tradeSequence = 0;
  var state = makeState({});

  function clone(value) {
    if (value === null || value === undefined) return value;
    if (Array.isArray(value)) {
      var array = value.map(clone);
      /* Keep named compatibility properties on the new bracket list. */
      Object.keys(value).forEach(function (key) {
        if (!/^\d+$/.test(key) && key !== 'length') array[key] = clone(value[key]);
      });
      return array;
    }
    if (typeof value !== 'object') return value;
    var copy = {};
    Object.keys(value).forEach(function (key) { copy[key] = clone(value[key]); });
    return copy;
  }

  function number(value) {
    var result = Number(value);
    return isFinite(result) ? result : null;
  }

  function positive(value) {
    var result = number(value);
    return result !== null && result > 0 ? result : null;
  }

  function nonNegativeInteger(value) {
    var result = number(value);
    if (result === null || result < 0 || Math.floor(result) !== result) return null;
    return result;
  }

  function timestamp(value) {
    if (value === null || value === undefined || value === '') return null;
    var result = number(value);
    if (result !== null) return result < 10000000000 ? result * 1000 : result;
    if (typeof value === 'string') {
      var parsed = Date.parse(value);
      return isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function fieldName(value) {
    var field = String(value === null || value === undefined ? '' : value).toLowerCase();
    return FIELDS.indexOf(field) >= 0 ? field : null;
  }

  function sideName(value) {
    var raw = String(value === null || value === undefined ? '' : value).trim();
    var side = raw.toLowerCase().replace(/[\s_-]+/g, '');
    if (side === 'buy') return 'buy';
    if (side === 'sell') return 'sell';
    if (side === 'takeprofit' || side === 'tp') return 'takeProfit';
    if (side === 'stoploss' || side === 'sl') return 'stopLoss';
    return null;
  }

  function makeState(options) {
    options = options || {};
    var configuredAmount = positive(
      options.defaultAmount !== undefined ? options.defaultAmount : options.amount
    );
    return {
      status: 'flat',
      cursor: -1,
      cursorTimestamp: null,
      cursorBar: null,
      position: null,
      pendingOrders: { buy: null, sell: null, takeProfit: null, stopLoss: null },
      bracketOrders: [],
      nextOrderNumber: 1,
      nextBracketNumber: 1,
      realizedAmount: 0,
      realizedCost: 0,
      unrealizedAmount: 0,
      unrealizedPercent: 0,
      holdingPerformance: null,
      completedTrades: [],
      executions: [],
      lastExecution: null,
      lastSettlement: null,
      lastBar: null,
      lastError: null,
      lastBuyBarIndex: null,
      lastSellBarIndex: null,
      defaultAmount: configuredAmount || DEFAULT_AMOUNT
    };
  }

  function pnlPercent(amount, cost) {
    return cost > 0 ? (amount / cost) * 100 : 0;
  }

  function setCursor(bar, index) {
    var resolvedIndex = nonNegativeInteger(index);
    if (resolvedIndex === null) return false;
    state.cursor = resolvedIndex;
    state.cursorTimestamp = timestamp(bar && (bar.timestamp !== undefined ? bar.timestamp : bar.time));
    state.cursorBar = bar ? clone(bar) : null;
    state.lastBar = bar ? clone(bar) : null;
    return true;
  }

  function normalizeBar(bar) {
    if (!bar || typeof bar !== 'object') return null;
    var copy = clone(bar);
    ['open', 'high', 'low', 'close'].forEach(function (key) {
      var value = number(bar[key]);
      copy[key] = value;
    });
    copy.timestamp = timestamp(bar.timestamp !== undefined ? bar.timestamp : bar.time);
    return copy;
  }

  function inRange(bar, target) {
    var low = number(bar && bar.low);
    var high = number(bar && bar.high);
    return low !== null && high !== null && low <= target && target <= high;
  }

  function bracketTrigger(order, bar) {
    if (!order || !bar) return null;
    var target = positive(order.price);
    var open = positive(bar.open);
    var high = positive(bar.high);
    var low = positive(bar.low);
    if (target === null || high === null || low === null) return null;
    if (order.side === 'stopLoss' && low <= target) {
      return { order: order, price: open !== null && open <= target ? open : target };
    }
    if (order.side === 'takeProfit' && high >= target) {
      return { order: order, price: open !== null && open >= target ? open : target };
    }
    return null;
  }

  function currentUnrealized() {
    if (!state.position) return { amount: 0, percent: 0 };
    var position = state.position;
    var price = positive(position.currentPrice);
    if (price === null) price = position.entryPrice;
    var amount = (position.lots || []).reduce(function (total, lot) {
      return total + (price - Number(lot.price || 0)) * Number(lot.quantity || 0);
    }, 0);
    return {
      amount: amount,
      percent: pnlPercent(amount, position.amount)
    };
  }

  function holdingPerformanceSnapshot(field) {
    if (!state.position) return null;
    var position = state.position;
    var currentPrice = positive(position.currentPrice);
    if (currentPrice === null) currentPrice = position.entryPrice;
    var pnl = currentUnrealized();
    return {
      currentPrice: currentPrice,
      marketValue: currentPrice * position.shares,
      investedAmount: position.amount,
      cost: position.cost,
      profitAmount: pnl.amount,
      profitPercent: pnl.percent,
      amount: pnl.amount,
      percent: pnl.percent,
      lots: clone(position.lots || []),
      barIndex: position.currentBarIndex,
      timestamp: position.currentTimestamp,
      field: field || 'close'
    };
  }

  function applyUnrealized(bar, index) {
    if (!state.position) {
      state.unrealizedAmount = 0;
      state.unrealizedPercent = 0;
      state.holdingPerformance = null;
      return;
    }
    var close = positive(bar && bar.close);
    if (close !== null) state.position.currentPrice = close;
    if (nonNegativeInteger(index) !== null) state.position.currentBarIndex = Number(index);
    var currentTimestamp = timestamp(bar && (bar.timestamp !== undefined ? bar.timestamp : bar.time));
    if (currentTimestamp !== null) state.position.currentTimestamp = currentTimestamp;
    state.position.lots = (state.position.lots || []).map(function (lot) {
      return lotSnapshot(lot, state.position.currentPrice);
    });
    var pnl = currentUnrealized();
    state.unrealizedAmount = pnl.amount;
    state.unrealizedPercent = pnl.percent;
    state.holdingPerformance = holdingPerformanceSnapshot('close');
    state.position.unrealizedPnl = clone(pnl);
    state.position.floatingPnl = clone(pnl);
    state.position.marketValue = state.position.currentPrice * state.position.shares;
  }

  function entrySnapshot(lot) {
    return {
      executionId: lot.executionId,
      orderNumber: lot.orderNumber,
      label: lot.label || (lot.orderNumber ? 'B' + lot.orderNumber : null),
      barIndex: lot.barIndex,
      timestamp: lot.timestamp,
      field: lot.field,
      price: lot.price,
      amount: lot.cost,
      cost: lot.cost,
      shares: lot.quantity,
      quantity: lot.quantity
    };
  }

  function lotSnapshot(lot, currentPrice) {
    var copy = clone(lot || {});
    var quantity = positive(copy.quantity !== undefined ? copy.quantity : copy.shares);
    if (quantity === null) quantity = 0;
    var price = positive(copy.price) || 0;
    var remainingCost = number(copy.cost);
    if (remainingCost === null) remainingCost = price * quantity;
    var investedAmount = number(copy.investedAmount);
    if (investedAmount === null) investedAmount = number(copy.originalAmount);
    if (investedAmount === null) investedAmount = remainingCost;
    var resolvedPrice = positive(currentPrice);
    if (resolvedPrice === null) resolvedPrice = price;
    var marketValue = resolvedPrice * quantity;
    var unrealizedAmount = marketValue - remainingCost;
    copy.quantity = quantity;
    copy.shares = quantity;
    copy.amount = remainingCost;
    copy.cost = remainingCost;
    copy.remainingQuantity = quantity;
    copy.remainingShares = quantity;
    copy.remainingAmount = remainingCost;
    copy.investedAmount = investedAmount;
    copy.currentPrice = resolvedPrice;
    copy.currentMarketValue = marketValue;
    copy.marketValue = marketValue;
    copy.unrealizedAmount = unrealizedAmount;
    copy.unrealizedPercent = pnlPercent(unrealizedAmount, remainingCost);
    copy.profitAmount = unrealizedAmount;
    copy.profitPercent = copy.unrealizedPercent;
    copy.label = copy.label || (copy.orderNumber ? 'B' + copy.orderNumber : null);
    return copy;
  }

  function positionSnapshot(lots, currentPrice, currentBarIndex, currentTimestamp) {
    var candidates = (lots || []).filter(function (lot) {
      return positive(lot && lot.quantity) !== null && Number(lot.quantity) > EPSILON;
    });
    var resolvedPositionPrice = positive(currentPrice);
    var openLots = candidates.map(function (lot) {
      return lotSnapshot(lot, resolvedPositionPrice);
    });
    var totalShares = openLots.reduce(function (total, lot) {
      return total + Number(lot.quantity);
    }, 0);
    var totalCost = openLots.reduce(function (total, lot) {
      return total + Number(lot.cost || 0);
    }, 0);
    var firstLot = openLots[0] || null;
    var averagePrice = totalShares > EPSILON ? totalCost / totalShares : 0;
    var resolvedPrice = positive(currentPrice);
    if (resolvedPrice === null) resolvedPrice = averagePrice;
    var position = {
      side: 'long',
      entry: firstLot ? entrySnapshot(firstLot) : null,
      barIndex: firstLot ? firstLot.barIndex : null,
      timestamp: firstLot ? firstLot.timestamp : null,
      field: firstLot ? firstLot.field : null,
      price: averagePrice,
      entryPrice: averagePrice,
      amount: totalCost,
      cost: totalCost,
      investedAmount: totalCost,
      shares: totalShares,
      quantity: totalShares,
      lots: clone(openLots),
      currentPrice: resolvedPrice,
      currentBarIndex: currentBarIndex,
      currentTimestamp: currentTimestamp,
      marketValue: resolvedPrice * totalShares,
      unrealizedPnl: { amount: 0, percent: 0 },
      floatingPnl: { amount: 0, percent: 0 },
      lotsByOrderNumber: openLots.reduce(function (result, lot) {
        if (lot.orderNumber !== null && lot.orderNumber !== undefined) {
          result[String(lot.orderNumber)] = clone(lot);
        }
        return result;
      }, {}),
      lastBuyBarIndex: openLots.reduce(function (latest, lot) {
        return latest === null || lot.barIndex > latest ? lot.barIndex : latest;
      }, null),
      lastBuyTimestamp: openLots.reduce(function (latest, lot) {
        return latest === null || (lot.timestamp !== null && lot.timestamp > latest) ? lot.timestamp : latest;
      }, null)
    };
    return position;
  }

  function updateHoldingPerformance() {
    if (!state.position) {
      state.unrealizedAmount = 0;
      state.unrealizedPercent = 0;
      state.holdingPerformance = null;
      return;
    }
    var pnl = currentUnrealized();
    state.unrealizedAmount = pnl.amount;
    state.unrealizedPercent = pnl.percent;
    state.holdingPerformance = holdingPerformanceSnapshot('close');
    state.position.unrealizedPnl = clone(pnl);
    state.position.floatingPnl = clone(pnl);
    state.position.marketValue = state.position.currentPrice * state.position.shares;
  }

  function currentOpenLots() {
    return state.position && Array.isArray(state.position.lots)
      ? state.position.lots.filter(function (lot) {
        return positive(lot && lot.quantity) !== null && Number(lot.quantity) > EPSILON;
      }) : [];
  }

  function uniqueStrings(values) {
    var result = [];
    (Array.isArray(values) ? values : [values]).forEach(function (value) {
      if (value === null || value === undefined || value === '') return;
      var normalized = String(value);
      if (result.indexOf(normalized) < 0) result.push(normalized);
    });
    return result;
  }

  function uniqueOrderNumbers(values) {
    var result = [];
    (Array.isArray(values) ? values : [values]).forEach(function (value) {
      var normalized = nonNegativeInteger(value);
      if (normalized === null || normalized === 0 || result.indexOf(normalized) >= 0) return;
      result.push(normalized);
    });
    return result;
  }

  function bindingValues(input, key, singularKey) {
    if (input[key] !== undefined) return input[key];
    if (singularKey && input[singularKey] !== undefined) return input[singularKey];
    return undefined;
  }

  function hasExplicitBracketBinding(input) {
    return input.executionIds !== undefined || input.executionId !== undefined ||
      input.orderNumbers !== undefined || input.orderNumber !== undefined;
  }

  function lotMatchesBracket(order, lot) {
    if (!order || !lot) return false;
    if (order.bindingMode === 'all') return true;
    var executionIds = order.executionIds || [];
    var orderNumbers = order.orderNumbers || [];
    return (lot.executionId !== undefined && executionIds.indexOf(String(lot.executionId)) >= 0) ||
      (lot.orderNumber !== undefined && orderNumbers.indexOf(Number(lot.orderNumber)) >= 0);
  }

  function resolveBracketLots(order) {
    return currentOpenLots().filter(function (lot) {
      return lotMatchesBracket(order, lot);
    });
  }

  function makeBracketOrder(side, input) {
    var lots = currentOpenLots();
    var explicit = hasExplicitBracketBinding(input);
    var requestedExecutionIds = uniqueStrings(bindingValues(input, 'executionIds', 'executionId'));
    var requestedOrderNumbers = uniqueOrderNumbers(bindingValues(input, 'orderNumbers', 'orderNumber'));
    var selected = explicit ? lots.filter(function (lot) {
      return (lot.executionId !== undefined && requestedExecutionIds.indexOf(String(lot.executionId)) >= 0) ||
        (lot.orderNumber !== undefined && requestedOrderNumbers.indexOf(Number(lot.orderNumber)) >= 0);
    }) : lots;
    if (!explicit) {
      requestedExecutionIds = selected.map(function (lot) { return String(lot.executionId); });
      requestedOrderNumbers = selected.map(function (lot) { return Number(lot.orderNumber); });
    } else if (!requestedExecutionIds.length) {
      requestedExecutionIds = selected.map(function (lot) { return String(lot.executionId); });
    } else if (!requestedOrderNumbers.length) {
      requestedOrderNumbers = selected.map(function (lot) { return Number(lot.orderNumber); });
    }
    return {
      id: input.id || input.bracketId || ('bracket-' + (state.nextBracketNumber++)),
      side: side,
      type: side,
      role: side,
      price: positive(input.price),
      amount: input.amount === undefined || input.amount === null || input.amount === ''
        ? null : positive(input.amount),
      quantity: input.quantity === undefined || input.quantity === null || input.quantity === ''
        ? null : positive(input.quantity),
      targetPrice: positive(input.price),
      trigger: input.trigger || 'range',
      bindingMode: explicit ? 'selected' : 'all',
      executionIds: requestedExecutionIds,
      orderNumbers: requestedOrderNumbers
    };
  }

  function bracketSnapshot(order) {
    if (!order) return null;
    var copy = clone(order);
    var lots = resolveBracketLots(order);
    copy.openExecutionIds = lots.map(function (lot) { return String(lot.executionId); });
    copy.openOrderNumbers = lots.map(function (lot) { return Number(lot.orderNumber); });
    if (order.bindingMode === 'all') {
      copy.executionIds = copy.openExecutionIds.slice();
      copy.orderNumbers = copy.openOrderNumbers.slice();
    }
    copy.boundExecutionIds = copy.openExecutionIds.slice();
    copy.boundOrderNumbers = copy.openOrderNumbers.slice();
    copy.boundQuantity = lots.reduce(function (total, lot) {
      return total + Number(lot.quantity || 0);
    }, 0);
    copy.lotCount = lots.length;
    var plannedQuantity = copy.quantity === null || copy.quantity === undefined
      ? copy.boundQuantity : Math.min(Number(copy.quantity), copy.boundQuantity);
    var plannedCost = lots.reduce(function (total, lot) {
      return total + Number(lot.price || 0) * Number(lot.quantity || 0);
    }, 0);
    if (plannedQuantity > EPSILON && plannedCost > EPSILON && plannedQuantity < copy.boundQuantity) {
      plannedCost = plannedCost * plannedQuantity / copy.boundQuantity;
    }
    var expectedAmount = 0;
    if (plannedQuantity > EPSILON && copy.price !== null && copy.price !== undefined) {
      var plannedEntryPrice = plannedQuantity > EPSILON ? plannedCost / plannedQuantity : 0;
      expectedAmount = (Number(copy.price) - plannedEntryPrice) * plannedQuantity;
      if (copy.side === 'stopLoss') expectedAmount = Math.min(0, expectedAmount);
      if (copy.side === 'takeProfit') expectedAmount = Math.max(0, expectedAmount);
    }
    copy.expectedAmount = expectedAmount;
    copy.expectedPnlAmount = expectedAmount;
    copy.expectedProfitAmount = copy.side === 'takeProfit' ? expectedAmount : 0;
    copy.expectedLossAmount = copy.side === 'stopLoss' ? expectedAmount : 0;
    copy.expectedPercent = pnlPercent(expectedAmount, plannedCost);
    return copy;
  }

  function latestBracket(side) {
    for (var i = state.bracketOrders.length - 1; i >= 0; i -= 1) {
      if (state.bracketOrders[i].side === side) return state.bracketOrders[i];
    }
    return null;
  }

  function bracketListSnapshot() {
    var list = state.bracketOrders.map(bracketSnapshot);
    BRACKET_SIDES.forEach(function (side) {
      list[side] = bracketSnapshot(latestBracket(side));
    });
    return list;
  }

  function pendingSnapshot() {
    var pending = clone(state.pendingOrders);
    BRACKET_SIDES.forEach(function (side) {
      pending[side] = bracketSnapshot(latestBracket(side));
    });
    return pending;
  }

  function syncPendingBracketViews() {
    BRACKET_SIDES.forEach(function (side) {
      state.pendingOrders[side] = clone(latestBracket(side));
    });
  }

  function stateSnapshot() {
    var realized = {
      amount: state.realizedAmount,
      percent: pnlPercent(state.realizedAmount, state.realizedCost)
    };
    var unrealized = {
      amount: state.unrealizedAmount,
      percent: state.unrealizedPercent
    };
    var pending = pendingSnapshot();
    var brackets = bracketListSnapshot();
    return {
      status: state.position ? 'holding' : 'flat',
      holding: !!state.position,
      cursor: state.cursor,
      cursorIndex: state.cursor,
      cursorTimestamp: state.cursorTimestamp,
      cursorBar: clone(state.cursorBar),
      position: clone(state.position),
      currentPosition: clone(state.position),
      pending: pending,
      pendingOrders: clone(pending),
      bracketOrders: brackets,
      lots: clone(state.position ? state.position.lots : []),
      openLots: clone(state.position ? state.position.lots : []),
      orders: clone(pending),
      realizedPnl: clone(realized),
      realized: clone(realized),
      unrealizedPnl: clone(unrealized),
      unrealized: clone(unrealized),
      floatingPnl: clone(unrealized),
      holdingPerformance: clone(state.holdingPerformance),
      completedTrades: clone(state.completedTrades),
      executions: clone(state.executions),
      records: clone(state.executions),
      trades: clone(state.completedTrades),
      lastExecution: clone(state.lastExecution),
      lastSettlement: clone(state.lastSettlement),
      settlement: clone(state.lastSettlement),
      lastBar: clone(state.lastBar),
      lastError: state.lastError,
      realizedAmount: state.realizedAmount,
      realizedCost: state.realizedCost,
      unrealizedAmount: state.unrealizedAmount,
      unrealizedPercent: state.unrealizedPercent,
      lastBuyBarIndex: state.lastBuyBarIndex,
      lastSellBarIndex: state.lastSellBarIndex,
      defaultAmount: state.defaultAmount
    };
  }

  function event(name, detail) {
    try {
      if (typeof global.CustomEvent === 'function') {
        return new global.CustomEvent(name, { detail: detail });
      }
      if (typeof global.Event === 'function') {
        var fallback = new global.Event(name);
        fallback.detail = detail;
        return fallback;
      }
    } catch (error) {}
    return { type: name, detail: detail };
  }

  function dispatch(name, detail) {
    if (!global || typeof global.dispatchEvent !== 'function') return;
    try { global.dispatchEvent(event(name, detail)); } catch (error) {}
  }

  function notifyState(reason) {
    var snapshot = stateSnapshot();
    var detail = clone(snapshot);
    detail.reason = reason || 'state-change';
    detail.event = 'replay-trade-state';
    subscribers.slice().forEach(function (handler) {
      try { handler(clone(snapshot), { reason: detail.reason, event: detail.event }); } catch (error) {}
    });
    dispatch('replay-trade-state', detail);
  }

  function notifyExecution(execution) {
    var snapshot = stateSnapshot();
    var detail = clone(execution);
    detail.state = snapshot;
    detail.event = 'replay-trade-executed';
    dispatch('replay-trade-executed', detail);
  }

  function clearError() {
    state.lastError = null;
  }

  function invalid(message) {
    state.lastError = message || 'invalid trade request';
    return false;
  }

  function manualInput(input, isClose) {
    input = input || {};
    var index = nonNegativeInteger(input.barIndex);
    var field = fieldName(input.field);
    var price = positive(input.price);
    var time = timestamp(input.timestamp);
    if (index === null) return { error: 'barIndex must be a non-negative integer' };
    if (!field) return { error: 'field must be open, high, low, or close' };
    if (price === null) return { error: 'price must be greater than zero' };
    if (isClose) {
      var quantity = null;
      if (input.quantity !== undefined && input.quantity !== null && input.quantity !== '') {
        quantity = positive(input.quantity);
        if (quantity === null) return { error: 'quantity must be greater than zero' };
      }
      return { index: index, field: field, price: price, quantity: quantity, timestamp: time };
    }
    var amount = positive(input.amount);
    if (amount === null) return { error: 'amount must be greater than zero' };
    return { index: index, field: field, price: price, amount: amount, timestamp: time };
  }

  function makeExecution(side, action, input, trigger, order) {
    return {
      id: 'execution-' + (++tradeSequence),
      side: side,
      action: action,
      trigger: trigger || 'manual',
      barIndex: input.index,
      timestamp: input.timestamp,
      field: input.field === undefined ? null : input.field,
      price: input.price,
      amount: input.amount === undefined ? null : input.amount,
      targetPrice: order ? order.price : null,
      order: order ? clone(order) : null
    };
  }

  function lotFromExecution(execution) {
    return {
      executionId: execution.id,
      orderNumber: execution.orderNumber,
      label: execution.label,
      barIndex: execution.barIndex,
      timestamp: execution.timestamp,
      field: execution.field,
      price: execution.price,
      amount: execution.amount,
      cost: execution.amount,
      shares: execution.shares,
      quantity: execution.quantity,
      originalPrice: execution.price,
      originalAmount: execution.amount,
      originalShares: execution.shares,
      originalQuantity: execution.quantity,
      soldAmount: 0,
      soldQuantity: 0
    };
  }

  function addExecution(execution) {
    state.executions.push(execution);
    return execution;
  }

  function findExecution(id) {
    for (var i = 0; i < state.executions.length; i += 1) {
      if (state.executions[i].id === id) return state.executions[i];
    }
    return null;
  }

  function findLot(executionId) {
    if (!state.position || !Array.isArray(state.position.lots)) return null;
    for (var i = 0; i < state.position.lots.length; i += 1) {
      if (state.position.lots[i].executionId === executionId) return state.position.lots[i];
    }
    return null;
  }

  function executeBuy(input, trigger, order) {
    var shares = input.amount / input.price;
    var execution = makeExecution('buy', 'open', input, trigger, order);
    execution.orderNumber = state.nextOrderNumber++;
    execution.label = 'B' + execution.orderNumber;
    execution.shares = shares;
    execution.quantity = shares;
    execution.cost = input.amount;
    execution.originalShares = shares;
    execution.originalQuantity = shares;
    execution.originalAmount = input.amount;
    execution.remainingShares = shares;
    execution.remainingQuantity = shares;
    execution.remainingAmount = input.amount;
    execution.soldShares = 0;
    execution.soldQuantity = 0;
    execution.soldAmount = 0;
    var lots = state.position && Array.isArray(state.position.lots)
      ? state.position.lots.slice() : [];
    lots.push(lotFromExecution(execution));
    state.position = positionSnapshot(lots, input.price, input.index, input.timestamp);
    state.lastBuyBarIndex = input.index;
    state.pendingOrders.buy = null;
    state.lastExecution = execution;
    addExecution(execution);
    updateHoldingPerformance();
    execution.position = clone(state.position);
    clearError();
    return execution;
  }

  function isBracketOrder(order) {
    return !!order && BRACKET_SIDES.indexOf(sideName(order.side || order.type || order.role)) >= 0;
  }

  function sellAllocations(position, order, requestedQuantity) {
    var allLots = (position.lots || []).filter(function (lot) {
      return positive(lot && lot.quantity) !== null && Number(lot.quantity) > EPSILON;
    });
    var selectedLots = order && order.bindingMode === 'selected' ? allLots.filter(function (lot) {
      return lotMatchesBracket(order, lot);
    }) : allLots;
    var available = selectedLots.reduce(function (total, lot) {
      return total + Number(lot.quantity || 0);
    }, 0);
    if (available <= EPSILON) return { quantity: 0, allocations: [] };
    var quantity = requestedQuantity === undefined || requestedQuantity === null
      ? available : positive(requestedQuantity);
    if (quantity === null || quantity <= 0) return { quantity: 0, allocations: [] };
    if (quantity > available) quantity = available;
    var ratio = available > EPSILON ? quantity / available : 1;
    return {
      quantity: quantity,
      allocations: selectedLots.map(function (lot) {
        return {
          lot: lot,
          quantity: Math.min(Number(lot.quantity || 0), Number(lot.quantity || 0) * ratio)
        };
      }).filter(function (allocation) {
        return allocation.quantity > EPSILON;
      })
    };
  }

  function pruneBracketsAfterExit(triggeredOrder, affectedExecutionIds, affectedOrderNumbers) {
    if (!state.bracketOrders.length) return;
    var affectedIds = uniqueStrings(affectedExecutionIds);
    var affectedNumbers = uniqueOrderNumbers(affectedOrderNumbers);
    state.bracketOrders = state.bracketOrders.filter(function (order) {
      if (!state.position) return false;
      if (triggeredOrder && order.id === triggeredOrder.id) return false;
      if (order.bindingMode === 'all') return resolveBracketLots(order).length > 0;
      order.executionIds = (order.executionIds || []).filter(function (id) {
        return affectedIds.indexOf(String(id)) < 0;
      });
      order.orderNumbers = (order.orderNumbers || []).filter(function (orderNumber) {
        return affectedNumbers.indexOf(Number(orderNumber)) < 0;
      });
      return order.executionIds.length > 0 || order.orderNumbers.length > 0;
    });
    syncPendingBracketViews();
  }

  function executeSell(input, trigger, order) {
    if (!state.position) return null;
    var position = state.position;
    var totalQuantity = (position.lots || []).reduce(function (total, lot) {
      return total + Number(lot.quantity || 0);
    }, 0);
    var allocationResult = sellAllocations(position, order, input.quantity);
    var allocations = allocationResult.allocations;
    var quantity = allocations.reduce(function (total, allocation) {
      return total + allocation.quantity;
    }, 0);
    if (quantity <= EPSILON || quantity > totalQuantity + EPSILON) {
      state.lastError = 'sell quantity exceeds the open position';
      return null;
    }
    var plannedBrackets = state.bracketOrders.filter(function (candidate) {
      return allocations.some(function (allocation) {
        return lotMatchesBracket(candidate, allocation.lot);
      });
    }).map(bracketSnapshot);
    var allocatedCost = allocations.reduce(function (total, allocation) {
      return total + Number(allocation.lot.price || 0) * allocation.quantity;
    }, 0);
    var proceeds = input.price * quantity;
    var pnlAmount = proceeds - allocatedCost;
    var pnlPercent = pnlPercentForTrade(pnlAmount, allocatedCost);
    var execution = makeExecution('sell', 'close', input, trigger, order);
    var exit = {
      barIndex: input.index,
      timestamp: input.timestamp,
      field: input.field,
      price: input.price
    };
    var entryLot = allocations[0].lot;
    var lotSettlements = allocations.map(function (allocation) {
      var lot = allocation.lot;
      var lotCost = Number(lot.price || 0) * allocation.quantity;
      var lotProceeds = input.price * allocation.quantity;
      var lotPnl = lotProceeds - lotCost;
      return {
        executionId: lot.executionId,
        orderNumber: lot.orderNumber,
        label: lot.label || (lot.orderNumber ? 'B' + lot.orderNumber : null),
        exitLabel: lot.orderNumber ? 'S' + lot.orderNumber : 'S',
        quantity: allocation.quantity,
        shares: allocation.quantity,
        entryPrice: lot.price,
        exitPrice: input.price,
        investedAmount: lotCost,
        cost: lotCost,
        proceeds: lotProceeds,
        profitAmount: lotPnl,
        pnlAmount: lotPnl,
        profitPercent: pnlPercentForTrade(lotPnl, lotCost),
        pnlPercent: pnlPercentForTrade(lotPnl, lotCost)
      };
    });
    var settlement = {
      entryPrice: allocatedCost / quantity,
      exitPrice: input.price,
      investedAmount: allocatedCost,
      cost: allocatedCost,
      proceeds: proceeds,
      shares: quantity,
      quantity: quantity,
      profitAmount: pnlAmount,
      profitPercent: pnlPercent,
      realizedAmount: pnlAmount,
      realizedCost: allocatedCost,
      remainingQuantity: Math.max(0, totalQuantity - quantity),
      remainingAmount: Math.max(0, position.amount - allocatedCost),
      remainingCost: Math.max(0, position.cost - allocatedCost),
      trigger: trigger || 'manual',
      entry: entrySnapshot(entryLot),
      lots: clone(position.lots),
      lotSettlements: clone(lotSettlements),
      plannedBrackets: clone(plannedBrackets),
      closedExecutionIds: lotSettlements.map(function (lot) { return lot.executionId; }),
      closedOrderNumbers: lotSettlements.map(function (lot) { return lot.orderNumber; }),
      exit: clone(exit),
      entryBar: entryLot.barIndex,
      exitBar: input.index,
      entryBarIndex: entryLot.barIndex,
      entryTimestamp: entryLot.timestamp,
      entryField: entryLot.field,
      exitBarIndex: input.index,
      exitTimestamp: input.timestamp,
      exitField: input.field
    };
    var trade = {
      id: 'trade-' + (++tradeSequence),
      entry: entrySnapshot(entryLot),
      exit: clone(exit),
      barIndex: entryLot.barIndex,
      entryBarIndex: entryLot.barIndex,
      exitBarIndex: input.index,
      shares: quantity,
      quantity: quantity,
      amount: allocatedCost,
      cost: allocatedCost,
      proceeds: proceeds,
      pnlAmount: pnlAmount,
      pnlPercent: pnlPercent,
      profitAmount: pnlAmount,
      profitPercent: pnlPercent,
      lotSettlements: clone(lotSettlements),
      plannedBrackets: clone(plannedBrackets),
      settlement: clone(settlement),
      trigger: trigger || 'manual'
    };
    execution.shares = quantity;
    execution.quantity = quantity;
    execution.cost = allocatedCost;
    execution.allocatedCost = allocatedCost;
    execution.proceeds = proceeds;
    execution.pnlAmount = pnlAmount;
    execution.pnlPercent = pnlPercent;
    execution.profitAmount = pnlAmount;
    execution.profitPercent = pnlPercent;
    execution.realizedAmount = pnlAmount;
    execution.realizedCost = allocatedCost;
    execution.executionIds = settlement.closedExecutionIds.slice();
    execution.orderNumbers = settlement.closedOrderNumbers.slice();
    execution.orderNumber = execution.orderNumbers.length === 1 ? execution.orderNumbers[0] : null;
    execution.labels = execution.orderNumbers.map(function (orderNumber) { return 'S' + orderNumber; });
    execution.label = execution.labels.length === 1 ? execution.labels[0] : execution.labels.join('/');
    execution.lotSettlements = clone(lotSettlements);
    execution.settlement = clone(settlement);
    execution.trade = clone(trade);
    if (isBracketOrder(order)) execution.bracketOrderId = order.id;
    var affectedExecutionIds = [];
    var affectedOrderNumbers = [];
    var remainingLots = [];
    (position.lots || []).forEach(function (lot) {
      var allocation = allocations.filter(function (candidate) {
        return candidate.lot.executionId === lot.executionId;
      })[0];
      var soldQuantity = allocation ? allocation.quantity : 0;
      if (soldQuantity > EPSILON) {
        affectedExecutionIds.push(String(lot.executionId));
        affectedOrderNumbers.push(Number(lot.orderNumber));
      }
      var remainingQuantity = Number(lot.quantity || 0) - soldQuantity;
      lot.soldQuantity = Number(lot.soldQuantity || 0) + soldQuantity;
      lot.soldAmount = Number(lot.soldAmount || 0) + Number(lot.price) * soldQuantity;
      lot.quantity = remainingQuantity > EPSILON ? remainingQuantity : 0;
      lot.shares = lot.quantity;
      lot.amount = Number(lot.price) * lot.quantity;
      lot.cost = lot.amount;
      var buyExecution = findExecution(lot.executionId);
      if (buyExecution) {
        buyExecution.soldQuantity = Number(buyExecution.soldQuantity || 0) + soldQuantity;
        buyExecution.soldShares = buyExecution.soldQuantity;
        buyExecution.soldAmount = Number(buyExecution.soldAmount || 0) + Number(lot.price) * soldQuantity;
        buyExecution.remainingQuantity = lot.quantity;
        buyExecution.remainingShares = lot.quantity;
        buyExecution.remainingAmount = lot.amount;
      }
      if (lot.quantity > EPSILON) remainingLots.push(lot);
    });
    state.completedTrades.push(trade);
    state.realizedAmount += pnlAmount;
    state.realizedCost += allocatedCost;
    state.lastSettlement = settlement;
    state.lastSellBarIndex = input.index;
    if (remainingLots.length) {
      state.position = positionSnapshot(remainingLots, input.price, input.index, input.timestamp);
      updateHoldingPerformance();
      settlement.remainingPosition = clone(state.position);
    } else {
      state.position = null;
      state.unrealizedAmount = 0;
      state.unrealizedPercent = 0;
      state.holdingPerformance = null;
    }
    state.pendingOrders.sell = null;
    if (state.bracketOrders.length) {
      pruneBracketsAfterExit(isBracketOrder(order) ? order : null,
        affectedExecutionIds, affectedOrderNumbers);
    }
    state.lastExecution = execution;
    addExecution(execution);
    clearError();
    return execution;
  }

  function pnlPercentForTrade(amount, cost) {
    return pnlPercent(amount, cost);
  }

  function reset(options) {
    state = makeState(options || {});
    tradeSequence = 0;
    notifyState('reset');
    return true;
  }

  function getState() {
    return clone(stateSnapshot());
  }

  function openManual(input) {
    var normalized = manualInput(input, false);
    if (normalized.error) return invalid(normalized.error);
    if (state.lastSellBarIndex === normalized.index) {
      return invalid('manual buy and sell cannot occur on the same bar');
    }
    var execution = executeBuy(normalized, 'manual', null);
    if (!execution) return invalid('unable to open position');
    notifyState('manual-buy');
    notifyExecution(execution);
    return true;
  }

  function closeManual(input) {
    var normalized = manualInput(input, true);
    if (normalized.error) return invalid(normalized.error);
    if (!state.position) return invalid('no open position');
    input = input || {};
    var executionIds = uniqueStrings(bindingValues(input, 'executionIds', 'executionId'));
    var orderNumbers = uniqueOrderNumbers(bindingValues(input, 'orderNumbers', 'orderNumber'));
    var selectionOrder = executionIds.length || orderNumbers.length ? {
      id: 'manual-selection-' + (tradeSequence + 1),
      side: 'sell',
      type: 'sell',
      role: 'sell',
      bindingMode: 'selected',
      executionIds: executionIds,
      orderNumbers: orderNumbers
    } : null;
    var selectedLots = selectionOrder ? currentOpenLots().filter(function (lot) {
      return lotMatchesBracket(selectionOrder, lot);
    }) : currentOpenLots();
    if (!selectedLots.length) return invalid('selected position was not found');
    var lastBuyBarIndex = selectedLots.reduce(function (latest, lot) {
      var value = nonNegativeInteger(lot && lot.barIndex);
      return value !== null && (latest === null || value > latest) ? value : latest;
    }, null);
    if (lastBuyBarIndex === null) lastBuyBarIndex = state.lastBuyBarIndex;
    if (normalized.index <= lastBuyBarIndex) {
      return invalid('manual sell must use a bar after the entry bar');
    }
    var execution = executeSell(normalized, 'manual', selectionOrder);
    if (!execution) return invalid('unable to close position');
    notifyState('manual-sell');
    notifyExecution(execution);
    return true;
  }

  function setPendingOrder(input) {
    input = input || {};
    var requestedSide = input.side !== undefined ? input.side
      : (input.type !== undefined ? input.type : input.role);
    var side = sideName(requestedSide);
    var price = positive(input.price);
    if (!side) return invalid('side must be buy, sell, takeProfit, or stopLoss');
    if (price === null) return invalid('price must be greater than zero');
    var amount = null;
    if (side === 'buy') {
      amount = input.amount === undefined || input.amount === null || input.amount === ''
        ? state.defaultAmount : positive(input.amount);
      if (amount === null) return invalid('amount must be greater than zero');
    } else if (side === 'sell' && input.amount !== undefined && input.amount !== null && input.amount !== '') {
      amount = positive(input.amount);
      if (amount === null) return invalid('amount must be greater than zero');
    }
    if (BRACKET_SIDES.indexOf(side) >= 0 && input.amount !== undefined &&
        input.amount !== null && input.amount !== '' && positive(input.amount) === null) {
      return invalid('amount must be greater than zero');
    }
    var quantity = null;
    if (side !== 'buy' && input.quantity !== undefined && input.quantity !== null && input.quantity !== '') {
      quantity = positive(input.quantity);
      if (quantity === null) return invalid('quantity must be greater than zero');
    }
    if (BRACKET_SIDES.indexOf(side) >= 0) {
      var bracket = makeBracketOrder(side, {
        id: input.id,
        bracketId: input.bracketId,
        price: price,
        amount: input.amount === undefined || input.amount === null || input.amount === ''
          ? null : amount,
        quantity: quantity,
        trigger: input.trigger,
        executionIds: bindingValues(input, 'executionIds', 'executionId'),
        orderNumbers: bindingValues(input, 'orderNumbers', 'orderNumber')
      });
      state.bracketOrders.push(bracket);
      syncPendingBracketViews();
    } else {
      state.pendingOrders[side] = {
        side: side,
        type: side,
        role: side,
        price: price,
        amount: amount,
        quantity: quantity,
        targetPrice: price,
        trigger: 'range'
      };
    }
    clearError();
    notifyState('pending-' + side);
    return true;
  }

  function updatePendingOrder(sideOrInput, patch) {
    var input = typeof sideOrInput === 'object' && sideOrInput !== null
      ? clone(sideOrInput) : clone(patch || {});
    var explicitBindingRequested = hasExplicitBracketBinding(input);
    if (typeof sideOrInput !== 'object' || sideOrInput === null) input.side = sideOrInput;
    var requestedSide = input.side !== undefined ? input.side
      : (input.type !== undefined ? input.type : input.role);
    var side = sideName(requestedSide);
    if (!side && input && (input.id || input.bracketId || input.orderId)) {
      var updateId = input.id || input.bracketId || input.orderId;
      var identifiedBracket = state.bracketOrders.filter(function (order) {
        return order.id === updateId;
      })[0];
      if (identifiedBracket) side = identifiedBracket.side;
    }
    if (!side) return invalid('side must be buy, sell, takeProfit, or stopLoss');
    var current = BRACKET_SIDES.indexOf(side) >= 0
      ? (input.id || input.bracketId || input.orderId
        ? state.bracketOrders.filter(function (order) {
          return order.id === (input.id || input.bracketId || input.orderId);
        })[0] : latestBracket(side))
      : state.pendingOrders[side];
    if (!current) return invalid('pending order was not found');
    Object.keys(current).forEach(function (key) {
      if (input[key] === undefined) input[key] = current[key];
    });
    input.side = side;
    if (BRACKET_SIDES.indexOf(side) >= 0) {
      var currentIndex = state.bracketOrders.indexOf(current);
      var replacement = makeBracketOrder(side, input);
      replacement.id = current.id;
      if (!explicitBindingRequested) {
        replacement.bindingMode = current.bindingMode;
        replacement.executionIds = clone(current.executionIds || []);
        replacement.orderNumbers = clone(current.orderNumbers || []);
      }
      state.bracketOrders[currentIndex] = replacement;
      syncPendingBracketViews();
      clearError();
      notifyState('pending-' + side + '-updated');
      return true;
    }
    return setPendingOrder(input);
  }

  function clearBracketOrders() {
    state.bracketOrders = [];
    syncPendingBracketViews();
  }

  function cancelPending(side) {
    var sideInput = side;
    if (side && typeof side === 'object') {
      side = side.side !== undefined ? side.side
        : (side.type !== undefined ? side.type : side.role);
      if ((side === undefined || side === null || side === '') &&
          (sideInput.id || sideInput.bracketId || sideInput.orderId)) {
        var cancelId = sideInput.id || sideInput.bracketId || sideInput.orderId;
        var identifiedCancelBracket = state.bracketOrders.filter(function (order) {
          return order.id === cancelId;
        })[0];
        if (identifiedCancelBracket) side = identifiedCancelBracket.side;
      }
    }
    if (side === undefined || side === null || side === '') {
      var hadPending = SIDES.some(function (pendingSide) {
        return !!state.pendingOrders[pendingSide];
      });
      if (!hadPending) return false;
      SIDES.forEach(function (pendingSide) { state.pendingOrders[pendingSide] = null; });
      clearError();
      notifyState('cancel-pending');
      return true;
    }
    var normalizedSide = sideName(side);
    if (!normalizedSide) return invalid('side must be buy, sell, takeProfit, or stopLoss');
    if (BRACKET_SIDES.indexOf(normalizedSide) >= 0) {
      var requestedId = sideInput && typeof sideInput === 'object'
        ? (sideInput.id || sideInput.bracketId || sideInput.orderId) : null;
      var bracketIndex = requestedId
        ? state.bracketOrders.findIndex(function (order) { return order.id === requestedId; })
        : -1;
      if (bracketIndex < 0) {
        for (var bracketCursor = state.bracketOrders.length - 1; bracketCursor >= 0; bracketCursor -= 1) {
          if (state.bracketOrders[bracketCursor].side === normalizedSide) {
            bracketIndex = bracketCursor;
            break;
          }
        }
      }
      if (bracketIndex < 0) return false;
      state.bracketOrders.splice(bracketIndex, 1);
      syncPendingBracketViews();
    } else {
      if (!state.pendingOrders[normalizedSide]) return false;
      state.pendingOrders[normalizedSide] = null;
    }
    clearError();
    notifyState('cancel-pending-' + normalizedSide);
    return true;
  }

  function markToMarket(bar, index, silent) {
    var normalized = normalizeBar(bar);
    var resolvedIndex = nonNegativeInteger(index);
    if (!normalized || resolvedIndex === null) return invalid('bar and index are required');
    setCursor(normalized, resolvedIndex);
    applyUnrealized(normalized, resolvedIndex);
    clearError();
    if (!silent) notifyState('mark-to-market');
    return true;
  }

  function pendingInput(index, timestampValue, price, amount, quantity) {
    return {
      index: index,
      timestamp: timestampValue,
      field: null,
      price: price,
      amount: amount,
      quantity: quantity
    };
  }

  function bracketExecutionOrder(order, barIndex) {
    var eligibleLots = resolveBracketLots(order).filter(function (lot) {
      var entryIndex = nonNegativeInteger(lot && lot.barIndex);
      return entryIndex === null || entryIndex < barIndex;
    });
    if (!eligibleLots.length) return null;
    var allLots = resolveBracketLots(order);
    if (eligibleLots.length === allLots.length) return order;
    var scoped = clone(order);
    scoped.bindingMode = 'selected';
    scoped.executionIds = eligibleLots.map(function (lot) { return String(lot.executionId); });
    scoped.orderNumbers = eligibleLots.map(function (lot) { return Number(lot.orderNumber); });
    scoped.quantity = null;
    return scoped;
  }

  function findTriggeredBracket(bar, barIndex) {
    var priority = ['stopLoss', 'takeProfit'];
    for (var p = 0; p < priority.length; p += 1) {
      for (var i = 0; i < state.bracketOrders.length; i += 1) {
        var order = state.bracketOrders[i];
        if (order.side !== priority[p]) continue;
        var executionOrder = bracketExecutionOrder(order, barIndex);
        if (!executionOrder) continue;
        var triggered = bracketTrigger(order, bar);
        if (triggered) {
          triggered.order = executionOrder;
          return triggered;
        }
      }
    }
    return null;
  }

  function processBar(bar, index) {
    var normalized = normalizeBar(bar);
    var resolvedIndex = nonNegativeInteger(index);
    if (!normalized || resolvedIndex === null) return invalid('bar and index are required');
    var executions = [];
    var buyFilledThisBar = false;
    setCursor(normalized, resolvedIndex);

    if (state.pendingOrders.buy && inRange(normalized, state.pendingOrders.buy.price)) {
      var buyOrder = state.pendingOrders.buy;
      var buyInput = pendingInput(
        resolvedIndex, normalized.timestamp, buyOrder.price,
        buyOrder.amount || state.defaultAmount, null
      );
      var buyExecution = executeBuy(buyInput, 'pending', buyOrder);
      if (buyExecution) {
        executions.push(buyExecution);
        buyFilledThisBar = true;
      }
    }

    /* A newly filled lot cannot exit on its entry bar; older lots still must. */
    var bracketTriggered = false;
    if (state.position) {
      var bracketTriggerResult = findTriggeredBracket(normalized, resolvedIndex);
      while (bracketTriggerResult && state.position) {
        var bracketOrder = bracketTriggerResult.order;
        var bracketInput = pendingInput(
          resolvedIndex, normalized.timestamp, bracketTriggerResult.price, null,
          bracketOrder.quantity
        );
        var bracketExecution = executeSell(
          bracketInput, 'pending-' + bracketOrder.side, bracketOrder
        );
        if (!bracketExecution) break;
        executions.push(bracketExecution);
        bracketTriggered = true;
        bracketTriggerResult = findTriggeredBracket(normalized, resolvedIndex);
      }

      if (!buyFilledThisBar && !bracketTriggered && state.position &&
          resolvedIndex > state.position.lastBuyBarIndex && state.pendingOrders.sell &&
          inRange(normalized, state.pendingOrders.sell.price)) {
        var sellOrder = state.pendingOrders.sell;
        var sellInput = pendingInput(
          resolvedIndex, normalized.timestamp, sellOrder.price, null,
          sellOrder.quantity
        );
        var sellExecution = executeSell(sellInput, 'pending', sellOrder);
        if (sellExecution) {
          executions.push(sellExecution);
        }
      }
    }

    applyUnrealized(normalized, resolvedIndex);
    clearError();
    notifyState('process-bar');
    executions.forEach(notifyExecution);
    return true;
  }

  function updateExecution(id, patch) {
    patch = patch || {};
    var execution = findExecution(id);
    if (!execution) return invalid('execution was not found');
    if (execution.side !== 'buy' || execution.action !== 'open') {
      return invalid('only an open buy execution can be updated');
    }
    if (Number(execution.soldQuantity || 0) > EPSILON ||
        Number(execution.remainingQuantity || 0) + EPSILON < Number(execution.originalQuantity || execution.quantity || 0)) {
      return invalid('settled buy executions cannot be updated');
    }
    var lot = findLot(id);
    if (!lot) return invalid('only an open buy execution can be updated');
    var price = patch.price === undefined ? positive(execution.price) : positive(patch.price);
    var amount = patch.amount === undefined ? positive(execution.amount) : positive(patch.amount);
    if (price === null) return invalid('price must be greater than zero');
    if (amount === null) return invalid('amount must be greater than zero');
    var shares = amount / price;
    var barIndex = patch.barIndex === undefined ? execution.barIndex : nonNegativeInteger(patch.barIndex);
    if (barIndex === null) return invalid('barIndex must be a non-negative integer');
    var executionTimestamp = patch.timestamp === undefined ? execution.timestamp : timestamp(patch.timestamp);
    var field = patch.field === undefined ? execution.field : fieldName(patch.field);
    if (patch.field !== undefined && !field) return invalid('field must be open, high, low, or close');
    execution.price = price;
    execution.amount = amount;
    execution.cost = amount;
    execution.shares = shares;
    execution.quantity = shares;
    execution.originalPrice = price;
    execution.originalAmount = amount;
    execution.originalShares = shares;
    execution.originalQuantity = shares;
    execution.remainingShares = shares;
    execution.remainingQuantity = shares;
    execution.remainingAmount = amount;
    execution.barIndex = barIndex;
    execution.timestamp = executionTimestamp;
    execution.field = field;
    lot.price = price;
    lot.amount = amount;
    lot.cost = amount;
    lot.shares = shares;
    lot.quantity = shares;
    lot.originalPrice = price;
    lot.originalAmount = amount;
    lot.originalShares = shares;
    lot.originalQuantity = shares;
    lot.barIndex = barIndex;
    lot.timestamp = executionTimestamp;
    lot.field = field;
    var currentPrice = state.position.currentPrice;
    var currentBarIndex = state.position.currentBarIndex;
    var currentTimestamp = state.position.currentTimestamp;
    state.position = positionSnapshot(state.position.lots, currentPrice, currentBarIndex, currentTimestamp);
    updateHoldingPerformance();
    execution.position = clone(state.position);
    if (state.lastExecution && state.lastExecution.id === execution.id) {
      state.lastExecution = execution;
    }
    clearError();
    notifyState('execution-updated');
    return true;
  }

  function subscribe(handler) {
    if (typeof handler !== 'function') return false;
    if (subscribers.indexOf(handler) < 0) subscribers.push(handler);
    return true;
  }

  function unsubscribe(handler) {
    var index = subscribers.indexOf(handler);
    if (index < 0) return false;
    subscribers.splice(index, 1);
    return true;
  }

  function detailOf(eventObject) {
    return eventObject && eventObject.detail !== undefined ? eventObject.detail : eventObject;
  }

  function resolveCursor(detail) {
    detail = detail || {};
    if (Array.isArray(detail)) return { bar: detail[0], index: 0 };
    var index = detail.index;
    if (index === undefined) index = detail.dataIndex;
    if (index === undefined) index = detail.barIndex;
    if (index === undefined && typeof detail.cursor === 'number') index = detail.cursor;
    var bar = detail.bar || detail.data || detail.kline || detail.currentBar;
    if (!bar && global.__kline_chart && typeof global.__kline_chart.getDataList === 'function') {
      var list = global.__kline_chart.getDataList();
      var resolved = nonNegativeInteger(index);
      if (Array.isArray(list) && resolved !== null) bar = list[resolved];
    }
    return { bar: bar, index: index };
  }

  function syncReplay(detail) {
    detail = detailOf(detail) || {};
    var cursor = resolveCursor(detail);
    var targetIndex = nonNegativeInteger(cursor.index);
    if (targetIndex === null || targetIndex <= state.cursor) return true;
    var history = Array.isArray(detail.history) ? detail.history
      : (Array.isArray(detail.visibleBars) ? detail.visibleBars : null);
    if (history) {
      for (var index = Math.max(0, state.cursor + 1); index <= targetIndex; index += 1) {
        if (history[index]) processBar(history[index], index);
      }
      return state.cursor >= targetIndex;
    }
    return cursor.bar ? processBar(cursor.bar, targetIndex) : false;
  }

  function bindReplayEvents() {
    if (!global || typeof global.addEventListener !== 'function' || state.eventsBound) return;
    state.eventsBound = true;
    global.addEventListener('bar-replay-start', function (eventObject) {
      var detail = detailOf(eventObject);
      var options = detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : {};
      reset({
        defaultAmount: options.defaultAmount !== undefined ? options.defaultAmount : options.amount
      });
    });
    global.addEventListener('bar-replay-cursor', function (eventObject) {
      syncReplay(eventObject);
    });
    global.addEventListener('bar-replay-exit', function () { reset(); });
  }

  var controller = {
    reset: reset,
    getState: getState,
    openManual: openManual,
    closeManual: closeManual,
    setPendingOrder: setPendingOrder,
    cancelPending: cancelPending,
    updatePendingOrder: updatePendingOrder,
    updateExecution: updateExecution,
    processBar: processBar,
    syncReplay: syncReplay,
    markToMarket: markToMarket,
    subscribe: subscribe,
    unsubscribe: unsubscribe
  };

  bindReplayEvents();
  global.ReplayTradeEngine = controller;
}(typeof window !== 'undefined' ? window : globalThis));
