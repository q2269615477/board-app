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
        # 局部直连 Session：仅本请求生效，不改全局 requests 默认行为
        session = requests.Session()
        session.trust_env = False
        session.proxies = {}
        try:
            r = session.get(
                'https://push2.eastmoney.com/api/qt/clist/get',
                params={'pn': 1, 'pz': 200, 'np': 1, 'fltt': 2, 'invt': 2,
                        'fid': 'f3', 'fs': 'm:1+s:2,m:0+t:5',
                        'fields': 'f12,f14,f2,f3'},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=10
            )
        finally:
            session.close()
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


@bp.route('/api/snapshot/refresh', methods=['POST'])
@write_protected
def refresh_board_snapshot_route():
    """强制刷新盘中板块快照，并立即同步左侧涨跌幅缓存。"""
    from services.board_snapshot import get_snapshot_cache
    from services.board_spot_cache import get_board_spot_cache

    snapshot = get_snapshot_cache()
    snapshot_ready = bool(snapshot.ensure_snapshot(force=True))
    stats = snapshot.stats()

    spot_cache = get_board_spot_cache()
    spot_cache.invalidate_all()
    # 盘中成功捕获后直接复用完整快照，避免行业/概念各触发一次网络抓取；
    # 盘后或快照为空时才让 spot cache 执行其结算数据降级链。
    changes = get_app_context().refresh_board_changes(force=not snapshot_ready)
    ok = bool(changes)
    return jsonify({
        'ok': ok,
        'snapshot_ready': snapshot_ready,
        'snapshot': stats,
        'board_changes_count': len(changes),
        'message': '板块快照已刷新' if ok else '板块快照暂不可用',
    }), 200 if ok else 503


@bp.route('/api/spot/<data_type>/<code>')
def get_spot_route(data_type, code):
    """鑾峰彇瀹炴椂琛屾儏"""
    from data_loader import (
        get_global_index_spot,
        get_spot_board,
        get_spot_index,
        get_spot_stock,
    )

    def _qmt_http_spot():
        """Return domestic stock/index spot from the fast QMT HTTP endpoint."""
        if data_type not in ('stock', 'index'):
            return {}
        if data_type == 'index' and (code == '800000' or code.startswith('BK')):
            return {}
        from services.nav_spot_service import _a_share_nav_phase
        if _a_share_nav_phase() not in ('live_morning', 'live_afternoon'):
            return {}
        try:
            from data.qmt_http_client import get_qmt_http_client

            payload = get_qmt_http_client().ohlc_batch(
                [code], timeout=2.0
            )
            items = payload.get('items') or {}
            row = items.get(code)
            if row is None and items:
                row = next(iter(items.values()))
            if not isinstance(row, dict):
                return {}
            price = row.get('price')
            if price is None:
                price = row.get('close')
            try:
                if float(price or 0) <= 0:
                    return {}
            except (TypeError, ValueError):
                return {}
            result = dict(row)
            result['price'] = price
            result['close'] = row.get('close', price)
            result['channel'] = result.get('channel') or 'qmt18080'
            return result
        except Exception:
            return {}

    try:
        if data_type in ('industry', 'concept'):
            data = get_spot_board(data_type, code)
        elif data_type == 'index':
            data = _qmt_http_spot() or get_spot_index(code)
        elif data_type == 'stock':
            data = _qmt_http_spot() or get_spot_stock(code)
        elif data_type in ('hk_index', 'us', 'global'):
            # 海外标的不属于 QMT 18080 的国内代码范围。
            data = get_global_index_spot(code)
        else:
            return jsonify({'error': 'invalid type'}), 400
        return jsonify({'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/spot/indices')
def spot_indices_route():
    """顶部导航栏批量行情：按 tickers 动态并发补齐。"""
    from concurrent.futures import ThreadPoolExecutor, wait
    from datetime import datetime, timezone
    import threading

    from data_loader import (
        fetch_a_share_index_spots, get_local_spot, get_spot_index,
        get_spot_board, get_spot_stock,
        get_global_index_spot,
    )
    from data.global_index_kline import is_inactive_a_share_index
    from services.nav_spot_service import (
        fetch_nav_spots_fast, get_nav_targets,
    )
    from services.market_session import market_state

    raw_tickers = request.args.get('tickers', '')
    dynamic_codes = [c.strip() for c in raw_tickers.split(',') if c.strip()]
    nav_targets = get_nav_targets()
    default_codes = [c for c, _name, _typ in nav_targets]
    all_codes = list(dict.fromkeys(dynamic_codes or default_codes))
    target_type = {c: typ for c, _name, typ in nav_targets}
    route_now = datetime.now(timezone.utc)
    market_states = {
        code: market_state(code, now=route_now, data_type=target_type.get(code))
        for code in all_codes
    }
    global_codes = {'HSI', 'HSTECH', '^N225', '^KS11', '^TWII', 'SPX', 'IXIC', 'DJI'}
    inactive_codes = {
        code for code in all_codes if is_inactive_a_share_index(code)
    }
    a_share_index_codes = [
        code for code in all_codes
        if (code not in inactive_codes and code != '800000'
                and str(code).lower().startswith(('sh', 'sz', 'bj')))
    ]
    active_a_share_index_codes = [
        code for code in a_share_index_codes
        if market_states[code]['market_open']
    ]

    result = {
        code: {
            'code': code,
            'unavailable': True,
            'channel': 'unavailable',
            'reason': 'deprecated_no_remote',
            'market': market_states[code]['market'],
            'market_phase': market_states[code]['market_phase'],
            'market_open': False,
        }
        for code in inactive_codes
    }
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
        state = market_states.get(code) or market_state(
            code, now=route_now, data_type=target_type.get(code)
        )
        return {
            'code': code,
            'price': price,
            'close': price,
            'changePct': chg,
            'change_pct': chg,
            'channel': data.get('channel') or 'http',
            'market': state['market'],
            'market_phase': state['market_phase'],
            'market_open': state['market_open'],
        }

    def _fetch_any_spot(code):
        try:
            if code in inactive_codes or code in a_share_index_codes:
                return code, None
            state = market_states.get(code) or market_state(
                code, now=route_now, data_type=target_type.get(code)
            )
            if not state['market_open']:
                return code, None
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
        nav = fetch_nav_spots_fast(force=request.args.get('force') == '1')
        for code, row in (nav.get('data') or {}).items():
            if code in all_codes and code not in inactive_codes:
                packed = _pack(code, row)
                if packed:
                    result[code] = packed
    except Exception:
        pass

    # One batch request (or a small number of chunks) replaces the old
    # per-index worker fan-out. It also prevents a slow/stale local fallback
    # from turning a six-request quote refresh into a 30-second wait.
    if active_a_share_index_codes:
        try:
            batch = fetch_a_share_index_spots(active_a_share_index_codes)
            for code, data in batch.items():
                packed = _pack(code, data)
                if packed:
                    result[code] = packed
        except Exception:
            pass

    # Closed markets can only use the process cache above or a persisted local
    # close. They must not fan out to external quote sources.
    closed_missing_codes = [
        code for code in all_codes
        if code not in result and not market_states[code]['market_open']
    ]
    for code in closed_missing_codes:
        packed = _pack(code, get_local_spot(code))
        if packed:
            result[code] = packed

    # A failed live A-share batch is deliberately not replaced with stale
    # SQLite. Other live markets retain their existing per-symbol sources.
    missing_codes = [
        code for code in all_codes
        if (
            code not in result
            and code not in a_share_index_codes
            and market_states[code]['market_open']
        )
    ]
    if missing_codes:
        max_workers = max(1, min(3, len(missing_codes)))
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='spot_idx')
        tasks = [executor.submit(_fetch_any_spot, code) for code in missing_codes]
        done, pending = wait(tasks, timeout=2.0)
        for future in done:
            try:
                code, data = future.result()
                if data:
                    with result_lock:
                        result[code] = data
            except Exception:
                pass
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    active_markets = sorted({
        state['market'] for state in market_states.values() if state['market_open']
    })
    return jsonify({
        'data': result,
        'meta': {
            'requested': len(all_codes),
            'count': len(result),
            'active_markets': active_markets,
        },
    })
