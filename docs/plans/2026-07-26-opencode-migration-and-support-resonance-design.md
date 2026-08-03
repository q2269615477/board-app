# 复盘软件：OpenCode 迁移 + 支撑位/跨板块共振 设计文档

> **日期：** 2026-07-26
> **状态：** 设计已与用户对齐，待实现
> **供执行代理：** 按第六部分 **分阶段任务清单** 逐任务推进；每任务有可机械判定的完成标准。
> **关系：** 本文**收窄并取代** `docs/plans/2026-07-19-system-optimization-master-plan.md` 的 Phase 3/4 知识闭环部分，聚焦到用户的**唯一目标**；并新增 Phase 0 平台迁移。数据模型沿用现有 `services/annotation_service.py` 的 `level_origin`。

---

## 第一部分：目标与决策

### 1.1 唯一目标（经多轮澄清收窄）

用户要的是一个**交易复盘软件**，让 AI 学会并执行其交易系统，**只需两件事**：

1. **支撑/阻力位识别** —— 支撑位 = 一条**水平线**，锚定在某根「源 K」的某个价格要素（开/收/高/低之一），向后贯穿很远；未来某根 K 在此起稳反转即为**反应点**。
2. **跨板块共振支撑** —— 一组**同方向**的标的（龙头板块 + 同方向指数 + 相关板块/个股）**同时**逼近各自的支撑位时，形成「共振支撑」，是高价值机会。

> 用户原话：「**这是我唯一需要它学会的。**」
> 因此本期**不做**：通用规则引擎、进出场规则一等对象、会话因果链的进一步扩展、通用 Relation、向量库、真回测。

### 1.2 演进弧：教学期 → 自主期

| 期 | 谁定支撑位 | 谁定关联组 |
|----|-----------|-----------|
| **教学期** | 用户亲手画 | 用户手动指定 |
| **自主期** | AI 学会后主动提议 | 按现有 14 分类 / 申万映射自动分组 |

用户明确：支撑位识别要的是**真学习**（AI 学其画法主动提议），不是纯启发式工程。

### 1.3 铁律的重画（重要）

现有代码有一条铁律：**系统不判定共振/反向**——`annotation_service.create_relation` 与 `api/annotation_routes.py` 主动 `pop` 掉 `auto_score / correlation / system_hypothesis / verified_by_backtest`；每个 MCP handler 都写「禁止自动生成共振或反向结论」。

而**共振正是用户唯一要的**。因此本期**有意识地解除这条铁律的字面禁令**，但**保留其精神**：

- **判断标准来自用户**（几个标的、各自到支撑、距离阈值、要不要同方向——全部用户定义），不是 AI 凭空发明。
- **AI 只做两件事**：① 按用户标准扫盘产出**候选**；② 每个候选附上**用户当初画那条线时的原话**。
- **「这是不是机会、买不买」的最终判断权始终在用户。** 系统不输出「买/卖」结论。

### 1.4 平台迁移：WorkBuddy → OpenCode

**动机（用户）：** WorkBuddy 是闭源黑箱；要一套开源、可控、不锁定的栈。

**目标形态（分窗）：**

```
┌─────────────────────────┐        ┌──────────────────────────────┐
│  OpenCode（终端 · AI大脑）│ <MCP>  │  Flask :5000  +  真实浏览器面板 │
│  你选的模型 provider     │ ─────► │  KLineChart Pro · 点击标注     │
│  自定义"盘面分析师"agent  │ <SSE── │  （无 CSP 限制）              │
└─────────────────────────┘        └──────────────────────────────┘
```

**关键收益：** WorkBuddy 内置浏览器的 CSP 是 `app.py` 里 `window.__init_data__` 注入黑魔法的**唯一存在理由**。换真实浏览器后**整坨可删**，前端回归正常 `fetch`，为 Phase 1 重建标注 UI 扫清障碍。

**关键事实：** `mcp_board_bridge.js` 是标准 stdio JSON-RPC ↔ Flask HTTP，**无 WorkBuddy 专属代码**，OpenCode 直接连即可。

---

## 第二部分：目标架构（分层）

```
[捕获层]   真实浏览器面板：选源K · 吸附OHLC · 画水平线 · 选支撑/阻力 · 备注
   ↓ 落成 typed 对象（+ 特征快照，为学习攒样本）
[知识层]   level_origin Case（价位 + 角色 + 源K + 反应点 + 备注）
           —— SQLite 索引 + Obsidian vault 双写（已存在）
   ↓
[发现引擎] 共振扫描：一组标的 → 各自 support 距现价 <阈值 → 共振候选（确定性硬算）
   ↓
[AI 层]    OpenCode agent：① 调 MCP 拿候选 ② 用用户原话解释「为何符合你的系统」
           （教学期后：学用户画法主动提议支撑位）
```

**两个窗口之间的神经：** `/api/ctx`（浏览器 POST 当前 symbol/period）+ MCP `get_panel_context`（AI 读）。分窗后这是唯一纽带，Phase 0 必须确认它在真实浏览器正常。

---

## 记忆与学习架构（贯穿全程，Phase 1 起遵守）

> 本节是**跨阶段规范**，决定 `feature_snapshot` 与 `notes` 怎么设计、MCP 工具怎么返回、agent 指令怎么写。先于所有 Phase 落地。

### A. 前置认知：固定 LLM 不「在脑子里变聪明」

OpenCode 用的是**权重固定**的 LLM，它不会因为被喂过就更聪明。**「聪明」积累在知识库里，模型每次「现读现用」（RAG）。** 这对复盘工具是优点：**可审计、可纠正、永不悄悄漂移**——「它学到了什么」= 库里的数据与你的原话，错了直接改数据。契合本项目的信任纪律（AI 只出候选 + 复述、用户裁判）。

### B. 信息的两条路

```
写入：浏览器画线 → Flask → ① SQLite(typed 价位/组，精确)  ② vault md(原话，供检索)
读取：OpenCode 模型 → MCP 工具(search_cases/get_case/scan_resonance/get_panel_context/propose_support_levels)
      → 你的数据进入当前对话上下文
```
**无「训练」步骤。** 模型靠 MCP 把相关标注拉进上下文来「懂」你。

### C. 分层记忆（不同信息 → 不同载体，硬塞一处就学不好）

| 信息类型 | 最佳载体 | 机制 | 例 |
|---|---|---|---|
| **稳定原则/定义** | agent 指令文件（AGENTS.md） | 常驻系统提示 | 「支撑=源K的OHLC被后续K尊重；共振=一组同时到位」 |
| **精确事实** | SQLite typed 对象 | 工具确定性查询 | BK0727 支撑 1180.5（**绝不让 LLM 背价格，会编**） |
| **经验/理由原文** | vault md | 按需检索复述 | 「1180 是11-12放量反转开盘价，后被尊重3次」 |
| **画法风格** | 精选样本集 | few-shot 类比 | 20 个范例 → 对新图提议（Phase 3） |
| **窄模式（可选）** | 特征快照上训的小模型 | 轻量分类器 | 学「哪根K像支撑源」（Phase 3 档B） |

### D. 核心原则：**精确回忆** 与 **模糊泛化** 分离

- 精确的（你的价位、原话）→ 存数据、用工具**查**，**不让 LLM 记**。
- 模糊的（对新图提议支撑位）→ 才交给 LLM 泛化。
- 违反 = 让 LLM 背你的支撑价 = 它必给你编个「差不多」的数字。

### E. 不用微调（fine-tuning）

微调是技术上的「真学习」，但对本项目是错工具：需自托管开源模型 + 数百上千样本 + 评估管线（重、慢）；**黑箱、无法引用原话、坏了难纠、过拟合主观且在变的画法**。RAG + few-shot 可即时更新、能溯源、能纠错——完胜。**本项目不做微调。**

### F. 真正的杠杆：反馈质量（比机制重要得多）

学习效果 ~90% 取决于喂进去什么，而非用哪种机制：

1. **记「为什么」，不只记「画在哪」。** 光一条 1180 的线几乎没教任何东西；带上理由才教得会。**`notes` 的质量 = 学习效果的天花板。** → 前端迷你表单应**引导**填写理由（源K定位 + 依据）。
2. **也要记负样本。** 「否掉这个候选，因为量能不够」比只给正例更能抓住你的风格 → Phase 3 的 accept/reject 闭环价值极高。
3. **闭环要快。** 对提议做接受/否决 = 主动学习，信号最强。

### G. 对本文档的约束（据此设计）

- `feature_snapshot`（精确、机读）与 `notes`（理由原文、供检索复述）**职责分离**，二者都从 Phase 1 起落盘。
- MCP 工具返回**既给 typed 数据又给原话 md**，让 agent「用数据算、用原话讲」。
- agent 指令文件承载**稳定定义**，不承载具体价位。
- Phase 3 提议走 few-shot（不微调）；档B 分类器只在数据足量后可选叠加。

---

## 第三部分：分阶段详细设计

### Phase 0 — 平台解耦（迁 OpenCode + 删 CSP）

**目的：** 面板跑在真实浏览器；MCP 接入 OpenCode；删掉 WorkBuddy 专属复杂度。先于 Phase 1，避免标注 UI 写两遍。

**删除 / 简化**

- `app.py` 的 `/` 路由（`app.py:126-190`）：去掉对 CSP 绕过的**依赖**。前端改为正常 `fetch('/api/classification/load')` 与 `/api/kline/...`。
  - `window.__init_data__` 嵌入可**保留为首屏优化**（可选），但前端不得再**依赖**它存在。
- 前端 `loadClassification()` / `getHistoryKLineData()`：删除「优先读 `__init_data__`、否则不 fetch」的短路分支，统一走 fetch。
- WorkBuddy 专属：`~/.workbuddy/mcp.json` 注册、WorkBuddy automations —— 不再使用。

**保留（一行不改）**

- Flask 全部 API、SQLite、QMT/Tushare 管线、SSE `/api/events`、`/mcp/*`、`mcp_board_bridge.js`。

**新增 / 调整（面向 OpenCode）**

- OpenCode MCP 配置指向 `mcp_board_bridge.js`（stdio）。若 OpenCode 支持 remote/HTTP MCP，可直连 `/mcp/*` 省掉 node 桥（实现时按 OpenCode 版本的 MCP 配置格式定）。
- OpenCode 自定义 agent 指令文件（`AGENTS.md` 或 OpenCode 对应机制）：把它从「编码助手」调成「**盘面复盘分析师**」，声明可用 MCP 工具、强调「只出候选 + 复述用户原话、不给买卖结论」。
- CORS 已允许 `127.0.0.1:5000`（`app.py:57-60`），真实浏览器直连无碍。

**完成标准**

1. 真实浏览器（Chrome/Edge）开 `http://127.0.0.1:5000/`，导航/K线/搜索/指数栏全部正常，控制台无 CSP 报错。
2. 前端代码中不再有依赖 `window.__init_data__` 才能渲染的路径。
3. OpenCode 里 `tools/list` 能列出 board-app 的 MCP 工具；`get_panel_context` 能读到浏览器当前标的。
4. 在浏览器切板块 → OpenCode 调 `get_panel_context` 返回的 symbol/period 与之一致。

**主要文件：** `app.py`、`static/js/nav-panel.js`/`chart-core.js`（`loadClassification`/`getHistoryKLineData`）、OpenCode 配置、agent 指令文件。

---

### Phase 1 — typed 支撑位 + session 桥接（教学捕获）

**目的：** 用户画的每条 SR 线落成**可扫描的 typed 价位对象**，并**同时开始积累训练样本**（Phase 3 的数据发动机）。

**后端：大体已存在**。`level_origin` 的 create/update/add_reaction/list/get + vault 双写已在 `services/annotation_service.py` + `data/annotation_repo.py` + `services/vault_writer.py` + `api/annotation_routes.py`。本阶段主要是**前端桥接** + **新增特征快照字段**。

**前端桥接点（复用已跑通的 session-ui 能力）**

- `snapPriceElement()`（`static/js/session-ui.js:297-318`）：已把点击价吸附到最近 OHLC。
- `barToKbar()`（`:320-356`）：已产出 `{price_element, price, ohlc, symbol, period}`。
- 新增交互：画水平 SR 线时弹迷你表单 → 选 **支撑/阻力(role)** + 备注 → `POST /api/annotations`（`type=level_origin`）。
- **切标的自动回载**（当前缺失）：`selectBoard` / period 切换后 `GET /api/annotations?symbol=&period=` → 重绘所有 overlay（水平线 + 源K marker + 反应点 marker）。
- 反应点采集（当前前端缺）：点某根反应 K → `POST /api/annotations/<id>/reactions`。

**新增：特征快照 `feature_snapshot`**（写进 case，供 Phase 3 学习）

在**画线当下**抓取源 K 的上下文特征（哪怕 Phase 3 才用，也从第一天开始存）：

```json
"feature_snapshot": {
  "window_bars": 60,
  "is_local_extremum": true,
  "extremum_type": "low",
  "extremum_rank_in_window": 1,
  "volume_percentile": 0.88,
  "body_pct": 0.021,
  "is_gap": false,
  "dist_to_round_pct": 0.004,
  "prior_touches": 2,
  "trend_context": "downtrend",
  "atr14": 12.3
}
```

> 特征集合可迭代；关键是**从 Phase 1 起就落盘**，否则 Phase 3 无米下锅。

**完成标准**

1. 浏览器手工：选源K → 吸附到"开" → 画水平线 → 选"支撑" → 存 → 切走再切回 → **线与源K标记仍在**。
2. 该 case 在 `annotation_index.sqlite` 与 vault md 均有记录，`level.price == 源K.open`，且含非空 `feature_snapshot`。
3. 加一个反应点后 `reactions` 长度 +1 且图上出现 reaction marker。
4. MCP `search_cases("支撑")` 能命中并回读 vault md 正文。

**主要文件：** `static/js/session-ui.js`（或抽独立 `annotation-ui.js`）、`services/annotation_service.py`（加 `feature_snapshot` 透传与校验）、`services/vault_writer.py`（md 追加特征段落，可选）。

---

### Phase 2 — 共振检测（确定性，手动指定组）

**目的：** 用户的核心价值当场可用。**无 AI 猜测，纯硬算。**

**关联组模型（手动）**

```json
{
  "group_id": "grp_20260726_medical",
  "theme": "医疗",
  "members": [
    {"symbol": "BK0727", "asset_type": "industry", "role": "leader_board", "period": "daily"},
    {"symbol": "sh000933", "asset_type": "index", "role": "sector_index", "period": "daily"},
    {"symbol": "300760", "asset_type": "stock", "role": "leader_stock", "period": "daily"}
  ],
  "threshold_pct": 3.0,
  "min_aligned": 2
}
```

**共振算法（MVP = 当前快照同时逼近）**

```
输入：group（成员 + 阈值 threshold_pct + 最少对齐数 min_aligned）
对每个成员 m：
  1. 取现价 price_now（走 /api/spot 或最新 K 收盘）
  2. 取 m 的所有 level_origin（role=support）价位 L[]
  3. dist_m = min(|price_now - l| / l) over L，记录命中的 level 与其 case.notes
  4. m 视为"在支撑" iff dist_m*100 < threshold_pct
aligned = [在支撑的成员]
共振成立 iff len(aligned) >= min_aligned
score = f(len(aligned)/len(members), 各 dist 的紧密度)   # 0-100，仅排序用，非"信号强度断言"
输出：{group, aligned[], 每个 aligned 附 {member, hit_level, note(用户原话)}, score}
```

> 可复用 `board_scanner.py` 的 proximity 逻辑，但**价位来源必须是用户画的 level_origin**，不是 ATR 自动识别（那是 Phase 3）。
> 时间维「同时」MVP 用当前快照；后续可加「N 日窗口内先后到位」。

**API**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/resonance/groups` | 创建/保存关联组 |
| GET | `/api/resonance/groups` | 列出组 |
| POST | `/api/resonance/scan` | 传 group（或 group_id）→ 返回共振候选 |

**MCP 工具（解除铁律的那个，措辞要克制）**

- `scan_resonance(group_id | group)` → 候选列表。工具描述明确：**标准由用户定义；输出为候选 + 用户原话；不含买卖结论**。
- `list_resonance_groups` / `save_resonance_group`。
- handler 里**不得**计算 correlation、不得输出 verdict/信号强度断言，`score` 仅用于排序并注明。

**前端**

- 「关联组」管理面板：加成员（从当前标的一键加入、标 role）、设阈值。
- 「扫共振」按钮 → 列候选 → 点候选高亮各成员的命中支撑线 + 显示用户原话。

**完成标准**

1. 手动建一个 3 成员组，其中 2 个现价距各自支撑 <3% → `scan` 返回共振成立、`aligned` 含这 2 个、各附对应 case 的原话。
2. 全仓无 `correlation` / verdict 断言；`scan_resonance` 工具描述含「不给买卖结论」。
3. OpenCode 里让 AI「扫医疗组共振」→ 它调 `scan_resonance` → 复述候选与用户原话，不自创结论。

**主要文件：** `services/resonance_service.py`（新建）、`api/resonance_routes.py`（新建，`api/__init__.py` 注册）、`mcp/tools.py`+`mcp/handlers.py`（新工具）、前端组管理 UI。

---

### Phase 3 — AI 学画法、主动提议支撑位（真学习）

**目的：** 攒够样本后，AI 对新板块**提议**候选支撑位，用户 accept/reject。

**诚实的前置约束（务必对用户明示）**

- **冷启动**：需要 Phase 1 攒下的大批 `feature_snapshot` 样本，第一天必然差。
- **仅正样本**：用户只画认可的线；需引入**负样本**——把「启发式提议但用户否决」的候选记为负例。
- **主观 + 漂移**：提议一定有误判，**用户始终是裁判**。

**实现（从可解释到更硬核，分两档）**

*档 A（先做）：启发式候选 + 校准 + LLM few-shot*

1. **候选生成器**：pivot（窗口局部极值）+ 量能分位 + ATR 聚类（`board_scanner.py` 已有 SR 雏形）→ 原始候选价位（可解释）。
2. **校准**：用 Phase 1 用户已画集合当验证集，调阈值/权重使候选尽量命中用户实际画的线（报告 precision/recall）。
3. **LLM few-shot 提议**：把用户样本（源K特征 + 备注原话）当范例喂给 OpenCode 的模型，让它对新图**类比**提议，并**引用相似样本的用户原话**解释。可由 OpenCode agent 直接用 MCP（`get_kline` + `search_cases`）完成，无需额外训练基建。

*档 B（后续可选）：轻量分类器*

- 对 `feature_snapshot` 做 PU 学习（logistic / GBDT），输出每根 K 为支撑源的概率。数据够（数百+标注）再上，牺牲部分可解释性。

**反馈闭环**

- 提议 → 用户 accept（升级为正式 level_origin）/ reject（记负样本）→ 定期重校准。
- 新增 MCP 工具 `propose_support_levels(symbol, period)`（返回候选 + 依据的相似用户样本）。

**完成标准**

1. 对一个用户从未画过的板块，`propose_support_levels` 返回 Top-N 候选，每条附「像你在 X 画的那条」的原话依据。
2. accept 的候选落成 level_origin；reject 的进负样本表。
3. 在留出集上报告 precision/recall，且**明确展示给用户、不掩饰误判**。

**主要文件：** `services/level_proposal_service.py`（新建）、`board_scanner.py`（复用/抽取 SR 检测）、`data/annotation_repo.py`（负样本表）、`mcp/tools.py`+`handlers.py`。

---

### Phase 4 — 自主分组 + 全市场扫共振

**目的：** 学得可用后，AI 按现有分类自动成组、全市场发现共振。

- **自动分组**：复用 `static/board_classification.json`（14 分类）+ `data/qmt_sector_mapping.json`（申万映射），同主题的板块 + 对应指数 + 龙头个股自动成组。
- **龙头识别**（子决策）：板块内按成交额/涨幅排序取头部，或用户打 tag；MVP 可先用排序，标注为待确认。
- **全市场扫**：对所有自动组跑 Phase 2 的共振算法（支撑位来源 = 用户画的 + Phase 3 AI 提议且用户已确认的），产出全局 Top 候选。
- **调度**：用项目自带 `data_update_manager` 调度或系统计划任务盘后跑，SSE 推面板。

**完成标准**

1. 无需手动建组，系统按分类给出候选组并跑出全市场共振 Top 列表。
2. 每个候选可溯源到具体成员的支撑位与其来源（用户画 / AI 提议已确认）。

---

## 第四部分：数据模型附录

### level_origin Case（已存在，Phase 1 加 feature_snapshot）

```json
{
  "id": "case_BK0727_20260726_ab12cd34",
  "type": "level_origin",
  "symbol": "BK0727", "symbol_name": "医疗服务", "asset_type": "industry", "period": "daily",
  "source_bar": {
    "timestamp": 1762876800000, "date": "2025-11-12",
    "ohlc": {"open": 1180.5, "high": 1205, "low": 1172, "close": 1198},
    "price_element": "open", "price": 1180.5
  },
  "price_element": "open",
  "level": {"role": "support", "price": 1180.5},
  "reactions": [{"id": "rx_x", "timestamp": 1770000000000, "price": 1182, "kind": "起稳反转"}],
  "feature_snapshot": { "...": "见 Phase 1" },
  "notes": "数月后仍有效的强支撑",
  "reminders": [],
  "relation_ids": [],
  "agent": {"distillable": true, "quality": 3},
  "overlays": [ /* horizontalLine + markers */ ],
  "created_at": "...", "updated_at": "..."
}
```

### 共振组 / 共振候选：见 Phase 2。

### SQLite 索引（现状 + 新增）

- 现有：`cases` / `relations` / `reminders`（`data/annotation_repo.py`）。**注意：现无 FTS，搜索靠 `LIKE payload_json`**，样本增长后需补 FTS5（列入技术债）。
- 新增：`resonance_groups`、`level_proposals`（含负样本 label）。

---

## 第五部分：风险 / 决策 / 置信度

| 项 | 说明 |
|----|------|
| **风险：Phase 3 冷启动** | 最不确定的一环；靠 Phase 1/2 攒样本 + 明示误判缓解。不承诺「学得准」。 |
| **风险：分窗 UX** | 终端 agent + 浏览器面板两窗；`/api/ctx` 是唯一纽带，Phase 0 必须验证。 |
| **风险：OpenCode 编码取向** | 需专门写 analyst agent 指令，别漏算工作量。 |
| **决策：铁律解除** | 已与用户确认，有意识解除字面禁令、保留精神（标准来自用户、AI 只出候选+复述、用户裁判）。 |
| **技术债：无 FTS** | 现搜索 `LIKE payload_json`，数据增长会退化，择机补 FTS5。 |
| **技术债：两套标注模型** | session 因果链 vs Case/Relation 并存。本期以 level_origin 为主线；session 能力被复用为桥接，暂不强制合并。 |

**明确不做（YAGNI）：** 通用规则引擎、进出场规则对象、向量库、真回测、多图网格、自动「买/卖」信号。

**置信度**

| 区块 | 置信度 |
|------|--------|
| Phase 0 迁移可行性 | 高（MCP 桥已宿主无关、CSP 是唯一 WorkBuddy 耦合） |
| Phase 1/2 数据模型与算法 | 高（后端大体已存在、共振为确定性硬算） |
| Phase 3 学习效果 | 中偏低（冷启动 + 主观 + 仅正样本，先攒数据） |
| OpenCode MCP 配置细节 | 中（按实际 OpenCode 版本对齐） |

---

## 第六部分：分阶段任务清单（按序）

> 执行代理：完成一任务勾一项；每任务末跑对应完成标准。硬门禁：**Phase 1 未跑通不进 Phase 3**（错误样本会污染学习）。

### Phase 0

- [x] **T0.1** 前端去 CSP 依赖 —— 现役前端 `static/js/*`（`nav-panel.js` fetch `/api/classification/load`、`chart-core.js` fetch `/api/kline`）**已无 `__init_data__` 依赖**（旧入口与归档目录已在 2026-07-30 清理）。真实浏览器验证：导航(990板块)/指数栏/会话面板正常渲染，控制台零报错。
- [x] **T0.2** `app.py` `/` 路由简化 —— 65 行→8 行，移除分类文件读取 + DB/QMT 读 K 线 + `__init_data__` 注入。验证 `/` 返回 200 且 `__init_data__` 计数=0。
- [~] **T0.3** OpenCode 接入 —— 创建 `opencode.json`（`mcp.board-app`→`node mcp_board_bridge.js`）。桥 stdio 路径已端到端验证：`initialize`→board-app 3.0.0、`tools/list`→27 工具、`tools/call get_panel_context`→返回上下文。**待用户装 OpenCode 读取该配置。**
- [x] **T0.4** OpenCode「盘面分析师」agent 指令文件 —— 创建 `AGENTS.md`（角色 + 铁律 + 精确/模糊分离 + 工具清单 + 工作流）。

### Phase 1

- [ ] **T1.1** 前端画 SR 线 → 迷你表单（支撑/阻力 + 备注）→ `POST /api/annotations`(level_origin)。
- [ ] **T1.2** 切标的/周期自动回载 `GET /api/annotations?symbol=&period=` 并重绘 overlay。
- [ ] **T1.3** 反应点采集 → `POST /api/annotations/<id>/reactions`。
- [ ] **T1.4** 后端加 `feature_snapshot` 透传/校验；vault md 追加特征段落。
- [ ] **T1.5** 单测：create level_origin → price==源K要素、含 feature_snapshot、vault+sqlite 均有。

### Phase 2

- [ ] **T2.1** `services/resonance_service.py`：组模型 + 共振算法（确定性）。
- [ ] **T2.2** `api/resonance_routes.py`：groups CRUD + scan；`api/__init__.py` 注册。
- [ ] **T2.3** MCP：`scan_resonance` / `save_resonance_group` / `list_resonance_groups`（措辞：不给买卖结论）。
- [ ] **T2.4** 前端：组管理 + 扫共振 + 候选高亮 + 显示用户原话。
- [ ] **T2.5** 完成标准 3 项全过。

### Phase 3

- [ ] **T3.1** 候选生成器（复用 `board_scanner.py` SR）+ 对用户已画集校准，报告 P/R。
- [ ] **T3.2** `propose_support_levels` MCP 工具 + LLM few-shot（用样本原话解释）。
- [ ] **T3.3** accept/reject 反馈闭环 + 负样本表。

### Phase 4

- [ ] **T4.1** 按 classification + 申万映射自动分组（含龙头识别 MVP）。
- [ ] **T4.2** 全市场扫共振 + 盘后调度 + SSE 推面板。

### 收尾

- [ ] **T5.1** 更新 `PROJECT_BRIEF.md` / `docs/ARCHITECTURE.md`：平台改 OpenCode、新增 resonance 层、Phase 状态表。

---

*本文档随实现推进更新；Phase 完成后回勾任务并同步状态。*
