# -*- coding: utf-8 -*-
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(str(ROOT / "data" / "kline.db"))
lag = conn.execute(
    """
    SELECT COUNT(1) FROM (
      SELECT code FROM kline WHERE period='daily' AND LENGTH(code)=6
      GROUP BY code
      HAVING REPLACE(COALESCE(MAX(date),''),'-','') < '20260717'
    )
    """
).fetchone()[0]
total = conn.execute(
    "SELECT COUNT(DISTINCT code) FROM kline WHERE period='daily' AND LENGTH(code)=6"
).fetchone()[0]
print(f"fresh_ge_0717={total - lag}/{total} lagging={lag}")
st = json.loads((ROOT / "data" / "update_status.json").read_text(encoding="utf-8"))
boards = st.get("boards", {})
ok = sum(1 for v in boards.values() if v.get("status") == "success")
fail = sum(1 for v in boards.values() if v.get("status") == "failed")
print(f"boards success={ok} failed={fail} total={len(boards)}")
conn.close()
