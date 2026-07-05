"""
api/system_routes.py — 系统管理路由
/cache/* /update/* /events /system/*
"""
from flask import Blueprint, request, jsonify, Response

from core.cache import get_cache
from core.config import DATA_DIR, STATIC_DIR
from core.lifecycle import get_app_context, PREWARM_TARGETS
from core.events import get_event_bus
from services.board_service import get_board_service
from services.kline_service import get_kline_service

bp = Blueprint('system', __name__, url_prefix='')


@bp.route('/api/cache/status')
def cache_status_route():
    return jsonify(get_cache().status())


@bp.route('/api/cache/health')
def cache_health_route():
    from data.sqlite_repo import get_sqlite_repo
    cache = get_cache()
    return jsonify({
        'cache': cache.health_check(),
        'uptime': get_app_context().uptime_seconds,
    })


@bp.route('/api/cache/clear', methods=['POST'])
def cache_clear_route():
    count = get_cache().clear()
    return jsonify({'ok': True, 'cleared': count})


@bp.route('/api/cache/clear/<data_type>/<code>', methods=['POST'])
def cache_clear_key_route(data_type, code):
    period = request.args.get('period', 'daily')
    key = f'{data_type}:{code}:{period}'
    get_cache().delete(key)
    return jsonify({'ok': True, 'deleted': key})


@bp.route('/api/system/health')
def system_health_route():
    from datetime import datetime
    cache = get_cache()
    ctx = get_app_context()
    result = {
        'status': 'ok',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'uptime_seconds': ctx.uptime_seconds,
        'qmt_available': ctx.qmt_available,
        'cache': cache.status(),
    }
    return jsonify(result)


@bp.route('/api/system/restart', methods=['POST'])
def system_restart_route():
    """清缓存+重新预热（不重启进程）"""
    get_cache().clear()
    from data_loader import load_index_kline, load_hk_index_kline, load_board_kline
    from ..core.lifecycle import PREWARM_TARGETS
    # 预热关键数据
    for code, name, typ in PREWARM_TARGETS[:6]:
        try:
            loader = load_hk_index_kline if typ == 'hk_index' else load_index_kline
            loader(code)
        except Exception:
            pass  # 预热失败不影响主流程，后续请求会自动加载
    return jsonify({'ok': True, 'message': '缓存已清除，关键数据已预热'})


# ---- 数据更新管理 ----

@bp.route('/api/update/status')
def update_status_route():
    try:
        from data_update_manager import get_update_status
        return jsonify(get_update_status())
    except Exception:
        return jsonify({'status': 'unknown'})  # 更新状态未就绪


@bp.route('/api/update/boards', methods=['POST'])
def trigger_board_update_route():
    """手动触发全量板块更新"""
    try:
        from data_update_manager import update_all_today
        import threading
        def _run():
            update_all_today()
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'message': '板块更新任务已触发'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/update/force', methods=['POST'])
def trigger_force_update_route():
    """强制刷新所有数据"""
    try:
        from data_update_manager import update_all_today
        import threading
        import json
        from pathlib import Path

        get_cache().clear()

        def _run():
            # 重置今日标记
            sf = DATA_DIR / 'update_status.json'
            if sf.exists():
                with open(sf, 'r', encoding='utf-8') as f:
                    st = json.load(f)
                st['today'] = ''
                with open(sf, 'w', encoding='utf-8') as f:
                    json.dump(st, f, ensure_ascii=False, indent=2)
            update_all_today()

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'message': '全量强制更新已触发'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/update/stock/<code>', methods=['POST'])
def trigger_stock_update_route(code):
    """手动触发单只个股更新"""
    try:
        from data_update_manager import update_stock_if_needed
        ok = update_stock_if_needed(code, force=True)
        return jsonify({'ok': ok, 'code': code})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---- SSE 事件推送 ----

@bp.route('/api/events')
def sse_event_stream_route():
    """SSE endpoint：推送数据更新 + AI分析事件给前端"""
    event_bus = get_event_bus()

    def event_stream():
        last_idx = 0
        while True:
            # 内部事件（数据更新等）
            try:
                from data_update_manager import get_sse_events
                events, last_idx = get_sse_events(last_idx)
                for ev in events:
                    yield f"event: {ev['type']}\ndata: {ev['message']}\n\n"
            except Exception:
                pass  # SSE事件轮询中获取事件失败是正常情况

            # AI事件
            while True:
                evt = event_bus.get_sse_events(timeout=0.05)
                if evt is None:
                    break
                event_type, data = evt
                yield f"event: {event_type}\ndata: {data}\n\n"

            import time
            time.sleep(1)

    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )


@bp.route('/api/classification/save', methods=['POST'])
def save_classification_route():
    """保存用户重分类后的板块数据"""
    from pathlib import Path
    try:
        data = request.get_json()
        if not data or 'categories' not in data:
            return jsonify({'error': 'invalid data'}), 400
        save_file = STATIC_DIR / 'board_classification_saved.json'
        with open(save_file, 'w', encoding='utf-8') as f:
            import json
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/classification/load')
def load_classification_route():
    """加载用户保存的板块分类"""
    from pathlib import Path
    import json
    save_file = Path('static') / 'board_classification_saved.json'
    try:
        if save_file.exists():
            with open(save_file, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
    except Exception:
        pass  # 用户保存的分类不存在或损坏，回退到原始分类
    # 回退到原始分类
    orig = Path('static') / 'board_classification.json'
    with open(orig, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))
