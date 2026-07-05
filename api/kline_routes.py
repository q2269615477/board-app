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
    service = get_kline_service()
    board_name = request.args.get('name', '')
    period = request.args.get('period', 'daily')
    force = request.args.get('force', '').lower() == 'true'
    timeout = float(request.args.get('timeout', '15'))

    result, status = service.get_kline(data_type, code, period, board_name, force, timeout)

    response = {
        'data': result.get('data', []),
        'count': result.get('count', 0),
        'last_date': result.get('last_date', ''),
    }
    if 'today' in result:
        response['today'] = result['today']
    if 'range' in result and result['range']:
        response['range'] = result['range']

    return jsonify(response), status


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
