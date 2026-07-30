# 板块K线分析面板 — SQLite 数据库迁移报告

## 执行日期
2026-06-30 00:30

## 执行内容
将全部日/周/月线数据从 CSV 文件迁移至 SQLite 数据库

---

## 数据库结构

### 文件位置
`data/kline.db`

### 表结构

#### `kline` — K线数据主表
| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | TEXT | 股票/板块/指数代码（如 `BK0433`, `600519`, `sh000001`） |
| `period` | TEXT | 周期：`daily` / `weekly` / `monthly` |
| `date` | TEXT | 日期（`YYYY-MM-DD`） |
| `open` | REAL | 开盘价 |
| `high` | REAL | 最高价 |
| `low` | REAL | 最低价 |
| `close` | REAL | 收盘价 |
| `volume` | REAL | 成交量 |
| `updated_at` | TEXT | 最后更新时间 |

联合主键：`(code, period, date)`

#### `meta` — 元数据表
| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | TEXT | 代码 |
| `period` | TEXT | 周期 |
| `name` | TEXT | 名称 |
| `type` | TEXT | 类型（industry/concept/stock/index） |
| `category` | TEXT | 分类 |
| `rows` | INTEGER | 数据行数 |
| `first_date` | TEXT | 首条数据日期 |
| `last_date` | TEXT | 末条数据日期 |
| `updated_at` | TEXT | 最后更新时间 |

#### `stock_ledger` — 个股缓存台账
| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | TEXT | 个股代码（主键） |
| `name` | TEXT | 个股名称 |
| `first_cached` | TEXT | 首次缓存时间 |
| `last_updated` | TEXT | 最后更新时间 |

---

## 迁移数据统计

| 指标 | 值 |
|------|-----|
| meta 记录数 | 991 条 |
| 独立代码数 | 990 个 |
| K线总行数 | **2,831,787 条** |
| 数据库文件大小 | ~489 MB |
| 迁移前CSV文件数 | ~2970 个 |
| 迁移后文件数 | **1 个** |

---

## 数据更新策略

### 板块（行业/概念）
- **日线**：增量拉取（只拉取本地缺失日期）
- **周/月线**：AKShare 原生 API 拉取，本地缓存
- 首次无数据时全量拉取

### 个股
- **全量覆盖**：用户查询时拉取近3年数据，直接覆盖本地
- 不设每日自动更新（个股查询由用户主动触发）

### 指数
- **A股指数**：AKShare 无日期参数，全量拉取（0.5秒/个）
- **港股指数**：AKShare 无日期参数，全量拉取（6秒/个）
- **周/月线**：从日线 `W-FRI` 重采样后缓存

---

## 代码变更

### `data_loader.py`
- 新增 `_get_db()` — 线程安全的 SQLite 连接管理（WAL模式）
- 新增 `_db_write_kline()` — 写入（INSERT OR REPLACE）
- 新增 `_db_read_kline()` — 按code/period/日期范围读取
- 新增 `_db_get_last_date()` — 查询本地最后日期
- 所有 load 函数改为 SQLite 优先 + AKShare 增量补充
- 实时行情/搜索函数保持不变

### `data_update_manager.py`
- 个股台账从 `stock_ledger.json` 改为 `kline.db` 的 `stock_ledger` 表
- SQLite 内置事务保障一致性

---

## 启动方式

```bash
cd /d/\.workbuddy/2026-06-27-21-35-52/board-app
python app.py
```

默认端口 5000，自动递增找空闲端口。

---

## 注意事项

1. **AKShare 频率限制**：批量拉取 990 个板块触发了东财 API 限流，解封后全量更新才能正常工作
2. **数据库文件较大**（~500MB），建议在 SSD 上运行
3. 现有 CSV 文件未删除，可作为备份
4. SQLite 使用 WAL 模式，支持读写并发
