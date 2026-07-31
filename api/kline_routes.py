"""
api/kline_routes.py — K线相关路由
路径: /api/kline/*, /api/stock/data/*
"""
from flask import Blueprint, request, jsonify

from services.kline_service import get_kline_service, _format_kline_helper
from data.sqlite_repo import get_sqlite_repo

bp = Blueprint('kline', __name__, url_prefix='')


@bp.route('/api/kline/<data_type>/<code>')
def get_kline_route(data_type, code):
    """统一K线接口"""
    from core.config import KLINE_SYNC_TIMEOUT

    service = get_kline_service()
    board_name = request.args.get('name', '')
    period = request.args.get('period', 'daily')
    force = request.args.get('force', '').lower() == 'true'

    # cache_first: prefer_cache / stale_ok / refresh_async 任一为 1 即启用
    cache_first = (
        request.args.get('prefer_cache', '0') == '1'
        or request.args.get('stale_ok', '0') == '1'
        or request.args.get('refresh_async', '0') == '1'
    )

    # timeout: 优先 query 参数，缺省用 KLINE_SYNC_TIMEOUT
    timeout_str = request.args.get('timeout', str(KLINE_SYNC_TIMEOUT))
    timeout = float(timeout_str)

    result, status = service.get_kline(
        data_type, code, period, board_name=board_name,
        force=force, timeout=timeout, cache_first=cache_first
    )

    # 透传所有字段
    return jsonify(result), status


@bp.route('/api/stock/data/<code>')
def get_stock_data_route(code):
    """个股日K线数据"""
    from data_loader import load_stock_data
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    period = request.args.get('period', 'daily')
    try:
        df = load_stock_data(code, start, end)
        data = _format_kline_helper(df)
        return jsonify({
            'data': data, 'count': len(data),
            'start': data[0]['timestamp'] if data else '',
            'end': data[-1]['timestamp'] if data else '',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
