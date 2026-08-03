(function (global) {
  'use strict';

  if (global.BarReplayEvents &&
      typeof global.BarReplayEvents.emit === 'function' &&
      typeof global.BarReplayEvents.on === 'function' &&
      typeof global.BarReplayEvents.off === 'function') {
    return;
  }

  var handlers = Object.create(null);
  var START = 'bar-replay-start';
  var CURSOR = 'bar-replay-cursor';
  var STATUS = 'bar-replay-status';
  var EXIT = 'bar-replay-exit';

  function copy(value, seen) {
    if (value === null || typeof value !== 'object') return value;
    if (!seen) seen = [];
    for (var i = 0; i < seen.length; i += 1) {
      if (seen[i][0] === value) return seen[i][1];
    }
    var result = Array.isArray(value) ? [] : {};
    seen.push([value, result]);
    Object.keys(value).forEach(function (key) {
      result[key] = copy(value[key], seen);
    });
    return result;
  }

  function on(name, handler) {
    if (typeof name !== 'string' || typeof handler !== 'function') return handler;
    if (!handlers[name]) handlers[name] = [];
    if (handlers[name].indexOf(handler) < 0) handlers[name].push(handler);
    return function () { off(name, handler); };
  }

  function off(name, handler) {
    if (!handlers[name]) return;
    if (typeof handler !== 'function') {
      handlers[name] = [];
      return;
    }
    handlers[name] = handlers[name].filter(function (item) { return item !== handler; });
  }

  function emit(name, detail) {
    var listeners = (handlers[name] || []).slice();
    listeners.forEach(function (handler) {
      try { handler(copy(detail)); } catch (error) { /* one listener must not block replay */ }
    });
    if (global && typeof global.dispatchEvent === 'function') {
      try {
        var event = typeof global.CustomEvent === 'function'
          ? new global.CustomEvent(name, { detail: copy(detail) })
          : { type: name, detail: copy(detail) };
        global.dispatchEvent(event);
      } catch (error) { /* DOM observers are optional */ }
    }
    return copy(detail);
  }

  global.BarReplayEvents = {
    START: START,
    CURSOR: CURSOR,
    STATUS: STATUS,
    EXIT: EXIT,
    emit: emit,
    on: on,
    off: off,
  };
}(typeof window !== 'undefined' ? window : globalThis));
