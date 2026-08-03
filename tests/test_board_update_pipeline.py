"""test_board_update_pipeline.py — 板块更新管线回归测试

覆盖:
  _append_board_row_csv() — 11列/7列追加、去重
  update_all_boards() — CSV写入、SQLite写入、meta非倒退、并发锁、空数据
"""
import csv
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ===== 共享夹具 =====

@pytest.fixture(autouse=True)
def isolate_runtime_update_status(monkeypatch):
    """Board pipeline tests must never mutate data/update_status.json."""
    monkeypatch.setattr('data_update_manager._update_status', lambda mutator: None)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """临时 DATA_ROOT：含 行业/概念 CSV 目录 + kline.db + static/board_classification.json"""
    data_root = tmp_path / 'data'
    for sub in ['行业板块K线数据', '概念板块K线数据']:
        (data_root / sub).mkdir(parents=True)
    # 创建真实的 board_classification.json
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    with open(static_dir / 'board_classification.json', 'w', encoding='utf-8') as f:
        json.dump({
            'categories': [
                {
                    'name': '行业板块',
                    'boards': [
                        {'type': 'industry', 'name': '测试行业A', 'code': 'BK1001'},
                        {'type': 'industry', 'name': '测试行业B', 'code': 'BK1002'},
                    ]
                },
                {
                    'name': '概念板块',
                    'boards': [
                        {'type': 'concept', 'name': '测试概念C', 'code': 'BK2001'},
                    ]
                }
            ]
        }, f)
    return data_root


@pytest.fixture
def sample_11col_csvs(tmp_data_dir):
    """创建 3 个 11 列样本 CSV（含 header + 若干历史行）"""
    header = ['日期', '开盘', '收盘', '最高', '最低', '涨跌幅', '涨跌额',
              '成交量', '成交额', '振幅', '换手率']
    codes = {'BK1001': '测试行业A', 'BK1002': '测试行业B', 'BK2001': '测试概念C'}
    paths = {}
    for code, name in codes.items():
        subdir = '行业板块K线数据' if code.startswith('BK1') else '概念板块K线数据'
        fname = f'{name}_{code}.csv'
        fp = tmp_data_dir / subdir / fname
        with open(fp, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerow(['2026-07-15', '100.00', '102.00', '103.00', '99.00',
                        '2.00', '0', '1000000', '100000000', '4.00', '1.50'])
            w.writerow(['2026-07-16', '102.00', '101.00', '104.00', '100.00',
                        '-0.98', '0', '1200000', '120000000', '3.92', '1.60'])
        paths[code] = fp
    return paths


@pytest.fixture
def sample_7col_csv(tmp_data_dir):
    """创建 1 个 7 列旧格式样本 CSV"""
    header = ['日期', '开盘', '收盘', '最高', '最低', '成交量', '成交额']
    fp = tmp_data_dir / '行业板块K线数据' / '旧格式_BK9999.csv'
    with open(fp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerow(['2026-07-15', '50.00', '51.00', '52.00', '49.00',
                    '500000', '50000000'])
    return fp


@pytest.fixture
def tmp_sqlite(tmp_data_dir):
    """临时 SQLite with kline + kline_meta 表 + 预填历史 meta"""
    db_path = tmp_data_dir / 'kline.db'
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        'CREATE TABLE kline '
        '(code TEXT, period TEXT, date TEXT, open REAL, high REAL, '
        'low REAL, close REAL, volume INTEGER, '
        'PRIMARY KEY (code, period, date))'
    )
    conn.execute(
        'CREATE TABLE kline_meta '
        '(code TEXT, period TEXT, rows INTEGER, first_date TEXT, '
        'last_date TEXT, updated_at TEXT, '
        'PRIMARY KEY (code, period))'
    )
    for code in ['BK1001', 'BK1002', 'BK2001']:
        conn.execute(
            'INSERT INTO kline_meta VALUES (?,?,?,?,?,?)',
            (code, 'daily', 1200, '2015-01-05', '2026-07-21',
             '2026-07-21 15:00:00')
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def today_row():
    return {
        'date': '2026-07-22',
        'open': 103.0, 'high': 105.0, 'low': 101.0, 'close': 104.0,
        'pct_change': 1.98, 'vol': 1500000.0, 'amount': 150000000.0,
    }


def _make_mock_df(codes_and_values):
    """构造与 Tushare dc_daily 返回格式一致的 Mock DataFrame"""
    import pandas as pd
    records = []
    for code, vals in codes_and_values.items():
        records.append({
            'ts_code': f'{code}.DC',
            'trade_date': '20260722',
            'close': vals.get('close', 0),
            'open': vals.get('open', 0),
            'high': vals.get('high', 0),
            'low': vals.get('low', 0),
            'pct_change': vals.get('pct_change', 0),
            'vol': vals.get('vol', 0),
            'amount': vals.get('amount', 0),
        })
    return pd.DataFrame(records) if records else pd.DataFrame()


# ===== 测试用例 =====

class TestAppendBoardRowCsv:
    """_append_board_row_csv 单元测试"""

    def test_append_11col_csv(self, sample_11col_csvs, today_row):
        from data_update_manager import _append_board_row_csv

        fp = sample_11col_csvs['BK1001']
        rows_before = len(fp.read_text('utf-8-sig').splitlines())

        result = _append_board_row_csv(fp, today_row['date'], today_row)
        assert result is True

        lines = fp.read_text('utf-8-sig').splitlines()
        assert len(lines) == rows_before + 1
        last = lines[-1].split(',')
        assert last[0] == '2026-07-22'
        assert float(last[1]) == pytest.approx(103.0)   # 开盘
        assert float(last[2]) == pytest.approx(104.0)   # 收盘

    def test_append_7col_csv(self, sample_7col_csv, today_row):
        from data_update_manager import _append_board_row_csv

        fp = sample_7col_csv
        rows_before = len(fp.read_text('utf-8-sig').splitlines())

        result = _append_board_row_csv(fp, today_row['date'], today_row)
        assert result is True

        lines = fp.read_text('utf-8-sig').splitlines()
        assert len(lines) == rows_before + 1
        last = lines[-1].split(',')
        assert last[0] == '2026-07-22'
        assert len(last) == 7

    def test_append_empty_file_defaults_11col(self, tmp_data_dir, today_row):
        from data_update_manager import _append_board_row_csv

        fp = tmp_data_dir / '行业板块K线数据' / '空文件_BK8888.csv'
        fp.write_text('', encoding='utf-8-sig')

        _append_board_row_csv(fp, today_row['date'], today_row)

        lines = fp.read_text('utf-8-sig').strip().split('\n')
        assert len(lines) == 1
        assert len(lines[0].split(',')) == 11


class TestUpdateAllBoards:
    """update_all_boards 集成测试"""

    def test_nested_classification_schema_is_loaded(self, tmp_path, monkeypatch):
        import data_update_manager

        static_dir = tmp_path / 'static'
        static_dir.mkdir()
        (static_dir / 'board_classification.json').write_text(
            json.dumps({
                'categories': [{
                    'name': '一级',
                    'subcategories': [{
                        'name': '二级',
                        'boards': [
                            {'type': 'industry', 'name': '行业A', 'code': 'BK1001'},
                            {'type': 'concept', 'name': '概念B', 'code': 'BK2001'},
                            {'type': 'index', 'name': '指数C', 'code': 'sh000300'},
                        ],
                    }],
                }],
            }, ensure_ascii=False),
            encoding='utf-8',
        )
        monkeypatch.chdir(tmp_path)

        assert data_update_manager._load_classified_boards(
            ('industry', 'concept')
        ) == [
            ('industry', '行业A', 'BK1001'),
            ('concept', '概念B', 'BK2001'),
        ]

    def test_writes_csv(self, tmp_path, tmp_data_dir, sample_11col_csvs):
        """Mock dc_daily 返回 3 板 → CSV 尾部均为今日"""
        import data_update_manager
        import pandas as pd

        mock_df = pd.DataFrame([
            {'ts_code': 'BK1001.DC', 'trade_date': '20260722',
             'close': 104, 'open': 103, 'high': 105, 'low': 101,
             'pct_change': 1.98, 'vol': 1500000, 'amount': 150000000},
            {'ts_code': 'BK1002.DC', 'trade_date': '20260722',
             'close': 204, 'open': 203, 'high': 205, 'low': 201,
             'pct_change': 0.5, 'vol': 800000, 'amount': 80000000},
            {'ts_code': 'BK2001.DC', 'trade_date': '20260722',
             'close': 304, 'open': 303, 'high': 305, 'low': 301,
             'pct_change': -1.2, 'vol': 600000, 'amount': 60000000},
        ])

        # Patch Path 使 board_classification.json 路径指向 tmp_path
        with patch('data_update_manager.Path', lambda p: Path(tmp_path) / p), \
             patch('data_loader.DATA_ROOT', tmp_data_dir), \
             patch('data_update_manager._load_status',
                   return_value={'boards': {}, 'today': '', 'indices': {}, 'stocks': {}}), \
             patch('data_update_manager._save_status'), \
             patch('data_update_manager._get_tushare_pro') as mock_pro:
            mock_pro.return_value.dc_daily.return_value = mock_df
            data_update_manager._update_in_progress = False

            result = data_update_manager.update_all_boards()

        assert result['success'] == 3
        assert result['failed'] == 0

        for code in ['BK1001', 'BK1002', 'BK2001']:
            fp = sample_11col_csvs[code]
            lines = fp.read_text('utf-8-sig').splitlines()
            last_date = lines[-1].split(',')[0]
            assert last_date == '2026-07-22', f'{code} 最后日期:{lines[-1][:30]}'

    def test_writes_sqlite_meta_not_overwritten(self, tmp_path, tmp_data_dir,
                                                  tmp_sqlite, sample_11col_csvs):
        """kline 表 INSERT + kline_meta.rows 保持 1200"""
        import data_update_manager
        import pandas as pd

        mock_df = pd.DataFrame([
            {'ts_code': 'BK1001.DC', 'trade_date': '20260722',
             'close': 104, 'open': 103, 'high': 105, 'low': 101,
             'pct_change': 1.98, 'vol': 1500000, 'amount': 150000000},
        ])

        with patch('data_update_manager.Path', lambda p: Path(tmp_path) / p), \
             patch('data_loader.DATA_ROOT', tmp_data_dir), \
             patch('data_update_manager._load_status',
                   return_value={'boards': {}, 'today': '', 'indices': {}, 'stocks': {}}), \
             patch('data_update_manager._save_status'), \
             patch('data_update_manager._get_tushare_pro') as mock_pro:
            mock_pro.return_value.dc_daily.return_value = mock_df
            data_update_manager._update_in_progress = False
            data_update_manager.update_all_boards()

        conn = sqlite3.connect(str(tmp_sqlite))
        # kline 表
        row = conn.execute(
            "SELECT * FROM kline WHERE code='BK1001' AND period='daily' AND date='2026-07-22'"
        ).fetchone()
        assert row is not None
        assert row[3] == pytest.approx(103.0)

        # kline_meta.rows = DB 真实 COUNT（T1/T4：禁止用 len(本次df) 脏写；
        # 夹具若只预填 meta=1200 而无历史 kline 行，COUNT 以 kline 为准）
        meta = conn.execute(
            "SELECT rows, last_date FROM kline_meta WHERE code='BK1001' AND period='daily'"
        ).fetchone()
        assert meta is not None
        real_cnt = conn.execute(
            "SELECT COUNT(*) FROM kline WHERE code='BK1001' AND period='daily'"
        ).fetchone()[0]
        assert meta[0] == real_cnt, f'kline_meta.rows={meta[0]}, COUNT={real_cnt}'
        assert meta[0] >= 1
        assert str(meta[1]).replace('-', '') >= '20260722'
        conn.close()

    def test_concurrent_lock_returns_error(self):
        """_update_in_progress=True 返回 error"""
        import data_update_manager
        data_update_manager._update_in_progress = True
        result = data_update_manager.update_all_boards()
        assert result.get('error') == '上次更新进行中'
        assert result['success'] == 0
        data_update_manager._update_in_progress = False

    def test_empty_dc_daily_graceful(self, tmp_path, tmp_data_dir):
        """dc_daily 空 → 返回 success=0 不崩溃"""
        import data_update_manager
        import pandas as pd

        with patch('data_update_manager.Path', lambda p: Path(tmp_path) / p), \
             patch('data_loader.DATA_ROOT', tmp_data_dir), \
             patch('data_update_manager._load_status',
                   return_value={'boards': {}, 'today': ''}), \
             patch('data_update_manager._save_status'), \
             patch('data_update_manager._get_tushare_pro') as mock_pro:
            mock_pro.return_value.dc_daily.return_value = pd.DataFrame()
            data_update_manager._update_in_progress = False
            result = data_update_manager.update_all_boards()

        assert result['success'] == 0
        assert result['failed'] == 0
        assert result['completion_ready'] is False

    def test_incomplete_source_snapshot_cannot_finish_settlement(
        self, tmp_path, tmp_data_dir, sample_11col_csvs
    ):
        import data_update_manager
        import pandas as pd

        mock_df = pd.DataFrame([
            {
                'ts_code': code + '.DC',
                'trade_date': '20260722',
                'close': 100,
                'open': 99,
                'high': 101,
                'low': 98,
                'pct_change': 1,
                'vol': 1000,
                'amount': 10000,
            }
            for code in ('BK1001', 'BK1002')
        ])

        with patch('data_update_manager.Path', lambda p: Path(tmp_path) / p), \
             patch('data_loader.DATA_ROOT', tmp_data_dir), \
             patch('data_update_manager._load_status',
                   return_value={'boards': {}, 'today': ''}), \
             patch('data_update_manager._save_status'), \
             patch('data_update_manager._get_tushare_pro') as mock_pro:
            mock_pro.return_value.dc_daily.return_value = mock_df
            data_update_manager._update_in_progress = False
            result = data_update_manager.update_all_boards()

        assert result['success'] == 2
        assert result['unavailable'] == 1
        assert result['completion_ready'] is False
