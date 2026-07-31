# TradingView MCP 调研与 AI 炒股面板优化设计

## 一、TradingView MCP 调研结论

### 1.1 TradingView MCP 现状

经 GitHub 搜索调研，**目前不存在官方开源的 TradingView MCP Server**。但存在以下相关生态：

| 项目 | 说明 | 对您的影响 |
|------|------|-----------|
| `tradingview/lightweight-charts` | TradingView 开源的轻量级图表库（KLineChart Pro 的竞品） | 无需更换，KLineChart Pro 已满足需求 |
| `tnick502/servers` | MCP Server 集合，无 TradingView 专用 | 需自建 MCP |
| `docker/mcp-servers` | Docker 官方 MCP 集合 | 无 TradingView 相关 |
| 腾讯云 MCP 广场 | 有股票数据 MCP，但非 TradingView | 可参考架构 |

### 1.2 TradingView MCP 付费服务

TradingView 官方提供 **REST API (Pro+ 订阅)**：
- 价格：$14.95-$59.95/月
- 功能：历史数据、实时报价、技术指标
- 限制：需要 API Key，有调用频率限制

**结论**：不建议直接使用 TradingView MCP，原因：
1. 付费成本
2. 您已集成 KLineChart Pro
3. QMT + Tushare 已覆盖 A 股数据需求

---

## 二、智能体控制图表的方案设计

### 2.1 核心目标

通过智能体聊天窗口控制 AI 炒股面板，实现：
1. **读取图表信息**：当前标的、周期、价格、成交量
2. **控制图表操作**：切换标的、切换周期、缩放、画线
3. **获取分析结果**：AI 分析信号、回测结果

### 2.2 技术架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        智能体 (CatPawAI)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ 自然语言指令  │→│ MCP Client   │→│ 调用 Board-App MCP   │   │
│  │ "查看茅台K线" │  │              │  │                      │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ HTTP/WebSocket
┌─────────────────────────────────────────────────────────────────┐
│                      Board-App MCP Server                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Tools:                                                  │   │
│  │  • get_panel_context()  → 获取当前标的、周期、价格        │   │
│  │  • set_symbol(code)     → 切换标的                      │   │
│  │  • set_period(p)        → 切换周期                      │   │
│  │  • get_kline_data()     → 获取K线数据                   │   │
│  │  • run_backtest()       → 执行回测                      │   │
│  │  • add_signal()         → 添加信号标记                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ SSE/WebSocket
┌─────────────────────────────────────────────────────────────────┐
│                      前端 (KLineChart Pro)                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • 接收指令更新图表                                       │   │
│  │  • 推送图表状态变化                                       │   │
│  │  • 显示AI信号标记                                         │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 已有基础

您当前的 Board-App 已实现：

| 功能 | 实现位置 | 状态 |
|------|---------|------|
| 上下文暴露 | `core/context_bridge.py` | ✅ 可用 |
| 上下文API | `api/ctx_route.py` | ✅ 可用 |
| 前端上下文 | `window.__board_ctx` | ✅ 可用 |
| SSE实时推送 | `index.html:1274-1348` | ✅ 可用 |

### 2.4 需要新增的功能

#### 2.4.1 MCP Server 扩展

新增 `mcp_chart_control.py`：

```python
# 智能体可调用的工具
TOOLS = {
    "get_chart_context": {
        "description": "获取当前图表上下文",
        "returns": {
            "code": "标的代码",
            "name": "标的名称",
            "type": "类型(index/stock/board)",
            "period": "周期(daily/weekly/1m/5m...)",
            "price": "当前价格",
            "change_pct": "涨跌幅",
            "volume": "成交量",
            "date_range": "显示日期范围"
        }
    },
    "set_symbol": {
        "description": "切换图表标的",
        "params": {"code": "代码", "type": "类型"}
    },
    "set_period": {
        "description": "切换周期",
        "params": {"period": "周期字符串"}
    },
    "get_kline": {
        "description": "获取K线数据",
        "params": {"code": "代码", "period": "周期", "count": "条数"}
    },
    "run_backtest": {
        "description": "执行策略回测",
        "params": {"strategy": "策略代码", "params": "参数"}
    }
}
```

#### 2.4.2 前端指令接收

扩展 `index.html` SSE 处理器：

```javascript
// 新增指令类型
_sse.addEventListener('chart_command', (e) => {
    const cmd = JSON.parse(e.data);
    switch(cmd.action) {
        case 'setSymbol':
            selectBoard(cmd.name, cmd.code, cmd.type, null);
            break;
        case 'setPeriod':
            pro.setPeriod(cmd.period);
            break;
        case 'zoomIn':
            pro.zoomIn();
            break;
        case 'addAnnotation':
            pro.addAnnotation(cmd.annotation);
            break;
    }
});
```

---

## 三、盘中 QMT 实时数据 + 盘后更新方案

### 3.1 当前架构分析

```
数据管线现状：
┌──────────┐     ┌──────────┐     ┌──────────┐
│  QMT RPC │────→│ SQLite   │────→│ 前端图表 │
│ (58600)  │     │ kline.db │     │          │
└──────────┘     └──────────┘     └──────────┘
     ↑
     │ 盘中实时
┌──────────┐
│ xtquant  │
└──────────┘
```

### 3.2 优化方案

#### 3.2.1 盘中实时数据流

```
盘中 (9:30-15:00):
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ QMT RPC  │────→│ 内存缓存  │────→│ 前端订阅  │────→│ KLineChart│
│ 58600    │     │ (5s TTL) │     │ SSE/WS   │     │ 实时更新  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
     │
     │ 异步写入
     ▼
┌──────────┐
│ SQLite   │ ← 延迟写入，不影响实时性
└──────────┘
```

**关键改进**：
1. **内存缓存层**：5秒 TTL，避免频繁查询 QMT
2. **SSE 推送**：价格变化时主动推送到前端
3. **异步持久化**：盘后统一写入 SQLite

#### 3.2.2 盘后数据更新

```
盘后 (15:00后):
┌──────────┐     ┌──────────┐     ┌──────────┐
│ QMT RPC  │────→│ 数据校验  │────→│ SQLite   │
│ 全量拉取  │     │ 去重/补全 │     │ 批量写入 │
└──────────┘     └──────────┘     └──────────┘
```

**自动化脚本**：
- `scripts/qmt_daily_update.py`：盘后自动拉取当日数据
- `scripts/verify_data_integrity.py`：数据完整性校验

#### 3.2.3 衔接逻辑

```python
# data_loader.py 优化

def get_spot_realtime(code: str) -> dict:
    """盘中实时行情"""
    # 1. 检查内存缓存
    if code in _realtime_cache:
        cache_time, data = _realtime_cache[code]
        if time.time() - cache_time < 5:  # 5秒内直接返回
            return data
    
    # 2. 查询 QMT
    data = _qmt_spot(code)
    
    # 3. 更新缓存
    _realtime_cache[code] = (time.time(), data)
    
    # 4. 异步标记待写入
    _pending_writes.add(code)
    
    return data

def batch_persist_after_close():
    """盘后批量持久化"""
    for code in _pending_writes:
        data = _realtime_cache.get(code)
        if data:
            _db_write_kline(code, 'daily', data)
```

---

## 四、回测引擎对比分析

### 4.1 候选引擎对比

| 引擎 | 性能 | 易用性 | 功能丰富度 | 维护状态 | 推荐指数 |
|------|------|--------|-----------|---------|---------|
| **vectorbt** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ 活跃 | **⭐⭐⭐⭐⭐** |
| **backtesting.py** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ 活跃 | **⭐⭐⭐⭐** |
| **backtrader** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⚠️ 停滞 | ⭐⭐⭐ |
| **zipline** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⚠️ 停滞 | ⭐⭐⭐ |
| **bt** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⚠️ 停滞 | ⭐⭐⭐ |

### 4.2 详细对比

#### 4.2.1 vectorbt (推荐首选)

```python
# 示例：双均线策略回测
import vectorbt as vbt
import pandas as pd

# 从您的 SQLite 读取数据
price = get_kline_from_sqlite('600519')['close']

# 计算指标
fast_ma = vbt.MA.run(price, 10)
slow_ma = vbt.MA.run(price, 50)

# 生成信号
entries = fast_ma.ma_crossed_above(slow_ma)
exits = fast_ma.ma_crossed_below(slow_ma)

# 回测
pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=100000)
print(pf.total_return())  # 总收益率
print(pf.sharpe_ratio())  # 夏普比率
```

**优势**：
- 性能最强：Numba 加速，比 backtrader 快 100x+
- 向量化运算：适合参数优化
- 功能丰富：内置 100+ 指标
- 可视化：内置图表输出

**劣势**：
- 学习曲线较陡
- 事件驱动场景不如 backtrader 灵活

#### 4.2.2 backtesting.py (推荐次选)

```python
# 示例：双均线策略回测
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

class SmaCross(Strategy):
    def init(self):
        self.sma1 = self.I(SMA, self.data.Close, 10)
        self.sma2 = self.I(SMA, self.data.Close, 50)
    
    def next(self):
        if crossover(self.sma1, self.sma2):
            self.buy()
        elif crossover(self.sma2, self.sma1):
            self.sell()

bt = Backtest(data, SmaCross, commission=.002)
stats = bt.run()
bt.plot()  # 生成交互式图表
```

**优势**：
- API 简洁直观
- 交互式图表输出
- 内置优化器
- 文档完善

**劣势**：
- 性能不如 vectorbt
- 大规模参数扫描较慢

#### 4.2.3 backtrader (不推荐)

**现状**：原开发者已停止维护，社区 fork 为 backtrader2

**劣势**：
- 性能较差
- 代码复杂
- 学习曲线陡峭

### 4.3 推荐方案

| 场景 | 推荐引擎 | 理由 |
|------|---------|------|
| **快速策略验证** | backtesting.py | 代码简洁，快速迭代 |
| **大规模参数优化** | vectorbt | 性能最强，向量化运算 |
| **复杂事件驱动** | backtrader2 | 事件机制完善 |
| **生产环境** | vectorbt | 性能稳定，维护活跃 |

### 4.4 集成建议

**推荐采用 vectorbt 作为主引擎**，原因：
1. 与您的 Python 3.13 环境兼容
2. 可直接读取 SQLite 数据
3. 性能满足实时分析需求
4. 支持 A 股数据格式

---

## 五、实施路线图

### 阶段一：智能体控制图表 (1-2 天)

```
Day 1:
├── 扩展 MCP Server 增加图表控制工具
├── 前端 SSE 指令接收器
└── 测试基本控制功能

Day 2:
├── 自然语言指令解析
├── 上下文联动优化
└── 文档编写
```

### 阶段二：实时数据优化 (2-3 天)

```
Day 3:
├── 内存缓存层实现
├── QMT 实时数据优化
└── SSE 推送优化

Day 4:
├── 盘后自动更新脚本
├── 数据校验机制
└── 异常处理

Day 5:
├── 测试盘中/盘后衔接
├── 性能压测
└── 文档编写
```

### 阶段三：回测引擎集成 (3-4 天)

```
Day 6:
├── vectorbt 安装测试
├── 数据适配器开发
└── 基础策略回测

Day 7:
├── 策略库设计
├── 回测结果可视化
└── MCP 工具集成

Day 8:
├── 参数优化功能
├── 回测报告生成
└── 文档编写

Day 9:
├── 集成测试
├── 性能优化
└── 部署上线
```

---

## 六、下一步行动

请确认以下问题，以便进入实施阶段：

1. **智能体控制图表**：是否需要支持语音指令？
2. **实时数据**：盘中刷新频率期望是多少（当前 30 秒）？
3. **回测引擎**：确认使用 vectorbt 还是 backtesting.py？
4. **策略来源**：您是否有现成的策略需要迁移？
5. **优先级**：三个阶段的实施顺序是否有调整？

确认后，我将按照 **Superpowers 工作流** 进入实施计划阶段。
