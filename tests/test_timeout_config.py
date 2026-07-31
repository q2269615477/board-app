"""test_timeout_config.py — config 默认值 + bg 线程池回归测试"""
import os
import importlib
import pytest

import core.config as core_config
from services.kline_service import KLineService, _pending, _pending_lock


def _clear_pending():
    """清理模块级 _pending 状态，避免测试间互相干扰"""
    with _pending_lock:
        _pending.clear()


class TestConfigDefaults:
    """config 模块默认值断言"""

    def test_kline_sync_timeout_default(self):
        """KLINE_SYNC_TIMEOUT 默认值应为 15"""
        assert core_config.KLINE_SYNC_TIMEOUT == 15

    def test_kline_bg_refresh_workers_default(self):
        """KLINE_BG_REFRESH_WORKERS 默认值应为 2"""
        assert core_config.KLINE_BG_REFRESH_WORKERS == 2

    def test_qmt_timeout_minute_default(self):
        """QMT_TIMEOUT_MINUTE 默认值应为 30"""
        assert core_config.QMT_TIMEOUT_MINUTE == 30


class TestConfigEnvOverride:
    """环境变量覆盖测试：设 env 后重载/读取生效"""

    def test_env_override_kline_sync_timeout(self, monkeypatch):
        """设 KLINE_SYNC_TIMEOUT env 后 reload config 生效"""
        monkeypatch.setenv('KLINE_SYNC_TIMEOUT', '42')
        importlib.reload(core_config)
        assert core_config.KLINE_SYNC_TIMEOUT == 42

    def test_env_override_kline_bg_refresh_workers(self, monkeypatch):
        """设 KLINE_BG_REFRESH_WORKERS env 后 reload config 生效"""
        monkeypatch.setenv('KLINE_BG_REFRESH_WORKERS', '8')
        importlib.reload(core_config)
        assert core_config.KLINE_BG_REFRESH_WORKERS == 8

    def test_env_override_qmt_timeout_minute(self, monkeypatch):
        """设 QMT_TIMEOUT_MINUTE env 后 reload config 生效"""
        monkeypatch.setenv('QMT_TIMEOUT_MINUTE', '60')
        importlib.reload(core_config)
        assert core_config.QMT_TIMEOUT_MINUTE == 60

    def teardown_method(self):
        """恢复默认值：移除 env 并 reload"""
        for key in ('KLINE_SYNC_TIMEOUT', 'KLINE_BG_REFRESH_WORKERS', 'QMT_TIMEOUT_MINUTE'):
            os.environ.pop(key, None)
        importlib.reload(core_config)


class TestBackgroundRefreshUsesBgExecutor:
    """验证 _submit_background_refresh 使用 _bg_executor 而非 _executor"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_submit_background_refresh_uses_bg_executor(self):
        """_submit_background_refresh 应提交到 _bg_executor，_executor 不应被调用"""
        from unittest.mock import patch, MagicMock
        from services.kline_service import _executor

        svc = KLineService.__new__(KLineService)
        svc._cache = MagicMock()
        svc._db = MagicMock()
        svc._qmt = MagicMock()

        with patch('services.kline_service._bg_executor') as mock_bg_exec, \
             patch.object(_executor, 'submit') as mock_exec_submit:
            mock_bg_exec.submit.return_value = MagicMock()

            result = svc._submit_background_refresh(
                'stock', '600519', 'daily', '', 'stock:600519:daily'
            )

            # _bg_executor.submit 应被调用
            mock_bg_exec.submit.assert_called_once()
            # _executor.submit 不应被调用
            mock_exec_submit.assert_not_called()
            # 返回 True 表示本次提交了新任务
            assert result is True

    def test_submit_background_refresh_dedup_via_bg_executor(self):
        """同一 cache_key 重复调用时，_bg_executor 只提交一次"""
        from unittest.mock import patch, MagicMock
        from services.kline_service import _executor

        svc = KLineService.__new__(KLineService)
        svc._cache = MagicMock()
        svc._db = MagicMock()
        svc._qmt = MagicMock()

        with patch('services.kline_service._bg_executor') as mock_bg_exec, \
             patch.object(_executor, 'submit') as mock_exec_submit:
            mock_bg_exec.submit.return_value = MagicMock()

            # 第一次：提交
            r1 = svc._submit_background_refresh(
                'stock', '600519', 'daily', '', 'stock:600519:daily'
            )
            # 第二次：去重，不提交
            r2 = svc._submit_background_refresh(
                'stock', '600519', 'daily', '', 'stock:600519:daily'
            )

            assert mock_bg_exec.submit.call_count == 1
            mock_exec_submit.assert_not_called()
            assert r1 is True
            assert r2 is False
