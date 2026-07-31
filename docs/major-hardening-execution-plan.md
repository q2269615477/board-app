# 重大改进专项执行方案：board-app 稳定基线后的防回归增强

本文给后续执行模型阅读。目标是完成稳定基线之后的重大增强，不做小修小补，不做无边界的大型重构。

## 当前前提

当前稳定基线已经封存：

- GitHub 仓库：`https://github.com/q2269615477/board-app.git`
- 稳定基线分支：`codex/stable-board-baseline-2026-07-30`
- 稳定基线 tag：`stable-board-baseline-clean-2026-07-30`
- 当前本地基线验证：`501 passed`

禁止：

- 不允许 `git reset --hard`
- 不允许 `git rebase`
- 不允许 `git push --force`
- 不允许删除源码
- 不允许修改前端布局
- 不允许继续无目标大重构

本方案只做 3 个重大增强专项：

1. 写接口统一保护
2. 前端入口脚本自动验证
3. 数据资产备份/恢复方案

不要做 UI 美化，不要拆巨型模块，不要改 K 线业务逻辑，不要改搜索排序逻辑。

## 开始前检查

执行：

```powershell
git status --short
.\scripts\verify_baseline.ps1
```

要求：

- `git status --short` 必须为空。
- `verify_baseline.ps1` 必须全部 PASS。
- 如果不满足，停止并报告，不要继续。

创建新分支：

```powershell
git checkout -b feature/major-hardening
```

## 专项一：写接口统一保护

### 目标

所有会修改状态、写文件、写数据库、触发任务、调用 QMT 写操作的 POST 接口，必须统一进入写保护机制。

当前已有：

- `api/auth_guard.py`
- `@write_protected`

但覆盖不完整，需要统一补齐。

### 重点检查文件

至少检查：

- `api/system_routes.py`
- `api/board_routes.py`
- `api/task_routes.py`
- `api/annotation_routes.py`
- `api/session_routes.py`
- `api/resonance_routes.py`
- `api/level_proposal_routes.py`
- `api/analysis_run_routes.py`
- `api/search_routes.py`
- `api/ctx_route.py`
- `api/mcp_routes.py`
- `api/signal_ai_routes.py`
- `api/stream_routes.py`

### 执行要求

1. 盘点所有 POST 路由。
2. 分类：
   - 只读 POST：例如纯查询、批量读取、scan/query，可以暂不保护，但必须写明原因。
   - 写状态 POST：必须加 `@write_protected`。
3. 对以下类型必须保护：
   - 保存分类
   - 新建、修改、关闭 session
   - 新建、修改 annotation
   - 新建、修改 resonance group
   - 记录 run、visit、scan
   - 清缓存
   - 重启系统服务
   - 任务创建、取消
   - 写 AI result、signal
   - 覆盖、同步、保存 overlays
4. 不要改变接口返回结构。
5. 不要修改前端调用逻辑，除非测试证明必须修改。

### 测试要求

新增或扩展测试文件：

```text
tests/test_write_protection_coverage.py
```

测试内容：

1. 枚举 Flask app 的所有 POST rule。
2. 建立 allowlist：明确允许不保护的只读 POST。
3. 对非 allowlist 的 POST，验证 endpoint 函数已被 `write_protected` 包装。
4. 设置 `BOARD_APP_WRITE_TOKEN` 后，非本地请求无 token 应返回 403。
5. 本地请求仍放行。

如果当前 `write_protected` 不容易被测试识别，可以在装饰器上设置标记：

```python
wrapper._board_app_write_protected = True
```

然后测试这个标记。

## 专项二：前端入口脚本自动验证

### 目标

`verify_baseline.ps1` 不应手写 15 个 JS 文件，而应自动读取 `static/index.html` 中所有本地 `<script src="/static/js/...">` 文件并逐一 `node --check`。

这样以后新增生产入口 JS，不会漏测。

### 修改文件

- `scripts/verify_baseline.ps1`
- `tests/test_frontend_entry_contract.py`

### 执行要求

1. 从 `static/index.html` 解析所有本地脚本：
   - `/static/js/*.js`
   - 忽略外部 URL
   - 去掉 query string，例如 `?v=...`
2. 对每个存在的本地 JS 执行：

```powershell
node --check <file>
```

3. 如果文件不存在，验证失败。
4. 输出中显示脚本数量，例如：

```text
JS syntax (19 files) PASS
```

5. `tests/test_frontend_entry_contract.py` 也要改成：
   - 自动解析入口脚本
   - 验证所有入口脚本存在
   - 验证所有入口脚本被 Git 跟踪
   - 验证 `verify_baseline.ps1` 不再维护硬编码 15 文件列表

### 注意

`klinecharts.min.js` 和 `klinecharts-pro.umd.js` 是压缩库。如果 `node --check` 能通过就纳入。

如果不适合 `node --check`，必须在文档和测试里明确 allowlist，不允许沉默跳过。

## 专项三：数据资产备份/恢复方案

### 目标

代码已经能通过 GitHub 同步，但本地大数据资产不能进 Git。需要给以后模型、新机器或新环境一个清晰的数据资产迁移方案。

### 新增文档

创建：

```text
docs/data-asset-backup-restore.md
```

内容必须包括：

1. 哪些数据不进 Git：
   - `data/kline.db`
   - `data/kline.db-wal`
   - `data/backup/`
   - `data/行业板块K线数据/`
   - `data/个股数据/`
   - `data/个股K线缓存/`
   - `static/search_index.json`
2. 哪些数据可自举重建：
   - `static/search_index.json`
   - 部分 K 线缓存
   - 板块/个股搜索索引
3. 哪些数据建议外部备份：
   - `data/kline.db`
   - 重要历史行情库
   - 用户标注/会话相关 sqlite，如适用
4. 如何备份：
   - 先停止 Flask/QMT 写入
   - 执行 SQLite checkpoint
   - 复制主 db
   - 记录备份日期
5. 如何恢复：
   - 停止应用
   - 放回对应文件
   - 启动后运行验证脚本
6. 不允许把大数据文件提交到 GitHub。

### 可选脚本

如果时间允许，新增只读/安全脚本：

- `scripts/export_data_snapshot.ps1`
- `scripts/import_data_snapshot.ps1`

要求：

- 默认只备份到用户指定目录。
- 不自动删除原数据。
- 不提交备份文件。
- 对大文件操作前打印路径和大小。
- import 前必须要求用户显式传参确认。

如果脚本复杂，先只写文档，不强行实现。

## 验证命令

完成后执行：

```powershell
git status --short
node --check static/js/*.js
py -m pytest tests/test_repository_hygiene.py -q
py -m pytest tests/test_frontend_entry_contract.py tests/test_write_protection_coverage.py -q
py -m pytest tests/ --tb=no
.\scripts\verify_baseline.ps1
```

要求：

- 所有测试 0 failed。
- `git status --short` 只能出现本次专项相关文件。
- 不允许出现日志、缓存、数据库、大 CSV、真实配置。

## 提交要求

建议拆 3 个 commit：

```text
security: 统一保护写接口并补覆盖测试
test: 自动验证前端入口脚本
docs: 补充数据资产备份恢复方案
```

如果脚本也实现了数据备份恢复，可以用：

```text
chore: 增加数据资产备份恢复脚本
```

## 禁止事项

禁止：

- `git reset --hard`
- `git rebase`
- `git push --force`
- 删除源码
- 修改前端布局
- 修改 K 线业务逻辑
- 修改搜索排序逻辑
- 提交任何运行缓存、日志、数据库、大 CSV、真实配置
- 把 GitHub token 写进 remote URL 或文档

## 交付报告格式

完成后报告：

1. 改了哪些文件
2. 哪些 POST 接口新增了保护
3. 哪些 POST 被列为 allowlist，为什么
4. 前端入口脚本自动识别数量
5. 数据资产文档是否完成，是否新增脚本
6. 所有验证命令结果
7. `git status --short`
8. commit 列表

## 最终提醒

这三个专项是重大防回归增强，不是功能开发。

执行模型必须保持小步提交，不能边做边扩大范围。

如果遇到需要改变业务逻辑的情况，先停止并报告用户。
