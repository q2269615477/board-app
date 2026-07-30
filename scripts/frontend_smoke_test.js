// Frontend smoke test -- zero-dependency (Node built-ins only)
// Loads static/js/nav-panel.js in a vm sandbox with minimal browser mocks
// and exercises deriveBoardTagsAfterMove for two target categories.
'use strict';

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const NAV_PANEL = path.join(__dirname, '..', 'static', 'js', 'nav-panel.js');

let pass = 0;
let fail = 0;

function check(label, cond) {
  if (cond) {
    console.log(`  PASS  ${label}`);
    pass++;
  } else {
    console.log(`  FAIL  ${label}`);
    fail++;
  }
}

// ---------------------------------------------------------------------------
// Phase 1: Dynamic -- run the script inside a vm sandbox
// ---------------------------------------------------------------------------
function dynamicTest() {
  console.log('\n=== Phase 1: dynamic (vm sandbox) ===\n');

  if (!fs.existsSync(NAV_PANEL)) {
    console.log('  FAIL  nav-panel.js not found:', NAV_PANEL);
    fail++;
    return false;
  }

  const src = fs.readFileSync(NAV_PANEL, 'utf8');

  // ---- minimal browser mocks ------------------------------------------------
  const store = {
    categoryData: [],
    classificationDoc: null,
    _expandedCats: {},
    activeCat: null,
    selected: null,
    _catSortState: {},
  };

  const mockElement = () => ({
    innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    appendChild() {},
    previousElementSibling: null,
    dataset: {},
    style: {},
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }),
    offsetWidth: 280,
  });

  const documentMock = {
    getElementById: () => mockElement(),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => mockElement(),
    addEventListener() {},
  };

  const sandbox = {
    console,
    store,
    document: documentMock,
    window: {},
    fetch: () => Promise.resolve({ json: () => Promise.resolve({}) }),
    API: 'http://localhost:8000',
    setTimeout: () => {},
    setInterval: () => {},
    clearTimeout: () => {},
    clearInterval: () => {},
    Date,
    Math,
    JSON,
    Object,
    Array,
    Set,
    Map,
    String,
    Number,
    Boolean,
    Promise,
    encodeURIComponent,
    Symbol,
    RegExp,
    Error,
  };
  // window.store and window references used by nav-panel.js
  sandbox.window = sandbox;
  sandbox.window.innerWidth = 1920;
  sandbox.window.innerHeight = 1080;
  sandbox.window.addEventListener = () => {};
  sandbox.window.removeEventListener = () => {};

  vm.createContext(sandbox);

  try {
    vm.runInContext(src, sandbox, { filename: 'nav-panel.js' });
  } catch (e) {
    console.log('  FAIL  vm.runInContext threw:', e.message);
    fail++;
    return false;
  }

  // ---- pull the function out of the sandbox ---------------------------------
  const fn = sandbox.deriveBoardTagsAfterMove;
  if (typeof fn !== 'function') {
    console.log('  FAIL  deriveBoardTagsAfterMove is not a function (got ' + typeof fn + ')');
    fail++;
    return false;
  }
  console.log('  OK    deriveBoardTagsAfterMove loaded dynamically');

  // ---- test case 1: 新能源与电力设备 / 光伏产业链 ---------------------------
  const board1 = { name: '测试光伏', code: '999999', type: 'concept', tags: [] };
  const tags1 = fn(board1, '新能源与电力设备', '光伏产业链');
  console.log('  INFO  tags for 新能源/光伏产业链:', tags1.join(', '));
  check('新能源/光伏产业链: 2-6 tags', tags1.length >= 2 && tags1.length <= 6);
  check('新能源/光伏产业链: 含"新能源"', tags1.some(t => t === '新能源'));
  check('新能源/光伏产业链: 含"光伏"', tags1.some(t => t === '光伏'));
  check('新能源/光伏产业链: 含"概念"', tags1.some(t => t === '概念'));

  // ---- test case 2: AI 与数字科技 / 芯片半导体 ------------------------------
  const board2 = { name: '测试芯片', code: '888888', type: 'concept', tags: [] };
  const tags2 = fn(board2, 'AI 与数字科技', '芯片半导体');
  console.log('  INFO  tags for AI/芯片半导体:', tags2.join(', '));
  check('AI/芯片半导体: 2-6 tags', tags2.length >= 2 && tags2.length <= 6);
  check('AI/芯片半导体: 含"AI科技"', tags2.some(t => t === 'AI科技'));
  check('AI/芯片半导体: 含"半导体"', tags2.some(t => t === '半导体'));
  check('AI/芯片半导体: 含"概念"', tags2.some(t => t === '概念'));

  return true;
}

// ---------------------------------------------------------------------------
// Phase 2: static fallback -- structural assertions on the source
// ---------------------------------------------------------------------------
function staticTest() {
  console.log('\n=== Phase 2: static (source assertions) ===\n');

  if (!fs.existsSync(NAV_PANEL)) {
    console.log('  SKIP  nav-panel.js not found:', NAV_PANEL);
    return;
  }

  const src = fs.readFileSync(NAV_PANEL, 'utf8');

  check('static: deriveBoardTagsAfterMove 函数声明存在',
    /function\s+deriveBoardTagsAfterMove\s*\(/.test(src));

  check('static: _PRIMARY_SHORT_TAG 存在',
    /const\s+_PRIMARY_SHORT_TAG\s*=/.test(src));

  check('static: _SECONDARY_SHORT_TAG 存在',
    /const\s+_SECONDARY_SHORT_TAG\s*=/.test(src));

  check('static: moveBoardToSubCat 调用 board.tags = deriveBoardTagsAfterMove',
    /board\.tags\s*=\s*deriveBoardTagsAfterMove\(/.test(src));

  // Check that the lookup tables contain our target keys
  check('static: _PRIMARY_SHORT_TAG 含"新能源与电力设备"',
    /'新能源与电力设备'\s*:\s*'新能源'/.test(src) ||
    /"新能源与电力设备"\s*:\s*"新能源"/.test(src));

  check('static: _SECONDARY_SHORT_TAG 含"光伏产业链"',
    /'光伏产业链'\s*:\s*'光伏'/.test(src) ||
    /"光伏产业链"\s*:\s*"光伏"/.test(src));

  check('static: _PRIMARY_SHORT_TAG 含"AI 与数字科技"',
    /'AI 与数字科技'\s*:\s*'AI科技'/.test(src) ||
    /"AI 与数字科技"\s*:\s*"AI科技"/.test(src));

  check('static: _SECONDARY_SHORT_TAG 含"芯片半导体"',
    /'芯片半导体'\s*:\s*'半导体'/.test(src) ||
    /"芯片半导体"\s*:\s*"半导体"/.test(src));

  check('static: _DOMAIN_TAGS 含"芯片半导体" → 半导体',
    /'芯片半导体'\s*:\s*\[\s*'半导体'/.test(src) ||
    /"芯片半导体"\s*:\s*\[\s*"半导体"/.test(src));
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
console.log('Frontend smoke test --', new Date().toISOString());
console.log('Node', process.version);

dynamicTest();
staticTest();

console.log('\n=== Summary ===');
console.log(`  ${pass} passed, ${fail} failed`);

if (fail > 0) {
  console.log('\nRESULT: FAIL');
  process.exit(1);
} else {
  console.log('\nRESULT: PASS');
  process.exit(0);
}
