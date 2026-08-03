(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.PositionRiskModel = exported;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var MAX_QTY_PRECISION = 20;

  function finiteNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'boolean' || typeof value === 'object') return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function error(code, message, field) {
    return {
      ok: false,
      error: {
        code: code,
        message: message,
        field: field || null,
      },
    };
  }

  function directionName(value) {
    if (typeof value !== 'string') return null;
    var direction = value.trim().toLowerCase();
    return direction === 'long' || direction === 'short' ? direction : null;
  }

  function positiveInput(value, field) {
    var number = finiteNumber(value);
    if (number === null) return error('INVALID_NUMBER', field + ' must be a finite number', field);
    if (!(number > 0)) return error('NON_POSITIVE', field + ' must be greater than zero', field);
    return number;
  }

  function normalizePositionPoints(direction, entry, target, stop) {
    var input = direction;
    if (direction && typeof direction === 'object' && arguments.length === 1) {
      input = direction;
      direction = input.direction;
      entry = input.entry;
      target = input.target;
      stop = input.stop;
    }

    var normalizedDirection = directionName(direction);
    if (!normalizedDirection) {
      return error('INVALID_DIRECTION', 'direction must be long or short', 'direction');
    }

    var normalizedEntry = positiveInput(entry, 'entry');
    if (typeof normalizedEntry !== 'number') return normalizedEntry;
    var normalizedTarget = positiveInput(target, 'target');
    if (typeof normalizedTarget !== 'number') return normalizedTarget;
    var normalizedStop = positiveInput(stop, 'stop');
    if (typeof normalizedStop !== 'number') return normalizedStop;

    if (normalizedDirection === 'long' &&
        !(normalizedTarget > normalizedEntry && normalizedEntry > normalizedStop)) {
      return error(
        'INVALID_PRICE_ORDER',
        'long positions require target > entry > stop',
        'target'
      );
    }
    if (normalizedDirection === 'short' &&
        !(normalizedStop > normalizedEntry && normalizedEntry > normalizedTarget)) {
      return error(
        'INVALID_PRICE_ORDER',
        'short positions require stop > entry > target',
        'target'
      );
    }

    return {
      ok: true,
      direction: normalizedDirection,
      entry: normalizedEntry,
      target: normalizedTarget,
      stop: normalizedStop,
    };
  }

  function readRisk(input, accountSize) {
    var rawRisk = input.risk;
    var mode = input.riskMode || input.riskType || input.riskUnit || null;
    var value = rawRisk;

    if (rawRisk && typeof rawRisk === 'object' && !Array.isArray(rawRisk)) {
      var hasAmount = Object.prototype.hasOwnProperty.call(rawRisk, 'amount');
      var hasPercent = Object.prototype.hasOwnProperty.call(rawRisk, 'percent');
      if (hasAmount && hasPercent) {
        return error('AMBIGUOUS_RISK', 'risk must provide amount or percent, not both', 'risk');
      }
      if (hasAmount) {
        mode = 'amount';
        value = rawRisk.amount;
      } else if (hasPercent) {
        mode = 'percent';
        value = rawRisk.percent;
      } else if (Object.prototype.hasOwnProperty.call(rawRisk, 'value')) {
        mode = rawRisk.mode || rawRisk.type || mode || 'percent';
        value = rawRisk.value;
      } else {
        return error('INVALID_RISK', 'risk must contain amount or percent', 'risk');
      }
    }

    if (input.riskAmount !== undefined) {
      if (rawRisk !== undefined) return error('AMBIGUOUS_RISK', 'risk and riskAmount cannot both be supplied', 'risk');
      mode = 'amount';
      value = input.riskAmount;
    }
    if (input.riskPercent !== undefined) {
      if (rawRisk !== undefined) return error('AMBIGUOUS_RISK', 'risk and riskPercent cannot both be supplied', 'risk');
      mode = 'percent';
      value = input.riskPercent;
    }

    mode = mode == null ? 'percent' : String(mode).toLowerCase();
    if (mode !== 'amount' && mode !== 'percent') {
      return error('INVALID_RISK_MODE', 'risk mode must be amount or percent', 'riskMode');
    }

    var riskValue = positiveInput(value, 'risk');
    if (typeof riskValue !== 'number') return riskValue;
    var riskSize = mode === 'percent' ? accountSize * riskValue / 100 : riskValue;
    if (!isFinite(riskSize) || !(riskSize > 0)) {
      return error('INVALID_RISK_SIZE', 'risk amount must be finite and greater than zero', 'risk');
    }
    return { mode: mode, value: riskValue, size: riskSize };
  }

  function readPositiveSetting(input, name, fallback) {
    var raw = input[name];
    if (raw === undefined && fallback !== undefined) raw = fallback;
    return positiveInput(raw, name);
  }

  function readOptionalPositiveSetting(input, name) {
    if (input[name] === undefined || input[name] === null || input[name] === '') return null;
    return positiveInput(input[name], name);
  }

  function readPrecision(input) {
    var raw = input.qtyPrecision === undefined ? 0 : finiteNumber(input.qtyPrecision);
    if (raw === null || Math.floor(raw) !== raw || raw < 0 || raw > MAX_QTY_PRECISION) {
      return error('INVALID_QTY_PRECISION', 'qtyPrecision must be an integer from 0 to ' + MAX_QTY_PRECISION, 'qtyPrecision');
    }
    return raw;
  }

  function roundTo(value, precision) {
    var rounded = Number(value.toFixed(precision));
    return isFinite(rounded) ? rounded : null;
  }

  function calculatePositionRisk(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
      return error('INVALID_INPUT', 'input must be an object', 'input');
    }

    var points = normalizePositionPoints(input);
    if (!points.ok) return points;

    var accountSize = positiveInput(input.accountSize, 'accountSize');
    if (typeof accountSize !== 'number') return accountSize;

    var risk = readRisk(input, accountSize);
    if (risk && risk.ok === false) return risk;

    var lotSize = readPositiveSetting(input, 'lotSize', 1);
    if (typeof lotSize !== 'number') return lotSize;
    var leverage = readPositiveSetting(input, 'leverage', 1);
    if (typeof leverage !== 'number') return leverage;
    var pointValue = readPositiveSetting(input, 'pointValue', 1);
    if (typeof pointValue !== 'number') return pointValue;
    var investmentAmount = readOptionalPositiveSetting(input, 'investmentAmount');
    if (investmentAmount && investmentAmount.ok === false) return investmentAmount;
    var qtyPrecision = readPrecision(input);
    if (typeof qtyPrecision !== 'number') return qtyPrecision;

    var targetDistance = points.direction === 'long'
      ? points.target - points.entry
      : points.entry - points.target;
    var riskDistance = points.direction === 'long'
      ? points.entry - points.stop
      : points.stop - points.entry;
    var denominator = riskDistance * pointValue;
    if (!isFinite(targetDistance) || !(targetDistance > 0)) {
      return error('ZERO_DENOMINATOR', 'target distance must be greater than zero', 'target');
    }
    if (!isFinite(riskDistance) || !(riskDistance > 0) || !isFinite(denominator) || !(denominator > 0)) {
      return error('ZERO_DENOMINATOR', 'risk distance and pointValue must produce a positive denominator', 'stop');
    }

    var qtyRisk = (risk.size / denominator) / lotSize;
    var qtyLeverage = (accountSize * leverage / points.entry) * pointValue / lotSize;
    var qtyInvestment = investmentAmount == null
      ? null
      : investmentAmount * leverage / (points.entry * pointValue * lotSize);
    if (!isFinite(qtyRisk) || !isFinite(qtyLeverage) || !(qtyRisk >= 0) || !(qtyLeverage >= 0) ||
        (qtyInvestment != null && (!isFinite(qtyInvestment) || !(qtyInvestment >= 0)))) {
      return error('NON_FINITE_RESULT', 'quantity calculation produced a non-finite result', 'qty');
    }
    var rawQty = qtyInvestment == null ? Math.min(qtyRisk, qtyLeverage) : qtyInvestment;
    var qty = roundTo(rawQty, qtyPrecision);
    if (qty === null) return error('NON_FINITE_RESULT', 'quantity rounding produced a non-finite result', 'qty');

    var targetPct = targetDistance / points.entry * 100;
    var stopPct = riskDistance / points.entry * 100;
    var multiplier = qty * pointValue * lotSize;
    var profitPnl = targetDistance * multiplier;
    var lossPnl = points.direction === 'long'
      ? (points.stop - points.entry) * multiplier
      : (points.entry - points.stop) * multiplier;
    var profitLossRatio = Math.abs(lossPnl) > 0
      ? Math.abs(profitPnl) / Math.abs(lossPnl)
      : targetDistance / riskDistance;
    // Compatibility alias for older callers. New UI and callers should use profitLossRatio.
    var riskReward = profitLossRatio;
    var targetBalance = accountSize + profitPnl;
    var stopBalance = accountSize + lossPnl;

    var values = [targetPct, stopPct, profitLossRatio, qtyRisk, qtyLeverage, qty,
      profitPnl, lossPnl, targetBalance, stopBalance];
    if (qtyInvestment != null) values.push(qtyInvestment);
    for (var index = 0; index < values.length; index += 1) {
      if (!isFinite(values[index])) {
        return error('NON_FINITE_RESULT', 'risk calculation produced a non-finite result', 'calculation');
      }
    }

    return {
      ok: true,
      direction: points.direction,
      entry: points.entry,
      target: points.target,
      stop: points.stop,
      accountSize: accountSize,
      riskMode: risk.mode,
      risk: risk.value,
      riskSize: risk.size,
      lotSize: lotSize,
      leverage: leverage,
      pointValue: pointValue,
      qtyPrecision: qtyPrecision,
      investmentAmount: investmentAmount,
      sizingMode: investmentAmount == null ? 'risk' : 'investment',
      targetPct: targetPct,
      stopPct: stopPct,
      profitLossRatio: profitLossRatio,
      riskReward: riskReward,
      qtyRisk: qtyRisk,
      qtyLeverage: qtyLeverage,
      qtyInvestment: qtyInvestment,
      qty: qty,
      profitPnl: profitPnl,
      lossPnl: lossPnl,
      targetBalance: targetBalance,
      stopBalance: stopBalance,
    };
  }

  function reversePosition(positionOrDirection, entry, target, stop) {
    if (typeof positionOrDirection === 'string' && arguments.length === 1) {
      var onlyDirection = directionName(positionOrDirection);
      return onlyDirection ? (onlyDirection === 'long' ? 'short' : 'long') : null;
    }

    var input;
    if (typeof positionOrDirection === 'string') {
      input = {
        direction: positionOrDirection,
        entry: entry,
        target: target,
        stop: stop,
      };
    } else if (positionOrDirection && typeof positionOrDirection === 'object' && !Array.isArray(positionOrDirection)) {
      input = positionOrDirection;
    } else {
      return error('INVALID_INPUT', 'position must be an object', 'position');
    }

    var points = normalizePositionPoints(input);
    if (!points.ok) return points;

    var reversed = {};
    Object.keys(input).forEach(function (key) {
      if (key !== 'ok' && key !== 'error' && key !== 'value') reversed[key] = input[key];
    });
    reversed.direction = points.direction === 'long' ? 'short' : 'long';
    reversed.entry = points.entry;
    reversed.target = points.stop;
    reversed.stop = points.target;

    return Object.assign({ ok: true, value: reversed }, reversed);
  }

  return {
    calculatePositionRisk: calculatePositionRisk,
    calculatePosition: calculatePositionRisk,
    calculate: calculatePositionRisk,
    normalizePositionPoints: normalizePositionPoints,
    reversePosition: reversePosition,
  };
}));
