# -*- coding: utf-8 -*-
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import SQLITE_PATH
from data.qmt_client import get_qmt_client
from data_update_manager import _to_qmt_code

conn = sqlite3.connect(str(SQLITE_PATH))
# oldest 10 pure stocks
rows = conn.execute(
    """
    SELECT code, MAX(date) as md, COUNT(1) as n FROM kline
    WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
    GROUP BY code ORDER BY md ASC LIMIT 10
    """
).fetchall()
print("oldest", rows)

codes = [r[0] for r in rows[:5]]
qcs = [_to_qmt_code(c) for c in codes]
print("qcs", qcs)
c = get_qmt_client()
batch = c.get_daily_batch(qcs, start="20260601", count=-1)
for qc, df in batch.items():
    print(qc, len(df), df.iloc[-1].to_dict() if len(df) else None)

# single
for code, qc in zip(codes, qcs):
    df = c.get_daily(qc, start="20260601", count=-1)
    print("single", code, None if df is None else (len(df), str(df.iloc[-1]["date"]), float(df.iloc[-1]["close"])))

conn.close()
