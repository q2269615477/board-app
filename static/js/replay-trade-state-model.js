(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.ReplayTradeStateModel = exported;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function timestamp(value) {
    var number = finite(value);
    if (number == null) return null;
    return number < 10000000000 ? number * 1000 : number;
  }

  function sideOf(value) {
    var side = String(value == null ? '' : value).toLowerCase();
    if (side === 'buy' || side === 'b' || side === 'long' || side === 'entry' || side === 'in') return 'buy';
    if (side === 'sell' || side === 's' || side === 'short' || side === 'exit' || side === 'out') return 'sell';
    return '';
  }

  function normalizeRecord(record) {
    if (!record || typeof record !== 'object') return null;
    var side = sideOf(record.side || record.action || record.type || record.direction);
    if (!side) return null;
    var index = finite(record.dataIndex != null ? record.dataIndex : record.index != null ? record.index : record.barIndex);
    var price = finite(record.price != null ? record.price : record.executionPrice != null ? record.executionPrice : record.fillPrice);
    var amount = finite(record.amount != null ? record.amount : record.value != null ? record.value : record.cost);
    var quantity = finite(record.quantity != null ? record.quantity :
      record.shares != null ? record.shares : record.qty != null ? record.qty : record.remainingQuantity);
    if (quantity == null && side === 'buy' && amount != null && price != null && price > 0) {
      quantity = amount / price;
    }
    return Object.assign({}, record, {
      side: side,
      index: index,
      timestamp: timestamp(record.timestamp || record.time || record.date),
      price: price,
      amount: amount,
      quantity: quantity,
      orderNumber: finite(record.orderNumber != null ? record.orderNumber : record.orderNo),
      priceField: record.priceField || record.field || record.element || 'close',
    });
  }

  function assignOrderNumbers(records) {
    var next = 0;
    (records || []).forEach(function (record) {
      if (!record || record.side !== 'buy') return;
      var explicit = finite(record.orderNumber);
      if (explicit != null && explicit > 0) next = Math.max(next, Math.round(explicit));
      else record.orderNumber = ++next;
    });
    return records;
  }

  function recordIdentity(record) {
    if (!record) return '';
    if (record.id != null && record.id !== '') return 'id:' + String(record.id);
    return [
      sideOf(record.side || record.action || record.type || record.direction),
      timestamp(record.timestamp || record.time || record.date),
      finite(record.index != null ? record.index : record.dataIndex != null ? record.dataIndex : record.barIndex),
      finite(record.price != null ? record.price : record.executionPrice),
      finite(record.amount != null ? record.amount : record.value),
      finite(record.quantity != null ? record.quantity : record.shares),
    ].join('|');
  }

  function appendUniqueRecord(records, seen, record) {
    var normalized = normalizeRecord(record);
    if (!normalized) return false;
    var key = recordIdentity(normalized);
    if (seen[key]) return false;
    seen[key] = true;
    records.push(normalized);
    return true;
  }

  function positionAggregate(position) {
    if (!position) return null;
    if (Array.isArray(position)) {
      var allLots = [];
      position.forEach(function (item) {
        if (item && Array.isArray(item.lots)) allLots = allLots.concat(item.lots);
        else if (item) allLots.push(item);
      });
      return positionAggregate({ lots: allLots });
    }
    var lots = Array.isArray(position.lots) ? position.lots : [];
    var quantity = 0;
    var cost = 0;
    var lotCount = 0;
    lots.forEach(function (lot) {
      var lotQuantity = finite(lot && (lot.remainingQuantity != null ? lot.remainingQuantity :
        lot.quantity != null ? lot.quantity : lot.shares != null ? lot.shares : lot.qty));
      var lotPrice = finite(lot && (lot.entryPrice != null ? lot.entryPrice : lot.price));
      if (lotQuantity == null || lotQuantity <= 0 || lotPrice == null || lotPrice <= 0) return;
      var lotCost = finite(lot.investedAmount != null ? lot.investedAmount :
        lot.cost != null ? lot.cost : lot.amount);
      if (lotCost == null) lotCost = lotQuantity * lotPrice;
      quantity += lotQuantity;
      cost += lotCost;
      lotCount += 1;
    });
    if (!lotCount) {
      quantity = finite(position.remainingQuantity != null ? position.remainingQuantity :
        position.quantity != null ? position.quantity : position.shares);
      var entryPrice = finite(position.weightedEntryPrice != null ? position.weightedEntryPrice :
        position.entryPrice != null ? position.entryPrice : position.price);
      cost = finite(position.investedAmount != null ? position.investedAmount :
        position.amount != null ? position.amount : position.cost);
      if (quantity == null) quantity = 0;
      if (cost == null && entryPrice != null) cost = quantity * entryPrice;
      lotCount = quantity > 0 || entryPrice != null ?
        Number(position.lotsCount != null ? position.lotsCount : position.lotCount != null ? position.lotCount : 1) : 0;
    }
    if (quantity <= 0 || cost == null || cost <= 0) return null;
    var weightedEntryPrice = cost / quantity;
    var currentPrice = finite(position.currentPrice != null ? position.currentPrice :
      position.marketPrice != null ? position.marketPrice : position.lastPrice);
    var marketValue = finite(position.marketValue);
    if (marketValue == null && currentPrice != null) marketValue = currentPrice * quantity;
    return {
      quantity: quantity,
      investedAmount: cost,
      weightedEntryPrice: weightedEntryPrice,
      currentPrice: currentPrice,
      marketValue: marketValue,
      lotCount: Math.max(1, lotCount),
    };
  }

  function flattenTradeState(raw) {
    if (!raw || typeof raw !== 'object') return { records: [], presets: {}, pnl: {} };
    var sources = [];
    if (Array.isArray(raw.executions)) sources = sources.concat(raw.executions);
    if (Array.isArray(raw.records)) sources = sources.concat(raw.records);
    if (!sources.length && Array.isArray(raw.transactions)) sources = raw.transactions.slice();
    var records = [];
    var seen = Object.create(null);
    sources.forEach(function (record) { appendUniqueRecord(records, seen, record); });
    var completed = raw.completedTrades || raw.trades || [];
    if (!records.length && Array.isArray(completed)) {
      completed.forEach(function (trade) {
        if (!trade || !trade.entry || !trade.exit) return;
        appendUniqueRecord(records, seen, Object.assign({}, trade.entry, { side: 'buy' }));
        appendUniqueRecord(records, seen, Object.assign({}, trade.exit, { side: 'sell' }));
      });
    }
    if (!records.length && raw.lastExecution) {
      appendUniqueRecord(records, seen, raw.lastExecution);
    }
    assignOrderNumbers(records);
    records.sort(function (left, right) {
      var leftTime = timestamp(left && left.timestamp);
      var rightTime = timestamp(right && right.timestamp);
      if (leftTime !== rightTime) return (leftTime == null ? Infinity : leftTime) - (rightTime == null ? Infinity : rightTime);
      var leftIndex = finite(left && left.index);
      var rightIndex = finite(right && right.index);
      return (leftIndex == null ? Infinity : leftIndex) - (rightIndex == null ? Infinity : rightIndex);
    });
    var positions = raw.positions || raw.position || raw.openPosition;
    var position = Array.isArray(positions) ? positions : positions || null;
    var aggregate = positionAggregate(position);
    if (!records.length && position && !Array.isArray(position) &&
        (position.price != null || position.entryPrice != null) && !Array.isArray(position.lots)) {
      appendUniqueRecord(records, seen, Object.assign({}, position, {
        side: 'buy',
        price: position.price != null ? position.price : position.entryPrice,
        amount: position.amount != null ? position.amount : position.value,
        timestamp: position.timestamp || position.entryTimestamp,
      }));
    }
    var presets = raw.presets || raw.pendingPresets || raw.pendingOrders || raw.pending || {};
    var currentPosition = raw.currentPosition || raw.position || raw.openPosition || null;
    if (Array.isArray(currentPosition)) currentPosition = currentPosition[0] || null;
    var unrealized = raw.holdingPerformance || (currentPosition && currentPosition.performance) ||
      raw.unrealizedPnl || raw.unrealized || raw.floatingPnl || {};
    var realized = raw.realizedPnl || raw.realized || {};
    var settlement = raw.lastSettlement || raw.settlement || null;
    if (!settlement && Array.isArray(completed) && completed.length) {
      settlement = completed[completed.length - 1];
    }
    return {
      records: records,
      presets: presets,
      position: currentPosition,
      positionSummary: aggregate,
      openLots: currentPosition && Array.isArray(currentPosition.lots) ? currentPosition.lots.slice() : [],
      bracketOrders: raw.bracketOrders || null,
      completedTrades: Array.isArray(completed) ? completed.slice() : [],
      settlement: settlement,
      pnl: {
        unrealizedPct: finite(unrealized.percent != null ? unrealized.percent : (raw.unrealizedPct != null ? raw.unrealizedPct : raw.floatingPct)),
        unrealizedAmount: finite(unrealized.amount != null ? unrealized.amount : raw.unrealizedAmount),
        realizedPct: finite(realized.percent != null ? realized.percent : raw.realizedPct),
        realizedAmount: finite(realized.amount != null ? realized.amount : raw.realizedAmount),
      },
    };
  }

  return {
    normalizeRecord: normalizeRecord,
    assignOrderNumbers: assignOrderNumbers,
    recordIdentity: recordIdentity,
    appendUniqueRecord: appendUniqueRecord,
    positionAggregate: positionAggregate,
    flattenTradeState: flattenTradeState,
    finite: finite,
    timestamp: timestamp,
    sideOf: sideOf,
  };
}));
