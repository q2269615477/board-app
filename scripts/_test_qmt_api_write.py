# -*- coding: utf-8 -*-
"""qmt_api 通道端到端验证 — 取日线 → 写 SQLite

用法：
  venv\Scripts\python.exe scripts\_test_qmt_api_write.py

流程：
  1. 通过 qmt_api（公式 RPC 58600）获取指数日线
  2. 调用 _db_write_kline 写入 SQLite
  3. 验证写入行数与最新日期
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import QMT_PYTHON_PATH, QMT_DIR
from data_loader import _db_write_kline

TARGETS = [
    ("sh000001", "000001.SH", "上证指数"),
    ("sh000300", "000300.SH", "沪深300"),
    ("sh000688", "000688.SH", "科创50"),
    ("sh000016", "000016.SH", "上证50"),
    ("sh000852", "000852.SH", "中证1000"),
    ("sh000853", "000853.SH", "中证2000"),
    ("sh000985", "000985.SH", "中证全指"),
    ("600519", "600519.SH", "贵州茅台"),
]

SCRIPT_TEMPLATE = r'''
import json, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(sys.executable)) + '/lib/site-packages')
import qmt_api.api as api
import pandas as pd

codes = {codes!r}
results = {{}}
for c in codes:
    try:
        md = api.get_market_data(
            ['open','high','low','close','volume'],
            [c], '{start}', '{end}', '1d', 'none', {count}
        )
        if md is not None and isinstance(md, pd.DataFrame) and len(md) > 0:
            rows = []
            for idx, row in md.iterrows():
                ds = str(idx)[:10].replace('-', '')
                vol = int(float(row.get('volume', 0))) if not pd.isna(row.get('volume', 0)) else 0
                rows.append({{
                    'date': ds,
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': vol
                }})
            results[c] = rows
        else:
            results[c] = []
    except Exception as e:
        results[c] = {{'err': str(e)[:200]}}
print(json.dumps(results, ensure_ascii=False))
'''


def fetch_all() -> dict:
    probe_script = SCRIPT_TEMPLATE.format(
        codes=[p[1] for p in TARGETS],
        start="20260601",
        end="20260717",
        count=-1,
    )
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe_script],
        capture_output=True,
        timeout=120,
        cwd=QMT_DIR,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="ignore").strip()
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    print("parse fail, stdout:", stdout[-500:])
    return {}


def main():
    print("=" * 60)
    print("qmt_api 公式 RPC 通道 — 端到端批量取数与写库")
    print("=" * 60)

    raw = fetch_all()
    if not raw:
        print("❌ 全部获取失败")
        return

    total_written = 0
    for panel_code, qmt_code, name in TARGETS:
        data = raw.get(qmt_code)
        if not data or isinstance(data, dict):
            print(f"  {panel_code} ({name}): 失败 {data.get('err', '无数据')}")
            continue
        df = pd.DataFrame(data)
        if df.empty:
            print(f"  {panel_code} ({name}): 空")
            continue
        n = _db_write_kline(panel_code, "daily", df)
        last_date = df["date"].iloc[-1]
        total_written += n
        print(
            f"  ✓ {panel_code} ({name}) → {qmt_code}: "
            f"取 {len(df)} 条, 写 {n} 条, 最新 {last_date}"
        )

    # 验证
    print()
    print("=" * 60)
    import sqlite3
    c = sqlite3.connect(str(ROOT / "data" / "kline.db"))
    placeholders = ",".join("?" * len(TARGETS))
    codes = [p[0] for p in TARGETS]
    rows = c.execute(
        f"select code, max(date), count(*) from kline "
        f"where code in ({placeholders}) and period='daily' group by code order by code",
        codes,
    )
    for r in rows:
        print(f"  DB {r[0]}: max_date={r[1]} rows={r[2]}")
    c.close()
    print(f"总计写入: {total_written} 条")
    print("done")


if __name__ == "__main__":
    main()
