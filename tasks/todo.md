# Todo: 盘中 OHLC 快照（方案 B）+ S1/S2/S3

| 字段 | 值 |
|------|-----|
| **计划** | `tasks/plan.md` **v2.1.0** |
| **Spec** | `docs/specs/2026-07-28-data-update-system.md` **v0.3.0** |
| **状态** | ⏳ 待选：先验 U1 / Plan 批准开工 |

---

## 核心共识（方案 B）

```text
盘中 = 当日 1 根 OHLC（close≈现价），不是 240 根历史 K
API  = get_market_data(..., stock_code=[...N...], period=1d, count=1)
改点 = extract 多标的 + /ohlc_batch
落点 = 盘中可清理缓存；权威历史仍 kline.db / QMT 增量
```

| 方案 | 极限 | 本 todo |
|------|------|---------|
| A 循环 | ~500/请求 | 降级 |
| **B 原生批量** | **~5000/请求** | **主路径** |
| C 订阅 | 未知 | 不做（除非 U1 失败） |

---

## Tasks

- [ ] **T0** 验证 U1：盘中 count=1 的 close 是否实时  
  - Verify: 对照面板/东财现价  
  - Priority: high  

- [ ] **T1** 18080：`extract` 多标的 + `/ohlc_batch`（单票 candles 不回归）  
  - Deps: T0 go 或用户跳过  
  - Priority: high  

- [ ] **T2** board-app：client + `intraday_ohlc` 缓存（可清、不 save 权威）  
  - Deps: T1  
  - Priority: high  

- [ ] **T3** S1：10s + ohlc_batch；HTTP 降级；WS 脱空 qmt_cache  
  - Deps: T2  
  - Priority: high  

- [ ] **T4** S2：点股 overlay 盘中 OHLC  
  - Deps: T2  
  - Priority: high  

- [ ] **T5** CONTRACT/文档：B=快照≠历史全量；S3=QMT 增量  
  - Priority: medium  

- [ ] **T6** 聚焦测 + 验收清单  
  - Deps: T3 T4 T5  
  - Priority: medium  

---

## 不做

- 方案 C 全市场订阅  
- 盘后 18080 历史全市场 batch  
- tick 修 0  

---

## 门禁

回复其一：`先验 U1` · `Plan 批准，开工 B` · 改意见  
