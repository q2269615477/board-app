"""
api/signal_ai_routes.py — + AI 分析相关路由
"""
from flask import Blueprint, request, jsonify

from services.signal_service import get_signal_service
from services.ai_service import get_ai_service
from data.sqlite_repo import get_sqlite_repo

bp = Blueprint('signal_ai', __name__, url_prefix='')


# ---- 信号接口 ----

@bp.route('/api/signals/<board_code>', methods=['GET'])
def get_signals_route(board_code):
    """获取板块信号"""
    service = get_signal_service()
    return jsonify(service.get_signals(board_code))


@bp.route('/api/signals/<board_code>', methods=['POST'])
def post_signals_route(board_code):
    """提交板块信号"""
    service = get_signal_service()
    data = request.get_json()
    skill = data.get('skill')
    signals = data.get('signals', [])
    replace = data.get('replace', False)
    if not skill or not signals:
        return jsonify({'error': 'missing skill or signals'}), 400
    service.submit_signals(board_code, skill, signals, 'replace' if replace else 'append')
    return jsonify({'ok': True, 'count': len(signals)})


# ---- AI 分析接口 ----

@bp.route('/api/ai/result', methods=['POST'])
def api_ai_result_post_route():
    """POST /api/ai/result — WorkBuddy Hook 推送AI分析结论"""
    data = request.get_json() or {}
    if not data.get('board_code') or not data.get('summary'):
        return jsonify({'error': 'missing board_code or summary'}), 400
    service = get_ai_service()
    service.save_result(data['board_code'], data.get('skill_id', ''), data)
    return jsonify({'ok': True, 'stored': True})


@bp.route('/api/ai/result/<board_code>', methods=['GET'])
def api_ai_result_get_route(board_code):
    """GET /api/ai/result/<board_code> — 前端获取AI分析结论"""
    service = get_ai_service()
    result = service.get_result(board_code)
    if result:
        return jsonify(result)
    return jsonify({'board_code': board_code, 'status': 'no_data'})


@bp.route('/api/ai/result/<board_code>/clear', methods=['POST'])
def api_ai_result_clear_route(board_code):
    """清除指定板块的AI分析结论"""
    service = get_ai_service()
    service.clear_result(board_code)
    return jsonify({'ok': True})


# ---- MCP 接口 ----

MCP_TOOLS = [
    {"name": "analyze_board", "description": "对指定板块执行分析",
     "inputSchema": {"type": "object", "properties": {
         "board_code": {"type": "string"}, "board_name": {"type": "string"},
         "board_type": {"type": "string"},
         "skill": {"type": "string"}},
         "required": ["board_code", "board_name", "skill"]}},
    {"name": "get_kline", "description": "获取板块/个股/指数K线数据",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"}, "type": {"type": "string"},
         "period": {"type": "string"}, "count": {"type": "integer"}},
         "required": ["code", "type"]}},
    {"name": "search", "description": "搜索股票/板块/指数（支持拼音首字母）",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
         "required": ["query"]}},
    {"name": "submit_signals", "description": "提交分析信号，标注到K线图",
     "inputSchema": {"type": "object", "properties": {
         "board_code": {"type": "string"}, "skill": {"type": "string"},
         "replace": {"type": "boolean"},
         "signals": {"type": "array", "items": {"type": "object"}}},
         "required": ["board_code", "skill", "signals"]}},
    {"name": "get_signals", "description": "获取板块历史分析信号",
     "inputSchema": {"type": "object", "properties": {
         "board_code": {"type": "string"}, "skill": {"type": "string"},
         "limit": {"type": "integer", "default": 20}},
         "required": ["board_code"]}},
    {"name": "get_constituents", "description": "获取板块成分股列表（按市值排序）",
     "inputSchema": {"type": "object", "properties": {
         "board_code": {"type": "string"}, "board_name": {"type": "string"}},
         "required": ["board_code"]}},
    {"name": "list_boards", "description": "列出所有可用板块",
     "inputSchema": {"type": "object", "properties": {
         "type": {"type": "string"}, "keyword": {"type": "string"}}}},
    {"name": "submit_ai_result", "description": "提交AI分析结论（由WorkBuddy调用）",
     "inputSchema": {"type": "object", "properties": {
         "board_code": {"type": "string"}, "board_name": {"type": "string"},
         "skill_id": {"type": "string"}, "summary": {"type": "string"},
         "direction": {"type": "string"},
         "confidence": {"type": "number"},
         "annotations": {"type": "array", "items": {"type": "object"}}},
         "required": ["board_code", "summary"]}},
    {"name": "get_panel_context", "description": "获取当前面板显示的标的、周期、类型等上下文信息",
     "inputSchema": {"type": "object", "properties": {}}},
]


@bp.route('/mcp/tools', methods=['GET'])
def mcp_list_tools_route():
    """列出所有可用MCP工具"""
    return jsonify({"tools": MCP_TOOLS})


@bp.route('/mcp/call', methods=['POST'])
def mcp_call_tool_route():
    """统一MCP工具调用入口"""
    data = request.get_json() or {}
    tool = data.get('tool', '')
    args = data.get('arguments', {})

    try:
        if tool == 'get_kline':
            return _mcp_get_kline(args)
        elif tool == 'search':
            return _mcp_search(args)
        elif tool == 'submit_signals':
            return _mcp_submit_signals(args)
        elif tool == 'get_signals':
            return _mcp_get_signals(args)
        elif tool == 'get_constituents':
            return _mcp_get_constituents(args)
        elif tool == 'list_boards':
            return _mcp_list_boards(args)
        elif tool == 'submit_ai_result':
            service = get_ai_service()
            service.save_result(args.get('board_code', ''), args.get('skill_id', ''), args)
            return jsonify({'ok': True})
        elif tool == 'get_panel_context':
            from core.context_bridge import get_context
            return jsonify(get_context())
        return jsonify({"error": f"Unknown tool: {tool}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _mcp_get_kline(args):
    code = args['code']
    dtype = args.get('type', 'industry')
    period = args.get('period', 'daily')
    count = args.get('count', 200)
    # 用 KLineService 通用逻辑
    from services.kline_service import get_kline_service
    ks = get_kline_service()
    result, _ = ks.get_kline(dtype, code, period)
    data = result.get('data', [])
    if len(data) > count:
        data = data[-count:]
    return jsonify({'data': data, 'count': len(data)})


def _mcp_search(args):
    from ..services.search_service import get_search_service
    ss = get_search_service()
    results = ss.search(args['query'])
    return jsonify({'data': results, 'count': len(results)})


def _mcp_submit_signals(args):
    service = get_signal_service()
    board_code = args['board_code']
    skill = args['skill']
    signals = args.get('signals', [])
    replace = args.get('replace', False)
    service.submit_signals(board_code, skill, signals, 'replace' if replace else 'append')
    return jsonify({'ok': True, 'count': len(signals)})


def _mcp_get_signals(args):
    service = get_signal_service()
    signals = service.get_signals(args['board_code'])
    return jsonify({'signals': signals, 'count': len(signals)})


def _mcp_get_constituents(args):
    from ..services.board_service import get_board_service
    bs = get_board_service()
    cons = bs.get_constituents_sorted(args.get('board_type', 'industry'), args['board_code'])
    return jsonify({'data': cons, 'count': len(cons)})


def _mcp_list_boards(args):
    import json
    from pathlib import Path
    with open('static/board_classification.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    result = []
    for cat in data.get('categories', []):
        result.extend(cat.get('boards', []))
    return jsonify({'data': result[:200], 'count': len(result)})


# ---- Hook 接口 ----

@bp.route('/api/health', methods=['GET'])
def api_health_route():
    """健康检查"""
    from datetime import datetime
    return jsonify({'ok': True, 'service': 'board-app', 'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


@bp.route('/api/hooks/status', methods=['GET'])
def api_hooks_status_route():
    """返回当前面板的Hook配置"""
    return jsonify({
        'panel_url': 'http://127.0.0.1:5000',
        'hook_result_url': 'http://127.0.0.1:5000/api/ai/result',
        'panel_status': 'running',
    })


@bp.route('/api/ai/hooks/stop', methods=['POST'])
def hook_stop_route():
    return jsonify({'ok': True})


@bp.route('/api/ai/hooks/subagent-stop', methods=['POST'])
def hook_subagent_stop_route():
    return jsonify({'ok': True})
