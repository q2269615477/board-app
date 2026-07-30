"""
sqlite_repo.py — SQLite数据访问层
封装所有SQLite操作（K线缓存、元数据、台账）
"""
import sqlite3
import json
import logging
import math
from pathlib import Path
from typing import Optional

import pandas as pd

from core.config import Config

logger = logging.getLogger('sqlite')


def _finite_float(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def normalize_date(date_str) -> str:
    """
    统一的日期格式标准化函数。
    所有写入 kline 表的 date 字段必须经过此函数处理。

    输入支持:
      - 20260108 (int 或 str)
      - "2026-01-08"
      - "2026-01-08 00:00:00" (pd.Timestamp str)
      - "2026-01-08T00:00:00"
      - pd.Timestamp 对象

    输出: "2026-01-08" (YYYY-MM-DD)
    """
    if date_str is None or pd.isna(date_str):
        return ''
    s = str(date_str).strip()
    if not s:
        return ''
    # 去掉 T 后面的内容
    if 'T' in s:
        s = s.split('T')[0]
    # 去掉空格后的时间部分
    if ' ' in s and len(s) > 10:
        s = s.split(' ')[0]
    # YYYYMMDD → YYYY-MM-DD
    if len(s) == 8 and s.isdigit():
        s = f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return s


class SqliteRepo:
    """SQLite数据访问对象（线程安全）"""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Config.SQLITE_PATH
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_tables(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kline (
                    code TEXT NOT NULL,
                    period TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    updated_at TEXT,
                    PRIMARY KEY (code, period, date)
                );
                CREATE INDEX IF NOT EXISTS idx_kline_code_period ON kline(code, period);
                CREATE INDEX IF NOT EXISTS idx_kline_date ON kline(date);
                CREATE TABLE IF NOT EXISTS kline_meta (
                    code TEXT NOT NULL,
                    period TEXT NOT NULL,
                    rows INTEGER, first_date TEXT, last_date TEXT, updated_at TEXT,
                    PRIMARY KEY (code, period)
                );
                CREATE TABLE IF NOT EXISTS stock_ledger (
                    code TEXT PRIMARY KEY,
                    name TEXT, first_cached TEXT, last_updated TEXT
                );
                CREATE TABLE IF NOT EXISTS board_cache (
                    code TEXT PRIMARY KEY,
                    name TEXT, board_type TEXT, data_json TEXT, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mkt_cap (
                    code TEXT PRIMARY KEY,
                    mkt_cap REAL, updated_at TEXT
                );
            """)
            # Migration: add updated_at column if missing
            cols = {row[1] for row in conn.execute("PRAGMA table_info(kline)").fetchall()}
            if cols and 'updated_at' not in cols:
                conn.execute("ALTER TABLE kline ADD COLUMN updated_at TEXT")
        finally:
            conn.close()

    # ---- K线读写 ----

    def read_kline(self, code: str, period: str) -> Optional[pd.DataFrame]:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                'SELECT date, open, high, low, close, volume '
                'FROM kline WHERE code=? AND period=? ORDER BY date',
                (code, period)
            )
            rows = cur.fetchall()
            if rows:
                return pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        finally:
            conn.close()
        return None

    def save_kline(self, code: str, period: str, df: pd.DataFrame, name: str = '', data_type: str = ''):
        if df is None or df.empty:
            return
        conn = self._get_conn()
        try:
            conn.execute('BEGIN')
            now_str = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
            records = []
            for _, row in df.iterrows():
                date_raw = normalize_date(row.get('date', ''))
                if not date_raw:
                    continue
                open_v = _finite_float(row.get('open'))
                high_v = _finite_float(row.get('high'))
                low_v = _finite_float(row.get('low'))
                close_v = _finite_float(row.get('close'))
                if None in (open_v, high_v, low_v, close_v):
                    continue
                volume_v = _finite_float(row.get('volume', 0)) or 0
                records.append((
                    code, period, date_raw,
                    open_v,
                    high_v,
                    low_v,
                    close_v,
                    int(volume_v),
                    now_str
                ))
            if not records:
                conn.rollback()
                return
            conn.executemany(
                'INSERT OR REPLACE INTO kline '
                '(code, period, date, open, high, low, close, volume, updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?)', records
            )
            # Check existing meta to avoid row count regression
            existing = conn.execute(
                'SELECT rows FROM kline_meta WHERE code=? AND period=?', (code, period)
            ).fetchone()
            existing_rows = existing[0] if existing else 0
            # Count actual rows after insert to get the true total
            actual_count = conn.execute(
                'SELECT COUNT(*) FROM kline WHERE code=? AND period=?', (code, period)
            ).fetchone()[0]
            actual_last_date = conn.execute(
                'SELECT MAX(date) FROM kline WHERE code=? AND period=?', (code, period)
            ).fetchone()[0]
            new_rows = max(actual_count, existing_rows)  # non-retrogression
            last_date = normalize_date(actual_last_date or '')
            conn.execute(
                'INSERT OR REPLACE INTO kline_meta '
                '(code, period, rows, last_date, updated_at) VALUES (?,?,?,?,?)',
                (code, period, new_rows, last_date, now_str)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.warning(f"[SQLite] save_kline {code}: {e}")
        finally:
            conn.close()

    def has_kline_data(self, code: str, period: str) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                'SELECT 1 FROM kline WHERE code=? AND period=? LIMIT 1',
                (code, period)
            )
            return cur.fetchone() is not None
        finally:
            conn.close()

    def delete_kline(self, code: str, period: str = None) -> int:
        """Delete kline rows for a code, optionally filtered by period. Returns count deleted."""
        conn = self._get_conn()
        try:
            if period:
                cur = conn.execute(
                    'DELETE FROM kline WHERE code=? AND period=?', (code, period))
            else:
                cur = conn.execute('DELETE FROM kline WHERE code=?', (code,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def clear_all(self) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute('SELECT COUNT(*) FROM kline')
            count = cur.fetchone()[0]
            conn.execute('DELETE FROM kline')
            conn.execute('DELETE FROM kline_meta')
            conn.commit()
            return count
        finally:
            conn.close()

    # ---- 台账管理 ----

    def record_stock_cache(self, code: str, name: str, data_type: str = 'stock'):
        conn = self._get_conn()
        try:
            today = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                'INSERT OR REPLACE INTO stock_ledger (code, name, first_cached, last_updated) '
                'VALUES (?,?,COALESCE((SELECT first_cached FROM stock_ledger WHERE code=?),?),?)',
                (code, name, code, today, today)
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_cached_stocks(self) -> list:
        conn = self._get_conn()
        try:
            cur = conn.execute('SELECT code FROM stock_ledger')
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()


_sqlite_repo: Optional[SqliteRepo] = None


def get_sqlite_repo() -> SqliteRepo:
    global _sqlite_repo
    if _sqlite_repo is None:
        _sqlite_repo = SqliteRepo()
    return _sqlite_repo
