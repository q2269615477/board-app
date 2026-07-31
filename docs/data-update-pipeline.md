# 数据更新链路契约

> 本文档定义 board-app 的数据更新链路契约。
> 任何数据更新操作必须遵循本契约，确保数据一致性和可验证性。

## 1. 固定更新顺序

分类、成分股、搜索索引和 K 线缓存必须按以下固定顺序执行：

```
1. 更新板块分类       (scripts/build_3level_classification.py / validate_board_classification.py)
2. 更新成分股         (scripts/update_constituents.py)
3. 更新板块/个股/指数 K 线缓存  (agent_data_update.py / daily_stock_update.py)
4. 重建搜索索引       (build_search_index.py)
5. 跑契约测试         (pytest tests/test_board_update_pipeline.py / test_constituents_contract.py)
```

**严禁：**
- 跳过步骤 2 直接更新 K 线（会使成分股面板出现空白）
- 更新 K 线后忘记重建搜索索引（会使搜索仍然搜不到新标的）
- 未经完整测试就部署

---

## 2. 生产入口脚本

唯一被允许触发生产数据更新的脚本：

| 脚本 | 职责 |
|------|------|
| `scripts/agent_data_update.py` | 全量数据更新编排入口 |
| `scripts/run_full_update.py` | 完整更新流程 |
| `scripts/update_boards.py` | 板块数据更新 |
| `scripts/update_constituents.py` | 成分股更新 |
| `scripts/daily_stock_update.py` | 个股日更 |
| `scripts/enrich_board_tags.py` | 板块标签补充 |
| `scripts/build_3level_classification.py` | 重建三级板块分类 |
| `scripts/validate_board_classification.py` | 分类校验 |

---

## 3. 前端禁止依赖的脚本

以下文件被标记为**禁止前端依赖**（仅用于离线维护）：

- 任何以 `_` 前缀命名的脚本
- 任何 QMT 探测脚本 (`qmt_probe_*`, `qmt_test_*`)
- 任何 `test_*.py` 开头的文件
- `scripts/_archive/` 目录下所有内容
- `scripts/_probe/` 目录下所有内容

前端只能通过 `api/update/*` 路由触发数据更新任务，不能直接 import scripts。

---

## 4. 数据写入规则

所有 K 线写入前必须归一化为 `date, open, high, low, close, volume` 格式。

允许的额外字段（不得成为图表加载必要条件）：
- `amount` (成交额)
- `pct_change` (涨跌幅)
- `turnover` (换手率)
- `amplitude` (振幅)

**K 线 canonical schema**：
```text
date, open, high, low, close, volume
```

---

## 5. Tushare Token 规则

禁止：
- Python 文件中硬编码 token
- BAT 文件中硬编码 token
- 测试中写入伪 token

允许来源（按优先级）：
1. 进程环境变量 `TUSHARE_TOKEN`
2. 项目根目录 `.env`
3. 用户级 `~/.board-app.env`

维护脚本调用 Tushare 前必须先确认 token 已加载。

---

## 6. 成分股更新后搜索索引联动

当成分股数据发生变更后，必须：

1. 自动触发重建搜索索引（`build_search_index.py`），或
2. 在前端任务中心显示"搜索索引需要重建"提示，引导手动触发

**推荐方案**：在 `scripts/update_constituents.py` 成功完成后自动调用 `build_search_index.py` 的 `rebuild_index()` 函数。

---

## 7. 验证门禁

每次完成数据更新链路后，必须通过以下验证：

```bash
py -m pytest tests/test_board_update_pipeline.py
py -m pytest tests/test_constituents_contract.py
py scripts/validate_board_classification.py --strict-tags
```

全部通过后才算更新完成。

---

## 8. 改动记录

| 日期 | 改动 | 作者 |
|------|------|------|
| 2026-07-30 | 初版建立数据更新链路契约 | 自动化重构 |
