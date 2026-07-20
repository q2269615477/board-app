"""test_data_loader.py — 数据加载层测试"""
import sqlite3
import tempfile
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import pandas as pd
import pytest

from data_loader import (
    _normalize_df, _resample, _safe_filename,
    _sqlite_spot, _qmt_live_spot, get_spot_index, get_spot_stock,
    get_global_index_spot, get_all_stocks,
)


class TestNormalizeDf:
    """列名映射测试"""

    def test_normal_df(self):
        df = pd.DataFrame({
            '日期': ['2025-01-01'],
            '开盘': [100], '收盘': [105],
            '最高': [110], '最低': [95],
            '成交量': [10000],
        })
        result = _normalize_df(df)
        assert 'date' in result.columns
        assert 'open' in result.columns
        assert len(result) == 1

    def test_empty_df(self):
        df = pd.DataFrame()
        result = _normalize_df(df)
        assert result.empty

    def test_none_df(self):
        result = _normalize_df(None)
        assert result.empty


class TestResample:
    """重采样测试"""

    def test_weekly(self):
        df = pd.DataFrame({
            'date': ['2025-01-02', '2025-01-03', '2025-01-09'],
            'open': [100, 101, 102],
            'high': [110, 111, 112],
            'low': [90, 91, 92],
            'close': [105, 106, 107],
            'volume': [1000, 2000, 3000],
        })
        result = _resample(df, 'weekly')
        assert len(result) >= 1
        assert 'date' in result.columns

    def test_monthly(self):
        df = pd.DataFrame({
            'date': ['2025-01-02', '2025-01-15', '2025-02-03'],
            'open': [100, 101, 102],
            'high': [110, 111, 112],
            'low': [90, 91, 92],
            'close': [105, 106, 107],
            'volume': [1000, 2000, 3000],
        })
        result = _resample(df, 'monthly')
        assert len(result) == 2  # 1月和2月

    def test_empty_df(self):
        df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        result = _resample(df, 'weekly')
        assert result.empty


class TestSafeFilename:
    """文件名清洗测试"""

    def test_normal(self):
        assert _safe_filename('正常名称') == '正常名称'

    def test_bad_chars(self):
        assert _safe_filename('a/b\\c:d*e?f<g>h|i') == 'a_b_c_d_e_f_g_h_i'


class TestSqliteSpot:
    """SQLite 行情测试"""

    def test_no_data(self, tmp_path):
        """无数据返回空 dict"""
        db_path = str(tmp_path / 'test.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
        conn.commit()
        conn.close()

        with patch('data_loader.DB_PATH', db_path):
            with patch('data_loader._conn_local') as mock_local:
                mock_conn = sqlite3.connect(db_path)
                mock_local.conn = mock_conn
                result = _sqlite_spot('sh000001')
                assert result == {}
                mock_conn.close()

    def test_with_data(self, tmp_path):
        """有数据返回正确价格"""
        db_path = str(tmp_path / 'test.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
        conn.execute("INSERT INTO kline VALUES ('sh000001', 'daily', '2025-01-01', 3000, 3100, 2950, 3050, 100000)")
        conn.execute("INSERT INTO kline VALUES ('sh000001', 'daily', '2024-12-31', 2980, 3010, 2970, 3000, 90000)")
        conn.commit()
        conn.close()

        with patch('data_loader._conn_local') as mock_local:
            mock_conn = sqlite3.connect(db_path)
            mock_local.conn = mock_conn
            result = _sqlite_spot('sh000001')
            assert result['price'] == 3050.0
            assert result['pre_close'] == 3000.0
            mock_conn.close()


class TestQmtLiveSpot:
    """QMT 子进程行情测试"""

    def test_qmt_disabled(self):
        """QMT 禁用时返回空"""
        with patch('core.lifecycle.is_qmt_available', return_value=False):
            result = _qmt_live_spot('sh000001')
            assert result == {}


class TestGetSpotIndex:
    """指数行情测试"""

    @patch('data_loader._qmt_live_spot', return_value={})
    @patch('data_loader._sqlite_spot', return_value={'price': 3000})
    def test_fallback_to_sqlite(self, mock_sqlite, mock_qmt):
        result = get_spot_index('sh000001')
        assert result['price'] == 3000

    @patch('data_loader._qmt_live_spot', return_value={'price': 3100, 'change_pct': 1.5})
    @patch('data_loader._sqlite_spot', return_value={})
    def test_qmt_priority(self, mock_sqlite, mock_qmt):
        result = get_spot_index('sh000001')
        assert result['price'] == 3100


class TestGetSpotStock:
    """个股行情测试"""

    @patch('data_loader._qmt_live_spot', return_value={})
    @patch('data_loader._sqlite_spot', return_value={'price': 1800})
    def test_fallback_to_sqlite(self, mock_sqlite, mock_qmt):
        result = get_spot_stock('600519')
        assert result['price'] == 1800


class TestGetGlobalIndexSpot:
    """全球指数行情测试"""

    def test_unknown_code(self):
        result = get_global_index_spot('UNKNOWN')
        assert result == {}


class TestGetAllStocks:
    """个股列表测试"""

    @patch('core.lifecycle.is_qmt_available', return_value=False)
    def test_qmt_disabled_returns_empty(self, mock_avail):
        result = get_all_stocks()
        assert result == []
