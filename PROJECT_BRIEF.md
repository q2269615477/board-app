# board-app — 项目简介（给后续模型 / 开发者）

> **生成**：2026-07-27 · **用途**：快速建立上下文，再深入读代码
> **状态**：Phase 0–4 + 分析场次 + 前端修复全部完成；本文为现状快照。

---

## 1. 这是什么

A股**盘面复盘分析系统**。核心两件事：

1. **支撑/阻力位识别** —— 一条水平线锚定在某根源 K 的开/收/高/低之一，向后贯穿；后续 K 在此起稳反转即为反应点。
2. **跨板块共振支撑** —— 一组同方向标的（龙头板块 + 同方向指数 + 相关个股）同时逼近各自支撑位 → 高价值机会。

技术栈：Python 3.13 + Flask 3.1.3 + SQLite + QMT + Tushare + KLineChart Pro。
跑在 `http://127.0.0.1:5000`，OpenCode 通过 MCP 接入。

---

## 2. 怎么跑

```bat
cd /d D:\.workbuddy\2026-06-27-21-35-52\board-app
py -3 app.py
```

- 主库 `data/kline.db`（WAL 模式）。
- 环境：`.env` / `TUSHARE_TOKEN`。
- QMT 子进程 RPC（端口 58600），Python 不能直接 `import xtquant`。

---

## 3. 架构一句话

```
前端 static/js/*
  → Flask api/*_routes.py
  → services/*
  → data_loader / sqlite_repo / qmt_client / board_api
  → SQLite 优先；缺口用 Tushare(板块) / 腾讯·东财 HTTP(个股) / QMT(指数·分钟·盘中)
```

数据源纪律：东财 BK 板块只用 Tushare `dc_*`；指数/全市场个股日更优先 QMT；请求路径上的个股缺口用 HTTP，避免同步卡死 Flask 线程池。

---

## 4. 完成状态表

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 0 平台迁移 | 去 CSP、`app.py` 简化、OpenCode MCP 接入、`AGENTS.md` | ✅ |
| Phase 1 支撑位标注 | `level_origin` + `feature_snapshot` + vault 双写 + 前端画线捕获 | ✅ |
| Phase 2 共振检测 | `resonance_service` + API + MCP + 前端共振面板 | ✅ |
| Phase 3 AI 学习 | `level_proposal_service` + `user_style` + 反馈闭环 | ✅ |
| Phase 4 全市场扫描 | `auto_groups` + `scan_all` + 全市场 MCP | ✅ |
| 分析场次 | `analysis_runs` + 自动留痕 + 日历归档 | ✅ |
| 前端修复 | 删除/生命周期/搜索/报价/指数栏/快捷键(Alt+H/R/L/M/P/K) | ✅ |
| T5.1 文档更新 | 本文档 | ✅ |

---

## 5. 核心功能速查

| 功能 | 入口 | 链路 |
|------|------|------|
| 标注 | `POST /api/annotations`（`level_origin`） | `annotation_service` → `annotation_repo` + `vault_writer` |
| 共振 | `POST /api/resonance/scan` | `resonance_service.scan()` —— 确定性硬算 |
| AI 提议 | `POST /api/levels/propose` | `level_proposal_service.propose()` → `user_style()` 画像 |
| 全市场 | `POST /api/resonance/scan_all` | `auto_groups()` + `scan_all()` |
| 场次 | `GET /api/runs/current` | `analysis_run_service` 自动留痕 |
| MCP | `mcp/tools.py`（23 个工具） | `agent_hint` 强制"复述用户原话、不给买卖结论" |

---

## 6. 该读哪些文档

| 文档 | 内容 |
|------|------|
| 本文 `PROJECT_BRIEF.md` | 现状快照 |
| `docs/ARCHITECTURE.md` | 全量分层架构 |
| `docs/technical-audit/2026-07-27-full-audit.md` | 全面技术审计 |
| `docs/plans/2026-07-26-opencode-migration-and-support-resonance-design.md` | 设计文档 |
| `docs/PROJECT_HANDOVER_AND_DEVELOPMENT_PROGRESS.md` | 性能优化史 |
| `AGENTS.md` | Agent 角色与铁律 |

---

## 7. 目录速查

```
app.py, opencode.json, AGENTS.md
core/          config, cache, events, lifecycle
api/           kline/board/annotation/resonance/level_proposal/analysis_run/session/mcp/...
services/      kline_service, board_service, annotation_service, resonance_service,
               level_proposal_service, analysis_run_service, vault_writer, ...
data/          sqlite_repo, annotation_repo, qmt_client, board_api, vault_writer, kline.db
mcp/           tools.py(23工具), handlers.py, schemas.py
static/js/     session-ui(4524行), chart-core, nav-panel, index-bar, realtime-client, search-panel
tests/         37文件 349测试
vault/TradingVault/  Obsidian双写目标
```

---

## 8. 铁律（来自 AGENTS.md）

- 判断标准来自用户，不是 AI 发明的。
- AI 只做两件事：① 按用户标准扫盘产出候选；② 每个候选附上用户当初画那条线时的原话。
- 绝不输出"买/卖/建仓"结论。
- 绝不自己发明共振/反向/相关性结论。
- **精确的东西用工具查，绝不凭记忆背**（价位、关联组、历史标注一律调工具取）。
- 模糊的东西（对新图提议支撑位）才用推理，且必须引用相似的用户历史样本原话作为依据。

---

*后续模型请先看第4节完成状态表，再深入读代码。*