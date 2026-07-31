"""
流式数据API路由
提供JSONL格式的K线数据流
"""

import json
import logging
from flask import Blueprint, request, Response, stream_with_context
from services.kline_service import get_kline_service

logger = logging.getLogger('stream_api')

# 创建蓝图
stream_api = Blueprint('stream', __name__)


def jsonl_stream(data_generator):
    """
    将数据生成器转换为JSONL流
    
    Args:
        data_generator: 生成字典的生成器
        
    Yields:
        JSONL格式的字符串行
    """
    for item in data_generator:
        yield json.dumps(item, ensure_ascii=False) + '\n'


@stream_api.route('/api/stream/kline/<symbol>', methods=['GET'])
def stream_kline(symbol):
    """
    流式获取K线数据（JSONL格式）
    
    Query参数:
        - type: 数据类型 (stock/index/industry/concept/hk)
        - period: 周期 (1m/5m/15m/30m/60m/daily/weekly/monthly)
        - start: 开始日期 (YYYY-MM-DD)
        - end: 结束日期 (YYYY-MM-DD)
        - count: 限制条数
        - format: 输出格式 (jsonl/csv)
    """
    try:
        # 获取参数
        dtype = request.args.get('type', 'stock')
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start')
        end_date = request.args.get('end')
        count = request.args.get('count', type=int)
        output_format = request.args.get('format', 'jsonl')
        
        # 推断类型
        if not dtype:
            if symbol.startswith('sh') or symbol.startswith('sz'):
                dtype = 'index'
            elif symbol.startswith('BK'):
                dtype = 'industry'
            elif symbol.startswith('HK'):
                dtype = 'hk'
            else:
                dtype = 'stock'
        
        # 获取K线服务
        ks = get_kline_service()
        result, _ = ks.get_kline(dtype, symbol, period)
        data = result.get('data', [])
        
        # 过滤日期范围
        if start_date or end_date:
            from datetime import datetime
            filtered = []
            for bar in data:
                ts = bar.get('timestamp', 0)
                date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                if start_date and date_str < start_date:
                    continue
                if end_date and date_str > end_date:
                    continue
                filtered.append(bar)
            data = filtered
        
        # 限制条数
        if count and count > 0:
            data = data[-count:]
        
        logger.info(f"[Stream] K线流: {symbol}/{period}, {len(data)}条")
        
        # 根据格式返回
        if output_format == 'csv':
            return _stream_csv(data, symbol, period)
        else:
            return _stream_jsonl(data, symbol, period)
            
    except Exception as e:
        logger.error(f"[Stream] K线流失败: {e}")
        return Response(
            json.dumps({'error': str(e)}) + '\n',
            mimetype='application/jsonl',
            status=500
        )


def _stream_jsonl(data, symbol, period):
    """生成JSONL流"""
    def generate():
        # 发送元数据
        yield json.dumps({
            'type': 'metadata',
            'symbol': symbol,
            'period': period,
            'count': len(data)
        }) + '\n'
        
        # 发送数据
        for i, bar in enumerate(data):
            yield json.dumps({
                'type': 'data',
                'index': i,
                'timestamp': bar.get('timestamp'),
                'open': bar.get('open'),
                'high': bar.get('high'),
                'low': bar.get('low'),
                'close': bar.get('close'),
                'volume': bar.get('volume')
            }) + '\n'
        
        # 发送结束标记
        yield json.dumps({'type': 'end'}) + '\n'
    
    return Response(
        stream_with_context(generate()),
        mimetype='application/jsonl',
        headers={
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'no-cache'
        }
    )


def _stream_csv(data, symbol, period):
    """生成CSV流"""
    def generate():
        # 发送表头
        yield 'timestamp,date,open,high,low,close,volume\n'
        
        from datetime import datetime
        for bar in data:
            ts = bar.get('timestamp', 0)
            date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
            yield f"{ts},{date_str},{bar.get('open')},{bar.get('high')},{bar.get('low')},{bar.get('close')},{bar.get('volume')}\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={symbol}_{period}.csv',
            'Cache-Control': 'no-cache'
        }
    )


@stream_api.route('/api/stream/batch', methods=['POST'])
def stream_batch():
    """
    批量流式获取多个标的K线
    
    Request Body:
        {
            "symbols": ["600519", "000001"],
            "type": "stock",
            "period": "daily",
            "count": 100
        }
    """
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        dtype = data.get('type', 'stock')
        period = data.get('period', 'daily')
        count = data.get('count', 100)
        
        if not symbols:
            return Response(
                json.dumps({'error': '缺少symbols参数'}) + '\n',
                mimetype='application/jsonl',
                status=400
            )
        
        ks = get_kline_service()
        
        def generate():
            yield json.dumps({
                'type': 'metadata',
                'symbols': symbols,
                'period': period,
                'total': len(symbols)
            }) + '\n'
            
            for symbol in symbols:
                try:
                    result, _ = ks.get_kline(dtype, symbol, period)
                    klines = result.get('data', [])
                    if count:
                        klines = klines[-count:]
                    
                    yield json.dumps({
                        'type': 'symbol',
                        'symbol': symbol,
                        'count': len(klines),
                        'data': klines
                    }) + '\n'
                    
                except Exception as e:
                    yield json.dumps({
                        'type': 'error',
                        'symbol': symbol,
                        'error': str(e)
                    }) + '\n'
            
            yield json.dumps({'type': 'end'}) + '\n'
        
        return Response(
            stream_with_context(generate()),
            mimetype='application/jsonl',
            headers={'Cache-Control': 'no-cache'}
        )
        
    except Exception as e:
        logger.error(f"[Stream] 批量流失败: {e}")
        return Response(
            json.dumps({'error': str(e)}) + '\n',
            mimetype='application/jsonl',
            status=500
        )


@stream_api.route('/api/stream/health', methods=['GET'])
def stream_health():
    """流式服务健康检查"""
    return json.dumps({
        'status': 'ok',
        'service': 'stream_api',
        'features': ['jsonl', 'csv', 'batch']
    })
