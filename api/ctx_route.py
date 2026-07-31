"""
api/ctx_route.py — 面板上下文获取接口
WorkBuddy MCP Tool 通过此接口获取当前面板显示的标的、周期信息
"""
from flask import Blueprint, jsonify, request

from core.context_bridge import get_context, update_context
from api.auth_guard import write_protected

bp = Blueprint('ctx', __name__)


@bp.route('/api/ctx', methods=['GET'])
def ctx_get():
    """获取当前面板上下文（WorkBuddy MCP Tool 调用）"""
    return jsonify(get_context())


@bp.route('/api/ctx', methods=['POST'])
@write_protected
def ctx_post():
    """前端更新面板上下文"""
    data = request.get_json() or {}
    update_context(
        code=data.get('code', 'sh000001'),
        type_=data.get('type', 'index'),
        period=data.get('period', 'daily'),
        name=data.get('name', ''),
        range_=data.get('range', ''),
        analysis_period=data.get('analysis_period', 'daily'),
        analysis_range=data.get('analysis_range', '1y')
    )
    return jsonify(ok=True)
