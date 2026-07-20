"""
api/board_routes.py — 板块相关路由（已切换至 Tushare）
"""
import time
from flask import Blueprint, request, jsonify

from services.board_service import get_board_service
from core.lifecycle import get_app_context
from data.board_api import (
    get_industry_boards, get_concept_boards,
    get_industry_constituents, get_concept_constituents,
)

bp = Blueprint('board', __name__, url_prefix='')


@bp.route('/api/boards/<board_type>')
def get_boards_route(board_type):
    """获取板块列表"""
    service = get_board_service()
    if board_type == 'industry':
        return jsonify({'data': service.get_industry_boards() or []})
    elif board_type == 'concept':
        return jsonify({'data': service.get_concept_boards() or []})
    elif board_type == 'index':
        # 获取指数列表（东财HTTP API）
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
        records = [{'代码': item['f12'], '名称': item['f14'],
                    '最新价': item.get('f2'), '涨跌幅': item.get('f3', 0)}
                   for item in data if item.get('f12')]
        return jsonify({'data': records})
    return jsonify({'error': 'invalid type'}), 400


@bp.route('/api/board-cons/<board_type>/<code>')
def get_board_cons_route(board_type, code):
    """板块成分股（统一走sorted路径，含SQLite涨跌幅补充）"""
    service = get_board_service()
    return jsonify({'data': service.get_constituents_sorted(board_type, code)})


@bp.route('/api/board-cons-sorted/<board_type>/<code>')
def get_board_cons_sorted_route(board_type, code):
    """板块成分股（按市值排序，?refresh=1 强制刷新）"""
    from flask import request
    force = request.args.get('refresh') == '1'
    service = get_board_service()
    return jsonify({'data': service.get_constituents_sorted(board_type, code, force_refresh=force)})


@bp.route('/api/cons/<board_type>/<code>')
def get_cons_route(board_type, code):
    """实时成分股（东财HTTP API）"""
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
    """获取所有板块最新涨跌幅"""
    service = get_board_service()
    return jsonify({'data': service.get_board_changes()})


@bp.route('/api/spot/<data_type>/<code>')
def get_spot_route(data_type, code):
    """获取实时行情"""
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
    """导航栏批量指数行情（并行获取，避免串行超时）"""
    from data_loader import get_spot_index, get_spot_board, get_spot_stock, get_global_index_spot
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    
    result = {}
    result_lock = threading.Lock()
    
    def _fetch_index(code, typ):
        try:
            d = get_spot_index(code) if typ == 'index' else get_spot_board('concept', code)
            if d and d.get('price', 0) > 0:
                return code, {'price': d.get('price', 0), 'change_pct': d.get('change_pct', 0)}
        except Exception:
            pass
        return code, None
    
    def _fetch_global(code):
        try:
            d = get_global_index_spot(code)
            if d and d.get('price', 0) > 0:
                return code, {'price': d.get('price', 0), 'change_pct': d.get('change_pct', 0)}
        except Exception:
            pass
        return code, None
    
    # 并行获取所有指数
    tasks = []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix='spot_idx') as executor:
        # A股指数
        for code, typ in [
            ('sh000001','index'),('sz399006','index'),('sh000688','index'),
            ('sh000300','index'),('sh000016','index'),('sh000852','index'),
            ('sh000853','index'),('BK1158','concept'),('800000','index'),
        ]:
            tasks.append(executor.submit(_fetch_index, code, typ))
        
        # 港股/亚太/美股指数
        for code in ['HSI', 'HSTECH', '^N225', '^KS11', '^TWII', 'SPX', 'IXIC', 'DJI']:
            tasks.append(executor.submit(_fetch_global, code))
        
        for future in as_completed(tasks, timeout=20):
            try:
                code, data = future.result(timeout=15)
                if data:
                    with result_lock:
                        result[code] = data
            except Exception:
                pass  # 单个标的超时或失败不影响其他
    
    return jsonify({'data': result})
