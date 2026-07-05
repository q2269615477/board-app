# AI炒股面板 —— 工具链与功能架构

**版本**：v3.0  
**最后更新**：2026-07-05  
**启动端口**：5000（自动递增）  
**入口**：`app.py`

---

## 一、工具与基础设施

### 1.1 数据源

| 工具 | 接入方式 | 用途 | 状态 |
|------|---------|------|------|
| **QMT 标准客户端** | 子进程 `rpc_init(127.0.0.1:58600)` | A股日线、分钟线（1m/5m/15m/30m/60m）、指数K线、港股指数 | ✅ 运行中 |
| **TuShare MCP** | `mcp__tushareMcp__*` 远程 API | 板块列表、板块成分股、板块K线（dc_index/dc_member/dc_daily） | ✅ 可用 |
| **TuShare 本地库** | `tushare 1.4.29` (pip安装) | 个股收盘价补全（`ts.pro_api().daily()`）、交易日历 | ✅ 可用 |
| **SQLite 本地缓存** | `data/kline.db` | 板块/个股/指数日线历史数据、meta信息、市值缓存 | ✅ 主力存储 |

#### QMT 连接细节

```
架构：单端口 58600 RPC（MiniQMT 58610 已移除）
Python 3.13 无法 import xtquant → 通过 subprocess 调用 QMT 自带 Python
数据流：Flask API → services/kline_service.py → data/qmt_client.py → subprocess(QMT python.exe) → rpc_init(58600) → xtdata
```

### 1.2 前端

| 工具 | 版本 | 用途 |
|------|------|------|
| **KLineChart Pro** | UMD 构建（本地文件） | 完整K线图表渲染 |
| **KLineChart** | 9.6.0（open source） | 基础图表引擎 |
| **Vanilla JS** | — | 无框架，原生DOM操作 |

#### KLineChart Pro 配置

```
主题：暗色（#13161F 背景）
时区：Asia/Shanghai
语言：中文 locale
蜡烛颜色：红涨绿跌（upColor=#ef5350, downColor=#26a69a）
时间轴：bar 模式（等间距，无周末缺口）
成交量：颜色跟随K线
右侧空白：offsetRightBarCount=0（非交易日不延伸）
```

### 1.3 后端

| 组件 | 用途 |
|------|------|
| **Flask** | Web 服务器，处理 API 请求 + 静态文件 |
| **pandas** | 数据处理（DataFrame → JSON 转换） |
| **pypinyin** | 拼音首字母计算（板块搜索） |
| **threading** | 后台线程（QMT验证、预热、看门狗） |

### 1.4 MCP 桥接

| 端点 | 用途 |
|------|------|
| `/mcp/tools` | MCP 协议工具列表 |
| `/mcp/call` | MCP 工具调用 |
| `mcp_board_bridge.js` | Node.js 桥接（board-app MCP server） |

---

## 二、项目文件结构

```
board-app/
├── app.py                    # Flask 入口 + 生命周期引导
├── core/
│   ├── config.py             # 集中配置（路径、端口、参数）
│   ├── lifecycle.py          # 应用生命周期（QMT初始化、后台服务）
│   └── cache.py              # 内存缓存（TTL-based LRU）
├── api/
│   ├── __init__.py           # 路由注册
│   ├── kline_routes.py       # K线数据 API 路由
│   ├── board_routes.py       # 板块/成分股/排序 API 路由
│   └── hooks_routes.py       # 钩子状态 API
├── services/
│   ├── kline_service.py      # K线业务逻辑（多周期、resample）
│   └── board_service.py      # 板块业务逻辑（成分股、涨跌幅）
├── data/
│   ├── qmt_client.py         # QMT 统一调用客户端（子进程模式）
│   ├── sqlite_repo.py        # SQLite 仓库（读写kline/meta/ledger/mkt_cap表）
│   ├── board_api.py          # Tushare 数据客户端（dc_index/dc_daily/dc_member）
│   └── board_cache.py        # 板块数据内存缓存
├── data/
│   ├── kline.db              # SQLite 数据库（kline/meta/stock_ledger/board_cache/mkt_cap）
│   ├── 行业板块K线数据/       # 496 个 CSV 文件（东财历史数据）
│   ├── 概念板块K线数据/       # 495 个 CSV 文件（东财历史数据）
│   └── *_constituents.json   # 成分股 JSON 缓存
├── static/
│   ├── index.html            # 前端面板（约 1500 行，含全部业务逻辑）
│   ├── board_classification.json  # 14大类板块分类数据
│   └── js/
│       ├── klinecharts.min.js       # KLineChart 基础库
│       └── klinecharts-pro.umd.js   # KLineChart Pro UMD
└── SESSION_STATE.md          # 会话状态快照
```

---

## 三、已实现功能

### 3.1 布局架构

```
+--------------------------------------------------+
| 工具栏：[分类导航]  [索引快捷栏(10个指数)]         |
+----------+-----------------------+---------------+
|          |                       | 技能市场(180px)|
| 板块导航 |   KLineChart Pro      +---------------+
| (280px)  |   完整K线图表          | 信号记录      |
|          |                       |               |
|  14大类  |                       |               |
| + 概念   |                       |               |
|          |                       |               |
+----------+-----------------------+---------------+
|              搜索输入框（右下角）                  |
+--------------------------------------------------+
```

### 3.2 左侧：板块分类导航（280px）

- **14大行业分类**：三级树形结构（行业→子行业→个股）
- **概念板块**：独立分类，与行业并行
- **递归展开/折叠**：点击分类名展开子节点
- **右键重分类**：`ctxMenu` 可将板块拖入不同分类（持久化到 `/api/classification/save`）
- **涨幅排序**：按板块涨跌幅排名（红色涨、绿色跌）

### 3.3 板块成分股浮动面板

展开板块后 hover 时右侧弹出二级浮动面板：

- **默认排序**：市值降序
- **⇅ 排序按钮**：三态循环——市值 → 涨幅↓ → 涨幅↑
- **↻ 刷新按钮**：`?refresh=1` 绕过 300s 缓存，Tushare 实时补全收盘价/涨跌幅
- **红涨绿跌**：涨跌幅颜色规范
- **个股跳转**：点击成分股直接加载到 K 线图
- **面板关闭时恢复默认排序**

### 3.4 中部：KLineChart Pro 图表

| 功能 | 详情 |
|------|------|
| **周期切换** | 1m / 5m / 15m / 1H / 2H / 4H / 日 / 周 / 月 / 季 / 年 |
| **画线工具** | 水平线、趋势线、矩形、Fibonacci、文字标注等 |
| **技术指标** | MA / EMA / BOLL / MACD / RSI / KDJ / VOL / OBV 等 30+ 指标 |
| **指标参数设置** | 可调整指标参数（如 MA 周期） |
| **时区设置** | 支持多时区 |
| **主题切换** | 明暗主题 |
| **截图** | 一键截图下载 |
| **水印** | 自定义水印 |
| **十字星提示** | hover 显示 OHLC + 涨跌幅 |
| **成交量** | K 线下方，红涨绿跌 |
| **Datafeed** | 连接 Flask 后端，支持多周期自适应（15m+ 从 1m resample） |

### 3.5 右侧：技能市场 + 信号记录（180px）

- **AI 技能市场**：14 个可扩展技能（brainstorming / planning / TDD / debugging 等）
- **信号记录**：手动添加/查看交易信号（`/api/signals/<code>`）

### 3.6 顶部工具栏

- **分类导航按钮**：显示/隐藏左侧面板
- **指数快捷栏（11个）**：

| 名称 | 代码 |
|------|------|
| 上证指数 | sh000001 |
| 创业板指 | sz399006 |
| 科创50 | sh000688 |
| 沪深300 | sh000300 |
| 上证50 | sh000016 |
| 中证1000 | sh000852 |
| 中证2000 | sh000853 |
| 中证全指 | sh000985 |
| 恒生指数 | HSI |
| 恒生科技 | HSTECH |
| 微盘股 | BK1158 |

- **右键移除指数**
- **实时价格刷新**：30秒轮询 `/api/spot/indices`

### 3.7 右下角：搜索系统

- **拼音首字母搜索**：输入 `zz` → 匹配 "制造"、"自主" 等首字拼音以 z 开头的板块
- **逐级匹配**：`_progressiveMatch()`，输入 C→筛选第1字，再输 X→筛选第2字
- **代码搜索**：纯数字直接匹配代码
- **990+ 板块**：全部预计算 initials
- **键盘导航**：上下箭头 + Enter 选择

### 3.8 数据管线

```
板块 K 线请求
  ├── SQLite 缓存命中 → 直接返回
  ├── 非交易日（weekday >= 5）→ 跳过更新
  └── 缓存未命中 → Tushare dc_daily → 写入 SQLite → 返回

指数 K 线请求
  └── QMT RPC(58600) → xtdata.get_local_data → SQLite → 返回

分钟线请求 (1m/5m)
  └── QMT RPC(58600) → xtdata.get_local_data → 返回

分钟线请求 (15m/30m/60m)
  └── QMT RPC(58600) → 1m 原始数据 → pandas resample → 返回

成分股面板
  ├── JSON 缓存 → 成分股列表
  ├── SQLite kline 表 → 收盘价/涨跌幅
  └── Tushare daily API → 缺失数据补全（限10只）
```

### 3.9 后端 API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/kline/<type>/<code>` | GET | K线数据（?period=daily&name=xxx） |
| `/api/spot/<type>/<code>` | GET | 实时行情（QMT→Tushare→SQLite三级回退） |
| `/api/spot/indices` | GET | 批量指数实时行情 |
| `/api/boards/<type>` | GET | 板块列表 |
| `/api/board-cons-sorted/<type>/<code>` | GET | 成分股排序（?refresh=1强制刷新） |
| `/api/board-changes` | GET | 全部板块涨跌幅 |
| `/api/cons/<type>/<code>` | GET | 实时成分股（东财API） |
| `/api/signals/<code>` | GET/POST | 信号读取/写入 |
| `/api/classification/load` | GET | 加载板块分类 |
| `/api/classification/save` | POST | 保存板块分类 |
| `/api/pinyin/all` | GET | 拼音首字母数据 |
| `/api/search` | GET | 搜索（?q=xxx） |
| `/api/hooks/status` | GET | 钩子状态 |
| `/api/health` | GET | 健康检查 |
| `/mcp/tools` | GET | MCP 工具列表 |
| `/mcp/call` | POST | MCP 调用 |

### 3.10 生命周期与自动化

- **启动预热**：预加载 12 个常用标的（指数+微盘股）
- **QMT 验证**：启动时通过子进程验证 RPC 可用性
- **QMT 同步**：后台线程同步常用标的到 SQLite
- **周末防护**：`weekday >= 5` 跳过 Tushare API 调用
- **订阅防护**：非交易日 subscribe 不推送 `Date.now()` 虚 bar
- **板块涨跌幅轮询**：60秒刷新

---

## 四、核心修复记录（2026-07-05）

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `lifecycle.py` | MiniQMT 无用常驻 | 移除 XtMiniQmt.exe 进程和看门狗 |
| 2 | `qmt_client.py` | QMT_DATA_PORT 死代码 | 删除 58610 端口引用 |
| 3 | `index.html:821` | `try{}` 缺 catch → JS 解析失败白屏 | 补 `catch(e){}` |
| 4 | `data_loader.py` | 周末仍调 Tushare API | `weekday >= 5` 守卫 |
| 5 | `index.html:409` | subscribe 用 `Date.now()` 周末创虚 bar | `if (!ts) return` |
| 6 | `index.html:506` | K 线右侧空 bar 延伸至当天 | `setOffsetRightBarCount(0)` |
| 7 | `board_service.py` | 成分股无收盘价 | Tushare 批量补全 |
| 8 | `sqlite_repo.py` | 缺市值表 | 新增 `mkt_cap` 表 |

---

## 五、继续开发建议

1. **填充市值数据**：通过 Tushare `daily_basic` 批量刷新 `mkt_cap` 表
2. **节假日日历**：不仅周末，含法定假日判断
3. **面板自动刷新**：页面定时刷新机制
4. **错误边界增强**：前端 error boundary 覆盖更多异常场景
