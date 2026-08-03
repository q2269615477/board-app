"""
api/kline_routes.py — K线相关路由
路径: /api/kline/*, /api/stock/data/*
"""
import datetime

from flask import Blueprint, request, jsonify

from services.kline_service import get_kline_service, _format_kline_helper
from data.sqlite_repo import get_sqlite_repo

bp = Blueprint('kline', __name__, url_prefix='')


def _parse_timestamp_ms(value):
    """Parse seconds or milliseconds from a query parameter."""
    if value in (None, ''):
        return None
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return timestamp * 1000 if timestamp < 10_000_000_000 else timestamp


def _window_kline_result(result, from_value=None, to_value=None, limit_value=None):
    """Return only the chart-requested slice without mutating service caches."""
    if not isinstance(result, dict) or not isinstance(result.get('data'), list):
        return result

    from_ms = _parse_timestamp_ms(from_value)
    to_ms = _parse_timestamp_ms(to_value)
    try:
        limit = int(limit_value) if limit_value not in (None, '') else None
    except (TypeError, ValueError):
        limit = None
    if limit is not None:
        limit = max(1, min(limit, 2000))

    if from_ms is None and to_ms is None and limit is None:
        return result

    source_rows = result['data']
    rows = source_rows
    if from_ms is not None or to_ms is not None:
        low = from_ms if from_ms is not None else float('-inf')
        high = to_ms if to_ms is not None else float('inf')
        if low > high:
            low, high = high, low
        rows = [
            row for row in rows
            if isinstance(row, dict)
            and isinstance(row.get('timestamp'), (int, float))
            and low <= row['timestamp'] <= high
        ]
    if limit is not None and len(rows) > limit:
        rows = rows[-limit:]

    windowed = dict(result)
    windowed['data'] = rows
    windowed['total_count'] = len(source_rows)
    windowed['count'] = len(rows)
    windowed['windowed'] = True
    windowed['range'] = (
        f'{rows[0]["timestamp"]}~{rows[-1]["timestamp"]}' if rows else ''
    )
    windowed['last_date'] = (
        datetime.datetime.fromtimestamp(
            rows[-1]['timestamp'] / 1000, datetime.timezone.utc
        ).strftime('%Y-%m-%d') if rows else ''
    )

    # A current intraday overlay must never be painted onto an older window.
    if rows and source_rows and rows[-1].get('timestamp') != source_rows[-1].get('timestamp'):
        windowed.pop('intraday', None)
    if not rows:
        windowed.pop('intraday', None)
    return windowed


@bp.route('/api/kline/<data_type>/<code>')
def get_kline_route(data_type, code):
    """统一K线接口"""
    from core.config import KLINE_SYNC_TIMEOUT

    service = get_kline_service()
    board_name = request.args.get('name', '')
    period = request.args.get('period', 'daily')
    force = request.args.get('force', '').lower() == 'true'

    # cache_first: 保留历史别名，同时接通前端实际使用的 cache_first=1。
    cache_first = (
        request.args.get('cache_first', '0') == '1'
        or
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

    if status == 200:
        result = _window_kline_result(
            result,
            from_value=request.args.get('from'),
            to_value=request.args.get('to'),
            limit_value=request.args.get('limit'),
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
