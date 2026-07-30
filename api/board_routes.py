"""Board related routes."""
import time
from flask import Blueprint, request, jsonify

from services.board_service import get_board_service
from services.board_service import reload_constituents_json_cache, get_constituents_json_cache_status
from core.lifecycle import get_app_context
from api.auth_guard import write_protected
from data.board_api import (
    get_industry_boards, get_concept_boards,
    get_industry_constituents, get_concept_constituents,
)

bp = Blueprint('board', __name__, url_prefix='')


@bp.route('/api/boards/<board_type>')
def get_boards_route(board_type):
    """Get board list."""
    service = get_board_service()
    if board_type == 'industry':
        return jsonify({'data': service.get_industry_boards() or []})
    elif board_type == 'concept':
        return jsonify({'data': service.get_concept_boards() or []})
    elif board_type == 'index':
        import requests
        time.sleep(0.3)
        r = requests.get(
            'https://push2.eastmoney.com/api/qt/clist/get',
            params={'pn': 1, 'pz': 200, 'np': 1, 'fltt': 2, 'invt': 2,
                    'fid': 'f3', 'fs': 'm:1+s:2,m:0+t:5',
                    'fields': 'f12,f14,f2,f3'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10
        )
        data = r.json().get('data', {}).get('diff', [])
        records = [{'code': item['f12'], 'name': item['f14'],
                    'price': item.get('f2'), 'change_pct': item.get('f3', 0)}
                   for item in data if item.get('f12')]
        return jsonify({'data': records})
    return jsonify({'error': 'invalid type'}), 400


@bp.route('/api/board-cons/<board_type>/<code>')
def get_board_cons_route(board_type, code):
    """Get board constituents."""
    service = get_board_service()
    return jsonify({'data': service.get_constituents_sorted(board_type, code)})


@bp.route('/api/board-cons-sorted/<board_type>/<code>')
def get_board_cons_sorted_route(board_type, code):
    """Get sorted board constituents."""
    from flask import request
    force = request.args.get('refresh') == '1'
    service = get_board_service()
    return jsonify({'data': service.get_constituents_sorted(board_type, code, force_refresh=force)})


@bp.route('/api/constituents/cache/status')
def constituents_cache_status_route():
    return jsonify({'ok': True, 'data': get_constituents_json_cache_status()})


@bp.route('/api/constituents/cache/reload', methods=['POST'])
@write_protected
def constituents_cache_reload_route():
    data = reload_constituents_json_cache()
    get_board_service()._cache.clear()
    return jsonify({'ok': True, 'data': data})


@bp.route('/api/cons/<board_type>/<code>')
def get_cons_route(board_type, code):
    """Get realtime board constituents from HTTP API."""
    try:
        data = []
        if board_type == 'industry':
            data = get_industry_constituents(code)
        elif board_type == 'concept':
            data = get_concept_constituents(code)
        return jsonify({'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/board-changes')
def get_board_changes_route():
    """Get latest board changes."""
    service = get_board_service()
    return jsonify({'data': service.get_board_changes()})


@bp.route('/api/spot/<data_type>/<code>')
def get_spot_route(data_type, code):
    """鑾峰彇瀹炴椂琛屾儏"""
    from data_loader import get_spot_board, get_spot_index, get_spot_stock
    try:
        if data_type in ('industry', 'concept'):
            data = get_spot_board(data_type, code)
        elif data_type == 'index':
            data = get_spot_index(code)
        elif data_type == 'stock':
            data = get_spot_stock(code)
        else:
            return jsonify({'error': 'invalid type'}), 400
        return jsonify({'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/spot/indices')
def spot_indices_route():
    """顶部导航栏批量行情：按 tickers 动态并发补齐。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    from core.config import PREWARM_TARGETS
    from data_loader import get_spot_index, get_spot_board, get_spot_stock, get_global_index_spot

    raw_tickers = request.args.get('tickers', '')
    dynamic_codes = [c.strip() for c in raw_tickers.split(',') if c.strip()]
    default_codes = [c for c, _name, _typ in PREWARM_TARGETS] + [
        '^N225', '^KS11', '^TWII', 'SPX', 'IXIC', 'DJI'
    ]
    all_codes = list(dict.fromkeys(dynamic_codes or default_codes))
    target_type = {c: typ for c, _name, typ in PREWARM_TARGETS}
    global_codes = {'HSI', 'HSTECH', '^N225', '^KS11', '^TWII', 'SPX', 'IXIC', 'DJI'}

    result = {}
    result_lock = threading.Lock()

    def _to_float(value, default=0.0):
        try:
            if value is None or value == '':
                return default
            return float(value)
        except Exception:
            return default

    def _pack(code, data):
        if not data:
            return None
        price = data.get('price')
        if price is None:
            price = data.get('close')
        price = _to_float(price)
        if price <= 0:
            return None
        chg = data.get('changePct')
        if chg is None:
            chg = data.get('change_pct')
        chg = _to_float(chg)
        return {
            'code': code,
            'price': price,
            'close': price,
            'changePct': chg,
            'change_pct': chg,
            'channel': data.get('channel') or 'http',
        }

    def _fetch_any_spot(code):
        try:
            typ = target_type.get(code)
            data = None
            if code == 'BK1158':
                data = get_spot_index(code) or get_spot_board('concept', code)
            elif code in global_codes:
                data = get_global_index_spot(code)
            elif code.startswith('BK') or typ in ('concept', 'industry'):
                data = get_spot_board('industry' if typ == 'industry' else 'concept', code)
                if not data:
                    data = get_spot_index(code)
            elif code.startswith(('sh', 'sz', 'bj')) or typ == 'index':
                data = get_spot_index(code)
            elif typ == 'stock':
                data = get_spot_stock(code)
            else:
                data = get_global_index_spot(code) or get_spot_stock(code)

            if not data and code not in global_codes:
                data = get_global_index_spot(code)
            return code, _pack(code, data)
        except Exception:
            return code, None

    try:
        from services.nav_spot_service import fetch_nav_spots

        nav = fetch_nav_spots(force=request.args.get('force') == '1')
        for code, row in (nav.get('data') or {}).items():
            if code in all_codes:
                packed = _pack(code, row)
                if packed:
                    result[code] = packed
    except Exception:
        pass

    missing_codes = [code for code in all_codes if code not in result]
    if missing_codes:
        max_workers = max(1, min(12, len(missing_codes)))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='spot_idx') as executor:
            tasks = [executor.submit(_fetch_any_spot, code) for code in missing_codes]
            try:
                for future in as_completed(tasks, timeout=8):
                    try:
                        code, data = future.result(timeout=1)
                        if data:
                            with result_lock:
                                result[code] = data
                    except Exception:
                        pass
            except Exception:
                pass

    return jsonify({'data': result, 'meta': {'requested': len(all_codes), 'count': len(result)}})
