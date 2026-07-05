"""
core — 底层基础设施层
包含：统一配置、缓存管理、事件总线、应用生命周期
"""
from .config import Config
from .cache import CacheManager, get_cache
from .events import EventBus, get_event_bus

__all__ = ['Config', 'CacheManager', 'get_cache', 'EventBus', 'get_event_bus']
