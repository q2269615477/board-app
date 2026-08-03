(function (root, factory) {
  var exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (root) root.ReplayTradeGeometry = exported;
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

  function indexFromValue(value, rows) {
    rows = Array.isArray(rows) ? rows : [];
    var direct = finite(value);
    if (direct != null && Math.round(direct) === direct && direct >= 0 && direct < rows.length) return direct;
    var ts = timestamp(value);
    if (ts == null) return -1;
    for (var index = 0; index < rows.length; index += 1) {
      if (timestamp(rows[index] && rows[index].timestamp) === ts) return index;
    }
    return -1;
  }

  function eventCoordinates(event, rect) {
    rect = rect || { left: 0, top: 0 };
    var x = event && typeof event.clientX === 'number'
      ? event.clientX - Number(rect.left || 0)
      : event && typeof event.offsetX === 'number' ? event.offsetX : null;
    var y = event && typeof event.clientY === 'number'
      ? event.clientY - Number(rect.top || 0)
      : event && typeof event.offsetY === 'number' ? event.offsetY : null;
    return { x: x, y: y, rect: rect };
  }

  function extractConvertedPrice(value, depth) {
    depth = finite(depth) == null ? 0 : Number(depth);
    if (depth > 4 || value == null) return null;
    var direct = finite(value);
    if (direct != null) return direct;
    if (Array.isArray(value)) {
      for (var index = value.length - 1; index >= 0; index -= 1) {
        var item = extractConvertedPrice(value[index], depth + 1);
        if (item != null) return item;
      }
      return null;
    }
    if (typeof value !== 'object') return null;
    var keys = ['price', 'value', 'yValue', 'quote', 'close'];
    for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
      var key = keys[keyIndex];
      if (value[key] !== undefined) {
        var nested = extractConvertedPrice(value[key], depth + 1);
        if (nested != null) return nested;
      }
    }
    return null;
  }

  function firstConvertedPoint(converted) {
    if (Array.isArray(converted)) return converted.length ? converted[0] : null;
    return converted;
  }

  function convertedPrice(converted) {
    var point = converted;
    if (Array.isArray(converted) && converted.length && typeof converted[0] === 'object') {
      point = converted[0];
    }
    if (point == null) return null;
    var rawValue = point;
    if (typeof point === 'object' && !Array.isArray(point)) {
      if (point.value !== undefined) rawValue = point.value;
      else if (point.price !== undefined) rawValue = point.price;
      else if (point.yValue !== undefined) rawValue = point.yValue;
      else if (point.quote !== undefined) rawValue = point.quote;
      else if (point.close !== undefined) rawValue = point.close;
    }
    return extractConvertedPrice(rawValue, 0);
  }

  function pixelConversionInput(coordinates, width) {
    coordinates = coordinates || {};
    if (coordinates.y == null) return null;
    var x = coordinates.x;
    if (x == null) x = Number(width) > 0 ? Number(width) / 2 : 0;
    var pixel = { x: x, y: coordinates.y };
    return { x: x, y: coordinates.y, attempts: [[pixel], pixel] };
  }

  function indexConversionInputs(coordinates) {
    coordinates = coordinates || {};
    var x = coordinates.x;
    var y = coordinates.y;
    if (x == null) return [];
    var pixel = { x: x, y: y == null ? 0 : y };
    return [[pixel], pixel];
  }

  function convertedIndex(converted, rows) {
    var point = firstConvertedPoint(converted);
    return point ? indexFromValue(point.dataIndex, rows) : -1;
  }

  function proportionalIndexFromX(x, width, rowCount) {
    if (x == null || !(Number(width) > 0) || !(Number(rowCount) > 0)) return -1;
    return Math.max(0, Math.min(Number(rowCount) - 1,
      Math.round((Number(x) / Number(width)) * (Number(rowCount) - 1))));
  }

  function priceConversionInputs(index, price, rows) {
    rows = Array.isArray(rows) ? rows : [];
    var resolvedIndex = finite(index) != null ? Number(index) : Math.max(0, rows.length - 1);
    return [
      [{ dataIndex: resolvedIndex, value: price }],
      { dataIndex: resolvedIndex, value: price },
      [{ value: price }],
      { value: price },
    ];
  }

  function convertedPixelPoint(converted) {
    var point = firstConvertedPoint(converted);
    if (point && finite(point.x) != null && finite(point.y) != null) {
      return { x: Number(point.x), y: Number(point.y) };
    }
    return null;
  }

  function legacyPriceToPixel(index, price, rows, width, height) {
    rows = Array.isArray(rows) ? rows : [];
    width = Number(width) || 0;
    height = Number(height) || 0;
    var x = rows.length > 1 && finite(index) != null ? (Number(index) / (rows.length - 1)) * width : width / 2;
    var values = rows.map(function (row) { return [finite(row && row.high), finite(row && row.low)]; })
      .reduce(function (all, pair) {
        if (pair[0] != null) all.push(pair[0]);
        if (pair[1] != null) all.push(pair[1]);
        return all;
      }, []);
    var max = values.length ? Math.max.apply(Math, values) : price + 1;
    var min = values.length ? Math.min.apply(Math, values) : price - 1;
    var y = height - ((price - min) / ((max - min) || 1)) * height;
    return { x: x, y: y };
  }

  return {
    indexFromValue: indexFromValue,
    eventCoordinates: eventCoordinates,
    extractConvertedPrice: extractConvertedPrice,
    firstConvertedPoint: firstConvertedPoint,
    convertedPrice: convertedPrice,
    pixelConversionInput: pixelConversionInput,
    indexConversionInputs: indexConversionInputs,
    convertedIndex: convertedIndex,
    proportionalIndexFromX: proportionalIndexFromX,
    priceConversionInputs: priceConversionInputs,
    convertedPixelPoint: convertedPixelPoint,
    legacyPriceToPixel: legacyPriceToPixel,
  };
}));
