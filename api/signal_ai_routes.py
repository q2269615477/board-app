"""
api/signal_ai_routes.py — + AI 分析相关路由
"""
from flask import Blueprint, request, jsonify

from services.signal_service import get_signal_service
from services.ai_service import get_ai_service
from data.sqlite_repo import get_sqlite_repo
from api.auth_guard import write_protected

bp = Blueprint('signal_ai', __name__, url_prefix='')


# ---- 信号接口 ----

@bp.route('/api/signals/<board_code>', methods=['GET'])
def get_signals_route(board_code):
    """获取板块信号"""
    service = get_signal_service()
    return jsonify(service.get_signals(board_code))


@bp.route('/api/signals/<board_code>', methods=['POST'])
@write_protected
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
@write_protected
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
@write_protected
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
    # ---- 图表控制工具 ----
    {"name": "set_symbol", "description": "切换图表标的",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"}, "type": {"type": "string"}},
         "required": ["code"]}},
    {"name": "set_period", "description": "切换图表周期",
     "inputSchema": {"type": "object", "properties": {
         "period": {"type": "string"}},
         "required": ["period"]}},
    {"name": "create_overlay", "description": "在K线图上创建画线/标注",
     "inputSchema": {"type": "object", "properties": {
         "type": {"type": "string"},
         "points": {"type": "array", "items": {"type": "object"}},
         "styles": {"type": "object"},
         "extendData": {"type": "object"}},
         "required": ["type", "points"]}},
    {"name": "remove_overlay", "description": "删除K线图上的画线/标注",
     "inputSchema": {"type": "object", "properties": {
         "overlayId": {"type": "string"}},
         "required": ["overlayId"]}},
    {"name": "scroll_to_timestamp", "description": "滚动图表到指定时间戳",
     "inputSchema": {"type": "object", "properties": {
         "timestamp": {"type": "integer"}},
         "required": ["timestamp"]}},
    # ---- 知识库 / Obsidian 学习（Agent 只复述用户原文）----
    {"name": "search_cases", "description": "检索图表标注 Case（Obsidian 双写索引）。参数 q 或 symbol/period/type",
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string"}, "symbol": {"type": "string"},
         "period": {"type": "string"}, "type": {"type": "string"},
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_case", "description": "获取单条 Case 全文（含 vault md 正文，供 Agent 复述学习）",
     "inputSchema": {"type": "object", "properties": {
         "case_id": {"type": "string"}},
         "required": ["case_id"]}},
    {"name": "search_relations", "description": "检索用户声明的跨标的 Relation（relation_note 原文）",
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string"}, "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_relation", "description": "获取单条 Relation",
     "inputSchema": {"type": "object", "properties": {
         "relation_id": {"type": "string"}},
         "required": ["relation_id"]}},
    {"name": "list_due_reminders", "description": "列出到期标注提醒",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_sessions", "description": "列出分析会话（会话彼此独立）",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_session", "description": "获取会话全文（图表+因果树+选K+画线），Agent 只复述原文",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}},
         "required": ["session_id"]}},
]


def _merged_mcp_tools():
    """对外契约 /mcp/*：面板工具 + handlers 工具表（去重，面板优先）。"""
    names = {t['name'] for t in MCP_TOOLS}
    merged = list(MCP_TOOLS)
    try:
        from mcp.tools import TOOLS as HANDLER_TOOLS
        for name, meta in HANDLER_TOOLS.items():
            if name in names:
                continue
            merged.append({
                'name': name,
                'description': meta.get('description', name),
                'inputSchema': {
                    'type': 'object',
                    'properties': meta.get('parameters') or {},
                    'required': meta.get('required') or [],
                },
            })
            names.add(name)
    except Exception:
        pass
    # 标注知识库工具（handlers 有实现但 TOOLS 可能未列全）
    for extra in (
        'search_cases', 'get_case', 'search_relations', 'get_relation', 'list_due_reminders',
    ):
        if extra not in names:
            merged.append({
                'name': extra,
                'description': f'知识库工具 {extra}',
                'inputSchema': {'type': 'object', 'properties': {}},
            })
    return merged


@bp.route('/mcp/tools', methods=['GET'])
def mcp_list_tools_route():
    """统一 MCP 工具列表（对外契约 /mcp/*）"""
    return jsonify({"tools": _merged_mcp_tools(), "contract": "/mcp/*"})


@bp.route('/mcp/call', methods=['POST'])
@write_protected
def mcp_call_tool_route():
    """统一 MCP 工具调用入口（arguments 或 params 均可）。"""
    data = request.get_json() or {}
    tool = data.get('tool', '')
    args = data.get('arguments') if data.get('arguments') is not None else data.get('params', {})
    if not isinstance(args, dict):
        args = {}
    session_id = data.get('session_id', 'default')

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
        # ---- 图表控制工具 ----
        elif tool == 'set_symbol':
            return _mcp_set_symbol(args)
        elif tool == 'set_period':
            return _mcp_set_period(args)
        elif tool == 'create_overlay':
            return _mcp_create_overlay(args)
        elif tool == 'remove_overlay':
            return _mcp_remove_overlay(args)
        elif tool == 'scroll_to_timestamp':
            return _mcp_scroll_to_timestamp(args)
        # ---- 回落：mcp.handlers（知识库 / 画线高级 / 回测 503 等）----
        try:
            from mcp.handlers import MCPHandler
            from mcp.tools import TOOLS as HANDLER_TOOLS
            handler_names = set(HANDLER_TOOLS.keys()) | {
                'search_cases', 'get_case', 'search_relations', 'get_relation', 'list_due_reminders',
            }
            if tool in handler_names:
                result = MCPHandler().handle(tool, args, session_id)
                return jsonify(result if isinstance(result, dict) else {'result': result})
        except Exception as he:
            return jsonify({"error": f"handler {tool}: {he}"}), 500
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
    from services.search_service import get_search_service
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
    from services.board_service import get_board_service
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


# ---- 图表控制工具实现 ----

import time as _time
_overlay_counter = 0


def _mcp_set_symbol(args):
    """切换图表标的 — 通过SSE广播到前端"""
    from mcp.sse import sse_manager
    code = args['code']
    dtype = args.get('type', 'stock')
    sse_manager.broadcast('set_symbol', {'code': code, 'type': dtype})
    # 同步更新上下文
    from core.context_bridge import update_context
    update_context(code=code, type_=dtype)
    return jsonify({'ok': True, 'code': code, 'type': dtype, 'message': f'已切换到: {code}'})


def _mcp_set_period(args):
    """切换图表周期"""
    from mcp.sse import sse_manager
    period = args['period']
    sse_manager.broadcast('set_period', {'period': period})
    from core.context_bridge import update_context
    update_context(period=period, analysis_period=period)
    return jsonify({'ok': True, 'period': period, 'message': f'已切换到: {period}'})


def _mcp_create_overlay(args):
    """创建画线/标注"""
    global _overlay_counter
    from mcp.sse import sse_manager
    overlay_type = args.get('type', 'line')
    points = args.get('points', [])
    styles = args.get('styles', {})
    extend_data = args.get('extendData', {})
    _overlay_counter += 1
    overlay_id = f"overlay_{_overlay_counter}"
    overlay = {
        'id': overlay_id,
        'type': overlay_type,
        'points': points,
        'styles': styles,
        'extendData': extend_data,
        'createdAt': int(_time.time() * 1000)
    }
    sse_manager.broadcast('create_overlay', overlay)
    return jsonify({'ok': True, 'overlayId': overlay_id, 'overlay': overlay})


def _mcp_remove_overlay(args):
    """删除画线/标注"""
    from mcp.sse import sse_manager
    overlay_id = args['overlayId']
    sse_manager.broadcast('remove_overlay', {'overlayId': overlay_id})
    return jsonify({'ok': True, 'removedId': overlay_id})


def _mcp_scroll_to_timestamp(args):
    """滚动图表到指定时间戳"""
    from mcp.sse import sse_manager
    timestamp = args['timestamp']
    sse_manager.broadcast('scroll_to_timestamp', {'timestamp': timestamp})
    return jsonify({'ok': True, 'timestamp': timestamp})


# ---- SSE 事件流端点 ----

@bp.route('/mcp/sse', methods=['GET'])
def mcp_sse_route():
    """SSE事件流 — 前端订阅此端点接收实时事件"""
    from flask import Response
    from mcp.sse import sse_manager

    client = sse_manager.subscribe()

    def generate():
        try:
            # 发送连接成功事件
            yield f"event: connected\ndata: {{\"clientId\": \"{client.client_id}\"}}\n\n"
            while client.connected:
                event = client.get(timeout=30)
                if event:
                    import json
                    event_type = event.get('type', 'message')
                    data = json.dumps(event.get('data', {}), ensure_ascii=False)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                else:
                    # 心跳
                    yield ": heartbeat\n\n"
        finally:
            sse_manager.unsubscribe(client.client_id)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'Connection': 'keep-alive',
                             'Access-Control-Allow-Origin': '*'})


# ---- MCP 事件端点 ----

@bp.route('/mcp/event', methods=['POST'])
@write_protected
def mcp_event_route():
    """接收Agent事件并广播到前端"""
    from mcp.sse import sse_manager
    data = request.get_json() or {}
    event_type = data.get('type', 'message')
    event_data = data.get('data', {})
    sse_manager.broadcast(event_type, event_data)
    return jsonify({'ok': True, 'broadcast': True, 'clients': sse_manager.get_client_count()})


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
@write_protected
def hook_stop_route():
    return jsonify({'ok': True})


@bp.route('/api/ai/hooks/subagent-stop', methods=['POST'])
@write_protected
def hook_subagent_stop_route():
    return jsonify({'ok': True})
