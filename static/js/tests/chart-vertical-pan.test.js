const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadController() {
  const elements = new Map();
  const makeElement = () => ({
    id: '', className: '', textContent: '', dataset: {}, children: [],
    classList: { toggle() {} },
    setAttribute() {}, addEventListener() {},
  });
  const periodBar = makeElement();
  periodBar.querySelector = () => null;
  periodBar.appendChild = (child) => { elements.set(child.id, child); periodBar.children.push(child); };
  periodBar.insertBefore = periodBar.appendChild;
  const document = {
    readyState: 'complete', documentElement: {},
    createElement: makeElement,
    getElementById: (id) => elements.get(id) || null,
    querySelector: (selector) => selector === '.klinecharts-pro-period-bar' ? periodBar : null,
  };
  const window = {
    document,
    addEventListener() {},
    setTimeout(handler) { handler(); },
  };
  const context = vm.createContext({ window, document, globalThis: window, console });
  const source = fs.readFileSync(require.resolve('../chart-vertical-pan.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'chart-vertical-pan.js' });
  return { controller: window.ChartVerticalPanController, elements };
}

function makeChart() {
  const calls = [];
  let auto = true;
  let wheelHandler = null;
  let wheelOptions = null;
  const axis = {
    getExtremum: () => ({ min: 10, max: 20, range: 10, realMin: 10, realMax: 20, realRange: 10 }),
    setExtremum: (value) => calls.push(['extremum', value.min, value.max]),
    getAutoCalcTickFlag: () => auto,
    setAutoCalcTickFlag: (value) => { auto = value; calls.push(['auto', value]); },
    buildTicks: (force) => calls.push(['ticks', force === true]),
    convertToRealValue: (value) => value,
  };
  const mainDom = {
    clientHeight: 100,
    getBoundingClientRect: () => ({ top: 0, height: 100 }),
    addEventListener: (type, handler, options) => {
      if (type === 'wheel') {
        wheelHandler = handler;
        wheelOptions = options;
      }
    },
    removeEventListener: () => { wheelHandler = null; },
  };
  const chart = {
    getDrawPaneById: (id) => id === 'candle_pane' ? { getAxisComponent: () => axis } : null,
    getDom: (paneId, position) => paneId === 'candle_pane' && position === 'main' ? mainDom : null,
    setPaneOptions: (options) => calls.push(['pane', options.axisOptions.scrollZoomEnabled]),
    adjustPaneViewport: (height, width, xAxis, yAxis, force) => calls.push([
      'redraw', yAxis === true, force === true,
    ]),
    updatePane: (level) => calls.push(['update', level]),
  };
  return {
    chart, calls, getAuto: () => auto, getWheel: () => wheelHandler,
    getWheelOptions: () => wheelOptions,
  };
}

describe('ChartVerticalPanController', () => {
  test('enabling preserves the range and unlocks native vertical drag', () => {
    const env = loadController();
    const fixture = makeChart();
    env.controller.init({ chart: fixture.chart });
    assert.equal(env.controller.setEnabled(true), true);
    assert.equal(env.controller.isEnabled(), true);
    assert.equal(fixture.getAuto(), false);
    assert.deepEqual(fixture.calls.slice(0, 3), [
      ['pane', true],
      ['auto', false],
      ['extremum', 10, 20],
    ]);
  });

  test('reset restores automatic price scaling', () => {
    const env = loadController();
    const fixture = makeChart();
    env.controller.init({ chart: fixture.chart });
    env.controller.setEnabled(true);
    assert.equal(env.controller.reset(), true);
    assert.equal(env.controller.isEnabled(), false);
    assert.equal(fixture.getAuto(), true);
    assert.deepEqual(fixture.calls.slice(-3), [
      ['ticks', true],
      ['redraw', true, true],
      ['update', 4],
    ]);
  });

  test('unsupported chart fails closed', () => {
    const env = loadController();
    assert.equal(env.controller.setEnabled(true, { chart: {} }), false);
    assert.equal(env.controller.isEnabled(), false);
  });

  test('wheel zooms the price axis around the pointer and keeps native X zoom', () => {
    const env = loadController();
    const fixture = makeChart();
    let preventDefaultCalls = 0;
    env.controller.init({ chart: fixture.chart });
    assert.equal(env.controller.setEnabled(true), true);
    assert.equal(typeof fixture.getWheel(), 'function');
    assert.equal(fixture.getWheelOptions().passive, true);

    // At y=25 the pointer price is 17.5. Zooming in must keep that price
    // at the same screen position while reducing the visible price range.
    fixture.getWheel()({
      clientY: 25,
      deltaY: -120,
      preventDefault() { preventDefaultCalls += 1; },
    });
    const zoom = fixture.calls.filter((call) => call[0] === 'extremum').at(-1);
    assert.ok(zoom[2] - zoom[1] < 10);
    assert.ok(Math.abs((17.5 - zoom[1]) / (zoom[2] - zoom[1]) - 0.75) < 1e-9);
    // Passive means the controller never cancels the event, so native X zoom
    // remains available to KLineCharts on the same wheel event.
    assert.equal(preventDefaultCalls, 0);
    assert.equal(fixture.getAuto(), false);
  });

  test('wheel zoom is bounded and disabling removes the price-wheel handler', () => {
    const env = loadController();
    const fixture = makeChart();
    env.controller.init({ chart: fixture.chart });
    env.controller.setEnabled(true);
    const wheel = fixture.getWheel();
    for (let i = 0; i < 100; i += 1) wheel({ clientY: 50, deltaY: -10000 });
    const zoomIn = fixture.calls.filter((call) => call[0] === 'extremum').at(-1);
    assert.ok(zoomIn[2] - zoomIn[1] >= 0.5);

    env.controller.reset();
    assert.equal(fixture.getWheel(), null);
    assert.equal(fixture.getAuto(), true);
  });
});
