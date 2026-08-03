const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_ROOT = path.resolve(__dirname, '..');

function makeVm() {
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
      throw new Error('network access is forbidden in replay contract tests');
    },
    setTimeout() { return 0; },
    clearTimeout() {},
    setInterval() { return 0; },
    clearInterval() {},
  };
  window.window = window;
  const sandbox = {
    window,
    console,
    fetch: window.fetch,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
  };
  const context = vm.createContext(sandbox);
  return {
    window,
    context,
    get networkCalls() { return networkCalls; },
  };
}

function loadEvents() {
  const file = path.join(JS_ROOT, 'bar-replay-events.js');
  assert.ok(fs.existsSync(file), 'missing BarReplayEvents production module; the contract must not be weakened');

  const env = makeVm();
  vm.runInContext(fs.readFileSync(file, 'utf8'), env.context, { filename: file });
  const exported = env.window.BarReplayEvents || env.context.BarReplayEvents;
  assert.ok(exported, 'BarReplayEvents must be exported to the browser global');
  const events = typeof exported === 'function'
    ? new exported()
    : typeof exported.create === 'function'
      ? exported.create()
      : exported;
  ['emit', 'on', 'off'].forEach((method) => {
    assert.equal(typeof events[method], 'function', `BarReplayEvents.${method} is required`);
  });
  return { env, events };
}

function detailOf(value) {
  if (value && value.detail !== undefined) return value.detail;
  if (value && value.payload !== undefined) return value.payload;
  return value;
}

function nativeArray(value, mapper) {
  return Array.from(value || [], mapper);
}

function makeElement(tagName, registry) {
  const listeners = new Map();
  const element = {
    tagName: tagName.toUpperCase(),
    id: '',
    type: '',
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    clientWidth: 1000,
    style: {},
    dataset: {},
    children: [],
    classList: { add() {}, remove() {} },
    appendChild(child) {
      this.children.push(child);
      registry.register(child);
      return child;
    },
    insertBefore(child, before) {
      const index = this.children.indexOf(before);
      if (index < 0) return this.appendChild(child);
      this.children.splice(index, 0, child);
      registry.register(child);
      return child;
    },
    querySelector(selector) {
      if (selector === '.item.tools') return this.children.find((child) => child.className === 'item tools') || null;
      return null;
    },
    setAttribute(name, value) {
      this[name] = String(value);
      if (name === 'id') this.id = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    },
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
    getBoundingClientRect() { return { left: 0, top: 0 }; },
  };
  return element;
}

function makeReplayHarness(count = 5) {
  const registry = {
    elements: new Map(),
    register(element) {
      if (element.id) this.elements.set(element.id, element);
      element.children.forEach((child) => this.register(child));
    },
  };
  const periodBar = makeElement('div', registry);
  periodBar.className = 'klinecharts-pro-period-bar';
  const document = {
    createElement: (tagName) => makeElement(tagName, registry),
    getElementById: (id) => registry.elements.get(id) || null,
    querySelector: (selector) => selector === '.klinecharts-pro-period-bar' ? periodBar : null,
  };
  const windowListeners = new Map();
  const intervals = new Map();
  let intervalId = 0;
  const window = {
    document,
    addEventListener(name, handler) {
      if (!windowListeners.has(name)) windowListeners.set(name, []);
      windowListeners.get(name).push(handler);
    },
    removeEventListener(name, handler) {
      const values = windowListeners.get(name) || [];
      windowListeners.set(name, values.filter((item) => item !== handler));
    },
    dispatchEvent(event) {
      (windowListeners.get(event.type) || []).slice().forEach((handler) => handler(event));
    },
    setInterval(handler) {
      const id = ++intervalId;
      intervals.set(id, handler);
      return id;
    },
    clearInterval(id) { intervals.delete(id); },
    setTimeout() { return 0; },
    clearTimeout() {},
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    Event: function Event(type) { this.type = type; },
    fetch() { throw new Error('network access is forbidden in replay contract tests'); },
  };
  window.window = window;
  const sandbox = {
    window,
    document,
    console,
    fetch: window.fetch,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
  };
  const context = vm.createContext(sandbox);

  const all = Array.from({ length: count }, (_, index) => ({
    timestamp: index + 1,
    open: 10 + index,
    high: 12 + index,
    low: 9 + index,
    close: 11 + index,
  }));
  const dom = makeElement('div', registry);
  const chart = {
    all,
    visible: all.slice(),
    updates: [],
    applies: [],
    getDataList() { return this.visible; },
    applyNewData(data) {
      this.visible = data.slice();
      this.applies.push(data.slice());
    },
    updateData(bar) {
      this.visible.push(bar);
      this.updates.push(bar);
    },
    getDom() { return dom; },
    convertFromPixel() { return [{ dataIndex: 1 }]; },
  };

  const eventsFile = path.join(JS_ROOT, 'bar-replay-events.js');
  const controllerFile = path.join(JS_ROOT, 'bar-replay.js');
  assert.ok(fs.existsSync(eventsFile), 'missing BarReplayEvents production module');
  assert.ok(fs.existsSync(controllerFile), 'missing BarReplayController production module');
  vm.runInContext(fs.readFileSync(eventsFile, 'utf8'), context, { filename: eventsFile });
  vm.runInContext(fs.readFileSync(controllerFile, 'utf8'), context, { filename: controllerFile });

  const events = window.BarReplayEvents || context.BarReplayEvents;
  const controller = window.BarReplayController || context.BarReplayController;
  assert.ok(events, 'BarReplayEvents must be available to the controller');
  assert.ok(controller, 'BarReplayController must be exported to the browser global');
  assert.equal(typeof controller.init, 'function');
  controller.init({ chart, events, eventBus: events, replayEvents: events });

  return {
    window,
    context,
    chart,
    controller,
    events,
    names: {
      START: events.START || 'bar-replay-start',
      CURSOR: events.CURSOR || 'bar-replay-cursor',
      STATUS: events.STATUS || 'bar-replay-status',
      EXIT: events.EXIT || 'bar-replay-exit',
    },
    flushIntervals() {
      [...intervals.values()].forEach((handler) => handler());
    },
  };
}

function captureReplayEvents(env) {
  const records = { START: [], CURSOR: [], STATUS: [], EXIT: [] };
  Object.keys(records).forEach((key) => {
    env.events.on(env.names[key], (value) => records[key].push(detailOf(value)));
  });
  return records;
}

describe('BarReplayEvents contract', () => {
  test('emit/on/off deliver independent event copies', () => {
    const { env, events } = loadEvents();
    const source = {
      cursor: 3,
      bar: { timestamp: 4, close: 101.25 },
      nested: { label: 'original' },
    };
    const received = [];
    let domReceived = null;
    env.window.addEventListener('CURSOR', (event) => { domReceived = event.detail; });
    const first = (value) => {
      const detail = detailOf(value);
      received.push(detail);
      detail.bar.close = -1;
      detail.nested.label = 'mutated by first listener';
    };
    const second = (value) => received.push(detailOf(value));

    events.on('CURSOR', first);
    events.on('CURSOR', second);
    const result = events.emit('CURSOR', source);

    assert.equal(received.length, 2);
    assert.notStrictEqual(received[0], source);
    assert.notStrictEqual(received[1], source);
    assert.notStrictEqual(received[0].bar, source.bar);
    assert.notStrictEqual(received[1].nested, source.nested);
    assert.equal(received[1].bar.close, 101.25);
    assert.equal(received[1].nested.label, 'original');
    assert.ok(domReceived, 'emit must also dispatch the DOM event');
    assert.notStrictEqual(domReceived, source);
    assert.notStrictEqual(domReceived.bar, source.bar);
    assert.equal(domReceived.bar.close, 101.25);
    assert.equal(domReceived.nested.label, 'original');
    assert.equal(source.bar.close, 101.25);
    assert.equal(source.nested.label, 'original');
    assert.notEqual(result, false, 'emit may return a receipt, but must not suppress delivery');
    assert.equal(env.networkCalls, 0);

    events.off('CURSOR', first);
    events.emit('CURSOR', source);
    assert.equal(received.length, 3, 'off(type, listener) must remove exactly that listener');
    assert.equal(received[2].bar.close, 101.25);

    events.off('CURSOR', second);
    events.emit('CURSOR', source);
    assert.equal(received.length, 3, 'off must stop subsequent delivery');
  });

  test('off can remove all listeners for one event without affecting another event', () => {
    const { events } = loadEvents();
    let cursorCount = 0;
    let statusCount = 0;
    const cursorListener = () => { cursorCount += 1; };
    const statusListener = () => { statusCount += 1; };

    events.on('CURSOR', cursorListener);
    events.on('STATUS', statusListener);
    events.off('CURSOR');
    events.emit('CURSOR', { cursor: 1 });
    events.emit('STATUS', { status: 'paused' });

    assert.equal(cursorCount, 0);
    assert.equal(statusCount, 1);
  });
});

describe('BarReplayController event contract', () => {
  test('selecting a start emits START and CURSOR with a complete replay snapshot', () => {
    const env = makeReplayHarness();
    const records = captureReplayEvents(env);

    assert.equal(env.controller.startSelection(), true);
    assert.equal(env.controller.selectIndex(1), true);
    assert.equal(records.START.length, 1);
    assert.equal(records.CURSOR.length, 1);

    const start = records.START[0];
    assert.equal(start.cursor, 1);
    assert.equal(start.status, 'paused');
    assert.deepEqual(nativeArray(start.history, (bar) => bar.timestamp), [1, 2, 3, 4, 5]);
    assert.deepEqual(nativeArray(start.visibleBars, (bar) => bar.timestamp), [1, 2]);
    assert.equal(start.bar.timestamp, 2);
    assert.equal(start.reason, 'start-selected');
    assert.notStrictEqual(start.history, env.chart.all);
    assert.notStrictEqual(start.history[0], env.chart.all[0]);
  });

  test('step emits CURSOR for exactly the next bar and play/pause emit STATUS', () => {
    const env = makeReplayHarness();
    const records = captureReplayEvents(env);
    env.controller.startSelection();
    env.controller.selectIndex(1);
    records.CURSOR.length = 0;

    assert.equal(env.controller.step(), true);
    assert.equal(records.CURSOR.length, 1);
    assert.equal(records.CURSOR[0].cursor, 2);
    assert.equal(records.CURSOR[0].bar.timestamp, 3);
    assert.equal(records.CURSOR[0].visibleBars.length, 3);
    assert.equal(records.CURSOR[0].reason, 'step');

    assert.equal(env.controller.togglePlay(), true);
    assert.equal(env.controller.togglePlay(), false);
    assert.deepEqual(nativeArray(records.STATUS, (item) => item.status), ['playing', 'paused']);
    assert.deepEqual(nativeArray(records.STATUS, (item) => item.reason), ['play', 'pause']);
  });

  test('exit emits the full pre-exit snapshot and leaves controller/chart empty or restored', () => {
    const env = makeReplayHarness();
    const records = captureReplayEvents(env);
    env.controller.startSelection();
    env.controller.selectIndex(2);
    assert.equal(env.controller.exit({ silent: true }), true);

    assert.equal(records.EXIT.length, 1);
    const exit = records.EXIT[0];
    assert.equal(exit.cursor, 2);
    assert.equal(exit.status, 'paused');
    assert.deepEqual(nativeArray(exit.history, (bar) => bar.timestamp), [1, 2, 3, 4, 5]);
    assert.deepEqual(nativeArray(exit.visibleBars, (bar) => bar.timestamp), [1, 2, 3]);
    assert.equal(exit.reason, 'user');
    assert.equal(exit.restore, true);
    assert.notStrictEqual(exit.history, env.chart.all);

    const after = env.controller.getState();
    assert.equal(after.status, 'idle');
    assert.equal(after.cursor, -1);
    assert.equal(after.total, 0);
    assert.equal(after.visibleCount, 0);
    assert.deepEqual(nativeArray(env.chart.visible, (bar) => bar.timestamp), [1, 2, 3, 4, 5]);
  });
});
