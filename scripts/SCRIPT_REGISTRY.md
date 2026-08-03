# scripts/ 脚本注册表

> 本文件标记 scripts/ 目录下各脚本的用途分类，防止历史脚本与生产脚本混用。
> 分类依据：是否被前端运行时依赖、是否被测试直接调用、是否文档明确提到。

## 分类约定

- **production**：生产入口，前端/测试/文档链路依赖的脚本
- **diagnostic**：诊断工具，一次性排查问题用
- **probe**：探测脚本，探测 QMT/Tushare 接口能力
- **archive**：已归档脚本，移入 scripts/_archive/，不做任何调用

---

## production（生产入口）

这些脚本是数据更新链路的正式入口：

| 脚本 | 职责 | 调用方 |
|------|------|--------|
| agent_data_update.py | 全量数据更新编排 | 手动触发 / board-app全量更新.bat |
| build_3level_classification.py | 重建三级板块分类 | 分类维护入口 |
| update_boards.py | 更新板块数据 | 日更链路 |
| update_constituents.py | 更新成分股 | 日更链路 |
| run_full_update.py | 完整更新流程 | 维护入口 |
| validate_board_classification.py | 分类校验 | 测试门禁 / 手动 |
| enrich_board_tags.py | 板块标签补充 | 分类维护 |
| daily_stock_update.py | 个股日更 | 日更链路 |
| report_tag_quality.py | 标签质量报告 | 诊断辅助 |

---

## diagnostic（诊断工具）

用于排查问题，非生产链路依赖：

| 脚本 | 职责 |
|------|------|
| check_system_status.py | 系统状态检查 |
| check_status_simple.py | 简化状态检查 |
| diag_stock.py | 个股诊断 |
| debug_minqmt.py | QMT 调试 |
| verify_constituents.py | 成分股校验 |
| verify_kline_periods.py | K 线周期校验 |
| verify_tushare.py | Tushare 连通性校验 |
| fix_date_format.py | 日期格式修复 |
| inspect_qmt_env.py | QMT 环境检查 |
| inspect_tree.py | 目录结构查看 |
| import_qmt_export_csv.py | QMT 导出 CSV 导入 |
| parse_dat_inner.py | 解析 DAT 内部格式 |
| read_dat_inner.py | 读取 DAT 内部 |
| analyze_dat_format.py | 分析 DAT 格式 |
| batch_check_all_boards.py | 批量检查所有板块 |
| batch_check_worker.py | 批量检查工作线程 |
| fast_update_all_stocks.py | 快速全量更新个股 |
| update_all_stocks.py | 全量更新个股 |
| update_stock_helper.py | 个股更新辅助 |
| run_backfill_direct.py | 直接回补 |
| run_phase2_continue.py | 阶段2继续执行 |
| run_phase2_progress.py | 阶段2进度 |
| tushare_board_update.py | Tushare 板块更新 |
| reclassify_boards.py | 板块重分类 |
| rebuild_board_classification_v5.py | 重建分类 v5 |
| refine_classification_v51.py | 细化分类 v51 |
| test_data_update.py | 数据更新测试 |
| test_em_stock.py | EM 个股测试 |
| test_minqmt_fixed.py | MiniQMT 修复测试 |
| test_minqmt_manager.py | MiniQMT 管理测试 |
| test_minqmt_quick.py | MiniQMT 快速测试 |
| test_minqmt_three_modes.py | MiniQMT 三模式测试 |
| test_qmt_download.py | QMT 下载测试 |
| test_qmt_stocks.py | QMT 个股测试 |
| test_three_time_dimensions.py | 三维时间测试 |
| test_datasource_dll.py | 数据源 DLL 测试 |

---

## probe（探测脚本）

已移至 scripts/_probe/ 目录：

| 脚本 | 职责 |
|------|------|
| probe_em_board.py | EM 板块探测 |
| probe_dat_file.py | DAT 文件探测 |
| probe_qmt_apis.py | QMT API 探测 |

---

## QMT 测试/探测脚本（归档候选）

以下以 `qmt_test_stocks*` 和 `qmt_probe*` 前缀的脚本为历史实验脚本，应归档：

- qmt_test_stocks.py ~ qmt_test_stocks_v5.py
- qmt_probe_inner.py
- qmt_update_via_mini.py
- miniqmt_boot.py
- miniqmt_boot.py

---

## 下划线前缀脚本（归档候选）

以 `_` 前缀命名的脚本为一次性脚本或实验脚本，应归档至 scripts/_archive/：

_analyze_boards.py, _analyze_gaps.py, _check_index_dates.py, _compare_qmt_tushare.py,
_db_snapshot.py, _debug_stock_batch.py, _e2e_data_pipeline.py, _e2e_obsidian_agent.py,
_extract_boards.py, _fetch_dc_daily.py, _fetch_dc_daily_em.py, _mini_md3_decode.py,
_mini_nologin_probe.py, _mini_probe_update.py, _parse_qmt_dat.py, _phase_status.py,
_probe_kline_api.py, _qmt_diag_latest.py, _qmt_diag_stale.py, _qmt_download_probe.py,
_qmt_probe_*.py, _rematerialize_periods.py, _run_doc_catchup.py, _stock_freshness.py,
_stress_ohlc_batch.py, _test_qmt_api_write.py, _verify_*.py

---

## 非脚本文件

| 文件 | 用途 |
|------|------|
| check_progress.json | 进度缓存 |
| update_all.bat | 全量更新批处理 |
| fix_start_qmt.ps1 | QMT 启动修复 |
| verify_delivery.ps1 | 交付校验 |
| _all_boards.txt | 板块列表（大文本） |
| frontend_smoke_test.js | 前端冒烟测试 |
| 启动QMT_修复后.bat | QMT 启动修复 |
| data/ | 数据子目录 |
