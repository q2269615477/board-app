"""
MCP API 路由
提供智能体交互的HTTP端点
"""

import json
import logging
import time
from flask import Blueprint, request, jsonify, Response, stream_with_context

from core.config import Config
from mcp.tools import TOOLS, validate_tool_params, validate_and_convert
from mcp.handlers import MCPHandler
from mcp.sse import sse_manager
from services.qmt_cache_service import qmt_cache_service
from services.backtest_service import backtest_service
from services.vectorbt_backtest import vectorbt_service
from services.qmt_xtquant import qmt_service
from services.ai_integration import ai_service, ChartEvent
from services.overlay_service import get_overlay_service

logger = logging.getLogger('mcp_api')

# 创建蓝图
mcp_api = Blueprint('mcp', __name__)

# 全局处理器实例
mcp_handler = MCPHandler()


@mcp_api.route('/api/mcp/tools', methods=['GET'])
def get_tools():
    """【兼容别名】正式契约为 GET /mcp/tools。"""
    try:
        from api.signal_ai_routes import _merged_mcp_tools
        tools = _merged_mcp_tools()
    except Exception:
        tools = list(TOOLS.values()) if isinstance(TOOLS, dict) else TOOLS
    resp = jsonify({
        'success': True,
        'tools': tools,
        'deprecated': True,
        'prefer': '/mcp/tools',
    })
    resp.headers['Deprecation'] = 'true'
    resp.headers['Link'] = '</mcp/tools>; rel="successor-version"'
    return resp


@mcp_api.route('/api/mcp/call', methods=['POST'])
def call_tool():
    """【兼容别名】正式契约为 POST /mcp/call（params 与 arguments 均可）。"""
    try:
        data = request.get_json() or {}
        tool_name = data.get('tool')
        params = data.get('params') if data.get('params') is not None else data.get('arguments', {})
        if not isinstance(params, dict):
            params = {}
        session_id = data.get('session_id', 'default')

        # 全部转发到统一 /mcp/call 实现（面板工具 + handlers）
        from flask import current_app
        from api.signal_ai_routes import mcp_call_tool_route
        with current_app.test_request_context(
            '/mcp/call',
            method='POST',
            json={'tool': tool_name, 'arguments': params, 'session_id': session_id},
        ):
            resp = mcp_call_tool_route()
        # 附加弃用提示（若是 Response）
        try:
            resp.headers['Deprecation'] = 'true'
            resp.headers['Link'] = '</mcp/call>; rel="successor-version"'
        except Exception:
            pass
        return resp

    except Exception as e:
        logger.error(f"工具调用失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'prefer': '/mcp/call',
        }), 500


@mcp_api.route('/api/mcp/sse')
def sse_stream():
    """SSE事件流"""
    def event_stream():
        client = sse_manager.subscribe()
        logger.info(f"SSE客户端连接: {client.client_id}")
        
        try:
            # 发送连接成功事件
            yield f"data: {json.dumps({'type': 'connected', 'clientId': client.client_id})}\n\n"
            
            while True:
                event = client.get(timeout=30)
                if event:
                    yield f"data: {json.dumps(event)}\n\n"
                else:
                    # 发送心跳
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    
        except GeneratorExit:
            sse_manager.unsubscribe(client.client_id)
            logger.info(f"SSE客户端断开: {client.client_id}")
    
    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@mcp_api.route('/api/mcp/event', methods=['POST'])
def report_event():
    """接收前端事件上报"""
    try:
        data = request.get_json()
        event_type = data.get('type')
        event_data = data.get('data', {})
        
        # 记录事件
        logger.debug(f"收到事件: {event_type}, 数据: {event_data}")
        
        # 可以在这里处理事件，或转发给智能体
        # TODO: 与AI服务集成
        
        return jsonify({
            'success': True,
            'message': '事件已接收'
        })
        
    except Exception as e:
        logger.error(f"事件处理失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== QMT缓存API ====================

@mcp_api.route('/api/cache/prices', methods=['POST'])
def get_cached_prices():
    """获取缓存价格"""
    try:
        data = request.get_json()
        codes = data.get('codes', [])
        
        if not codes:
            return jsonify({
                'success': False,
                'error': '缺少codes参数'
            }), 400
        
        prices = qmt_cache_service.get_cached_prices(codes)
        
        return jsonify({
            'success': True,
            'prices': prices,
            'timestamp': int(time.time() * 1000)
        })
        
    except Exception as e:
        logger.error(f"获取缓存价格失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/cache/watch', methods=['POST'])
def watch_codes():
    """添加关注标的"""
    try:
        data = request.get_json()
        codes = data.get('codes', [])
        
        qmt_cache_service.watch_codes(codes)
        
        return jsonify({
            'success': True,
            'message': f'已关注 {len(codes)} 个标的',
            'watchedCount': len(qmt_cache_service._watched_codes)
        })
        
    except Exception as e:
        logger.error(f"添加关注失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/cache/unwatch', methods=['POST'])
def unwatch_codes():
    """移除关注标的"""
    try:
        data = request.get_json()
        codes = data.get('codes', [])
        
        qmt_cache_service.unwatch_codes(codes)
        
        return jsonify({
            'success': True,
            'message': f'已移除 {len(codes)} 个标的',
            'watchedCount': qmt_cache_service.get_watched_count()
        })
        
    except Exception as e:
        logger.error(f"移除关注失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== 回测API ====================

@mcp_api.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    """运行回测（不可用时 503 + BACKTEST_UNAVAILABLE）"""
    try:
        data = request.get_json() or {}
        
        symbol = data.get('symbol')
        start_date = data.get('startDate')
        end_date = data.get('endDate')
        strategy_code = data.get('strategyCode')
        params = data.get('params', {})
        
        # 参数验证
        if not all([symbol, start_date, end_date, strategy_code]):
            return jsonify({
                'success': False,
                'error': '缺少必需参数'
            }), 400
        
        # 执行回测
        result = backtest_service.run_backtest(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            strategy_code=strategy_code,
            params=params
        )
        status = 503 if result.get('code') == 'BACKTEST_UNAVAILABLE' else 200
        return jsonify(result), status
        
    except Exception as e:
        logger.error(f"回测执行失败: {e}")
        return jsonify({
            'success': False,
            'ok': False,
            'code': 'BACKTEST_UNAVAILABLE',
            'error': str(e)
        }), 503


@mcp_api.route('/api/backtest/strategies', methods=['GET'])
def get_strategies():
    """获取可用策略列表"""
    return jsonify({
        'success': True,
        'strategies': backtest_service.get_available_strategies()
    })


# ==================== vectorbt回测API ====================

@mcp_api.route('/api/vectorbt/backtest', methods=['POST'])
def run_vectorbt_backtest():
    """运行vectorbt回测（schema 未对齐时诚实 503）"""
    try:
        data = request.get_json() or {}
        
        symbol = data.get('symbol')
        start_date = data.get('startDate')
        end_date = data.get('endDate')
        strategy_code = data.get('strategyCode')
        params = data.get('params', {})
        period = data.get('period', 'daily')
        initial_capital = data.get('initialCapital', 100000.0)
        
        # 参数验证
        if not all([symbol, start_date, end_date, strategy_code]):
            return jsonify({
                'success': False,
                'error': '缺少必需参数'
            }), 400
        
        # 执行回测
        result = vectorbt_service.run_backtest(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            strategy_code=strategy_code,
            params=params,
            period=period,
            initial_capital=initial_capital
        )
        
        body = {
            'success': result.success,
            'ok': result.success,
            'signals': result.signals,
            'metrics': result.metrics,
            'equityCurve': result.equity_curve,
            'trades': result.trades,
            'error': result.error,
        }
        if not result.success and 'BACKTEST_UNAVAILABLE' in (result.error or ''):
            body['code'] = 'BACKTEST_UNAVAILABLE'
            return jsonify(body), 503
        return jsonify(body)
        
    except Exception as e:
        logger.error(f"vectorbt回测执行失败: {e}")
        return jsonify({
            'success': False,
            'ok': False,
            'code': 'BACKTEST_UNAVAILABLE',
            'error': str(e)
        }), 503


@mcp_api.route('/api/vectorbt/strategies', methods=['GET'])
def get_vectorbt_strategies():
    """获取vectorbt策略列表"""
    return jsonify({
        'success': True,
        'strategies': vectorbt_service.get_available_strategies()
    })


# ==================== QMT xtquant API ====================

@mcp_api.route('/api/qmt/connect', methods=['POST'])
def qmt_connect():
    """连接QMT"""
    try:
        success = qmt_service.connect()
        return jsonify({
            'success': success,
            'message': 'QMT连接成功' if success else 'QMT连接失败'
        })
    except Exception as e:
        logger.error(f"QMT连接失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/qmt/subscribe', methods=['POST'])
def qmt_subscribe():
    """订阅QMT行情"""
    try:
        data = request.get_json()
        codes = data.get('codes', [])
        
        success = qmt_service.subscribe(codes)
        return jsonify({
            'success': success,
            'message': f'已订阅 {len(codes)} 个标的'
        })
    except Exception as e:
        logger.error(f"QMT订阅失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/qmt/prices', methods=['POST'])
def qmt_get_prices():
    """获取QMT实时价格"""
    try:
        data = request.get_json()
        codes = data.get('codes', [])
        
        prices = qmt_service.get_full_tick(codes)
        
        return jsonify({
            'success': True,
            'prices': {
                code: {
                    'code': p.code,
                    'name': p.name,
                    'price': p.price,
                    'open': p.open,
                    'high': p.high,
                    'low': p.low,
                    'preClose': p.pre_close,
                    'volume': p.volume,
                    'amount': p.amount,
                    'changePct': p.change_pct,
                    'timestamp': p.timestamp
                }
                for code, p in prices.items()
            }
        })
    except Exception as e:
        logger.error(f"获取QMT价格失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/qmt/kline', methods=['POST'])
def qmt_get_kline():
    """获取QMT K线数据"""
    try:
        data = request.get_json()
        code = data.get('code')
        period = data.get('period', '1d')
        count = data.get('count', 100)
        
        klines = qmt_service.get_kline_data(code, period, count)
        
        return jsonify({
            'success': True,
            'klines': klines
        })
    except Exception as e:
        logger.error(f"获取QMT K线失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== AI分析API ====================

@mcp_api.route('/api/ai/analyze', methods=['POST'])
def ai_analyze_event():
    """AI分析图表事件"""
    try:
        data = request.get_json()
        
        event = ChartEvent(
            type=data.get('type'),
            timestamp=data.get('timestamp'),
            data=data.get('data', {}),
            context=data.get('context', {})
        )
        
        # 异步处理
        import asyncio
        result = asyncio.run(ai_service.process_event(event))
        
        return jsonify({
            'success': result.success,
            'action': result.action,
            'reasoning': result.reasoning,
            'confidence': result.confidence,
            'suggestions': result.suggestions,
            'error': result.error
        })
    except Exception as e:
        logger.error(f"AI分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/ai/analyze_context', methods=['POST'])
def ai_analyze_context():
    """AI分析图表上下文"""
    try:
        data = request.get_json()
        context = data.get('context', {})
        
        import asyncio
        result = asyncio.run(ai_service.analyze_chart_context(context))
        
        return jsonify({
            'success': result.success,
            'action': result.action,
            'reasoning': result.reasoning,
            'confidence': result.confidence,
            'suggestions': result.suggestions,
            'error': result.error
        })
    except Exception as e:
        logger.error(f"AI上下文分析失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/ai/history', methods=['GET'])
def ai_get_history():
    """获取AI分析历史"""
    try:
        limit = request.args.get('limit', 20, type=int)
        history = ai_service.get_analysis_history(limit)
        
        return jsonify({
            'success': True,
            'history': history
        })
    except Exception as e:
        logger.error(f"获取AI历史失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== 画线同步API ====================

@mcp_api.route('/api/mcp/overlays/sync', methods=['POST'])
def sync_overlays():
    """同步画线状态（前端上报）"""
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        overlays = data.get('overlays', [])
        
        if not symbol:
            return jsonify({
                'success': False,
                'error': '缺少symbol参数'
            }), 400
        
        service = get_overlay_service()
        result = service.sync_overlays(symbol, overlays)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"同步画线失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/mcp/overlays/<symbol>', methods=['GET'])
def get_overlays(symbol):
    """获取指定标的的画线"""
    try:
        service = get_overlay_service()
        overlays = service.get_overlays(symbol)
        
        return jsonify({
            'success': True,
            'symbol': symbol,
            'overlays': overlays,
            'count': len(overlays)
        })
        
    except Exception as e:
        logger.error(f"获取画线失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_api.route('/api/mcp/overlays/stats', methods=['GET'])
def get_overlay_stats():
    """获取画线统计信息"""
    try:
        service = get_overlay_service()
        stats = service.get_stats()
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        logger.error(f"获取画线统计失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
