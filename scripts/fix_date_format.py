#!/usr/bin/env python
"""
修复 SQLite kline 表中日期格式不一致问题。
根因：多个写入路径使用了 YYYY-MM-DD 和 YYYYMMDD 两种格式，导致：
  1. 部分 code+period+date 组合有两种格式记录并存（重复数据）
  2. 图表读取时可能拿到不同格式的同一日期，导致显示错误
修复策略：
  1. 对 day/month/weekly 记录，如果某个日期同时存在 YYYY-MM-DD 和 YYYYMMDD 两种格式，
     删除 YYYYMMDD 格式的行（保留 YYYY-MM-DD 格式的行，因为它是更晚写入的标准化格式）
  2. 对剩余的 YYYYMMDD 格式记录，转换为 YYYY-MM-DD
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'kline.db'

def fix():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Step 1: 对 daily/weekly/monthly 记录，找出在同 code+period+normalize_date 下有两种格式的重复记录
    # 注意：只转换纯8位数字的date（跳过Unix时间戳）
    cur.execute("""
        DELETE FROM kline
        WHERE LENGTH(date) = 8 AND date NOT LIKE '%-%'
        AND period IN ('daily', 'weekly', 'monthly')
        AND EXISTS (
            SELECT 1 FROM kline k2
            WHERE k2.code = kline.code
            AND k2.period = kline.period
            AND k2.date LIKE '____-__-__'
            AND REPLACE(k2.date,'-','') = kline.date
        )
    """)
    print(f"删除重复格式（保留YYYY-MM-DD）: {cur.rowcount}")

    # Step 2: 剩余无连字符记录全部转换（仅8位纯数字）
    cur.execute("""
        UPDATE kline
        SET date = SUBSTR(date,1,4) || '-' || SUBSTR(date,5,2) || '-' || SUBSTR(date,7,2)
        WHERE LENGTH(date) = 8 AND date NOT LIKE '%-%'
    """)
    print(f"转换格式: {cur.rowcount}")
    conn.commit()

    # 验证
    cur.execute("SELECT COUNT(*) FROM kline WHERE date NOT LIKE '____-__-__'")
    bad = cur.fetchone()[0]
    print(f"剩余格式异常: {bad}")

    cur.execute("SELECT COUNT(*) FROM kline")
    print(f"总记录数: {cur.fetchone()[0]}")

    # 显示 date 分布
    cur.execute("""
        SELECT DISTINCT SUBSTR(date,1,7) as month, COUNT(*) as cnt
        FROM kline GROUP BY month ORDER BY month DESC LIMIT 10
    """)
    print("\n=== 月度分布 ===")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}条")

    conn.close()
    print("\n✅ 修复完成")

if __name__ == '__main__':
    fix()
