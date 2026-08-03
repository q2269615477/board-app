// ===== 图表主题控制 =====
(function () {
  'use strict';

  var STORAGE_KEY = 'board-app.chart-theme';
  function getChartStyles(theme) {
    var light = theme === 'light';
    var up = light ? '#f23645' : '#ef5350';
    var down = light ? '#089981' : '#26a69a';
    var styles = {
      candle: { bar: {
        upColor: up, upBorderColor: up, upWickColor: up,
        downColor: down, downBorderColor: down, downWickColor: down
      }},
      indicator: { bars: [{
        upColor: light ? 'rgba(242,54,69,0.78)' : 'rgba(239,83,80,0.7)',
        downColor: light ? 'rgba(8,153,129,0.78)' : 'rgba(38,166,154,0.7)'
      }]}
    };
    if (light) {
      // TradingView 风格：纯白画布、极浅网格、深色坐标文字。
      styles.grid = {
        horizontal: { color: '#f0f3fa', size: 1 },
        vertical: { color: '#f0f3fa', size: 1 }
      };
      styles.xAxis = {
        axisLine: { color: '#e0e3eb', size: 1 },
        tickLine: { color: '#e0e3eb', size: 1 },
        tickText: { color: '#434651' }
      };
      styles.yAxis = {
        axisLine: { color: '#e0e3eb', size: 1 },
        tickLine: { color: '#e0e3eb', size: 1 },
        tickText: { color: '#434651' }
      };
      styles.separator = {
        color: '#e0e3eb',
        activeBackgroundColor: '#c7ccd6'
      };
      styles.crosshair = {
        horizontal: { line: { color: '#9598a1' } },
        vertical: { line: { color: '#9598a1' } }
      };
    }
    return styles;
  }

  function normalizeTheme(theme) { return theme === 'light' ? 'light' : 'dark'; }
  function readStoredTheme() {
    try { return normalizeTheme(localStorage.getItem(STORAGE_KEY)); }
    catch (e) { return 'dark'; }
  }
  function setOuterTheme(theme) {
    var next = normalizeTheme(theme);
    document.documentElement.setAttribute('data-board-theme', next);
    if (document.body) document.body.setAttribute('data-board-theme', next);
  }
  function updateThemeButton(theme) {
    var button = document.getElementById('board-theme-toggle');
    if (!button) return;
    var next = normalizeTheme(theme);
    var target = next === 'dark' ? 'light' : 'dark';
    button.textContent = next === 'dark' ? '☼' : '☾';
    button.title = target === 'light' ? '切换到浅色主题' : '切换到深色主题';
    button.setAttribute('aria-label', button.title);
    button.dataset.theme = next;
  }
  function applyBoardChartTheme(theme, persist) {
    var next = normalizeTheme(theme);
    if (persist !== false) {
      try { localStorage.setItem(STORAGE_KEY, next); } catch (e) {}
    }
    setOuterTheme(next);
    var chart = window.pro;
    if (chart && typeof chart.setTheme === 'function') {
      try {
        chart.setTheme(next);
        if (typeof chart.setStyles === 'function') chart.setStyles(getChartStyles(next));
        if (typeof chart.getTheme === 'function') next = normalizeTheme(chart.getTheme());
      } catch (e) { console.warn('[Theme] KLineChartPro 主题切换失败:', e); }
    }
    updateThemeButton(next);
    try { window.dispatchEvent(new CustomEvent('chart-theme-change', { detail: { theme: next } })); }
    catch (e) {}
    return next;
  }
  function mountThemeButton() {
    // 顶部工具栏不受图表宽度和右侧会话面板影响，按钮始终可见。
    var bar = document.getElementById('toolbar');
    if (!bar || bar.querySelector('#board-theme-toggle')) return;
    var button = document.createElement('button');
    button.id = 'board-theme-toggle';
    button.type = 'button';
    button.className = 'board-theme-toggle';
    button.addEventListener('click', function () {
      var current = readStoredTheme();
      applyBoardChartTheme(current === 'dark' ? 'light' : 'dark');
    });
    bar.appendChild(button);
    updateThemeButton(readStoredTheme());
  }
  function initChartTheme() {
    applyBoardChartTheme(readStoredTheme(), false);
    var container = document.getElementById('pro-container');
    if (container && typeof MutationObserver !== 'undefined') {
      new MutationObserver(mountThemeButton).observe(container, { childList: true, subtree: true });
    }
    mountThemeButton();
  }
  window.getBoardChartTheme = readStoredTheme;
  window.applyBoardChartTheme = applyBoardChartTheme;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initChartTheme);
  else initChartTheme();
})();

// ===== 9. 分割条 =====
let drg=false;
document.getElementById('splitter').onmousedown=e=>{drg=true;e.preventDefault();};
document.onmousemove=e=>{if(!drg)return;const w=Math.max(200,Math.min(400,e.clientX));document.getElementById('nav-panel').style.width=w+'px';};
document.onmouseup=()=>{drg=false;};

// ===== 启动前拉取前端配置（fire-and-forget，失败静默沿用默认值） =====
fetch('/api/system/frontend-config')
  .then(r => r.json())
  .then(data => { window.__FRONTEND_CFG = data; })
  .catch(() => { /* 忽略，前端继续使用已有常量默认值 */ });

// ===== 启动（分类 → 拼音索引 + Pro 常驻） =====
// 1. 加载分析历史
loadAnalysisHistory();
renderAnalysisHistory();
// 2. 检测WorkBuddy Hook
detectWorkBuddyHook();
// 3. 加载拼音首字母数据
async function loadPinyinData() {
  try {
    const r = await fetch(API + '/api/pinyin/all');
    const resp = await r.json();
    window._sm = resp.data || [];
  } catch(e) { window._sm = []; }
}
// 6. 分类与拼音索引互不依赖，并行拉取以缩短左栏首屏时间。
Promise.all([loadPinyinData(), loadClassification()]).then(() => {
  console.log('[启动] 板块分类加载完成（含拼音排序）');
});
// 顶部导航栏由 index-bar.js 自启，避免在 DOMContentLoaded 前后重复渲染和拉取。

// 页面加载后立即初始化 Pro（仅调度一次）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(initPro, 100));
} else {
  setTimeout(initPro, 100);
}

// ===== MCP 集成初始化 =====
let mcpClient, klineIntegrator, cacheManager;

function initMCP() {
  try {
    if (typeof MCPClient === 'function') {
      mcpClient = new MCPClient();
      if (typeof mcpClient.connect === 'function') mcpClient.connect();
      if (typeof CacheRefreshManager === 'function') {
        cacheManager = new CacheRefreshManager(mcpClient);
        if (typeof cacheManager.start === 'function') cacheManager.start();
      }
      console.log('[MCP] 初始化完成');
    }
  } catch(e) { console.warn('[MCP] 初始化静操跳过:', e); }
}

// 在Pro初始化后初始化MCP
setTimeout(initMCP, 500);

// ===== 统一选择链路初始化 (阶段二) =====
// 在 ChartController 准备好后（依赖 initPro 完成后 window.pro 存在）
function initUnifiedSelector() {
  try {
    if (typeof window.ChartController !== 'undefined') {
      window.ChartController.init();
      console.log('[启动] ChartController 已初始化');
    }
    // search-panel.js 仍然是当前生产搜索绑定；SearchController 是下一阶段迁移壳。
    // 避免两套控制器同时监听 #search-input，导致重复请求和回车选择竞争。
  } catch(e) { console.warn('[启动] 统一选择链路初始化异常:', e); }
}

// 延迟等待 Pro 和 UnifiedSelector 就绪
setTimeout(initUnifiedSelector, 800);
