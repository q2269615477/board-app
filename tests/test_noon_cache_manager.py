"""
午休缓存管理器测试
"""
import pytest
import json
from datetime import datetime
from pathlib import Path
from services.noon_cache_manager import NoonCacheManager


def test_save_and_load_noon_data():
    """测试午休数据保存和加载"""
    manager = NoonCacheManager()
    test_data = {
        'sh000001': {'price': 3000.5, 'change_pct': 1.2, 'volume': 1000000},
        'BK1499': {'price': 1500.0, 'change_pct': -0.5, 'volume': 500000}
    }
    
    # 保存
    manager.save_noon_data(test_data)
    
    # 加载
    loaded = manager.load_noon_data()
    assert loaded == test_data


def test_clear_noon_cache():
    """测试清空午休缓存"""
    manager = NoonCacheManager()
    manager.save_noon_data({'test': 'data'})
    manager.clear_noon_cache()
    assert manager.load_noon_data() == {}


def test_is_noon_cache_valid():
    """测试缓存有效性检查"""
    manager = NoonCacheManager()
    
    # 空缓存应该无效
    assert not manager.is_noon_cache_valid()
    
    # 有数据后应该有效
    manager.save_noon_data({'test': 'data'})
    assert manager.is_noon_cache_valid()


def test_get_cache_metadata():
    """测试获取缓存元数据"""
    manager = NoonCacheManager()
    
    # 先清空缓存确保干净状态
    manager.clear_noon_cache()
    
    # 空缓存返回None
    assert manager.get_cache_metadata() is None
    
    # 保存数据后
    test_data = {'code1': {'price': 100}, 'code2': {'price': 200}}
    manager.save_noon_data(test_data)
    
    metadata = manager.get_cache_metadata()
    assert metadata is not None
    assert metadata['date'] == datetime.now().strftime('%Y%m%d')
    assert metadata['record_count'] == 2
    assert 'timestamp' in metadata
