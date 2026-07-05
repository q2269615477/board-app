# 技能↔WorkBuddy 交互技术方案

> 生成时间：2026-07-03 | 目标：浏览器面板点击技能→AI分析→结论自动回写面板

---

## 一、方案可行性结论

| 评估项 | 结论 |
|--------|------|
| **技术可行性** | ✅ **可行** — MCP 桥 + Flask 端点 + WorkBuddy Hook 全链路已存在 |
| **架构瓶颈** | ⚠️ **需要补齐 3 处空壳**（状态机、等待UI、通知机制） |
| **预计工作量** | 中等 — 约 30–40 行后端代码 + 少量前端改造 |

**一句话**：按钮→MCP→WorkBuddy→面板的链路骨架已搭好，但缺少「等待状态感知」和「推送通知机制」。

---

## 二、当前架构诊断

```
[浏览器面板]
    │
    ▼ click "逐K量价分析"
[前端 triggerAiAnalysis]
    │ POST /mcp/call (tool=trigger_ai_analysis)
    ▼
[Flask /mcp/call API]
    │ 返回 {prompt}（⚠️空壳：没有真正触发 WorkBuddy）
    ▼
[前端 waitForAiResult]
    │ 每2秒轮询 GET /api/ai/result/<code>
    ▼
[Flask /api/ai/result/<code>]
    │ 返回 latest_result（初始为空）
    ▼
[WorkBuddy Hook]
    │ POST /api/ai/result（AI完成时回调）
    ▼
[Flask 保存结果]
    │ 存入 /data/ai_results.json
    ✅ 前端下一次轮询就能拿到结论
```

**核心问题**：`trigger_ai_analysis` 当前**没有真正调用 WorkBuddy**，只是把 prompt 字符串返回给前端复制粘贴。

---

## 三、改造方案（最小可行闭环）

### 3.1 后端改造（5处）

#### ① Flask 添加任务状态表

```python
# services/ai_service.py（或新建 task_store.py）
_analysis_tasks = {}  # board_code -> {status, skill_id, created_at, result}

def start_analysis(board_code, skill_id, prompt_template):
    """标记分析已开始（pending）"""
    _analysis_tasks[board_code] = {
        'status': 'pending',  # pending -> running -> done | failed
        'skill_id': skill_id,
        'created_at': time.time(),
        'result': None
    }

def get_task_status(board_code):
    """查询任务状态"""
    task = _analysis_tasks.get(board_code)
    if not task:
        return {'status': 'none'}
    return {'status': task['status'], 'created_at': task['created_at']}

def complete_analysis(board_code, result):
    """AI完成回调"""
    if board_code in _analysis_tasks:
        _analysis_tasks[board_code]['status'] = 'done'
        _analysis_tasks[board_code]['result'] = result
```

#### ② `/mcp/call` 端点改造

```python
# api/signal_ai_routes.py — handler for trigger_ai_analysis
@bp.route('/mcp/call', methods=['POST'])
def mcp_call():
    data = request.get_json()
    tool = data.get('tool')
    args = data.get('arguments', {})
    
    if tool == 'trigger_ai_analysis':
        board_code = args.get('board_code')
        skill_id = args.get('skill_id')
        prompt = args.get('prompt_template', '')
        
        # 1. 标记任务开始
        start_analysis(board_code, skill_id, prompt)
        
        # 2. 通过 MCP 桥通知 WorkBuddy（实际调用）
        # 方式A：同步调用 mcp_board_bridge 的 HTTP 推送到 WorkBuddy
        # 方式B：写入 task_queue，由后台线程轮询
        
        return jsonify({
            'ok': True, 
            'status': 'pending',
            'task_id': f'{board_code}:{skill_id}',
            'prompt': prompt  # 保留，用于 debug
        })
    
    # ... 其他 tools
```

#### ③ `/api/ai/result/<code>` 改造（返回状态+结果）

```python
@bp.route('/api/ai/result/<board_code>', methods=['GET'])
def api_ai_result_get(board_code):
    """返回 {status, result}，前端可判断是否在分析中"""
    task = get_task_status(board_code)
    if task['status'] == 'done':
        return jsonify({**task, 'summary': task['result']})
    return jsonify({'status': task['status']})  # pending 或 running
```

#### ④ `/api/ai/result` POST 回调（保存+推送）

```python
@bp.route('/api/ai/result', methods=['POST'])
def api_ai_result_post():
    data = request.get_json() or {}
    board_code = data.get('board_code')
    
    if not board_code:
        return jsonify({'error': 'missing board_code'}), 400
    
    # 保存结果
    complete_analysis(board_code, data)
    
    # 通过 SSE 推送（如果前端已连接）
    push_ai_result_to_sse(board_code, data)
    
    return jsonify({'ok': True, 'stored': True})
```

#### ⑤ 后台任务队列（可选但推荐）

```python
# tasks/analysis_queue.py
# 使用 threading + queue 实现简单的异步任务分发
import threading, queue, time

_task_queue = queue.Queue()
_worker_running = False

def enqueue_analysis(board_code, skill_id, prompt):
    """入队分析请求"""
    _task_queue.put({
        'board_code': board_code,
        'skill_id': skill_id,
        'prompt': prompt,
        'enqueued_at': time.time()
    })

def _worker():
    """消费队列：推送到 WorkBuddy"""
    while True:
        task = _task_queue.get()
        if task is None:
            break
        # 这里调用 WorkBuddy MCP 或 Hook
        _notify_workbuddy(task)
        _task_queue.task_done()

def start_worker():
    global _worker_running
    if _worker_running:
        return
    _worker_running = True
    threading.Thread(target=_worker, daemon=True).start()
```

### 3.2 前端改造（3处）

#### ① `triggerAiAnalysis` — 保留 prompt，增加状态感知

```javascript
async function triggerAiAnalysis(btn, board){
  // ... 组装参数 ...
  
  const r = await fetch(API+'/mcp/call', { /* ... */ });
  const j = await r.json();
  
  if(j.status === 'pending'){
    // 立即更新按钮为"等待中"
    btn.classList.add('analyzing');
    btn.querySelector('.skill-status').textContent='⏳';
    toast('分析请求已发送，等待 WorkBuddy...');
  }
  
  waitForAiResult(board.code, sid);
}
```

#### ② `waitForAiResult` — 添加状态反馈

```javascript
function waitForAiResult(code, skillId){
  // ... panel 初始化 ...
  
  let tries = 0;
  const poll = setInterval(async()=>{
    tries++;
    if(tries > 30){ 
      clearInterval(poll); 
      content.innerHTML+='<div style="color:#ef5350;font-size:10px">超时未收到结果，请检查 WorkBuddy 是否运行</div>'; 
      return; 
    }
    
    try{
      const r = await fetch(API+'/api/ai/result/'+code);
      const j = await r.json();
      
      if(j.status === 'running'){
        content.innerHTML='<div style="color:#f39c12;font-size:11px">🔄 AI 正在分析中...</div>';
      } else if(j.status === 'done' && j.summary){
        clearInterval(poll);
        renderAiResult(j);
        // 更新按钮状态...
      }
    }catch(e){}
  }, 3000);  // 间隔改为3秒，避免刷屏
}
```

#### ③ `renderAiResult` — 兼容新格式

```javascript
function renderAiResult(r){
  // r 可能是 {status:'done', summary:...} 或直接的 result
  const data = r.status === 'done' ? r.result || r : r;
  // ... 现有渲染逻辑保持不变 ...
}
```

---

## 四、MCP 桥接器改造

### 现状
`mcp_board_bridge.js` 通过 HTTP 调用 Flask，但**没有真正的 stdio↔HTTP 双向推送**。

### 改造方案A（推荐）：Flask 侧主动推送到 WorkBuddy

不需要改桥接器，只需在 Flask 内部调用 WorkBuddy API：

```python
# 在 trigger_ai_analysis handler 中
import requests

def _notify_workbuddy(task):
    """通过 WorkBuddy Hook URL 推送分析任务"""
    workbuddy_hook = 'http://127.0.0.1:9999/api/hook/ai_task'  # WorkBuddy 本地端口
    try:
        requests.post(workbuddy_hook, json=task, timeout=5)
    except Exception as e:
        logger.warning(f'[WorkBuddy] 推送失败: {e}')
```

### 改造方案B（备选）：桥接器加推送到 WorkBuddy

```javascript
// mcp_board_bridge.js 改造
function pushToWorkbuddy(task) {
  return fetch('http://127.0.0.1:9999/api/hook/ai_task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task)
  });
}
```

---

## 五、分阶段实施计划

### Phase 1：最小可用闭环（30分钟）

| # | 文件 | 改动 | 验证标准 |
|---|------|------|----------|
| 1 | `services/ai_service.py` | 添加 `_analysis_tasks` + 3个函数 | `curl /api/ai/result/<code>` 返回 `{status:'pending'}` |
| 2 | `api/signal_ai_routes.py` | `/mcp/call` 调用 `start_analysis` | 按钮点击后状态变为 pending |
| 3 | `api/signal_ai_routes.py` | `/api/ai/result/<code>` 返回完整 task | 前端能收到 pending 状态 |
| 4 | `static/index-local.html` | `waitForAiResult` 读取 `j.status` | 按钮显示"🔄 分析中" |

**验证**：点击技能 → 按钮变⏳ → API 返回 pending

---

### Phase 2：状态机 + 错误处理（20分钟）

| # | 文件 | 改动 |
|---|------|------|
| 5 | `services/ai_service.py` | `pending → running → done \| failed` 三态切换 |
| 6 | `api/signal_ai_routes.py` | POST `/api/ai/result` 调用 `complete_analysis` |
| 7 | `static/index-local.html` | 超时后显示"检查 WorkBuddy"提示 |

---

### Phase 3：异步队列 + SSE 推送（1小时）

| # | 文件 | 改动 |
|---|------|------|
| 8 | `tasks/analysis_queue.py` | 添加 `Queue` + worker 线程 |
| 9 | `api/signal_ai_routes.py` | POST 回调触发 SSE 推送 |
| 10 | `static/index-local.html` | SSE `ai_result` 事件直接渲染，轮询改为兜底 |

---

## 六、数据流改造后全图

```
[浏览器] 点击技能
    │
    ▼ POST /mcp/call (tool=trigger_ai_analysis)
[Flask] 保存 pending → enqueue_analysis()
    │
    ▼ 后台 worker 线程
[Queue] _notify_workbuddy() → POST WorkBuddy Hook
    │
    ▼ WorkBuddy 执行 AI 技能
[AI Skill] 逐K分析器 / 顶部预警 / ...
    │
    ▼ 分析完成 → POST /api/ai/result
[Flask] 保存 done → 推送 SSE ai_result
    │
    ▼ SSE / 轮询 ← [浏览器实时收到结论]
[面板] renderAiResult() 渲染到右侧栏
```

---

## 七、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| WorkBuddy Hook 未配置 | AI 任务无法送达 | Phase 1 先用 prompt 复制粘贴兜底 |
| 端口冲突 | MCP 桥启动失败 | 桥接器复用 5000，不另开端口 |
| SSE 连接断开 | 前端感知不到结果 | 保留轮询作为兜底（`poll.onerror`） |
| 同时触发多个技能 | 状态互相覆盖 | `skill_id` 作为 task key 的一部分 |

---

## 八、附录：API 契约

### POST `/mcp/call` — 触发分析
```json
// Request
{"tool":"trigger_ai_analysis","arguments":{
  "board_code":"BK0477",
  "skill_id":"default_bar_analysis",
  "prompt_template":"请分析{name}({code})..."
}}

// Response (Phase 1)
{"ok":true,"status":"pending","task_id":"BK0477:default_bar_analysis"}
```

### GET `/api/ai/result/<code>` — 查询状态
```json
// 分析中
{"status":"pending","created_at":1720000000}

// 完成
{"status":"done","result":{"summary":"看多🐂","confidence":0.8}}
```

### POST `/api/ai/result` — AI 回调（WorkBuddy→Flask）
```json
{
  "board_code": "BK0477",
  "skill_id": "default_bar_analysis",
  "summary": "基于5根K线分析...",
  "direction": "bullish",
  "confidence": 0.82,
  "key_levels": {"support": ["7800","7750"], "resistance": ["8100"]},
  "operation": {"suggestion": "回调买入", "entry": 7850, "stop_loss": 7700}
}
```
