"""test_qmt_client.py — QMT 客户端测试"""
import json
import subprocess
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

from data.qmt_client import QMTClient, get_qmt_client


class TestToQmtCode:
    """代码映射测试"""

    def test_index_sh(self):
        assert QMTClient.to_qmt_code('sh000001') == '000001.SH'

    def test_index_sz(self):
        assert QMTClient.to_qmt_code('sz399006') == '399006.SZ'

    def test_stock_sh(self):
        assert QMTClient.to_qmt_code('600519', 'stock') == '600519.SH'

    def test_stock_sz(self):
        assert QMTClient.to_qmt_code('000001', 'stock') == '000001.SZ'

    def test_stock_bj(self):
        assert QMTClient.to_qmt_code('430047', 'stock') == '430047.BJ'
        assert QMTClient.to_qmt_code('830799', 'stock') == '830799.BJ'

    def test_hk(self):
        assert QMTClient.to_qmt_code('00700', 'hk') == '00700.HK'

    def test_unknown_returns_as_is(self):
        assert QMTClient.to_qmt_code('UNKNOWN') == 'UNKNOWN'

    def test_hk_index(self):
        assert QMTClient.to_qmt_code('HSI') == 'HSI.HK'


class TestGetConstituentsBatch:
    """批量成分股数据测试"""

    def test_empty_codes_returns_empty(self):
        client = QMTClient()
        assert client.get_constituents_batch([]) == {}

    @patch('data.qmt_client.QMT_ENABLED', False)
    def test_qmt_disabled_returns_empty(self):
        client = QMTClient()
        assert client.get_constituents_batch(['600519']) == {}

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_batch_returns_data(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({'600519': {'close': 1800.0, 'change_pct': 1.5, 'mkt_cap': 22600.0, 'volume': 10000}}).encode(),
            stderr=b''
        )
        client = QMTClient()
        result = client.get_constituents_batch(['600519'])
        assert '600519' in result
        assert result['600519']['close'] == 1800.0

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_batch_handles_subprocess_error(self, mock_run, mock_exists):
        mock_run.side_effect = subprocess.TimeoutExpired('cmd', 15)
        client = QMTClient()
        result = client.get_constituents_batch(['600519'])
        assert result == {}

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_batch_no_injection(self, mock_run, mock_exists):
        """验证 code_list 通过 sys.argv 传参，不通过 f-string 注入"""
        mock_run.return_value = MagicMock(stdout=b'{}', stderr=b'')
        client = QMTClient()
        client.get_constituents_batch(['600519'])
        # 检查 subprocess.run 被调用时第三个参数是 code_list_json
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert len(cmd) >= 4  # [python, '-c', script, code_list_json]
        # 确保脚本中不包含 code_list 字面量
        script = cmd[2]
        assert 'codes = json.loads(sys.argv[1])' in script
        assert '600519' not in script  # 不应该在脚本中出现


class TestGetDailyLocal:
    """日线数据测试"""

    @patch('data.qmt_client.QMT_ENABLED', False)
    def test_qmt_disabled_returns_none(self):
        client = QMTClient()
        assert client.get_daily_local('000001.SH') is None

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_returns_dataframe(self, mock_run, mock_exists):
        data = [
            {'date': '2025-01-01', 'open': 3000, 'high': 3100, 'low': 2950, 'close': 3050, 'volume': 100000},
        ]
        mock_run.return_value = MagicMock(
            stdout=json.dumps(data).encode(),
            stderr=b''
        )
        client = QMTClient()
        df = client.get_daily_local('000001.SH')
        assert df is not None
        assert not df.empty
        assert 'close' in df.columns

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_empty_output_returns_none(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(stdout=b'[]', stderr=b'')
        client = QMTClient()
        df = client.get_daily_local('000001.SH')
        assert df is None


class TestGetDailyUnified:
    """统一日线：公式口优先"""

    @patch('data.qmt_client.QMT_ENABLED', False)
    def test_get_daily_disabled(self):
        client = QMTClient()
        assert client.get_daily('000001.SH') is None

    def test_get_daily_prefers_formula(self):
        client = QMTClient()
        formula_df = pd.DataFrame([
            {'date': '2026-07-10', 'open': 1, 'high': 2, 'low': 1, 'close': 1.5, 'volume': 100},
        ])
        with patch.object(client, 'get_daily_formula', return_value=formula_df) as m_f, \
             patch.object(client, 'get_daily_local') as m_x:
            df = client.get_daily('000001.SH', start='20260601', end='20260717')
            assert df is not None
            assert len(df) == 1
            assert client.active_channel == 'formula'
            m_f.assert_called_once()
            m_x.assert_not_called()

    def test_get_daily_fallback_xtdata(self):
        client = QMTClient()
        xtdata_df = pd.DataFrame([
            {'date': '2026-07-10', 'open': 1, 'high': 2, 'low': 1, 'close': 1.5, 'volume': 100},
        ])
        with patch.object(client, 'get_daily_formula', return_value=None), \
             patch.object(client, 'get_daily_local', return_value=xtdata_df):
            df = client.get_daily('000001.SH')
            assert df is not None
            assert client.active_channel == 'xtdata'

    def test_probe_formula_ready(self):
        client = QMTClient()
        formula_df = pd.DataFrame([
            {'date': '2026-07-10', 'open': 1, 'high': 2, 'low': 1, 'close': 3996.16, 'volume': 100},
        ])
        with patch.object(client, 'get_daily_formula', return_value=formula_df):
            r = client.probe_formula_ready()
            assert r['ok'] is True
            assert r['rows'] == 1
            assert r['channel'] == 'formula'


class TestGetMinuteKline:
    """分钟线测试"""

    def test_invalid_period_returns_empty(self):
        client = QMTClient()
        df = client.get_minute_kline('600519', 'stock', 'invalid')
        assert df.empty


class TestGetStockList:
    """个股列表测试"""

    @patch('data.qmt_client.QMT_ENABLED', False)
    def test_qmt_disabled_returns_empty(self):
        client = QMTClient()
        assert client.get_stock_list() == []

    @patch('data.qmt_client.QMT_ENABLED', True)
    @patch('data.qmt_client.os.path.exists', return_value=True)
    @patch('data.qmt_client.subprocess.run')
    def test_returns_list(self, mock_run, mock_exists):
        data = [{'code': '600519', 'name': '贵州茅台', 'market': 'SH'}]
        mock_run.return_value = MagicMock(
            stdout=json.dumps(data).encode(),
            stderr=b''
        )
        client = QMTClient()
        result = client.get_stock_list()
        assert len(result) == 1
        assert result[0]['code'] == '600519'


class TestSingleton:
    """单例模式测试"""

    def test_get_qmt_client_singleton(self):
        c1 = get_qmt_client()
        c2 = get_qmt_client()
        assert c1 is c2
