#!/usr/bin/env python
"""QMT 个股日K线盘后增量更新脚本

职责：交易日收盘后，通过 QMT xtdata 拉取 SQLite 中已有个股的最新日K线，
      INSERT OR REPLACE 写入 kline.db，供面板次日开盘直接读取。

分工原则：
  - 盘后日K  → 本脚本（QMT，分钟级响应）
  - 东财板块  → scripts/update_boards.py（tushare dc_index/dc_daily/dc_member）
  - 日内分时  → 面板搜索时 kline_service.py 实时读 QMT（不在此脚本范围）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import json
import time
import logging
import sqlite3
from datetime import datetime, timedelta

# QMT 必须通过 xtdata 读取
from xtquant import xtdata

# ─── 配置 ───────────────────────────────────────────────
DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'kline.db'
LOG_PATH = Path(__file__).resolve().parent.parent / 'data' / 'update_logs'
LOG_PATH.mkdir(parents=True, exist_ok=True)

QMT_PORT = 58610
INSERT_SQL = """INSERT OR REPLACE INTO kline
                 (code, period, date, open, high, low, close, volume, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)"""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH / f'qmt_daily_{datetime.now().strftime("%Y%m%d_%H%M")}.log',
                           encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('qmt_daily')

# ─── QMT 工具函数 ───────────────────────────────────────

def qmt_connect() -> bool:
    try:
        xtdata.connect(port=QMT_PORT)
        xtdata.enable_hello = False
        logger.info(f"QMT 连接成功 (127.0.0.1:{QMT_PORT})")
        return True
    except Exception as e:
        logger.error(f"QMT 连接失败: {e}")
        return False


def to_qmt_code(code: str) -> str:
    """内部代码(如 000001) → QMT 代码(如 000001.SZ)"""
    code = str(code).zfill(6)
    if code.startswith(('6', '9')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


def from_qmt_ts(ts_val) -> str:
    """QMT time 字段(毫秒时间戳) → YYYY-MM-DD"""
    if isinstance(ts_val, (int, float)) and ts_val > 1e12:
        return datetime.fromtimestamp(ts_val / 1000).strftime('%Y-%m-%d')
    s = str(ts_val)
    if len(s) == 8 and s.isdigit():
        return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return s[:10]


def fetch_stock_kline(code: str, start_date: str) -> list:
    """
    通过 QMT 读取单只股票从 start_date 至今的日K线。
    start_date 格式: YYYYMMDD
    返回 list of dict with keys: date(YYYYMMDD), open, high, low, close, volume
    """
    qmt_code = to_qmt_code(code)
    try:
        xtdata.download_history_data(qmt_code, period='1d',
                                     start_time=start_date,
                                     end_time=datetime.now().strftime('%Y%m%d'))
    except Exception as e:
        logger.warning(f"  download 失败: {e}")

    data = xtdata.get_local_data(
        field_list=['time', 'open', 'high', 'low', 'close', 'volume'],
        stock_list=[qmt_code], period='1d',
        start_time=start_date,
        end_time=datetime.now().strftime('%Y%m%d'), count=0
    )

    if not isinstance(data, dict) or qmt_code not in data:
        return []

    df = data[qmt_code]
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        d = from_qmt_ts(row.get('time', 0))  # YYYY-MM-DD
        result.append({
            'date': d,
            'open': float(row.get('open', 0) or 0),
            'high': float(row.get('high', 0) or 0),
            'low': float(row.get('low', 0) or 0),
            'close': float(row.get('close', 0) or 0),
            'volume': float(row.get('volume', 0) or 0),
        })
    return result


# ─── 主流程 ─────────────────────────────────────────────

def update_stock(code: str, conn: sqlite3.Connection, max_retries: int = 3) -> tuple:
    """
    增量更新单只股票：只写缺失日期，若有新增则返回 (True, 新增条数)
    """
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(date),'19900101') FROM kline WHERE code=? AND period='daily'", (code,))
    max_date = cur.fetchone()[0]
    max_norm = (max_date or '').replace('-', '')
    if len(max_norm) != 8 or not max_norm.isdigit():
        max_norm = '19900101'
    next_start = (datetime.strptime(max_norm, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')

    if next_start > datetime.now().strftime('%Y%m%d'):
        return True, 0

    rows = []
    for attempt in range(max_retries):
        try:
            rows = fetch_stock_kline(code, next_start)
            break
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            logger.error(f"[{code}] 读取失败: {e}")
            return False, 0

    if not rows:
        return True, 0

    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    batch = [(code, 'daily', r['date'], r['open'], r['high'], r['low'], r['close'], r['volume'], now_str)
              for r in rows]

    cur.executemany(INSERT_SQL, batch)
    conn.commit()

    logger.debug(f"[{code}] 写入 {len(batch)} 条日K ({batch[0][2]}~{batch[-1][2]})")
    return True, len(batch)


def main():
    logger.info("=" * 60)
    logger.info("QMT 个股日K线盘后增量更新开始")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    if not qmt_connect():
        logger.error("QMT 未就绪，退出")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. 获取 stock_ledger 中的所有个股
    cur.execute("SELECT code, name FROM stock_ledger WHERE LENGTH(code)=6 ORDER BY code")
    stocks = cur.fetchall()

    if not stocks:
        logger.info("stock_ledger 为空，跳过")
        conn.close()
        sys.exit(0)

    logger.info(f"待更新个股: {len(stocks)} 只")

    # 2. 过滤掉已经更新到今天或更晚的(周末节假日也会跑)
    today = datetime.now().strftime('%Y-%m-%d')
    today_norm = datetime.now().strftime('%Y%m%d')
    pending = []
    for code, name in stocks:
        cur.execute("SELECT MAX(date) FROM kline WHERE code=? AND period='daily'", (code,))
        max_date = cur.fetchone()[0]
        max_date_norm = (max_date or '').replace('-', '')
        if max_date is None or max_date_norm < today_norm:
            pending.append((code, name))
        # 最新日期在今天或之后，跳过

    logger.info(f"实际需要更新: {len(pending)} 只")

    if not pending:
        logger.info("所有个股已是最新，退出")
        conn.close()
        sys.exit(0)

    # 3. 逐个增量更新
    success = 0
    failed = 0
    skipped = 0
    total_new = 0
    start_ts = time.time()

    for i, (code, name) in enumerate(pending, 1):
        try:
            ok, n_new = update_stock(code, conn)
            if ok:
                if n_new > 0:
                    total_new += n_new
                    success += 1
                else:
                    skipped += 1
            else:
                failed += 1

            if i % 50 == 0:
                elapsed = time.time() - start_ts
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(pending) - i) / rate if rate > 0 else 0
                logger.info(f"进度: {i}/{len(pending)} 成功={success} 跳过={skipped} 失败={failed} "
                           f"新增={total_new}条 {rate:.1f}只/s ETA={eta:.0f}s")

        except Exception as e:
            logger.error(f"[{code}] 更新异常: {e}")
            failed += 1

        # QMT 批量读极快(0.3ms/只)，但下载(download_history_data)有小延迟
        time.sleep(0.1)

    elapsed = time.time() - start_ts
    logger.info(f"=== 更新完成 ===")
    logger.info(f"  成功: {success}  跳过(已是最新): {skipped}  失败: {failed}")
    logger.info(f"  新增记录: {total_new} 条")
    logger.info(f"  耗时: {elapsed:.1f}s  速率: {len(pending)/elapsed:.1f}只/s")

    # 4. 更新 kline_meta
    try:
        cur.execute("""INSERT OR REPLACE INTO kline_meta (code, period, rows, first_date, last_date, updated_at)
                       SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ?
                       FROM kline WHERE code IN ({}) AND period='daily'
                       GROUP BY code""".format(','.join(['?'] * len(pending))),
                    [datetime.now().strftime('%Y-%m-%d %H:%M:%S')] +
                    [c for c, _ in pending])
        conn.commit()
    except Exception:
        pass  # kline_meta 非核心表，写入失败不影响主流程

    conn.close()
    logger.info("=== 全部完成 ===")


if __name__ == '__main__':
    main()
