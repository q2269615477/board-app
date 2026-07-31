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
// 6. 加载分类
loadPinyinData().then(() => loadClassification()).then(() => {
  console.log('[启动] 板块分类加载完成（含拼音排序）');
});
// 4. 渲染与启动顶部导航栏 3 秒高频高亮轮询
if (typeof renderIndexBar === 'function') renderIndexBar();
if (typeof _startIdxFallback === 'function') _startIdxFallback();

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
