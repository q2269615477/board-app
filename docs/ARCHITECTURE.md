# Current Runtime Note (2026-07-30)

The legacy one-file frontend `static/index-local.html` / `static/index-local-fixed.html` has been removed. The supported runtime entry is `static/index.html` plus modular files under `static/js/`. For active development constraints, see `docs/development-constraints.md` and `技术文档/项目清理与开发约束.md`. Older sections below are historical unless they match those constraints.

---

# panel-ai-trader — 技术架构文档  
> **生成时间**：2026-07-02 · **维护者**：Admin（运城市委编办资深AI量化分析师）  
> **代码位置（历史记录）**：`D:\.workbuddy\2026-06-27-21-35-52\board-app\`（现应以仓库根目录为准，路径可能已变化）  
> **用途**：面向其他模型 / 开发者的全量审核与协作底档

---

## 目录

1. [项目定位与迭代轨迹](#1-项目定位与迭代轨迹)
2. [整体架构总览（分层+数据流）](#2-整体架构总览分层数据流)
3. [目录结构与文件职责清单](#3-目录结构与文件职责清单)
4. [后端核心模块详解](#4-后端核心模块详解)
   - [4.1 `app.py` — Flask主入口](#41-apppy--flask主入口)
   - [4.2 `core/` — 基础设施层](#42-core--基础设施层)
   - [4.3 `data/` — 数据访问层](#43-data--数据访问层)
   - [4.4 `services/` — 业务服务层](#44-services--业务服务层)
   - [4.5 `api/` — 路由蓝图层](#45-api--路由蓝图层)
5. [前端渲染与WorkBuddy CSP绕过](#5-前端渲染与workbuddy-csp绕过)
6. [MCP与对话驱动分析协议](#6-mcp与对话驱动分析协议)
7. [Skill Dispatcher机制](#7-skill-dispatcher机制)
8. [周扫描器 board_scanner.py 设计](#8-周扫描器-board_scannerpy-设计)
9. [关键配置速查表](#9-关键配置速查表)
10. [已知限制与未决TODO](#10-已知限制与未决todo)
11. [与 vibe-trading 的差异化对比](#11-与-vibe-trading-的差异化对比)
12. [调试与排查指南](#12-调试与排查指南)

---

## 1. 项目定位与迭代轨迹

**定位**：A股板块级看盘与智能分析面板。核心使命是把"900+东方财富BK板块的K线图+实时行情+AI分析"装进 WorkBuddy 的本地浏览器窗口。

**已完成的重要迭代**：

| 阶段 | 时间 | 内容 |
|------|------|------|
| V1 起步 | 2026-06 前 | 单体 `app.py`，直接读东方财富 V43 数据文件 |
| V2 分层 | 2026-06-27 | 拆出 `core/` `data/` `services/` `api/` 四层；Blueprint 注册 |
| V3 QMT 直连 | 2026-06-30 | 双端口 `58600`（公式/策略）+ `58610`（行情引擎）；MiniQMT 后台守护 |
| V3 TDZ修复 | 2026-07-02 | 63处重复 `let` 声明 → 赋值；`classification_data` 双层嵌套解包；WorkBuddy 面板恢复正常 |
| V3 MCP | 2026-07-02 | Node.js MCP Bridge；`get_panel_context` + `dispatch_skill_result` 注册到 WorkBuddy |
| V3 Scanner ⭐新 | 2026-07-02 | `board_scanner.py` —— 板块批量扫描/支撑阻力识别/周报告/推送 |

---

## 2. 整体架构总览（分层+数据流）

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         WorkBuddy 内置浏览器 (902KB HTML)                │
│  ┌────────────┐   ┌────────────┐   ┌──────────────┐                    │
│  │ KLineChart │   │  IndexBar  │   │ SkillResult  │                    │
│  │    Pro     │   │  指数快栏  │   │   Overlay    │                    │
│  └──────┬─────┘   └──────┬─────┘   └──────┬───────┘                    │
└─────────┼────────────────┼────────────────┼────────────────────────────┘
          │ fetch()        │ SSE            │ skill_result event
          ▼                ▼                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  Flask 后端 :5000  (app.py)                              │
│                                                                          │
│   ┌─────────────────────┐  ┌─────────────────────┐                      │
│   │ Blueprint: kline    │  │ Blueprint: system   │                      │
│   │ /api/kline/...      │  │ /api/events (SSE)   │                      │
│   │ /api/stock/data/... │  │ /api/cache/*        │                      │
│   └─────────┬───────────┘  └─────────┬───────────┘                      │
│             │                        │                                   │
│   ┌─────────┴────────────────────────┴────────────────────┐             │
│   │              services / 业务逻辑层                     │             │
│   │  kline_service   board_service   search_service       │             │
│   │  skill_dispatcher (EventBus publish → SSE push)      │             │
│   └─────────┬────────────────────────┬────────────────────┘             │
│             │                        │                                   │
│   ┌─────────┴────────────────────────┴────────────────────┐             │
│   │              data / 数据访问层                         │             │
│   │  sqlite_repo (kline.db)    qmt_client (58610)         │             │
│   │  akshare_client (免费源)   CSV 原始数据               │             │
│   └─────────┬────────────────────────┬────────────────────┘             │
│             │                        │                                   │
│   ┌─────────┴────────────────────────┴────────────────────┐             │
│   │              core / 基础设施层                         │             │
│   │  config   cache (TTL+LRU)   context_bridge   events    │             │
│   │  lifecycle (QMT验证 + MiniQMT守护 + 预热)             │             │
│   └───────────────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────────────┘
```

> **最关键的数据路径**（针对 WorkBuddy CSP 限制的解法）：  
> **服务端渲染时把初始数据直接嵌入 HTML `window.__init_data__`** → 前端 `loadClassification()` 和 `getHistoryKLineData()` 优先读取嵌入数据 → **零网络请求，零脚本外联，完全绕过 CSP**。

---

## 3. 目录结构与文件职责清单

### 3.1 根目录入口文件

| 文件 | 职责 |
|------|------|
| `app.py` | Flask 启动、数据内嵌、路由总注册 |
| `mcp_board_bridge.js` | Node.js MCP 桥接器：stdin/stdout JSON-RPC 2.0 ↔ HTTP → board-app |
| `board_scanner.py` | ⭐ 周扫描器：900+ 板块 SR 识别、批量扫描、报告输出、SSE 推送 |
| `port_manager.py` | 端口 alive 检测与占用处理 |
| `proxy_bypass.py` | 本地连接绕过系统代理 |
| `rate_limiter.py` | API 频率限制 |
| `signals_store.py` | AI 信号 JSON 持久化 |
| `data_loader.py` | V43 CSV 数据 + QMT 数据的统一加载函数 |
| `data_update_manager.py` | 定时全量/增量更新调度 |
| `cache_manager.py` | 旧缓存管理器（已被 `core/cache.py` 替代） |

### 3.2 `core/`

| 文件 | 职责 |
|------|------|
| `config.py` | 集中路径、端口、预热目标 |
| `cache.py` | 线程安全 TTL 缓存 + LRU 淘汰 + 后台清理线程 |
| `context_bridge.py` | 面板上下文读写（Lock 保护） |
| `events.py` | EventBus 单例：subscribe / publish / push_sse / get_sse_events |
| `lifecycle.py` | 应用启动全流程 + MiniQMT 守护线程 |

### 3.3 `data/`

| 文件/目录 | 职责 |
|------|------|
| `sqlite_repo.py` | SQLite DAO：WAL 模式、kline 读写、meta、ledger |
| `qmt_client.py` | 双端口 QMT 客户端封装 |
| `akshare_client.py` | AKShare 免费数据备选 |
| `kline.db` | SQLite 数据库（K线 + 元数据 + 台账） |
| `行业板块K线数据/` | 东方财富 V43 行业 CSV 源文件 |
| `概念板块K线数据/` | 东方财富 V43 概念 CSV 源文件 |
| `个股K线缓存/` / `港股K线缓存/` | 本地文件缓存 |

### 3.4 `services/`

| 文件 | 职责 |
|------|------|
| `kline_service.py` | 主 K 线加载（QMT / SQLite / 板块 CSV）；线程池 + loading 信号 |
| `board_service.py` | 板块列表 / 涨跌幅计算 |
| `search_service.py` | 名称/代码搜索 |
| `skill_dispatcher.py` | 技能结果解析 + EventBus.publish('skill_result') |

### 3.5 `api/` (Blueprint)

| 文件 | URL 前缀 | 关键路由 |
|------|------|------|
| `__init__.py` | — | `register_routes(app)` |
| `kline_routes.py` | `/api/kline` | `GET /<type>/<code>` / `GET /spot/<type>/<code>` |
| `board_routes.py` | `/api/board` | 板块分类、成分股、涨跌幅 |
| `search_routes.py` | `/api/search` | `GET /api/search?q=` |
| `signal_ai_routes.py` | `/api/ai` | AI 分析触发 + MCP 工具注册 |
| `system_routes.py` | `/api` | cache、update、events SSE、classification |
| `ctx_route.py` | `/api/ctx` | GET/POST 面板上下文 |
| `skill_route.py` | `/api/skill` | POST 结果推送、GET 前端轮询 |

### 3.6 `static/`

| 文件 | 用途 |
|------|------|
| `index-local.html` | 面板主页面（所有JS/CSS内联，902KB） |
| `board_classification.json` | 14分类 / 990板块本体 |
| `js/klinecharts.min.js` | KLineChart v9.6.0（192KB） |
| `js/klinecharts-pro.umd.js` | KLineChart Pro（176KB） |
| `js/klinecharts-pro.css` | Pro 样式（36KB） |

### 3.7 `docs/`

| 文件 | 用途 |
|------|------|
| `ARCHITECTURE.md` | 本文档（全量技术底档） |

---

## 4. 后端核心模块详解

### 4.1 `app.py`（主入口）

**职责**：
1. 启动日志 + 依赖检查
2. `_bootstrap()` → `start_app()` 触发预热
3. `register_routes(app)` 注册 7 个 Blueprint
4. **`/` 路由**：动态构建 HTML，注入 `window.__init_data__`，绕过 WorkBuddy CSP
5. `PORT=5000` 运行

```python
@app.route('/')
def index():
    template = (static_dir / 'index-local.html').read_text(encoding='utf-8')
    
    # 1. 提取 classification（双层嵌套解包）
    _cats = classification_data.get('categories', []) if isinstance(classification_data, dict) else classification_data
    
    # 2. 嵌入初始 K 线
    init_data = {
        'categories': _cats,  # 单层数组
        'defaultKline': kline_data,  # 上证指数日线
        'defaultSymbol': {'ticker': 'sh000001', 'name': '上证指数', 'type': 'index'}
    }
    embed = f'<script>\nwindow.__init_data__ = {json.dumps(init_data, ensure_ascii=False)};\n</script>\n'
    template = template.replace('</head>', embed + '</head>', 1)
    return Response(template, mimetype='text/html')
```

> **为什么这样做**：WorkBuddy 的浏览器 CSP 阻止所有 `<script src=...>` 外联 + 阻止 `fetch('http://localhost:5000/...')`。服务端内嵌是唯一解。

### 4.2 `core/config.py`

**集中配置**（路径、端口、预热列表、缓存参数）。主要字段：

| 字段 | 值 |
|------|------|
| `FLASK_HOST` | `127.0.0.1` |
| `FLASK_PORT` | `5000` |
| `QMT_PYTHON_PATH` | `...\bin.x4\python.exe` |
| `QMT_DIR` | QMT 安装路径 |
| `STATIC_DIR` | `static` |
| `PREWARM_TARGETS` | 12个顶部指数列表 |
| `BOARD_CHG_REFRESH_INTERVAL` | `60` (秒) |
| `CACHE_DEFAULT_TTL` | `600` (秒) |

### 4.3 `core/cache.py`

线程安全内存缓存（`CacheManager`）：
- `get/set/delete/clear`
- TTL 自动过期
- LRU 拒绝缓存空数据 (`_is_stale_empty`)
- 后台清理线程（每 `CACHE_CLEAN_INTERVAL` 秒）

### 4.4 `core/context_bridge.py`

```python
_ctx = {'code': 'sh000001', 'type': 'index', 'period': 'daily', 'name': '上证指数', 'range': ''}
_lock = Lock()

def update_context(code, type_, period='daily', name='', range_=''): ...
def get_context() -> dict: ...
```

面板 → MCP Tool 获取当前上下文的通道。

### 4.5 `core/lifecycle.py`

启动流程：
1. `_check_dependencies()` → 文件完整性检查
2. `_start_qmt()` → 验证 58600 RPC 可用性
3. `_start_background_services()`：
   - `_board_chg_loop()`：60秒增量更新板块涨跌幅
   - `_prewarm_indices()`：预热 12 个顶部指数
   - `_start_miniquote()` + `_miniquote_watchdog_loop()`：58610 MiniQMT 守护

**关键二进制路径**：
```python
QMT_MINI_BINARY = r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\XtMiniQmt.exe'
```

### 4.6 `core/events.py`

```python
class EventBus:
    subscribe(event_type, callback)
    publish(event_type, data)          # 同步通知所有监听器
    push_sse(event_type, data)         # 异步推入 SSE 队列
    get_sse_events(timeout=0.1)        # SSE 消费者获取
```

单例模式：`get_event_bus()`。替代了旧的 `_ai_sse_event_queue + _ai_sse_event_lock`。

### 4.7 `data/sqlite_repo.py`

SQLite 数据访问对象（WAL 模式 + 索引）：
- `read_kline(code, period) → DataFrame | None`
- `save_kline(code, period, df)` / `upsert_kline(...)`
- `search_boards(query)` / `get_board_constituents(code)`
- 表：`kline`, `kline_meta`, `stock_ledger`, `board_cache`

### 4.8 `data/qmt_client.py`

双端口客户端：
- `rpc_init(uri="127.0.0.1:58600")` — 策略/公式引擎端口
- `get_minute_kline(code, type_, period)` — 通过 58610 获取分钟线
- `get_daily_local(qmt_code)` — 本地日线数据
- `to_qmt_code(panel_code, type_)` — BK1499 → BK1499 映射

### 4.9 `services/kline_service.py`

主 K 线加载服务（KLineService 单例 + ThreadPool）：
- 分钟线 → QMT 58610（15m/30m 需 resample from 1m）
- 日线 → QMT 58610 优先，失败回退 SQLite
- 周/月/季/年 → `_load_resample` from daily
- 板块行业/概念 → SQLite 或 CSV

**响应格式**：
```json
{
  "data": [{"timestamp": 1719763200000, "open": 3066.34, "high": 3098.1, "low": 3066.34, "close": 3085.20, "volume": 292470208}, ...],
  "count": 3480, "last_date": "2026-07-02",
  "source": "cache|load|pending"
}
```

### 4.10 `services/skill_dispatcher.py`

```python
def dispatch_skill_result(board_code, skill_id, result_dict):
    """发布 'skill_result' 事件 → EventBus → SSE → 前端 overlay"""
    event_bus = get_event_bus()
    event_bus.publish('skill_result', {
        'board_code': board_code,
        'skill_id': skill_id,
        'overlay': overlay,   # KLineChart Pro 标注数据
        'report': report,     # Markdown 文字报告
    })
```

### 4.11 `api/kline_routes.py`

```
GET /api/kline/<type>/<code>?period=daily&timeout=15
     type ∈ {index, stock, industry, concept, hk_index}
     限流：60 QPS/IP；timeout 后返回 408

GET /api/spot/<type>/<code>         # 实时报价（30秒轮询）
GET /api/stock/data/<code>          # 个股V43数据
GET /api/board/changes              # 涨跌幅缓存
```

### 4.12 `api/system_routes.py`

```
GET  /api/cache/status              # 缓存健康
POST /api/cache/clear               # 清缓存
GET  /api/update/status             # 更新状态
POST /api/update/boards             # 手动触发板块更新
GET  /api/events                    # SSE 数据更新+AI事件
GET  /api/classification/load       # 加载板块分类
POST /api/classification/save       # 保存用户自定义分类
```

### 4.13 `api/ctx_route.py`

```
GET  /api/ctx   → 返回 {code, type, period, name, range}
POST /api/ctx   → 更新面板上下文（由前端 selectBoard 调用）
```

### 4.14 `api/skill_route.py`

```
POST /api/skill/result  → Skill 分析结果推送（overlay + report）
GET  /api/skill/result/<board_code>  → 前端轮询（预留）
```

---

## 5. 前端渲染与 WorkBuddy CSP 绕过

### 问题背景

WorkBudy 内置浏览器的 CSP：
- 阻止所有 `<script src=cdn...>` 外联
- 阻止 `fetch('http://localhost:5000/...')` 
- 仅允许：内联 `<script>` + 同域 curl

### 解法：全量内联 + 数据预注入

**Step 1**：CDN JS/CSS 下载到本地 `static/js/`：
- `klinecharts.min.js` (192KB)
- `klinecharts-pro.umd.js` (176KB)
- `klinecharts-pro.css` (36KB)

**Step 2**：`static/index-local.html` 把所有外联 → 内联。

**Step 3**：Flask `/` 路由在 `</head>` 前插入 `<script>window.__init_data__ = {...}</script>`。

**Step 4**：前端关键加载入口：

```javascript
// loadClassification() — 优先使用嵌入数据
if (window.__init_data__ && window.__init_data__.categories) {
    store.categoryData = window.__init_data__.categories;
    renderNav();
    return;          // ← 不 fetch，直接渲染
}

// getHistoryKLineData() — 默认标的用嵌入数据
if (window.__init_data__ && window.__init_data__.defaultKline &&
    symbol.ticker === window.__init_data__.defaultSymbol.ticker && p === 'daily') {
    return window.__init_data__.defaultKline;   // ← 不 fetch
}
```

**Step 5**：非默认标的再走 `fetch('/api/kline/...')`，此时 WorkBuddy 允许（因为 fetch 的 URL 是同域页面路径，除 `/` 外的其他路径）。

### 前端脚本结构

HTML 内含 3 个 `<script>` 块：
1. `#0` — KLineChart v9.6.0 库（188KB）
2. `#1` — KLineChart Pro 扩展（171KB）
3. `#2` — 应用主脚本（49.9KB）：
   - `BoardDatafeed` 类：实现了 `searchSymbols()` / `getHistoryKLineData()` / `subscribe()` / `unsubscribe()`
   - `store` 全局状态：`{ activeCat, selected, categoryData, _seq, _skills }`
   - `initPro()`：KLineChart Pro 初始化（带 CDN 重试 + 失败后用户提示）
   - `selectBoard(name, code, type, ev)`：切换板块 → 暴露 `window.__board_ctx` → POST `/api/ctx`
   - `loadClassification()` / `renderNav()` / `renderIndexBar()` 等渲染函数
   - SSE 监听：`sources = new EventSource('/api/events')`
   - `skill_result` 处理：创建 overlay + 浮动报告面板

### 前端关键函数索引

| 函数 | 所在脚本# | 职责 |
|------|------|------|
| `loadClassification` | #2 | 加载板块分类（优先嵌入） |
| `renderNav` | #2 | 渲染左侧导航栏 |
| `toggleCat` | #2 | 分类展开/折叠 |
| `selectBoard` | #2 | 切换板块（暴露上下文） |
| `initPro` | #2 | KLineChart Pro 初始化 |
| `forceReloadPro` | #2 | 失败后用户重试 |
| `renderIndexBar` | #2 | 顶部指数栏 |
| `periodToApi` | #2 | 周期参数映射 |
| `getHistoryKLineData` | #2 (BoardDatafeed) | 拉取 K 线（默认标的优先嵌入） |
| `searchSymbols` | #2 (BoardDatafeed) | 全局搜索 |
| `dispatch_skill_result` 入口 | #2 | SSE skill_result 处理 |

---

## 6. MCP 与对话驱动分析协议

### MCP 桥接器 (`mcp_board_bridge.js`)

Node.js 父子进程桥接器，把 board-app HTTP REST 包装为标准 MCP JSON-RPC 2.0 协议：

```
WorkBuddy (MCP client)
    ↓  stdin (JSON-RPC 2.0)
mcp_board_bridge.js
    ↑  stdout (JSON-RPC 2.0)
    ↓  HTTP requests
Flask :5000
```

### 已注册的 MCP Tools

| Tool 名 | 方法 | 说明 |
|---------|------|------|
| `mcp__board-app__get_panel_context` | GET | 获取当前面板标的（code/type/period/name/range） |
| `mcp__board-app__dispatch_skill_result` | POST | 把分析结果推送到面板（overlay + report） |
| `mcp__board-app__get_kline` | GET | 获取任意标的 K 线 JSON |
| `mcp__board-app__analyze_board` | POST | （预留）批量分析触发 |

### 上下文更新面板 → MCP Tool

用户切换板块时：
```javascript
window.__board_ctx = { code, type, name, period };
fetch('/api/ctx', {method:'POST', body: JSON.stringify(window.__board_ctx)});
```

MCP Tool `get_panel_context` 调用 `GET /api/ctx`，获取 JSON。

### MCP Tool → 面板

AI 调 `dispatch_skill_result` → HTTP POST `/api/skill/result` → EventBus.publish → SSE push → 前端 `skill_result` 事件 → 创建 overlay + 文字面板。

### WorkBuddy MCP 注册

`~/.workbuddy/mcp.json`：
```json
{
  "board-app": {
    "command": "node.exe",
    "args": ["mcp_board_bridge.js"],
    "cwd": "D:/.workbuddy/2026-06-27-21-35-52/board-app",
    "env": {},
    "disabled": false
  }
}
```

在 WorkBuddy 连接器管理中点"Trust"启用。

---

## 7. Skill Dispatcher 机制

### 触发路径

```
WorkBuddy 对话
  → MCP call_tool("analyze", {"skill":"support_resistance", "code":"BK1499"})
    → mcp_board_bridge.js HTTP POST /api/skill/result
      → api/skill_route.py: ctx_post()
        → services/skill_dispatcher.py: dispatch()
          → core/events.py: event_bus.publish('skill_result', {...})
            → system_routes.py: SSE event_stream() loop
              → 前端 EventSource listen 'skill_result'
                → createOverlay(data.overlay) + 浮动报告面板
```

### 返回格式

```json
{
  "ok": true,
  "message": "skill_result dispatched",
  "skill_id": "support_resistance",
  "board_code": "BK1499"
}
```

SSE event `data` 字段包含 `board_code`、`skill_id`、`overlay`（KLineChart Pro 标注）、`report`（Markdown）。

---

## 8. 周扫描器 board_scanner.py 设计

### 功能

- 遍历 `static/board_classification.json` 900+ 板块
- 调用本地 `GET /api/kline/industry/{code}` 获取日线
- 计算支撑/阻力位（ATR 聚类）
- 判断当前价格是否临近关键位（< 3% 阈值）
- 按临近程度评分排序
- Markdown 报告 + SSE 推送面板

### 算法

```
输入：N 个板块 K 线（最近 SR_N_BARS=60 根）
对每个板块：
    1. ATR(14) 计算波动率
    2. 局部高/低点（5根K线 rolling max/min）
    3. ATR 容差聚类（价格差异 < ATR*0.5 的合并）
    4. 聚类中心 = 交易量加权平均
    5. 取距离当前价最近的支撑/阻力
    6. 距离 < 3% → 标记为"临近"
输出：按评分排序的 TOP-30 列表
```

### 数据结构

```python
@dataclass
class ScanResult:
    code, name, category, type_
    current_price, price_date
    nearest_support, nearest_resistance
    dist_to_support_pct, dist_to_resistance_pct
    near_support, near_resistance, near_any_level
    score  # 0-100
```

### 配置常量

```python
BASE_URL = "http://127.0.0.1:5000"
REQUEST_TIMEOUT = 15
RATE_LIMIT_SEC = 0.05   # 20 QPS
SR_N_BARS = 60
SR_ATR_MULTIPLIER = 1.5
SR_CLUSTER_ATR = 0.5
PROXIMITY_PCT = 3.0
```

### 调用方法

```bash
# 手动运行扫描
python board_scanner.py

# WorkBuddy automation 周日 02:00 运行
# 或通过 MCP Tool trigger
```

### 输出

- 文件：`reports/weekly_scan_YYYY-MM-DD.md`
- 推送：POST `/api/skill/result`（summary + report）

### 独立运行（不影响面板架构）

- 不修改任何现有 .py 文件
- 仅依赖 Flask 已有的 `/api/kline/industry/{code}` 和 `board_classification.json`
- 可作为 WorkBuddy automation 定时调度

---

## 9. 关键配置速查表

| 参数 | 值 | 位置 |
|------|------|------|
| Flask 主机 | `127.0.0.1` | `core/config.py` |
| Flask 端口 | `5000` | `core/config.py` |
| QMT RPC 地址 | `127.0.0.1:58600` | `core/config.py` |
| MiniQMT 端口 | `58610` | `core/lifecycle.py` |
| MiniQMT 二进制 | `D:\迅投...\XtMiniQmt.exe` | `core/lifecycle.py` |
| SQLite 数据库 | `data/kline.db` | `core/config.py` |
| 板块分类文件 | `static/board_classification.json` | `static/` |
| K线数据缓存 TTL | 600 秒 | `core/config.py` |
| 板块涨跌幅刷新 | 60 秒 | `core/config.py` |
| SSE 推送 | `/api/events` (EventBus) | `system_routes.py` |
| MCP 桥接器 | `mcp_board_bridge.js` | 项目根目录 |
| MCP 注册 | `~/.workbuddy/mcp.json` | WorkBuddy 配置 |
| 扫描器输出 | `reports/*.md` | `board_scanner.py` |
| 扫描周期 | 周日 02:00（建议） | WorkBuddy automation |
| 扫描参数 | N_BARS=60, PROXIMITY=3% | `board_scanner.py` |

---

## 10. 已知限制与未决 TODO

### 当前限制

1. **KLineChart Pro 是商业库** — 社区版功能受限（水印已去除，但高级功能如自定义指标需商业许可）
2. **分钟级数据依赖 QMT 本地** — 离开 QMT 客户端环境后回退到 SQLite 日线
3. **前端 JS 变量作用域修复后需重新压缩** — `index-local-fixed.html` 覆盖 `index-local.html` 时也丢失了原始压缩
4. **Scanner 无增量扫描** — 每次全量 900+ 板块 ≈ 45 秒（20 QPS），后续可引入缓存增量
5. **Skill dispatcher 当前只支持单个 skill 结果推送** — 尚未实现多 skill 并发编排

### TODO

- [ ] 把 KLineChart Pro 替换为纯开源方案（如遇许可问题）
- [ ] board_scanner 支持增量只扫昨日有大波动的板块
- [ ] 多 Agent 编排（类 vibe-trading 的投资委员会模式）
- [ ] 单元测试覆盖核心算法（SR 聚类、搜索、上下文切换）
- [ ] Prometheus metrics 接入（见 SLI/SLO 看板设计）
- [ ] 策略回测引擎集成（作为 MCP Tool 暴露）

---

## 11. 与 vibe-trading 的差异化对比

| 维度 | **board-app** | **vibe-trading (HKUDS)** |
|------|------|------|
| 定位 | 板块级看盘 + MCP 上下文面板 | 全自动量化研究平台 |
| 技术栈 | Flask + 内联JS + KLineChart Pro | FastAPI + React 19 + ECharts + LangChain |
| 数据源 | QMT本地 + SQLite | 18 数据源自动回退 |
| 板块覆盖 | ✅ 900+ BK 板块本体 | ❌ 仅个股/代币 |
| 分钟线 | ✅ QMT 本地 1m-120m | 依赖外部数据源 |
| 智能体编排 | ❌ 单 skill 调用 | ✅ 29 个多 Agent 协作 |
| 回测引擎 | ❌ | ✅ 多引擎 + 蒙特卡洛 |
| 策略导出 | ❌ | ✅ Pine / 通达信 / MQL5 |
| MCP 覆盖 | 4 Tool | 54 Tool |
| 本地部署 | ✅ 完全离线（QMT） | ⚠️ 需要 LLM API |
| 延迟 | ~500ms 面板交互 | 分钟级研究流程 |
| 用途互补性 | **面板交互界面** | **策略研究上游** |

### 互补工作流

```
用户（WorkBuddy）
    ↓ "分析当前面板标的"
MCP get_panel_context → board-app
    ↓ 返回 BK1499 医美服务
MCP run_backtest → vibe-trading Agent
    ↓ 多 Agent 协作回测 + 因子分析
    返回策略报告 + 通达信公式
MCP dispatch_skill_result → board-app
    ↓ KLineChart Pro overlay + 标注
面板直接显示买入/卖出信号
```

---

## 12. 调试与排查指南

### 面板全空 / 板块导航不显示

**排查顺序**：

1. **确认 Flask 在跑**：
   ```bash
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/
   # 应返回 200
   ```

2. **确认嵌入数据存在**：
   ```bash
   curl -s http://127.0.0.1:5000/ | grep -c "__init_data__"
   # 应返回 3
   ```

3. **确认分类数据正确（单层数组）**：
   ```bash
   curl -s http://127.0.0.1:5000/ | python -c "
   import sys, json
   from json import JSONDecoder
   content = sys.stdin.read()
   idx = content.find('window.__init_data__ = ') + len('window.__init_data__ = ')
   d, _ = JSONDecoder().raw_decode(content[idx:])
   print('categories is list:', isinstance(d.get('categories'), list))
   print('count:', len(d.get('categories', [])))
   "
   ```

4. **确认前端 JS 无语法错误**：
   打开 WorkBuddy DevTools → Console，寻找 `Cannot access '_analysisHistory' before initialization` 或其他语法错误。

### WorkBuddy CSP 阻止 fetch

症状：API 连通但数据 0 条。

诊断：
- 打开 `http://127.0.0.1:5000/static/pure_test.html`
- 检查 XHR 按钮状态（应为"网络错误"）

解法：已在 `index-local.html` 的 `loadClassification()` 和 `getHistoryKLineData()` 优先使用 `window.__init_data__`。

### 分钟级 K线数据缺失

默认流程：
1. `kline_service.py` 先看 QMT 58610 是否可用
2. 可用 → 获取分钟数据
3. 如果 period 是 15m/30m → 先获取 1m resample
4. 不可用 → 回退 SQLite 日线

检查：
```bash
# 测试分钟线 API
curl -s http://127.0.0.1:5000/api/kline/index/sh000001?period=15m | python -c "import sys,json; d=json.load(sys.stdin); print('count:', d.get('count'))"
```

### Scanner 执行失败

检查：
```bash
# 单独运行 scanner 看日志（请 cd 到项目根目录，以下为历史示例路径）
# cd D:\.workbuddy\2026-06-27-21-35-52\board-app
cd .\board-app    # 或你的实际项目目录
python board_scanner.py
```

常见原因：
- Flask 未启动（需保持 `python app.py` 后台运行）
- `board_classification.json` 格式错误
- 本地代理未清除（scanner 内的 `PROXIES = {"http": None, "https": None}` 应该已处理）

### SSE skill_result 不推送

检查：
```bash
# 监听 SSE 流
curl -s http://127.0.0.1:5000/api/events
```

检查 EventBus：
```python
from core.events import get_event_bus
print(len(get_event_bus()._listeners))
```

### MCP Tool 未注册

检查：
```bash
# 查看 MCP 桥接器是否正常响应
# cd D:\.workbuddy\2026-06-27-21-35-52\board-app    # 历史路径，请替换为实际目录
cd .\board-app
node mcp_board_bridge.js
# 在另一个终端 echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | node mcp_board_bridge.js
```

检查 WorkBuddy MCP 注册：
- 确认 `~/.workbuddy/mcp.json` 中 `board-app` 条目 `disabled: false`
- 在 WorkBuddy 连接器管理页点"Trust"

---

## 附录 A：完整的 Curl 快速测试集

```bash
# 1. 顶部指数
curl -s http://127.0.0.1:5000/api/spot/indices

# 2. 上证指数日线
curl -s http://127.0.0.1:5000/api/kline/index/sh000001?period=daily

# 3. 板块分类
curl -s http://127.0.0.1:5000/api/classification/load

# 4. 板块K线
curl -s http://127.0.0.1:5000/api/kline/industry/BK1499

# 5. 实时报价
curl -s http://127.0.0.1:5000/api/spot/index/sh000001

# 6. 搜索
curl -s "http://127.0.0.1:5000/api/search?q=600519"

# 7. 上涨幅
curl -s http://127.0.0.1:5000/api/board/changes

# 8. 缓存状态
curl -s http://127.0.0.1:5000/api/cache/status

# 9. 更新状态
curl -s http://127.0.0.1:5000/api/update/status

# 10. 系统健康
curl -s http://127.0.0.1:5000/api/system/health

# 11. 面板上下文
curl -s http://127.0.0.1:5000/api/ctx

# 12. SSE 测试
curl -s http://127.0.0.1:5000/api/events
```

## 附录 B：项目级 WorkBuddy Automation 配置建议

```yaml
# ~/.workbuddy/automations → 新建自动化
name: board-scanner-weekly
schedule:
  type: weekly
  day: sunday
  hour: 2
  minute: 0
tasks:
  # python D:\.workbuddy\2026-06-27-21-35-52\board-app\board_scanner.py  # 历史路径，请替换
  - python ./board_scanner.py
```

## 附录 C：关键数据schema

### SQLite `kline` 表

```sql
CREATE TABLE kline (
    code TEXT NOT NULL,      -- 标的代码（BK1499 / sh000001 / 600519）
    period TEXT NOT NULL,    -- daily / 1m / 5m / weekly / monthly ...
    date TEXT NOT NULL,      -- 日期（YYYY-MM-DD）
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    PRIMARY KEY (code, period, date)
);
CREATE INDEX idx_kline_code_period ON kline(code, period);
CREATE INDEX idx_kline_date ON kline(date);
```

### `board_classification.json`

```json
{
  "categories": [
    {
      "name": "医疗健康",
      "boards": [
        {"name": "医美服务", "type": "industry", "code": "BK1499"},
        ...
      ]
    },
    ...
  ]
}
```

14 类，990 板块。

---

> **文档结束** — 本文档应随项目演进持续更新。任何新增 Blueprint、Service 或 MCP Tool 时同步追加对应章节。
