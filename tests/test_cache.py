"""test_cache.py — 缓存管理器测试"""
import time
from unittest.mock import patch
import pytest

from core.cache import CacheManager, CacheEntry


class TestCacheGetSet:
    """基本读写测试"""

    def test_get_miss(self):
        cache = CacheManager()
        assert cache.get('nonexistent') is None

    def test_set_then_get(self):
        cache = CacheManager()
        cache.set('key1', 'value1')
        assert cache.get('key1') == 'value1'

    def test_set_dict(self):
        cache = CacheManager()
        cache.set('dict_key', {'a': 1, 'b': 2})
        assert cache.get('dict_key') == {'a': 1, 'b': 2}


class TestCacheTTL:
    """TTL 过期测试"""

    def test_expired_eviction(self):
        cache = CacheManager()
        cache.set('expire_key', 'value', ttl=1)
        time.sleep(1.2)
        assert cache.get('expire_key') is None

    def test_not_expired(self):
        cache = CacheManager()
        cache.set('fresh_key', 'value', ttl=3600)
        assert cache.get('fresh_key') == 'value'


class TestCacheDelete:
    """删除测试"""

    def test_delete(self):
        cache = CacheManager()
        cache.set('del_key', 'value')
        cache.delete('del_key')
        assert cache.get('del_key') is None

    def test_delete_nonexistent(self):
        cache = CacheManager()
        cache.delete('nonexistent')  # 不应抛异常


class TestCacheClear:
    """清除测试"""

    def test_clear(self):
        cache = CacheManager()
        cache.set('k1', 'v1')
        cache.set('k2', 'v2')
        count = cache.clear()
        assert count >= 2
        assert cache.get('k1') is None

    def test_clear_by_prefix(self):
        cache = CacheManager()
        cache.set('prefix:k1', 'v1')
        cache.set('prefix:k2', 'v2')
        cache.set('other:k3', 'v3')
        count = cache.clear_by_prefix('prefix:')
        assert count == 2
        assert cache.get('other:k3') == 'v3'


class TestStaleEmpty:
    """空数据覆盖保护测试"""

    def test_rejects_empty_over_nonempty(self):
        cache = CacheManager()
        cache.set('key', [1, 2, 3])
        cache.set('key', [])  # 空列表不应覆盖
        assert cache.get('key') == [1, 2, 3]

    def test_allows_first_empty(self):
        cache = CacheManager()
        cache.set('key', [])  # 首次空列表允许
        assert cache.get('key') == []

    def test_rejects_none(self):
        cache = CacheManager()
        cache.set('key', None)
        assert cache.get('key') is None  # None 不应被缓存


class TestLRUEviction:
    """LRU 淘汰测试"""

    def test_lru_eviction(self):
        cache = CacheManager(max_items=3)
        cache.set('k1', 'v1')
        cache.set('k2', 'v2')
        cache.set('k3', 'v3')
        # 访问 k1 使 k2 成为最久未访问
        cache.get('k1')
        cache.set('k4', 'v4')  # 应淘汰 k2
        assert cache.get('k2') is None
        assert cache.get('k1') == 'v1'
