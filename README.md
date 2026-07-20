# board-app — AI股票画板

Flask + KLineChart Pro 9.x + Vanilla JS + SQLite

对接 QMT（公式口 58600）/ 东财（Tushare/HTTP兜底），支持板块/指数/个股 K 线、画线工具、右侧「会话分析」因果链与元素采集，并写入 Obsidian vault 供 Agent 学习。

## 2026-07-20 备份 — P0 阶段完成

### 已完成
- **P0-1** 乐观锁：rev + base_rev → 409 REVISION_CONFLICT
- **P0-2** committed 只读：_check_writable 守护 + clone 端点
- **P0-3** 会话回放：GET /api/sessions/<id>/replay + applySessionReplay
- **P0-4** 画线按事件显示：filterOverlaysByEvent 灰显

### 测试
35/35 会话+标注 passed

### API 错误码
| code | HTTP | 说明 |
|------|------|------|
| REVISION_CONFLICT | 409 | base_rev 不匹配 |
| COMMITTED_READONLY | 409 | committed 会话不可修改 |

### 启动
```
cd D:/.workbuddy/2026-06-27-21-35-52/board-app
.\venv\Scripts\python.exe app.py
# http://127.0.0.1:5000
```
