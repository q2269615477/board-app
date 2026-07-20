# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "data" / "kline.db"
c = sqlite3.connect(str(db))
rows = c.execute(
    "select code, max(date), count(*) from kline "
    "where code like 'sh000%' and period='daily' "
    "group by code order by code limit 20"
).fetchall()
for r in rows:
    print(r)
print(
    "sh000001",
    c.execute(
        "select max(date), count(*) from kline where code='sh000001' and period='daily'"
    ).fetchone(),
)
