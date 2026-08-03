# Spec: board-app 三场景数据更新体系

| 字段 | 值 |
|------|-----|
| **版本** | 0.4.0-implemented |
| **日期** | 2026-07-28 |
| **状态** | ✅ T1–T6 已落地（18080 `/ohlc_batch` + board-app S1/S2） |
| **技能** | spec-driven-development |
| **关联** | `技术文档/数据更新机制.md` · `技术文档/AGENT_DATA_UPDATE_CONTRACT.md` · `qmt-http-server/` · `tasks/plan.md` |

---

## 已批准决策（2026-07-28）

| # | 议题 | 决定 |
|---|------|------|
| D1 | Spec | 批准进入 Plan（后因 D7 修订） |
| D2 | S1 源（初） | HTTP spot 防频控 — **被 D7 部分取代** |
| D3 | S3 × 历史 K batch | **盘后暂不** board-app 拉全历史；**QMT 自动增量** |
| D4 | 18080 | QMT 在线 ⇒ 18080 在线 |
| D5 | 盘中写库 | 盘中 = **可清理缓存**，不当权威日 K |
| D6 | 范围 | 18080 + S1/S2/S3 编排 |
| **D7** | **盘中数据形态** | **不是历史 K**，是 **当日 1 根 OHLC 快照**（open/high/low/close≈现价） |
| **D8** | **盘中最优路径** | **方案 B：原生批量** `get_market_data(stock_code=[...], period='1d', count=1)` |

### 固化假设（v0.3）

1. 复盘用户；不输出买卖建议。
2. **权威历史日 K** → `kline.db`；**盘中 OHLC 快照** → 独立缓存，可丢弃。
3. 盘中字段契约（每标的 ~100B）：

   | 字段 | 含义 |
   |------|------|
   | open | 当日日线 open |
   | high | 当日日线 high |
   | low | 当日日线 low |
   | close | 当日日线 close（**盘中=现价**） |

4. **方案对比（已采纳 B 为改造目标）**

   | 方案 | 单次极限 | 全A≈5000 | 改造 |
   |------|----------|----------|------|
   | A 循环 count=1 | ~500/请求 | ~10 次 / ~8s | 已有 candles_batch 可退化 |
   | **B 原生批量 count=1** | **~5000/请求** | **1 次 / ~0.6s** | **改 extract 多标的** |
   | C subscribe+/quotes | 订阅上限未知 | 订 10s+读 0ms | 订阅管理；可能拖累实盘 |

5. S1（导航 ≤20，10s）：可用 B 小批量或 HTTP 降级；响应体不是瓶颈。
6. S2（点股 1 只）：B 的 N=1 或单票 `/ohlc`；缓存可清。
7. 盘中「扫一屏/板块成分/自选」：走 B，按批 ≤5000（建议先 500→2000 实测）。
8. S3 盘后历史：仍 QMT 自动增量 + 板块 Tushare；**不**用 B 冒充全历史落库。
9. 未知数（实现前建议验证）：
   - **U1**：盘中 `count=1` 的 close 是否为实时价（非昨收）
   - **U2**：若走 C，`subscribe_quote` 上限（本阶段 **不默认 C**）
10. 范围外：tick 修 0、方案 C 全市场订阅、重装 QMT。

---

## 1. Objective

### 1.1 我们在建什么

一套 **分场景、分源、可降级** 的数据更新体系，把已有能力收束为统一契约：

| 场景 | 用户感知 | 频率 | 数据形态 |
|------|----------|------|----------|
| **S1 顶部导航栏** | 指数涨跌/点位在动 | **每 10 秒** | 现价 + 涨跌幅（spot） |
| **S2 左侧点股** | 点开某股图立刻对 | **按需 + 短 TTL** | 日 K（SQLite）+ 末 bar/现价校正 |
| **S3 盘后全量** | 全市场日 K 到齐目标日 | **每日 1 次（+ 欠更补）** | 完整 OHLCV 写入 `kline.db` |

### 1.2 为何

当前多源并存但职责交叉、路径分裂：

- 外部 `qmt_api@58600` 读缓存可停在旧日；
- 策略内 `18080/candles(_batch)` 已有更新日 K，**board-app 未接**；
- 盘中 spot 已有 HTTP，WebSocket 曾绑空 QMT 缓存；
- 盘后 Tushare 板块 + QMT/HTTP 个股混用，Agent/人缺少一张「谁在何时用谁」的图。

### 1.3 成功长什么样

- S1：交易时段导航栏 ≤10s 刷新，源可标注（qmt18080 | http），失败不整页卡死。
- S2：点击个股后首屏 K 线 < 2s（本地命中）或 < 5s（需补洞）；末 bar 与现价不长期矛盾。
- S3：`scan_update_debt` 在盘后任务后，指数非 skip lagging→0；板块 lagging 仅剩可解释；个股单次 catchup 可证明 ≥200 只推进。
- 任意 Agent：只跑 `scripts/agent_data_update.py` + 本 Spec 源表，不发明第二套爬虫。

---

## 2. Tech Stack（现状锚点）

| 层 | 技术 |
|----|------|
| 面板 | Flask :5000 · 前端 static JS · SSE/WS |
| 主库 | SQLite `data/kline.db` |
| QMT 策略 HTTP | `127.0.0.1:18080`（`qmt-http-server/deploy`，ContextInfo） |
| QMT 公式口 | `58600` qmt_api（降级读） |
| Mini xtdata | `58610`（可选，默认不依赖） |
| 板块 | Tushare `dc_daily` / `dc_index` / member |
| HTTP 行情/K | 东财 push2 / push2his · 腾讯 qt/fqkline |
| Agent 入口 | `scripts/agent_data_update.py` |
| 语言 | Python 3（venv）+ QMT 内 3.6 策略环境 |

---

## 3. Commands

```text
# 健康
.\venv\Scripts\python.exe scripts\agent_data_update.py probe
.\venv\Scripts\python.exe scripts\agent_data_update.py debt
curl http://127.0.0.1:18080/health
curl "http://127.0.0.1:18080/candles_batch?symbols=600519.SH,000001.SH&period=1d&count=3"

# 盘后 / 补齐（实现后应对齐本 Spec 源优先级）
.\venv\Scripts\python.exe scripts\agent_data_update.py catchup --stocks-limit 200
.\venv\Scripts\python.exe scripts\agent_data_update.py indices
.\venv\Scripts\python.exe scripts\agent_data_update.py boards --only-lagging

# 测试（聚焦，禁止默认全量挂起包）
.\venv\Scripts\python.exe -m pytest tests/test_lagging_stock_update.py tests/test_cancel_propagation.py -q --tb=line

# 面板
.\venv\Scripts\python.exe app.py
# 或既有启动方式；QMT 策略须保持运行以提供 18080
```

---

## 4. Project Structure（本体系相关）

```text
data/kline.db                 → 日 K 权威存储
data/qmt_client.py            → 58600/58610 客户端（降级）
data_update_manager.py        → 盘后/欠更/全量编排
data/board_api.py             → 板块 Tushare + spot
data_loader.py                → spot HTTP（个股/指数）
services/kline_service.py     → 读库 + 末 bar/HTTP 补洞
services/realtime_websocket.py→ 导航栏推送（须改源）
services/update_task_factories.py → force 任务
scripts/agent_data_update.py  → Agent 唯一 CLI
qmt-http-server/deploy/       → 18080 策略 HTTP（candles_batch）
docs/specs/本文件
技术文档/AGENT_DATA_UPDATE_CONTRACT.md → 同步修订
技术文档/数据更新机制.md      → 长文同步
tasks/plan.md · tasks/todo.md → Phase 2/3 产出
```

---

## 5. 体系设计：三场景 × 多源

### 5.1 总原则

```text
1. 读多写少：盘中尽量读缓存/短 TTL spot；写库集中在盘后与按需补洞。
2. 源有主次：每类资产一张优先级表，禁止「谁先返回用谁」无日志。
3. 失败降级：上一级超时/空 → 下一级；全部失败 → 上次有效缓存，并打 channel。
4. 规模门禁：10s 任务禁止全市场；全市场只在 S3。
5. 可观测：每次更新结果带 channel、elapsed、target_trade_date、success/fail。
```

### 5.2 源优先级表（核心契约）

#### 个股 / 指数 — 日 K（写入或校正 `kline.db`）

| 优先级 | 源 | 接口/位置 | 适用 |
|--------|-----|-----------|------|
| **P0** | QMT 策略 HTTP | `18080/candles` · `/candles_batch` | S2 补洞 · **S3 主路径** |
| P1 | QMT 公式口 | `qmt_client` @58600 | 18080 不可用时 |
| P2 | HTTP 历史 | 腾讯 fqkline · 东财 push2his | 批缺/陈旧尾补 |
| — | Mini 58610 download | 可选 | 明确启用时 |
| ✗ | Tushare 个股日线 | — | **不作个股主源**（既有策略） |

#### 个股 / 指数 — 盘中现价（不强制写全历史）

| 优先级 | 源 | 适用 |
|--------|-----|------|
| **P0** | HTTP spot（东财 push2 / 腾讯） | **S1 · S2** 默认（稳、与 18080 tick 无关） |
| P1 | 18080 `/quote`·`/quotes`（若 tick 有数） | 盘中增强 |
| P2 | 18080 最近 1m/5m 末 close | tick 空时的「准实时」 |
| P3 | SQLite 末日 close | 纯离线兜底 |

#### 板块 BK*

| 用途 | 主源 | 灾备 |
|------|------|------|
| 日 K 落库（S3） | Tushare `dc_daily` | 东财 push2his |
| 涨跌列表/spot（S1 旁路/S2） | Tushare `dc_index` 最新 trade_date 或短 TTL 缓存 | 东财 Push2 |
| — | **禁止 QMT** | — |

### 5.3 场景 S1 — 顶部导航栏 10 秒

**目标：** `PREWARM_TARGETS`（及配置白名单）指数点位/涨跌每 10s 更新。

**数据流：**

```text
Timer/WS 每 10s
  → 仅白名单 codes（≤20）
  → spot 源：HTTP P0（并行/短超时 2s）
  → 可选：18080 quotes 若 quote_count>0 则覆盖
  → 推前端（WS 或 SSE 或轮询）
  → 不写全历史；可选更新内存 spot 缓存
```

**验收：**

- 配置项 `NAV_REFRESH_INTERVAL_SEC=10`（或等价）。
- 单轮 wall time p95 < 3s；单源失败不阻塞整轮。
- 响应/推送含 `channel`、`ts`。
- 非交易时段：可用最后交易日收盘或标记 `session=closed`，不报假涨跌。

**不做：** 10s 调 `qmt_update_all_stocks` / Tushare 全板块。

### 5.4 场景 S2 — 左侧点股即时

**目标：** 用户点击某股/指数，图表与现价正确。

**数据流：**

```text
点击 code
  → 1) read kline.db daily（主路径，快）
  → 2) 若 last_date < target_trade_day 或末 bar 半成品：
        优先 18080/candles（单票，count 小窗口）
        失败 → HTTP supplement（现有 kline_service）
        成功 → save_kline 缝合
  → 3) spot：HTTP 现价 → 校正图上末 bar close/high/low（TTL 如 10–30s）
  → 4) 返回 JSON；后台可 async 再补，不挡首屏超过 5s
```

**验收：**

- 本地已是目标日：p95 < 500ms。
- 缺 1–3 日：p95 < 5s 且补后 db last_date 前进。
- 连续点击不同股票不串数据（按 code 缓存键）。

**不做：** 点股触发全市场 batch；点股走 Tushare 个股日线主路径。

### 5.5 场景 S3 — 盘后全量

**目标：** 目标交易日日 K 对齐；欠更可扫、可补、可取消。

**数据流：**

```text
15:05+ 或 agent catchup / force 后台
  → scan_update_debt (before)
  → 指数：18080 batch（QMT 映射码）→ 缺口公式口 → HTTP
  → 个股 pending=date_lag|sparse：
        18080 /candles_batch（建议每批 ≤50–100）
        缺数 → HTTP _http_daily_batch_fast
        （58600 仅 18080 整体不可用时）
  → 板块：update_all_boards(only_lagging) Tushare
  → materialize 高周期（若既有）
  → scan_update_debt (after) + 写 task.detail
```

**验收：**

- `channel` 统计可区分 `qmt18080` / `formula` / `http` / `tushare`。
- 单批 50 股 candles_batch 在 18080 可用时 p95 < 10s。
- cancel_check 批间生效；取消不 mark_done。
- Agent 契约文档与 CLI 输出字段一致。

**不做：** 盘后依赖 58610；盘后用东财 spot 冒充完整日 K 主源（仅允许尾补）。

### 5.6 与「本地库 / Tushare / 东财 HTTPS / QMT」如何合成一张图

```text
                    ┌─────────────────────────────┐
                    │     用户界面 / Agent CLI      │
                    └──────────────┬──────────────┘
           S1 10s spot │  S2 点股   │  S3 盘后
                       ▼           ▼              ▼
              ┌────────────┐ ┌──────────┐ ┌────────────────┐
              │ HTTP Spot  │ │ kline.db │ │ 编排器          │
              │ 东财/腾讯  │ │  读写    │ │ data_update_*   │
              └─────▲──────┘ └────▲─────┘ └────────┬───────┘
                    │             │                 │
                    │        补洞/全量写入           │
                    │             │                 │
         ┌──────────┴─────────────┼─────────────────┼──────────┐
         │                        │                 │          │
         ▼                        ▼                 ▼          ▼
   东财 HTTPS              QMT 18080          QMT 58600    Tushare
   push2/push2his          candles_batch      公式降级      仅板块
   腾讯 fqkline            （策略内新K）       （旧缓存）
         │                        │                 │          │
         └────────────────────────┴─────────────────┴──────────┘
                                  │
                                  ▼
                           data/kline.db
                        （日K唯一权威落点）
```

**一句话合成：**  
- **库**是中心；  
- **盘中**用东财/腾讯 HTTPS 做「快」；  
- **日 K 新**优先 QMT 18080；  
- **板块**只用 Tushare（+ 东财灾备）；  
- **58600** 降级；**58610** 可选。

---

## 6. Code Style（实现时遵循）

```python
# 源通道必须可观测
result = {
    "channel": "qmt18080",  # qmt18080 | formula | http | tushare | mixed
    "target_trade_date": "20260728",
    "success": 50,
    "failed": 0,
    "elapsed_sec": 1.2,
}

# 场景门禁
assert len(nav_codes) <= 20, "S1 must not scan market"
# S3 only:
# qmt_update_all_stocks(...) / candles_batch chunks
```

- 命名：`channel`、`target_trade_date`、`lagging` 与既有 debt API 对齐。  
- 超时：spot ≤2s；单票 candles ≤5s；batch 按 size 线性预留。  
- 禁止：在 Flask 请求线程同步全市场循环无超时。

---

## 7. Testing Strategy

| 级别 | 内容 | 位置 |
|------|------|------|
| 单元 | 源优先级选择、code 映射 18080、batch 分块 | `tests/test_*` |
| 契约 | 18080 不可用时降级公式/HTTP；channel 字段 | mock |
| 集成（本机可选） | curl 18080 batch + 写库 1 票 | 手工/脚本 |
| 回归 | lagging/cancel/meta 既有聚焦测 | 禁止默认全量 pytest |
| S1 | 间隔配置=10；白名单长度 | 单元 |
| S3 | debt before/after 样本 | `agent_data_update debt` |

覆盖率：不强制全局 %；**新增分支必须有测或可脚本验收。**

---

## 8. Boundaries

### Always

- 更新日 K 最终以 `kline.db` 为准并记录 `channel`。
- S1 只碰白名单；S3 才全市场。
- 板块走 Tushare 主路径。
- Agent 走 `scripts/agent_data_update.py`。
- 改源优先级先改本 Spec / CONTRACT，再改代码。

### Ask first

- 改 `PREWARM_TARGETS` 规模或 10s 改为其它间隔。
- 启用 58610 为默认。
- 把 18080 交易/下单接口接入面板。
- 调整欠更验收阈值（200/100 等）。
- 原生 `get_market_data` 多代码一次 RPC（方案 B）。

### Never

- 盘中 10s 全市场 download / Tushare 扫库。
- 用 Tushare 个股日线替换 QMT 主源（除非用户改策略）。
- 无 18080/公式/HTTP 时假写「已更新到目标日」。
- 提交密钥、`server_config` 账号令牌。
- 输出买卖建议（AGENTS.md）。

---

## 9. Success Criteria（可测试）

1. **源表成文**：本 Spec §5.2 与 `AGENT_DATA_UPDATE_CONTRACT` 一致且已提交。  
2. **S1**：配置 10s；导航数据源为 HTTP spot（或文档记载的 P0）；推送/轮询含 ts。  
3. **S2**：点股路径「db → 18080 补洞 → HTTP」有代码锚点与至少 1 个自动化测或脚本。  
4. **S3**：全量/catchup 在 18080 up 时优先 `candles_batch`；debt 报告 channel 可区分。  
5. **降级**：停 18080 后 S3 仍能公式或 HTTP 完成样本补齐，不崩溃。  
6. **性能基线（本机 18080 up）**：batch 20 日 K < 3s；S1 单轮 < 3s。  
7. **用户验收句**：  
   - 「导航 10 秒在动且不卡」  
   - 「点股图是今天/目标日」  
   - 「盘后 debt 按约定下降」

---

## 10. Open Questions（需你拍板）

1. S1 是否 **必须** 尝试 18080 tick，还是 **仅 HTTP spot** 即可（推荐仅 HTTP，简单稳）？  
2. S3 个股 batch 大小默认 50 还是 100？  
3. 盘后是否 **强制** 要求 QMT 策略（18080）在线，还是允许纯 HTTP 完成（推荐允许纯 HTTP 降级）？  
4. S2 是否在点股时 **写回** 18080 拉到的日 K 到 sqlite（推荐写回）？  
5. 本 Spec 批准后，Phase 2 Plan 是 **只接 18080+S1/S2/S3 编排**，还是包含 tick 修复？

---

## 11. Out of Scope（本版本明确不做）

- 修复 18080 tick 回调为 0（可另开 Spec）  
- 重装 QMT / 恢复 DAT 后台同步服务  
- 原生多代码 `get_market_data` 方案 B（可选后续）  
- 前端大改版、共振、下单  

---

## 12. Phase Gate

| 阶段 | 状态 |
|------|------|
| **1 SPECIFY** | ✅ 已批准（v0.2.0 + D1–D6） |
| **2 PLAN** | ✅ 已写出 `tasks/plan.md` v2.0.0 — **待 Plan 批准** |
| **3 TASKS** | ✅ 已写出 `tasks/todo.md` T1–T6 |
| **4 IMPLEMENT** | 未开始 — 需 `Plan 批准` |

**请回复：`Plan 批准` 或改 AD/Task 编号。**
