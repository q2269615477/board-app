"""
qmt_sync.py - QMT → SQLite 数据同步工具
通过 QMT xtdata（端口58610）读取最新日线数据，写入 SQLite
"""
import os
import sys
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='[QMT] %(asctime)s %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('qmt_sync')

# ====== 连接 ======

_connected = False

def connect() -> bool:
    global _connected
    if _connected:
        return True
    try:
        from xtquant import xtdata
        xtdata.connect(port=58610)
        xtdata.enable_hello = False
        _connected = True
        logger.info("QMT xtdata 连接成功 (127.0.0.1:58610)")
        return True
    except Exception as e:
        logger.error(f"QMT 连接失败: {e}")
        _connected = False
        return False


def disconnect():
    global _connected
    _connected = False

# ====== 数据读取 ======

def get_kline(code: str, start_date: str = '20260601', end_date: str = '') -> list:
    """
    从 QMT 读取日线数据
    返回 [{date, open, high, low, close, volume}, ...]
    date 格式统一为 YYYY-MM-DD
    """
    if not connect():
        return []
    try:
        from xtquant import xtdata
        end = end_date or datetime.now().strftime('%Y%m%d')
        data = xtdata.get_local_data(
            field_list=['time', 'open', 'high', 'low', 'close', 'volume'],
            stock_list=[code],
            period='1d',
            start_time=start_date,
            end_time=end,
            count=0
        )
        if isinstance(data, dict) and code in data:
            df = data[code]
            if df is not None and not df.empty:
                result = []
                for _, row in df.iterrows():
                    ts = row.get('time', 0)
                    if isinstance(ts, (int, float)) and ts > 1e12:
                        date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                    else:
                        s = str(ts)
                        # YYYYMMDD → YYYY-MM-DD
                        if len(s) == 8 and s.isdigit():
                            date_str = f'{s[:4]}-{s[4:6]}-{s[6:8]}'
                        else:
                            date_str = s[:10]
                    result.append({
                        'date': date_str,
                        'open': float(row.get('open', 0)),
                        'high': float(row.get('high', 0)),
                        'low': float(row.get('low', 0)),
                        'close': float(row.get('close', 0)),
                        'volume': float(row.get('volume', 0)),
                    })
                return result
    except Exception as e:
        logger.warning(f"读取 {code} 失败: {e}")
    return []


def download_latest(code: str) -> bool:
    """触发 QMT 下载最新数据"""
    try:
        from xtquant import xtdata
        today = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        xtdata.download_history_data(code, period='1d', start_time=start, end_time=today)
        return True
    except Exception as e:
        logger.warning(f"下载 {code} 失败: {e}")
        return False


# ====== 代码映射 ======

def map_code_to_qmt(code: str, data_type: str) -> str:
    """将面板代码映射为 QMT 代码"""
    # 指数
    idx_map = {
        'sh000001': '000001.SH', 'sz399006': '399006.SZ',
        'sh000688': '000688.SH', 'sh000300': '000300.SH',
        'sh000016': '000016.SH', 'sh000852': '000852.SH',
        'sh000853': '000853.SH', 'sh000985': '000985.SH',
        'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
        '800000': '800000.SH',  # 东财全A（可能不适用）
    }
    if code in idx_map:
        return idx_map[code]
    # 港股个股
    if data_type == 'hk' and code.startswith('0'):
        return f'{code}.HK'
    # A股
    if len(code) == 6:
        if code.startswith(('6', '9')):
            return f'{code}.SH'
        else:
            return f'{code}.SZ'
    # 板块BK代码不适用QMT，由 Tushare dc_daily 处理
    return code


# ====== 写入 SQLite ======

def write_to_db(code: str, period: str, data: list) -> int:
    """写入 kline.db"""
    if not data:
        return 0
    db_path = Path('data') / 'kline.db'
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    rows = 0
    for d in data:
        cur.execute(
            'INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (code, period, d['date'], d['open'], d['high'], d['low'], d['close'], d['volume'])
        )
        rows += 1
    conn.commit()
    conn.close()
    return rows


# ====== 全量同步 ======

def sync_all_indices() -> dict:
    """同步所有指数数据"""
    indices = [
        ('sh000001', '上证指数'), ('sz399006', '创业板指'),
        ('sh000688', '科创50'), ('sh000300', '沪深300'),
        ('sh000016', '上证50'), ('sh000852', '中证1000'),
        ('sh000853', '中证2000'), ('sh000985', '中证全指'),
        ('HSI', '恒生指数'), ('HSTECH', '恒生科技'),
    ]
    if not connect():
        return {'success': 0, 'failed': len(indices), 'error': '未连接'}

    result = {'success': 0, 'failed': 0, 'downloaded': 0}
    for code, name in indices:
        qmt_code = map_code_to_qmt(code, 'index')
        # 下载最新
        if download_latest(qmt_code):
            result['downloaded'] += 1
        # 读取
        data = get_kline(qmt_code)
        if data:
            n = write_to_db(code, 'daily', data)
            result['success'] += 1
            logger.info(f"  ✓ {name} ({code}): {n}条, 最新{data[-1]['date']}")
        else:
            result['failed'] += 1
            logger.warning(f"  ✗ {name} ({code}): 无数据")

    disconnect()
    return result


def sync_board_stocks(board_type: str, codes: list):
    """同步板块成分股（个股）数据"""
    if not connect():
        return
    today = datetime.now().strftime('%Y%m%d')
    for code in codes:
        qmt_code = map_code_to_qmt(code, 'stock')
        download_latest(qmt_code)
        data = get_kline(qmt_code)
        if data:
            n = write_to_db(code, 'daily', data)
            if n > 0:
                logger.info(f"  个股 {code}: {n}条, 最新{data[-1]['date']}")


def fast_update_today() -> dict:
    """
    快速更新今日数据（增量更新）
    只读取最近3天的数据，降低读取量
    """
    indices = [
        ('sh000001', '000001.SH'), ('sz399006', '399006.SZ'),
        ('sh000688', '000688.SH'), ('sh000300', '000300.SH'),
        ('sh000016', '000016.SH'), ('sh000852', '000852.SH'),
        ('sh000853', '000853.SH'), ('sh000985', '000985.SH'),
        ('HSI', 'HSI.HK'), ('HSTECH', 'HSTECH.HK'),
    ]
    if not connect():
        return {'success': 0, 'failed': len(indices), 'error': '未连接'}

    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')

    result = {'success': 0, 'failed': 0}
    for code, qmt_code in indices:
        try:
            download_latest(qmt_code)
            data = get_kline(qmt_code, start_date=start, end_date=today)
            if data:
                # 只保留今天及最近3天的（增量）
                recent = [d for d in data if d['date'] >= (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')]
                n = write_to_db(code, 'daily', recent)
                result['success'] += 1
                dates = [d['date'] for d in recent]
                logger.info(f"  ✓ {code}: {n}条 {dates}")
            else:
                result['failed'] += 1
        except Exception as e:
            logger.warning(f"  ✗ {code}: {e}")
            result['failed'] += 1

    disconnect()
    return result


# ====== 入口 ======

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'fast':
        print('=== QMT 快速增量同步（仅最近数据）===')
        r = fast_update_today()
        print(f"结果: 成功{r['success']}, 失败{r['failed']}")
    else:
        print('=== QMT 全量同步所有指数 ===')
        r = sync_all_indices()
        print(f"结果: 成功{r['success']}, 失败{r['failed']}, 下载{r.get('downloaded',0)}")
