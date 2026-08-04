"""
数据更新调度器测试
"""
import pytest
import pandas as pd
from datetime import datetime
from unittest.mock import Mock, patch
from services.data_update_scheduler import DataUpdateScheduler, data_update_scheduler
from services.data_update_scheduler import update_noon_data
import data_update_manager as manager


@pytest.fixture(autouse=True)
def isolate_real_scheduler_thread(monkeypatch):
    """Facade unit tests must not launch the production scheduler thread."""
    monkeypatch.setattr('data_update_manager.start_scheduler', lambda: None)
    monkeypatch.setattr('data_update_manager.stop_scheduler', lambda: None)


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


def test_noon_snapshot_reuses_unified_live_caches(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots',
        lambda force=False: {
            'data': {'sh000300': {'price': 4588.2, 'channel': 'qmt18080'}},
        },
    )

    class Cache:
        def get(self, board_type):
            return pd.DataFrame([{
                '板块代码': 'BK0001',
                '最新价': 123.4,
                '涨跌幅': 1.2,
            }])

    monkeypatch.setattr(
        'services.board_spot_cache.get_board_spot_cache',
        lambda: Cache(),
    )
    monkeypatch.setattr(
        'services.data_update_scheduler.noon_cache_manager.save_noon_data',
        lambda data: captured.update(data) or True,
    )

    assert update_noon_data() is True
    assert captured['sh000300']['channel'] == 'qmt18080'
    assert captured['BK0001']['source'] == 'eastmoney_push2delay_frozen'


def test_noon_snapshot_accepts_board_cache_dictionary(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots',
        lambda force=False: {'data': {}},
    )

    class Cache:
        def get(self, board_type):
            return {
                'BK0001': {
                    '最新价': 123.4,
                    '涨跌幅': 1.2,
                },
            }

    monkeypatch.setattr(
        'services.board_spot_cache.get_board_spot_cache',
        lambda: Cache(),
    )
    monkeypatch.setattr(
        'services.data_update_scheduler.noon_cache_manager.save_noon_data',
        lambda data: captured.update(data) or True,
    )

    assert update_noon_data() is True
    assert captured['BK0001']['price'] == 123.4
    assert captured['BK0001']['source'] == 'eastmoney_push2delay_frozen'


def test_noon_industry_failure_does_not_block_concept(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots',
        lambda force=False: {'data': {}},
    )

    class Cache:
        def get(self, board_type):
            if board_type == 'industry':
                raise RuntimeError('industry unavailable')
            return {
                'BK2001': {'最新价': 98.7, '涨跌幅': -0.8},
            }

    monkeypatch.setattr(
        'services.board_spot_cache.get_board_spot_cache', lambda: Cache()
    )
    monkeypatch.setattr(
        'services.data_update_scheduler.noon_cache_manager.save_noon_data',
        lambda data: captured.update(data) or True,
    )

    assert update_noon_data() is True
    assert captured['BK2001']['price'] == 98.7


def test_completed_friday_update_waits_until_next_trading_close(monkeypatch):
    friday = datetime(2026, 7, 31, 19, 52, 49)
    monday_close = datetime(2026, 8, 3, 15, 30)
    monkeypatch.setattr(
        manager,
        '_get_trade_cal_set',
        lambda *args, **kwargs: {'2026-08-03'},
    )

    wait_seconds = manager._wait_after_daily_update(friday, True)

    assert friday.timestamp() + wait_seconds == monday_close.timestamp()


def test_incomplete_daily_update_retries_in_ten_minutes():
    now = datetime(2026, 7, 31, 19, 52, 49)
    assert manager._wait_after_daily_update(now, False) == 600
