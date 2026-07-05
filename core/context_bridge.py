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


def update_context(code: str, type_: str, period: str = 'daily',
                   name: str = '', range_: str = '',
                   analysis_period: str = 'daily', analysis_range: str = '1y'):
    """前端切换标的或分析参数时调用，更新全局上下文"""
    with _lock:
        ctx = dict(code=code, type=type_, period=period, name=name, range=range_,
                   analysis_period=analysis_period, analysis_range=analysis_range)
        _ctx.update(ctx)


def get_context() -> dict:
    """WorkBuddy MCP Tool 调用，获取当前面板上下文"""
    with _lock:
        return dict(_ctx)
