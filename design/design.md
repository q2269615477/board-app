# 炒股 AI 白板系统 · 设计方案

**版本**: v1.0
**日期**: 2026-07-04
**基于**: 炒股 AI 白板系统 · 完整技术档案 (v2.0)
**开发路径**: 从零重构

---

## 1. 设计决策

### 1.1 为什么从零重构

现有 board-app 是一个**数据驱动的看盘面板**，有完整的后端基础设施（Flask 4层架构、QMT/SQLite双数据源、990板块数据）。但它缺少白板系统的任何实现。

新项目需要从看盘面板升级为**分析系统**，两者交互范式不同：
- 看盘面板：纯数据展示，用户被动消费
- 白板系统：用户主动标注，AI 学习用户的分析判断

从零重构的原因是：白板不是"加个页面"而是"换个产品形态"。

### 1.2 架构兼容

保留现有后端基础设施，在其上增加白板层：
- Flask + Blueprint 架构 ✅ 保留
- SQLite 数据库 ✅ 保留（新增 whiteboard 表）
- QMT/Tushare 数据源 ✅ 保留
- KLineChart 前端库 ✅ 保留
- CSP 内联方案 ✅ 保留
- MCP 桥接器 ✅ 保留并增强

新增：白板画布层（HTML5 Canvas + DOM 混合方案，不用 React/Plait）

### 1.3 技术选型调整（vs 技术档案）

| 维度 | 技术档案 | 本设计 | 理由 |
|------|----------|--------|------|
| 白板引擎 | Plait/Drawnix | Canvas + DOM | WorkBuddy CSP 限制 npm 包加载；用户模式：轻量够用 |
| 图表库 | TradingView Lightweight Charts | KLineChart Pro v9.6.0 | 已有现成的，与 Pickup模式深度集成 |
| 持久化 | 文件 JSON | SQLite + JSON 导出 | 多白板管理、搜索、版本更方便 |
| 传输 | MCP 轻量指令 | 混合模式 | 按需实时 vs 批处理 |
| AI触发 | 自动触发 | 复制粘贴兜底 | WorkBuddy 实时触发需无限在线 |

### 1.4 MVP 范围（Phase 1）

**入选范围**（8 个模块）：
1. 双人交互：K 线图表 + 白板画布
2. Pickup 模式：点击 K 线拾取 → 白板生成元素
3. 白板元素：K 线卡片、水平线标注、文本框、过渡区
4. 标注体系：重要性星级 + 多空方向
5. 关联系统：自动价格对应推断 + 手动关联线
6. 净偏计算：框内净偏 → 标的结论 → 统一结论
7. 持久化：自动保存 + 白板目录
8. WorkBuddy 触发：复制粘贴兜底 + MCP 实时通知（异步）

**不纳入 MVP**（后续 Phase）：
- Trend line（趋势线）：初版用水平线兜底
- Cross-period / cross-symbol association：初版仅框内关联
- Memory learning system：标记上传，不做高级学习
- Full market scanning：用现有 board_scanner.py
- TimeLine correction：暂不实现
- Visual adaptation layer：直接文本模式（DeepSeek 不支持图片）

---

## 2. 整体布局

### 2.1 面板布局

```
┌─ 面板 ────────────────────────────────────────────────────────────┐
│ 顶部栏: [标的搜索] [周期: 日/周/月] [📂 白板目录] [📨 发送]        │
├───────────────────────────────────────┬───────────────────────────┤
│                                       │ 状态栏: [拾取模式 🟢]      │
│  左侧: K 线图表区                      │ [💾 已保存] [3 卡片]       │
│  ┌─────────────────────────────────┐  ├───────────────────────────┤
│  │  K 线 + 成交量                    │  │ 白板工作区                 │
│  │  画线工具（水平线/过渡区）           │  │ ┌───────────────────────┐ │
│  │  Pickup 光标: 十字准星              │  │ │ 标的框: 创新药 (日线)  │ │
│  │  已拾取 K 线: ⬆️ 标记             │  │ │ 元素列表 + 关联线       │ │
│  │                                   │  │ │ [净偏: +0.4 偏多]     │ │
│  │                                   │  │ └───────────────────────┘ │
│  │                                   │  │ 工具栏: [水平线] [趋势线]  │
│  │                                   │  │ [文本] [框选] [删除]      │
│  └─────────────────────────────────┘  │ 结论框: [看多 3.2 vs 空 1.4] │
├───────────────────────────────────────┴───────────────────────────┤
│ 底部状态栏: 当前标的 | 白板状态: active | 自动保存: 2s 防抖          │
└───────────────────────────────────────────────────────────────────┘
```

### 2.2 白板四层嵌套（Phase 1）

Phase 1 仅实现单层（单一标的单周期），四层嵌套在 Phase 2 实现：

```
白板
└── 周期框（日线）
    ├── K 线卡片
    ├── 水平线标注
    ├── 文本框
    ├── 过渡区
    ├── 框内关联线
    └── 周期结论节点
```

---

## 3. 白板元素设计

### 3.1 卡片视觉规则

```
┌─────────────────────────────────┐  ← 重要性边框（星数）
│ ⭐⭐⭐⭐☆  日线  2025-05-12     │  ← 头部：星级 + 周期 + 日期
│                                 │
│  O: 3210.4  H: 3268.7          │  ← OHLCV 数据
│  L: 3195.2  C: 3255.1          │
│  Vol: 185万  量比: 1.6          │
│                                 │
│  [🔴 看多] [编辑] [删除]         │  ← 方向 + 操作
│  备注: ___________________      │
└─────────────────────────────────┘
```

### 3.2 水平线卡片

```
┌─────────────────────────────────┐  ← 5星边框（金色粗线）
│ ──────────────────── 3245.6    │  ← 价格线样式
│ ⭐⭐⭐⭐⭐  支撑位                │
│  触及: 3次  缩量有效             │
│                                 │
│  [🔴 看多] [编辑] [删除]         │
│  备注: 3月15日收盘价，三次触及     │
└─────────────────────────────────┘
```

### 3.3 过渡区卡片

```
┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  ← 灰色虚线
│  ☆ 过渡区  3250-3290            │
│  2025-03-16 ~ 2025-06-19       │
│  无关键性｜已排除                │
│  [⚪ 中性] [编辑] [删除]         │
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
```

### 3.4 文本框

```
┌─────────────────────────────────┐
│  _____________________________  │
│  _____________________________  │
│  _____________________________  │
│  [🔴 看多] [删除]               │
└─────────────────────────────────┘
```

---

## 4. Pickup 模式状态机

```
[空闲] ──🖱️ 点击"Pickup"──→ [Pickup 模式]
                                    │
         ┌──────────────────────────┤
         │                          │
    🖱️ 点击 K 线              🖱️ 点击水平线按钮
         │                          │
    生成 K 线卡片            进入画线模式
    闪烁 2 次确认                  │
    "+1" 动画                🖱️ 点击图表位置
         │                          │
         └──────────┬─────────── 生成水平线
                    │
               🖱️ 再次点击"Pickup"
                    │
               [整理模式]
                    │
               🖱️ 点击"发送"
                    │
               [已发送] → 返回空闲
```

---

## 5. 核心算法

### 5.1 自动关联推断

```python
def infer_relations(new_element, existing_elements):
    """推断新元素与已有元素的关系"""
    relations = []
    for elem in existing_elements:
        # K线 → 水平线: 价格来源
        if new['type'] == 'kline' and elem['type'] == 'horizontal_line':
            price_diff = abs(new['close'] - elem['price']) / elem['price']
            if price_diff < 0.02:
                relations.append({
                    'type': 'derived_from',
                    'from': new['id'],
                    'to': elem['id']
                })
        
        # 水平线 → K线: 触及验证
        if new['type'] == 'horizontal_line' and elem['type'] == 'kline':
            low_diff = abs(elem['low'] - new['price']) / new['price']
            if low_diff < 0.01:
                relations.append({
                    'type': 'touch_validate',
                    'from': new['id'],
                    'to': elem['id']
                })
    return relations
```

### 5.2 净偏计算

```python
def calc_net_bias(elements):
    """计算框内净偏"""
    bullish_power = sum(e['weight'] for e in elements if e['direction'] == 'bullish')
    bearish_power = sum(e['weight'] for e in elements if e['direction'] == 'bearish')
    net_bias = bullish_power - bearish_power
    total_power = bullish_power + bearish_power
    
    if total_power == 0:
        return {'bias': 0, 'direction': 'neutral', 'strength': 'none'}
    
    net_strength = abs(net_bias) / total_power
    
    if net_bias > 1.5: direction, strength = 'bullish', 'strong'
    elif net_bias > 0.5: direction, strength = 'bullish', 'medium'
    elif net_bias > 0.2: direction, strength = 'bullish', 'weak'
    elif net_bias > -0.2: direction, strength = 'neutral', 'balanced'
    elif net_bias > -0.5: direction, strength = 'bearish', 'weak'
    elif net_bias > -1.5: direction, strength = 'bearish', 'medium'
    else: direction, strength = 'bearish', 'strong'
    
    return {
        'bias': round(net_bias, 2),
        'direction': direction,
        'strength': strength,
        'bullish_power': round(bullish_power, 2),
        'bearish_power': round(bearish_power, 2),
        'net_strength': round(net_strength, 2)
    }
```

---

## 6. 数据结构

### 6.1 白板 JSON 结构

```json
{
  "board_id": "wb_20260704_001",
  "symbol": "创新药板块",
  "period": "日线",
  "status": "active",
  "created_at": "2026-07-04T11:14:00",
  "updated_at": "2026-07-04T11:20:00",
  "chart_state": {
    "period": "日线",
    "visible_range": ["2024-07-01", "2025-07-03"],
    "scroll_position": 0.75
  },
  "elements": [
    {
      "element_id": "wb_d_001",
      "type": "kline_card",
      "position": {"x": 120, "y": 80},
      "importance": {"level": 4, "weight": 0.8, "user_set": true},
      "direction": {"direction": "bullish", "user_set": true},
      "data": {
        "date": "2025-05-12",
        "values": {"open": 3210.4, "close": 3255.1, "high": 3268.7, "low": 3195.2}
      },
      "note": "长下影探底",
      "is_pinned": false,
      "created_at": "2026-07-04T11:15:00"
    }
  ],
  "relations": [
    {
      "relation_id": "rel_001",
      "type": "derived_from",
      "from": "wb_d_001",
      "to": "wb_d_002",
      "auto_generated": true
    }
  ],
  "conclusion": {
    "net_bias": 0.4,
    "net_direction": "bullish",
    "net_strength": "weak",
    "bullish_power": 1.8,
    "bearish_power": 1.4,
    "summary": "支撑强于阻力，偏多但上方有压力"
  }
}
```

### 6.2 SQLite 表

```sql
CREATE TABLE IF NOT EXISTS whiteboards (
    board_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    period TEXT DEFAULT '日线',
    status TEXT DEFAULT 'active',
    title TEXT,
    data_json TEXT NOT NULL,  -- 完整白板 JSON
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS board_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (board_id) REFERENCES whiteboards(board_id)
);

CREATE INDEX idx_whiteboards_symbol ON whiteboards(symbol);
CREATE INDEX idx_whiteboards_updated ON whiteboards(updated_at);
```

---

## 7. 后端 API 设计

### 7.1 白板 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/wb/list?symbol=xxx` | 列出白板 |
| GET | `/api/wb/<board_id>` | 获取完整白板 |
| POST | `/api/wb` | 创建白板 |
| PUT | `/api/wb/<board_id>` | 更新白板 |
| DELETE | `/api/wb/<board_id>` | 删除白板 |
| POST | `/api/wb/<board_id>/snapshot` | 创建快照 |
| GET | `/api/wb/<board_id>/snapshots` | 获取快照列表 |

### 7.2 Pickup 相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/wb/<board_id>/pickup/kline` | 拾取 K 线 |
| POST | `/api/wb/<board_id>/pickup/horizontal_line` | 画水平线 |
| POST | `/api/wb/<board_id>/pickup/text` | 添加文本框 |
| POST | `/api/wb/<board_id>/pickup/transition` | 标记过渡区 |

### 7.3 元素操作

| 方法 | 路径 | 说明 |
|------|------|------|
| PUT | `/api/wb/<board_id>/element/<elem_id>` | 更新元素 |
| DELETE | `/api/wb/<board_id>/element/<elem_id>` | 删除元素 |
| POST | `/api/wb/<board_id>/relation` | 创建关联 |
| DELETE | `/api/wb/<board_id>/relation/<rel_id>` | 删除关联 |

### 7.4 分析触发

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/wb/<board_id>/send` | 发送到 WorkBuddy |
| GET | `/api/wb/<board_id>/prompt` | 获取分析 Prompt（供复制粘贴） |

### 7.5 SSE 实时推送

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/wb/<board_id>/events` | SSE 事件流 |

---

## 8. 前端架构

### 8.1 核心模块

```
whiteboard/
├── app.js           # 入口、初始化、状态管理
├── chart-manager.js # KLineChart Pro 封装 + Pickup 交互
├── whiteboard.js    # 白板画布引擎（Canvas + DOM）
├── element.js       # 元素卡片渲染
├── relation.js      # 关联线绘制
├── pickup.js        # Pickup 模式状态机
├── conclusion.js    # 净偏计算 + 结论显示
├── persistence.js   # 自动保存 + 加载
├── workbuddy-bridge.js # WorkBuddy 触发
└── ui.js            # 工具栏、弹窗、目录
```

### 8.2 白板引擎技术方案

**选择**：Canvas + DOM 混合方案

**理由**：
- WorkBuddy CSP 限制 React/npm 包打包
- Canvas 绘制关联线（覆盖层）
- DOM 渲染卡片（可点击、可编辑、可直接操作 input）
- 两者叠加在一个容器内，用绝对定位对齐

**渲染流程**：
1. Canvas (z-index: 1): 绘制关联线
2. DOM 层 (z-index: 2): 渲染卡片元素
3. Canvas (z-index: 3): Pickup 十字准星 + 闪烁动画

---

## 9. WorkBuddy 触发设计

### 9.1 复制粘贴兜底（MVP）

用户点击"发送"按钮：
1. 前端把白板数据 GET `/api/wb/<board_id>/prompt`
2. 返回完整文字 prompt（结构化格式）
3. 前端弹 modal，用户点击复制
4. 用户切到 WorkBuddy 面板粘贴提交

### 9.2 MCP 实时通知（异步增强）

同时发送 MCP 通知给 WorkBuddy：
- `trigger_workbuddy_analysis` 工具
- 传递 `{ whiteboard_file: "xxx.json", skill: "用户当前技能" }`
- WorkBuddy 在后台分析，通过 MCP 回调通知面板
- 面板收到 POST `/api/wb/<board_id>/analysis_result` → SSE 推送

---

## 10. 开发计划

### Phase 1: 白板核心（MVP）

| 任务 | 依赖 | 预估 |
|------|------|------|
| 1. 项目骨架搭建 | - | 30min |
| 2. SQLite 表 + DAO | 1 | 20min |
| 3. 后端 API: 白板 CRUD | 2 | 40min |
| 4. 后端 API: Pickup 操作 | 2 | 30min |
| 5. 后端 API: 元素操作 | 2 | 30min |
| 6. 后端 API: 关联 + 计算 | 2 | 30min |
| 7. 后端 API: 触发 WorkBuddy | 2 | 20min |
| 8. 前端: 白板画布引擎 | 1 | 60min |
| 9. 前端: K 线卡片 DOM 元素 | 8 | 30min |
| 10. 前端: 水平线卡片 | 8 | 25min |
| 11. 前端: 文本框卡片 | 8 | 20min |
| 12. 前端: 过渡区卡片 | 8 | 25min |
| 13. 前端: Pickup 状态机 | 8 | 40min |
| 14. 前端: 关联线绘制 | 8 | 30min |
| 15. 前端: 净偏计算 + 结论框 | 8 | 30min |
| 16. 前端: 自动保存 | 8 | 20min |
| 17. 前端: 标注交互（星级+方向） | 8-12 | 40min |
| 18. 前端: 发送 + prompt 生成 | 8 | 30min |
| 19. 集成测试 | all | 60min |
| **总计** | | **~13小时** |

### Phase 2: 多标的 + 跨周期联动

### Phase 3: 记忆学习系统

### Phase 4: 全市场扫描引擎

---

## 11. 文件结构

```
board-app/
├── app.py                      # Flask 主入口（保留+扩展）
├── whiteboard_engine.py        # 核心算法（关联推断、净偏计算）
├── api/
│   ├── __init__.py
│   ├── whiteboard_routes.py    # 新增：白板相关路由
│   ├── pickup_routes.py        # 新增：Pickup 操作路由（可合并到 wb）
│   └── ...（现有路由保留）
├── data/
│   ├── whiteboard_repo.py      # 新增：白板 SQLite DAO
│   └── ...（现有数据层保留）
├── services/
│   ├── whiteboard_service.py   # 新增：白板业务逻辑
│   └── ...（现有服务保留）
├── static/
│   ├── index.html              # 新主页面（替代 index-local.html）
│   ├── css/
│   │   ├── whiteboard.css      # 白板样式
│   │   └── main.css            # 主界面样式
│   ├── js/
│   │   ├── klinecharts-pro.umd.js  # 保留
│   │   ├── whiteboard/
│   │   │   ├── app.js
│   │   │   ├── chart-manager.js
│   │   │   ├── whiteboard.js
│   │   │   ├── element-renderer.js
│   │   │   ├── relation-drawer.js
│   │   │   ├── pickup-controller.js
│   │   │   ├── conclusion-box.js
│   │   │   ├── persistence.js
│   │   │   └── workbuddy-bridge.js
│   │   └── utils.js
│   └── ...（现有静态文件保留）
└── design/
    ├── design.md               # 本文档
    └── plan.md                 # 实施计划（待生成）
```

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| KLineChart Pro Pickup 集成难度 | 高 | 用 chart.subscribeOn('click') 自定义回调 |
| Canvas 性能（100+ 卡片时卡顿） | 中 | 虚拟化渲染：仅渲染视口内卡片 |
| WorkBuddy 实时触发不稳定的 | 低 | MVP 复制兜底，异步增强 |
| 白板 JSON 数据量变大 | 中 | 增量保存 + 快照机制 |
| SQLite 并发写冲突 | 中 | WAL 模式 + 写队列 |

---

## 13. 验收标准

1. 打开面板，选择板块，看到 K 线图
2. 点击"Pickup"按钮，十字准光标出现
3. 点击 K 线，白板区生成 K 线卡片（带 OHLCV 数据）
4. 点击"水平线"，点击图表，白板区生成水平线卡片
5. 每张卡片可设置星级、方向、备注
6. 自动推断的价格对应关系，显示关联线
7. 结论框实时显示净偏计算结果
8. 点击"发送"，弹出 prompt 可复制
9. 白板自动保存，刷新页面后恢复
10. 白板目录可列出、打开历史记录

---

**设计方案完成，待用户确认后进入 Implementation Planning 阶段。**
