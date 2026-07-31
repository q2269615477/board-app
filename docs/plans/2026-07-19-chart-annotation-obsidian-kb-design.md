# 图表标注 → Obsidian 知识库 → Agent 学习闭环设计

**日期**：2026-07-19  
**状态**：设计草案（待用户确认后进入实现计划）  
**完成标准（本设计文档）**：列出必做/建议/后置功能、推荐数据模型、与现有 overlay/MCP 的衔接方式；用户确认边界后再写 IMPLEMENTATION_PLAN。

---

## 1. 目标

把「看盘画线」升级为「可复盘、可检索、可教给 Agent 的交易知识资产」。

闭环：

```
图表画线 → 语义笔记卡（意图+备注）→ Obsidian vault（人可读）
        → 机读索引（Agent 可查）→ 结果回填 → 规则蒸馏 → 回写图表
```

**原则**：

1. **几何层与语义层分离**：画线坐标是几何；「为什么画、属于哪种形态、后来对不对」是语义。
2. **Obsidian 存人读笔记**；系统侧另有 JSON/SQLite 索引供 Agent 稳定查询。
3. **Agent 只学带标签 + 带结果的案例**，不学裸画线。
4. **先捕获、后结构化**：日常 3 秒完成保存；标签尽量一键预设，备注可后补。

---

## 2. 现状缺口

| 已有 | 缺什么 |
|------|--------|
| `overlay_service` 按 symbol 存 JSON 画线 | 无意图、备注、周期、截图、结果 |
| MCP `create_overlay` / SSE 控图 | 无「保存为知识卡」工具 |
| `signal_service` 买卖信号 | 与画线未统一为同一案例对象 |
| 面板 `ctx` 当前标的 | 未与笔记绑定、未导出 vault |

---

## 3. 推荐数据模型：Annotation Case（案例卡）

每个「有意义的标注动作」= 一张 **Case**，不是一条裸 overlay。

### 3.1 Frontmatter（Obsidian YAML）示例

```yaml
id: case_20260719_BK0437_001
type: chart-annotation
symbol: BK0437
symbol_name: 煤炭
asset_type: industry   # industry | concept | stock | index
period: daily
created_at: 2026-07-19T10:32:00+08:00
updated_at: 2026-07-19T10:35:00+08:00
status: open           # open | resolved | archived

intent: support        # 见标签体系
setup: 威科夫-弹簧
tags:
  - 形态/支撑阻力
  - 量价/放量突破
  - 板块/煤炭
  - 结果/待验证

time_range:
  start: 2026-03-01
  end: 2026-07-19
  focus_ts: 2026-07-18   # 标注关注的关键 bar

overlays_ref: case_20260719_BK0437_001.overlays.json
screenshot: attachments/case_20260719_BK0437_001.png

outcome:
  status: pending      # pending | win | lose | invalid | partial
  verified_at: null
  note: null
  metrics: null        # 事后可填：N 日后涨跌幅、最大回撤等

agent:
  distillable: true    # 是否进入 Agent 学习语料
  quality: 3           # 1-5 人工质量分
```

### 3.2 正文 Markdown 模板

```markdown
# BK0437 煤炭 · 日线 · 支撑

## 当时判断
- 为什么画这条线：
- 市场结构：
- 关键位/量价：

## 计划
- 触发条件：
- 失效条件：
- 预期路径：

## 备注
（自由书写）

## 复盘
- 实际走势：
- 对/错在哪：
- 规则更新：
```

### 3.3 机读 overlays 旁路文件

```json
{
  "case_id": "case_20260719_BK0437_001",
  "symbol": "BK0437",
  "period": "daily",
  "overlays": [
    {
      "id": "ov1",
      "type": "horizontalLine",
      "points": [{"timestamp": 1721260800000, "value": 1234.5}],
      "styles": {},
      "semantic": "support"
    }
  ],
  "viewport": {"from": "...", "to": "..."},
  "chart_snapshot_meta": {"bar_count": 120, "indicators": ["MA", "VOL"]}
}
```

---

## 4. 应实现功能（按优先级）

### P0 — 没有则闭环不成立

| # | 功能 | 说明 | 完成判定 |
|---|------|------|----------|
| F1 | **画线自动捕获 + 上下文打包** | 监听 KLineChart 画线完成；打包 symbol/period/viewport/overlays/indicators | 画线后 1s 内生成 case draft |
| F2 | **一键「保存为笔记」** | 热键/按钮；弹轻量表单：意图标签 + 可选备注 | 落盘 md + overlays json + 可选截图 |
| F3 | **Obsidian vault 导出器** | 配置 vault 根路径；按 `charts/YYYY/MM/` 或 `symbols/{code}/` 写入 | 文件可在 Obsidian 直接打开 |
| F4 | **截图附件** | 保存时截当前图（Pro 已有截图能力）写入 `attachments/` | md 内可预览图 |
| F5 | **历史标注回载** | 打开某标的时加载该 symbol 的 open cases overlays | 图上重现历史线，可开关 |
| F6 | **Case 列表与备注编辑** | 面板侧栏：当日/本标的案例；点开补备注、改标签 | 不强制打开 Obsidian 也能改语义 |

### P1 — Agent 真正能「学」

| # | 功能 | 说明 | 完成判定 |
|---|------|------|----------|
| F7 | **受控标签体系** | 意图/形态/量价/结果 四级预设；禁止完全自由标签泛滥 | 保存时至少选 1 个 intent |
| F8 | **结果回填（Outcome）** | N 日后或手动：win/lose/invalid + 简述；可半自动算 N 日收益 | case.outcome 非 pending 占比可统计 |
| F9 | **Agent 检索 MCP** | `search_cases` / `get_case` / `list_cases_by_symbol` / `similar_setups` | Agent 可按标签/标的/结果检索 |
| F10 | **机读索引** | vault 旁 `index/cases.sqlite` 或 JSONL：全量 frontmatter 同步 | 检索不依赖解析全部 md |
| F11 | **复盘会话模板** | 「对当前图做复盘」→ 拉历史相似 case + 当前 overlays → 生成复盘草稿写回 vault | 一次命令产出复盘 md |
| F12 | **规则蒸馏出口** | 从 win/lose 案例聚合：高频 setup + 失败模式 → `rules/playbook.md` | 人工确认后写入 playbook |

### P2 — 体验与质量

| # | 功能 | 说明 |
|---|------|------|
| F13 | **时间轴复盘视图** | 按日浏览「我画过什么、结果如何」 |
| F14 | **同类形态相似度** | 简单特征：线位相对位置、周期、setup 标签（先不做像素 CV） |
| F15 | **版本与审计** | case 修改历史；避免 Agent/人互相覆盖无痕 |
| F16 | **隐私分级** | 笔记可标 `private`，不进 Agent 语料 |
| F17 | **与 signal 统一** | 买卖信号 = case 的一种 intent，避免双系统 |
| F18 | **周/月复盘报告** | 胜率、常见错误标签、待验证 case 清单 |

### 后置（YAGNI 暂不做）

- 全自动「从画线猜意图」（准确率低，破坏信任）
- 向量库大而全（先标签 + BM25/FTS 足够）
- 云同步/多端协作
- 复杂像素级图像相似度
- 自动下单联动

---

## 5. 存储架构建议

**推荐：双写，单真相源清晰**

| 层 | 存什么 | 路径示例 |
|----|--------|----------|
| Obsidian vault | 人读 md + 截图 + 可选 overlays 附件 | `D:/Obsidian/TradingVault/charts/...` |
| 系统索引 | case 元数据、检索字段、outcome 统计 | `board-app/data/annotation_index.sqlite` |
| 几何 overlays | 按 case 或按 symbol | vault `attachments/*.overlays.json` 或沿用 `logs/drawings` 升级 |

**不推荐**：只把 JSON 塞进 Obsidian 当唯一库（Agent 检索与统计会痛）；也不要只 SQLite 不写 md（人复盘体验差）。

**配置项**：

```yaml
annotation:
  vault_path: "D:/Obsidian/TradingVault"
  vault_subdir: "charts"
  auto_screenshot: true
  require_intent: true
  default_period_in_path: true
  agent_index: "data/annotation_index.sqlite"
```

---

## 6. 标签体系（首版建议，保持短）

**意图 intent（必选其一）**

- `support` / `resistance` / `trendline` / `channel`
- `breakout` / `breakdown` / `retest`
- `entry` / `exit` / `stop` / `target`
- `structure`（结构标注，非交易点）
- `question`（存疑，待查）

**Setup（可选，可扩展）**

- 威科夫：弹簧/上冲回落/SOS/SOW…
- 量价：放量突破/缩量回踩…
- 自定义短列表（用户维护 `setups.yaml`）

**结果 outcome**

- `pending` / `win` / `lose` / `partial` / `invalid`

---

## 7. 与现有代码衔接

| 现有组件 | 改法 |
|----------|------|
| `overlay_service` | 扩展为 case 维度；或新增 `annotation_service` 调用它 |
| 前端 `index.html` / mcp-bridge | 画线事件 → draft；保存 UI；回载 overlays |
| MCP tools | 新增 `save_annotation` / `search_cases` / `update_outcome` / `load_case_overlays` |
| `signal_service` | 中期合并为 case.intent ∈ {entry,exit,...} |
| `ctx` | 保存时自动附上 panel context |

---

## 8. Agent 学习边界（重要）

Agent **可以**：

- 检索「类似 setup 且 win 的历史 case」辅助解读当前图
- 根据 playbook 规则提示「你过去在此 setup 的失效条件」
- 生成复盘草稿供你改

Agent **不可以默认**：

- 把所有未标注结果的草稿当真理
- 静默改写你的 playbook（须 `quality>=4` 且人工确认）
- 在无截图/无时间窗的情况下编造形态

**学习语料过滤**：`outcome != pending` 且 `agent.distillable` 且 `quality >= 3`。

---

## 9. 推荐落地顺序（最小可用）

**Phase A（1 个可用闭环）**

1. F1 捕获 + F2 保存表单  
2. F3 vault 写 md + F4 截图  
3. F5 回载  

**Phase B（可教 Agent）**

4. F7 标签 + F8 outcome  
5. F10 索引 + F9 MCP 检索  
6. F11 复盘模板  

**Phase C（沉淀）**

7. F12 playbook 蒸馏 + F18 周报  

---

## 10. 风险与反驳

| 反驳 | 回应 |
|------|------|
| 只存 JSON 不就够了？ | 人复盘需要 md 与图；Obsidian 链接/检索/手改成本最低 |
| 自动保存会不会噪声太多？ | 默认「画完进 draft，显式保存才入 vault」；草稿可 TTL 清理 |
| 标签太重会不想记？ | 意图 1 键 + 备注可选；结果用「3 天后提醒回填」 |
| 与现有 signal 重复？ | P0 并行；P1 统一数据模型 |
| K 线周期 bug 未修会影响标注？ | 是。标注依赖正确 timestamp；**O1 K 线修复应先于或并行 Phase A** |

---

## 11. 待用户确认的决策点

1. Vault 路径与目录约定（按 symbol 还是按日期）？  
2. 默认「显式保存」还是「自动保存 draft」？  
3. Phase A 是否包含截图（截图对 Agent 很有用，但实现稍重）？  
4. 是否现在就把 signal 并入 case，还是 P1 再统一？  

确认后可写 `IMPLEMENTATION_PLAN` 并开工。

---

## 12. 「语义笔记卡」如何实现？（2026-07-19 补充）

### 12.1 它是什么

**语义笔记卡 = 数据对象（Case）+ 一次极短的语义补全交互**，不是「在面板里重做 Obsidian」。

| 层 | 职责 |
|----|------|
| 几何层 | KLineChart 画线结果（points/type） |
| 语义层（笔记卡） | intent / setup / 备注 / outcome 等字段 |
| 持久层 | Obsidian md + 机读索引 + overlays JSON |

### 12.2 三种实现位置

| 方案 | 做法 | 优点 | 缺点 | 判定 |
|------|------|------|------|------|
| A. 只在 Obsidian | 面板只导出裸几何，人去 vault 补标签 | 面板零改 UI | 摩擦大，90% 不补标签，Agent 无语料 | 不推荐主路径 |
| B. 面板重型编辑器 | 侧栏富文本、双向链、图谱 | 功能全 | 重复造 Obsidian；`index.html` 已过重 | 不推荐 |
| C. 混合（推荐） | **面板轻量表单 3 秒落卡**；**长文复盘回 Obsidian**；后端 `annotation_service` 双写 | 捕获率高 + 深度写作不丢 | 需一点面板 UI + API | **采用** |

### 12.3 推荐：面板「轻量卡」+ 后端服务 + Obsidian「深度页」

**要在炒股面板里做的（轻量，必须）：**

1. 画线完成 → 草稿条（未语义化）  
2. 快捷键/按钮「存笔记」→ **迷你表单**（非富文本编辑器）：  
   - 意图 chips（必选 1）  
   - setup 下拉（可选）  
   - 一行备注（可选）  
   - 保存 / 稍后  
3. 右侧 Case 列表：本标的历史卡；点开可改标签、补 outcome  
4. 可选：`Ctrl+E` 用系统默认程序打开对应 vault 文件  

**不要在面板里做的：**

- 长文 Markdown 编辑、双链、图谱、插件体系 → 交给 Obsidian  
- 完整知识库浏览 UI  

**后端（面板与 Obsidian 之间）：**

```
POST /api/annotations          # 创建 Case：收前端打包的几何+语义
GET  /api/annotations?symbol=  # 列表
PATCH /api/annotations/<id>    # 改备注/outcome
POST /api/annotations/<id>/open-vault  # 可选：返回 path 或调起
```

服务内：`annotation_service` 写 vault md + 写 index + 调 `overlay_service`/旁路 JSON。

### 12.4 交互时序（显式保存）

```
用户画线
  → 前端 onDrawEnd：收集 overlays + ctx → draft（内存/本地）
  → 用户点「存笔记」或 Ctrl+S
  → 弹出语义迷你卡（intent 必选）
  → POST /api/annotations
  → 服务写：
       vault/.../case_xxx.md
       attachments/case_xxx.overlays.json
       attachments/case_xxx.png（可选）
       annotation_index 一行
  → 前端 Case 列表刷新；draft 清除
```

**长备注路径：** 迷你卡只写一句 → 之后在 Obsidian 改 md 正文；下次面板 `GET` 可只信 frontmatter/index，正文以 vault 为准（或面板只显示摘要字段）。

### 12.5 与「直接在面板实现」的一句话结论

- **是**：语义补全的 **入口与高频字段** 在面板实现（否则捕获失败）。  
- **否**：语义卡 **不等于** 在面板实现完整笔记应用；卡片的「家」在 Case 模型与 vault，面板只是 **采集前端**。

---

## 13. 场景细化：历史 K 线价格要素 → 水平位 → 后期反转（2026-07-19）

### 13.1 用户故事（原话抽象）

> 某根数月前的 K 线，其开盘价是关键支撑。选定该 K 线，用水平线画其开/收/高，延伸到数月后，引发一波行情。  
> 须记录：① 当前周期 ② 发挥支撑的那根 K 的价格要素 ③ 当前水平线 ④ 数月后引起行情的反转位置；可备注与提醒。

这不是「随便画条水平线」，而是一条 **因果链**：

```
源 K 线 (source bar) + 价格要素 (O/H/L/C)
        → 水平位 (level / horizontal overlay)
        → 反应事件 (reaction)：何时、何价、何方向触及/反转
        → 备注 + 提醒
```

### 13.2 专用 Case 类型：`level_origin`（价位溯源）

在通用 Case 上增加结构化字段（frontmatter + 机读 JSON 一致）：

```yaml
id: case_20260719_BK0437_lv01
type: level_origin          # 区别于 generic chart-annotation
symbol: BK0437
symbol_name: 煤炭
asset_type: industry
period: daily                 # ① 当前周期

# ② 源 K 线 + 价格要素
source_bar:
  timestamp: 2025-11-12T00:00:00+08:00   # bar 时间（与 K 线 timestamp 一致）
  date: "2025-11-12"
  ohlc:
    open: 1180.5
    high: 1205.0
    low: 1172.0
    close: 1198.0
  price_element: open         # open | high | low | close | custom
  price: 1180.5               # 实际用于画线的价（= ohlc[price_element] 或 custom）

# ③ 水平线（几何 + 语义角色）
level:
  role: support               # support | resistance | pivot
  price: 1180.5
  drawn_at: 2026-07-19T10:32:00+08:00
  overlay_id: ov_h_1180
  extend: right               # 向右延伸至反应点或至今

# ④ 反转 / 反应位置（可后补；可多条）
reactions:
  - id: rx1
    timestamp: 2026-03-18T00:00:00+08:00
    date: "2026-03-18"
    price: 1182.0
    kind: bounce              # bounce | reject | break | retest | accelerate
    direction: up             # up | down
    note: "回踩开盘价后放量拉升"
    # 可选：关联第二根「反应 K」完整 OHLC
    reaction_bar:
      timestamp: 2026-03-18T00:00:00+08:00
      ohlc: { open: 1175, high: 1220, low: 1170, close: 1210 }

notes: "几个月后仍有效，典型左侧结构价"
reminders:
  - id: rm1
    at: 2026-07-25T09:30:00+08:00
    kind: review              # review | watch_touch | expire
    message: "复查该支撑是否仍有效"
    status: pending           # pending | done | dismissed

outcome:
  status: win                 # 反应后走出预期 → win；跌破失效 → lose
  note: "3/18 反转后 10 日板块 +8%"

agent:
  distillable: true
  quality: 4
  pattern_summary: "日线·历史开盘价作支撑·数月后回踩反转向上"
```

### 13.3 机读 overlays 旁路（与上表对齐）

```json
{
  "case_id": "case_20260719_BK0437_lv01",
  "type": "level_origin",
  "period": "daily",
  "source_bar": {
    "timestamp": 1762876800000,
    "price_element": "open",
    "price": 1180.5
  },
  "overlays": [
    {
      "id": "ov_h_1180",
      "type": "horizontalLine",
      "role": "level",
      "points": [{"timestamp": 1762876800000, "value": 1180.5}],
      "styles": { "color": "#ef5350", "lineStyle": "solid" }
    },
    {
      "id": "ov_src_mark",
      "type": "marker",
      "role": "source_bar",
      "points": [{"timestamp": 1762876800000, "value": 1180.5}],
      "label": "源·开"
    },
    {
      "id": "ov_rx1",
      "type": "marker",
      "role": "reaction",
      "points": [{"timestamp": 1773792000000, "value": 1182.0}],
      "label": "反转·上"
    }
  ]
}
```

回载时：水平线 + 源 K 标记 + 反应点标记一并恢复。

### 13.4 面板交互（仍是轻量卡，但字段贴合本场景）

**两阶段采集（匹配真实画线习惯）：**

| 阶段 | 用户动作 | 系统记录 |
|------|----------|----------|
| A. 定源与线 | 选定历史 K → 选价格要素 O/H/L/C → 生成水平线 | `period` + `source_bar` + `level` + overlay |
| B. 标反应 | 数月后（或回看时）点击反应 K / 价位 → 「标为反转点」 | 追加 `reactions[]` |
| C. 备注提醒 | 迷你卡写备注；设提醒日期 | `notes` + `reminders[]` |

**阶段 A UI（面板内，约 5 秒）：**

1. 十字星停在目标 K → 快捷键或右键「设为源 K」  
2. 弹出四键：`开 / 高 / 低 / 收`（显示该 bar 具体数值）  
3. 确认后自动：`horizontalLine @ price`，并写入 draft Case  
4. 意图默认 `support`/`resistance`（可改）+ 一行备注可选 → 保存  

**阶段 B UI：**

1. 打开已有 Case 或当前水平线右键 →「添加反应点」  
2. 点选另一根 K（或输入日期+价）→ 选 kind：反弹/拒绝/跌破/回踩  
3. 可同时设提醒：「N 日后再看是否守住」

**不要求** 用户手填 OHLC 数字（从 Datafeed 当前 bar 读取）。

### 13.5 提醒如何实现（本场景刚需）

| 通道 | 做法 |
|------|------|
| 面板内 | 启动/盘前扫描 `reminders` 中 `at <= now && pending` → 顶部黄条 + 列表 |
| SSE | `annotation_reminder` 事件推前端 |
| 可选 | Windows 通知 / 写入 vault 日记「今日待复查」 |
| Agent | MCP `list_due_reminders`；复盘时优先拉 due cases |

提醒对象挂在 **Case** 上，不挂在裸 overlay 上。

### 13.6 Agent 可学到什么（本场景）

检索示例：

- 「日线里，历史 **开盘价** 作支撑、数月后回踩成功的案例」  
- `period=daily` + `price_element=open` + `level.role=support` + `reactions.kind=bounce` + `outcome=win`

可蒸馏规则句式：

> 当 period=日线 且 源 K 的 open 被标为 support，若在 ≥N 根 bar 后出现 bounce，历史 win 率 …

（规则写入 playbook 仍须人工确认。）

### 13.7 与通用 Case 的关系

| type | 何时用 |
|------|--------|
| `level_origin` | 本例：源 K + 价格要素 + 水平位 + 反应链 |
| `chart-annotation` | 趋势线、通道、Fib、纯结构标注等 |
| `signal`（中期并入） | 明确入场/出场点 |

实现时 **先做 `level_origin`**（用户主路径），通用卡复用同一保存管线。

### 13.8 完成标准（本场景可机械判定）

1. 选定 bar + 选 open → 水平线价 = 该 bar.open，误差 0（同精度）。  
2. Case 落盘含：`period`、`source_bar`、`level`、`overlays`。  
3. 添加反应点后 `reactions` 长度 +1，图上出现反应标记。  
4. 备注与提醒可读写；到期提醒在面板可见。  
5. 再开该标的+周期可回载源标记、水平线、反应点。  
6. `search_cases(price_element=open, role=support)` 能命中该 Case。

---

## 14. 多标的 / 跨周期关联（用户声明，系统不判定）（2026-07-19）

### 14.1 用户故事与原则修正

> 创新药与医疗服务可同时处在支撑——这是 **我声明的关联**，不是系统算出来的「共振成立」。  
> 中证红利与科创50：**红利见底往往对应科创见顶**——这是 **我凭经验写的关联结论**，系统只存储与检索，**绝不自动生成或验证该结论**。  
> 关联可跨 **指数 / 板块 / 个股**，可跨 **日 / 周 / 月 / 年** 等周期。

**铁律（产品边界）：**

| 系统做 | 系统不做 |
|--------|----------|
| 记录用户声明的关联边 / 关联组 | 判定「是否共振」「是否反向」 |
| 保存用户手写的 `relation_note`（经验句） | 用涨跌相关、分类树自动推断关系 |
| 跨类型、跨周期挂接 Case 或符号 | 因不同周期而拒绝关联 |
| 按用户检索/回放关联，供 Agent **复述经验** | 用回测自动给关联打 win/lose（除非用户 **主动** 事后标注） |

「共振」「相反」等词只出现在 **用户文案** 里，不是系统枚举出的判定结果。

### 14.2 两层对象（简化）

| 层 | 对象 | 职责 |
|----|------|------|
| L1 | Case（如 `level_origin`） | 单图：某 symbol + 某 period 上的价位/画线因果 |
| L2 | `Relation`（关联） | 用户声明的「这些腿有关」+ **用户写的结论原文** |

不再把 L2 做成「系统共振引擎」。名称可用 `Relation` / `LinkSet`；UI 文案用「关联」，避免「系统共振」。

```
Case A (创新药 · daily · support)
Case B (医疗服务 · daily · support)
        ──用户加入──▶  Relation
                         relation_note: "同属大医疗，双支撑时我更看多主题反转"
                         （此句仅用户输入，系统不改写）

Case C (中证红利 · weekly · 见底结构)
Case D (科创50 · weekly · 见顶结构)
        ──用户加入──▶  Relation
                         relation_note: "红利见底一般意味着科创见顶（经验）"
```

### 14.3 Relation 数据模型

```yaml
id: rel_20260719_med_01
type: relation                    # 用户声明的关联组
created_at: 2026-07-19T11:00:00+08:00
status: open

# 用户结论原文（唯一「结论」来源；可空，可后补）
relation_note: "创新药与医疗服务同时支撑，我视为大医疗反转共振"
# 可选短标签，仍是用户选的，不是系统判定
user_tags: [大医疗, 同向支撑]     # 自由或预设词表，不作真值

# 成员：任意 asset_type × 任意 period
members:
  - ref: case                     # case | symbol_only（尚未画线也可先挂符号）
    case_id: case_创新药
    symbol: BKxxxx
    symbol_name: 创新药
    asset_type: industry          # industry | concept | index | stock | hk...
    period: daily                 # daily | weekly | monthly | yearly | 1m...
  - ref: case
    case_id: case_医疗服务
    symbol: BKyyyy
    symbol_name: 医疗服务
    asset_type: industry
    period: daily

notes: ""                         # 补充备注
reminders: []                     # 用户提醒，与「系统判定到期」无关
agent:
  distillable: true               # 是否允许 Agent 引用此经验句
  quality: 4                      # 用户自评

# 明确禁止字段（实现时不要加）：
# auto_score / correlation / system_hypothesis / verified_by_backtest
```

**跨类型 / 跨周期示例（红利 vs 科创）：**

```yaml
id: rel_dividend_star_inverse
relation_note: "中证红利见底一般意味着科创50见顶（个人经验）"
user_tags: [风格轮动, 相反]
members:
  - ref: case
    case_id: case_红利_底
    symbol: sh000922              # 示例代码以实际为准
    asset_type: index
    period: weekly
  - ref: case
    case_id: case_科创_顶
    symbol: sh000688
    asset_type: index
    period: weekly
# 也可一腿 daily、一腿 monthly —— 允许 period 不一致，仅如实记录
```

- Obsidian：`charts/relations/YYYY/MM/rel_*.md`  
- 各 Case：`relation_ids: [rel_...]`  
- 索引字段：`member_symbols`、`member_periods`、`relation_note` 全文，便于搜「红利」「科创」

### 14.4 面板采集

1. 任意图保存 Case 后 →「加入关联」/「新建关联」  
2. 切换 **任意** 指数/板块/个股、**任意** 周期，再保存或挑选已有 Case →「并入该关联」  
3. 关联编辑页：**只展示成员列表 + 大文本 `relation_note` + 备注/提醒**  
4. **无**「是否共振」「相关系数」「系统结论」按钮或徽章  
5. 点成员 → 切换到该 symbol + **该成员自己的 period** 并回载 Case  

草稿篮跨标的、跨周期切换保留。

### 14.5 规则（对齐用户约束）

| 项 | 规则 |
|----|------|
| 谁建立关联 | **仅用户**；无自动建组、无分类推断 |
| 结论 | **仅 `relation_note`（及用户 tags）**；系统不生成、不改写、不验证 |
| 标的范围 | index / industry / concept / stock / … 全开 |
| 周期范围 | 成员各自带 `period`；允许混用，**不**要求同周期，**不**因跨周期降权或拦截 |
| 时间窗 | 可选、用户填；不用于系统判定「是否同窗共振」 |
| Agent | 检索并 **引用用户原文**；可说「根据你的笔记…」；不可说「系统检测到反向关系」 |

### 14.6 提醒

提醒 = 用户备忘（「下周再看这两条」），不是「系统判定关联失效」。  
单 Case 与 Relation 都可挂 `reminders[]`。

### 14.7 与 outcome 的关系

- **默认**：Relation **无** 系统 outcome。  
- **可选（用户主动）**：用户后来手写「这回经验应验/不适用」→ `user_review: { note, at }`，仍非自动回测。  
- 单 Case 的 level 守住/跌破若用户要记，仍在 L1 自己标，与关联结论解耦。

### 14.8 Agent

- `search_relations(q=红利)` / `get_relation` / `link_case_to_relation`  
- 回答时优先引用 `relation_note`  
- 蒸馏进 playbook：**原文进经验库**，标题可注「用户经验，非统计结论」

### 14.9 落地优先级

| 阶段 | 内容 |
|------|------|
| 最小 | Case 互链 + `relation_note` 文本 + 跨 symbol/period 成员 |
| Phase B | Relation 实体页、草稿篮、vault、跳转（带 period） |
| 明确砍掉 | 自动共振检测、相关计算、系统 hypothesis、强制同周期 |

### 14.10 完成标准

1. 用户可将任意两个（及以上）Case 或符号加入同一 Relation，**不限** asset_type / period。  
2. `relation_note` 原样落盘；API/UI **无** 自动结论字段。  
3. 打开 Relation 可跳转各成员正确周期与标注。  
4. 全文检索能命中经验句（如「红利」「科创」）。  
5. 代码路径中不存在「根据行情自动创建/评分关联」逻辑。
