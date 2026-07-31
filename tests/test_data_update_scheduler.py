"""
数据更新调度器测试
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch
from services.data_update_scheduler import DataUpdateScheduler, data_update_scheduler


def test_scheduler_initialization():
    """测试调度器初始化"""
    scheduler = DataUpdateScheduler()
    assert scheduler.is_running() == False
    assert scheduler.get_status()['schedule_times'] is not None


def test_scheduler_start_stop():
    """测试调度器启动和停止"""
    scheduler = DataUpdateScheduler()
    
    # 启动
    assert scheduler.start() == True
    assert scheduler.is_running() == True
    
    # 重复启动应该返回True
    assert scheduler.start() == True
    
    # 停止
    assert scheduler.stop() == True
    assert scheduler.is_running() == False


def test_trigger_update_when_not_running():
    """测试调度器未运行时触发更新应该失败"""
    scheduler = DataUpdateScheduler()
    # 确保调度器未运行
    scheduler.stop()
    
    result = scheduler.trigger_update('noon')
    assert result == False


def test_trigger_morning_prewarm():
    """测试触发早盘预热"""
    scheduler = DataUpdateScheduler()
    scheduler.start()
    
    with patch('services.data_update_scheduler.noon_cache_manager') as mock_cache:
        result = scheduler.trigger_update('morning_prewarm')
        assert result == True
        mock_cache.clear_noon_cache.assert_called_once()


def test_trigger_afternoon_switch():
    """测试触发下午开盘切换"""
    scheduler = DataUpdateScheduler()
    scheduler.start()
    
    with patch('services.data_update_scheduler.noon_cache_manager') as mock_cache:
        result = scheduler.trigger_update('afternoon_switch')
        assert result == True
        mock_cache.clear_noon_cache.assert_called_once()


def test_get_status():
    """测试获取调度器状态"""
    scheduler = DataUpdateScheduler()
    scheduler.start()
    
    status = scheduler.get_status()
    assert 'running' in status
    assert 'last_updates' in status
    assert 'schedule_times' in status
    assert 'noon_cache_valid' in status
    assert status['running'] == True
