# 模型开发同步说明

本文给以后参与 `board-app` 开发的模型阅读。目标是让模型知道：代码应该同步到哪里、从哪个稳定版本开始、什么时候提交、什么时候推送到 GitHub，以及哪些操作禁止执行。

## 一句话结论

本项目的 GitHub 仓库地址是：

```powershell
https://github.com/q2269615477/board-app.git
```

当前推荐的稳定基线分支是：

```powershell
codex/stable-board-baseline-2026-07-30
```

当前推荐的稳定基线 tag 是：

```powershell
stable-board-baseline-clean-2026-07-30
```

以后开发新功能时，不要直接改稳定基线分支。应该从稳定基线新建功能分支，再在功能分支上开发、提交、推送。

## 基本概念

Git 是本地版本管理工具，用来记录项目每一次可靠修改。

GitHub 是远程代码仓库，用来备份代码、跨机器同步代码，也方便不同模型或开发者继续接手。

因此：

- `git commit` 是提交到本机 Git 历史。
- `git push` 是把本机提交上传到 GitHub。
- Git 不等于 GitHub，但 GitHub 使用 Git 来同步代码。

## 当前仓库和基线

远程仓库：

```powershell
origin https://github.com/q2269615477/board-app.git
```

稳定基线分支：

```powershell
codex/stable-board-baseline-2026-07-30
```

稳定基线 tag：

```powershell
stable-board-baseline-clean-2026-07-30
```

稳定基线快照 commit：

```text
1c34cdda6d00e06044521284148ab429c7c423f1
```

本地历史中的业务稳定基线 commit：

```text
3c16593b0a17e7aca01efa06ff9ea2285fbacc4d
```

本地封存脚本提交 commit：

```text
e70f3c574238874997837e532084ff55865d85ff
```

说明：由于本地早期历史曾包含超过 GitHub 100MB 限制的大文件，原始完整历史没有直接推送到 GitHub。GitHub 上的 `codex/stable-board-baseline-2026-07-30` 是当前干净文件树的稳定快照分支，适合作为后续开发起点。

## 新模型接手时应该怎么做

如果是在一个新目录重新拉取项目，执行：

```powershell
git clone -b codex/stable-board-baseline-2026-07-30 https://github.com/q2269615477/board-app.git
cd board-app
```

确认当前状态：

```powershell
git status --short
git log --oneline -5
```

如果存在验证脚本，执行：

```powershell
.\scripts\verify_baseline.ps1
```

只有在工作区干净、验证通过后，才开始开发。

## 开发新功能的标准流程

每个新功能都应该从稳定基线拉出一个新分支。

示例：开发顶部指数栏优化。

```powershell
git checkout -b feature/index-bar-scroll
```

开发完成后，先检查改动：

```powershell
git status --short
git diff
```

运行相关验证。前端改动至少执行：

```powershell
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
```

后端、搜索、K 线、仓库卫生相关改动至少执行：

```powershell
py -m pytest tests/test_repository_hygiene.py -q
py -m pytest tests/test_search_bootstrap.py tests/test_kline_bootstrap.py tests/test_kline_api_fresh_cache.py -q
```

如果改动较大，执行全量测试：

```powershell
py -m pytest tests/ --tb=no
```

验证通过后提交：

```powershell
git add <实际修改的文件>
git commit -m "feat: 简短说明本次功能"
```

推送到 GitHub：

```powershell
git push -u origin feature/index-bar-scroll
```

## 什么时候 commit

建议在以下时机 commit：

- 一个小功能完成并验证通过。
- 一个 bug 修复完成并验证通过。
- 一组相关文件调整完成，且改动范围清楚。
- 工作一天结束前，需要保存可靠进度。

不要把多个不相关功能混在一个 commit 里。例如不要把“搜索优化”“K 线接口修复”“UI 颜色调整”放进同一个 commit。

## 什么时候 push 到 GitHub

建议在以下时机 push：

- 本地 commit 已经通过测试。
- 希望备份到远程。
- 要让其他模型、其他电脑或其他开发者继续接手。
- 准备创建 Pull Request。

简单规则：

```text
小步 commit，阶段性 push。
```

## 不要推送到哪里

不要直接推送到：

```powershell
codex/stable-board-baseline-2026-07-30
```

这个分支是稳定基线，应该保持不动。

不要覆盖或删除：

```powershell
stable-board-baseline-clean-2026-07-30
```

这个 tag 是稳定基线标记，用来以后回溯。

## 禁止操作

未经用户明确同意，禁止执行：

```powershell
git reset --hard
git rebase
git push --force
git tag -f
git branch -D
```

禁止提交以下文件：

- 运行日志：`*.log`
- Python 缓存：`__pycache__/`, `*.pyc`
- 数据库运行文件：`*.db`, `*.sqlite`, `*.db-wal`, `*.db-shm`
- 搜索构建产物：`static/search_index.json`
- 行情缓存 CSV：`data/行业板块K线数据/`, `data/个股数据/`, `data/个股K线缓存/`
- 运行状态：`data/update_status.json`
- 真实本地配置：`qmt-http-server/deploy/server_config.json`
- 本地批处理脚本：`*.bat`
- WorkBuddy 内部文件：`.workbuddy/`

如果这些文件出现在 `git status --short` 中，应先停下来检查 `.gitignore` 或仓库卫生测试，不要直接提交。

## 如果推送失败怎么办

如果 GitHub 提示超过 100MB 大文件限制，不要强推，不要使用 `git reset --hard`。

应该先报告用户，并说明是哪几个文件导致失败。

### GitHub 使用本地 7688 代理

本机访问 GitHub 时优先使用 HTTP 代理：

```text
http://127.0.0.1:7688
```

当前系统的 `NO_PROXY` 可能包含 `github.com`，这会让 Git 绕过代理并直接连接
GitHub，表现为连接重置或无法连接 443 端口。推送时应只在当前 PowerShell
进程中临时清空 `NO_PROXY`：

```powershell
$env:NO_PROXY=''
$env:no_proxy=''
git -c http.proxy=http://127.0.0.1:7688 -c http.version=HTTP/1.1 push -u origin $(git branch --show-current)
```

执行前可以确认代理端口是否正常：

```powershell
Test-NetConnection 127.0.0.1 -Port 7688
```

不要使用 `git config --global http.proxy ...` 写入永久代理配置。不要修改系统
全局代理，也不要删除 Tushare、QMT 等域名已有的直连规则。

如果只是新功能分支推送失败，常见检查命令：

```powershell
git status --short
git remote -v
git branch --show-current
git log --oneline -5
```

如果远程已经有别人提交，先报告用户，不要擅自合并复杂历史。

## GitHub token 安全

不要把 GitHub token 写进 remote URL。

正确 remote 应类似：

```powershell
https://github.com/q2269615477/board-app.git
```

错误示例：

```powershell
https://用户名:token@github.com/q2269615477/board-app.git
```

如果发现 remote URL 中含有 token，应改为普通地址：

```powershell
git remote set-url origin https://github.com/q2269615477/board-app.git
```

然后提醒用户去 GitHub 后台撤销泄露过的 token。

## 给模型的最终提醒

开发前先确认：

```powershell
git status --short
```

开发时小步修改。

提交前先验证。

推送时推到新的功能分支。

不要直接修改稳定基线分支。

不要提交缓存、日志、真实配置、大文件。

不确定时先报告用户，不要擅自改写历史。
