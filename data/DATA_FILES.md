# data/ 本地数据文件说明

以下文件因超过 GitHub 100MB 限制未纳入版本控制，仅存在于本地。

## SQLite 数据库

| 文件 | 大小 | 说明 |
|------|------|------|
| `kline.db` | ~642MB | 板块/指数/个股 K 线（QMT + Tushare + 东财） |
| `kline.db-wal` | ~142MB | WAL 日志（运行时自动生成） |
| `kline.db-shm` | 32KB | 共享内存索引（运行时自动生成） |
| `session_index.sqlite` | 可变 | 会话草稿索引 |
| `annotation_index.sqlite` | 可变 | 图表标注索引 |

## 备份

| 目录/文件 | 大小 | 说明 |
|-----------|------|------|
| `backup/kline_backup_before_qmt_sync.db` | ~483MB | QMT 同步前 kline.db 备份（2026-06-30） |

## 使用时

以上文件由以下脚本/流程自动生成：
- `data_update_manager.py` → kline.db 日更
- QMT 盘后同步 → kline.db 分钟线
- `session_service.py` → session_index.sqlite
- `annotation_service.py` → annotation_index.sqlite

首次克隆后需运行 `python update_all_today.py` 重建数据。
