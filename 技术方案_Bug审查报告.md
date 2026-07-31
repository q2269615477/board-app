# 技术方案 Bug 审查报告（Superpowers 系统性调试）

> 审查对象：`技术方案_技能WorkBuddy集成.md`  
> 方法论：四阶段调试（根因调查 → 模式分析 → 假设验证 → 实现）

---

## 一、Bugs 列表（按严重程度排序）

### 🔴 Fatal Bugs（方案无法工作）

#### B1: `_notify_workbuddy` HTTP 推送 — 架构性错误

**位置**: 方案四、MCP 桥接器改造 → 方案A

**问题**: 方案假设 WorkBuddy 有 HTTP API 端口 `http://127.0.0.1:9999/api/hook/ai_task`
**实际**: WorkBuddy MCP 通过桥接器进程 **stdin/stdout JSON-RPC** 通信，无 HTTP 端口

```python
# ❌ 方案代码 — 永远连不上
workbuddy_hook = 'http://127.0.0.1:9999/api/hook/ai_task'
requests.post(workbuddy_hook, json=task, timeout=5)
```

**根因分析**: 未读 `mcp_board_bridge.js` 源码。桥接器没有 HTTP server，只有 stdio 管道。

**后果**: Flask 无法主动推送任务给 WorkBuddy。自动触发链路断裂。

**修复选项**:
- **选项1（推荐）**: WorkBuddy 端注册 custom hook，定时轮询 Flask `/api/ai/pending`（需 WorkBuddy 端脚本）
- **选项2**: 前端触发时复制 prompt 到剪贴板，用户手动粘贴到 WorkBuddy（回退到当前实现）
- **选项3**: WorkBuddy 端写一个常驻脚本，bridge 的 MCP server 注册为 `board-app`，WorkBuddy 主动 `tools/call`

---

#### B2: `_analysis_tasks` 非线程安全竞态条件

**位置**: 方案三 → ① Flask 添加任务状态表

**问题**: Flask 是多线程的（`threaded=True`），模块级字典 `_analysis_tasks` 被多线程并发读写

```python
# ❌ 竞态条件
_analysis_tasks[board_code] = {...}  # 线程A写
# 同时线程B也写同一个 key → 覆盖
```

**后果**: 快速点击多个技能时，后一个请求覆盖前一个，前一个轮询永远收不到结果 → 前端"⏳ 分析中"直到超时。

**修复**:
```python
import threading
_analysis_tasks = {}
_analysis_tasks_lock = threading.Lock()

def start_analysis(board_code, skill_id, prompt_template):
    with _analysis_tasks_lock:
        _analysis_tasks[f"{board_code}:{skill_id}"] = {...}
```

---

### 🟠 High Bugs（会导致数据丢失或体验问题）

#### B3: 内存状态 → Flask 重启丢失

**位置**: 方案三 → ① 状态表存储

**问题**: `_analysis_tasks` 是内存字典。Flask 重启（代码修改自动重载）→ 所有 pending 状态丢失 → 前端轮询永远返回 `{status:'none'}`

**后果**: 用户点击技能后看到"⏳"，然后 Flask 自动重载，90秒后超时"No result"

**修复**:
- **轻量方案**: 用 SQLite 建 `ai_tasks` 表替代内存字典
- **中等方案**: 用文件持久化（`data/ai_tasks.json`）+ 启动时加载
- **当前阶段妥协**: 前端检测到 `status === 'none'` 时提示"任务已过期，请重新触发"

---

#### B4: `board_code` 作为 task key → 后覆盖前

**位置**: 方案三 → ① `start_analysis` 函数

**问题**: 同一个板块快速触发两次不同技能（如先点"逐K分析"再点"顶部预警"），后者覆盖前者

```python
# ❌ key 冲突
_analysis_tasks["BK0477"] = {skill_id: "bar_analysis", ...}
_analysis_tasks["BK0477"] = {skill_id: "top_warning", ...}  # 覆盖了！
```

**修复**: key 用 `f"{board_code}:{skill_id}"`，前端轮询时也要带上 `skill_id`

---

#### B5: Worker 异常时任务永久丢失

**位置**: 方案三 → ⑤ 后台任务队列

**问题**: `queue.task_done()` 在 `_notify_workbuddy` 成功后调用，如果调用失败抛异常，task 已经出队但从未被处理

```python
def _worker():
    while True:
        task = _task_queue.get()
        _notify_workbuddy(task)      # ← 抛异常
        _task_queue.task_done()      # ← 永远到不了
```

**修复**:
```python
def _worker():
    while True:
        task = _task_queue.get()
        try:
            _notify_workbuddy(task)
            _task_queue.task_done()
        except Exception as e:
            # 失败重试：重新入队（最多3次）
            if task.get('_retry', 0) < 3:
                task['_retry'] = task.get('_retry', 0) + 1
                _task_queue.put(task)  # 重新入队
            else:
                logger.error(f"任务永久失败: {e}")
                _task_queue.task_done()
```

---

#### B6: 前端 `waitForAiResult` 轮询泄漏

**位置**: 方案三 → ② `waitForAiResult`

**问题**: 每次调用 `waitForAiResult` 创建新的 `setInterval`，但从不清理旧的。用户快速切换板块时，多个 setInterval 同时存在。

```javascript
// ❌ 没有清理旧轮询
_aiWaiting[key] = poll;  // 覆盖引用，但旧的 setInterval 还在跑！
```

**后果**: 内存泄漏 + 网络请求翻倍 + 回调函数可能操作已卸载的 DOM

**修复**:
```javascript
function waitForAiResult(code, skillId){
  const key = code + ':' + skillId;
  
  // 清理旧轮询
  if (_aiWaiting[key]) {
    clearInterval(_aiWaiting[key]);
    delete _aiWaiting[key];
  }
  
  const poll = setInterval(async()=>{ ... }, 3000);
  _aiWaiting[key] = poll;
}
```

---

### 🟡 Medium Bugs（体验/边界问题）

#### B7: 超时时间太短（90秒）

**位置**: 方案三 → ② `waitForAiResult`

```javascript
if(tries > 30){ ... return; }  // 30 × 3秒 = 90秒
```

**问题**: 逐K量价分析 6000+ 根 K 线，AI 处理需要 30秒~3分钟。90秒可能不够。

**修复**: 超时改为 180秒（60次 × 3秒），或根据技能动态调整

---

#### B8: SSE 推送函数未实现

**位置**: 方案三 → ④ POST 回调

```python
push_ai_result_to_sse(board_code, data)  # ← 函数不存在
```

**问题**: Flask 当前没有 SSE 端点，这个函数调用会 `NameError`

**修复**:
- Phase 1: 删除这行，前端只用轮询
- Phase 3: 实现 SSE endpoint `@app.route('/api/events')` 用 `stream_with_context`

---

#### B9: 前端 `waitForAiResult` 的 `tries` 变量拼写风险

**位置**: 方案三 → ② `waitForAiResult`

```javascript
let tries = 0;           // 声明了
if(tries > 30){ ... }    // 用了
```

实际代码中还有另一处：
```javascript
// ❌ 之前的修复遗留：这个变量没有 let/var
key = code+':'+skillId;  // 隐式全局变量！
```

**连带修复**: 补上 `var`/`let` 声明（已在前一轮修复过，但需再次确认）

---

#### B10: `complete_analysis` 不处理"结果先到，任务记录后到"

**位置**: 方案三 → ④ POST 回调

**问题**: WorkBuddy 分析完成先回调 POST `/api/ai/result`，但 Flask 重启导致 `_analysis_tasks` 为空。此时结果保存到 `/data/ai_results.json`（持久化），但状态仍为 'none'。前端再次轮询时读 SQLite/json 可以拿到结果，但不知道 status 是 done 还是 none。

**修复**: GET `/api/ai/result/<code>` 如果内存查不到，检查持久化存储（SQLite），返回 `{status:'done', result:...}` 如果存在

---

## 二、可优化点（非 Bug 但有改进空间）

### O1: 前端应显示"分析进度"而非仅"⏳"

**现状**: 只有 pending/done 两种状态，用户看不到进度

**建议**: WorkBuddy 分析过程中，中间结果通过 POST `/api/ai/result` 多次回传（progress 字段），前端实时更新进度条

---

### O2: 取消/重试机制

**现状**: 用户点击后只能等或刷新页面

**建议**:
- 增加"取消"按钮 → POST `/api/ai/cancel/<code>` 标记取消
- WorkBuddy 端检测取消标记 → 中断分析

---

### O3: 技能缓存（避免重复分析）

**现状**: 每次点击都重新分析

**建议**:
- 分析结果带 timestamp
- 同一天内同一技能再次点击 → 直接返回缓存结果（或提示"已分析过，是否重新分析？"）

---

### O4: 错误分类（用户看到不同的错误提示）

**现状**: 所有错误都显示"超时未收到结果"

**建议**:
- `status: 'failed'` → 显示具体错误原因
- `status: 'none'` → 显示"任务不存在，请重新触发"
- `status: 'timeout'` → 显示"分析耗时较长，请在分析历史中查看"

---

### O5: 分析历史"重新分析"入口

**现状**: 历史记录只能重新渲染

**建议**: 点击历史记录旁的"🔄"按钮 → 用相同参数触发新分析

---

## 三、总结

| 严重程度 | 数量 | 必须修复？ |
|----------|------|-----------|
| 🔴 Fatal | 2 | 是 — B1（HTTP 推送不可行）、B2（竞态条件） |
| 🟠 High | 4 | 是 — B3（重启丢状态）、B4（key 覆盖）、B5（任务丢失）、B6（轮询泄漏） |
| 🟡 Medium | 4 | 建议 — B7（超时短）、B8（SSE 未实现）、B9（变量声明）、B10（先到后到） |
| 💡 优化 | 5 | 可选 |

**关键结论**:
1. **方案中的 `_notify_workbuddy` HTTP 推送行不通**，需要重新设计触发机制
2. **内存状态 + 非线程安全** 必须先修，否则生产环境频繁出错
3. 前端轮询泄漏是**最隐蔽的 bug**，快速切换板块必现

---

## 四、推荐修复后方案（Phase 1 精简版）

### 修正后的触发机制

```
[浏览器] 点击技能 → POST /mcp/call (tool=trigger_ai_analysis)
    │
    ▼ Flask
[Flask] 写入 SQLite ai_tasks (status=pending)
    │
    ⚠️ 关键差异：不自动推 WorkBuddy，而是把 prompt 弹到前端 + 复制剪贴板
    │
[前端] showToast("分析请求已发送，请在 WorkBuddy 中粘贴分析")
    │ 同时开始轮询（3秒间隔，180秒超时）
    ▼
[WorkBuddy] 用户手动粘贴 → AI 技能执行
    │
    ▼ AI 完成 → POST /api/ai/result
[Flask] 更新 ai_tasks (status=done) → 持久化到 SQLite
    │
    ▼ 前端轮询拿到 done → 渲染结论
[面板] renderAiResult()
```

### SQLite 替代内存字典

```sql
CREATE TABLE IF NOT EXISTS ai_tasks (
    id TEXT PRIMARY KEY,           -- board_code:skill_id
    board_code TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- pending|done|failed|cancelled
    prompt TEXT,
    created_at REAL,
    completed_at REAL,
    result TEXT                    -- JSON string
);
CREATE INDEX IF NOT EXISTS idx_ai_tasks_board ON ai_tasks(board_code, created_at DESC);
```

这是**最小改动 + 最可靠**的 Phase 1 方案。
