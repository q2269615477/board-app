"""
data_update_manager.py - 数据更新管理器（v5.2）
数据源纪律（硬）：
  - 盘中指数 / 个股临时行情 → **QMT HTTP 18080**，仅内存，不落正式日线
  - 个股盘后目标日日K → **QMT HTTP 18080 /ohlc_batch（80只/批）**
  - 指数盘后日K → **QMT 公式口 / 本地 datadir**
  - QMT 指数尾部陈旧超过 1 个交易日 → **Tushare 仅作日线补齐**
  - 东财 BK 板块（行业+概念）  → **仅 Tushare** dc_index/dc_daily/dc_member
  - 禁止用 QMT 替代东财板块接口
"""
import json
import os
import time
import threading
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

from services.kline_quality_service import scan_daily_frame

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
# The unified daily-update entry point owns this guard.  Other entry points
# (for example the force-update task) may observe it, but must never maintain a
# second copy of the state.  Keeping the flag beside ``update_all_today``
# prevents two scheduler/agent callers from passing the initial "not done"
# check at the same time and starting duplicate full updates.
_full_update_lock = threading.Lock()
_full_update_in_progress = False


def _claim_full_update() -> bool:
    """Atomically claim the unified daily-update slot."""
    global _full_update_in_progress
    with _full_update_lock:
        if _full_update_in_progress:
            return False
        _full_update_in_progress = True
        return True


def _release_full_update() -> None:
    """Release the unified daily-update slot, including exceptional exits."""
    global _full_update_in_progress
    with _full_update_lock:
        _full_update_in_progress = False


def is_full_update_in_progress() -> bool:
    """Read the manager-owned full-update guard without starting any work."""
    with _full_update_lock:
        return bool(_full_update_in_progress)

# 状态存储委托给 services/update_status_store.py（已抽取为独立模块）
from services.update_status_store import (
    load_status as _status_load,
    save_status as _status_save,
    update_status as _status_update,
    mark_today_done as _status_mark_today,
    mark_qmt_daily_done as _status_mark_qmt,
    is_today_updated as _status_is_today,
    is_qmt_daily_done as _status_is_qmt_done,
)

# ===== QMT 指数映射表（仅 QMT；无映射则跳过，不走 Tushare）=====
QMT_INDEX_MAP = {
    'sh000001': '000001.SH', 'sz399006': '399006.SZ',
    'sh000688': '000688.SH', 'sh000300': '000300.SH',
    'sh000016': '000016.SH', 'sh000852': '000852.SH',
    'sh000853': '000853.SH', 'sh000985': '000985.SH',
    'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
}
# 东财板指标的：不在指数日更内，由 update_all_boards（Tushare）维护。
BOARD_ONLY_PREWARM = frozenset({'BK1158'})
# 不走 QMT 的东财自有指数；由官方 secid 历史/快照接口维护。
EASTMONEY_INDEX_CODES = frozenset({'800000'})
PERMANENT_SKIP_INDICES = {}

# ===== 指数日更按交易所日历 =====
# A股指数继续沿用 A股日历；港股/全球指数按各自交易所日历。
# market key 与 services/market_session.py / services/exchange_calendar_service.py 对齐。
INDEX_EXCHANGE_MARKET = {
    'sh000001': 'a_share', 'sz399006': 'a_share', 'sh000688': 'a_share',
    'sh000300': 'a_share', 'sh000016': 'a_share', 'sh000852': 'a_share',
    'sh000853': 'a_share', 'sh000985': 'a_share', '800000': 'a_share',
    'HSI': 'hong_kong', 'HSTECH': 'hong_kong',
    '^N225': 'japan', '^KS11': 'south_korea', '^TWII': 'taiwan',
    'SPX': 'us', 'IXIC': 'us', 'DJI': 'us',
}
INDEX_EXCHANGE_TZ = {
    'a_share': 'Asia/Shanghai',
    'hong_kong': 'Asia/Hong_Kong',
    'japan': 'Asia/Tokyo',
    'south_korea': 'Asia/Seoul',
    'taiwan': 'Asia/Taipei',
    'us': 'America/New_York',
}

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


# ===== 个股状态分类工具 =====

def _target_trade_day_str():
    """返回当天交易日 YYYYMMDD 字符串（用于数据新鲜度判定）。"""
    from datetime import datetime
    return datetime.now().strftime('%Y%m%d')


def classify_stock_daily_status(max_date_str, row_count, target_norm, quality_report=None):
    """分类个股日线数据状态。

    Args:
        max_date_str: DB 中 MAX(date)，可能带横线(YYYY-MM-DD)或不带(YYYYMMDD)，也可能为 None
        row_count: kline 行数
        target_norm: 目标交易日 YYYYMMDD（如 '20260727'）

    Returns:
        'date_lag' / 'sparse' / 'repair_pending' / 'up_to_date'
    """
    if quality_report and quality_report.get('blocking_failure'):
        return 'repair_pending'
    if max_date_str is None or row_count == 0:
        return 'date_lag'
    normalized = max_date_str.replace('-', '')
    if normalized != target_norm:
        return 'date_lag'
    # 历史稀疏由 history_repair 游标维护，不阻塞目标日结算。
    return 'up_to_date'


def build_stock_pending_from_ledger(
    stocks, max_dates, row_counts, target_norm, quality_reports=None
):
    """根据台账信息构建 pending 列表。

    Args:
        stocks: [(code, name), ...]
        max_dates: {code: max_date_str_or_None}
        row_counts: {code: int}
        target_norm: 目标交易日 YYYYMMDD

    Returns:
        dict with pending, skipped_up_to_date, pending_date_lag, pending_sparse, total
    """
    pending = []
    skipped_up_to_date = 0
    pending_date_lag = 0
    pending_sparse = 0
    pending_repair = 0
    repair_pending_codes = []
    quality_reports = quality_reports or {}
    for code, name in stocks:
        status = classify_stock_daily_status(
            max_dates.get(code), row_counts.get(code, 0), target_norm,
            quality_reports.get(code),
        )
        if status == 'up_to_date':
            skipped_up_to_date += 1
        elif status == 'date_lag':
            pending.append((code, name))
            pending_date_lag += 1
        elif status == 'sparse':
            pending.append((code, name))
            pending_sparse += 1
        elif status == 'repair_pending':
            pending.append((code, name))
            pending_repair += 1
            repair_pending_codes.append(code)
    return {
        'pending': pending,
        'skipped_up_to_date': skipped_up_to_date,
        'pending_date_lag': pending_date_lag,
        'pending_sparse': pending_sparse,
        'pending_repair': pending_repair,
        'repair_pending_codes': repair_pending_codes,
        'total': len(stocks),
    }


def _scan_daily_quality_cursor(cur, codes):
    """Read daily rows for selected codes and run the local quality scanner."""
    reports = {}
    for code in codes:
        cur.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM kline WHERE code=? AND period='daily' ORDER BY date",
            (code,),
        )
        rows = cur.fetchall()
        frame = pd.DataFrame(
            rows,
            columns=['date', 'open', 'high', 'low', 'close', 'volume'],
        )
        reports[code] = scan_daily_frame(frame, code=code)
    return reports


def _spot_trade_date(row) -> str:
    raw = str((row or {}).get('time') or '')
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8 and digits[:2] in {'19', '20'}:
        return digits[:8]
    return ''


def _valid_settlement_row(row) -> bool:
    try:
        open_ = float(row.get('open', 0) or 0)
        high = float(row.get('high', 0) or 0)
        low = float(row.get('low', 0) or 0)
        close = float(row.get('close', 0) or 0)
        volume = float(row.get('volume', 0) or 0)
        return (
            open_ > 0 and high > 0 and low > 0 and close > 0
            and high >= max(open_, low, close)
            and low <= min(open_, high, close)
            and volume >= 0
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _verify_no_bar_candidates(codes, target_norm):
    """Return {code: traded_on_target_day}; None means verification unavailable."""
    codes = [str(code) for code in codes if code]
    if not codes:
        return {}
    try:
        from core.env_bootstrap import ensure_tushare_token
        if not ensure_tushare_token():
            return None
        pro = _get_tushare_pro()
        frame = pro.daily(
            trade_date=str(target_norm).replace('-', '')[:8],
            fields='ts_code',
        )
        if frame is None:
            return None
        traded = {
            str(value).split('.')[0]
            for value in frame.get('ts_code', pd.Series(dtype=str)).dropna()
        }
        return {code: code in traded for code in codes}
    except Exception as exc:
        logger.warning(f"[QMT个股日更] 无bar校验失败: {exc}")
        return None


def _refresh_daily_meta_cursor(cur, codes, updated_at):
    codes = [str(code) for code in codes if code]
    if not codes:
        return
    cur.execute(
        """CREATE TABLE IF NOT EXISTS kline_meta (
            code TEXT NOT NULL, period TEXT NOT NULL, rows INTEGER,
            first_date TEXT, last_date TEXT, updated_at TEXT,
            PRIMARY KEY (code, period)
        )"""
    )
    placeholders = ','.join('?' for _ in codes)
    cur.execute(
        f"""INSERT OR REPLACE INTO kline_meta
            (code, period, rows, first_date, last_date, updated_at)
            SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ?
            FROM kline
            WHERE code IN ({placeholders}) AND period='daily'
            GROUP BY code""",
        [updated_at] + codes,
    )


def _load_status() -> dict:
    return _status_load(STATUS_FILE)


def _save_status(status: dict):
    _status_save(status, STATUS_FILE)


def _update_status(mutator):
    return _status_update(mutator, STATUS_FILE)


def _mark_today_done():
    _status_mark_today(STATUS_FILE)


def _mark_qmt_daily_done():
    _status_mark_qmt(STATUS_FILE)


def is_today_updated() -> bool:
    return _status_is_today(STATUS_FILE)


def is_qmt_daily_done() -> bool:
    """今天是否已完成 QMT 个股日更"""
    return _status_is_qmt_done(STATUS_FILE)


# ===== 个股台账（SQLite存储） =====

import sqlite3 as _sqlite3
_LEDGER_DB = str(Path('data') / 'kline.db')


def _ensure_ledger_schema():
    """Create the shared market-cache schema for standalone update entrypoints."""
    from data.sqlite_repo import SqliteRepo
    return SqliteRepo(db_path=Path(_LEDGER_DB))

# 台账委托给 services/stock_ledger_service.py（已抽取为独立模块）
from services.stock_ledger_service import (
    get_ledger_conn as _ledger_get_conn,
    is_stock_cached as _ledger_is_cached,
    add_stock_to_ledger as _ledger_add,
    get_all_cached_stocks as _ledger_get_all,
    rebuild_stock_ledger_from_kline as _ledger_rebuild,
)


def _get_ledger_conn():
    return _ledger_get_conn(_LEDGER_DB)


def is_stock_cached(code: str) -> bool:
    return _ledger_is_cached(code, _LEDGER_DB)


def add_stock_to_ledger(code: str, name: str = ''):
    _ledger_add(code, name, _LEDGER_DB)


def get_all_cached_stocks() -> list:
    return _ledger_get_all(_LEDGER_DB)


def rebuild_stock_ledger_from_kline(min_rows: int = 1) -> dict:
    return _ledger_rebuild(min_rows, _LEDGER_DB)


# ===== QMT 工具函数 =====

def _qmt_connect() -> bool:
    """连接 QMT 行情服务（通过子进程调用 QMT Python，避免版本不兼容）。

    注意：不可在模块顶层 import lifecycle，且须防循环导入
    （lifecycle → data_update_scheduler → data_update_manager → lifecycle）。

    判定顺序：
      1) lifecycle.is_qmt_available（面板运行中）
      2) qmt_client.probe_formula_ready（CLI/调度冷启动，公式口 58600）
    """
    try:
        import sys
        mod = sys.modules.get('core.lifecycle')
        if mod is not None and hasattr(mod, 'is_qmt_available'):
            if bool(mod.is_qmt_available()):
                return True
        elif mod is None:
            try:
                from core.lifecycle import is_qmt_available
                if bool(is_qmt_available()):
                    return True
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"QMT lifecycle 探测跳过: {e}")

    # 冷启动 / 公式口：不依赖 Flask 已标 available
    try:
        from data.qmt_client import get_qmt_client
        probe = get_qmt_client().probe_formula_ready()
        if probe.get('ok') and int(probe.get('rows') or 0) > 0:
            logger.info(
                f"[QMT] 公式口探测通过 rows={probe.get('rows')} last={probe.get('last_date')}"
            )
            return True
    except Exception as e:
        logger.debug(f"QMT 公式口探测跳过: {e}")
    return False


def _qmt_http_available() -> bool:
    """Check the independent 18080 market-data service."""
    try:
        from data.qmt_http_client import get_qmt_http_client
        health = get_qmt_http_client().health()
        return bool(health.get('_ok'))
    except Exception as exc:
        logger.debug(f"QMT HTTP 18080 探测失败: {exc}")
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

    取数顺序（qmt_client.get_daily）：
      1) qmt_api 公式 RPC（58600 getMarketData）— 本环境已实证可取真实日线
      2) xtdata get_local_data — 需行情服务注册（常 58610），多数空壳
    Flask venv 无 xtquant，必须走 qmt_client 子进程（QMT 自带 pythonw）。
    """
    qmt_code = QMT_INDEX_MAP.get(code, _to_qmt_code(code))
    end = datetime.now().strftime('%Y%m%d')
    try:
        from data.qmt_client import get_qmt_client
        client = get_qmt_client()
        df = client.get_daily(qmt_code, start=start_date, end=end, count=-1)
    except Exception as e:
        logger.warning(f"读取 {code}({qmt_code}) 失败: {e}")
        return []

    if df is None or getattr(df, 'empty', True):
        return []

    result = []
    for _, row in df.iterrows():
        raw_date = row.get('date', '')
        if 'time' in row and not raw_date:
            raw_date = _from_qmt_ts(row.get('time', 0))
        ds = str(raw_date)[:10]
        if len(ds) == 8 and ds.isdigit():
            ds = f'{ds[:4]}-{ds[4:6]}-{ds[6:8]}'
        volume = float(row.get('volume', 0) or 0)
        if -(2 ** 31) <= volume < 0:
            volume += 2 ** 32
        result.append({
            'date': ds,
            'open': float(row.get('open', 0) or 0),
            'high': float(row.get('high', 0) or 0),
            'low': float(row.get('low', 0) or 0),
            'close': float(row.get('close', 0) or 0),
            'volume': volume,
        })
    return result


# ===== QMT 个股日更 =====

def qmt_update_all_stocks(
    max_retries: int = 3,
    force: bool = False,
    limit=None,
    batch_size: int = 80,
    rebuild_ledger: bool = True,
    mark_done: bool = True,
    cancel_check=None,
) -> dict:
    """通过 QMT HTTP 18080 批量更新台账内个股当日结算 K 线。

    Args:
        force: 忽略「今日已完成」标记
        limit: 最多处理只数（None=全部 pending）
        batch_size: 每批 /ohlc_batch 代码数；fallback_loop 建议 80
        rebuild_ledger: 台账过少时从 kline 自动重建
        mark_done: 结束后是否标记 qmt_daily_done
        cancel_check: 可选 callable，返回 True 时中断更新
    """
    if is_qmt_daily_done() and not force:
        return {'skipped': True, 'message': '今日QMT个股日更已完成', 'completion_ready': True}

    logger.info("[QMT个股日更] 开始...")
    if not _qmt_http_available():
        logger.error("[QMT个股日更] QMT HTTP 18080 不可用")
        return {
            'success': 0,
            'failed': 0,
            'error': 'QMT HTTP 18080不可用',
            'completion_ready': False,
        }

    _ensure_ledger_schema()

    if rebuild_ledger:
        try:
            n_ledger = len(get_all_cached_stocks())
            if n_ledger < 100:
                rebuild_stock_ledger_from_kline(min_rows=1)
        except Exception as e:
            logger.warning(f"[QMT个股日更] 台账重建跳过: {e}")

    conn = _sqlite3.connect(_LEDGER_DB)
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM stock_ledger WHERE LENGTH(code)=6 ORDER BY code")
    stocks = cur.fetchall()

    if not stocks:
        conn.close()
        if mark_done:
            _mark_qmt_daily_done()
        return {
            'success': 0, 'skipped': True, 'message': '台账为空',
            'completion_ready': False,
        }

    # pending：用 classify_stock_daily_status 分类
    target_norm = _target_trade_day_str().replace('-', '')
    target_date = f'{target_norm[:4]}-{target_norm[4:6]}-{target_norm[6:8]}'
    pending = []
    stock_codes = {code for code, _ in stocks}
    max_dates = {code: None for code in stock_codes}
    row_counts = {code: 0 for code in stock_codes}
    first_dates = {code: None for code in stock_codes}
    try:
        cur.execute(
            "SELECT code, last_date, rows, first_date "
            "FROM kline_meta WHERE period='daily'"
        )
        meta_rows = cur.fetchall()
    except _sqlite3.OperationalError:
        meta_rows = []
    for code, last_date, n_rows, first_date in meta_rows:
        if code in stock_codes:
            max_dates[code] = last_date
            row_counts[code] = int(n_rows or 0)
            first_dates[code] = first_date
    # 旧版本可能已写目标日 bar、但在全量结束前未刷新 meta；快速恢复断点。
    try:
        cur.execute(
            """UPDATE kline_meta
               SET last_date=?,
                   rows=rows + CASE
                       WHEN REPLACE(COALESCE(last_date,''),'-','') < ? THEN 1
                       ELSE 0
                   END
               WHERE period='daily'
                 AND REPLACE(COALESCE(last_date,''),'-','') < ?
                 AND EXISTS (
                     SELECT 1 FROM kline
                     WHERE kline.code=kline_meta.code
                       AND kline.period='daily' AND kline.date=?
                 )""",
            (target_date, target_norm, target_norm, target_date),
        )
        recovered_meta = int(cur.rowcount or 0)
    except _sqlite3.OperationalError:
        recovered_meta = 0
    if recovered_meta:
        conn.commit()
        cur.execute(
            "SELECT code, last_date, rows, first_date "
            "FROM kline_meta WHERE period='daily'"
        )
        for code, last_date, n_rows, first_date in cur.fetchall():
            if code in stock_codes:
                max_dates[code] = last_date
                row_counts[code] = int(n_rows or 0)
                first_dates[code] = first_date
    missing_meta = [code for code in stock_codes if max_dates[code] is None]
    if missing_meta:
        # 冷库/旧库兼容：一次 GROUP BY 补齐缺失元数据，禁止逐代码查询。
        cur.execute(
            "SELECT code, MAX(date), COUNT(1) FROM kline "
            "WHERE period='daily' GROUP BY code"
        )
        for code, last_date, n_rows in cur.fetchall():
            if code in stock_codes and max_dates[code] is None:
                max_dates[code] = last_date
                row_counts[code] = int(n_rows or 0)

    # 旧库可能只有 last_date/rows 或完全缺少 meta。仅重建这些少量代码，
    # 不在每晚对全市场日线做 GROUP BY。
    meta_repair_codes = [
        code for code in stock_codes
        if max_dates[code] is None or not first_dates.get(code)
    ]
    if meta_repair_codes:
        meta_updated_at = datetime.now().strftime('%Y%m%d %H%M%S')
        for start in range(0, len(meta_repair_codes), 500):
            _refresh_daily_meta_cursor(
                cur,
                meta_repair_codes[start:start + 500],
                meta_updated_at,
            )
        conn.commit()

    pending_info = build_stock_pending_from_ledger(
        [(code, name) for code, name in stocks], max_dates, row_counts, target_norm,
    )
    pending = pending_info['pending']

    # 优先：行数少 → 再按 max_date 升序
    pending.sort(key=lambda x: (row_counts.get(x[0], 0), (max_dates.get(x[0]) or '').replace('-', '')))
    if limit is not None and limit > 0:
        pending = pending[: int(limit)]

    logger.info(f"[QMT个股日更] 待更新 {len(pending)}/{len(stocks)} (batch_size={batch_size})")

    if not pending:
        conn.close()
        if mark_done:
            _mark_qmt_daily_done()
        return {
            'success': 0, 'skipped': True, 'message': '已是最新', 'ledger': len(stocks),
            'skipped_up_to_date': pending_info['skipped_up_to_date'],
            'pending': 0,
            'pending_date_lag': pending_info['pending_date_lag'],
            'pending_sparse': pending_info['pending_sparse'],
            'pending_repair': pending_info['pending_repair'],
            'repair_pending_codes': pending_info['repair_pending_codes'],
            'quality': {
                'total': 0,
                'repair_pending': 0,
                'reports': {},
                'scope': 'updated_codes_only',
            },
            'total': pending_info['total'],
            'completion_ready': pending_info['pending_repair'] == 0,
        }

    from data.qmt_http_client import get_qmt_http_client
    client = get_qmt_http_client()
    success = failed = no_bar = total_new = 0
    no_bar_codes = []
    no_bar_candidates = []
    active_stale_codes = []
    unverified_no_bar_codes = []
    updated_codes = []
    start_ts = time.time()
    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""

    bs = max(1, int(batch_size))
    canceled = False
    settlement_probed = False
    for batch_i in range(0, len(pending), bs):
        # 取消检查
        if cancel_check is not None and callable(cancel_check) and cancel_check():
            canceled = True
            break

        if not settlement_probed:
            probe_codes = ['000001', '603259', 'sh000300']
            probe = client.ohlc_batch(
                probe_codes, period='1d', max_batch=3, timeout=15,
            ) or {}
            probe_items = probe.get('items') or {}
            target_probes = sum(
                1 for row in probe_items.values()
                if _spot_trade_date(row) == target_norm
            )
            if target_probes < 2:
                failed = len(pending)
                logger.error(
                    f"[QMT个股日更] 18080目标日探针失败 "
                    f"{target_probes}/3 target={target_norm}"
                )
                break
            settlement_probed = True

        chunk = pending[batch_i: batch_i + bs]
        chunk_codes = [code for code, _name in chunk]
        payload = {}
        for attempt in range(max_retries):
            try:
                payload = client.ohlc_batch(
                    chunk_codes,
                    period='1d',
                    max_batch=bs,
                    timeout=30,
                ) or {}
                if payload.get('items'):
                    break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[QMT个股日更] batch@{batch_i} 失败: {e}")

        items = payload.get('items') or {}
        item_dates = {code: _spot_trade_date(row) for code, row in items.items()}

        for code in chunk_codes:
            try:
                row = items.get(code)
                if not row:
                    failed += 1
                    continue
                if item_dates.get(code) != target_norm:
                    # 旧日期只是候选；稍后用独立目标日交易集合确认。
                    no_bar_candidates.append(code)
                    continue
                if not _valid_settlement_row(row):
                    failed += 1
                    logger.error(f"[QMT个股日更] {code} 目标日 OHLCV 非法")
                    continue
                cur.execute(
                    INSERT_SQL,
                    (
                        code, 'daily', target_date,
                        float(row.get('open', 0) or 0),
                        float(row.get('high', 0) or 0),
                        float(row.get('low', 0) or 0),
                        float(row.get('close', 0) or 0),
                        float(row.get('volume', 0) or 0),
                        now_str,
                    ),
                )
                total_new += 1
                success += 1
                updated_codes.append(code)
            except Exception as e:
                logger.error(f"[{code}] 更新异常: {e}")
                failed += 1

        _refresh_daily_meta_cursor(cur, chunk_codes, now_str)
        conn.commit()
        done_n = min(batch_i + bs, len(pending))
        if done_n % 200 < bs or done_n == len(pending):
            elapsed = time.time() - start_ts
            rate = done_n / elapsed if elapsed > 0 else 0
            logger.info(
                f"[QMT个股日更] {done_n}/{len(pending)} 成功={success} 失败={failed} "
                f"新增={total_new} {rate:.1f}只/s"
            )

    if no_bar_candidates:
        verification = _verify_no_bar_candidates(
            sorted(set(no_bar_candidates)),
            target_norm,
        )
        if verification is None:
            unverified_no_bar_codes = sorted(set(no_bar_candidates))
            failed += len(unverified_no_bar_codes)
            logger.error(
                f"[QMT个股日更] {len(unverified_no_bar_codes)} 只旧日期标的"
                "无法验证是否停牌，保留失败待重试"
            )
        else:
            for code in sorted(set(no_bar_candidates)):
                if verification.get(code):
                    active_stale_codes.append(code)
                    failed += 1
                else:
                    no_bar_codes.append(code)
                    no_bar += 1
                    success += 1
            if active_stale_codes:
                logger.error(
                    f"[QMT个股日更] {len(active_stale_codes)} 只目标日有交易"
                    "但18080返回旧日期"
                )

    # 目标日 bar 已在写入前验收；完整历史质量由分批 history_repair 维护。
    post_quality = {}
    repair_pending_codes = []
    pending_repair = 0
    conn.close()
    elapsed = time.time() - start_ts
    result = {
        'success': success,
        'failed': failed,
        'no_bar': no_bar,
        'no_bar_codes': sorted(set(no_bar_codes))[:500],
        'active_stale_codes': active_stale_codes[:500],
        'unverified_no_bar_codes': unverified_no_bar_codes[:500],
        'new_rows': total_new,
        'pending': len(pending),
        'skipped_up_to_date': pending_info['skipped_up_to_date'],
        'pending_date_lag': pending_info['pending_date_lag'],
        'pending_sparse': pending_info['pending_sparse'],
        'pending_repair': pending_repair,
        'repair_pending_codes': repair_pending_codes,
        'total': pending_info['total'],
        'ledger': len(stocks),
        'elapsed_sec': round(elapsed, 1),
        'channel': 'qmt18080',
        'updated_codes': sorted(set(updated_codes)),
        'quality': {
            'total': len(post_quality),
            'repair_pending': pending_repair,
            'reports': post_quality,
            'scope': 'updated_codes_only',
        },
    }
    if canceled:
        result['canceled'] = True
    result['completion_ready'] = (
        not canceled and failed == 0 and pending_repair == 0
        and success >= len(pending)
    )
    logger.info(
        f"[QMT个股日更] 完成: 成功={success} 失败={failed} 新增={total_new}条 "
        f"耗时={elapsed:.1f}s channel={result['channel']}"
        + (" [已取消]" if canceled else "")
    )
    if result['completion_ready'] and mark_done and limit is None:
        _mark_qmt_daily_done()
    elif result['completion_ready'] and mark_done and limit is not None and len(pending) < (limit or 0):
        _mark_qmt_daily_done()
    return result


# ===== 指数数据（QMT 优先 + Tushare 兜底） =====

def _tushare_fallback_single_index(
    code: str, name: str, local_max: str, last_td_norm: str,
    cur, insert_sql: str, now_str: str
) -> int:
    """QMT 数据陈旧时，用 Tushare 补齐尾部。

    返回写入行数；失败返回 0。
    """
    start = (datetime.strptime(local_max, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
    if start > last_td_norm:
        return 0
    try:
        mapping = TUSHARE_INDEX_API_MAP.get(code)
        if not mapping:
            logger.warning(f"[Tushare兜底] {name}({code}) 无 Tushare 映射，跳过")
            return 0
        api, ts_code = mapping
        df = _fetch_tushare_index_df(api, ts_code, start, last_td_norm)
        rows = df.to_dict('records') if not df.empty else []
        if not rows:
            # 盘中/非交易时段 Tushare 日线尚未更新 → 降为 INFO
            if start > datetime.now().strftime('%Y%m%d') or start == last_td_norm:
                logger.info(
                    f"[Tushare兜底] {name}({code}) 今日日线暂未发布 "
                    f"({api}/{ts_code} {start}) — 数据已是最新"
                )
            else:
                logger.warning(
                    f"[Tushare兜底] {name}({code}) Tushare 也无新数据 "
                    f"({api}/{ts_code} {start}..{last_td_norm})"
                )
            return 0
        batch = [
            (code, 'daily', r['date'], r['open'], r['high'], r['low'],
             r['close'], r['volume'], now_str)
            for r in rows
        ]
        cur.executemany(insert_sql, batch)
        cur.connection.commit()
        last_d = max(r['date'] for r in rows)
        logger.info(
            f"[Tushare兜底] ✓ {name}({code}) 补齐 {len(batch)} 条 "
            f"{start}→{last_d} (api={api}/{ts_code})"
        )
        return len(batch)
    except Exception as e:
        logger.error(f"[Tushare兜底] {name}({code}) 失败: {e}")
        return 0


# ===== 指数目标交易日：按交易所日历 =====

def _exchange_calendar_api():
    """延迟加载 services.exchange_calendar_service（另一写集新增）。

    返回 (latest_expected_session_date, market_state)；服务尚未落地或导入
    失败时返回 None，由调用方回退旧逻辑，避免阻塞现有调度。
    """
    try:
        from services.exchange_calendar_service import (
            latest_expected_session_date as _latest_session,
            market_state as _market_state,
        )
    except Exception as exc:
        logger.debug(f"[交易日] exchange_calendar_service 未就绪，回退本地工作日: {exc}")
        return None
    return _latest_session, _market_state


def _normalize_session_date(value) -> str:
    """把日历服务返回的会话日归一化为 YYYYMMDD；无法解析返回 ''。"""
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y%m%d')
    s = str(value).strip().replace('-', '').replace('/', '')
    return s if (len(s) == 8 and s.isdigit()) else ''


def _local_weekday_session_date(code: str, now=None) -> str:
    """无日历服务时的回退：按指数所属交易所时区取最近工作日（YYYYMMDD）。"""
    market = INDEX_EXCHANGE_MARKET.get(str(code), 'a_share')
    tz_name = INDEX_EXCHANGE_TZ.get(market, 'Asia/Shanghai')
    try:
        from zoneinfo import ZoneInfo
        if now is None:
            now = datetime.now()
        if now.tzinfo is None:
            local = now.replace(tzinfo=ZoneInfo(tz_name))
        else:
            local = now.astimezone(ZoneInfo(tz_name))
        day = local.date()
    except Exception:
        day = (now or datetime.now()).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime('%Y%m%d')


def _index_session_target(code, now=None, fallback_td_norm=None):
    """指数所属交易所的 (期望会话日YYYYMMDD, 是否盘中)。

    - A股指数 → A股日历；HSI/HSTECH → 香港；
      ^N225/^KS11/^TWII/SPX/IXIC/DJI → 各自市场。
    - 优先 exchange_calendar_service：
      latest_expected_session_date(code, now) 给出期望最新会话日，
      market_state(code, now)['market_open'] 给出本地市场盘中状态。
    - 服务缺失/失败时回退：A股沿用旧 last_td_norm；其他市场按本地时区最近工作日。
    """
    api = _exchange_calendar_api()
    if api is not None:
        latest_session, market_state_fn = api
        try:
            target = _normalize_session_date(
                latest_session(str(code), now=now)
            )
            state = market_state_fn(str(code), now=now)
            is_open = bool(state.get('market_open')) if isinstance(state, dict) else False
            if target:
                return target, is_open
            logger.warning(f"[交易日] {code} 日历服务返回空会话日，回退本地工作日")
        except Exception as exc:
            logger.warning(f"[交易日] {code} 日历服务异常，回退本地工作日: {exc}")
    market = INDEX_EXCHANGE_MARKET.get(str(code), 'a_share')
    if market == 'a_share':
        return (fallback_td_norm or _local_weekday_session_date(code, now)), False
    return _local_weekday_session_date(code, now), False


def update_all_indices_qmt(max_retries: int = 3) -> dict:
    """更新指数日K：QMT 优先，若无数据或断连则 Tushare 兜底。"""
    _ensure_ledger_schema()

    qmt_targets = [
        (c, n, t) for c, n, t in PREWARM_TARGETS
        if ((c in QMT_INDEX_MAP or c in EASTMONEY_INDEX_CODES)
            and c not in BOARD_ONLY_PREWARM)
    ]
    result = {
        'success': 0, 'failed': 0, 'skipped': 0,
        'deferred': 0, 'delegated': 0, 'total': len(qmt_targets),
        'written': 0, 'channel': 'qmt', 'updated_codes': [],
    }
    outcomes = {}
    logger.info(f"[QMT指数] 开始更新 {result['total']} 个指数（QMT 优先）")

    qmt_available = _qmt_connect()
    if not qmt_available:
        logger.warning("[QMT指数] QMT 不可用，全部回退 Tushare 兜底")

    conn = _sqlite3.connect(_LEDGER_DB)
    cur = conn.cursor()
    # Keep the instant timezone-aware before converting it for each exchange.
    # A naive Shanghai wall-clock interpreted as New York time can shift the
    # expected US session by an entire day.
    now = datetime.now().astimezone()
    now_str = now.strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""
    today_norm = now.strftime('%Y%m%d')
    last_trade_day = now.date()
    if last_trade_day.weekday() >= 5:
        last_trade_day -= timedelta(days=last_trade_day.weekday() - 4)
    last_td_norm = last_trade_day.strftime('%Y%m%d')
    target_norm = last_td_norm

    for code, name, dtype in qmt_targets:
        try:
            target_norm, market_open = _index_session_target(
                code, now, fallback_td_norm=last_td_norm
            )
            if market_open:
                # 本地市场仍在盘中：不落当日正式日线，延后到该市场收盘后再结算。
                logger.info(
                    f"[QMT指数] {name}({code}) 本地市场盘中，结算日线延后 "
                    f"target={target_norm}"
                )
                result['deferred'] += 1
                outcomes[code] = {
                    'status': 'deferred',
                    'name': name,
                    'target_date': target_norm,
                    'message': '本地市场盘中，结算日线延后',
                }
                continue

            if code in EASTMONEY_INDEX_CODES:
                from data.global_index_kline import load_global_index_kline

                cur.execute(
                    "SELECT COALESCE(MAX(date),'19900101') FROM kline "
                    "WHERE code=? AND period='daily'",
                    (code,),
                )
                before_max = str(cur.fetchone()[0] or '19900101').replace('-', '')
                df = load_global_index_kline(code, 'daily')
                cur.execute(
                    "SELECT COALESCE(MAX(date),'19900101') FROM kline "
                    "WHERE code=? AND period='daily'",
                    (code,),
                )
                local_max = str(cur.fetchone()[0] or '19900101').replace('-', '')
                if df is not None and not df.empty and local_max >= target_norm:
                    written = int(
                        (pd.to_datetime(df['date'], errors='coerce')
                         > pd.to_datetime(before_max, format='%Y%m%d', errors='coerce')).sum()
                    )
                    result['success'] += 1
                    result['written'] += written
                    result['channel'] = 'qmt+eastmoney'
                    if written:
                        result['updated_codes'].append(code)
                    outcomes[code] = {
                        'status': 'success',
                        'name': name,
                        'local_max': local_max,
                        'target_date': target_norm,
                        'channel': 'eastmoney',
                    }
                else:
                    result['failed'] += 1
                    outcomes[code] = {
                        'status': 'stale_no_source',
                        'name': name,
                        'local_max': local_max,
                        'target_date': target_norm,
                        'channel': 'eastmoney',
                    }
                continue

            qmt_code = QMT_INDEX_MAP[code]
            cur.execute(
                "SELECT COALESCE(MAX(date),'19900101') FROM kline WHERE code=? AND period='daily'",
                (code,),
            )
            max_date = cur.fetchone()[0]
            max_norm = (max_date or '').replace('-', '')
            if len(max_norm) != 8 or not max_norm.isdigit():
                max_norm = '19900101'
            next_start = (datetime.strptime(max_norm, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')

            if next_start > target_norm:
                logger.debug(f"[QMT指数] {name} 已是最新")
                result['success'] += 1
                outcomes[code] = {
                    'status': 'success',
                    'name': name,
                    'local_max': max_norm,
                    'target_date': target_norm,
                }
                continue

            rows = []
            if qmt_available:
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
                accepted_rows = []
                for row in rows:
                    row_date = str(row.get('date', '')).replace('-', '')[:8]
                    if not row_date or row_date > target_norm:
                        continue
                    if row_date == target_norm and not _valid_settlement_row(row):
                        logger.error(
                            f"[QMT指数] {name}({code}) 目标日 OHLCV 非法"
                        )
                        continue
                    accepted_rows.append(row)
                batch = [
                    (
                        code, 'daily', row['date'], row['open'], row['high'],
                        row['low'], row['close'], row['volume'], now_str,
                    )
                    for row in accepted_rows
                ]
                if batch:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                result['written'] += len(batch)
                if batch:
                    result['updated_codes'].append(code)
                last_written = max(
                    (str(row[2]) for row in batch),
                    default=max_norm,
                ).replace('-', '')
                logger.info(
                    f"[QMT指数] {name}({code}) 写入 {len(batch)} 条 末bar={last_written}"
                )
                if last_written >= target_norm:
                    result['success'] += 1
                    outcomes[code] = {
                        'status': 'success',
                        'name': name,
                        'local_max': last_written,
                        'target_date': target_norm,
                    }
                else:
                    logger.warning(
                        f"[QMT指数] {name} 返回非空但仍滞后 "
                        f"last={last_written} target={target_norm}"
                    )
                    fallback_written = _tushare_fallback_single_index(
                        code,
                        name,
                        last_written,
                        target_norm,
                        cur,
                        INSERT_SQL,
                        now_str,
                    )
                    if fallback_written > 0:
                        result['written'] += fallback_written
                        result['success'] += 1
                        result['updated_codes'].append(code)
                        result['channel'] = 'qmt+tushare_fallback'
                        outcomes[code] = {
                            'status': 'success',
                            'name': name,
                            'local_max': target_norm,
                            'target_date': target_norm,
                            'channel': 'tushare_tail_fallback',
                        }
                    else:
                        result['failed'] += 1
                        outcomes[code] = {
                            'status': 'stale_no_source',
                            'name': name,
                            'local_max': last_written,
                            'target_date': target_norm,
                        }
            else:
                # 无新 bar：若本地已覆盖最近交易日（周末回退周五）→ 视为最新；否则记滞后
                if max_norm >= target_norm:
                    logger.debug(
                        f"[QMT指数] {name} 本地已最新 max={max_norm} (>= {target_norm})"
                    )
                    result['success'] += 1
                    outcomes[code] = {
                        'status': 'success',
                        'name': name,
                        'local_max': max_norm,
                        'target_date': target_norm,
                    }
                else:
                    # QMT 数据陈旧 → Tushare 兜底补齐尾部
                    logger.info(
                        f"[QMT指数] {name} QMT 无新 bar (start={next_start}) "
                        f"local_max={max_norm} < last_td={target_norm} → 回退 Tushare"
                    )
                    fallback_written = _tushare_fallback_single_index(
                        code, name, max_norm, target_norm, cur, INSERT_SQL, now_str
                    )
                    if fallback_written > 0:
                        result['written'] += fallback_written
                        result['success'] += 1
                        result['updated_codes'].append(code)
                        result['channel'] = 'qmt+tushare_fallback'
                        outcomes[code] = {
                            'status': 'success',
                            'name': name,
                            'local_max': target_norm,
                            'target_date': target_norm,
                            'channel': 'tushare_tail_fallback',
                        }
                    else:
                        # 盘后结算必须命中目标交易日；昨天的数据不能算完成。
                        result['failed'] += 1
                        outcomes[code] = {
                            'status': 'stale_no_source',
                            'name': name,
                            'local_max': max_norm,
                            'target_date': target_norm,
                        }

        except Exception as e:
            logger.error(f"[QMT指数] {name}({code}) 失败: {e}")
            result['failed'] += 1
            outcomes[code] = {
                'status': 'failed',
                'name': name,
                'error': str(e)[:200],
                'target_date': target_norm,
            }

    # 板指标的由独立板块日更维护。
    for code in BOARD_ONLY_PREWARM:
        result['delegated'] += 1
        if code in PERMANENT_SKIP_INDICES:
            def mark_permanent_skip(status):
                status.setdefault('indices', {})[code] = {
                    'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'status': 'permanent_skip',
                    'error': PERMANENT_SKIP_INDICES[code],
                    'name': next((n for c, n, _ in PREWARM_TARGETS if c == code), code),
                }
            _update_status(mark_permanent_skip)
            logger.info(f"[QMT指数] {code} permanent_skip: {PERMANENT_SKIP_INDICES[code][:60]}")
        else:
            logger.debug(f"[QMT指数] {code} 属东财板块，由 Tushare 板块日更维护")

    if outcomes:
        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        def mark_index_batch(status):
            index_status = status.setdefault('indices', {})
            valid_codes = set(outcomes) | set(PERMANENT_SKIP_INDICES)
            for stale_code in list(index_status):
                if stale_code not in valid_codes:
                    index_status.pop(stale_code, None)
            for index_code, item in outcomes.items():
                index_status[index_code] = {
                    **item,
                    'last_update': updated_at,
                }

        _update_status(mark_index_batch)

    _refresh_daily_meta_cursor(
        cur,
        [code for code, _name, _data_type in qmt_targets],
        now_str,
    )
    conn.commit()
    conn.close()
    result['completion_ready'] = (
        result['failed'] == 0 and result['success'] == result['total']
    )
    result['updated_codes'] = sorted(set(result['updated_codes']))
    logger.info(
        f"[QMT指数] 完成: 成功={result['success']}, 失败={result['failed']}, "
        f"委托板指={result['delegated']}, 写入={result['written']}, 通道={result['channel']}"
    )
    return result


# Tushare 指数代码映射：内部 code → (api, ts_code)
# 注意：与 QMT 映射可不同（如中证2000 QMT 用 000853.SH，Tushare 用 932000.CSI）
TUSHARE_INDEX_API_MAP = {
    'sh000001': ('index_daily', '000001.SH'),
    'sz399006': ('index_daily', '399006.SZ'),
    'sh000688': ('index_daily', '000688.SH'),
    'sh000300': ('index_daily', '000300.SH'),
    'sh000016': ('index_daily', '000016.SH'),
    'sh000852': ('index_daily', '000852.SH'),
    'sh000853': ('index_daily', '932000.CSI'),   # 中证2000
    'sh000985': ('index_daily', '000985.CSI'),   # 中证全指
    'HSI': ('index_global', 'HSI'),
    'HSTECH': ('index_global', 'HKTECH'),        # Tushare 全球指数代码为 HKTECH
    'BK1158': ('dc_daily', 'BK1158.DC'),
    # 800000 东方财富全A：Tushare 无稳定公开日线接口，单独记失败
}


def _get_tushare_pro():
    """获取唯一 Tushare 客户端（data_loader 工厂的兼容包装）。

    保持旧调用约定：无 token 或初始化失败时抛出 RuntimeError，
    由各调用方自行降级（返回空集/跳过 Tushare 数据源）。
    """
    from core.env_bootstrap import ensure_tushare_token
    if not ensure_tushare_token():
        raise RuntimeError('TUSHARE_TOKEN 未设置')
    from data_loader import get_tushare_pro
    pro = get_tushare_pro()
    if pro is None:
        raise RuntimeError('TUSHARE_TOKEN 未设置或 Tushare 客户端初始化失败')
    return pro


def _fetch_tushare_index_df(api: str, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """
    拉取指数/板块日线，统一为 date/open/high/low/close/volume (YYYY-MM-DD)。
    start/end: YYYYMMDD
    """
    pro = _get_tushare_pro()
    if api == 'index_daily':
        raw = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    elif api == 'index_global':
        raw = pro.index_global(ts_code=ts_code, start_date=start, end_date=end)
    elif api == 'dc_daily':
        raw = pro.dc_daily(ts_code=ts_code, start_date=start, end_date=end)
    else:
        raise ValueError(f'未知 Tushare API: {api}')

    if raw is None or raw.empty:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])

    rows = []
    for _, r in raw.iterrows():
        d = str(r.get('trade_date', '') or '').replace('-', '')
        if len(d) != 8 or not d.isdigit():
            continue
        date_s = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        vol = r.get('vol', None)
        if vol is None or (isinstance(vol, float) and pd.isna(vol)):
            vol = r.get('volume', 0) or 0
        rows.append({
            'date': date_s,
            'open': float(r.get('open', 0) or 0),
            'high': float(r.get('high', 0) or 0),
            'low': float(r.get('low', 0) or 0),
            'close': float(r.get('close', 0) or 0),
            'volume': float(vol or 0),
        })
    if not rows:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df = pd.DataFrame(rows)
    df = df.sort_values('date').drop_duplicates(subset=['date'], keep='last').reset_index(drop=True)
    return df


def update_all_indices_tushare(max_retries: int = 3) -> dict:
    """【已停用】指数禁止走 Tushare。保留函数仅防旧脚本 import 崩溃。

    正式路径请用 update_all_indices_qmt()。
    """
    logger.warning(
        "[Tushare指数] 已按策略停用：指数/个股仅 QMT，Tushare 只更新东财板块。"
        "本调用 no-op，请改用 update_all_indices_qmt()。"
    )
    return {
        'success': 0, 'failed': 0, 'total': 0, 'written': 0,
        'skipped': True, 'message': 'policy: indices via QMT only',
    }


def _update_single_index_tushare(code: str, name: str, data_type: str, max_retries: int = 3):
    """【已停用】指数禁止 Tushare。日更不再调用；返回 (ok, written_rows)。"""
    logger.warning(
        f"[Tushare指数] 策略停用调用被拦截: {name}({code}) — 请用 QMT"
    )
    return False, 0


# ===== 板块数据（Tushare 仅此路径） =====


# These wrappers intentionally resolve manager globals on every call.  Existing
# callers patching data_update_manager._get_tushare_pro, _update_status,
# _append_board_row_csv, _write_board_rows_sqlite, or Path therefore retain the
# same seam while the implementation lives in services/board_update_service.
from services.board_update_service import (
    BoardUpdateDependencies as _BoardUpdateDependencies,
    BoardUpdateService as _BoardUpdateService,
    append_board_row_csv as _board_append_row_csv,
    normalize_board_update_rows as _board_normalize_rows,
    write_board_rows_sqlite as _board_write_rows_sqlite,
    load_classified_boards as _board_load_classified_boards,
)


def _make_board_update_service() -> _BoardUpdateService:
    return _BoardUpdateService(
        _BoardUpdateDependencies(
            get_tushare_pro=_get_tushare_pro,
            load_status=_load_status,
            save_status=_save_status,
            update_status=_update_status,
            append_board_row_csv=_append_board_row_csv,
            normalize_board_update_rows=_normalize_board_update_rows,
            write_board_rows_sqlite=_write_board_rows_sqlite,
            load_classified_boards=_load_classified_boards,
            update_single_board=_update_single_board,
            lagging_board_codes=_lagging_board_codes,
            get_last_trading_date=_get_last_trading_date,
            claim_update=_claim_board_update,
            release_update=_release_board_update,
            path_factory=Path,
            logger=logger,
            now=datetime.now,
            sleep=time.sleep,
            random_uniform=random.uniform,
        )
    )


def _append_board_row_csv(csv_path, date_str, row_data) -> bool:
    return _board_append_row_csv(csv_path, date_str, row_data)


def _normalize_board_update_rows(raw: pd.DataFrame, code: str) -> pd.DataFrame:
    return _board_normalize_rows(raw, code)


def _write_board_rows_sqlite(db_path, code: str, rows: pd.DataFrame) -> None:
    return _board_write_rows_sqlite(db_path, code, rows)


def _load_classified_boards(include_types=None):
    return _board_load_classified_boards(Path, include_types)


def _get_last_trading_date():
    from data.board_api import get_last_trading_date

    return get_last_trading_date()


def _claim_board_update() -> bool:
    """Atomically claim the board-update slot owned by this manager."""
    global _update_in_progress
    with _update_lock:
        if _update_in_progress:
            return False
        _update_in_progress = True
        return True


def _release_board_update() -> None:
    """Release the manager-owned board-update slot."""
    global _update_in_progress
    with _update_lock:
        _update_in_progress = False


def _update_single_board(
    board_type: str,
    name: str,
    code: str,
    raw_override: pd.DataFrame = None,
    record_status: bool = True,
):
    return _make_board_update_service().update_single_board(
        board_type,
        name,
        code,
        raw_override=raw_override,
        record_status=record_status,
    )


def update_failed_boards(max_retries: int = 2, limit: int = 50) -> dict:
    """仅重试 update_status 中 status=failed 的板块。"""
    return _make_board_update_service().update_failed_boards(
        max_retries=max_retries,
        limit=limit,
    )


def materialize_higher_periods(codes=None, periods=None) -> dict:
    """从完整 daily 重算高周期，并原子替换每个 code/period。"""
    from data_loader import _resample
    from data.sqlite_repo import SqliteRepo

    if periods is None:
        periods = ('weekly', 'monthly', 'quarterly', 'yearly')
    periods = tuple(periods)
    _ensure_ledger_schema()
    conn = _sqlite3.connect(_LEDGER_DB)
    if codes is None:
        # 覆盖全部已有 daily、台账个股和预热指数，不能只物化白名单。
        rows = conn.execute(
            "SELECT DISTINCT code FROM kline WHERE period='daily' "
            "UNION SELECT code FROM stock_ledger"
        ).fetchall()
        codes = sorted({
            str(row[0]) for row in rows if row and row[0]
        } | {
            c for c, _, _ in PREWARM_TARGETS
            if c not in PERMANENT_SKIP_INDICES
        })
    else:
        codes = sorted({str(code) for code in codes if code})

    repo = SqliteRepo(db_path=_LEDGER_DB)
    result = {
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'written': 0,
        'total': len(codes) * len(periods),
        'quality_pending': 0,
        'quality_pending_codes': [],
    }
    for code in codes:
        try:
            cur = conn.execute(
                "SELECT date, open, high, low, close, volume FROM kline "
                "WHERE code=? AND period='daily' ORDER BY date",
                (code,),
            )
            rows = cur.fetchall()
            if not rows:
                result['skipped'] += len(periods)
                continue
            df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            quality = scan_daily_frame(df, code=code)
            if quality.get('blocking_failure'):
                result['quality_pending'] += 1
                result['quality_pending_codes'].append(code)
                result['failed'] += len(periods)
                continue
            for period in periods:
                try:
                    out = _resample(df, period)
                    if out is None or out.empty:
                        result['failed'] += 1
                        continue
                    n = repo.replace_kline_period(code, period, out)
                    result['written'] += int(n)
                    result['success'] += 1
                except Exception as e:
                    logger.debug(f"[周期物化] {code} {period}: {e}")
                    result['failed'] += 1
        except Exception as e:
            logger.warning(f"[周期物化] {code}: {e}")
            result['failed'] += len(periods)
    conn.close()
    result['quality_pending_codes'] = sorted(set(result['quality_pending_codes']))
    result['completion_ready'] = (
        result['failed'] == 0
        and result['success'] + result['skipped'] == result['total']
        and result['quality_pending'] == 0
    )
    logger.info(
        f"[周期物化] success={result['success']} failed={result['failed']} "
        f"written≈{result['written']}"
    )
    return result


def _lagging_board_codes(boards, target: str):
    """Return board codes whose daily metadata is not at ``target``.

    ``None`` is never returned: if metadata cannot be read, treating every
    requested board as lagging is the safe interpretation for a catch-up run.
    The query is batched so ``only_lagging`` does not turn into one SQLite
    round-trip per board.
    """
    ordered = list(dict.fromkeys(str(code) for _typ, _name, code in boards if code))
    if not ordered:
        return set()
    try:
        conn = _sqlite3.connect(_LEDGER_DB)
        try:
            meta = {}
            for start in range(0, len(ordered), 500):
                chunk = ordered[start:start + 500]
                placeholders = ','.join('?' for _ in chunk)
                rows = conn.execute(
                    "SELECT code, last_date, rows FROM kline_meta "
                    f"WHERE period='daily' AND code IN ({placeholders})",
                    chunk,
                ).fetchall()
                meta.update({str(code): (last_date, int(count or 0))
                             for code, last_date, count in rows})
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[板块] only_lagging 元数据读取失败，按全量补齐: %s", exc)
        return set(ordered)

    target_norm = str(target or '').replace('-', '')[:8]
    return {
        code for code in ordered
        if (
            str(meta.get(code, (None, 0))[0] or '').replace('-', '')[:8]
            != target_norm
            or int(meta.get(code, (None, 0))[1] or 0) <= 0
        )
    }


def update_all_boards(
    max_retries: int = 3,
    cancel_check=None,
    from_full=False,
    only_lagging: bool = False,
) -> dict:
    return _make_board_update_service().update_all_boards(
        max_retries=max_retries,
        cancel_check=cancel_check,
        from_full=from_full,
        only_lagging=only_lagging,
    )


# ===== Tushare 个股尾部补齐（QMT 公式口本地滞后时） =====

def _to_ts_code(code: str) -> str:
    """6 位 A 股 → Tushare ts_code。"""
    code = str(code).zfill(6)
    if code.startswith(('6', '9')) and not code.startswith('920'):
        return f'{code}.SH'
    if code.startswith(('4', '8')) or code.startswith('920'):
        return f'{code}.BJ'
    return f'{code}.SZ'


def tushare_fill_stocks_recent(
    max_codes: int = 800,
    lookback_days: int = 20,
    min_lag_days: int = 1,
) -> dict:
    """【已停用】个股禁止走 Tushare。保留 no-op 防旧脚本崩溃。

    正式路径：qmt_update_all_stocks() / fetch_qmt_kline。
    """
    logger.warning(
        "[Tushare个股补齐] 已按策略停用：个股仅 QMT。请用 qmt_update_all_stocks()。"
    )
    return {
        'success': 0, 'failed': 0, 'written': 0,
        'skipped': True, 'message': 'policy: stocks via QMT only',
    }


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
    try:
        boards = _load_classified_boards(('industry', 'concept'))
    except Exception:
        return {'success': 0, 'failed': 0, 'total': 0}

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

def _is_at_or_after(hour: int, minute: int = 0) -> bool:
    now = datetime.now()
    return (now.hour, now.minute) >= (hour, minute)


def _intraday_sync_window(now=None) -> str:
    """Return the scheduler phase used by the real QMT sync loop."""
    now = now or datetime.now()
    current = (now.hour, now.minute)
    if (9, 30) <= current < (11, 30):
        return 'morning'
    if (11, 30) <= current < (13, 0):
        return 'lunch'
    if (13, 0) <= current < (15, 0):
        return 'afternoon'
    if current < (9, 30):
        return 'preopen'
    if current < (15, 30):
        return 'settlement'
    return 'after_close'


def _startup_allows_full_update(now=None) -> bool:
    now = now or datetime.now()
    return (now.hour, now.minute) >= (15, 30)


def _stage_ready(stage: dict, *, skipped_ok: bool = False) -> bool:
    if not isinstance(stage, dict):
        return False
    if stage.get('canceled') or stage.get('error') or stage.get('deferred'):
        return False
    if stage.get('completion_ready') is not None:
        return bool(stage.get('completion_ready'))
    if stage.get('failed', 0):
        return False
    if stage.get('skipped') and not skipped_ok:
        return False
    total = stage.get('total')
    success = stage.get('success')
    if total is None:
        return True
    return int(total or 0) > 0 and int(success or 0) >= int(total or 0)


def _mark_daily_update_pending(stages, result):
    def mark(status):
        status['daily_update'] = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'status': 'pending_retry',
            'stages': stages,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
    _update_status(mark)


def _mark_daily_stage_running(stage: str):
    def mark(status):
        status['daily_update'] = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'status': 'running',
            'current_stage': stage,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
    _update_status(mark)


def _verify_board_daily_target(expected_codes=None, target_date=None) -> dict:
    """Verify that successful board rows actually reach today's trade date."""
    target = str(target_date or datetime.now().strftime('%Y-%m-%d'))
    if len(target) == 8 and '-' not in target:
        target = f'{target[:4]}-{target[4:6]}-{target[6:8]}'
    try:
        required = sorted(set(
            expected_codes
            if expected_codes is not None
            else (
                code for _board_type, _name, code
                in _load_classified_boards(('industry', 'concept'))
            )
        ))
        if not required:
            return {'target_date': target, 'verified': False, 'error': 'no_required_boards'}
        conn = _sqlite3.connect(_LEDGER_DB)
        current = 0
        found = 0
        for start in range(0, len(required), 500):
            chunk = required[start:start + 500]
            placeholders = ','.join('?' for _ in chunk)
            rows = conn.execute(
                "SELECT code, last_date FROM kline_meta "
                f"WHERE period='daily' AND code IN ({placeholders})",
                chunk,
            ).fetchall()
            found += len(rows)
            current += sum(1 for _code, last_date in rows if last_date == target)
        conn.close()
        return {
            'target_date': target,
            'required_boards': len(required),
            'stored_boards': found,
            'current_boards': current,
            'verified': current == len(required),
        }
    except Exception as exc:
        return {'target_date': target, 'verified': False, 'error': str(exc)[:160]}


def _emit_update_progress(progress_callback, stage: str, current: int, total: int, message: str) -> None:
    """Notify a progress consumer without allowing it to break the update."""
    if not callable(progress_callback):
        return
    try:
        progress_callback(stage, current, total, message)
    except Exception as exc:
        logger.warning("[日更] progress_callback(%s) ignored: %s", stage, exc)


def update_all_today(
    max_retries: int = 3,
    cancel_check=None,
    progress_callback=None,
    force: bool = False,
) -> dict:
    """Run the unified daily update under the manager-owned single-flight guard."""
    if not _claim_full_update():
        logger.warning("[日更] 已有全量更新进行中，跳过重复调用")
        return {
            'skipped': True,
            'running': True,
            'completion_ready': False,
            'message': '全量日更进行中',
        }
    try:
        return _update_all_today_impl(
            max_retries=max_retries,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
            force=force,
        )
    finally:
        _release_full_update()


def _update_all_today_impl(
    max_retries: int = 3,
    cancel_check=None,
    progress_callback=None,
    force: bool = False,
) -> dict:
    """
    每日全量更新入口（数据源纪律）：
    1. 指数 → 仅 QMT
    2. 个股 → 仅 QMT（公式口批量）
    3. 东财板块 → 仅 Tushare（dc_*）
    4. 板块周月线 → 本地重采样/加载（依赖上一步板块日线）

    Args:
        cancel_check: 可选 callable，返回 True 时中断
        progress_callback: 可选 callable(stage, current, total, message)，报告阶段边界
    """
    if is_today_updated() and not force:
        logger.info("[日更] 今天已更新，跳过")
        return {'skipped': True, 'message': '今日已更新'}
    if _is_trading_day() and not _is_at_or_after(15, 30):
        logger.info("[日更] 尚未到15:30收盘结算，延后QMT增量与板块日更")
        return {
            'deferred': True,
            'completion_ready': False,
            'pending_stages': ['indices', 'stocks', 'boards'],
            'message': '等待15:30后执行盘后增量',
        }

    logger.info("=" * 50)
    logger.info("[日更] === 开始每日全量更新 (指数/个股=QMT, 东财板块=Tushare) ===")
    result = {
        'indices': None, 'stocks': None, 'boards': None,
        'weekly_monthly': None, 'higher_periods': None,
        'history_repair': None,
    }

    qmt_formula_ok = _qmt_connect()

    # 1. 指数：仅 QMT
    logger.info("[日更] Step 1/4: 指数更新 (仅 QMT)")
    _emit_update_progress(progress_callback, 'indices', 0, 4, '指数更新开始')
    _mark_daily_stage_running('indices')
    if qmt_formula_ok:
        result['indices'] = update_all_indices_qmt(max_retries)
    else:
        logger.error("[日更] QMT 不可用，跳过指数（不回退 Tushare）")
        result['indices'] = {
            'success': 0, 'failed': 0, 'skipped': True, 'error': 'QMT不可用',
        }
    _emit_update_progress(progress_callback, 'indices', 1, 4, '指数更新完成')

    # 2. 个股：仅 QMT
    logger.info("[日更] Step 2/4: 个股日更 (QMT HTTP 18080)")
    _emit_update_progress(progress_callback, 'stocks', 1, 4, '个股更新开始')
    _mark_daily_stage_running('stocks')
    result['stocks'] = qmt_update_all_stocks(
        max_retries,
        cancel_check=cancel_check,
        force=force,
    )
    if not (result['stocks'] or {}).get('error'):
        # 取消检查：如果个股更新被取消，跳过后续步骤
        if (result['stocks'] or {}).get('canceled'):
            result['canceled'] = True
            logger.info("[日更] 个股更新被取消，跳过周期物化及后续步骤")
        else:
            # 每日推进一个有界历史修复批次；维护失败单独留痕，不阻塞当日结算。
            try:
                _mark_daily_stage_running('history_repair')
                from services.history_repair_service import repair_history_batch
                repair_limit = max(
                    0, int(os.getenv('BOARD_HISTORY_REPAIR_BATCH', '25') or 0)
                )
                if repair_limit:
                    result['history_repair'] = repair_history_batch(
                        limit=repair_limit,
                        materialize=False,
                    )
                else:
                    result['history_repair'] = {
                        'processed': 0,
                        'repaired': 0,
                        'failed': 0,
                        'skipped': True,
                        'completion_ready': True,
                        'message': 'BOARD_HISTORY_REPAIR_BATCH=0',
                    }
            except Exception as e:
                logger.warning(f"[日更] 历史修复批次异常: {e}")
                result['history_repair'] = {
                    'processed': 0,
                    'repaired': 0,
                    'failed': 1,
                    'error': str(e)[:200],
                    'completion_ready': False,
                }

            affected_codes = set((result['stocks'] or {}).get('updated_codes') or [])
            affected_codes.update((result['indices'] or {}).get('updated_codes') or [])
            affected_codes.update(
                (result['history_repair'] or {}).get('repaired_codes') or []
            )
            try:
                _mark_daily_stage_running('higher_periods')
                if affected_codes:
                    result['higher_periods'] = materialize_higher_periods(
                        codes=sorted(affected_codes)
                    )
                else:
                    result['higher_periods'] = {
                        'success': 0,
                        'failed': 0,
                        'skipped': True,
                        'total': 0,
                        'completion_ready': True,
                        'message': '本轮没有发生日线变更',
                    }
                logger.info(f"[日更] 周期物化: {result['higher_periods']}")
            except Exception as e:
                logger.warning(f"[日更] 周期物化异常: {e}")
    else:
        logger.error("[日更] QMT HTTP 18080 不可用，个股日结待重试")
    _emit_update_progress(progress_callback, 'stocks', 2, 4, '个股更新完成')

    # 3–4. 东财板块：17:00 前不把 Tushare 正式日线视为完成。
    _emit_update_progress(progress_callback, 'boards', 3, 4, '板块更新开始')
    if not _is_trading_day():
        logger.info("[日更] 非交易日，跳过全量板块与周月线刷新")
        result['boards'] = {'success': 0, 'skipped': True, 'message': '非交易日'}
        result['weekly_monthly'] = {'success': 0, 'skipped': True, 'message': '非交易日'}
    elif not _is_at_or_after(17, 0):
        result['boards'] = {
            'success': 0, 'failed': 0, 'total': 0,
            'deferred': True,
            'message': '等待17:00后验证Tushare正式板块日线',
        }
        result['weekly_monthly'] = {
            'success': 0, 'failed': 0, 'total': 0,
            'deferred': True,
            'message': '等待板块正式日线',
        }
        logger.info("[日更] 当前早于17:00，板块正式日线延后并保留重试状态")
    else:
        logger.info("[日更] Step 3/4: 东财板块更新 (仅 Tushare dc_*)")
        _mark_daily_stage_running('boards')
        try:
            result['boards'] = update_all_boards(max_retries, from_full=True)
            result['boards']['target_verification'] = _verify_board_daily_target(
                expected_codes=result['boards'].get('settled_codes'),
                target_date=result['boards'].get('target_trade_date'),
            )
            result['boards']['formal_ready'] = (
                _stage_ready(result['boards'])
                and result['boards']['target_verification'].get('verified', False)
            )
        except Exception as e:
            logger.warning(f"[日更] 板块更新异常: {e}")
            result['boards'] = {'success': 0, 'failed': 0, 'error': str(e)[:200]}
        logger.info("[日更] Step 4/4: 板块周月线刷新")
        _mark_daily_stage_running('board_higher_periods')
        try:
            result['weekly_monthly'] = refresh_all_boards_weekly_monthly()
        except Exception as e:
            logger.warning(f"[日更] 周月线刷新异常: {e}")
            result['weekly_monthly'] = {'success': 0, 'skipped': True, 'error': str(e)[:120]}

    _emit_update_progress(progress_callback, 'boards', 4, 4, '板块更新完成')
    pending_stages = []
    if not _stage_ready(result.get('indices')):
        pending_stages.append('indices')
    if not _stage_ready(result.get('stocks')):
        pending_stages.append('stocks')
    if not _stage_ready(result.get('higher_periods')):
        pending_stages.append('higher_periods')
    if _is_trading_day():
        if not result.get('boards', {}).get('formal_ready', False):
            pending_stages.append('boards')
        if not _stage_ready(result.get('weekly_monthly')):
            pending_stages.append('weekly_monthly')
    else:
        if not _stage_ready(result.get('boards'), skipped_ok=True):
            pending_stages.append('boards')
        if not _stage_ready(result.get('weekly_monthly'), skipped_ok=True):
            pending_stages.append('weekly_monthly')

    result['pending_stages'] = pending_stages
    result['completion_ready'] = not pending_stages and not result.get('canceled')
    if result['completion_ready']:
        _mark_today_done()
        logger.info("[日更] === 全部完成 ===")
    else:
        _mark_daily_update_pending(pending_stages, result)
        logger.warning(f"[日更] 未达完成门槛，保留待重试状态: {pending_stages}")
    logger.info("=" * 50)
    return result


# ===== 盘中数据同步 =====

def _intraday_sync_qmt():
    """盘中刷新顶部行情内存缓存；临时日线绝不写入 SQLite。"""
    try:
        from services.nav_spot_service import fetch_nav_spots
        result = fetch_nav_spots(force=True)
        count = len(result.get('data') or {})
        if count:
            logger.info(f"[盘中] 刷新顶部行情内存缓存 {count} 个标的")
    except Exception as e:
        logger.debug(f"[盘中] 同步异常(可忽略): {e}")


# ===== 交易日历 =====

_trade_dates_cache = None
_trade_dates_cached_at = 0


def _get_trade_cal_set(end_date: str, start_date: str = '20200101') -> set:
    """拉取 A 股开市日集合（YYYY-MM-DD）。优先 Tushare，失败返回空集。"""
    try:
        pro = _get_tushare_pro()
        end = end_date.replace('-', '')
        start = start_date.replace('-', '')
        df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end, is_open='1')
        if df is None or df.empty:
            return set()
        out = set()
        for x in df['cal_date'].astype(str):
            x = x.replace('-', '')
            if len(x) == 8 and x.isdigit():
                out.add(f'{x[:4]}-{x[4:6]}-{x[6:8]}')
        return out
    except Exception as e:
        logger.warning(f"[交易日] trade_cal 失败: {e}")
        return set()


def _is_trading_day() -> bool:
    """判断今天是否为A股交易日"""
    global _trade_dates_cache, _trade_dates_cached_at
    now = time.time()
    if _trade_dates_cache is not None and now - _trade_dates_cached_at < 3600:
        return _trade_dates_cache
    today_str = datetime.now().strftime('%Y-%m-%d')
    # 周末快速路径（无 token 时也正确）
    if datetime.now().weekday() >= 5:
        _trade_dates_cache = False
        _trade_dates_cached_at = now
        return False
    trade_dates = _get_trade_cal_set(today_str)
    if trade_dates:
        is_trade = today_str in trade_dates
        _trade_dates_cache = is_trade
        _trade_dates_cached_at = now
        return is_trade
    # 无日历时：工作日默认交易日，周末已在上方返回 False
    logger.warning("[交易日] 无日历数据，工作日默认交易日")
    _trade_dates_cache = True
    _trade_dates_cached_at = now
    return True


def _next_trade_close(now: datetime | None = None) -> datetime:
    """找到下一个交易日的收盘时间"""
    now = now or datetime.now()
    end = (now + timedelta(days=15)).strftime('%Y-%m-%d')
    trade_set = _get_trade_cal_set(end)
    if not trade_set:
        # 无日历：跳到下一工作日 15:30
        candidate = now + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.replace(hour=15, minute=30, second=0, microsecond=0)

    for offset in range(1, 15):
        candidate = now + timedelta(days=offset)
        candidate = candidate.replace(hour=15, minute=30, second=0, microsecond=0)
        if candidate.strftime('%Y-%m-%d') in trade_set:
            return candidate
    return now.replace(hour=18, minute=0) + timedelta(days=1)


def _wait_after_daily_update(now: datetime, completion_ready: bool) -> float:
    """完成后等到下一交易日 15:30；未完成则十分钟后重试。"""
    if not completion_ready:
        return 600
    return max(1, (_next_trade_close(now) - now).total_seconds())


# ===== 定时调度器（交易日历感知） =====

_scheduler_thread = None
_scheduler_running = False
_scheduler_error_count = 0
_SCHEDULER_MAX_ERRORS = 5


def _scheduler_loop():
    global _scheduler_running, _scheduler_error_count
    logger.info("[调度器] 启动（指数/个股=QMT, 东财板块=Tushare, 交易日历感知）")
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
                    phase = _intraday_sync_window(now)
                    if phase == 'morning':
                        # 上午连续交易：每 5 分钟同步一次。
                        morning_close = now.replace(hour=11, minute=30, second=0, microsecond=0)
                        wait_sec = min(300, max(1, (morning_close - now).total_seconds()))
                        _intraday_sync_qmt()
                        logger.info(f"[调度器] 盘中QMT指数同步完成，下次 {wait_sec}s后")
                    elif phase == 'afternoon':
                        # 下午连续交易：每 5 分钟同步一次。
                        afternoon_close = now.replace(hour=15, minute=0, second=0, microsecond=0)
                        wait_sec = min(300, max(1, (afternoon_close - now).total_seconds()))
                        _intraday_sync_qmt()
                        logger.info(f"[调度器] 盘中QMT指数同步完成，下次 {wait_sec}s后")
                    elif phase == 'preopen':
                        morning_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                        wait_sec = max(1, (morning_open - now).total_seconds())
                        logger.info(f"[调度器] 尚未进入上午交易窗口，{wait_sec}s后检查")
                    elif phase == 'lunch':
                        afternoon_open = now.replace(hour=13, minute=0, second=0, microsecond=0)
                        # 午休：不访问 QMT，等待下午开盘。
                        wait_sec = max(1, (afternoon_open - now).total_seconds())
                        logger.info(f"[调度器] 午休暂停盘中同步，{wait_sec}s后恢复")
                    else:
                        # 15:00-15:30 是收盘结算缓冲，不提前做全量更新。
                        wait_sec = max(1, (close_time - now).total_seconds())
                        logger.info(f"[调度器] 等待15:30收盘结算，{wait_sec}s后触发日更")
                elif is_today_updated():
                    next_trade = _next_trade_close()
                    wait_sec = (next_trade - now).total_seconds()
                    logger.info(f"[调度器] 今日已更新，下次 {(next_trade).strftime('%m-%d %H:%M')}")
                else:
                    # ===== 收盘后全量更新 =====
                    logger.info("[调度器] 收盘后触发全量更新")
                    _notify_event('data_updating', '全量更新')
                    def mark_scheduler_running(status):
                        status['scheduler'] = {
                            'last_run': now.strftime('%Y-%m-%d %H:%M:%S'),
                            'next_run': '',
                            'status': 'updating',
                            'is_trading_day': True,
                        }
                    _update_status(mark_scheduler_running)
                    update_result = update_all_today()
                    if update_result.get('completion_ready'):
                        _notify_event('data_updated', '全量完成')
                    else:
                        _notify_event('data_update_pending', '全量更新待重试')
                    wait_sec = _wait_after_daily_update(
                        now,
                        bool(update_result.get('completion_ready')),
                    )

            # 成功执行，重置错误计数
            _scheduler_error_count = 0

            def mark_scheduler(status):
                if 'scheduler' not in status or not isinstance(status.get('scheduler'), dict):
                    status['scheduler'] = {'last_run': '', 'next_run': '', 'status': 'idle'}
                status['scheduler']['last_run'] = now.strftime('%Y-%m-%d %H:%M:%S')
                next_dt = now + timedelta(seconds=wait_sec)
                status['scheduler']['next_run'] = next_dt.strftime('%Y-%m-%d %H:%M:%S')
                status['scheduler']['status'] = 'waiting'
                status['scheduler']['is_trading_day'] = bool(is_trade)
            _update_status(mark_scheduler)

            remaining = wait_sec
            while remaining > 0 and _scheduler_running:
                time.sleep(min(60, remaining))
                remaining -= 60

        except Exception as e:
            _scheduler_error_count += 1
            # 指数退避：60s → 120s → 240s → 300s（上限）
            backoff = min(60 * (2 ** (_scheduler_error_count - 1)), 300)
            logger.error(f"[调度器] 异常(第{_scheduler_error_count}次): {e}, {backoff}s后重试")

            if _scheduler_error_count >= _SCHEDULER_MAX_ERRORS:
                logger.critical(f"[调度器] 连续失败{_scheduler_error_count}次，暂停调度，需人工干预")
                _notify_event('scheduler_error', f'调度器连续失败{_scheduler_error_count}次')
                _scheduler_running = False
                break

            time.sleep(backoff)


def start_scheduler():
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name='data-scheduler')
    _scheduler_thread.start()
    logger.info("[调度器] 已启动")


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
    logger.info("[调度器] 已停止")


# ===== SSE 事件通知（通过 EventBus 统一分发） =====
# 委托给 services/update_event_log.py（已抽取为独立模块）
from services.update_event_log import (
    notify_event as _evt_notify,
    get_sse_events as _evt_get_sse,
)


def _notify_event(event_type: str, message: str = ''):
    """推送事件到 EventBus（委托给 update_event_log）"""
    _evt_notify(event_type, message)


def get_sse_events(last_index: int = 0):
    """兼容接口：从 EventBus 队列中非阻塞读取待处理事件。
    返回 (events_list, new_index)，与旧接口签名一致。
    """
    return _evt_get_sse(last_index)


# ===== 状态查询 =====

def _daily_debt_bucket(codes, target_by_code, sample_limit=5) -> dict:
    """Compare tracked daily metadata with each symbol's expected session."""
    ordered = list(dict.fromkeys(str(code) for code in codes if code))
    meta = {}
    try:
        conn = _sqlite3.connect(_LEDGER_DB)
        try:
            for start in range(0, len(ordered), 500):
                chunk = ordered[start:start + 500]
                if not chunk:
                    continue
                placeholders = ','.join('?' for _ in chunk)
                rows = conn.execute(
                    "SELECT code, last_date, rows FROM kline_meta "
                    f"WHERE period='daily' AND code IN ({placeholders})",
                    chunk,
                ).fetchall()
                meta.update({str(code): (last_date, int(count or 0))
                             for code, last_date, count in rows})
        finally:
            conn.close()
    except Exception as exc:
        return {
            'total': len(ordered), 'lagging': len(ordered), 'up_to_date': 0,
            'max_lag': 0, 'samples': [], 'error': str(exc)[:160],
        }

    lagging = []
    max_lag = 0
    for code in ordered:
        last_date, count = meta.get(code, (None, 0))
        target = str(target_by_code.get(code) or '').replace('-', '')[:8]
        latest = str(last_date or '').replace('-', '')[:8]
        if not target or latest != target or count <= 0:
            lag_days = 0
            try:
                lag_days = max(
                    0,
                    (datetime.strptime(target, '%Y%m%d')
                     - datetime.strptime(latest, '%Y%m%d')).days,
                )
            except Exception:
                lag_days = 0
            max_lag = max(max_lag, lag_days)
            lagging.append({
                'code': code,
                'last_date': last_date,
                'target_date': target,
                'rows': count,
                'lag_days': lag_days,
            })
    return {
        'total': len(ordered),
        'lagging': len(lagging),
        'up_to_date': len(ordered) - len(lagging),
        'max_lag': max_lag,
        'samples': lagging[:max(0, int(sample_limit or 0))],
    }


def scan_update_debt(sample_limit: int = 5) -> dict:
    """Scan stock, index and board daily metadata for missing latest bars."""
    try:
        from services.exchange_calendar_service import (
            MARKET_A_SHARE,
            latest_expected_session_date,
        )
        a_share_day = latest_expected_session_date(MARKET_A_SHARE)
        a_share_target = a_share_day.strftime('%Y%m%d') if a_share_day else ''
    except Exception:
        a_share_target = _target_trade_day_str().replace('-', '')[:8]

    try:
        stock_rows = get_all_cached_stocks()
        stock_codes = [
            row if isinstance(row, str) else row[0]
            for row in (stock_rows or [])
        ]
    except Exception:
        stock_codes = []

    index_codes = [
        code for code, _name, _typ in PREWARM_TARGETS
        if ((code in QMT_INDEX_MAP or code in EASTMONEY_INDEX_CODES)
            and code not in BOARD_ONLY_PREWARM
            and code not in PERMANENT_SKIP_INDICES)
    ]
    index_targets = {}
    for code in index_codes:
        target, _is_open = _index_session_target(
            code, fallback_td_norm=a_share_target
        )
        index_targets[code] = target

    try:
        board_codes = [
            code for _typ, _name, code
            in _load_classified_boards(('industry', 'concept'))
        ]
    except Exception:
        board_codes = []

    stocks = _daily_debt_bucket(
        stock_codes, {code: a_share_target for code in stock_codes}, sample_limit
    )
    indices = _daily_debt_bucket(index_codes, index_targets, sample_limit)
    boards = _daily_debt_bucket(
        board_codes, {code: a_share_target for code in board_codes}, sample_limit
    )
    needs = any(bucket.get('lagging', 0) > 0 for bucket in (stocks, indices, boards))
    return {
        'target_trade_date': a_share_target,
        'target': a_share_target,
        'needs_catchup': needs,
        'summary': (
            f"欠更扫描：个股 {stocks['lagging']}/{stocks['total']}，"
            f"指数 {indices['lagging']}/{indices['total']}，"
            f"板块 {boards['lagging']}/{boards['total']}"
        ),
        'stocks': stocks,
        'indices': indices,
        'boards': boards,
        'skipped': False,
    }

def get_update_status() -> dict:
    status = _load_status()
    boards = status.get('boards', {})
    try:
        classified_board_codes = {
            code for _board_type, _name, code
            in _load_classified_boards(('industry', 'concept'))
        }
        boards = {
            code: value for code, value in boards.items()
            if code in classified_board_codes
        }
    except Exception:
        pass
    indices = status.get('indices', {})
    required_index_codes = {
        code for code, _name, _data_type in PREWARM_TARGETS
        if ((code in QMT_INDEX_MAP or code in EASTMONEY_INDEX_CODES)
            and code not in BOARD_ONLY_PREWARM)
    }
    reported_index_codes = required_index_codes | set(PERMANENT_SKIP_INDICES)
    indices = {
        code: value for code, value in indices.items()
        if code in reported_index_codes
    }

    idx_success = sum(1 for v in indices.values() if v.get('status') == 'success')
    idx_failed = sum(1 for v in indices.values() if v.get('status') == 'failed')
    idx_failed += sum(
        1 for v in indices.values() if v.get('status') == 'stale_no_source'
    )
    idx_skipped = sum(
        1 for v in indices.values()
        if v.get('status') in ('skipped', 'permanent_skip')
    )
    board_success = sum(1 for v in boards.values() if v.get('status') == 'success')
    board_failed = sum(1 for v in boards.values() if v.get('status') == 'failed')
    board_unavailable = sum(
        1 for v in boards.values() if v.get('status') == 'unavailable'
    )
    stock_count = len(status.get('stocks', {}))
    try:
        conn = _sqlite3.connect(_LEDGER_DB)
        stock_count = int(
            conn.execute('SELECT COUNT(*) FROM stock_ledger').fetchone()[0]
        )
        conn.close()
    except Exception:
        pass

    sched = dict(status.get('scheduler') or {})
    real_alive = bool(
        _scheduler_running and _scheduler_thread and _scheduler_thread.is_alive()
    )
    sched['real_thread_alive'] = real_alive
    sched['facade_note'] = (
        'real_thread_alive 反映 data_update_manager 交易日历循环；'
        'system status.scheduler_running 为门面|真实任一侧'
    )

    return {
        'today_done': status.get('today'),
        'today_updated': is_today_updated(),
        'qmt_daily_done': status.get('qmt_daily_done'),
        'qmt_daily_completed': is_qmt_daily_done(),
        'scheduler': sched,
        'index_stats': {
            'total': len(indices),
            'success': idx_success,
            'failed': idx_failed,
            'skipped': idx_skipped,
        },
        'board_stats': {
            'total': len(boards),
            'success': board_success,
            'failed': board_failed,
            'unavailable': board_unavailable,
        },
        'stock_count': stock_count,
        'update_in_progress': _update_in_progress,
        'tushare_token_set': bool(os.environ.get('TUSHARE_TOKEN', '').strip()),
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
