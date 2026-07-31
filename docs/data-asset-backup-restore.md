# 数据资产备份与恢复方案

本文档说明 board-app 中哪些数据不进 Git、哪些可自举重建、哪些需要外部备份，
以及具体的备份与恢复操作步骤。

新机器 / 新环境克隆代码后，按本文档操作即可恢复完整运行环境。

## 1. 哪些数据不进 Git

以下文件 / 目录因体积大、含运行时状态或可自动重建，被 `.gitignore` 排除：

| 路径 | 类型 | 说明 |
|------|------|------|
| `data/kline.db` | SQLite 主库 | 板块/指数/个股 K 线（QMT + Tushare + 东财），约 642MB |
| `data/kline.db-wal` | SQLite WAL | 运行时 WAL 日志，约 142MB |
| `data/kline.db-shm` | SQLite SHM | 运行时共享内存索引 |
| `data/stock_data.db` | SQLite | 个股数据缓存 |
| `data/annotation_index.sqlite` | SQLite | 图表标注索引 |
| `data/session_index.sqlite` | SQLite | 会话草稿索引 |
| `data/backup/` | 目录 | kline.db 历史备份 |
| `data/行业板块K线数据/` | 目录 | 行业板块 K 线 CSV 缓存（约 498 文件） |
| `data/概念板块K线数据/` | 目录 | 概念板块 K 线 CSV 缓存（约 495 文件） |
| `data/个股数据/` | 目录 | 个股数据 CSV |
| `data/个股K线缓存/` | 目录 | 个股 K 线 JSON 缓存 |
| `data/港股K线缓存/` | 目录 | 港股 K 线缓存 |
| `data/tushare_cache/` | 目录 | Tushare API 响应缓存 |
| `data/noon_cache/` | 目录 | 午盘缓存 JSON |
| `data/update_logs/` | 目录 | 数据更新日志 |
| `data/update_status.json` | JSON | 运行时更新状态 |
| `data/signals.json` | JSON | 分析信号存储 |
| `data/qmt_export/` | 目录 | QMT 导出脚本 |
| `data/constituents_update_*.json` | JSON | 成分股更新快照 |
| `static/search_index.json` | JSON | 搜索索引（构建产物） |
| `vault/` | 目录 | Obsidian vault（人读副本） |

## 2. 哪些数据可自举重建

以下数据无需外部备份，可通过代码 + 数据源自动重建：

| 路径 | 重建方式 |
|------|----------|
| `static/search_index.json` | 运行 `python build_search_index.py` |
| `data/行业板块K线数据/*.csv` | 运行 `python update_all_today.py` 从东财/Tushare 拉取 |
| `data/概念板块K线数据/*.csv` | 同上 |
| `data/个股K线缓存/` | 首次访问个股时自动缓存 |
| `data/港股K线缓存/` | 首次访问港股时自动缓存 |
| `data/tushare_cache/` | Tushare API 调用时自动缓存 |
| `data/noon_cache/` | 盘中自动生成 |
| `data/update_logs/` | 更新任务运行时自动生成 |
| `data/update_status.json` | 应用启动时自动生成 |

**重建步骤（新机器首次部署）：**

```powershell
# 1. 克隆代码
git clone https://github.com/q2269615477/board-app.git
cd board-app

# 2. 安装依赖
pip install -r requirements.txt

# 3. 重建搜索索引
python build_search_index.py

# 4. 重建 K 线数据（需要 Tushare token 或 QMT 连接）
python update_all_today.py

# 5. 启动应用
python app.py
```

## 3. 哪些数据建议外部备份

以下数据包含不可重建的历史数据或用户标注，**必须**外部备份：

| 路径 | 重要性 | 原因 |
|------|--------|------|
| `data/kline.db` | **高** | 主行情库，含历史 K 线，重建耗时数小时且依赖外部 API 配额 |
| `data/annotation_index.sqlite` | **高** | 用户图表标注（支撑位/阻力位），无法重建 |
| `data/session_index.sqlite` | **中** | 用户复盘会话草稿，无法重建 |
| `data/stock_data.db` | **中** | 个股数据缓存，可重建但耗时 |
| `data/backup/kline_backup_before_qmt_sync.db` | **低** | 历史备份，保留可选 |
| `data/signals.json` | **中** | 分析信号历史记录 |
| `data/constituents_update_*.json` | **低** | 成分股更新快照 |
| `vault/TradingVault/` | **高** | Obsidian vault，用户标注的人读副本 |

## 4. 如何备份

### 前提条件

- 备份前先停止 Flask 应用和 QMT 写入，确保 SQLite 数据一致。
- 备份目标目录需要有足够空间（kline.db 约 642MB + WAL 约 142MB）。

### 手动备份步骤

```powershell
# 1. 停止 Flask 应用（Ctrl+C 或 kill 进程）
# 2. 停止 QMT 写入（关闭 QMT 客户端）

# 3. 执行 SQLite checkpoint（将 WAL 写入主库）
cd <项目根目录>
py -c "import sqlite3; c=sqlite3.connect('data/kline.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"

# 4. 复制关键文件到备份目录
$backupDir = "D:\backups\board-app\$(Get-Date -Format 'yyyy-MM-dd')"
New-Item -ItemType Directory -Force -Path $backupDir

# 主库（checkpoint 后 WAL 已合并，只需复制 .db）
Copy-Item data\kline.db $backupDir\
Copy-Item data\stock_data.db $backupDir\
Copy-Item data\annotation_index.sqlite $backupDir\
Copy-Item data\session_index.sqlite $backupDir\
Copy-Item data\signals.json $backupDir\

# Vault（用户标注人读副本）
Copy-Item -Recurse vault\TradingVault $backupDir\TradingVault

# 5. 记录备份日期
echo "Backup completed: $(Get-Date)" > "$backupDir\BACKUP_INFO.txt"
echo "kline.db size: $((Get-Item data\kline.db).Length / 1MB) MB" >> "$backupDir\BACKUP_INFO.txt"
```

### 使用脚本备份（推荐）

```powershell
# 备份到指定目录
.\scripts\export_data_snapshot.ps1 -DestDir "D:\backups\board-app\snapshot-20260731"
```

脚本会：
- 打印每个大文件的路径和大小
- 执行 SQLite checkpoint
- 复制关键数据文件
- 不自动删除原数据
- 不提交备份文件到 Git

## 5. 如何恢复

### 前提条件

- 已克隆代码并安装依赖。
- 有有效的备份目录。

### 手动恢复步骤

```powershell
# 1. 停止应用（如果正在运行）

# 2. 将备份文件放回对应位置
$backupDir = "D:\backups\board-app\2026-07-31"
cd <项目根目录>

Copy-Item "$backupDir\kline.db" data\kline.db -Force
Copy-Item "$backupDir\stock_data.db" data\stock_data.db -Force
Copy-Item "$backupDir\annotation_index.sqlite" data\annotation_index.sqlite -Force
Copy-Item "$backupDir\session_index.sqlite" data\session_index.sqlite -Force
Copy-Item "$backupDir\signals.json" data\signals.json -Force

# Vault（如果备份了）
Copy-Item -Recurse "$backupDir\TradingVault" vault\TradingVault -Force

# 3. 重建搜索索引（确保与数据一致）
python build_search_index.py

# 4. 启动应用并验证
python app.py
# 访问 http://127.0.0.1:5000 确认数据正常
```

### 使用脚本恢复（推荐）

```powershell
# 从备份目录恢复（需要显式确认）
.\scripts\import_data_snapshot.ps1 -SrcDir "D:\backups\board-app\snapshot-20260731" -Confirm
```

脚本会：
- 打印将要覆盖的文件列表和大小
- 要求 `-Confirm` 参数才执行（不传则只 dry-run）
- 不自动删除现有数据（覆盖前会警告）
- 恢复后建议运行 `python build_search_index.py`

### 恢复后验证

```powershell
# 运行验证脚本
.\scripts\verify_baseline.ps1

# 手动检查
# 1. 打开面板，确认 K 线数据加载正常
# 2. 检查标注/会话是否完整
# 3. 确认搜索功能正常
```

## 6. 禁止事项

- **不允许把大数据文件提交到 GitHub。** 包括但不限于：
  - `data/kline.db` / `data/kline.db-wal` / `data/kline.db-shm`
  - `data/*.sqlite`
  - `data/backup/`
  - `data/行业板块K线数据/` / `data/概念板块K线数据/`
  - `static/search_index.json`
  - `vault/`
- **不允许把 GitHub token 写进 remote URL 或文档。**
- **不允许在备份脚本中自动删除原数据。**
- **不允许在恢复脚本中不加确认直接覆盖。**

## 7. 备份频率建议

| 数据 | 建议频率 | 原因 |
|------|----------|------|
| `data/kline.db` | 每日盘后 | 行情数据每日更新，丢失需重新拉取 |
| `data/annotation_index.sqlite` | 每次标注后 | 用户标注不可重建 |
| `data/session_index.sqlite` | 每日 | 会话草稿 |
| `vault/TradingVault/` | 每次标注后 | 人读副本 |
| `data/signals.json` | 每日 | 分析信号历史 |
