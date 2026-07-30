#!/usr/bin/env node
/**
 * Board-App MCP Server
 * 
 * 标准MCP协议实现，通过stdio与CatPawAI/Claude Code通信
 * 内部通过HTTP调用board-app Flask后端
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const BOARD_APP_URL = process.env.BOARD_APP_URL || 'http://127.0.0.1:5000';

// ============================================================
// HTTP客户端
// ============================================================

async function fetchFromBoardApp(path, options = {}) {
    const url = `${BOARD_APP_URL}${path}`;
    const response = await fetch(url, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    });
    
    if (!response.ok) {
        const text = await response.text();
        throw new Error(`HTTP ${response.status}: ${text}`);
    }
    
    return response.json();
}

// ============================================================
// MCP Server配置
// ============================================================

const server = new McpServer(
    {
        name: 'board-app-mcp',
        version: '1.0.0',
        description: 'AI炒股面板MCP服务 - 支持A股板块分析、画线、回测'
    },
    {
        instructions: `
AI炒股面板 MCP 工具使用指南

## 核心功能
- 图表控制：切换标的、周期、滚动
- 画线管理：创建、获取、删除画线
- 数据分析：获取K线、运行回测
- 实时数据：价格查询

## 工具选择指南

### 获取当前状态
- get_panel_context → 获取当前标的、周期、价格

### 切换图表
- set_symbol → 切换股票/指数/板块（如 600519, sh000001, BK1499）
- set_period → 切换周期（1m/5m/15m/30m/60m/daily/weekly/monthly）

### 画线操作
- create_overlay → 创建水平线/趋势线/矩形/文字
- get_overlays → 获取当前所有画线
- remove_overlay → 删除指定画线

### 数据分析
- get_kline_data → 获取K线历史数据
- run_backtest → 运行策略回测（vectorbt引擎）
- get_cached_prices → 获取实时价格

### 图表导航
- scroll_to_timestamp → 滚动到指定时间

## 使用示例
1. "查看茅台K线" → set_symbol {symbol: "600519"}
2. "切换到15分钟线" → set_period {period: "15m"}
3. "在3000点画水平线" → create_overlay {type: "horizontalLine", points: [{timestamp: Date.now(), value: 3000}]}
4. "获取所有画线" → get_overlays {}
5. "运行双均线回测" → run_backtest {symbol: "600519", startDate: "2024-01-01", endDate: "2024-12-31", strategyCode: "sma_cross"}

## 注意事项
- 标的代码格式：股票(600519)、指数(sh000001)、板块(BK1499)
- 时间戳单位为毫秒
- 回测策略代码需预先定义
`
    }
);

// ============================================================
// 工具注册
// ============================================================

// 1. 获取面板上下文
server.tool('get_panel_context', 
    '获取当前面板上下文（标的、周期、价格）', 
    {}, 
    async () => {
        try {
            const data = await fetchFromBoardApp('/api/ctx');
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 2. 切换标的
server.tool('set_symbol', 
    '切换图表显示的标的（股票/指数/板块）', 
    {
        symbol: z.string().min(3).describe('标的代码，如600519或sh000001'),
        symbol_type: z.enum(['stock', 'index', 'board']).default('stock').describe('标的类型')
    }, 
    async ({ symbol, symbol_type }) => {
        try {
            const data = await fetchFromBoardApp('/api/ctx', {
                method: 'POST',
                body: JSON.stringify({ code: symbol, type: symbol_type })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: true, symbol, type: symbol_type, message: `已切换到: ${symbol}` })
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 3. 切换周期
server.tool('set_period', 
    '切换K线周期', 
    {
        period: z.enum(['1m', '5m', '15m', '30m', '60m', 'daily', 'weekly', 'monthly'])
            .describe('周期类型')
    }, 
    async ({ period }) => {
        try {
            // 通过MCP API调用
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({ tool: 'set_period', params: { period } })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 4. 创建画线
server.tool('create_overlay', 
    '在图表上创建画线（水平线/趋势线/矩形/文字）', 
    {
        type: z.enum(['horizontalLine', 'verticalLine', 'trendLine', 'rayLine', 'segmentLine', 'rect', 'fibonacci', 'text', 'icon'])
            .describe('画线类型'),
        points: z.array(z.object({
            timestamp: z.number().describe('时间戳（毫秒）'),
            value: z.number().describe('价格')
        })).min(1).describe('点位数组'),
        styles: z.object({}).optional().describe('样式配置'),
        extendData: z.object({}).optional().describe('扩展数据')
    }, 
    async ({ type, points, styles, extendData }) => {
        try {
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({
                    tool: 'create_overlay',
                    params: { type, points, styles, extendData }
                })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 5. 获取画线
server.tool('get_overlays', 
    '获取图表上所有画线', 
    {
        symbol: z.string().optional().describe('标的代码（可选）')
    }, 
    async ({ symbol }) => {
        try {
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({
                    tool: 'get_overlays',
                    params: symbol ? { symbol } : {}
                })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 6. 删除画线
server.tool('remove_overlay', 
    '删除指定画线', 
    {
        overlay_id: z.string().describe('画线ID'),
        symbol: z.string().optional().describe('标的代码（可选）')
    }, 
    async ({ overlay_id, symbol }) => {
        try {
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({
                    tool: 'remove_overlay',
                    params: { overlayId: overlay_id, symbol }
                })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 7. 滚动到时间
server.tool('scroll_to_timestamp', 
    '滚动图表到指定时间', 
    {
        timestamp: z.number().describe('目标时间戳（毫秒）')
    }, 
    async ({ timestamp }) => {
        try {
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({
                    tool: 'scroll_to_timestamp',
                    params: { timestamp }
                })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 8. 获取K线数据
server.tool('get_kline_data', 
    '获取K线历史数据', 
    {
        symbol: z.string().min(3).describe('标的代码'),
        period: z.string().default('daily').describe('周期'),
        start_date: z.string().optional().describe('开始日期（YYYY-MM-DD）'),
        end_date: z.string().optional().describe('结束日期（YYYY-MM-DD）'),
        count: z.number().min(1).max(5000).optional().describe('获取条数')
    }, 
    async ({ symbol, period, start_date, end_date, count }) => {
        try {
            const params = { symbol, period };
            if (start_date) params.startDate = start_date;
            if (end_date) params.endDate = end_date;
            if (count) params.count = count;
            
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({ tool: 'get_kline_data', params })
            });
            
            // 简化输出，只显示摘要
            const summary = {
                success: data.success,
                symbol: data.symbol,
                period: data.period,
                count: data.count,
                message: data.message,
                sample: data.data ? data.data.slice(0, 3) : []
            };
            
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(summary, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 9. 运行回测
server.tool('run_backtest', 
    '运行策略回测', 
    {
        symbol: z.string().min(3).describe('标的代码'),
        start_date: z.string().describe('开始日期（YYYY-MM-DD）'),
        end_date: z.string().describe('结束日期（YYYY-MM-DD）'),
        strategy_code: z.string().describe('策略代码'),
        params: z.object({}).optional().describe('策略参数'),
        period: z.string().default('daily').describe('数据周期'),
        initial_capital: z.number().default(100000).describe('初始资金')
    }, 
    async ({ symbol, start_date, end_date, strategy_code, params, period, initial_capital }) => {
        try {
            const body = {
                tool: 'run_backtest',
                params: {
                    symbol,
                    startDate: start_date,
                    endDate: end_date,
                    strategyCode: strategy_code,
                    params: params || {},
                    period,
                    initialCapital: initial_capital
                }
            };
            
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify(body)
            });
            
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// 10. 获取缓存价格
server.tool('get_cached_prices', 
    '获取缓存的实时价格', 
    {
        codes: z.array(z.string()).min(1).max(100).describe('股票代码列表')
    }, 
    async ({ codes }) => {
        try {
            const data = await fetchFromBoardApp('/api/mcp/call', {
                method: 'POST',
                body: JSON.stringify({ tool: 'get_cached_prices', params: { codes } })
            });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify(data, null, 2)
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ success: false, error: error.message })
                }],
                isError: true
            };
        }
    }
);

// ============================================================
// 启动服务
// ============================================================

// 错误日志输出到stderr（不影响stdio协议）
console.error(`Board-App MCP Server v1.0.0`);
console.error(`Backend: ${BOARD_APP_URL}`);
console.error(`Starting...`);

// 启动stdio传输
const transport = new StdioServerTransport();
await server.connect(transport);

console.error('Board-App MCP Server started successfully');
