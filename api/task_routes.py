"""
api/task_routes.py - 数据更新任务中心路由
提供任务化数据更新接口，支持任务跟踪、重复保护、取消。
所有任务创建逻辑统一调用 services/update_task_factories.py，
旧接口 (api/system_routes.py) 也调用同一套工厂函数，消除重复 runner。
"""
from flask import Blueprint, jsonify, request

from services.update_task_service import update_task_service
from services.update_task_factories import (
    create_force_update_task,
    create_boards_update_task,
    create_stock_update_task,
)
from api.auth_guard import write_protected

bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')


@bp.route('/update/force', methods=['POST'])
@write_protected
def task_update_force():
    """创建强制刷新任务（即时刷新 + 可选后台补齐）"""
    task = create_force_update_task()
    return jsonify({'ok': True, 'task': task.to_dict()})


@bp.route('/update/boards', methods=['POST'])
@write_protected
def task_update_boards():
    """创建全量板块更新任务"""
    task = create_boards_update_task()
    return jsonify({'ok': True, 'task': task.to_dict()})


@bp.route('/update/stock/<code>', methods=['POST'])
@write_protected
def task_update_stock(code):
    """创建单只个股更新任务"""
    task = create_stock_update_task(code)
    return jsonify({'ok': True, 'task': task.to_dict()})


@bp.route('', methods=['GET'])
def list_tasks():
    """获取任务列表"""
    limit = request.args.get('limit', 20, type=int)
    tasks = update_task_service.list_tasks(limit=limit)
    return jsonify({
        'ok': True,
        'tasks': [t.to_dict() for t in tasks],
        'has_running': update_task_service.has_running(),
    })


@bp.route('/<task_id>', methods=['GET'])
def get_task(task_id):
    """获取单个任务详情"""
    task = update_task_service.get_task(task_id)
    if not task:
        return jsonify({'ok': False, 'message': '任务不存在'}), 404
    return jsonify({'ok': True, 'task': task.to_dict()})


@bp.route('/<task_id>/cancel', methods=['POST'])
@write_protected
def cancel_task(task_id):
    """取消任务（支持 pending 和 running 状态，running 为协作式取消）"""
    result = update_task_service.cancel_task(task_id)
    if result['ok']:
        task = update_task_service.get_task(task_id)
        return jsonify({'ok': True, 'task': task.to_dict() if task else None})
    if result['reason'] == 'NOT_FOUND':
        return jsonify({'ok': False, 'message': '任务不存在'}), 404
    return jsonify({'ok': False, 'message': result.get('reason', '无法取消')}), 409
