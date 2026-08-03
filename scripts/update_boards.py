#!/usr/bin/env python
"""东财板块数据增量更新脚本
使用 tushare dc_index/dc_daily/dc_member 更新 SQLite 板块数据
增量策略：仅补充缺失日期的K线数据
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.env_bootstrap import ensure_tushare_token
ensure_tushare_token()

import time
import json
import logging
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

from data_loader import get_tushare_pro

pro = get_tushare_pro()
if pro is None:
    print('错误: TUSHARE_TOKEN 未配置，无法使用 Tushare 数据源', file=sys.stderr)
    sys.exit(1)

DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'kline.db'
LOG_PATH = Path(__file__).resolve().parent.parent / 'data' / 'update_logs'
LOG_PATH.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH / f'board_update_{datetime.now().strftime("%Y%m%d_%H%M")}.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('board_update')

INSERT_SQL = """INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)"""
CONSTITUENT_SQL = """INSERT OR REPLACE INTO board_constituents (code, date, constituent_code, constituent_name, updated_at)
                       VALUES (?,?,?,?,?)"""


def get_existing_dates(code: str, conn: sqlite3.Connection) -> set:
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT date FROM kline WHERE code=?", (code,))
    return set(row[0] for row in cur.fetchall())


def update_board_kline(code: str, name: str, conn: sqlite3.Connection, max_retries: int = 3) -> bool:
    """增量更新单只板块的K线数据"""
    existing = get_existing_dates(code, conn)

    if existing:
        latest = max(existing)
        latest_date = datetime.strptime(latest, '%Y-%m-%d')
        if latest_date >= datetime.now() - timedelta(days=1):
            logger.debug(f"[{code}] {name} 已是最新，跳过")
            return True
        start = (latest_date + timedelta(days=1)).strftime('%Y%m%d')
    else:
        start = '20000101'

    for attempt in range(max_retries):
        try:
            df = pro.dc_daily(ts_code=f'{code}.DC', start_date=start,
                              end_date=datetime.now().strftime('%Y%m%d'))
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning(f"[{code}] 重试({attempt+1}/{max_retries})：{e}，{wait}s后重试")
                time.sleep(wait)
            else:
                logger.error(f"[{code}] {name} dc_daily 失败：{e}")
                return False

    if df is None or df.empty:
        logger.debug(f"[{code}] {name} 无数据返回")
        return True

    new_rows = []
    for _, row in df.iterrows():
        d = row['trade_date']
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        if date_str in existing:
            continue
        new_rows.append((
            code, 'daily',
            date_str,
            float(row.get('open', 0) or 0),
            float(row.get('high', 0) or 0),
            float(row.get('low', 0) or 0),
            float(row.get('close', 0) or 0),
            float(row.get('vol', 0) or 0),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))

    if not new_rows:
        logger.debug(f"[{code}] {name} 无新增数据")
        return True

    cur = conn.cursor()
    cur.executemany(INSERT_SQL, new_rows)
    conn.commit()
    logger.info(f"[{code}] {name} 写入 {len(new_rows)} 条K线 ({new_rows[0][2]}~{new_rows[-1][2]})")
    return True


def update_board_constituents(code: str, name: str, dates: list, conn: sqlite3.Connection):
    """增量更新单只板块的成分股"""
    cur = conn.cursor()
    for date_str in dates:
        d = date_str.replace('-', '')
        try:
            df = pro.dc_member(ts_code=f'{code}.DC', trade_date=d)
            if df is None or df.empty:
                continue
            records = [(
                code, date_str,
                row['con_code'],
                row['name'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ) for _, row in df.iterrows() if row.get('con_code')]
            if records:
                cur.executemany(CONSTITUENT_SQL, records)
                logger.debug(f"[{code}] {date_str} 成分股 {len(records)}只")
        except Exception as e:
            logger.warning(f"[{code}] {date_str} 成分股失败：{e}")
            time.sleep(1)
    conn.commit()


def main():
    logger.info("=" * 60)
    logger.info("东财板块数据增量更新开始")
    logger.info("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS board_constituents (
        code TEXT,
        date TEXT,
        constituent_code TEXT,
        constituent_name TEXT,
        updated_at TEXT,
        PRIMARY KEY (code, date, constituent_code)
    )""")
    conn.commit()

    # 获取交易日
    trade_cal = pro.trade_cal(exchange='SSE', start_date='20250101',
                              end_date=datetime.now().strftime('%Y%m%d'),
                              is_open='1')
    trading_dates = set(trade_cal['cal_date'].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}").values)
    logger.info(f"交易日总数: {len(trading_dates)}")

    # 最近5个交易日
    recent_trading = sorted([d for d in trading_dates if d >= '2026-06-29'])[:5]
    if not recent_trading:
        # 如果近期没有，取最新的5个
        recent_trading = sorted(trading_dates)[-5:]
    logger.info(f"需要补全的日期: {recent_trading}")

    # 获取概念板块列表
    logger.info("获取概念板块列表...")
    concept_boards = pro.dc_index(idx_type='概念板块')
    industry_boards = pro.dc_index(idx_type='行业板块')

    all_boards = {}
    for df, btype in [(concept_boards, '概念'), (industry_boards, '行业')]:
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = row.get('ts_code', '').replace('.DC', '')
                if code and code.startswith('BK'):
                    all_boards[code] = {
                        'name': row.get('name', code),
                        'type': btype,
                        'pct_change': row.get('pct_change', 0)
                    }

    logger.info(f"板块总计: {len(all_boards)} 只（概念+行业）")

    # K线增量更新
    success = 0
    failed = 0
    start_time = time.time()
    for i, (code, info) in enumerate(all_boards.items(), 1):
        try:
            ok = update_board_kline(code, info['name'], conn)
            if ok:
                success += 1
            else:
                failed += 1
            if i % 100 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(all_boards) - i) / rate if rate > 0 else 0
                logger.info(f"K线进度: {i}/{len(all_boards)} ({rate:.1f}只/s, ETA {eta:.0f}s) 成功={success} 失败={failed}")
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"[{code}] 更新异常：{e}")
            failed += 1

    logger.info(f"=== K线更新完成 成功={success} 失败={failed} 耗时 {time.time()-start_time:.0f}s ===")

    # 成分股更新
    logger.info("更新成分股...")
    constituent_count = 0
    for i, (code, info) in enumerate(all_boards.items(), 1):
        try:
            update_board_constituents(code, info['name'], recent_trading, conn)
            constituent_count += 1
            if i % 100 == 0:
                logger.info(f"成分股进度: {i}/{len(all_boards)}")
            time.sleep(0.2)
        except Exception as e:
            logger.error(f"[{code}] 成分股更新异常：{e}")

    logger.info(f"=== 成分股更新完成 {constituent_count} 只 ===")

    # board_cache 更新
    logger.info("更新 board_cache ...")
    for code, info in all_boards.items():
        cur.execute("""INSERT OR REPLACE INTO board_cache (code, name, board_type, data_json, updated_at)
                        VALUES (?,?,?,?,?)""",
                    (code, info['name'], info['type'],
                     json.dumps({'pct_change': info['pct_change']}),
                     datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    logger.info(f"=== board_cache 更新完成 {len(all_boards)} 只 ===")

    # 最终统计
    cur.execute("SELECT MAX(date), COUNT(DISTINCT code) FROM kline WHERE code LIKE 'BK%'")
    latest, count = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM board_constituents WHERE date >= '2026-06-29'")
    const_count = cur.fetchone()[0]
    logger.info(f"最终状态: 最新日期={latest}, BK板块数={count}, 成分股记录={const_count}")

    conn.close()
    logger.info("=== 全部更新完成 ===")


if __name__ == '__main__':
    main()
