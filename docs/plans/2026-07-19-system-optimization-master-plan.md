# board-app 系统优化与知识库总方案

> **For Claude / 执行代理：** 实现时按文末 **Phase 任务清单** 逐任务推进；每任务有可机械判定的完成标准。  
> **RELATED:**  
> - `docs/项目功能全景与优化建议_2026-07-19.md`  
> - `docs/plans/2026-07-19-chart-annotation-obsidian-kb-design.md`  
> - `ARCHITECTURE.md`、`IMPLEMENTATION_PLAN.md`（历史 V5，本方案优先）

**日期：** 2026-07-19  
**Goal：** 在数据可信的前提下，统一架构真相源，并建成「图表标注 → 用户语义/关联 → Obsidian + 机读索引 → Agent 只复述经验」的闭环。  
**Architecture：** 先修 K 线与单入口数据/MCP；再叠加 Annotation（Case / level_origin）与用户声明 Relation；双写 vault + SQLite 索引；前端轻量采集、Obsidian 深度写作。  
**Tech Stack：** Flask、Vanilla JS + KLineChart Pro、SQLite、QMT 子进程 RPC 58600、Tushare/东财、MCP SSE、Obsidian Markdown vault（本地路径可配）

**总完成标准（整系统优化就绪）：**

1. 多周期 K 线抽样回归通过（时间戳单调、周期 bar 数量关系合理）。  
2. QMT/MCP 各仅一条对外契约路径。  
3. 用户可完成：源 K + 价要素 → 水平线 Case → 反应点 → 备注提醒 → 跨标的跨周期 Relation（仅用户结论）。  
4. vault 可打开 md；索引可被 MCP `search_cases` / `search_relations` 命中。  
5. 无「系统自动判定共振/反向」代码路径。

---

# 第一部分：总结

## 1.1 项目现状（一句话）

**板块看盘 + QMT/Tushare 管线 + Agent 控图** 已可用；短板是 **K 线正确性、双 MCP/死代码、假回测、标注无语义层**。

## 1.2 原优化建议（压缩）

| 优先级 | 编号 | 内容 |
|--------|------|------|
| P0 | O1 | 修 K 线日期/周期（日线重复、高低周期循环、5m/15m≈1m） |
| P0 | O2 | QMT 单入口，清 58610 / 双 client |
| P0 | O3 | MCP 保留 `/mcp/*`，合并或降级 `/api/mcp/*` |
| P0 | O4 | 回测真算或 503 诚实下线 |
| P0 | O5 | 东财/抽样零偏差契约 |
| P1 | O6–O12 | 交易日历、bootstrap 可测、统一错误体、归档噪音、README/STATE 对齐、搜索索引自检 |
| P2 | O13–O18 | WS 主推、成分分页、增量 K 线、库冷热、前端拆分、市值表 |
| P3 | O19–O22 | skill 源码、context 持久化、AI 异步、MCP 幂等 |

原新功能轨：自选、热力图、预警、扫描、技能市场、真回测等——**排在数据正确与知识闭环之后**，避免在错误 K 线上堆产品。

## 1.3 用户新需求（知识闭环）

| 需求 | 要点 |
|------|------|
| 画线落本地 | 自动捕获几何 + 上下文 |
| 语义笔记卡 | 面板轻量表单；非第二套 Obsidian |
| level_origin | 周期 + 源 K 价要素(O/H/L/C) + 水平线 + 后期反应点 + 备注/提醒 |
| 多标的关联 | 用户声明 Relation；**系统不判定**共振/反向 |
| 经验原文 | 如「红利见底≈科创见顶」仅存 `relation_note` |
| 范围 | 跨指数/板块/个股；跨日/周/月/年；成员各自 period |
| Agent | 检索与**复述**用户经验；不生成系统结论 |

## 1.4 合并后的目标架构（逻辑）

```
[看盘可信层]  K线/QMT/Tushare 单真相源
      ↓
[几何层]      KLineChart 画线 / overlay_service
      ↓
[语义层]      Case(level_origin) + notes + reminders
      ↓
[关联层]      Relation（用户声明 + relation_note）
      ↓
[沉淀层]      Obsidian vault（人读） + annotation_index.sqlite（机读）
      ↓
[Agent层]     MCP search/get/load；只引用用户原文
```

## 1.5 分期总览（建议执行顺序）

| Phase | 名称 | 目标 | 预估量级 |
|-------|------|------|----------|
| **0** | 基线与门禁 | 回归脚本、bootstrap 可测、完成标准工具 | 小 |
| **1** | 数据正确性 | O1 K 线 + O5 抽样；O2 QMT 清理 | **阻塞项** |
| **2** | 架构收敛 | O3 MCP 单入口；O4 回测诚实；O6 日历；配置/文档对齐 | 中 |
| **3** | 知识闭环 MVP | level_origin Case + vault 双写 + 回载 + 备注提醒 | **主新功能** |
| **4** | Relation | 跨标的跨周期关联 + relation_note + MCP 检索 | 主新功能 |
| **5** | 产品增强（可选） | 自选/热力图/预警/前端拆分/真回测 | 按需 |

**原则：** Phase 1 未过，不宣称知识库「可学」；错误 timestamp 会污染全部 Case。

---

# 第二部分：新设计方案

## 2.1 设计原则

1. **正确先于功能** — K 线 timestamp 是标注的坐标系。  
2. **几何 / 语义 / 关联分离** — overlay ≠ Case ≠ Relation。  
3. **用户结论主权** — 关联结论只来自用户输入；系统无判定引擎。  
4. **双写** — vault md + SQLite 索引；Agent 查索引，人改 md 可再同步 frontmatter。  
5. **面板轻、Obsidian 重** — 面板 3–5 秒落卡；长文在 vault。  
6. **YAGNI** — 不做自动共振、相关计算、向量库先行、多图同屏（可后置）。  
7. **禁止顺手优化** — 每 Phase 只动清单内文件。

## 2.2 领域模型

### Case（L1）

- `type`: `level_origin` | `chart-annotation` |（中期）`signal`  
- `symbol`, `symbol_name`, `asset_type`, `period`  
- `source_bar` + `price_element` + `price`（level_origin）  
- `level` + `overlays[]`  
- `reactions[]`  
- `notes`, `reminders[]`  
- `relation_ids[]`  
- `outcome`（可选，用户手标；**非系统回测**）  
- `agent.distillable`, `agent.quality`

### Relation（L2）

- `members[]`: 每项含 `case_id?`, `symbol`, `asset_type`, `period`  
- `relation_note`: **唯一结论原文**  
- `user_tags[]`: 用户词，非真值  
- `notes`, `reminders[]`  
- **禁止字段：** auto_score, correlation, system_hypothesis, verified_by_backtest

### 存储布局

```
# 可配置 vault 根
{VAULT}/
  charts/cases/{symbol}/{case_id}.md
  charts/relations/{yyyy}/{mm}/{rel_id}.md
  charts/attachments/{case_id}.overlays.json
  charts/attachments/{case_id}.png          # 可选

# 系统索引
data/annotation_index.sqlite
  tables: cases, relations, case_members_fts, reminders
```

配置（`core/config.py` 新增）：

```python
ANNOTATION_VAULT_PATH = os.environ.get("ANNOTATION_VAULT_PATH", str(BASE_DIR / "vault"))
ANNOTATION_INDEX_DB = DATA_DIR / "annotation_index.sqlite"
ANNOTATION_REQUIRE_INTENT = True  # level 默认 support/resistance
```

## 2.3 API 契约（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/annotations` | 创建 Case（含 level_origin 字段） |
| GET | `/api/annotations` | `?symbol=&period=&type=` |
| GET | `/api/annotations/<id>` | 详情 |
| PATCH | `/api/annotations/<id>` | 备注、反应点、提醒、outcome |
| POST | `/api/annotations/<id>/reactions` | 追加反应点 |
| GET | `/api/annotations/<id>/overlays` | 回载几何 |
| POST | `/api/relations` | 创建关联 |
| GET | `/api/relations` | 列表/搜索 note |
| GET | `/api/relations/<id>` | 详情 |
| PATCH | `/api/relations/<id>` | 改 note、成员、提醒 |
| POST | `/api/relations/<id>/members` | 加入成员 |
| GET | `/api/reminders/due` | 到期提醒 |
| GET | `/api/annotations/config` | vault 是否配置等（无密钥） |

统一响应：`{ "ok": true, "data": ... }` / `{ "ok": false, "error": "...", "code": "..." }`。

## 2.4 MCP 工具（增量，挂在 `/mcp/*`）

| 工具 | 作用 |
|------|------|
| `save_annotation` | 等同 POST annotations |
| `get_annotation` / `search_cases` | 检索 |
| `load_case_overlays` | 广播 SSE 回载 + 可选 set_symbol/period |
| `add_reaction` | 反应点 |
| `save_relation` / `search_relations` / `get_relation` | 关联 |
| `list_due_reminders` | 提醒 |
| **禁止** | `detect_resonance` / `score_relation` 类工具 |

## 2.5 前端交互（最小）

1. **设源 K** → 选 O/H/L/C → 自动水平线 → 迷你表单（role + 备注）→ 保存 Case。  
2. **添加反应点** → 点 K → kind。  
3. **加入关联** → 草稿篮跨切换 → 写 `relation_note` → 完成。  
4. Case/Relation 列表 + 到期提醒条。  
5. 无「系统共振」「相关度」UI。

## 2.6 与现有模块衔接

| 现有 | 改法 |
|------|------|
| `services/kline_service.py` | Phase1 修 resample/去重；标注读 bar OHLC 经现有 `/api/kline` |
| `services/overlay_service.py` | 保留几何；Case 存旁路 overlays JSON；回载可调 create/sync |
| `api/signal_ai_routes.py` `/mcp/*` | 注册新 tools；handlers 调 annotation 服务 |
| `api/mcp_routes.py` | Phase2 改为代理到 `/mcp/*` 或只保留非重复能力并文档化 |
| `static/index.html` | 采集 UI；优先抽 `static/js/annotation-ui.js` 减轻单文件 |
| `core/config.py` | vault 路径、QMT 端口列表收敛 |
| `logs/drawings/*` | 旧 overlay 文件保留；新 Case 不强制迁移 |

---

# 第三部分：详细技术实现方法

## 3.1 Phase 0 — 基线与门禁

### 目的

任何修复可回归；导入 app 可测。

### 实现要点

1. **环境：** 测试默认 `BOARD_APP_AUTO_BOOTSTRAP=0`、`QMT_ENABLED=0`（无 QMT 单测）。  
2. **脚本：** `scripts/verify_kline_periods.py`  
   - 输入：code, type, periods  
   - 断言：timestamps 严格递增；无重复 date；`len(5m) ≈ len(1m)/5`（容差可配）  
3. **契约夹具：** `tests/fixtures/kline_sample.json` 录制修复后金样（可选）。

### 完成标准

```text
BOARD_APP_AUTO_BOOTSTRAP=0 pytest tests/ -q --collect-only   # exit 0
python scripts/verify_kline_periods.py --help               # exit 0
```

### 主要文件

- Create: `scripts/verify_kline_periods.py`  
- Modify: `tests/conftest.py`（若无则建）设置 env  
- Modify: `core/lifecycle.py` / `app.py` 确认 env 跳过 bootstrap  

---

## 3.2 Phase 1 — K 线正确性 + QMT 收敛（O1/O2/O5）

### 3.2.1 K 线（O1）

**根因排查顺序（强制）：**

1. `services/kline_service.py`：`get_kline` → `_do_load` → `_resample_from_1m` / `_load_resample`  
2. `data_loader.py`：日期规范化、拼接、缓存写入  
3. `data/sqlite_repo.py`：主键/去重、date 格式  
4. 前端 Datafeed：是否二次改 timestamp  

**实现方法：**

| 问题 | 改法 |
|------|------|
| 日线重复 | load 后 `drop_duplicates(subset=[date])`；入库 UNIQUE(symbol,period,date) 若缺则补 |
| 5m/15m=1m | 确认 resample rule（`5min`/`15min`）在正确 DatetimeIndex 上；禁止 period 参数被忽略走日线缓存 |
| 1H/2H/4H/月季年循环 | 独立 period→rule 映射表；禁止错误 fallthrough 到同一 loader |
| 时区 | 统一 Asia/Shanghai naive 或统一 ms 时间戳约定，前后端一致 |

**验证：**

```bash
# 修复后（需 QMT/本地库有数据）
python scripts/verify_kline_periods.py --type index --code sh000001 \
  --periods 1m,5m,15m,1H,daily,weekly
# Expected: 各 period PASS；失败打印首个违规 timestamp
```

**东财抽样（O5 轻量）：**

- Create: `scripts/verify_board_vs_eastmoney.py`  
- 抽样 N 个 BK，比最近收盘/涨跌；报告 JSON；CI 可不强制 0 误差但盘后人工看。

### 3.2.2 QMT（O2）

1. `core/config.py`：`QMT_PORTS = [58600]`；移除 58610 默认。  
2. Grep 全仓 `58610`、`QMT_DATA_PORT`、双 client，只留 `data/qmt_client.py`。  
3. `miniqmt_*`：若文档称移除，API 返回明确 deprecated 或删路由（二选一写进 CHANGELOG）。

**完成标准：**

```bash
rg "58610" --glob '!venv/**' --glob '!**/archive/**'   # 无业务命中或仅注释说明已废弃
python -c "from core.config import QMT_PORTS; assert QMT_PORTS==[58600]"
```

---

## 3.3 Phase 2 — MCP / 回测 / 工程卫生（O3/O4/O6/O11）

### 3.3.1 MCP 单入口（O3）

**策略（推荐）：**

- **对外契约：** `/mcp/tools|call|sse|event`（`api/signal_ai_routes.py` + `mcp/*`）  
- **`api/mcp_routes.py`：**  
  - 方案 A：`/api/mcp/call` 内部 forward 到同一 handler（兼容旧客户端）  
  - 方案 B：410 + 文档指向 `/mcp/*`  
- `core/config.py`：`MCP_TOOLS_ENDPOINT = '/mcp/tools'` 等与真实一致  
- `agent_connector.py` / `mcp_board_bridge.js`：只打 `/mcp/*`  
- 工具表 **单一** 定义于 `mcp/tools.py`

**完成标准：** `GET /mcp/tools` 与 bridge 列表一致；`agent_connector` 连通 MCP 项满分路径不 404。

### 3.3.2 回测诚实化（O4）

- 若 vectorbt 未装或 schema 不齐：接口返回  
  `{"ok":false,"code":"BACKTEST_UNAVAILABLE","error":"..."}` status 503  
- 删除或隔离返回模拟 Sharpe 的路径  
- README 标明状态

### 3.3.3 交易日历（O6）

- `data/trade_calendar.py`：Tushare `trade_cal` 缓存本地 JSON  
- 替换纯 `weekday>=5` 守卫（`data_loader` / subscribe 逻辑）

### 3.3.4 文档

- 新建正式 `README.md`（启动、端口、QMT、vault 环境变量）  
- 同步 `ARCHITECTURE.md` 与本方案 Phase 状态一行表  

---

## 3.4 Phase 3 — 知识闭环 MVP（Case / level_origin）

### 3.4.1 后端服务

**Create:** `services/annotation_service.py`

职责：

- `create_case(payload) -> case`  
- `update_case` / `add_reaction` / `list_cases` / `get_case`  
- `_write_vault_md(case)`  
- `_write_overlays_json(case)`  
- `_upsert_index(case)`  
- `get_overlays_for_reload(case_id)`

**Create:** `services/vault_writer.py`

- 原子写：tmp → rename  
- YAML frontmatter + body 模板（level_origin 专用段落：源K / 水平位 / 反应 / 备注）

**Create:** `data/annotation_repo.py`（SQLite）

```sql
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  type TEXT,
  symbol TEXT,
  symbol_name TEXT,
  asset_type TEXT,
  period TEXT,
  payload_json TEXT NOT NULL,  -- 完整 Case
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX idx_cases_symbol_period ON cases(symbol, period);

CREATE TABLE reminders (
  id TEXT PRIMARY KEY,
  owner_type TEXT,  -- case | relation
  owner_id TEXT,
  at TEXT,
  message TEXT,
  status TEXT
);
```

**Create:** `api/annotation_routes.py` + 在 `api/__init__.py` register。

### 3.4.2 level_origin 创建算法

```text
输入: symbol, asset_type, period, source_bar{timestamp, ohlc}, price_element, level.role, notes?
1. price = ohlc[price_element]  # 或 custom
2. overlay horizontalLine @ price，anchor timestamp = source_bar.timestamp
3. case_id = f"case_{yyyymmdd}_{symbol}_{seq}"
4. 写 overlays json + md + sqlite
5. 返回 case（含 overlay 列表供前端确认）
```

**价格一致性：** `abs(level.price - source_bar.ohlc[price_element]) < 1e-6 * scale` 否则 400。

### 3.4.3 前端

**Create:** `static/js/annotation-ui.js`（由 index.html 引入）

- `pickSourceBar(bar)` — 从十字星/回调取 OHLC  
- `showPriceElementPicker(ohlc)` — 开高低收  
- `drawHorizontal(price, ts)` — 调 Pro API 或本地画线后同步  
- `saveCaseDraft()` → `POST /api/annotations`  
- `reloadCasesForSymbol(symbol, period)` → 画 marker + line  
- `showDueReminders()` → `GET /api/reminders/due`

**Hook 点：** 在 `selectBoard` / period 切换后调用 reload；在图表 ready 后 bind 右键菜单。

### 3.4.4 提醒

- 存 reminders 表；启动与每分钟轻量轮询或 SSE `annotation_reminder`  
- Phase3 仅面板黄条，不做强制 Win 通知（可选后续）

### 3.4.5 MCP

- `mcp/tools.py` 增加工具定义  
- `mcp/handlers.py` 调 `annotation_service`  
- `load_case_overlays`：`sse_manager.broadcast` + 可选 set_symbol/period

### 3.4.6 测试

```python
# tests/test_annotation_level_origin.py
def test_create_level_origin_price_matches_open(tmp_path, monkeypatch):
    # vault 指到 tmp_path
    # create case price_element=open
    # assert level.price == open
    # assert (tmp_path / ... md).exists()
    # assert repo.get(case_id) is not None
```

**完成标准：**

1. API 创建 level_origin 后 vault 有 md + overlays json。  
2. 再 GET overlays 与价格一致。  
3. 添加 reaction 后 payload 长度 +1。  
4. 前端手工：源K→开→线→保存→切走再回来线在。  

---

## 3.5 Phase 4 — Relation（用户声明关联）

### 3.5.1 模型与服务

**Extend:** `annotation_service` 或 **Create:** `services/relation_service.py`

```sql
CREATE TABLE relations (
  id TEXT PRIMARY KEY,
  relation_note TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT,
  updated_at TEXT
);
```

- `create_relation({members, relation_note, user_tags})`  
- `add_member(rel_id, member)` — member 可 `case_id` 或仅 symbol+period  
- **代码审查清单：** 禁止 import 行情做「是否反向」；禁止相关计算库  

### 3.5.2 前端草稿篮

```javascript
// localStorage key: annotation_relation_draft
{ id, member_case_ids: [], relation_note: "" }
```

- 保存 Case 后按钮「加入关联篮」  
- 「完成关联」弹窗：必填或可空 `relation_note`（产品上建议鼓励填写，不强制）  
- 成员列表点击 → `set_symbol` + `set_period` + reload case  

### 3.5.3 检索

- SQL LIKE / FTS5 on `relation_note`  
- MCP `search_relations(q="红利")` 返回 note 原文  

### 3.5.4 完成标准

1. 两腿不同 `asset_type`、不同 `period` 可进同一 Relation。  
2. `relation_note` 往返一致。  
3. 全仓无 `detect_resonance` / correlation 评分。  
4. vault 有 `rel_*.md` 与成员 wikilink。  

---

## 3.6 Phase 5 — 可选产品增强（原 N 轨，压缩）

仅在 1–4 稳定后：

| 项 | 说明 |
|----|------|
| N1 自选 | 扩展 classification 存储 |
| N3 热力图 | 消费 `board-changes` |
| N7 预警 | 与 reminders 分离：预警=行情规则，reminders=笔记备忘 |
| O17 前端拆分 | index.html 拆 nav/chart/search/annotation |
| N15 真回测 | 独立里程碑，对齐 kline schema |

---

## 3.7 风险与依赖

| 风险 | 缓解 |
|------|------|
| KLineChart Pro 源 K 选取 API 不直观 | 用十字星当前 bar + 自定义右键，不依赖官方「语义源K」 |
| vault 路径无效 | 启动检查；API 返回 `VAULT_NOT_CONFIGURED` |
| md 与 sqlite 不一致 | 以 sqlite 为 API 真相；提供 `POST /api/annotations/reindex` |
| 大 index.html 回归 | 新 UI 放独立 js；Phase3 最小 DOM |
| 用户期望系统「发现」关联 | 产品文案明确：只记录你的判断 |

## 3.8 初见审查

| 反驳 | 回应 |
|------|------|
| 先做知识库再修 K 线更快 | 错误坐标会批量污染 vault，返工成本更高 |
| Relation 与 Case 合成一张表 | 跨周期多腿与单腿生命周期不同，分离更清晰 |
| 只要 Obsidian 不要 SQLite | Agent/MCP 稳定检索与 due 提醒需要索引 |
| 面板做富文本 | 与「轻量采集」冲突，违背已确认边界 |

---

# 第四部分：可执行任务清单（按序）

> 执行代理：完成一任务勾一项；每任务末跑对应验证命令。

### Task 1: Phase0 测试 env 与 kline 校验脚本骨架

**Files:**
- Create: `scripts/verify_kline_periods.py`
- Create: `tests/conftest.py`（设置 `BOARD_APP_AUTO_BOOTSTRAP=0`）

**Steps:**
1. 写脚本：解析 args，对每个 period 调 `KLineService.get_kline`（或 HTTP），检查 timestamp 单调与去重。  
2. 无数据时 exit 2 并打印 skip 原因（不伪装 PASS）。  
3. Commit: `chore: add kline period verification script`

**Done when:** `--help` 可用；无数据时非 0 且信息明确。

---

### Task 2: Phase1 诊断并修复 resample / 去重

**Files:**
- Modify: `services/kline_service.py`
- Modify: `data_loader.py` / `data/sqlite_repo.py`（按诊断）
- Test: `tests/test_kline_period_invariants.py`

**Steps:**
1. 写失败单测：构造 1m DataFrame，断言 5m 根数与 OHLC 聚合。  
2. 修 `_resample_from_1m` 与 period 路由。  
3. 日线路径 drop_duplicates。  
4. 跑 `verify_kline_periods.py` 有数据环境。  
5. Commit: `fix: correct kline resample and dedupe`

**Done when:** 单测 PASS；有数据时脚本对 sh000001 多周期 PASS。

---

### Task 3: Phase1 QMT 端口与引用收敛

**Files:**
- Modify: `core/config.py`
- Modify: 所有 `58610` 命中文件
- Modify: `api/system_routes.py`（miniqmt 策略）

**Done when:** `QMT_PORTS==[58600]`；业务代码无 58610。

---

### Task 4: Phase2 MCP 配置与入口对齐

**Files:**
- Modify: `core/config.py` MCP 路径
- Modify: `api/mcp_routes.py`（forward 或 410）
- Modify: `agent_connector.py`、`mcp_board_bridge.js`
- Modify: `mcp/tools.py` 单一表

**Done when:** bridge 与 connector 只依赖 `/mcp/*`；tools 列表唯一。

---

### Task 5: Phase2 回测诚实响应

**Files:**
- Modify: `services/backtest_service.py`、`vectorbt_backtest.py`、`api/mcp_routes.py`

**Done when:** 不可用时 503 + `BACKTEST_UNAVAILABLE`；无假 metrics。

---

### Task 6: Phase3 annotation_repo + vault_writer + annotation_service

**Files:**
- Create: `data/annotation_repo.py`
- Create: `services/vault_writer.py`
- Create: `services/annotation_service.py`
- Test: `tests/test_annotation_level_origin.py`

**Done when:** 单测创建 case 后 tmp vault 与 sqlite 均有记录；价格与 open 一致。

---

### Task 7: Phase3 annotation API 注册

**Files:**
- Create: `api/annotation_routes.py`
- Modify: `api/__init__.py`
- Modify: `core/config.py` vault 配置

**Done when:**  
`curl -X POST http://127.0.0.1:5000/api/annotations -d {...}` → ok；GET 列表可见。

---

### Task 8: Phase3 前端 level_origin 采集与回载

**Files:**
- Create: `static/js/annotation-ui.js`
- Modify: `static/index.html`（引入与菜单）

**Done when:** 手工路径：源K→开→保存→切换回→线与标记在；提醒 due 可显示。

---

### Task 9: Phase3 MCP 标注工具

**Files:**
- Modify: `mcp/tools.py`、`mcp/handlers.py`、`mcp/schemas.py`

**Done when:** `/mcp/call` `save_annotation` / `search_cases` / `load_case_overlays` 可用。

---

### Task 10: Phase4 Relation 服务 + API + UI 篮 + MCP

**Files:**
- Create/Modify: `services/relation_service.py`（或并入 annotation_service）
- Modify: `api/annotation_routes.py` 或 `api/relation_routes.py`
- Modify: `static/js/annotation-ui.js`
- Modify: `mcp/tools.py`、`handlers.py`

**Done when:** 跨 period/类型两成员 + `relation_note` 落盘可检索；无自动判定代码。

---

### Task 11: 文档与 STATE 同步

**Files:**
- Create/Update: `README.md`
- Modify: `ARCHITECTURE.md`、`STATE.md`
- Modify: 本文件勾选状态

**Done when:** 启动方式、vault 环境变量、Phase 完成态与代码一致。

---

# 第五部分：执行方式建议

1. **本会话 Subagent 逐 Task** — 适合 Task 1–5 基建。  
2. **新会话按 executing-plans** — 适合 Task 6–10 大块功能。  
3. **硬门禁：** Task 2 未 PASS 不开始 Task 6。

**不在本期做：** 自动共振、向量检索、多图网格、技能市场重做、真 vectorbt 全量（单列里程碑）。

---

# 附录 A：level_origin 请求体示例

```json
{
  "type": "level_origin",
  "symbol": "BK0437",
  "symbol_name": "煤炭",
  "asset_type": "industry",
  "period": "daily",
  "source_bar": {
    "timestamp": 1762876800000,
    "date": "2025-11-12",
    "ohlc": { "open": 1180.5, "high": 1205, "low": 1172, "close": 1198 }
  },
  "price_element": "open",
  "level": { "role": "support", "price": 1180.5 },
  "notes": "数月后仍有效",
  "reminders": [
    { "at": "2026-07-25T09:30:00+08:00", "message": "复查支撑", "status": "pending" }
  ]
}
```

# 附录 B：Relation 请求体示例

```json
{
  "relation_note": "中证红利见底一般意味着科创50见顶（个人经验）",
  "user_tags": ["风格轮动", "相反"],
  "members": [
    {
      "case_id": "case_xxx",
      "symbol": "sh000922",
      "symbol_name": "中证红利",
      "asset_type": "index",
      "period": "weekly"
    },
    {
      "case_id": "case_yyy",
      "symbol": "sh000688",
      "symbol_name": "科创50",
      "asset_type": "index",
      "period": "weekly"
    }
  ]
}
```

# 附录 C：置信度

| 区块 | 置信度 |
|------|--------|
| 分期与门禁顺序 | 高 |
| level_origin / Relation 模型 | 高（已与用户对齐） |
| K 线具体 bug 行号 | 中（须 Task2 诊断锁定） |
| Pro API 画线事件细节 | 中（实现时对照 Pro 文档/运行时） |
