"""
api/kline_routes.py — K线相关路由
路径: /api/kline/*, /api/stock/data/*
"""
import datetime
import math

from flask import Blueprint, request, jsonify

from services.kline_service import get_kline_service, _format_kline_helper
from data.sqlite_repo import get_sqlite_repo

bp = Blueprint('kline', __name__, url_prefix='')


def _normalise_kline_observability(result, status=None):
    """Ensure every K-line route response exposes the metadata contract.

    The service owns the authoritative values.  The route still fills safe
    defaults because responses can come from older service implementations or
    be supplied by a test/integration adapter; this keeps the JSON wire
    contract stable for every status code.
    """
    if not isinstance(result, dict):
        result = {'data': [], 'count': 0, 'error': str(result or 'K线响应无效')}
    else:
        result = dict(result)

    if not result.get('source'):
        if status is not None and status >= 400:
            result['source'] = 'error'
        elif status == 202 or result.get('loading'):
            result['source'] = 'pending'
        elif result.get('cached'):
            result['source'] = 'cache'
        else:
            result['source'] = 'load'

    result['stale'] = bool(
        result.get('stale', False) or result.get('source') == 'cache_stale'
    )
    result['background_refresh_started'] = bool(
        result.get('background_refresh_started', False)
    )

    try:
        load_ms = float(result.get('load_ms', 0))
        result['load_ms'] = max(0, int(load_ms)) if math.isfinite(load_ms) else 0
    except (TypeError, ValueError, OverflowError):
        result['load_ms'] = 0

    fallback_chain = result.get('fallback_chain', [])
    if fallback_chain is None:
        fallback_chain = []
    elif isinstance(fallback_chain, list):
        fallback_chain = list(fallback_chain)
    elif isinstance(fallback_chain, (tuple, set, frozenset)):
        fallback_chain = list(fallback_chain)
    else:
        fallback_chain = [fallback_chain]
    result['fallback_chain'] = fallback_chain
    return result


def _invalid_timeout_response(timeout_value):
    """Return a JSON-safe 400 response for malformed timeout parameters."""
    return _normalise_kline_observability({
        'error': f'非法 timeout 参数: {timeout_value}',
        'timeout': False,
        'data': [],
        'count': 0,
        'source': 'invalid_request',
    }, status=400)


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
    try:
        timeout = float(timeout_str)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a positive finite number')
    except (TypeError, ValueError, OverflowError):
        result = _invalid_timeout_response(timeout_str)
        return jsonify(result), 400

    try:
        service = get_kline_service()
        result, status = service.get_kline(
            data_type, code, period, board_name=board_name,
            force=force, timeout=timeout, cache_first=cache_first
        )
    except Exception as exc:
        result, status = {
            'error': str(exc),
            'timeout': False,
            'data': [],
            'count': 0,
            'source': 'error',
        }, 500

    if status == 200:
        result = _window_kline_result(
            result,
            from_value=request.args.get('from'),
            to_value=request.args.get('to'),
            limit_value=request.args.get('limit'),
        )

    # 透传业务字段，同时保证状态码无关的观测元数据契约。
    return jsonify(_normalise_kline_observability(result, status=status)), status


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
