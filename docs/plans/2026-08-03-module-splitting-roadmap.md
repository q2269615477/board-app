# board-app 模块拆分路线图

## 目标

在不改变用户可见行为和既有全局 API 的前提下，把超大文件按“纯计算、状态模型、适配器、交互控制器、渲染器”逐步拆开。每次只完成一个可测试、可回滚的切片，避免同时改动数据源、图表坐标和 UI。

## 当前基线

- `static/js/replay-trade-ui.js`：约 3450 行，混合交易状态投影、面板、拖拽、SVG 和生命周期。
- `static/js/chart-comparison.js`：约 2300 行，混合周期对齐、百分比计算、搜索、框选和渲染。
- `static/js/session-ui.js`：约 2700 行，混合会话状态、分析卡片和交互。
- `static/js/nav-panel.js`：约 1600 行，混合分类树、行情、搜索和成分股弹层。
- `data_update_manager.py`：约 2300 行，虽已有 facade 拆分，仍包含个股、指数、板块与调度逻辑。

## 拆分原则

1. 先拆纯函数和只读状态投影，再拆 DOM 和图表交互。
2. 保留现有 `window.*` API、后端导入签名和路由契约。
3. 新模块必须在入口文件中显式加载，并由入口契约测试保护顺序。
4. 每一阶段必须通过针对性测试、前端全量测试、Python 全量测试和真实浏览器冒烟。
5. 行情数据、缓存、数据库和日志不进入 Git。

## 阶段安排

### 阶段 1：回放交易状态模型

状态：已完成并提交（`a5de3b4`）。

- 新增 `replay-trade-state-model.js`。
- 抽取交易记录兼容、去重排序、持仓汇总和状态扁平化。
- `ReplayTradeUI` 仅保留薄适配调用，公共 API 不变。

验收：直接模型单测、回放交易集成测试、入口顺序测试。

### 阶段 2：叠加比较计算模型

状态：已完成并提交。

- 新增 `chart-comparison-model.js`。
- 抽取周期映射、时间桶、序列对齐、归一化、区间统计和百分比坐标计算。
- 保留搜索、拖拽、SVG 和图表适配在控制器中。

实施边界：控制器通过 `window.ChartComparisonModel` 薄委托；`computeRangeComparison`、`computeMainRange`、`computeRangeDetails` 和 `createSearchUrl` 继续留在控制器，避免改变已有入口契约和网络行为。

验收：11 个周期矩阵、不同交易日对齐、框选缩放后端点黏合、多个叠加标的。

### 阶段 3：回放交易 UI 分层

状态：切片 3.1（坐标几何）已完成；切片 3.2.1（被动 SVG 构造）已完成。

- `replay-trade-geometry.js`：像素、K 线索引和价格转换。
- 图表 API 调用保留在 UI 适配层，几何模块保持纯计算、可独立测试。
- 真实浏览器已验收回放起点选择、控制层加载及滚轮缩放后的状态保持。
- `replay-trade-overlay-renderer.js`：已抽取 B/S 执行标记、止盈止损预设、风险区、预览线和历史虚影的无状态 SVG 构造；DOM 事件、图表坐标换算和交易状态仍由 UI 层持有。
- 切片 3.2.2 待办：在保持交互归属不变的前提下，再抽取订单栏与汇总框的被动 SVG 构造。
- `replay-trade-interaction-controller.js`：选择、拖拽和面板生命周期。

此阶段风险最高，必须有真实 Chromium 拖拽和缩放验收后再实施。

### 阶段 4：数据更新 facade 收缩

前置条件：先修复更新任务契约断裂，确认 `scan_update_debt`、`_full_update_in_progress`、`update_all_today(force/progress_callback)` 与 `update_all_boards(only_lagging)` 的调用方和实现一致；这些问题不得借“模块拆分”掩盖。

- 个股日更迁至 `stock_update_service.py`。
- 指数日更迁至 `index_update_service.py`。
- 板块日更迁至 `board_update_service.py`。
- `data_update_manager.py` 最终只保留兼容 facade 和总管线编排。

要求：共享锁和调度器所有权不得复制，旧导入签名保持不变。

### 阶段 5：导航与会话 UI

- 导航拆为分类树、行情投影、成分股弹层和搜索适配器。
- 会话拆为状态存储、分析卡片渲染和用户操作控制器。

## 每阶段禁止事项

- 不顺手重写业务规则。
- 不修改第三方压缩包 `klinecharts-pro.umd.js`。
- 不把多个超大文件同时重构。
- 不以 mock 测试代替真实坐标、缩放和拖拽验收。
- 不在模块拆分提交中混入数据更新或视觉改版。
