"""
core/context_bridge.py — 面板上下文暴露
前端通过 JS 更新 window.__board_ctx，后端通过 HTTP 暴露给 WorkBuddy MCP Tool
"""
from threading import Lock

_ctx = {
    'code': 'sh000001',
    'type': 'index',
    'period': 'daily',
    'name': '上证指数',
    'range': '',
    'analysis_period': 'daily',
    'analysis_range': '1y'
}
_lock = Lock()


def update_context(code: str = None, type_: str = None, period: str = None,
                   name: str = None, range_: str = None,
                   analysis_period: str = None, analysis_range: str = None):
    """前端切换标的或分析参数时调用，更新全局上下文
    所有参数可选，仅更新提供的字段"""
    with _lock:
        if code is not None:
            _ctx['code'] = code
        if type_ is not None:
            _ctx['type'] = type_
        if period is not None:
            _ctx['period'] = period
            _ctx['analysis_period'] = period
        if name is not None:
            _ctx['name'] = name
        if range_ is not None:
            _ctx['range'] = range_
        if analysis_period is not None:
            _ctx['analysis_period'] = analysis_period
        if analysis_range is not None:
            _ctx['analysis_range'] = analysis_range


def get_context() -> dict:
    """WorkBuddy MCP Tool 调用，获取当前面板上下文"""
    with _lock:
        return dict(_ctx)
