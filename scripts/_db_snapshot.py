# -*- coding: utf-8 -*-
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import SQLITE_PATH

conn = sqlite3.connect(str(SQLITE_PATH))
sparse = conn.execute(
    """
    SELECT COUNT(1) FROM (
      SELECT code FROM kline
      WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
      GROUP BY code HAVING COUNT(1) < 120
    )
    """
).fetchone()[0]
dense = conn.execute(
    """
    SELECT COUNT(1) FROM (
      SELECT code FROM kline
      WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
      GROUP BY code HAVING COUNT(1) >= 120
    )
    """
).fetchone()[0]
print("sparse_lt120", sparse, "dense_ge120", dense)
print(
    "samples",
    conn.execute(
        """
        SELECT code, COUNT(1), MIN(date), MAX(date) FROM kline
        WHERE period='daily' AND code IN ('000001','600519','002729','sh000001')
        GROUP BY code
        """
    ).fetchall(),
)
print(
    "periods",
    conn.execute(
        "SELECT period, COUNT(DISTINCT code), COUNT(1) FROM kline GROUP BY period"
    ).fetchall(),
)
print("ledger", conn.execute("SELECT COUNT(1) FROM stock_ledger").fetchone()[0])
conn.close()
