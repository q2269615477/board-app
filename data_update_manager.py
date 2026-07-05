"""
data_update_manager.py - 数据更新管理器（v5.0）
QMT + Tushare 混合架构
  - 盘后日K（个股+指数）→ QMT（毫秒级，无频率限制）
  - 东财BK板块         → Tushare dc_index/dc_daily/dc_member
  - 日内分时           → 面板搜索时实时读QMT（不在此模块范围）
"""
import json
import time
import threading
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# 日志配置
log_path = Path('data') / 'update_logs'
log_path.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_path / f'update_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('data_update')

STATUS_FILE = Path('data') / 'update_status.json'
_update_lock = threading.Lock()
_update_in_progress = False

# ===== QMT 指数映射表 =====
# 所有走 QMT 的指数（BK1158/800000 无 QMT 映射，回退到 tushare）
QMT_INDEX_MAP = {
    'sh000001': '000001.SH', 'sz399006': '399006.SZ',
    'sh000688': '000688.SH', 'sh000300': '000300.SH',
    'sh000016': '000016.SH', 'sh000852': '000852.SH',
    'sh000853': '000853.SH', 'sh000985': '000985.SH',
    'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
}
# 无 QMT 映射的，回退 tushare
TUSHARE_FALLBACK_INDICES = [
    ('BK1158', '微盘股', 'concept'),
    ('800000', '东方财富全A', 'index'),
]

PREWARM_TARGETS = [
    ('sh000001', '上证指数', 'index'),
    ('sz399006', '创业板指', 'index'),
    ('sh000688', '科创50', 'index'),
    ('sh000300', '沪深300', 'index'),
    ('sh000016', '上证50', 'index'),
    ('sh000852', '中证1000', 'index'),
    ('sh000853', '中证2000', 'index'),
    ('sh000985', '中证全指', 'index'),
    ('HSI', '恒生指数', 'hk_index'),
    ('HSTECH', '恒生科技', 'hk_index'),
    ('BK1158', '微盘股', 'concept'),
    ('800000', '东方财富全A', 'index'),
]


def _load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'indices' not in data: data['indices'] = {}
                if 'boards' not in data: data['boards'] = {}
                if 'stocks' not in data: data['stocks'] = {}
                return data
        except Exception:
            pass
    return {
        'boards': {}, 'indices': {}, 'stocks': {},
        'today': '',
        'qmt_daily_done': '',
        'scheduler': {'last_run': '', 'next_run': '', 'status': 'idle'}
    }


def _save_status(status: dict):
    with _update_lock:
        try:
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存状态失败: {e}")


def _mark_today_done():
    status = _load_status()
    status['today'] = datetime.now().strftime('%Y-%m-%d')
    _save_status(status)
    logger.info(f"[标记] 今日({status['today']})全量更新已完成")


def _mark_qmt_daily_done():
    status = _load_status()
    status['qmt_daily_done'] = datetime.now().strftime('%Y-%m-%d')
    _save_status(status)


def is_today_updated() -> bool:
    status = _load_status()
    return status.get('today') == datetime.now().strftime('%Y-%m-%d')


def is_qmt_daily_done() -> bool:
    """今天是否已完成 QMT 个股日更"""
    status = _load_status()
    return status.get('qmt_daily_done') == datetime.now().strftime('%Y-%m-%d')


# ===== 个股台账（SQLite存储） =====

import sqlite3 as _sqlite3
_LEDGER_DB = str(Path('data') / 'kline.db')


def _get_ledger_conn():
    conn = _sqlite3.connect(_LEDGER_DB)
    conn.row_factory = _sqlite3.Row
    return conn


def is_stock_cached(code: str) -> bool:
    conn = _get_ledger_conn()
    cur = conn.execute('SELECT 1 FROM stock_ledger WHERE code=?', (code,))
    r = cur.fetchone() is not None
    conn.close()
    return r


def add_stock_to_ledger(code: str, name: str = ''):
    conn = _get_ledger_conn()
    conn.execute(
        "INSERT OR REPLACE INTO stock_ledger (code, name, first_cached, last_updated) "
        "VALUES (?, ?, COALESCE((SELECT first_cached FROM stock_ledger WHERE code=?), "
        "datetime('now','localtime')), datetime('now','localtime'))",
        (code, name, code))
    conn.commit()
    conn.close()
    logger.info(f"[台账] 已记录个股 {code}({name})")


def get_all_cached_stocks() -> list:
    conn = _get_ledger_conn()
    cur = conn.execute('SELECT code FROM stock_ledger ORDER BY code')
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


# ===== QMT 工具函数 =====

def _qmt_connect() -> bool:
    """连接 QMT 行情服务"""
    try:
        from xtquant import xtdata
        xtdata.connect(port=58610)
        xtdata.enable_hello = False
        return True
    except Exception as e:
        logger.error(f"QMT 连接失败: {e}")
        return False


def _to_qmt_code(code: str) -> str:
    """内部代码 → QMT 代码"""
    code = str(code).zfill(6)
    if code.startswith(('6', '9')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


def _from_qmt_ts(ts_val) -> str:
    """QMT time 字段(毫秒时间戳) → YYYY-MM-DD"""
    if isinstance(ts_val, (int, float)) and ts_val > 1e12:
        return datetime.fromtimestamp(ts_val / 1000).strftime('%Y-%m-%d')
    return str(ts_val)[:10]


def fetch_qmt_kline(code: str, start_date: str) -> list:
    """
    通过 QMT 读取单只股票/指数从 start_date 至今的日K线。
    start_date: YYYYMMDD
    返回 list of dict: date(YYYY-MM-DD), open, high, low, close, volume
    """
    # 指数映射
    qmt_code = QMT_INDEX_MAP.get(code, _to_qmt_code(code))
    try:
        from xtquant import xtdata
        xtdata.download_history_data(qmt_code, period='1d',
                                     start_time=start_date,
                                     end_time=datetime.now().strftime('%Y%m%d'))
    except Exception as e:
        logger.debug(f"download 异常: {e}")

    try:
        from xtquant import xtdata
        data = xtdata.get_local_data(
            field_list=['time', 'open', 'high', 'low', 'close', 'volume'],
            stock_list=[qmt_code], period='1d',
            start_time=start_date,
            end_time=datetime.now().strftime('%Y%m%d'), count=0
        )
    except Exception as e:
        logger.warning(f"读取 {code}({qmt_code}) 失败: {e}")
        return []

    if not isinstance(data, dict) or qmt_code not in data:
        return []

    df = data[qmt_code]
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            'date': _from_qmt_ts(row.get('time', 0)),
            'open': float(row.get('open', 0) or 0),
            'high': float(row.get('high', 0) or 0),
            'low': float(row.get('low', 0) or 0),
            'close': float(row.get('close', 0) or 0),
            'volume': float(row.get('volume', 0) or 0),
        })
    return result


# ===== QMT 个股日更 =====

def qmt_update_all_stocks(max_retries: int = 3) -> dict:
    """通过 QMT 增量更新所有已缓存个股的日K线"""
    if is_qmt_daily_done():
        return {'skipped': True, 'message': '今日QMT个股日更已完成'}

    logger.info("[QMT个股日更] 开始...")
    if not _qmt_connect():
        logger.error("[QMT个股日更] QMT 连接失败")
        return {'success': 0, 'failed': 0, 'error': 'QMT未连接'}

    conn = _sqlite3.connect(_LEDGER_DB)
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM stock_ledger WHERE LENGTH(code)=6 ORDER BY code")
    stocks = cur.fetchall()

    if not stocks:
        conn.close()
        _mark_qmt_daily_done()
        return {'success': 0, 'skipped': True, 'message': '台账为空'}

    today_norm = datetime.now().strftime('%Y%m%d')
    pending = []
    for code, name in stocks:
        cur.execute("SELECT MAX(date) FROM kline WHERE code=? AND period='daily'", (code,))
        max_date = cur.fetchone()[0]
        max_norm = (max_date or '').replace('-', '')
        if max_date is None or max_norm < today_norm:
            pending.append((code, name))

    logger.info(f"[QMT个股日更]实际需要更新: {len(pending)}/{len(stocks)}")

    if not pending:
        conn.close()
        _mark_qmt_daily_done()
        return {'success': 0, 'skipped': True, 'message': '已是最新'}

    success = failed = total_new = 0
    start_ts = time.time()
    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""

    for i, (code, name) in enumerate(pending):
        try:
            cur.execute("SELECT COALESCE(MAX(date),'19900101') FROM kline WHERE code=? AND period='daily'", (code,))
            max_date = cur.fetchone()[0]
            max_norm = (max_date or '').replace('-', '')
            if len(max_norm) != 8 or not max_norm.isdigit():
                max_norm = '19900101'
            next_start = (datetime.strptime(max_norm, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')

            rows = []
            for attempt in range(max_retries):
                try:
                    rows = fetch_qmt_kline(code, next_start)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    else:
                        logger.error(f"[{code}] 读取失败: {e}")

            if rows:
                batch = []
                for r in rows:
                    batch.append((code, 'daily', r['date'], r['open'], r['high'], r['low'], r['close'], r['volume'], now_str))
                cur.executemany(INSERT_SQL, batch)
                total_new += len(batch)
                success += 1
            else:
                success += 1  # 无新增也算成功

        except Exception as e:
            logger.error(f"[{code}] 更新异常: {e}")
            failed += 1

        if i % 100 == 0 and i > 0:
            elapsed = time.time() - start_ts
            rate = i / elapsed if elapsed > 0 else 0
            logger.info(f"[QMT个股日更] {i}/{len(pending)} 成功={success} 失败={failed} {rate:.1f}只/s")

        time.sleep(0.1)

    conn.commit()

    # 更新 kline_meta
    try:
        cur.execute("""INSERT OR REPLACE INTO kline_meta (code, period, rows, first_date, last_date, updated_at)
                       SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ?
                       FROM kline WHERE code IN ({}) AND period='daily'
                       GROUP BY code""".format(','.join(['?'] * len(pending))),
                    [now_str] + [c for c, _ in pending])
        conn.commit()
    except Exception:
        pass

    conn.close()
    elapsed = time.time() - start_ts
    logger.info(f"[QMT个股日更] 完成: 成功={success} 失败={failed} 新增={total_new}条 耗时={elapsed:.1f}s")
    _mark_qmt_daily_done()
    return {'success': success, 'failed': failed, 'new_rows': total_new}


# ===== 指数数据（QMT） =====

def update_all_indices_qmt(max_retries: int = 3) -> dict:
    """通过 QMT 更新所有指数（日K线）"""
    result = {'success': 0, 'failed': 0, 'total': len(PREWARM_TARGETS)}
    logger.info(f"[QMT指数] 开始更新 {result['total']} 个指数")

    if not _qmt_connect():
        logger.warning("[QMT指数] QMT 不可用，全部回退 tushare")
        return update_all_indices_tushare()

    conn = _sqlite3.connect(_LEDGER_DB)
    cur = conn.cursor()
    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""
    today_norm = datetime.now().strftime('%Y%m%d')

    for code, name, dtype in PREWARM_TARGETS:
        try:
            # 无 QMT 映射的走 tushare 回退
            if code not in QMT_INDEX_MAP:
                logger.info(f"[QMT指数] {name}({code}) 无QMT映射，走tushare回退")
                _update_single_index_tushare(code, name, dtype)
                result['success'] += 1
                continue

            qmt_code = QMT_INDEX_MAP[code]
            # 查已有最新日期
            cur.execute("SELECT COALESCE(MAX(date),'19900101') FROM kline WHERE code=? AND period='daily'", (code,))
            max_date = cur.fetchone()[0]
            max_norm = (max_date or '').replace('-', '')
            if len(max_norm) != 8 or not max_norm.isdigit():
                max_norm = '19900101'
            next_start = (datetime.strptime(max_norm, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')

            if next_start > today_norm:
                logger.debug(f"[QMT指数] {name} 已是最新")
                result['success'] += 1
                continue

            # 最多重试 max_retries 次
            rows = []
            for attempt in range(max_retries):
                try:
                    rows = fetch_qmt_kline(code, next_start)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    else:
                        logger.error(f"[QMT指数] {name} 读取失败: {e}")

            if rows:
                batch = []
                for r in rows:
                    batch.append((code, 'daily', r['date'], r['open'], r['high'], r['low'], r['close'], r['volume'], now_str))
                cur.executemany(INSERT_SQL, batch)
                conn.commit()
                logger.info(f"[QMT指数] {name}({code}) 写入 {len(batch)} 条")
            result['success'] += 1

        except Exception as e:
            logger.error(f"[QMT指数] {name}({code}) 失败: {e}")
            result['failed'] += 1

    conn.close()
    logger.info(f"[QMT指数] 完成: 成功={result['success']}, 失败={result['failed']}")
    return result


def update_all_indices_tushare(max_retries: int = 3) -> dict:
    """回退方案：tushare 更新所有指数"""
    result = {'success': 0, 'failed': 0, 'total': len(PREWARM_TARGETS)}
    logger.info(f"[Tushare指数] 开始更新 {result['total']} 个指数")
    for code, name, dtype in PREWARM_TARGETS:
        ok = _update_single_index_tushare(code, name, dtype, max_retries)
        if ok:
            result['success'] += 1
        else:
            result['failed'] += 1
    return result


def _update_single_index_tushare(code: str, name: str, data_type: str, max_retries: int = 3) -> bool:
    """tushare 回退更新单个指数"""
    for attempt in range(max_retries):
        try:
            from data_loader import load_index_kline, load_hk_index_kline, load_board_kline
            logger.info(f"[Tushare指数] 更新 {name}({code})")
            if data_type == 'hk_index':
                load_hk_index_kline(code)
            elif data_type == 'concept':
                load_board_kline('concept', name, code)
            else:
                load_index_kline(code)
            status = _load_status()
            status['indices'][code] = {
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'success', 'name': name
            }
            _save_status(status)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 3)
            else:
                error_msg = str(e)[:200]
                status = _load_status()
                status['indices'][code] = {
                    'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'status': 'failed', 'error': error_msg, 'name': name
                }
                _save_status(status)
                logger.error(f"[Tushare指数] {name}({code}) 失败: {error_msg}")
                return False
    return False


# ===== 板块数据（Tushare） =====

def _update_single_board(board_type: str, name: str, code: str) -> bool:
    from data.board_api import get_board_kline
    try:
        logger.info(f"[板块] 更新 {name}({code})")
        df = get_board_kline(board_type, code)
        if df is not None and not df.empty:
            from data_loader import DATA_ROOT
            from data.sqlite_repo import get_sqlite_repo
            subdir = DATA_ROOT / ('行业板块K线数据' if board_type == 'industry' else '概念板块K线数据')
            subdir.mkdir(parents=True, exist_ok=True)
            csv_path = subdir / f'{name}_{code}.csv'

            if csv_path.exists():
                try:
                    local = pd.read_csv(csv_path, encoding='utf-8-sig')
                    merged = pd.concat([local, df], ignore_index=True)
                    merged = merged.drop_duplicates(subset=['日期'], keep='last')
                    merged = merged.sort_values('日期').reset_index(drop=True)
                    merged.to_csv(csv_path, index=False, encoding='utf-8-sig')
                except Exception:
                    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            else:
                df.to_csv(csv_path, index=False, encoding='utf-8-sig')

            try:
                db = get_sqlite_repo()
                db.save_kline(code, 'daily', df)
            except Exception:
                pass

            status = _load_status()
            status['boards'][code] = {'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'status': 'success'}
            _save_status(status)
            logger.info(f"[板块] {name}({code}) 更新成功")
            return True
        else:
            logger.warning(f"[板块] {name}({code}) 返回空数据")
            return False
    except Exception as e:
        error_msg = str(e)[:200]
        status = _load_status()
        status['boards'][code] = {'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'status': 'failed', 'error': error_msg}
        _save_status(status)
        logger.error(f"[板块] {name}({code}) 更新失败: {error_msg}")
        return False


def update_all_boards(max_retries: int = 3) -> dict:
    global _update_in_progress
    if _update_in_progress:
        logger.warning("[板块] 上次更新尚未完成，跳过")
        return {'success': 0, 'failed': 0, 'total': 0, 'error': '上次更新进行中'}

    _update_in_progress = True
    result = {'success': 0, 'failed': 0, 'total': 0}

    try:
        import json as _json
        cf = Path('static') / 'board_classification.json'
        if not cf.exists():
            raise FileNotFoundError("board_classification.json 不存在")
        with open(cf, 'r', encoding='utf-8') as f:
            cats = _json.load(f).get('categories', [])
        boards = [(b['type'], b['name'], b['code']) for cat in cats for b in cat.get('boards', [])]
        result['total'] = len(boards)
        logger.info(f"[板块] 共 {len(boards)} 个")

        for idx, (btype, name, code) in enumerate(boards):
            time.sleep(random.uniform(1.5, 4.0))
            ok = _update_single_board(btype, name, code)
            if ok:
                result['success'] += 1
            else:
                result['failed'] += 1
            logger.info(f"[板块更新] {idx+1}/{len(boards)} {name}({code}) {'✓' if ok else '✗'}")

        logger.info(f"[板块] 完成: 成功={result['success']}, 失败={result['failed']}, 共={result['total']}")
    except Exception as e:
        logger.error(f"[板块] 异常: {e}")
    finally:
        _update_in_progress = False

    return result


# ===== 已缓存个股的每日增量更新（通过 QMT） =====

def update_all_cached_stocks(max_retries: int = 2) -> dict:
    """
    调用 QMT 增量更新所有已缓存个股。
    盘中/启动时调用频率不高，盘后日更由 qmt_update_all_stocks() 完成。
    这里直接委托给 qmt_update_all_stocks（复用增量逻辑）。
    """
    return qmt_update_all_stocks(max_retries)


# ===== 板块周月线刷新 =====

def _refresh_board_weekly_monthly(board_type: str, name: str, code: str, period: str) -> bool:
    from data_loader import load_board_kline
    try:
        load_board_kline(board_type, name, code, period)
        return True
    except Exception:
        return False


def refresh_all_boards_weekly_monthly() -> dict:
    """日线更新后，刷新所有板块的周线和月线"""
    import json as _json
    cf = Path('static') / 'board_classification.json'
    if not cf.exists():
        return {'success': 0, 'failed': 0, 'total': 0}
    try:
        with open(cf, 'r', encoding='utf-8') as f:
            cats = _json.load(f).get('categories', [])
    except Exception:
        return {'success': 0, 'failed': 0, 'total': 0}

    boards = [(b['type'], b['name'], b['code']) for cat in cats for b in cat.get('boards', [])]
    result = {'success': 0, 'failed': 0, 'total': len(boards) * 2}

    for btype, name, code in boards:
        for period in ('weekly', 'monthly'):
            ok = _refresh_board_weekly_monthly(btype, name, code, period)
            if ok:
                result['success'] += 1
            else:
                result['failed'] += 1

    logger.info(f"[周月线刷新] 完成: 成功{result['success']}, 失败{result['failed']}")
    return result


# ===== 统一日更入口 =====

def update_all_today(max_retries: int = 3) -> dict:
    """
    每日全量更新入口：
    1. QMT 更新所有指数
    2. QMT 增量更新所有个股（日K线盘后）
    3. Tushare 更新东财板块（概念+行业）
    4. 刷新板块周月线
    """
    if is_today_updated():
        logger.info("[日更] 今天已更新，跳过")
        return {'skipped': True, 'message': '今日已更新'}

    logger.info("=" * 50)
    logger.info("[日更] === 开始每日全量更新 (QMT+Tushare) ===")
    result = {'indices': None, 'stocks': None, 'boards': None, 'weekly_monthly': None}

    # 1. QMT 更新所有指数
    logger.info("[日更] Step 1/4: QMT 指数更新")
    result['indices'] = update_all_indices_qmt(max_retries)

    # 2. QMT 增量更新个股
    logger.info("[日更] Step 2/4: QMT 个股日更")
    result['stocks'] = qmt_update_all_stocks(max_retries)

    # 3. Tushare 东财板块更新（仅当东财API可用时）
    eastmoney_ok = False
    try:
        import requests as _req
        r = _req.get('https://push2.eastmoney.com/api/qt/stock/get',
                      params={'secid': '1.000001', 'fields': 'f57,f58,f43'},
                      timeout=3)
        eastmoney_ok = r.status_code == 200 and len(r.text) > 20
    except Exception:
        pass

    if eastmoney_ok:
        logger.info("[日更] Step 3/4: 东财板块更新")
        result['boards'] = update_all_boards(max_retries)
        logger.info("[日更] Step 4/4: 板块周月线刷新")
        result['weekly_monthly'] = refresh_all_boards_weekly_monthly()
    else:
        logger.warning("[日更] ⚠️ 东财API不可用，跳过板块更新")
        result['boards'] = {'success': 0, 'skipped': True}
        result['weekly_monthly'] = {'success': 0, 'skipped': True}

    _mark_today_done()
    logger.info("[日更] === 全部完成 ===")
    logger.info("=" * 50)
    return result


# ===== 盘中数据同步 =====

def _intraday_sync_qmt():
    """盘中每 5 分钟执行一次：从 QMT 拉取最新指数数据"""
    try:
        import qmt_bridge as _qb
        from core.cache import get_cache
        if not _qb.connect():
            return

        synced = 0
        yesterday = (pd.Timestamp.now() - pd.Timedelta(days=3)).strftime('%Y%m%d')
        intraday_targets = [
            ('sh000001','上证指数','000001.SH'), ('sz399006','创业板指','399006.SZ'),
            ('sh000688','科创50','000688.SH'), ('sh000300','沪深300','000300.SH'),
            ('sh000016','上证50','000016.SH'), ('sh000852','中证1000','000852.SH'),
            ('sh000853','中证2000','000853.SH'), ('sh000985','中证全指','000985.SH'),
            ('HSI','恒生指数','HSI.HK'), ('HSTECH','恒生科技','HSTECH.HK'),
        ]
        for code, name, qmt_code in intraday_targets:
            try:
                _qb.download_latest(qmt_code)
                data = _qb.get_kline(qmt_code, start_date=yesterday)
                if data:
                    _qb.write_to_db(code, 'daily', data)
                    get_cache().delete(f'index:{code}:daily')
                    synced += 1
            except Exception as e:
                logger.debug(f"  {name}: {e}")
        if synced > 0:
            logger.info(f"[盘中] 同步了 {synced} 个指数的最新数据")
    except Exception as e:
        logger.debug(f"[盘中] 同步异常(可忽略): {e}")


# ===== 交易日历 =====

_trade_dates_cache = None
_trade_dates_cached_at = 0


def _is_trading_day() -> bool:
    """判断今天是否为A股交易日"""
    global _trade_dates_cache, _trade_dates_cached_at
    now = time.time()
    if _trade_dates_cache is not None and now - _trade_dates_cached_at < 3600:
        return _trade_dates_cache
    try:
        from data_loader import _tushare_pro
        df = _tushare_pro.trade_cal(exchange='SSE', start_date='2020-01-01',
                                     end_date=datetime.now().strftime('%Y-%m-%d'),
                                     is_open='1')
        trade_dates = set(df['cal_date'].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}").values)
        today_str = datetime.now().strftime('%Y-%m-%d')
        is_trade = today_str in trade_dates
        _trade_dates_cache = is_trade
        _trade_dates_cached_at = now
        return is_trade
    except Exception as e:
        logger.warning(f"[交易日] 查询失败({e})，默认是交易日")
        return True


def _next_trade_close() -> datetime:
    """找到下一个交易日的收盘时间"""
    now = datetime.now()
    try:
        from data_loader import _tushare_pro
        df = _tushare_pro.trade_cal(exchange='SSE', start_date='2020-01-01',
                                     end_date=(now + timedelta(days=15)).strftime('%Y-%m-%d'),
                                     is_open='1')
        trade_set = set(df['cal_date'].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}").values)
    except Exception:
        return now.replace(hour=18, minute=0) + timedelta(days=1)

    for offset in range(1, 15):
        candidate = now + timedelta(days=offset)
        candidate = candidate.replace(hour=15, minute=30, second=0, microsecond=0)
        if candidate.strftime('%Y-%m-%d') in trade_set:
            return candidate
    return now.replace(hour=18, minute=0) + timedelta(days=1)


# ===== 定时调度器（交易日历感知） =====

_scheduler_thread = None
_scheduler_running = False


def _scheduler_loop():
    global _scheduler_running
    logger.info("[调度器] 启动（QMT+Tushare混合, 交易日历感知）")
    _scheduler_running = True

    while _scheduler_running:
        try:
            now = datetime.now()
            is_trade = _is_trading_day()

            if not is_trade:
                next_trade = _next_trade_close()
                wait_sec = (next_trade - now).total_seconds()
                logger.info(f"[调度器] 非交易日，距下次更新 {(next_trade).strftime('%m-%d %H:%M')} 还有 {wait_sec/3600:.1f}小时")
            else:
                close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
                if now < close_time:
                    # 盘中：每 5 分钟拉一次 QMT 指数最新数据
                    wait_sec = 300
                    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    if now >= market_open:
                        _intraday_sync_qmt()
                        logger.info(f"[调度器] 盘中QMT指数同步完成，下次 {wait_sec}s后")
                    else:
                        wait_sec = min(300, (market_open - now).total_seconds())
                        logger.info(f"[调度器] 尚未开盘，{wait_sec}s后检查")
                elif is_today_updated():
                    next_trade = _next_trade_close()
                    wait_sec = (next_trade - now).total_seconds()
                    logger.info(f"[调度器] 今日已更新，下次 {(next_trade).strftime('%m-%d %H:%M')}")
                else:
                    # ===== 收盘后全量更新 =====
                    logger.info("[调度器] 收盘后触发全量更新")
                    _notify_event('data_updating', '全量更新')
                    update_all_today()
                    _notify_event('data_updated', '全量完成')
                    wait_sec = 86400

            status = _load_status()
            next_dt = now + timedelta(seconds=wait_sec)
            status['scheduler']['next_run'] = next_dt.strftime('%Y-%m-%d %H:%M:%S')
            status['scheduler']['status'] = 'waiting'
            _save_status(status)

            remaining = wait_sec
            while remaining > 0 and _scheduler_running:
                time.sleep(min(60, remaining))
                remaining -= 60

        except Exception as e:
            logger.error(f"[调度器] 异常: {e}")
            time.sleep(60)


def start_scheduler():
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name='data-scheduler')
    _scheduler_thread.start()
    logger.info("[调度器] 已启动")

    # 启动时检查
    def _startup_check():
        time.sleep(3)
        now = datetime.now()
        if _is_trading_day() and now.hour >= 15 and not is_today_updated():
            logger.info("[调度器] 启动时检测到交易日已收盘，触发立即更新")
            _notify_event('data_updating', '启动后首次更新')
            update_all_today()
            _notify_event('data_updated', '首次更新完成')
    threading.Thread(target=_startup_check, daemon=True).start()


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
    logger.info("[调度器] 已停止")


# ===== SSE 事件通知 =====

_events = []
_events_lock = threading.Lock()


def _notify_event(event_type: str, message: str = ''):
    with _events_lock:
        _events.append({'type': event_type, 'message': message, 'time': datetime.now().strftime('%H:%M:%S')})
        if len(_events) > 20:
            _events[:] = _events[-20:]


def get_sse_events(last_index: int = 0):
    with _events_lock:
        if last_index >= len(_events):
            return [], len(_events)
        return _events[last_index:], len(_events)


# ===== 状态查询 =====

def get_update_status() -> dict:
    status = _load_status()
    boards = status.get('boards', {})
    indices = status.get('indices', {})

    idx_success = sum(1 for v in indices.values() if v.get('status') == 'success')
    idx_failed = sum(1 for v in indices.values() if v.get('status') == 'failed')
    board_success = sum(1 for v in boards.values() if v.get('status') == 'success')
    board_failed = sum(1 for v in boards.values() if v.get('status') == 'failed')

    return {
        'today_done': status.get('today'),
        'today_updated': is_today_updated(),
        'qmt_daily_done': status.get('qmt_daily_done'),
        'qmt_daily_completed': is_qmt_daily_done(),
        'scheduler': status.get('scheduler', {}),
        'index_stats': {'total': len(indices), 'success': idx_success, 'failed': idx_failed},
        'board_stats': {'total': len(boards), 'success': board_success, 'failed': board_failed},
        'stock_count': len(status.get('stocks', {})),
        'update_in_progress': _update_in_progress,
        'last_log_file': str(log_path / f'update_{datetime.now().strftime("%Y%m%d")}.log')
    }


# ===== 健康检查 =====

def health_check() -> dict:
    import requests as _req
    try:
        r = _req.get('https://push2.eastmoney.com/api/qt/stock/get',
                      params={'secid': '1.000001', 'fields': 'f57,f58,f43'},
                      timeout=5)
        api_ok = r.status_code == 200 and len(r.text) > 20
    except Exception:
        api_ok = False
    return {
        'api_available': api_ok,
        'recommendation': '可以执行' if api_ok else '⚠️ 东财API不可用，建议等待后再试',
    }
