# Implementation Plan: 盘中 OHLC 快照体系（方案 B）+ S1/S2/S3

| 字段 | 值 |
|------|-----|
| **计划版本** | **v2.1.0** |
| **日期** | 2026-07-28 |
| **依据** | Spec v0.3.0 · 用户确认「了解/采用方案 B」 |
| **状态** | ⏳ 待确认：先验 U1 或直接批 Plan 开工 |
| **前序** | v2.0.0 以 HTTP/单票为主；**本版改为盘中原生批量 OHLC count=1** |

---

## Overview

盘中要的不是 240 根历史 K，而是：

```text
get_market_data(['open','high','low','close'], period='1d', count=1)
→ 每标的 1 行 × 4 字段 ≈ 100B
```

**方案 B（目标）：** 一次 `stock_code=[...N...]`，改 `extract_market_rows` 支持多标的解析；全 A≈5000 理论一次 ~0.6s / ~500KB。

| 场景 | 用法 |
|------|------|
| **S1 导航 10s** | N≤20 OHLC 快照（B 或同接口）；HTTP spot 仅降级 |
| **S2 点股** | N=1 快照 → 盘中缓存（可清理）+ sqlite 历史图 |
| **盘中批量**（板块成分/自选） | B，分片建议 500→2000 实测 |
| **S3 盘后** | QMT 自动增量历史；**不用 B 写全历史** |

---

## Architecture Decisions

### A1. 新端点（推荐）`/ohlc_batch`（或扩展现有）

```text
GET /ohlc_batch?symbols=600519.SH,000001.SZ,...&period=1d
→ {
  "channel": "qmt_native_batch",
  "period": "1d",
  "count": 1,
  "items": {
    "600519.SH": {"open", "high", "low", "close", "time", ...},
    ...
  },
  "failed": [],
  "elapsed_ms": 123
}
```

- 内部：`get_market_data(fields, stock_code=list, period='1d', count=1, ...)`
- **新建** `extract_market_rows_multi` / 扩展 extract，**尽量不破坏** 现有单标的 `extract_market_rows` + `/candles`
- 方案 A（循环 count=1）保留为 **降级**（N 小或 native 失败时）

### A2. board-app 盘中缓存

- `intraday_ohlc_cache`：key=code，TTL 短（S1 对齐 10s；S2 15–60s）
- **禁止**无条件 `save_kline` 权威日线
- 收盘/次日清理

### A3. S1

- 间隔 **10s**
- 主：18080 `/ohlc_batch`（PREWARM）
- 降级：现有 HTTP spot（防频控：单飞+TTL）
- WS 不再绑空 `qmt_cache` 日线

### A4. S2

- 点股：历史 `kline.db` + overlay 缓存 OHLC
- 拉 18080 N=1 或并入当前 batch 刷新

### A5. S3

- 不变：QMT 自动增量 + Tushare 板块 + debt 观测
- **不做** 全市场历史 candles_batch 主导

### A6. 验证门（建议最先）

| ID | 验证 | 通过标准 |
|----|------|----------|
| **U1** | 盘中 `/candles?count=1` 或试探 native | close≈现价（非冻结昨收） |
| **B1** | native `stock_code` 长度 50/500/2000 | 有数据、耗时、无崩 |
| **B2** | multi extract 解析正确 | 抽查 3 只 OHLC 与单票一致 |

U1 失败 → 盘中改 HTTP/方案 C，B 仅作盘后快照备用。

---

## Dependency Graph

```text
T0  U1 盘中实时性验证（手工/脚本，交易时段最佳）
 │
 T1  qmt-http-server：multi extract + /ohlc_batch（+ A 降级）
 │
 T2  board-app：qmt_http_client.ohlc_batch + intraday cache
 │
 ├── T3  S1 WS/导航 10s → ohlc_batch
 └── T4  S2 点股 overlay
 │
 T5  文档 CONTRACT + S3 说明
 T6  测试与验收
```

---

## Tasks

### T0: 验证 U1（盘中 count=1 是否实时）

- **Acceptance:** 记录 close vs 东财/面板现价；结论 go/no-go 方案 B 盘中
- **Verify:** 交易时段 curl 18080 + 对照
- **Files:** 可选 `scripts/probe_ohlc_realtime.py`
- **Scope:** XS

### T1: 18080 方案 B 实现

- **Acceptance:**
  - [ ] `stock_code` 多标的 + count=1 返回 per-symbol OHLC
  - [ ] 单标的 `/candles` 回归不坏
  - [ ] N=1/50 本地可测；文档注明 N 上限待 B1
- **Verify:** curl `/ohlc_batch`；对比单票
- **Files:** `qmt-http-server/deploy/server_market_utils.py`, `server.py`
- **Scope:** M

### T2: board-app 客户端 + 盘中缓存

- **Acceptance:** 缓存可清；不写权威日 K；down 时降级
- **Verify:** unit tests
- **Files:** `data/qmt_http_client.py`, `services/intraday_cache.py`, config
- **Scope:** M

### T3: S1 导航 10s

- **Acceptance:** interval=10；主 ohlc_batch；降级 HTTP；stale 标记
- **Verify:** mock + 手工 WS
- **Files:** `core/config.py`, `services/realtime_websocket.py`
- **Scope:** M

### T4: S2 点股 overlay

- **Acceptance:** 响应含盘中 OHLC 缓存字段；清理有效
- **Verify:** 单测 + 点股
- **Files:** kline_service / routes
- **Scope:** S–M

### T5: 文档与 S3 收口

- **Acceptance:** CONTRACT 写清 B=盘中快照≠历史全量
- **Files:** CONTRACT, Spec 定稿 0.3
- **Scope:** S

### T6: 测试 + 验收清单

- **Verify:** focused pytest；验收 5 步
- **Scope:** S

### Checkpoint

- U1 go + T1 单票一致 + S1 10s 有数 + S2 缓存可清 + 历史 `/candles` 不回归

---

## Risks

| Risk | Mitigation |
|------|------------|
| U1：count=1 非实时 | 停 B 盘中，S1 回 HTTP |
| multi 返回结构与单票不同 | 专测 extract；保留 A 降级 |
| N 过大拖垮策略线程 | 默认分片 500；可配置 |
| 误把快照 save 成日 K | T2 代码禁 save；清理钩子 |

---

## Out of Scope

- 方案 C 全市场 subscribe（除非 U1 失败再评估）
- 全市场历史 K batch 落库
- tick `/quotes` 修 0（可选后续）

---

## Phase Gate

| 阶段 | 状态 |
|------|------|
| SPECIFY | 🔄 v0.3 方案 B 修订 |
| PLAN | 本文件 v2.1.0 |
| IMPLEMENT | 建议：**先 T0/U1**，或你确认「盘中已验证实时」后直接 T1 |

**请选一项回复：**

1. `先验 U1` — 只做探测脚本/步骤，不改 extract  
2. `Plan 批准，开工 B` — 按 T1→T6 实现（默认交易时段再验 U1）  
3. 修改意见（编号）  
