# 项目重构实施记录 — 2026-07-30

> 按照 `项目重构实施路线图.md` 完成全部四个阶段的重构。

---

## 一、阶段一：约束固化

### 改动范围
- 完善仓库卫生测试
- 标记 scripts 脚本分类
- 确认 .env.example 不含真实 token

### 具体改动
1. **tests/test_repository_hygiene.py**：扩展检查项，从 3 个测试扩展到 7 个测试，新增：
   - `test_hardcoded_token_not_in_untracked_py` — 检查未跟踪的 .py 文件
   - `test_no_new_bak_files` — 禁止 .bak 文件
   - `test_bat_files_not_tracked` — .bat 文件不入 git
   - `test_init_files_exist_in_packages` — 包完整性检查

2. **scripts/SCRIPT_REGISTRY.md**：建立脚本注册表，标记 105 个脚本的分类：
   - production（生产入口）：10 个
   - diagnostic（诊断工具）：30+ 个
   - probe（探测脚本）：3 个
   - archive（归档候选）：49 个下划线脚本 + 11 个 QMT 测试脚本

3. **归档执行**：
   - 49 个下划线前缀脚本 → `scripts/_archive/`
   - 11 个 QMT 测试脚本 → `scripts/_archive/`
   - .bat 文件从 git 跟踪中移除

4. **.gitignore 完善**：新增 `scripts/_archive/`、`scripts/_probe/`、`scripts/__pycache__/` 等排除项

### 验收结果
```
tests/test_repository_hygiene.py: 7 passed
```

---

## 二、阶段二：前端选择链路统一

### 改动范围
- 创建 4 个核心模块
- 修改 3 个现有模块接入统一选择事件
- 添加新模块脚本引用到 index.html

### 新增模块
1. **static/js/symbol-router.js** — 标的选择路由器
   - `classify(code)` — 统一识别 stock/index/board/hk_index/global_index
   - `importSymbol(input)` — 标准化为 { code, name, type, source, trigger }
   - `normalizeCode(code, type)` — 归一化代码格式

2. **static/js/ui-state.js** — 全局 UI 状态管理
   - `snapshot()` — 只读快照
   - `setSymbol(symbol, source)` — 更新选中标的
   - `setPeriod(period)` — 更新周期
   - `setLoading(loading)` — 更新加载状态
   - `subscribe(fn)` — 状态变更订阅

3. **static/js/chart-controller.js** — 图表控制器
   - `applySymbol(sel)` — 唯一图表加载入口
   - `applyPeriod(periodObj)` — 应用周期
   - `bindLoadingSync()` — 监听 kline-loaded/kline-error 事件

4. **static/js/unified-selector.js** — 统一选择事件总线
   - 监听全局 `select-symbol` 事件
   - 协调 SymbolRouter + UIState + ChartController
   - 同步上下文到后端

5. **static/js/search-controller.js**（辅助）— 搜索控制器模板

### 修改的现有模块
1. **index-bar.js** — `clickIdxItem` 发出 `select-symbol` 事件
2. **nav-panel.js** — `selectBoard` 发出 `select-symbol` 事件，不再直接操作 pro.setSymbol
3. **search-panel.js** — `searchPick`/`searchPickHistory` 发出 `select-symbol` 事件
4. **app-init.js** — 添加 `initUnifiedSelector()` 初始化
5. **index.html** — 添加新模块脚本引用

### 统一事件协议
```js
selectSymbol({
  code,       // 标的代码
  name,       // 标的名称
  type,       // 类型: stock/index/board/hk_index/global_index
  source,     // 来源: top-index-bar/nav-panel/bottom-search
  trigger     // 触发方式: click/enter
})
```

### 验收结果
```
tests/test_frontend_entry_contract.py: 5 passed
tests/test_frontend_config.py: 5 passed
tests/test_kline_routes.py: 9 passed
tests/test_kline_data_contract.py: 3 passed
tests/test_symbol_router.py: 11 passed
node --check static/js/*.js: All 20 files OK
```

---

## 三、阶段三：数据更新链路统一

### 改动范围
- 创建统一数据更新链路管理器
- 建立数据更新链路契约文档
- 添加链路测试

### 产出物
1. **services/update_pipeline.py** — 统一数据更新链路管理器
   - `Stage` 枚举定义 5 个阶段
   - `run_pipeline()` — 按固定顺序执行
   - `run_partial(start_from, end_at)` — 从指定阶段开始
   - `on_constituents_updated()` — 成分股更新后自动触发搜索索引重建
   - `register_stage_runner(stage, fn)` — 注册阶段执行函数

2. **docs/data-update-pipeline.md** — 数据更新链路契约文档
   - 固定更新顺序：分类 → 成分股 → K线缓存 → 搜索索引 → 契约测试
   - 生产入口脚本清单
   - 前端禁止依赖的脚本列表
   - 数据写入规则和 Tushare token 规则
   - 成分股更新后搜索索引联动机制
   - 验证门禁

3. **tests/test_update_pipeline.py** — 14 个测试用例
   - 标准管道顺序验证
   - 全部成功/失败中止/继续执行
   - 部分执行（run_partial）
   - 搜索索引重建触发
   - 异常处理

### 验收结果
```
tests/test_update_pipeline.py: 14 passed
```

---

## 四、阶段四：历史文件清理

### 改动范围
- 归档 60 个下划线前缀实验脚本
- 从 git 跟踪中移除归档文件
- 建立文档索引

### 归档统计
- `scripts/_archive/` — 60 个文件
  - 49 个下划线前缀脚本（QMT 探测、诊断脚本）
  - 11 个 QMT 测试脚本
- `scripts/_probe/` — 1 个文件（probe_em_board.py，已存在）
- 4 个 .bat 文件从 git 移除

### 新增文档
- **docs/INDEX.md** — 项目文档入口索引

### 验收结果
- `git check-ignore scripts/_archive` → 确认被忽略
- `git status scripts/` → 归档文件已从 git 跟踪中移除
- scripts 目录保留 30+ 个生产+诊断脚本

---

## 最终验收清单

- [x] 全量测试通过（460 passed，基线 429，新增 31）
- [x] 所有 JS 文件语法检查通过（20/20）
- [x] .env.example 不含真实 token
- [x] .gitignore 完善（归档目录、scripts 排除）
- [x] 仓库卫生测试 7/7 通过
- [x] 统一选择链路模块创建完成（4 个核心模块）
- [x] 现有模块接入统一事件协议（3 个模块）
- [x] 数据更新链路管理器创建完成
- [x] 数据更新链路契约文档建立
- [x] 脚本注册表建立
- [x] 归档目录不被 git 跟踪

---

## 改动范围汇总

| 类别 | 新增 | 修改 | 归档/删除 |
|------|------|------|-----------|
| JS 模块 | 5 个 | 5 个 | 0 |
| Python 服务 | 1 个 | 0 | 0 |
| 测试文件 | 2 个 | 1 个 | 0 |
| 文档 | 3 个 | 0 | 0 |
| 脚本归档 | 0 | 0 | 60 个 |

**总计产出**: 11 个新增文件，6 个修改文件，60 个归档文件

---

## 后续建议

1. 考虑将 `scripts/SCRIPT_REGISTRY.md` 的 production 脚本入口进一步收口到 `update_pipeline.py`
2. 前端交互级回归测试（路线图第 6 个问题）仍需增强
3. `data_loader.py` 继续保持为兼容门面，不再新增业务逻辑

---

实施日期: 2026-07-30
执行人: CatPaw 自动化重构
