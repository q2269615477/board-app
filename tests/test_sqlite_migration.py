"""test_sqlite_migration.py — SQLite kline 表迁移测试

验证旧库（没有 updated_at 列）在初始化后自动补上 updated_at。
"""
import os
import sqlite3
import tempfile
import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')


class TestKlineMigration:
    """测试 kline 表的 updated_at 列迁移"""

    def test_new_db_has_updated_at(self, tmp_path):
        """新建的 kline 表应包含 updated_at 列"""
        from data.sqlite_repo import SqliteRepo

        db_path = str(tmp_path / 'test_new.db')
        repo = SqliteRepo(db_path)

        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute('PRAGMA table_info(kline)').fetchall()}
        conn.close()
        assert 'updated_at' in cols

    def test_old_db_migrated(self, tmp_path):
        """旧库（没有 updated_at）在初始化后应自动补上 updated_at"""
        db_path = str(tmp_path / 'test_old.db')

        # 手动创建一个没有 updated_at 的旧表
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kline (
                code TEXT NOT NULL,
                period TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                pct_chg REAL,
                PRIMARY KEY (code, period, date)
            )
        """)
        conn.execute("INSERT INTO kline (code, period, date, close) VALUES ('sh000001', 'daily', '2024-01-01', 3000)")
        conn.commit()

        # 确认旧表没有 updated_at
        cols_before = {row[1] for row in conn.execute('PRAGMA table_info(kline)').fetchall()}
        assert 'updated_at' not in cols_before
        conn.close()

        # 初始化 repo，触发迁移
        from data.sqlite_repo import SqliteRepo
        repo = SqliteRepo(db_path)

        # 验证迁移后有了 updated_at
        conn = sqlite3.connect(db_path)
        cols_after = {row[1] for row in conn.execute('PRAGMA table_info(kline)').fetchall()}
        conn.close()
        assert 'updated_at' in cols_after
