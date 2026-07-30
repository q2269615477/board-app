# 产物策略：源码 / 缓存 / 构建产物 / 本地配置

> 本文档明确 board-app 中每一类文件的归属策略，确保 fresh clone 后系统可自举。

## 分类定义

| 分类 | 定义 | 是否跟踪 Git | 示例 |
|------|------|-------------|------|
| **源码** | 应用运行必需的代码与静态资源 | ✅ 跟踪 | `*.py`, `static/js/*.js`, `static/index.html` |
| **数据种子** | 应用启动所需的最小数据集 | ✅ 跟踪 | `static/board_classification.json`, `data/industry_constituents.json` |
| **构建产物** | 由源码 + 数据种子生成的派生物 | ❌ 不跟踪 | `static/search_index.json` |
| **运行时缓存** | 运行时从外部数据源获取并缓存的数据 | ❌ 不跟踪 | `data/个股K线缓存/*.csv`, `data/*.db` |
| **运行时状态** | 应用运行过程中写入的状态文件 | ❌ 不跟踪 | `data/update_status.json`, `scripts/data/update_status.json` |
| **本地配置** | 含环境特定凭据/账号的配置 | ❌ 不跟踪（只跟踪 `.example`） | `qmt-http-server/deploy/server_config.json` |
| **运行日志** | 应用运行产生的日志文件 | ❌ 不跟踪 | `*.log` |
| **临时文件** | 实验/诊断/归档脚本与输出 | ❌ 不跟踪 | `scripts/_archive/`, `scripts/_probe/`, `tmp/` |

## 自举机制

### 搜索索引 (`static/search_index.json`)

- **生成方式**: `python build_search_index.py` 或 `build_search_index.build_index_json()`
- **数据来源** (按优先级):
  1. `static/board_classification.json` (✅ 跟踪) — 板块名称、代码、分类
  2. `KNOWN_INDICES` 常量 — 主要指数
  3. QMT xtdata — 全量指数 + 个股（需 QMT 可用）
  4. `data/industry_constituents.json` + `data/concept_constituents.json` (✅ 跟踪) — 本地成分股缓存兜底
- **自动重建**: `SearchService._load_index()` 在索引文件缺失且无缓存时，自动调用 `build_index_json()` 重建
- **Fresh clone 保证**: 即使无 QMT，也能从板块分类 + 已知指数 + 本地成分股缓存构建可用索引
- **验证测试**: `tests/test_search_bootstrap.py`

### K 线数据 (`data/*.db`, `data/个股K线缓存/*.csv`)

- **加载入口**: `services/kline_service.py` → `KLineService._load_daily()`
- **数据源优先级** (个股日线):
  1. SQLite 本地缓存 (`data/kline.db`) — 最快
  2. QMT HTTP Server (`/candles`) — 在线日线
  3. QMT xtdata (`get_daily`) — 完整 QMT 客户端
  4. Spot 在线行情 — 最新 bar 补齐
- **板块日线**: `load_board_kline()` → SQLite → board_kline 在线
- **Fresh clone 保证**: SQLite 为空时，自动通过 QMT HTTP/xtdata 获取并持久化
- **验证测试**: `tests/test_kline_bootstrap.py`

### 板块涨跌幅

- **加载入口**: `core/lifecycle.py` → `AppContext._reload_board_changes()`
- **数据源优先级**:
  1. Spot API (`board_api.get_industry_spot()`) — 在线
  2. CSV 兜底 (`data/行业板块K线数据/*.csv`) — 本地缓存
- **Fresh clone 保证**: Spot API 可用时不需要 CSV

## .gitignore 策略摘要

```gitignore
# 构建产物
static/search_index.json

# 运行时缓存
data/行业板块K线数据/
data/概念板块K线数据/
data/个股K线缓存/
data/港股K线缓存/
data/个股数据/
data/*.db
data/*.sqlite

# 运行时状态
data/update_status.json
scripts/data/update_status.json
data/update_logs/

# 本地配置（只跟踪 .example）
qmt-http-server/deploy/server_config.json

# 运行日志
*.log

# WorkBuddy 内部
.workbuddy/
```

## 仓库卫生测试

`tests/test_repository_hygiene.py` 包含以下禁止项检查：

| 测试函数 | 禁止内容 |
|---------|---------|
| `test_forbidden_legacy_files_are_not_tracked` | `__pycache__/`, `.pyc`, `.bak`, `_archive/`, `_probe/` 等 |
| `test_no_runtime_data_csv_tracked` | `data/行业板块K线数据/*.csv`, `data/概念板块K线数据/*.csv`, `data/个股数据/*.csv` |
| `test_no_search_index_tracked` | `static/search_index.json` |
| `test_no_workbuddy_tracked` | `.workbuddy/` |
| `test_no_runtime_status_json_tracked` | `update_status.json` |
| `test_no_real_server_config_tracked` | `qmt-http-server/deploy/server_config.json` |
| `test_no_log_files_tracked` | `*.log` |
| `test_bat_files_not_tracked` | `*.bat` |
| `test_hardcoded_tushare_token_is_not_tracked` | 硬编码 Tushare token |
