# 重大增强通过后的下一步路线

本文给后续模型阅读。前提是 `docs/major-hardening-execution-plan.md` 中的三项重大增强已经完成并验证通过。

## 适用前提

只有满足以下条件，才进入本文描述的下一阶段：

- 写接口统一保护已经完成。
- 前端入口脚本自动验证已经完成。
- 数据资产备份/恢复方案已经完成。
- `git status --short` 为空。
- `.\scripts\verify_baseline.ps1` 全部 PASS。
- `py -m pytest tests/ --tb=no` 全部 PASS。
- 相关变更已经提交到 Git。

如果以上任一条件不满足，不要继续下一阶段，先回到重大增强专项收口。

## 下一阶段总原则

下一阶段不再做仓库卫生和安全边界清理，除非发现严重问题。

下一阶段只做"业务能力增强"，并且必须按小功能、小分支、小提交推进。

禁止一次性修改多个功能域。例如不要同时改：

- 前端布局
- 搜索
- K 线数据
- 会话面板
- 数据更新调度
- QMT HTTP server

每次只选一个方向。

## 推荐优先级

### 第一优先级：真实点击链路端到端验证

目标：证明用户实际操作路径不会再出现"点击后图表不变、加载很慢、搜索结果缺失"。

建议新增 Playwright 或 Flask + 前端契约测试，覆盖：

1. 搜索 `ymkd`。
2. 使用键盘上下箭头选择药明康德。
3. 回车确认。
4. 断言 `select-symbol` 事件触发。
5. 断言 `window.__board_ctx` 更新为 `603259`。
6. 断言图表请求 `/api/kline/stock/603259`。
7. 断言图表数据不是上一个标的的数据。

涉及文件可能包括：

- `tests/test_playwright_interactions.py`
- `static/js/search-panel.js`
- `static/js/unified-selector.js`
- `static/js/chart-controller.js`
- `static/js/chart-core.js`

注意：如果只是补测试，不要改前端布局。

### 第二优先级：K 线真实数据源可观测性

目标：用户点击标的后，能明确知道数据来自哪里，为什么慢，是否走了降级。

建议增加后端响应字段或日志，但不要破坏现有前端字段：

- `source`: `cache` / `sqlite` / `qmt_http` / `qmt_xtdata` / `global` / `board_loader`
- `stale`: 是否旧数据
- `background_refresh_started`: 是否已启动后台刷新
- `load_ms`: 本次加载耗时
- `fallback_chain`: 实际尝试过的数据源列表

优先加测试，再改实现。

涉及文件可能包括：

- `services/kline_service.py`
- `api/kline_routes.py`
- `tests/test_kline_api_fresh_cache.py`
- `tests/test_kline_bootstrap.py`

注意：不要改变 K 线数据排序、周期重采样和现有字段语义。

### 第三优先级：前端会话面板拆分

目标：降低 `static/js/session-ui.js` 的变更风险。

当前 `session-ui.js` 行数很大，未来任何小改都容易影响会话、标注、共振、候选支撑等多个功能。

建议采用渐进式拆分，不要一次性重写：

1. 先抽 API 层：
   - `static/js/session-api.js`
2. 再抽状态层：
   - `static/js/session-state.js`
3. 再抽渲染辅助：
   - `static/js/session-render.js`
4. 最后保留 `session-ui.js` 作为装配入口。

每抽一层都必须保持 UI 行为不变，并补测试。

涉及文件可能包括：

- `static/js/session-ui.js`
- `static/index.html`
- `tests/test_frontend_entry_contract.py`
- `tests/test_unified_selector_js.py`

注意：不要在拆分同时改视觉样式。

### 第四优先级：数据更新管线拆分

目标：降低 `data_update_manager.py` 的维护风险。

当前该文件承担过多职责：

- 状态文件
- QMT 探测
- 指数更新
- 板块更新
- 个股更新
- SSE 事件
- 台账修复
- Tushare fallback

建议按职责拆分：

- `services/update_status_store.py`
- `services/stock_update_service.py`
- `services/board_update_service.py`
- `services/index_update_service.py`
- `services/update_event_log.py`

拆分规则：

- 先搬函数，不改行为。
- 先加兼容 facade，保持旧调用可用。
- 每拆一块跑对应测试。
- 不要和 K 线业务逻辑修改混在一起。

涉及文件可能包括：

- `data_update_manager.py`
- `services/update_pipeline.py`
- `services/update_task_factories.py`
- `tests/test_update_pipeline.py`
- `tests/test_board_update_pipeline.py`

### 第五优先级：本地数据资产自动巡检

目标：提前发现数据库膨胀、WAL 未 checkpoint、备份过期、索引缺失等问题。

建议新增只读诊断命令：

```powershell
.\scripts\diagnose_data_assets.ps1
```

输出：

- `data/kline.db` 大小
- WAL/SHM 是否存在
- 最近 checkpoint 时间
- 最近备份时间
- `static/search_index.json` 是否存在
- 搜索索引是否可重建
- SQLite 表行数概要

注意：诊断脚本默认只读，不自动删除、不自动压缩。

## 不建议马上做的事

以下事项暂时不要做，除非用户明确要求：

- 大规模 UI 改版
- 换前端框架
- 把所有 JS 改成 TypeScript
- 把 SQLite 换成别的数据库
- 把所有历史 commit 重写
- 把大数据文件推到 GitHub
- 删除 QMT 相关代码
- 删除 Obsidian / MCP / 会话系统

## 每个新阶段的标准流程

开始前：

```powershell
git status --short
.\scripts\verify_baseline.ps1
```

新建分支：

```powershell
git checkout -b feature/<short-topic>
```

开发后：

```powershell
git status --short
git diff
```

验证：

```powershell
py -m pytest tests/ --tb=no
.\scripts\verify_baseline.ps1
```

提交：

```powershell
git add <本次实际修改文件>
git commit -m "<type>: <简短说明>"
```

推送：

```powershell
git push -u origin feature/<short-topic>
```

## 交付报告格式

后续模型完成任一阶段后，必须报告：

1. 本次目标是什么
2. 修改了哪些文件
3. 是否改变用户可见行为
4. 新增或修改了哪些测试
5. 验证命令和结果
6. `git status --short`
7. commit 列表
8. 是否建议继续下一阶段

## 最终提醒

重大增强通过后，项目的下一步重点不是继续"修地基"，而是用小步方式增强用户真实工作流。

优先做能直接证明真实点击链路、真实数据链路、真实会话链路稳定的工作。

不要为了"看起来更干净"而做没有验收价值的大拆大改。
