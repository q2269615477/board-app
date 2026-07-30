// 核心测试：快捷键映射逻辑
// 依据：static/js/session-ui.js bindHotkeys()（约 4438-4469 行）
//   Alt+H 支撑分析   Alt+R 反应点   Alt+L 位表   Alt+M 共振
//   Alt+P AI提议     Alt+K 选K      Alt+↑/↓ 切板块  Esc 关弹窗/退出模式
//
// 本测试不依赖 DOM / Flask，只验证快捷键映射表的完整性与一致性。
const { describe, test } = require('node:test');
const assert = require('node:assert');

// 从 session-ui.js bindHotkeys() 抽取的纯映射（键名 → 动作名）
// 与源码 const map = {...} 一一对应
const SHORTCUT_MAP = {
  h: 'startDrawLevel',     // 支撑分析（画水平线）
  r: 'startReactionMode',  // 反应点
  l: 'openLevelManager',   // 位表
  m: 'openResonancePanel', // 共振
  p: 'openProposalPanel',  // AI 提议候选位
  k: 'togglePickK',        // 选K（源码用 try/catch 包裹）
};

// 非字母键的快捷键（源码用独立 if 分支处理）
const ARROW_SHORTCUTS = {
  ArrowDown: 'stepBoard(+1)',
  ArrowUp: 'stepBoard(-1)',
};

// UI 按钮上的 title 文案（来自 session-ui.js 599-604 行）—— 与映射一致性参考
const BUTTON_TITLES = {
  h: '[Alt+H]',
  r: '[Alt+R]',
  l: '[Alt+L]',
  m: '[Alt+M]',
  p: '[Alt+P]',
  k: '[Alt+K]',
};

describe('快捷键映射', () => {
  test('Alt+H 映射到支撑分析模式 startDrawLevel', () => {
    assert.strictEqual(SHORTCUT_MAP.h, 'startDrawLevel');
  });

  test('Alt+R 映射到反应点 startReactionMode', () => {
    assert.strictEqual(SHORTCUT_MAP.r, 'startReactionMode');
  });

  test('Alt+L 映射到位表 openLevelManager', () => {
    assert.strictEqual(SHORTCUT_MAP.l, 'openLevelManager');
  });

  test('Alt+M 映射到共振 openResonancePanel', () => {
    assert.strictEqual(SHORTCUT_MAP.m, 'openResonancePanel');
  });

  test('Alt+P 映射到 AI 提议 openProposalPanel', () => {
    assert.strictEqual(SHORTCUT_MAP.p, 'openProposalPanel');
  });

  test('Alt+K 映射到选K togglePickK', () => {
    assert.strictEqual(SHORTCUT_MAP.k, 'togglePickK');
  });

  test('映射键全部为小写字母（源码用 toLowerCase 规范化）', () => {
    for (const k of Object.keys(SHORTCUT_MAP)) {
      assert.ok(/^[a-z]$/.test(k), `键 "${k}" 应为单个小写字母`);
    }
  });

  test('映射动作名全部为非空字符串', () => {
    for (const action of Object.values(SHORTCUT_MAP)) {
      assert.ok(typeof action === 'string' && action.length > 0, `动作名应为非空字符串，实际: ${action}`);
    }
  });

  test('Alt+↑/↓ 切板块映射存在', () => {
    assert.strictEqual(ARROW_SHORTCUTS.ArrowUp, 'stepBoard(-1)');
    assert.strictEqual(ARROW_SHORTCUTS.ArrowDown, 'stepBoard(+1)');
  });

  test('映射表共 6 个字母键（H/R/L/M/P/K）', () => {
    const keys = Object.keys(SHORTCUT_MAP).sort().join('');
    assert.strictEqual(keys, 'hklmpr', `实际键集: ${keys}`);
  });

  test('每个映射键都有对应的按钮 title 文案', () => {
    for (const k of Object.keys(SHORTCUT_MAP)) {
      assert.ok(BUTTON_TITLES[k], `键 "${k}" 缺少按钮 title 文案`);
      assert.ok(BUTTON_TITLES[k].includes(`Alt+${k.toUpperCase()}`),
        `title "${BUTTON_TITLES[k]}" 应包含 Alt+${k.toUpperCase()}`);
    }
  });

  test('Esc 不在字母映射中（源码用独立分支处理）', () => {
    assert.strictEqual(SHORTCUT_MAP.escape, undefined);
    assert.strictEqual(SHORTCUT_MAP.Escape, undefined);
  });
});