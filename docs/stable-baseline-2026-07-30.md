# 稳定基线：stable-board-baseline-2026-07-30

> 封存日期：2026-07-30
> Commit：`3c16593b0a17e7aca01efa06ff9ea2285fbacc4d`
> Tag：`stable-board-baseline-2026-07-30`

## 基线内容

| 维度 | 状态 |
|------|------|
| Git 工作区 | 干净（`git status --short` 为空） |
| 测试套件 | 501 passed, 0 failed |
| 前端 JS | 15 个文件 `node --check` 全部通过 |
| 仓库卫生 | 13 个禁止项测试全部通过 |
| 搜索自举 | 7 个测试（含 2 个真实构建器 integration test） |
| K 线自举 | 4 个 fallback 链测试 + 6 个 API 契约测试 |

## 验证命令

```powershell
# 1. 工作区干净
git status --short

# 2. 前端 JS 语法
node --check static/js/api-client.js
node --check static/js/app-init.js
node --check static/js/chart-controller.js
node --check static/js/chart-core.js
node --check static/js/index-bar.js
node --check static/js/nav-panel.js
node --check static/js/pinyin_helper.js
node --check static/js/realtime-client.js
node --check static/js/search-panel.js
node --check static/js/session-ui.js
node --check static/js/sse-client.js
node --check static/js/symbol-router.js
node --check static/js/toast-modal.js
node --check static/js/ui-state.js
node --check static/js/unified-selector.js

# 3. 全量测试
py -m pytest tests/ --tb=no

# 4. 一键验证脚本
.\scripts\verify_baseline.ps1
```

## 提交历史（基线含 7 个 commit）

```
3c16593 移除残留的运行时日志/缓存文件Git跟踪（仓库卫生修复）
f919928 补充遗漏的配置/文档/前端文件（仓库卫生拆分遗留）
2f243d5 测试与文档
d4b0775 前端统一选择链路与 UI 修复
6f239a7 搜索索引自举与拼音修复
8a6b6e3 后端重构：K线自举/QMT fallback/数据层/服务层/API 路由
0b5659e 仓库卫生清理：移除 pycache/日志/缓存CSV/临时文件，扩展 .gitignore 与防复发测试
```

## 后续开发约束

1. **禁止 `git reset --hard` 和 `git rebase`** 改写基线历史。
2. **禁止删除源码**或进行大型重构，除非用户明确指示。
3. **禁止修改前端布局**，除非用户明确指示。
4. 新增功能前先确认 `git status --short` 为空，从干净状态开始。
5. 提交前必须通过 `py -m pytest tests/ --tb=no`（0 failed）。
6. 提交前必须通过 `node --check` 对所有 `static/js/*.js` 的语法检查。
7. 仓库卫生测试 `tests/test_repository_hygiene.py`（13 项）必须全绿，不得跳过。
8. 新增文件必须遵守 `.gitignore` 策略（见 `docs/artifact-strategy.md`）。

## 回滚规则

如果后续开发导致测试失败或功能回归：

```powershell
# 回滚到稳定基线（不丢失未提交的工作区改动）
git stash
git checkout stable-board-baseline-2026-07-30

# 验证基线是否仍然通过
.\scripts\verify_baseline.ps1

# 回到开发分支
git checkout full-project
git stash pop  # 恢复之前的工作区改动
```

如果需要彻底丢弃某个 commit：

```powershell
# 使用 revert（不改写历史，安全）
git revert <commit-hash>
```

**绝不使用 `git reset --hard` 丢弃已提交的代码。**

## 产物策略

参见 `docs/artifact-strategy.md`，明确区分：
- **源码**（Git 跟踪）：`*.py`, `static/js/*.js`, `static/index.html`
- **数据种子**（Git 跟踪）：`static/board_classification.json`, `data/industry_constituents.json`
- **构建产物**（.gitignore）：`static/search_index.json`
- **运行时缓存**（.gitignore）：`data/*.db`, `data/行业板块K线数据/*.csv`
- **运行时状态**（.gitignore）：`data/update_status.json`
- **本地配置**（.gitignore，只跟踪 .example）：`qmt-http-server/deploy/server_config.json`
