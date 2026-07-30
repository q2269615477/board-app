# 废弃文件清单 (Phase 4.2)

## 以下文件保持兼容但不再维护，新代码请直接使用 kline.db (SQLite)：

- `cache_manager.py` — 综合缓存管理（legacy，被 core/cache.py 替代）
- `data/sqlite_repo.py` — SQLite 数据访问层（被 services/ 层替代）

## API 路由前缀

当前所有路由挂载在 `/api/` 下，无版本前缀。建议在下次大版本更新时统一添加 `/v1/` 前缀。
