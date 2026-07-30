"""
services/__init__.py
业务逻辑层：封装所有业务规则和数据组装流程
"""
from services.kline_service import KLineService, get_kline_service
from services.board_service import BoardService, get_board_service
from services.search_service import SearchService, get_search_service

__all__ = [
    'KLineService', 'get_kline_service',
    'BoardService', 'get_board_service',
    'SearchService', 'get_search_service',
]
