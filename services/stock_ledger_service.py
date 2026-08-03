"""
services/stock_ledger_service.py — 个股台账（SQLite 存储）

职责：
 - 管理 stock_ledger 表的 CRUD
 - 检查个股是否已缓存
 - 从 kline 表重建台账

从 data_update_manager.py 中抽取，保持行为不变。
"""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger('data_update')

# 台账数据库路径
LEDGER_DB = str(Path('data') / 'kline.db')


def get_ledger_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取台账数据库连接。

    Args:
        db_path: 数据库路径，为 None 时使用默认 LEDGER_DB。

    Returns:
        sqlite3.Connection（row_factory 设为 Row）
    """
    conn = sqlite3.connect(db_path or LEDGER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def is_stock_cached(code: str, db_path: Optional[str] = None) -> bool:
    """检查个股是否已在台账中。

    Args:
        code: 股票代码（6 位数字）
        db_path: 数据库路径

    Returns:
        True 如果已缓存
    """
    conn = get_ledger_conn(db_path)
    try:
        cur = conn.execute('SELECT 1 FROM stock_ledger WHERE code=?', (code,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def add_stock_to_ledger(code: str, name: str = '', db_path: Optional[str] = None):
    """添加或更新个股到台账。

    使用 INSERT OR REPLACE，保留首次缓存时间。

    Args:
        code: 股票代码
        name: 股票名称
        db_path: 数据库路径
    """
    conn = get_ledger_conn(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_ledger (code, name, first_cached, last_updated) "
            "VALUES (?, ?, COALESCE((SELECT first_cached FROM stock_ledger WHERE code=?), "
            "datetime('now','localtime')), datetime('now','localtime'))",
            (code, name, code))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[台账] 已记录个股 {code}({name})")


def get_all_cached_stocks(db_path: Optional[str] = None) -> List[str]:
    """获取所有已缓存的个股代码列表。

    Args:
        db_path: 数据库路径

    Returns:
        股票代码列表（按代码排序）
    """
    conn = get_ledger_conn(db_path)
    try:
        cur = conn.execute('SELECT code FROM stock_ledger ORDER BY code')
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def rebuild_stock_ledger_from_kline(min_rows: int = 1, db_path: Optional[str] = None) -> dict:
    """从 kline 表重建 stock_ledger（仅 6 位 A 股代码）。

    历史问题：台账仅 4 条时 qmt_update_all_stocks 几乎不跑，库内 5000+ 只停滞。

    Args:
        min_rows: kline 表中至少有多少行才纳入台账
        db_path: 数据库路径

    Returns:
        {'codes': inserted_count, 'min_rows': min_rows}
    """
    conn = get_ledger_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT code, COUNT(1) AS n, MAX(date) AS last_d
            FROM kline
            WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            GROUP BY code
            HAVING n >= ?
            ORDER BY code
            """,
            (int(min_rows),),
        )
        rows = cur.fetchall()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        inserted = 0
        for code, n, last_d in rows:
            cur.execute(
                "INSERT OR REPLACE INTO stock_ledger (code, name, first_cached, last_updated) "
                "VALUES (?, COALESCE((SELECT name FROM stock_ledger WHERE code=?), ''), "
                "COALESCE((SELECT first_cached FROM stock_ledger WHERE code=?), ?), ?)",
                (code, code, code, now, now),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[台账] 自 kline 重建 {inserted} 只个股 (min_rows={min_rows})")
    return {'codes': inserted, 'min_rows': min_rows}
