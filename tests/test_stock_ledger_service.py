"""tests/test_stock_ledger_service.py — stock_ledger_service 模块测试

验证从 data_update_manager.py 抽取的个股台账层：
  - get_ledger_conn 基本行为
  - is_stock_cached / add_stock_to_ledger CRUD
  - get_all_cached_stocks 排序
  - rebuild_stock_ledger_from_kline 从 kline 表重建
  - data_update_manager.py facade 委托正确
"""
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.stock_ledger_service import (
    get_ledger_conn, is_stock_cached, add_stock_to_ledger,
    get_all_cached_stocks, rebuild_stock_ledger_from_kline,
)


def _create_ledger_schema(db_path):
    """创建测试用台账表结构。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_ledger (
            code TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            first_cached TEXT,
            last_updated TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kline (
            code TEXT,
            period TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


class TestGetLedgerConn:
    """get_ledger_conn 基本行为。"""

    def test_returns_connection(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        conn = get_ledger_conn(str(db))
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_row_factory_set(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        conn = get_ledger_conn(str(db))
        assert conn.row_factory == sqlite3.Row
        conn.close()


class TestIsStockCached:
    """is_stock_cached 基本行为。"""

    def test_not_cached_on_empty(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        assert not is_stock_cached('000001', str(db))

    def test_cached_after_add(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000001', '平安银行', str(db))
        assert is_stock_cached('000001', str(db))

    def test_not_cached_different_code(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000001', '平安银行', str(db))
        assert not is_stock_cached('600519', str(db))


class TestAddStockToLedger:
    """add_stock_to_ledger 基本行为。"""

    def test_add_single(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000001', '平安银行', str(db))
        assert is_stock_cached('000001', str(db))

    def test_add_multiple(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000001', '平安银行', str(db))
        add_stock_to_ledger('600519', '贵州茅台', str(db))
        stocks = get_all_cached_stocks(str(db))
        assert '000001' in stocks
        assert '600519' in stocks

    def test_add_duplicate_updates_name(self, tmp_path):
        """重复添加应更新名称，不创建重复行。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000001', 'Old Name', str(db))
        add_stock_to_ledger('000001', 'New Name', str(db))
        conn = sqlite3.connect(str(db))
        row = conn.execute('SELECT name FROM stock_ledger WHERE code=?', ('000001',)).fetchone()
        conn.close()
        assert row[0] == 'New Name'

    def test_add_empty_name(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        add_stock_to_ledger('000002', '', str(db))
        assert is_stock_cached('000002', str(db))


class TestGetAllCachedStocks:
    """get_all_cached_stocks 基本行为。"""

    def test_empty_returns_empty_list(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        assert get_all_cached_stocks(str(db)) == []

    def test_returns_sorted(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        # 逆序添加
        add_stock_to_ledger('600519', '贵州茅台', str(db))
        add_stock_to_ledger('000001', '平安银行', str(db))
        add_stock_to_ledger('002821', '凯中精密', str(db))
        stocks = get_all_cached_stocks(str(db))
        assert stocks == ['000001', '002821', '600519']


class TestRebuildStockLedgerFromKline:
    """rebuild_stock_ledger_from_kline 基本行为。"""

    def test_rebuild_from_kline_data(self, tmp_path):
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        # 插入 kline 数据
        conn = sqlite3.connect(str(db))
        conn.executemany(
            "INSERT INTO kline VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ('000001', 'daily', '2026-07-01', 10, 11, 9, 10.5, 1000, ''),
                ('000001', 'daily', '2026-07-02', 10.5, 11, 10, 10.8, 800, ''),
                ('600519', 'daily', '2026-07-01', 1800, 1820, 1790, 1810, 500, ''),
            ]
        )
        conn.commit()
        conn.close()

        result = rebuild_stock_ledger_from_kline(1, str(db))
        assert result['codes'] == 2
        assert is_stock_cached('000001', str(db))
        assert is_stock_cached('600519', str(db))

    def test_rebuild_respects_min_rows(self, tmp_path):
        """min_rows 过滤生效。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        conn = sqlite3.connect(str(db))
        conn.executemany(
            "INSERT INTO kline VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ('000001', 'daily', '2026-07-01', 10, 11, 9, 10.5, 1000, ''),
                ('600519', 'daily', '2026-07-01', 1800, 1820, 1790, 1810, 500, ''),
                ('600519', 'daily', '2026-07-02', 1800, 1820, 1790, 1810, 500, ''),
            ]
        )
        conn.commit()
        conn.close()

        # min_rows=2 → 只有 600519 有 2 行
        result = rebuild_stock_ledger_from_kline(2, str(db))
        assert result['codes'] == 1
        assert not is_stock_cached('000001', str(db))
        assert is_stock_cached('600519', str(db))

    def test_rebuild_ignores_non_6_digit_codes(self, tmp_path):
        """非 6 位代码（如指数 sh000001）不被纳入台账。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        conn = sqlite3.connect(str(db))
        conn.executemany(
            "INSERT INTO kline VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ('sh000001', 'daily', '2026-07-01', 10, 11, 9, 10.5, 1000, ''),
                ('000001', 'daily', '2026-07-01', 10, 11, 9, 10.5, 1000, ''),
            ]
        )
        conn.commit()
        conn.close()

        result = rebuild_stock_ledger_from_kline(1, str(db))
        assert result['codes'] == 1
        assert is_stock_cached('000001', str(db))
        assert not is_stock_cached('sh000001', str(db))

    def test_rebuild_preserves_existing_name(self, tmp_path):
        """重建时保留已有台账中的名称。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        # 先手动插入带名称的台账
        add_stock_to_ledger('000001', '平安银行', str(db))

        # 插入 kline 数据
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO kline VALUES ('000001','daily','2026-07-01',10,11,9,10.5,1000,'')"
        )
        conn.commit()
        conn.close()

        rebuild_stock_ledger_from_kline(1, str(db))
        conn = sqlite3.connect(str(db))
        row = conn.execute('SELECT name FROM stock_ledger WHERE code=?', ('000001',)).fetchone()
        conn.close()
        assert row[0] == '平安银行'


class TestConnectionCleanupOnFailure:
    """所有台账入口都必须在异常路径关闭连接并继续传播异常。"""

    def test_is_stock_cached_closes_after_sql_error(self):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError('query failed')
        with patch('services.stock_ledger_service.get_ledger_conn', return_value=conn):
            with pytest.raises(RuntimeError, match='query failed'):
                is_stock_cached('000001')
        conn.close.assert_called_once_with()

    def test_add_stock_to_ledger_closes_after_commit_error(self):
        conn = MagicMock()
        conn.commit.side_effect = RuntimeError('commit failed')
        with patch('services.stock_ledger_service.get_ledger_conn', return_value=conn):
            with pytest.raises(RuntimeError, match='commit failed'):
                add_stock_to_ledger('000001', '平安银行')
        conn.close.assert_called_once_with()

    def test_get_all_cached_stocks_closes_after_sql_error(self):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError('query failed')
        with patch('services.stock_ledger_service.get_ledger_conn', return_value=conn):
            with pytest.raises(RuntimeError, match='query failed'):
                get_all_cached_stocks()
        conn.close.assert_called_once_with()

    def test_rebuild_closes_after_sql_error(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError('rebuild query failed')
        conn.cursor.return_value = cursor
        with patch('services.stock_ledger_service.get_ledger_conn', return_value=conn):
            with pytest.raises(RuntimeError, match='rebuild query failed'):
                rebuild_stock_ledger_from_kline()
        conn.close.assert_called_once_with()


class TestFacadeDelegation:
    """验证 data_update_manager.py 中的 facade 函数正确委托。"""

    def test_dum_get_ledger_conn_delegates(self, tmp_path, monkeypatch):
        """_get_ledger_conn 委托正确。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        monkeypatch.setattr('data_update_manager._LEDGER_DB', str(db))
        from data_update_manager import _get_ledger_conn
        conn = _get_ledger_conn()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_dum_is_stock_cached_delegates(self, tmp_path, monkeypatch):
        """is_stock_cached 委托正确。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        monkeypatch.setattr('data_update_manager._LEDGER_DB', str(db))
        from data_update_manager import is_stock_cached, add_stock_to_ledger
        assert not is_stock_cached('000001')
        add_stock_to_ledger('000001', 'Test')
        assert is_stock_cached('000001')

    def test_dum_add_stock_to_ledger_delegates(self, tmp_path, monkeypatch):
        """add_stock_to_ledger 委托正确。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        monkeypatch.setattr('data_update_manager._LEDGER_DB', str(db))
        from data_update_manager import add_stock_to_ledger, is_stock_cached
        add_stock_to_ledger('600519', '贵州茅台')
        assert is_stock_cached('600519')

    def test_dum_get_all_cached_stocks_delegates(self, tmp_path, monkeypatch):
        """get_all_cached_stocks 委托正确。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        monkeypatch.setattr('data_update_manager._LEDGER_DB', str(db))
        from data_update_manager import get_all_cached_stocks, add_stock_to_ledger
        add_stock_to_ledger('000001', 'A')
        add_stock_to_ledger('600519', 'B')
        stocks = get_all_cached_stocks()
        assert stocks == ['000001', '600519']

    def test_dum_rebuild_delegates(self, tmp_path, monkeypatch):
        """rebuild_stock_ledger_from_kline 委托正确。"""
        db = tmp_path / 'test.db'
        _create_ledger_schema(db)
        monkeypatch.setattr('data_update_manager._LEDGER_DB', str(db))
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO kline VALUES ('000001','daily','2026-07-01',10,11,9,10.5,1000,'')"
        )
        conn.commit()
        conn.close()

        from data_update_manager import rebuild_stock_ledger_from_kline, is_stock_cached
        result = rebuild_stock_ledger_from_kline(1)
        assert result['codes'] == 1
        assert is_stock_cached('000001')
