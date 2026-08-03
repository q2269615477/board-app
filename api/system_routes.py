"""
api/system_routes.py — 系统管理路由
/cache/* /update/* /events /system/*
"""
import threading
import time
import uuid

from flask import Blueprint, request, jsonify, Response

from core.cache import get_cache
from core.config import (DATA_DIR, STATIC_DIR, KLINE_SYNC_TIMEOUT,
                          KLINE_FETCH_TIMEOUT_MS, LOADING_MIN_MS, LOADING_MAX_MS,
                          INTRADAY_OHLC_TTL_SEC)
from core.lifecycle import get_app_context, PREWARM_TARGETS
from core.events import get_event_bus
from services.board_service import get_board_service
from services.kline_service import get_kline_service
from api.auth_guard import write_protected
from build_search_index import build_index_json
from services.search_service import get_search_service
from services.update_task_factories import (
    create_force_update_task,
    create_boards_update_task,
    create_stock_update_task,
)

bp = Blueprint('system', __name__, url_prefix='')

_sse_stream_lock = threading.Lock()
_sse_stream_token = None


def _claim_sse_stream(managed=False):
    """只保留一个事件流；新版受管客户端可以接管旧版连接。"""
    global _sse_stream_token
    with _sse_stream_lock:
        if _sse_stream_token is not None and not managed:
            return None
        token = uuid.uuid4().hex
        _sse_stream_token = token
        return token


def _owns_sse_stream(token):
    with _sse_stream_lock:
        return bool(token) and _sse_stream_token == token


def _release_sse_stream(token):
    global _sse_stream_token
    with _sse_stream_lock:
        if _sse_stream_token == token:
            _sse_stream_token = None


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
@write_protected
def cache_clear_route():
    count = get_cache().clear()
    return jsonify({'ok': True, 'cleared': count})


@bp.route('/api/cache/clear/<data_type>/<code>', methods=['POST'])
@write_protected
def cache_clear_key_route(data_type, code):
    period = request.args.get('period', 'daily')
    key = f'{data_type}:{code}:{period}'
    get_cache().delete(key)
    return jsonify({'ok': True, 'deleted': key})


@bp.route('/api/system/status')
def system_status_route():
    """获取系统完整状态（含QMT连接警告）"""
    ctx = get_app_context()
    status = ctx.get_system_status()
    return jsonify({
        'success': True,
        'data': status
    })


@bp.route('/api/system/qmt-warning')
def qmt_warning_route():
    """获取QMT连接警告"""
    ctx = get_app_context()
    warning = ctx.get_qmt_warning()
    return jsonify({
        'success': True,
        'has_warning': bool(warning),
        'warning': warning,
        'qmt_available': ctx.qmt_available
    })


@bp.route('/api/system/miniqmt-status')
def miniqmt_status_route():
    """获取 MiniQMT 服务状态"""
    from services.miniqmt_service import miniqmt_service
    status = miniqmt_service.get_status()
    return jsonify({
        'success': True,
        'data': status
    })


@bp.route('/api/system/miniqmt-restart', methods=['POST'])
@write_protected
def miniqmt_restart_route():
    """手动重启 MiniQMT"""
    from services.miniqmt_service import miniqmt_service
    miniqmt_service._stop_process()
    success = miniqmt_service._start_process()
    return jsonify({
        'success': success,
        'message': '重启成功' if success else '重启失败'
    })


@bp.route('/api/system/miniqmt-setup-boot', methods=['POST'])
@write_protected
def miniqmt_setup_boot_route():
    """配置 MiniQMT 开机启动"""
    from services.miniqmt_service import setup_boot_start
    try:
        boot_script = setup_boot_start()
        return jsonify({
            'success': True,
            'message': '开机启动配置已生成',
            'boot_script': str(boot_script),
            'note': '请以管理员身份运行生成的命令'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'配置失败: {str(e)}'
        }), 500


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


@bp.route('/api/system/data-source-health')
def data_source_health_route():
    """Return local cache/update health without triggering upstream requests."""
    from services.data_source_health import build_data_source_health
    return jsonify(build_data_source_health())


@bp.route('/api/system/frontend-config')
def frontend_config_route():
    """前端需要的配置参数（纯数字，无敏感信息）"""
    return jsonify({
        'kline_sync_timeout': KLINE_SYNC_TIMEOUT,
        'kline_fetch_timeout_ms': KLINE_FETCH_TIMEOUT_MS,
        'loading_min_ms': LOADING_MIN_MS,
        'loading_max_ms': LOADING_MAX_MS,
        'intraday_ohlc_ttl_sec': INTRADAY_OHLC_TTL_SEC,
    })


@bp.route('/api/system/restart', methods=['POST'])
@write_protected
def system_restart_route():
    """清缓存+重新预热（不重启进程）"""
    get_cache().clear()
    from data.market_kline import load_index_kline, load_hk_index_kline
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
@write_protected
def trigger_board_update_route():
    """手动触发全量板块更新（已迁移到 /api/tasks/update/boards）"""
    task = create_boards_update_task()
    return jsonify({'ok': True, 'deprecated': True, 'task': task.to_dict()})


@bp.route('/api/update/force', methods=['POST'])
@write_protected
def trigger_force_update_route():
    """强制刷新所有数据（已迁移到 /api/tasks/update/force）"""
    task = create_force_update_task()
    return jsonify({'ok': True, 'deprecated': True, 'task': task.to_dict()})


@bp.route('/api/update/stock/<code>', methods=['POST'])
@write_protected
def trigger_stock_update_route(code):
    """手动触发单只个股更新（已迁移到 /api/tasks/update/stock/<code>）"""
    task = create_stock_update_task(code)
    return jsonify({'ok': True, 'deprecated': True, 'task': task.to_dict()})


@bp.route('/api/update/debt')
def update_debt_route():
    """GET /api/update/debt — 只读欠更扫描"""
    try:
        from data_update_manager import scan_update_debt
        debt = scan_update_debt()
    except Exception as e:
        debt = {
            'needs_catchup': False,
            'summary': f'debt scan unavailable: {e}',
        }
    return jsonify({'ok': True, 'debt': debt})


# ---- SSE 事件推送 ----

@bp.route('/api/events')
def sse_event_stream_route():
    """SSE endpoint：推送数据更新 + AI分析事件给前端"""
    token = _claim_sse_stream(managed=bool(request.args.get('client_id')))
    if token is None:
        return Response(status=204, headers={'Cache-Control': 'no-store'})

    event_bus = get_event_bus()

    def event_stream():
        last_idx = 0
        last_heartbeat = time.monotonic()
        try:
            yield ': connected\n\n'
            while _owns_sse_stream(token):
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

                now = time.monotonic()
                if now - last_heartbeat >= 15:
                    yield ': heartbeat\n\n'
                    last_heartbeat = now
                time.sleep(1)
        finally:
            _release_sse_stream(token)

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
@write_protected
def save_classification_route():
    """保存用户重分类后的板块数据（v5 schema 校验）"""
    from pathlib import Path
    data = request.get_json()
    if not data or 'categories' not in data:
        return jsonify({'error': 'missing categories'}), 400

    # ---- v5 schema 校验 ----
    error = _validate_classification_v5(data)
    if error:
        return jsonify({'error': error}), 400

    try:
        save_file = STATIC_DIR / 'board_classification_saved.json'
        with open(save_file, 'w', encoding='utf-8') as f:
            import json as _json
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # ---- 重建搜索索引 + 清缓存 ----
    search_index_rebuilt = False
    search_index_error = None
    try:
        build_index_json()
        search_index_rebuilt = True
    except Exception as e:
        search_index_error = str(e)

    try:
        svc = get_search_service()
        if hasattr(svc, 'clear_cache'):
            svc.clear_cache()
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'search_index_rebuilt': search_index_rebuilt,
        'search_index_error': search_index_error,
    })


def _validate_classification_v5(data):
    """校验 v5 分类 payload，返回 None 表示合法，否则返回错误描述字符串。"""
    if data.get('version') != '5.0':
        return 'version must be 5.0'

    valid_types = {'industry', 'concept'}
    for cat in data.get('categories', []):
        cat_name = cat.get('name')
        if not cat_name or not isinstance(cat_name, str):
            return 'category missing name'
        for sub in cat.get('subcategories', []):
            sub_name = sub.get('name')
            if not sub_name or not isinstance(sub_name, str):
                return f'subcategory in {cat_name} missing name'
            for board in sub.get('boards', []):
                # 必备字段
                for field in ('code', 'name', 'type', 'primary_category', 'secondary_category', 'tags'):
                    if field not in board:
                        return f'board {board.get("code","?")} missing {field}'
                if board['type'] not in valid_types:
                    return f'board {board["code"]} invalid type: {board["type"]}'
                # primary/secondary 一致性
                if board['primary_category'] != cat_name:
                    return f'board {board["code"]} primary_category mismatch'
                if board['secondary_category'] != sub_name:
                    return f'board {board["code"]} secondary_category mismatch'
                # tags 校验：2-5 个去重非空字符串
                tags = board.get('tags', [])
                if not isinstance(tags, list) or len(tags) < 2 or len(tags) > 5:
                    return f'board {board["code"]} tags count must be 2-5, got {len(tags) if isinstance(tags, list) else type(tags)}'
                cleaned = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
                if len(cleaned) != len(tags):
                    return f'board {board["code"]} tags contain empty or whitespace-only entries'
                if len(set(cleaned)) != len(cleaned):
                    return f'board {board["code"]} tags contain duplicates'
    return None


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
