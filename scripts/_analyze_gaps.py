# -*- coding: utf-8 -*-
import json
import sqlite3
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import SQLITE_PATH, DATA_DIR

st = json.loads((DATA_DIR / "update_status.json").read_text(encoding="utf-8"))
boards = st.get("boards") or {}
errs = Counter()
for k, v in boards.items():
    if isinstance(v, dict) and v.get("status") == "failed":
        errs[(v.get("error") or "unknown")[:100]] += 1
print("top board errors:")
for e, n in errs.most_common(12):
    print(f"  {n}: {e}")

conn = sqlite3.connect(str(SQLITE_PATH))
print("ledger", conn.execute("SELECT * FROM stock_ledger LIMIT 10").fetchall())
print("ledger_n", conn.execute("SELECT COUNT(1) FROM stock_ledger").fetchone()[0])

stale = conn.execute(
    """
    SELECT COUNT(1) FROM (
      SELECT code FROM kline
      WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
      GROUP BY code HAVING MAX(date) < '2026-07-10'
    )
    """
).fetchone()[0]
fresh = conn.execute(
    """
    SELECT COUNT(1) FROM (
      SELECT code FROM kline
      WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
      GROUP BY code HAVING MAX(date) >= '2026-07-10'
    )
    """
).fetchone()[0]
print("stocks stale_before_0710", stale, "fresh_ge_0710", fresh)

# max dates distribution
dist = conn.execute(
    """
    SELECT MAX(date) as d, COUNT(1) as n FROM (
      SELECT code, MAX(date) as md FROM kline
      WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9]*'
      GROUP BY code
    ) GROUP BY d ORDER BY d DESC LIMIT 15
    """
).fetchall()
print("max_date_dist", dist)
conn.close()
