"""test_cancel_propagation.py — 取消传播与 schema 一致性真实测试

覆盖：
1. qmt_update_all_stocks 真实取消时返回 {canceled: True}，且不调用 _mark_qmt_daily_done
2. update_all_today 检测到 stocks.canceled 后跳过 materialize_higher_periods
3. update_all_boards 真实建表包含 updated_at 列（调用真实函数）
"""
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')


class TestQmtCancelReal:
    """真实调用 qmt_update_all_stocks，验证取消逻辑"""

    def test_cancel_returns_canceled_true(self, monkeypatch, tmp_path):
        """cancel_check 立即返回 True 时：
        - 返回 dict 含 canceled=True
        - _mark_qmt_daily_done 未被调用
        """
        import data_update_manager as dum

        # 构造临时 ledger DB
        ledger_db = str(tmp_path / 'kline.db')
        conn = sqlite3.connect(ledger_db)
        conn.execute(
            'CREATE TABLE IF NOT EXISTS stock_ledger '
            '(code TEXT, name TEXT)'
        )
        conn.execute(
            'CREATE TABLE IF NOT EXISTS kline '
            '(code TEXT, period TEXT, date TEXT, open REAL, high REAL, '
            'low REAL, close REAL, volume INTEGER, updated_at TEXT, '
            'PRIMARY KEY (code, period, date))'
        )
        # 插入几条 pending 股票（max_date 旧 → 会被加入 pending）
        conn.execute("INSERT INTO stock_ledger VALUES ('600001', '测试股A')")
        conn.execute("INSERT INTO stock_ledger VALUES ('600002', '测试股B')")
        conn.commit()
        conn.close()

        monkeypatch.setattr(dum, '_LEDGER_DB', ledger_db)
        monkeypatch.setattr(dum, 'is_qmt_daily_done', lambda: False)
        monkeypatch.setattr(dum, '_qmt_connect', lambda: True)
        monkeypatch.setattr(dum, 'get_all_cached_stocks', lambda: [('600001', '测试股A'), ('600002', '测试股B')])
        monkeypatch.setattr(dum, 'rebuild_stock_ledger_from_kline', lambda **kw: None)

        # mock QMT client
        mock_client = type('MC', (), {
            'active_channel': 'test_channel',
            'get_daily_batch': lambda *a, **k: [],
        })()
        monkeypatch.setattr('data.qmt_client.get_qmt_client', lambda: mock_client)

        # 追踪 _mark_qmt_daily_done 是否被调用
        mark_done_called = []
        monkeypatch.setattr(dum, '_mark_qmt_daily_done', lambda: mark_done_called.append(True))

        # cancel_check 立即返回 True
        result = dum.qmt_update_all_stocks(cancel_check=lambda: True)

        assert result.get('canceled') is True
        assert len(mark_done_called) == 0  # 取消时不应标记完成


class TestCancelPropagation:
    """测试取消状态从子函数向上传递到 update_all_today"""

    def test_stocks_canceled_skips_materialize(self, monkeypatch):
        """当 qmt_update_all_stocks 返回 canceled=True 时，
        update_all_today 应跳过 materialize_higher_periods 并返回 canceled"""
        from data_update_manager import update_all_today

        materialize_called = False

        def mock_qmt_update_all_stocks(max_retries=3, **kwargs):
            return {'success': 5, 'canceled': True, 'pending': 100}

        def mock_materialize_higher_periods(codes=None):
            nonlocal materialize_called
            materialize_called = True
            return {}

        monkeypatch.setattr('data_update_manager.qmt_update_all_stocks', mock_qmt_update_all_stocks)
        monkeypatch.setattr('data_update_manager.materialize_higher_periods', mock_materialize_higher_periods)
        monkeypatch.setattr('data_update_manager.is_today_updated', lambda: False)
        monkeypatch.setattr('data_update_manager._qmt_connect', lambda: True)
        monkeypatch.setattr('data_update_manager._is_trading_day', lambda: False)

        result = update_all_today(cancel_check=lambda: False, progress_callback=lambda *a: None)

        assert result.get('canceled') is True
        assert materialize_called is False

    def test_stocks_not_canceled_runs_materialize(self, monkeypatch):
        """当 qmt_update_all_stocks 正常完成时，materialize_higher_periods 应被调用"""
        from data_update_manager import update_all_today

        materialize_called = False

        def mock_qmt_update_all_stocks(max_retries=3, **kwargs):
            return {'success': 100, 'canceled': False}

        def mock_materialize_higher_periods(codes=None):
            nonlocal materialize_called
            materialize_called = True
            return {'success': 10}

        monkeypatch.setattr('data_update_manager.qmt_update_all_stocks', mock_qmt_update_all_stocks)
        monkeypatch.setattr('data_update_manager.materialize_higher_periods', mock_materialize_higher_periods)
        monkeypatch.setattr('data_update_manager.is_today_updated', lambda: False)
        monkeypatch.setattr('data_update_manager._qmt_connect', lambda: True)
        monkeypatch.setattr('data_update_manager._is_trading_day', lambda: False)
        monkeypatch.setattr('data_update_manager._mark_today_done', lambda: None)
        # 防止真实 QMT/网络调用：update_all_today 的 Step1/3/4 未被 cancel 短路
        monkeypatch.setattr(
            'data_update_manager.update_all_indices_qmt',
            lambda max_retries=3: {'success': 10, 'failed': 0, 'skipped': 2, 'total': 10,
                                   'written': 0, 'channel': 'mock'},
        )
        monkeypatch.setattr(
            'data_update_manager.update_all_boards',
            lambda cancel_check=None, from_full=False: {'success': 0, 'failed': 0,
                                                         'skipped': 0, 'total': 0},
        )
        monkeypatch.setattr(
            'data_update_manager.refresh_all_boards_weekly_monthly',
            lambda cancel_check=None: {'success': 0, 'failed': 0, 'total': 0},
        )

        result = update_all_today(cancel_check=lambda: False, progress_callback=lambda *a: None)

        assert result.get('canceled') is not True
        assert materialize_called is True


class TestBoardsSchemaReal:
    """真实调用 update_all_boards，验证建表包含 updated_at"""

    def test_update_all_boards_creates_updated_at(self, monkeypatch, tmp_path):
        """真实调用 update_all_boards 后，kline.db 的 kline 表应包含 updated_at 列"""
        import data_update_manager as dum
        from data_loader import _safe_filename

        # 构造临时 board_classification.json
        static_dir = tmp_path / 'static'
        static_dir.mkdir(parents=True, exist_ok=True)
        bc = {
            'categories': [
                {
                    'name': '测试行业',
                    'boards': [
                        {'type': 'industry', 'name': '测试板块', 'code': 'BK0001'},
                    ],
                },
            ],
        }
        (static_dir / 'board_classification.json').write_text(
            json.dumps(bc, ensure_ascii=False), encoding='utf-8'
        )

        # 切换工作目录到 tmp_path
        monkeypatch.chdir(tmp_path)

        # 构造临时 DATA_ROOT
        data_dir = tmp_path / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr('data_loader.DATA_ROOT', data_dir)

        # mock tushare pro 返回一个含 BK 代码的 DataFrame
        today_str = pd.Timestamp.now().strftime('%Y%m%d')
        mock_df = pd.DataFrame([
            {
                'ts_code': 'BK0001.TS',
                'trade_date': today_str,
                'close': 1000.0,
                'open': 990.0,
                'high': 1010.0,
                'low': 985.0,
                'pct_change': 1.0,
                'vol': 500000.0,
                'amount': 50000000.0,
            },
        ])
        mock_pro = type('MP', (), {'dc_daily': lambda self, **kw: mock_df})()
        monkeypatch.setattr(dum, '_get_tushare_pro', lambda: mock_pro)
        monkeypatch.setattr(dum, '_load_status', lambda: {'boards': {}})
        monkeypatch.setattr(dum, '_save_status', lambda s: None)
        # 重置全局锁
        monkeypatch.setattr(dum, '_update_in_progress', False)

        # 调用真实 update_all_boards
        result = dum.update_all_boards(cancel_check=lambda: False)

        # 验证 kline.db 的 kline 表包含 updated_at 列
        db_path = data_dir / 'kline.db'
        assert db_path.exists(), f"kline.db not found at {db_path}"

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute('PRAGMA table_info(kline)').fetchall()}
        conn.close()

        assert 'updated_at' in cols, f"updated_at column missing in kline table. Columns: {cols}"

    def test_update_all_boards_writes_updated_at_value(self, monkeypatch, tmp_path):
        """真实调用 update_all_boards 后，写入的数据行应包含非空 updated_at 值"""
        import data_update_manager as dum

        static_dir = tmp_path / 'static'
        static_dir.mkdir(parents=True, exist_ok=True)
        bc = {
            'categories': [
                {
                    'name': '测试行业',
                    'boards': [
                        {'type': 'industry', 'name': '测试板块2', 'code': 'BK0002'},
                    ],
                },
            ],
        }
        (static_dir / 'board_classification.json').write_text(
            json.dumps(bc, ensure_ascii=False), encoding='utf-8'
        )

        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr('data_loader.DATA_ROOT', data_dir)

        today_str = pd.Timestamp.now().strftime('%Y%m%d')
        mock_df = pd.DataFrame([
            {
                'ts_code': 'BK0002.TS',
                'trade_date': today_str,
                'close': 2000.0,
                'open': 1990.0,
                'high': 2010.0,
                'low': 1985.0,
                'pct_change': 0.5,
                'vol': 300000.0,
                'amount': 60000000.0,
            },
        ])
        mock_pro = type('MP', (), {'dc_daily': lambda self, **kw: mock_df})()
        monkeypatch.setattr(dum, '_get_tushare_pro', lambda: mock_pro)
        monkeypatch.setattr(dum, '_load_status', lambda: {'boards': {}})
        monkeypatch.setattr(dum, '_save_status', lambda s: None)
        monkeypatch.setattr(dum, '_update_in_progress', False)

        dum.update_all_boards(cancel_check=lambda: False)

        db_path = data_dir / 'kline.db'
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT code, close, updated_at FROM kline WHERE code='BK0002' AND period='daily'"
        ).fetchone()
        conn.close()

        assert row is not None, "No row found for BK0002"
        assert row[0] == 'BK0002'
        assert row[2] is not None, "updated_at should not be NULL"
        assert len(row[2]) > 0, "updated_at should not be empty"
