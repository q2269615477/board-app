# 技术要点：Obsidian 落盘 · 面板连接 · Agent 接入与「学习」

**日期：** 2026-07-19  
**目的：** 在动手实现前，把四条链路理通，避免「接插件 / 训模型 / 系统判定」等错误预期。  
**完成标准：** 读者能回答：谁写文件、Obsidian 为何能看见、面板如何「连接」、Agent 调什么、学什么、不学什么。

---

## 0. 一句话总览

| 问题 | 答案 |
|------|------|
| 如何保存到 Obsidian？ | **Flask 直接写 vault 目录下的 `.md` / 附件**；Obsidian 只是打开同一文件夹的编辑器 |
| 面板如何「连接」Obsidian？ | **不是 WebSocket 协议握手**；是「配置路径 + 写文件 + 可选 `obsidian://` 打开」 |
| Agent 如何接入？ | 沿用现有 **MCP 桥**（`mcp_board_bridge.js` → `http://127.0.0.1:5000/mcp/*`），**增知识类 tools** |
| Agent 如何学习？ | **检索增强（RAG 式）**：查索引 → 注入对话 → **复述你的原文**；**不是**微调权重，**不是**自动发现关联 |

---

## 1. 如何保存到 Obsidian

### 1.1 正确心智模型

```
Obsidian Vault = 普通本地文件夹
Obsidian App   = 监视该文件夹的 Markdown IDE

board-app 保存 = 往该文件夹原子写入文件
             ≠ 调用 Obsidian 私有 API（默认不做）
             ≠ 必须安装社区插件才能写库
```

因此「保存到 Obsidian」在工程上等价于：

```text
write(ANNOTATION_VAULT_PATH / relative_path, content)
```

### 1.2 推荐目录约定

```text
{ANNOTATION_VAULT_PATH}/          # 例：D:/Obsidian/TradingVault
  charts/
    cases/
      {symbol}/
        {case_id}.md              # 人读主文档
    relations/
      {yyyy}/{mm}/
        {rel_id}.md
    attachments/
      {case_id}.overlays.json     # 机读几何（可被 md 引用）
      {case_id}.png               # 可选截图
    playbook/
      user_playbook.md            # 可选：人工确认后的经验汇编
  .obsidian/                      # 由用户/Obsidian 自己维护，board-app 不改
```

配置：

```text
环境变量 ANNOTATION_VAULT_PATH=D:\Obsidian\TradingVault
# 或 core/config.py 默认 BASE_DIR/vault（开发用）
```

**用户侧一步：** 在 Obsidian「打开文件夹作为库」→ 选中上述路径。此后 Flask 每写一个 md，库内自动出现（文件系统事件）。

### 1.3 单次保存的双写事务（逻辑）

保存 Case / Relation 时，`annotation_service` 顺序：

```text
1. 校验 payload（level 价 = 源 K 要素等）
2. 生成 id、组装完整 JSON 对象
3. 写 SQLite annotation_index（API/Agent 真相源）
4. vault_writer 原子写：
     - case md（YAML frontmatter + 正文模板）
     - overlays.json
     - 可选 png
5. 任一步失败：回滚 sqlite 或标记 dirty + 返回 error
```

**为何双写？**

| 存储 | 服务对象 |
|------|----------|
| SQLite 索引 | 列表、筛选、due 提醒、MCP 稳定检索 |
| Vault md | 人在 Obsidian 阅读、改长文、双链、图谱 |

**冲突策略（建议默认）：**

- **结构化字段**（symbol、period、source_bar、level、reactions、relation_note）：以 **API/SQLite** 为准；面板改走 PATCH API，再重写 frontmatter。  
- **正文长文**（「当时判断」大段）：以 **vault md body** 为准；API 不覆盖 body 已有用户段落（只更新 frontmatter 块），或提供「从 vault 刷新 body」。  
- 提供 `POST /api/annotations/reindex`：扫 vault frontmatter 重建索引（灾备）。

### 1.4 md 文件形态（Case 示例）

```markdown
---
id: case_20260719_BK0437_lv01
type: level_origin
symbol: BK0437
period: daily
price_element: open
level_price: 1180.5
relation_ids: []
---

# BK0437 · 日线 · 支撑（开）

## 源 K
- 日期：2025-11-12
- 要素：open = 1180.5

## 水平位
- role: support
- 附件：![[attachments/case_....png]]

## 反应点
- （后补）

## 备注
（面板一行 + 你在此继续写）

## 关联
- （wikilink 到 rel_*.md）
```

Relation 页同理：`relation_note` 必须在 frontmatter **且** 正文醒目复述一遍，便于人眼与全文搜索。

### 1.5 不做的事（保存路径）

- 不依赖 Local REST API 插件作为主路径（可选增强，非 MVP）。  
- 不写 `.obsidian/workspace` 等应用状态。  
- 不把 `kline.db` 放进 vault。

---

## 2. 面板如何「连接」Obsidian

### 2.1 三种连接，层次不同

| 层级 | 机制 | 谁做 | 用户感知 |
|------|------|------|----------|
| **A. 存储连接** | 环境变量/配置指向 vault 根；服务端 `os.path` 可写检查 | Flask 启动 + `/api/annotations/config` | 状态灯：绿=可写 |
| **B. 写入连接** | 面板 `POST /api/annotations` → 服务端写文件 | 保存按钮 | 保存成功 toast + 路径 |
| **C. 打开连接** | 打开已有笔记 | 按钮「在库中打开」 | 跳到 Obsidian 或资源管理器 |

**没有 D：面板与 Obsidian 进程的双向 RPC（MVP 不需要）。**

### 2.2 状态与配置 API

```http
GET /api/annotations/config
→ {
  "ok": true,
  "data": {
    "vault_path": "D:\\Obsidian\\TradingVault",
    "vault_writable": true,
    "index_path": ".../annotation_index.sqlite",
    "obsidian_uri_supported": true
  }
}
```

前端：顶栏/设置区显示「知识库：已连接 / 未配置 / 不可写」。

### 2.3 「在库中打开」实现选项

| 方式 | 实现 | 优点 | 注意 |
|------|------|------|------|
| **推荐** `obsidian://` | `obsidian://open?vault=TradingVault&file=charts/cases/BK0437/case_xxx` | 直接定位笔记 | vault 名须与 Obsidian 库名一致；需 encodeURIComponent |
| 资源管理器 | `os.startfile(path)` 或返回 path 让前端提示 | 不依赖 URI | 只打开文件/夹，未必聚焦 Obsidian |
| Local REST API 插件 | HTTP 调 Obsidian 插件 | 可远程命令 | 额外部署；MVP 不做 |

Windows 下也可：`subprocess` 调 `obsidian://...`（`os.startfile` 对自定义协议通常可用）。

### 2.4 面板职责边界（再强调）

面板 **只做**：

- 采集 level_origin / 反应点 / 短备注 / 提醒 / 关联篮  
- 调 Flask API  
- 显示连接状态与打开笔记  

面板 **不做**：

- 嵌入 Obsidian WebView  
- 同步 Obsidian 插件状态  
- 在浏览器里直接写磁盘（浏览器无权限；必须经 Flask）

### 2.5 数据流（保存）

```text
用户点「存笔记」
  → annotation-ui.js 打包 Case JSON
  → POST /api/annotations
  → annotation_service
       ├─→ annotation_index.sqlite
       └─→ vault_writer → D:/Obsidian/.../case_xxx.md
  → 响应 { id, vault_relpath, obsidian_uri? }
  → 前端 toast；可选自动 open
  → （用户已打开该库时）Obsidian 文件列表出现新笔记
```

---

## 3. Agent 如何接入

### 3.1 现有管道（已存在，扩展即可）

```text
WorkBuddy / CatPaw Agent
    │  MCP stdio (JSON-RPC tools/list, tools/call)
    ▼
mcp_board_bridge.js   （Node，已有）
    │  HTTP
    ▼
http://127.0.0.1:5000/mcp/tools
http://127.0.0.1:5000/mcp/call
    │
    ▼
mcp/handlers.py  →  annotation_service / relation_service
                 →  （读）annotation_index.sqlite
                 →  （可选读）vault md body
```

**前提：** board-app Flask 在 5000 运行；MCP 配置指向 `mcp_board_bridge.js`（与现面板控图相同）。

### 3.2 知识类 MCP 工具（拟增）

| 工具名 | 读写 | 作用 |
|--------|------|------|
| `search_cases` | 读 | symbol / period / price_element / 关键词 |
| `get_case` | 读 | 完整 Case + 可选 md 正文 |
| `search_relations` | 读 | 全文/标签；**返回 relation_note 原文** |
| `get_relation` | 读 | 成员列表 + note |
| `list_due_reminders` | 读 | 到期备忘 |
| `load_case_overlays` | 写图 | SSE 控图回载（不改知识结论） |
| `save_annotation` | 写 | Agent 代记（可选；须用户意图明确） |
| `save_relation` | 写 | 同上 |

**禁止注册：** `detect_resonance`、`infer_inverse`、`score_correlation`。

### 3.3 Agent 运行时系统提示（产品约束，应写进技能/说明）

```text
- 交易关联与形态结论以用户笔记为准。
- 引用 search_relations / get_case 返回的原文，使用「根据你的笔记…」。
- 不得声称「系统检测到共振/反向」。
- 无检索命中时明确说「知识库中无相关记录」，勿编造。
```

### 3.4 与「读 vault 文件」的关系

| 方式 | 何时用 |
|------|--------|
| **主路径：MCP → SQLite** | 列表、过滤、稳定字段 |
| **辅路径：get_case 附带 md body** | 用户写了长复盘，需全文 |
| **不推荐：Agent 直接扫盘整个 vault** | 慢、易超上下文、与索引不一致 |

WorkBuddy 若另有「读本地文件」能力，可作灾备；**契约仍以 MCP 为准**。

---

## 4. Agent 如何「学习」你保存的知识

### 4.1 「学习」在本系统中的定义

```text
学习 = 在需要时检索你的结构化笔记与 relation_note，
      并在推理中优先遵循这些经验。

≠ 训练/微调大模型权重
≠ 无监督地从行情发现规律
≠ 自动把相关板块收成共振组
```

这是 **检索增强 + 提示约束**（轻量 RAG），外加可选的 **人工 playbook 沉淀**。

### 4.2 三层「越学越像你」

| 层 | 机制 | 触发 | 持久性 |
|----|------|------|--------|
| **L1 即时检索** | 每次相关提问 `search_*` → 塞进当前上下文 | 对话中 | 会话级 |
| **L2 会话策略** | 系统提示：先搜知识库再答盘面 | 配置/技能 | 配置级 |
| **L3 经验汇编** | 你确认后把多条 note 写入 `playbook/user_playbook.md` | 手动或半自动建议 | 长期文件；Agent 可 `get_playbook` |

**默认只做 L1+L2；L3 须你点头。**

### 4.3 典型对话循环

```text
你：现在创新药和医疗服务都在关键支撑，结合我的笔记怎么看？

Agent：
  1. get_panel_context → 当前标的
  2. search_cases(symbol=创新药|医疗服务, type=level_origin)
  3. search_relations(q=医疗|创新药|支撑)
  4. 若有 relation_note，原样引用
  5. 回答：「根据你的笔记『…』；图上你标了…；我不会替你判定共振是否成立。」
  6. 可选 load_case_overlays 帮你把历史线调上图
```

```text
你：红利和科创现在一个像底一个像顶，我以前怎么说的？

Agent：
  search_relations(q=红利|科创)
  → 命中 relation_note:「中证红利见底一般意味着科创50见顶」
  → 复述该句 + 列出成员 case 的 period/symbol
  → 不计算相关系数
```

### 4.4 语料过滤（避免噪音）

写入「可引用」集合的条件（可配置）：

| 字段 | 建议 |
|------|------|
| `agent.distillable` | true |
| `agent.quality` | ≥ 3（你自评） |
| 草稿 | 未显式保存的不进索引检索默认视图 |
| Relation | 无 note 也可检索成员，但 Agent 应标明「无经验句，仅结构关联」 |

**不要求** outcome=win 才可引用关联经验——因关联结论是你的先验，不是回测标签。

### 4.5 与微调/向量库的边界

| 方案 | 本期 |
|------|------|
| SQLite + LIKE/FTS5 | **做** |
| 向量 embedding | 后置（笔记量大且关键词不够时） |
| LoRA/微调 | **不做** |
| 自动蒸馏改 playbook | 仅生成「建议 diff」，**你确认后**写入 |

### 4.6 Agent 可写回知识库吗？

| 写回 | 策略 |
|------|------|
| 代你 `save_annotation` | 允许，但需对话中明确授权 |
| 改 `relation_note` | **默认禁止**；或仅追加「Agent 备注」字段，不动原文 |
| 写 playbook | 仅 `propose_playbook_update` → 你确认 |

---

## 5. 端到端时序（对照实现）

### 5.1 人：源 K 支撑落库

```text
面板选 bar → 选 open → POST /api/annotations
  → sqlite + vault md
  → Obsidian 侧栏出现笔记
  → （可选）obsidian:// 打开补长文
```

### 5.2 人：跨板块关联

```text
Case A 保存 → 入关联篮
切换标的 Case B 保存 → 并入
填写 relation_note → POST /api/relations
  → rel_*.md + 两 case frontmatter 互链
```

### 5.3 Agent：用知识

```text
tools/call search_relations
  → handlers 查 sqlite
  → 返回 note 原文 + members
  → 模型生成对用户可见的复述
```

---

## 6. 实现模块清单（仅知识链路）

| 模块 | 职责 |
|------|------|
| `core/config.py` | `ANNOTATION_VAULT_PATH`、`ANNOTATION_INDEX_DB` |
| `services/vault_writer.py` | 原子写 md/json/png；生成 obsidian URI |
| `data/annotation_repo.py` | SQLite CRUD + FTS |
| `services/annotation_service.py` | Case 业务 + 双写编排 |
| `services/relation_service.py` | Relation 业务（无判定逻辑） |
| `api/annotation_routes.py` | HTTP |
| `mcp/tools.py` + `handlers.py` | Agent 入口 |
| `static/js/annotation-ui.js` | 采集 + 状态灯 + 打开库 |
| `mcp_board_bridge.js` | **一般无需改**（已转 HTTP） |

---

## 7. 验收口径（理通即这些能说清）

1. **保存：** 配置 vault 后，一次 API 调用在磁盘产生 md，Obsidian 打开该库可见。  
2. **连接：** 面板显示 vault_writable；一点可 `obsidian://` 或打开文件。  
3. **接入：** WorkBuddy 经现有 bridge 调到 `search_relations`，返回你的 note 原文。  
4. **学习：** 对话中 Agent 引用该原文；知识库为空时承认没有，不编造；无自动共振。

---

## 8. 常见误解对照

| 误解 | 正解 |
|------|------|
| 要先装 Obsidian 插件才能保存 | 否；写文件夹即可 |
| 面板与 Obsidian 要保持 WebSocket 在线 | 否；写盘即连 |
| Agent 学了就会记住所有笔记 | 否；每次靠检索；上下文有限 |
| 学 = 模型变聪明 | 否；学 = 有据可查地用你的笔记 |
| 关联会自动越积越准 | 否；关联质量取决于你写的 note 与引用习惯 |

---

## 9. 与总方案的衔接

- 总方案：`docs/plans/2026-07-19-system-optimization-master-plan.md` Phase 3–4  
- 场景模型：`docs/plans/2026-07-19-chart-annotation-obsidian-kb-design.md` §12–14  
- **实现顺序建议不变：** K 线可信（Phase1）→ 再双写 vault（Phase3），避免脏坐标污染库  

**置信度：** 架构与边界 **高**；`obsidian://` 在各版本参数细节 **中**（实现时以官方 URI 文档校准 vault 名与 file 路径编码）。
