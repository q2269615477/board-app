# K线面板划线 → WorkBuddy 同步技术方案

> 生成时间：2026-07-03 | 目标：用户在K线图上画支撑/阻力线 → WorkBuddy MCP 接收并分析

---

## 一、根因调查

### 1.1 用户划线去了哪里？

KLineChart Pro 内置画线工具（`horizontalRayLine`、`segment`、`priceLine`等）通过 overlay 实现。当前面板：

| 项目 | 现状 |
|------|------|
| 画线工具 | ✅ `drawingBarVisible: true` 已启用 |
| 可画类型 | `priceLine`、`horizontalRayLine`、`horizontalSegment`、`fibonacciLine`、`parallelStraightLine` 等 |
| 数据存储 | 内存 overlay 实例（图表销毁即丢失） |
| **事件监听** | ❌ **完全没有** — 用户画完线没有任何回调通知后端 |
| **WorkBuddy 同步** | ❌ **空壳** — 无任何同步通道 |

### 1.2 发现的技术关键点

根据 KLineChart Pro 官方文档（v9.6.0）：

```
registerOverlay({ name, totalStep, onDrawEnd, onRemoved, ... })  ← 注册自定义 overlay
createOverlay({ name, points })                                   ← 创建 overlay 实例
getOverlayList()                                                  ← 获取所有 overlay
overrideOverlay({ id, ... })                                      ← 修改属性
removeOverlay({ groupId })                                        ← 批量删除
```

关键发现：
- `onDrawEnd` 回调可以拿到 `{ coordinates, overlay }` — 完整数据点
- `getOverlayList()` 返回所有 overlay 实例数组
- 但没有全局的"任意 overlay 创建事件"

---

## 二、方案设计

### 2.1 策略：自定义 overlay 模板替代内置工具

不是监听所有内置画线工具（不可能），而是提供 **专属的"支撑线"和"阻力线"按钮**，注册自定义 overlay 模板，在 `onDrawEnd` 回调中完成同步。

**为什么这样做**：
- 精确控制（只有这两种线需要同步到 WorkBuddy）
- 语义明确（`supportLine` vs `resistanceLine`）
- 可定制样式（绿色支撑、红色阻力）
- 避免把调试用的临时划线也同步到 WorkBuddy

### 2.2 架构

```
[用户点击"添加支撑线"按钮]
    │
    ▼ 调用 createOverlay({name:'prodSupportLine', points:[]})
[KLineChart Pro] 进入交互式画线模式
    │
    ▼ 用户点击图表放置线条
[onDrawEnd 回调] 触发:
    │ 1. 提取 points → [{timestamp, price}]
    │ 2. 格式化 → {date, price, type}
    │ 3. POST /api/signals → SQLite
    │ 4. 可选: 同步到 MCP → WorkBuddy
    
[删除线条] onRemoved 回调:
    │ 自动从 SQLite 删除
    
[切换板块] 加载时:
    │ GET /api/signals/<code> → 恢复所有画线到图表
```

---

## 三、完整实现代码

### 3.1 后端：扩展 signals API（最小改动）

> 复用已有 signals 表，无需新建表

```python
# api/signal_ai_routes.py — 新增信号类型 support/resistance

@bp.route('/api/signals/support_resistance/<board_code>', methods=['POST'])
def post_support_resistance(board_code):
    """
    用户在 K 线图上画的支撑/阻力线
    Body: { type: 'support'|'resistance', price: float, date: 'YYYY-MM-DD', note: '' }
    """
    data = request.get_json()
    if not data or 'type' not in data or 'price' not in data:
        return jsonify({'error': 'missing type or price'}), 400
    
    from services.signal_service import get_signal_service
    service = get_signal_service()
    
    service.submit_signals(
        board_code=board_code,
        skill='user_drawn',  # 标记为用户手绘
        signals=[{
            'date': data.get('date', ''),
            'type': data['type'],  # 'support' or 'resistance'
            'price': float(data['price']),
            'note': data.get('note', '')
        }],  # append mode
        mode='append'
    )
    
    # 通知 WorkBuddy（可选，异步）
    _notify_workbuddy_draw_signal(board_code, data)
    
    return jsonify({'ok': True})


def _notify_workbuddy_draw_signal(board_code, data):
    """异步推送 WorkBuddy（不阻塞响应）"""
    import threading
    
    def push():
        try:
            import requests
            # WorkBuddy MCP 桥（通过 board-app MCP server）
            requests.post('http://127.0.0.1:5000/mcp/call', 
                json={'tool': 'on_user_draw', 'arguments': {
                    'board_code': board_code,
                    'overlay_type': data['type'],
                    'price': data['price'],
                    'action': 'add'
                }},
                timeout=3
            )
        except Exception:
            pass  # WorkBuddy 不在线时不报错
    
    threading.Thread(target=push, daemon=True).start()
```

### 3.2 前端：自定义 overlay 模板（核心改动）

```javascript
// ===== 在 initPro() 成功后注册自定义画线工具 =====

// 注册"支撑线"模板（绿色，水平线）
window.klinechartspro.registerOverlay({
  name: 'prodSupportLine',
  totalStep: 2,  // 1个点就够了（自动水平延伸）
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  mode: 'weak_magnet',  // 磁吸到K线
  modeSensitivity: 8,
  // 样式：绿色支撑
  styles: {
    line: { color: '#26a69a', size: 2, style: 'solid' },
    text: { color: '#26a69a', size: 12, fontFamily: 'sans-serif', weight: 'bold' },
    point: { color: '#26a69a', radius: 4, activeRadius: 6 }
  },
  // 核心：绘图完成后回调
  onDrawEnd: function({ overlay, coordinates }) {
    finishDrawLine('support', overlay, coordinates);
    return true;
  },
  // 删除时回调
  onRemoved: function({ overlay }) {
    deleteDrawLine(overlay);
  },
  // 只创建一个点（水平射线）
  performEventMoveForDrawing: function({ points, overlay, event }) {
    if (points.length >= 1) {
      // 返回一个点就够了，totalStep=2 即 coordinates.length >= 1 时自动结束
      return points.slice(0, 1);
    }
    return points;
  },
  // 创建图形
  createPointFigures: function({ overlay, coordinates }) {
    if (coordinates.length === 0) return [];
    return [{
      type: 'line',
      attrs: { coordinates },
      styles: { color: '#26a69a', size: 2 }
    }];
  },
  // 创建 Y 轴文字标签
  createYAxisFigure: function({ overlay, coordinates, bounding, yAxis }) {
    if (coordinates.length === 0) return null;
    const point = coordinates[0];
    if (!point) return null;
    return {
      type: 'text',
      attrs: { x: bounding.width, y: point.y, text: '支撑 ' + point.value.toFixed(2), 
               align: 'right', baseline: 'middle' },
      styles: { color: '#26a69a', size: 11 }
    };
  }
});

// 注册"阻力线"模板（红色）
window.klinechartspro.registerOverlay({
  name: 'prodResistanceLine',
  totalStep: 2,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  mode: 'weak_magnet',
  modeSensitivity: 8,
  styles: {
    line: { color: '#ef5350', size: 2 },
    text: { color: '#ef5350', size: 11, weight: 'bold' },
    point: { color: '#ef5350', radius: 4 }
  },
  onDrawEnd: function({ overlay, coordinates }) {
    finishDrawLine('resistance', overlay, coordinates);
    return true;
  },
  onRemoved: function({ overlay }) {
    deleteDrawLine(overlay);
  },
  performEventMoveForDrawing: function({ points }) {
    return points.length >= 1 ? points.slice(0, 1) : points;
  },
  createPointFigures: function({ coordinates }) {
    if (!coordinates.length) return [];
    return [{ type: 'line', attrs: { coordinates }, styles: { color: '#ef5350', size: 2 } }];
  },
  createYAxisFigure: function({ coordinates, bounding }) {
    if (!coordinates.length) return null;
    return {
      type: 'text',
      attrs: { x: bounding.width, y: coordinates[0].y, 
               text: '阻力 ' + coordinates[0].value.toFixed(2) },
      styles: { color: '#ef5350', size: 11 }
    };
  }
});
```

### 3.3 前端：按钮 + 事件处理

```html
<!-- 在工具条添加按钮 -->
<div id="draw-tools" style="display:flex;gap:4px;margin-left:auto">
  <button class="chart-bar-btn draw-btn" onclick="startDrawLine('support')" title="支撑线">
    📗 支撑
  </button>
  <button class="chart-bar-btn draw-btn onclick="startDrawLine('resistance')" title="阻力线">
    📕 阻力
  </button>
  <button class="chart-bar-btn draw-btn" onclick="clearMyDrawings()" title="清除手绘">
    🗑️
  </button>
</div>
```

```javascript
// ===== 画线控制 =====

function startDrawLine(type){
  if(!pro){ toast('K线图未就绪'); return; }
  if(!store.selected){ toast('请先选择板块'); return; }
  
  const name = type === 'support' ? 'prodSupportLine' : 'prodResistanceLine';
  // 创建 overlay 实例，totalStep=2 所以放一个点就够了
  pro.createOverlay({
    name: name,
    points: [],  // 空 → 进入交互式绘图模式
    lock: false,
    mode: 'weak_magnet'
  });
  
  toast(type === 'support' ? '请点击图表放置支撑线' : '请点击图表放置阻力线');
}

// 绘图完成回调
function finishDrawLine(type, overlay, coordinates){
  if(!coordinates || coordinates.length === 0) return;
  
  const price = coordinates[0].value;
  const timestamp = coordinates[0].timestamp;
  const date = new Date(timestamp);
  const dateStr = date.toISOString().slice(0, 10);
  const board = store.selected;
  
  // 保存到后端
  fetch(API+'/api/signals/support_resistance/'+board.code, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      type: type,
      price: parseFloat(price.toFixed(2)),
      date: dateStr,
      note: ''
    })
  }).then(r => r.json())
    .then(j => {
      if(j.ok){
        toast((type==='support'?'支撑':'阻力')+'线已保存 ¥'+price.toFixed(2));
        // 保存 overlay id 用于批量删除
        if(overlay && overlay.id){
          _myDrawSignals.set(overlay.id, board.code + ':' + dateStr + ':' + type);
        }
      } else {
        toast('保存失败');
      }
    })
    .catch(e => toast('网络错误: '+e.message));
}

// 删除回调
function deleteDrawLine(overlay){
  if(!overlay || !overlay.id) return;
  const key = overlay.id;
  
  // 找对应的 board code
  const match = _myDrawSignals.get(key);
  if(!match) return;
  
  const [code, date, type] = match.split(':');
  
  // 调用后端删除该信号
  fetch(API+'/api/signals/support_resistance/'+code, {
    method: 'DELETE',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ type: type, date: date })
  }).catch(()=>{});
  
  _myDrawSignals.delete(key);
  toast('已删除');
}

// 清除当前板块所有手绘
function clearMyDrawings(){
  if(!store.selected) return;
  if(!confirm('清除当前板块的所有手绘线?')) return;
  
  fetch(API+'/api/signals/support_resistance/'+store.selected.code, {
    method: 'DELETE',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ all: true })
  }).then(() => {
    // 清除 overlay
    if(pro) pro.removeOverlay({ groupId: 'user_drawings' });
    toast('已清除所有手绘');
  });
}

const _myDrawSignals = new Map(); // id → code:date:type
```

### 3.4 前端：切换板块时恢复画线

```javascript
// 在 selectBoard 成功调用 loadSignals 之后
function restoreDrawSignals(code){
  fetch(API+'/api/signals/'+code+'?skill=user_drawn')
    .then(r => r.json())
    .then(signals => {
      if(!signals || !signals.user_drawn || !pro) return;
      
      const points = signals.user_drawn.map(sg => {
        const timestamp = sg.date ? new Date(sg.date).getTime() : Date.now();
        const price = sg.price || 0;
        return { timestamp, value: price, text: sg.type };
      });
      
      // 恢复 overlay
      points.forEach(p => {
        const name = p.text === 'support' ? 'prodSupportLine' : 'prodResistanceLine';
        pro.createOverlay({
          name: name,
          points: [{ timestamp: p.timestamp, value: p.value }],
          groupId: 'user_drawings',  // 分组方便批量删除
          styles: { lock: false }
        });
      });
    })
    .catch(e => console.warn('恢复手绘失败:', e));
}
```

---

## 四、WorkBuddy 同步方案（MCP 调用）

### 4.1 方案对比

| 方案 | 实现难度 | 实时性 | 可靠性 | 推荐 |
|------|---------|--------|--------|------|
| A. 异步 MCP 推送 | 低 | 秒级 | 中（WorkBuddy 可能不在线） | ⭐⭐⭐ |
| B. 用户点击按钮触发 | 低 | 即时 | 高（用户确认后再发） | ⭐⭐⭐⭐ |
| C. WorkBuddy 轮询 | 中 | 秒~分钟级 | 高 | ⭐⭐⭐⭐⭐ |

### 4.2 推荐：混合方案 B + C

```
[用户画线] → 保存到 SQLite（立即完成）
    │
    ▼ 同时显示一个"同步到 WorkBuddy"小提示条
[用户点击提示条] → 调用 MCP on_user_draw → WorkBuddy 接收通知
    │
    ▼ WorkBuddy 弹窗："收到 BK0477 的支撑线 ¥7850，是否查看？"
```

 WorkBuddy 端（MCP Tool Handler）收到后：
 ```python
 # WorkBuddy 端 Skill 逻辑
 def on_user_draw(board_code, overlay_type, price, action):
     prompt = f"用户标记了 {board_code} 的{overlay_type}线 ¥{price}，请结合当前走势评估有效性"
     start_ai_analysis(prompt)
 ```

### 4.3 MCP 通信数据格式

```json
// POST /mcp/call from Flask
{
  "tool": "on_user_draw",
  "arguments": {
    "board_code": "BK0477",
    "board_name": "IT服务",
    "overlay_type": "support",
    "action": "add",
    "price": 7850.50,
    "date": "2026-07-03",
    "timestamp": 1720000000000
  }
}
```

---

## 五、实施步骤

### Phase 1：画线 + 保存（30分钟）

1. 在 `initPro()` 后注册 `prodSupportLine` / `prodResistanceLine` 模板
2. 添加手绘按钮到工具栏
3. `onDrawEnd` → POST `/api/signals/support_resistance`
4. 删除/恢复逻辑

**验证**：画线 → 刷新页面 → 线还在

---

### Phase 2：WorkBuddy 通知（30分钟）

1. 画线后显示提示条"已画支撑线 ¥7850 · 点击同步到 WorkBuddy"
2. 点击提示条 → 调用 `/mcp/call` `on_user_draw`
3. WorkBuddy 弹出确认

**验证**：画线 → 提示条出现 → WorkBuddy 通知到达

---

### Phase 3：画线标注增强（20分钟）

1. 支持画线后弹出输入框：添加备注（如"大级别支撑"）
2. 画线样式优化（颜色加粗、标签可拖拽）
3. 导出/分享画线数据

---

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| `onDrawEnd` 回调中 `pro` 为 null | 回调内 `if(!pro) return;` |
| WorkBuddy MCP 未启动 | `try-catch` + 异步不阻塞 |
| 画线坐标与实际K线价格偏差 | 使用 `weak_magnet` 模式自动吸附 |
| 手绘线随 chart 刷新消失 | `restoreDrawSignals()` 在 symbol 切换时恢复 |
| 大量画线导致图表混乱 | 用 `groupId` 分组 + 批量管理按钮 |
