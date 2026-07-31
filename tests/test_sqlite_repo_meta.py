"""tests/test_sqlite_repo_meta.py — save_kline kline_meta.rows 脏写修复验证"""
import sys
import os
import tempfile
from pathlib import Path
from datetime import timedelta

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')

from data.sqlite_repo import SqliteRepo


def _make_bars(start_date, n):
    """生成 n 根连续日K，date/open/high/low/close/volume"""
    dates = pd.bdate_range(start=start_date, periods=n)
    return pd.DataFrame({
        'date': dates,
        'open': range(100, 100 + n),
        'high': range(110, 110 + n),
        'low': range(90, 90 + n),
        'close': range(105, 105 + n),
        'volume': [1000 + i * 10 for i in range(n)],
    })


def _get_meta(repo, code, period):
    conn = repo._get_conn()
    cur = conn.execute(
        'SELECT rows, first_date, last_date FROM kline_meta WHERE code=? AND period=?',
        (code, period)
    )
    row = cur.fetchone()
    conn.close()
    return row


def test_full_then_incremental():
    """全量写入 ~100 根 → meta.rows~=100；再增量写入 1 根 → rows 不降，last_date 更新"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SqliteRepo(db_path=Path(tmp) / 'test.db')
        code, period = 'sh999999', 'daily'

        # 全量 100 根
        df_full = _make_bars('2024-01-02', 100)
        repo.save_kline(code, period, df_full)

        rows, _, last_date = _get_meta(repo, code, period)
        assert rows == 100, f"全量后 rows 应为 100，实际 {rows}"
        expected_last = df_full['date'].max().strftime('%Y-%m-%d')
        assert last_date == expected_last, f"全量后 last_date 应为 {expected_last}，实际 {last_date}"

        # 增量 1 根（新一天）
        new_day = pd.Timestamp(expected_last) + timedelta(days=1)
        # 跳过周末
        while new_day.weekday() >= 5:
            new_day += timedelta(days=1)
        df_inc = _make_bars(new_day, 1)
        repo.save_kline(code, period, df_inc)

        rows2, _, last_date2 = _get_meta(repo, code, period)
        assert rows2 >= 100, f"增量后 rows 应 >= 100，实际 {rows2}"
        assert rows2 == 101, f"增量后 rows 应为 101，实际 {rows2}"
        assert last_date2 == new_day.strftime('%Y-%m-%d'), \
            f"增量后 last_date 应为 {new_day.strftime('%Y-%m-%d')}，实际 {last_date2}"


def test_empty_df_noop():
    """空 DataFrame 不应写入 meta"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SqliteRepo(db_path=Path(tmp) / 'test.db')
        code, period = 'sh999999', 'daily'

        df_empty = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        repo.save_kline(code, period, df_empty)

        meta = _get_meta(repo, code, period)
        assert meta is None, "空 df 不应写入 meta"


def test_overwrite_same_day_no_row_inflation():
    """同一根 bar 重复写入不应让 rows 膨胀"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SqliteRepo(db_path=Path(tmp) / 'test.db')
        code, period = 'sh999999', 'daily'

        df = _make_bars('2024-01-02', 10)
        repo.save_kline(code, period, df)
        repo.save_kline(code, period, df)  # 完全重复写入
        repo.save_kline(code, period, df)

        rows, _, _ = _get_meta(repo, code, period)
        assert rows == 10, f"重复写入后 rows 应为 10，实际 {rows}"
