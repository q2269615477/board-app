(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.PriceRangeModel = exported;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var MAX_PRICE_PRECISION = 20;

  function finiteNumber(value, field) {
    if (value === null || value === undefined || value === '') {
      return error('INVALID_NUMBER', field + ' must be a finite number', field);
    }
    if (typeof value === 'boolean' || typeof value === 'object') {
      return error('INVALID_NUMBER', field + ' must be a finite number', field);
    }
    var number = Number(value);
    if (!Number.isFinite(number)) {
      return error('INVALID_NUMBER', field + ' must be a finite number', field);
    }
    return number;
  }

  function positiveNumber(value, field) {
    var number = finiteNumber(value, field);
    if (number && number.ok === false) return number;
    if (!(number > 0)) {
      return error('NON_POSITIVE', field + ' must be greater than zero', field);
    }
    return number;
  }

  function error(code, message, field) {
    return {
      ok: false,
      error: {
        code: code,
        message: message,
        field: field,
      },
    };
  }

  function round(value, precision) {
    var sign = value < 0 ? -1 : 1;
    var shifted = Number(String(Math.abs(value)) + 'e' + precision);
    var rounded;
    if (Number.isFinite(shifted)) {
      rounded = Number(String(sign * Math.round(shifted)) + 'e-' + precision);
    } else {
      rounded = Number(value.toFixed(precision));
    }
    return Number.isFinite(rounded) ? rounded : null;
  }

  function readPrecision(input) {
    if (input.pricePrecision === undefined || input.pricePrecision === null || input.pricePrecision === '') {
      return null;
    }
    var precision = finiteNumber(input.pricePrecision, 'pricePrecision');
    if (precision && precision.ok === false) return precision;
    if (!Number.isInteger(precision) || precision < 0 || precision > MAX_PRICE_PRECISION) {
      return error(
        'INVALID_PRECISION',
        'pricePrecision must be an integer from 0 to ' + MAX_PRICE_PRECISION,
        'pricePrecision'
      );
    }
    return precision;
  }

  function calculatePriceRange(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
      return error('INVALID_INPUT', 'input must be an object', 'input');
    }

    var startPrice = positiveNumber(input.startPrice, 'startPrice');
    if (startPrice && startPrice.ok === false) return startPrice;
    var endPrice = positiveNumber(input.endPrice, 'endPrice');
    if (endPrice && endPrice.ok === false) return endPrice;

    var precision = readPrecision(input);
    if (precision && precision.ok === false) return precision;

    var tickSize;
    if (input.tickSize !== undefined && input.tickSize !== null && input.tickSize !== '') {
      tickSize = positiveNumber(input.tickSize, 'tickSize');
      if (tickSize && tickSize.ok === false) return tickSize;
    }

    var difference = endPrice - startPrice;
    var absoluteDifference = Math.abs(difference);
    var percent = difference / startPrice * 100;
    if (!Number.isFinite(difference) || !Number.isFinite(absoluteDifference) || !Number.isFinite(percent)) {
      return error('NON_FINITE_RESULT', 'price range calculation produced a non-finite result', 'calculation');
    }

    if (precision !== null) {
      startPrice = round(startPrice, precision);
      endPrice = round(endPrice, precision);
      difference = round(difference, precision);
      absoluteDifference = round(absoluteDifference, precision);
      if (startPrice === null || endPrice === null || difference === null || absoluteDifference === null) {
        return error('NON_FINITE_RESULT', 'price precision produced a non-finite result', 'pricePrecision');
      }
    }

    var result = {
      ok: true,
      startPrice: startPrice,
      endPrice: endPrice,
      difference: difference,
      absoluteDifference: absoluteDifference,
      percent: percent,
      direction: difference > 0 ? 'up' : difference < 0 ? 'down' : 'flat',
    };

    if (tickSize !== undefined) {
      var tickCount = Math.abs((endPrice - startPrice) / tickSize);
      if (!Number.isFinite(tickCount)) {
        return error('NON_FINITE_RESULT', 'tick calculation produced a non-finite result', 'tickSize');
      }
      result.tickCount = precision === null ? tickCount : round(tickCount, precision);
      if (result.tickCount === null || !Number.isFinite(result.tickCount)) {
        return error('NON_FINITE_RESULT', 'tick precision produced a non-finite result', 'pricePrecision');
      }
    }

    if (!Number.isFinite(result.percent)) {
      return error('NON_FINITE_RESULT', 'percentage calculation produced a non-finite result', 'calculation');
    }
    return result;
  }

  return {
    calculatePriceRange: calculatePriceRange,
  };
}));
