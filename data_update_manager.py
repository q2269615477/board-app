"""
data_update_manager.py - 数据更新管理器（v5.2）
数据源纪律（硬）：
  - 指数 / 个股日K / 盘中同步 → **QMT 优先**（qmt_api 公式口 @58600）
  - QMT 数据陈旧超过 1 个交易日 → **自动回退 Tushare** 补齐尾部
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

# ===== QMT 指数映射表（仅 QMT；无映射则跳过，不走 Tushare）=====
QMT_INDEX_MAP = {
    'sh000001': '000001.SH', 'sz399006': '399006.SZ',
    'sh000688': '000688.SH', 'sh000300': '000300.SH',
    'sh000016': '000016.SH', 'sh000852': '000852.SH',
    'sh000853': '000853.SH', 'sh000985': '000985.SH',
    'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
}
# 东财板指标的：不在 QMT 指数日更内，由 update_all_boards（Tushare）维护
BOARD_ONLY_PREWARM = frozenset({'BK1158', '800000'})
# 800000 东方财富全A：无稳定 QMT 代码映射，亦无可靠公开日线源 → 永久 skip（不记 failed）
PERMANENT_SKIP_INDICES = {
    '800000': '东方财富全A：无 QMT/稳定日线源，永久 skip；面板可不展示或用成分聚合替代',
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


def _load_status() -> dict:
    with _update_lock:
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


def rebuild_stock_ledger_from_kline(min_rows: int = 1) -> dict:
    """从 kline 表重建 stock_ledger（仅 6 位 A 股代码）。

    历史问题：台账仅 4 条时 qmt_update_all_stocks 几乎不跑，库内 5000+ 只停滞。
    """
    conn = _get_ledger_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT code, COUNT(1) AS n, MAX(date) AS last_d
        FROM kline
        WHERE period='daily' AND length(code)=6 AND code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY code
        HAVING n >= ?
        ORDER BY code
        """,
        (int(min_rows),),
    )
    rows = cur.fetchall()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    for code, n, last_d in rows:
        cur.execute(
            "INSERT OR REPLACE INTO stock_ledger (code, name, first_cached, last_updated) "
            "VALUES (?, COALESCE((SELECT name FROM stock_ledger WHERE code=?), ''), "
            "COALESCE((SELECT first_cached FROM stock_ledger WHERE code=?), ?), ?)",
            (code, code, code, now, now),
        )
        inserted += 1
    conn.commit()
    conn.close()
    logger.info(f"[台账] 自 kline 重建 {inserted} 只个股 (min_rows={min_rows})")
    return {'codes': inserted, 'min_rows': min_rows}


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
        result.append({
            'date': ds,
            'open': float(row.get('open', 0) or 0),
            'high': float(row.get('high', 0) or 0),
            'low': float(row.get('low', 0) or 0),
            'close': float(row.get('close', 0) or 0),
            'volume': float(row.get('volume', 0) or 0),
        })
    return result


# ===== QMT 个股日更 =====

def qmt_update_all_stocks(
    max_retries: int = 3,
    force: bool = False,
    limit=None,
    batch_size: int = 40,
    rebuild_ledger: bool = True,
    mark_done: bool = True,
) -> dict:
    """通过 QMT（公式口批量优先）增量更新台账内个股日K。

    Args:
        force: 忽略「今日已完成」标记
        limit: 最多处理只数（None=全部 pending）
        batch_size: 每批 get_daily_batch 代码数
        rebuild_ledger: 台账过少时从 kline 自动重建
        mark_done: 结束后是否标记 qmt_daily_done
    """
    if is_qmt_daily_done() and not force:
        return {'skipped': True, 'message': '今日QMT个股日更已完成'}

    logger.info("[QMT个股日更] 开始...")
    if not _qmt_connect():
        logger.error("[QMT个股日更] QMT 连接失败")
        return {'success': 0, 'failed': 0, 'error': 'QMT未连接'}

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
        return {'success': 0, 'skipped': True, 'message': '台账为空'}

    # pending：稀疏历史（行数少）或 max_date 落后于日历今天
    today_norm = datetime.now().strftime('%Y%m%d')
    pending = []
    max_dates = {}
    row_counts = {}
    for code, name in stocks:
        cur.execute(
            "SELECT MAX(date), COUNT(1) FROM kline WHERE code=? AND period='daily'",
            (code,),
        )
        max_date, n_rows = cur.fetchone()
        max_norm = (max_date or '').replace('-', '')
        max_dates[code] = max_norm if len(max_norm) == 8 and max_norm.isdigit() else '19900101'
        row_counts[code] = int(n_rows or 0)
        # 稀疏 OR 未到今天 → 需要回填/增量（INSERT OR REPLACE 可补历史空洞）
        if max_date is None or max_norm < today_norm or row_counts[code] < 120:
            pending.append((code, name or ''))

    # 优先：行数少 → 再按 max_date 升序
    pending.sort(key=lambda x: (row_counts.get(x[0], 0), max_dates.get(x[0], '19900101')))
    if limit is not None and limit > 0:
        pending = pending[: int(limit)]

    logger.info(f"[QMT个股日更] 待更新 {len(pending)}/{len(stocks)} (batch_size={batch_size})")

    if not pending:
        conn.close()
        if mark_done:
            _mark_qmt_daily_done()
        return {'success': 0, 'skipped': True, 'message': '已是最新', 'ledger': len(stocks)}

    from data.qmt_client import get_qmt_client
    client = get_qmt_client()
    success = failed = total_new = 0
    start_ts = time.time()
    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""

    bs = max(1, int(batch_size))
    for batch_i in range(0, len(pending), bs):
        chunk = pending[batch_i: batch_i + bs]
        # 稀疏股从 2020 起全量拉；稠密股从 min(max_date-60d, …) 增量
        need_full = any(row_counts.get(c, 0) < 120 for c, _ in chunk)
        if need_full:
            batch_start = '20200101'
        else:
            starts = []
            for code, _ in chunk:
                mn = max_dates.get(code, '19900101')
                try:
                    ns = (datetime.strptime(mn, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
                except Exception:
                    ns = '20200101'
                starts.append(max(ns, '20200101'))
            batch_start = min(starts) if starts else '20200101'

        qmt_codes = []
        code_map = {}  # qmt_code -> panel code
        for code, _name in chunk:
            qc = _to_qmt_code(code)
            qmt_codes.append(qc)
            code_map[qc] = code

        data_map = {}
        for attempt in range(max_retries):
            try:
                data_map = client.get_daily_batch(qmt_codes, start=batch_start, end='', count=-1) or {}
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[QMT个股日更] batch@{batch_i} 失败: {e}")

        for qc, code in code_map.items():
            try:
                df = data_map.get(qc)
                if df is None or getattr(df, 'empty', True):
                    # 单只兜底：稀疏则全历史
                    start_one = '20200101' if row_counts.get(code, 0) < 120 else max_dates.get(code, '20200101')
                    rows = fetch_qmt_kline(code, start_one)
                else:
                    rows = []
                    for _, row in df.iterrows():
                        ds = str(row.get('date', ''))[:10]
                        if len(ds) == 8 and ds.isdigit():
                            ds = f'{ds[:4]}-{ds[4:6]}-{ds[6:8]}'
                        rows.append({
                            'date': ds,
                            'open': float(row.get('open', 0) or 0),
                            'high': float(row.get('high', 0) or 0),
                            'low': float(row.get('low', 0) or 0),
                            'close': float(row.get('close', 0) or 0),
                            'volume': float(row.get('volume', 0) or 0),
                        })
                # 关键 bar 均 INSERT OR REPLACE：可补历史空洞，不丢本地更新的 bar
                if rows:
                    batch_rows = [
                        (code, 'daily', r['date'], r['open'], r['high'], r['low'], r['close'], r['volume'], now_str)
                        for r in rows
                    ]
                    cur.executemany(INSERT_SQL, batch_rows)
                    total_new += len(batch_rows)
                    last_d = max(r['date'] for r in rows).replace('-', '')
                    if last_d > max_dates.get(code, ''):
                        max_dates[code] = last_d
                    row_counts[code] = max(row_counts.get(code, 0), len(rows))
                success += 1
            except Exception as e:
                logger.error(f"[{code}] 更新异常: {e}")
                failed += 1

        conn.commit()
        done_n = min(batch_i + bs, len(pending))
        if done_n % 200 < bs or done_n == len(pending):
            elapsed = time.time() - start_ts
            rate = done_n / elapsed if elapsed > 0 else 0
            logger.info(
                f"[QMT个股日更] {done_n}/{len(pending)} 成功={success} 失败={failed} "
                f"新增={total_new} {rate:.1f}只/s"
            )

    # kline_meta
    try:
        pending_codes = [c for c, _ in pending]
        META_BATCH = 500
        for batch_start in range(0, len(pending_codes), META_BATCH):
            batch = pending_codes[batch_start:batch_start + META_BATCH]
            placeholders = ','.join(['?'] * len(batch))
            cur.execute(
                f"""INSERT OR REPLACE INTO kline_meta (code, period, rows, first_date, last_date, updated_at)
                    SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ?
                    FROM kline WHERE code IN ({placeholders}) AND period='daily'
                    GROUP BY code""",
                [now_str] + batch,
            )
        conn.commit()
    except Exception:
        pass

    conn.close()
    elapsed = time.time() - start_ts
    result = {
        'success': success,
        'failed': failed,
        'new_rows': total_new,
        'pending': len(pending),
        'ledger': len(stocks),
        'elapsed_sec': round(elapsed, 1),
        'channel': getattr(client, 'active_channel', None),
    }
    logger.info(
        f"[QMT个股日更] 完成: 成功={success} 失败={failed} 新增={total_new}条 "
        f"耗时={elapsed:.1f}s channel={result['channel']}"
    )
    if mark_done and limit is None:
        _mark_qmt_daily_done()
    elif mark_done and limit is not None and len(pending) < (limit or 0):
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

def update_all_indices_qmt(max_retries: int = 3) -> dict:
    """更新指数日K：QMT 优先，若无数据或断连则 Tushare 兜底。"""
    qmt_targets = [
        (c, n, t) for c, n, t in PREWARM_TARGETS
        if c in QMT_INDEX_MAP and c not in BOARD_ONLY_PREWARM
    ]
    result = {
        'success': 0, 'failed': 0, 'skipped': 0,
        'total': len(qmt_targets), 'written': 0, 'channel': 'qmt',
    }
    logger.info(f"[QMT指数] 开始更新 {result['total']} 个指数（QMT 优先）")

    qmt_available = _qmt_connect()
    if not qmt_available:
        logger.warning("[QMT指数] QMT 不可用，全部回退 Tushare 兜底")

    conn = _sqlite3.connect(_LEDGER_DB)
    cur = conn.cursor()
    now_str = datetime.now().strftime('%Y%m%d %H%M%S')
    INSERT_SQL = """INSERT OR REPLACE INTO kline
                     (code, period, date, open, high, low, close, volume, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)"""
    today_norm = datetime.now().strftime('%Y%m%d')

    for code, name, dtype in qmt_targets:
        try:
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

            if next_start > today_norm:
                logger.debug(f"[QMT指数] {name} 已是最新")
                result['success'] += 1
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
                batch = [
                    (code, 'daily', r['date'], r['open'], r['high'], r['low'], r['close'], r['volume'], now_str)
                    for r in rows
                ]
                cur.executemany(INSERT_SQL, batch)
                conn.commit()
                result['written'] += len(batch)
                last_written = max(r['date'] for r in rows).replace('-', '')
                logger.info(
                    f"[QMT指数] {name}({code}) 写入 {len(batch)} 条 末bar={last_written}"
                )
                result['success'] += 1
            else:
                # 无新 bar：若本地已覆盖最近交易日（周末回退周五）→ 视为最新；否则记滞后
                last_td = datetime.now().date()
                if last_td.weekday() >= 5:
                    last_td = last_td - timedelta(days=last_td.weekday() - 4)
                last_td_norm = last_td.strftime('%Y%m%d')
                if max_norm >= last_td_norm:
                    logger.debug(
                        f"[QMT指数] {name} 本地已最新 max={max_norm} (>= {last_td_norm})"
                    )
                    result['success'] += 1
                else:
                    # QMT 数据陈旧 → Tushare 兜底补齐尾部
                    logger.info(
                        f"[QMT指数] {name} QMT 无新 bar (start={next_start}) "
                        f"local_max={max_norm} < last_td={last_td_norm} → 回退 Tushare"
                    )
                    fallback_written = _tushare_fallback_single_index(
                        code, name, max_norm, last_td_norm, cur, INSERT_SQL, now_str
                    )
                    if fallback_written > 0:
                        result['written'] += fallback_written
                        result['success'] += 1
                        result['channel'] = 'qmt+tushare_fallback'
                    else:
                        # 盘中 Tushare 日线未发布 → 数据已是最新（昨天），不算失败
                        yest = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
                        if max_norm >= yest:
                            logger.debug(
                                f"[QMT指数] {name} 无新 bar 但本地已达 {max_norm}（今日日线未发布）— 视为已最新"
                            )
                            result['success'] += 1
                        else:
                            result['failed'] += 1
                            status = _load_status()
                            status.setdefault('indices', {})[code] = {
                            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'status': 'stale_no_source',
                            'name': name,
                            'local_max': max_norm,
                            'last_td': last_td_norm,
                        }
                        _save_status(status)

        except Exception as e:
            logger.error(f"[QMT指数] {name}({code}) 失败: {e}")
            result['failed'] += 1

    # 板指标的 / 永久 skip
    for code in BOARD_ONLY_PREWARM:
        result['skipped'] += 1
        if code in PERMANENT_SKIP_INDICES:
            status = _load_status()
            status.setdefault('indices', {})[code] = {
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'permanent_skip',
                'error': PERMANENT_SKIP_INDICES[code],
                'name': next((n for c, n, _ in PREWARM_TARGETS if c == code), code),
            }
            _save_status(status)
            logger.info(f"[QMT指数] {code} permanent_skip: {PERMANENT_SKIP_INDICES[code][:60]}")
        else:
            logger.debug(f"[QMT指数] {code} 属东财板块，由 Tushare 板块日更维护")

    conn.close()
    logger.info(
        f"[QMT指数] 完成: 成功={result['success']}, 失败={result['failed']}, "
        f"跳过板指={result['skipped']}, 写入={result['written']}, 通道={result['channel']}"
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
    """获取 Tushare pro API（优先 data_loader 全局，其次环境变量）"""
    try:
        from data_loader import _tushare_pro
        if _tushare_pro is not None:
            return _tushare_pro
    except Exception:
        pass
    import os
    import tushare as ts
    token = os.environ.get('TUSHARE_TOKEN', '').strip()
    if not token:
        raise RuntimeError('TUSHARE_TOKEN 未设置')
    ts.set_token(token)
    return ts.pro_api()


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

def _update_single_board(board_type: str, name: str, code: str) -> bool:
    from data.board_api import get_board_kline
    from data_loader import DATA_ROOT, _safe_filename
    try:
        logger.info(f"[板块] 更新 {name}({code})")
        df = get_board_kline(board_type, code)
        if df is not None and not df.empty:
            from data.sqlite_repo import get_sqlite_repo
            subdir = DATA_ROOT / ('行业板块K线数据' if board_type == 'industry' else '概念板块K线数据')
            subdir.mkdir(parents=True, exist_ok=True)
            # Windows 非法文件名 → Errno 22；必须 sanitize
            safe_name = _safe_filename(str(name or code))
            csv_path = subdir / f'{safe_name}_{code}.csv'

            # 兼容旧文件名（含特殊字符）若存在则优先合并
            legacy = subdir / f'{name}_{code}.csv'
            merge_src = None
            if csv_path.exists():
                merge_src = csv_path
            elif legacy.exists() and legacy != csv_path:
                merge_src = legacy

            if merge_src is not None:
                try:
                    local = pd.read_csv(merge_src, encoding='utf-8-sig')
                    merged = pd.concat([local, df], ignore_index=True)
                    date_col = '日期' if '日期' in merged.columns else ('date' if 'date' in merged.columns else None)
                    if date_col:
                        merged = merged.drop_duplicates(subset=[date_col], keep='last')
                        merged = merged.sort_values(date_col).reset_index(drop=True)
                    merged.to_csv(csv_path, index=False, encoding='utf-8-sig')
                except Exception:
                    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            else:
                df.to_csv(csv_path, index=False, encoding='utf-8-sig')

            try:
                db = get_sqlite_repo()
                # 统一列名后再写库
                wdf = df.copy()
                colmap = {
                    '日期': 'date', '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low', '成交量': 'volume',
                }
                wdf = wdf.rename(columns={k: v for k, v in colmap.items() if k in wdf.columns})
                if 'date' in wdf.columns:
                    db.save_kline(code, 'daily', wdf)
            except Exception as e:
                logger.debug(f"[板块] SQLite 写 {code} 跳过: {e}")

            status = _load_status()
            status['boards'][code] = {
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'success',
                'name': name,
            }
            _save_status(status)
            logger.info(f"[板块] {name}({code}) 更新成功")
            return True
        else:
            logger.warning(f"[板块] {name}({code}) 返回空数据")
            return False
    except Exception as e:
        error_msg = str(e)[:200]
        status = _load_status()
        status['boards'][code] = {
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'failed',
            'error': error_msg,
            'name': name,
        }
        _save_status(status)
        logger.error(f"[板块] {name}({code}) 更新失败: {error_msg}")
        return False


def update_failed_boards(max_retries: int = 2, limit: int = 50) -> dict:
    """仅重试 update_status 中 status=failed 的板块（修 Errno 22 后首选用）。"""
    try:
        from core.env_bootstrap import force_direct_network
        force_direct_network()
    except Exception:
        pass
    status = _load_status()
    boards_st = status.get('boards') or {}
    failed_codes = [
        code for code, v in boards_st.items()
        if isinstance(v, dict) and v.get('status') == 'failed'
    ]
    if not failed_codes:
        return {'success': 0, 'failed': 0, 'total': 0, 'message': '无失败板块'}

    # 从分类表还原 type/name
    import json as _json
    cf = Path('static') / 'board_classification.json'
    if not cf.exists():
        return {'success': 0, 'failed': 0, 'error': 'board_classification.json 缺失'}
    with open(cf, 'r', encoding='utf-8') as f:
        cats = _json.load(f).get('categories', [])
    meta = {}
    for cat in cats:
        for b in cat.get('boards', []):
            meta[b.get('code')] = (b.get('type'), b.get('name'), b.get('code'))

    targets = []
    for code in failed_codes:
        if code in meta:
            targets.append(meta[code])
    if limit and limit > 0:
        targets = targets[: int(limit)]

    result = {'success': 0, 'failed': 0, 'total': len(targets)}
    logger.info(f"[板块重试] 共 {len(targets)} 个失败项 (limit={limit})")
    for btype, name, code in targets:
        ok = False
        for attempt in range(max_retries):
            ok = _update_single_board(btype, name, code)
            if ok:
                break
            time.sleep(1.0 + attempt)
        if ok:
            result['success'] += 1
        else:
            result['failed'] += 1
        time.sleep(random.uniform(0.3, 0.8))
    logger.info(f"[板块重试] 完成 success={result['success']} failed={result['failed']}")
    return result


def materialize_higher_periods(codes=None, periods=None) -> dict:
    """从日线物化周/月/季/年线到 SQLite（Phase3 多周期正确性基础）。"""
    from data_loader import _resample, _db_write_kline

    if periods is None:
        periods = ('weekly', 'monthly', 'quarterly', 'yearly')
    if codes is None:
        codes = [c for c, _, _ in PREWARM_TARGETS] + ['600519', '600036']

    conn = _sqlite3.connect(_LEDGER_DB)
    result = {'success': 0, 'failed': 0, 'written': 0, 'total': len(codes) * len(periods)}
    for code in codes:
        try:
            cur = conn.execute(
                "SELECT date, open, high, low, close, volume FROM kline "
                "WHERE code=? AND period='daily' ORDER BY date",
                (code,),
            )
            rows = cur.fetchall()
            if not rows:
                result['failed'] += len(periods)
                continue
            df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            for period in periods:
                try:
                    out = _resample(df, period)
                    if out is None or out.empty:
                        result['failed'] += 1
                        continue
                    n = _db_write_kline(code, period, out) or len(out)
                    result['written'] += int(n) if isinstance(n, int) else len(out)
                    result['success'] += 1
                except Exception as e:
                    logger.debug(f"[周期物化] {code} {period}: {e}")
                    result['failed'] += 1
        except Exception as e:
            logger.warning(f"[周期物化] {code}: {e}")
            result['failed'] += len(periods)
    conn.close()
    logger.info(
        f"[周期物化] success={result['success']} failed={result['failed']} "
        f"written≈{result['written']}"
    )
    return result


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
    每日全量更新入口（数据源纪律）：
    1. 指数 → 仅 QMT
    2. 个股 → 仅 QMT（公式口批量）
    3. 东财板块 → 仅 Tushare（dc_*）
    4. 板块周月线 → 本地重采样/加载（依赖上一步板块日线）
    """
    if is_today_updated():
        logger.info("[日更] 今天已更新，跳过")
        return {'skipped': True, 'message': '今日已更新'}

    logger.info("=" * 50)
    logger.info("[日更] === 开始每日全量更新 (指数/个股=QMT, 东财板块=Tushare) ===")
    result = {'indices': None, 'stocks': None, 'boards': None, 'weekly_monthly': None}

    qmt_ok = _qmt_connect()

    # 1. 指数：仅 QMT
    logger.info("[日更] Step 1/4: 指数更新 (仅 QMT)")
    if qmt_ok:
        result['indices'] = update_all_indices_qmt(max_retries)
    else:
        logger.error("[日更] QMT 不可用，跳过指数（不回退 Tushare）")
        result['indices'] = {
            'success': 0, 'failed': 0, 'skipped': True, 'error': 'QMT不可用',
        }

    # 2. 个股：仅 QMT
    logger.info("[日更] Step 2/4: 个股日更 (仅 QMT 公式口)")
    if qmt_ok:
        result['stocks'] = qmt_update_all_stocks(max_retries)
        # 日线到位后物化周/月/季/年（Phase3 多周期）
        try:
            prewarm_codes = [c for c, _, _ in PREWARM_TARGETS if c not in PERMANENT_SKIP_INDICES]
            result['higher_periods'] = materialize_higher_periods(codes=prewarm_codes)
            logger.info(f"[日更] 周期物化: {result['higher_periods']}")
        except Exception as e:
            logger.warning(f"[日更] 周期物化异常: {e}")
    else:
        logger.error("[日更] QMT 不可用，跳过个股（不回退 Tushare）")
        result['stocks'] = {'success': 0, 'skipped': True, 'message': 'QMT不可用'}

    # 3–4. 东财板块：仅 Tushare（非交易日跳过全量）
    if not _is_trading_day():
        logger.info("[日更] 非交易日，跳过全量板块与周月线刷新")
        result['boards'] = {'success': 0, 'skipped': True, 'message': '非交易日'}
        result['weekly_monthly'] = {'success': 0, 'skipped': True, 'message': '非交易日'}
    else:
        logger.info("[日更] Step 3/4: 东财板块更新 (仅 Tushare dc_*)")
        try:
            result['boards'] = update_all_boards(max_retries)
        except Exception as e:
            logger.warning(f"[日更] 板块更新异常: {e}")
            result['boards'] = {'success': 0, 'failed': 0, 'error': str(e)[:200]}
        logger.info("[日更] Step 4/4: 板块周月线刷新")
        try:
            result['weekly_monthly'] = refresh_all_boards_weekly_monthly()
        except Exception as e:
            logger.warning(f"[日更] 周月线刷新异常: {e}")
            result['weekly_monthly'] = {'success': 0, 'skipped': True, 'error': str(e)[:120]}

    _mark_today_done()
    logger.info("[日更] === 全部完成 ===")
    logger.info("=" * 50)
    return result


# ===== 盘中数据同步 =====

def _intraday_sync_qmt():
    """盘中每 5 分钟执行一次：通过 qmt_client 子进程拉取最新指数数据"""
    try:
        from core.lifecycle import is_qmt_available
        if not is_qmt_available():
            return

        from data.qmt_client import get_qmt_client
        from data.sqlite_repo import get_sqlite_repo
        from core.cache import get_cache

        client = get_qmt_client()
        db = get_sqlite_repo()
        synced = 0
        start = (datetime.now() - timedelta(days=3)).strftime('%Y%m%d')

        intraday_targets = [
            ('sh000001','上证指数','000001.SH'), ('sz399006','创业板指','399006.SZ'),
            ('sh000688','科创50','000688.SH'), ('sh000300','沪深300','000300.SH'),
            ('sh000016','上证50','000016.SH'), ('sh000852','中证1000','000852.SH'),
            ('sh000853','中证2000','000853.SH'), ('sh000985','中证全指','000985.SH'),
            ('HSI','恒生指数','HSI.HK'), ('HSTECH','恒生科技','HSTECH.HK'),
        ]
        for code, name, qmt_code in intraday_targets:
            try:
                # 公式口优先（get_daily），xtdata 空壳时仍可同步日线
                df = client.get_daily(qmt_code, start=start, count=-1)
                if df is not None and not df.empty:
                    db.save_kline(code, 'daily', df)
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


def _get_trade_cal_set(end_date: str, start_date: str = '20200101') -> set:
    """拉取 A 股开市日集合（YYYY-MM-DD）。优先 Tushare，失败返回空集。"""
    try:
        try:
            from core.env_bootstrap import ensure_tushare_token
            ensure_tushare_token()
        except Exception:
            pass
        pro = None
        try:
            from data_loader import _tushare_pro as pro
        except Exception:
            pro = None
        if pro is None:
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


def _next_trade_close() -> datetime:
    """找到下一个交易日的收盘时间"""
    now = datetime.now()
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

            # 成功执行，重置错误计数
            _scheduler_error_count = 0

            status = _load_status()
            if 'scheduler' not in status or not isinstance(status.get('scheduler'), dict):
                status['scheduler'] = {'last_run': '', 'next_run': '', 'status': 'idle'}
            status['scheduler']['last_run'] = now.strftime('%Y-%m-%d %H:%M:%S')
            next_dt = now + timedelta(seconds=wait_sec)
            status['scheduler']['next_run'] = next_dt.strftime('%Y-%m-%d %H:%M:%S')
            status['scheduler']['status'] = 'waiting'
            status['scheduler']['is_trading_day'] = bool(is_trade)
            _save_status(status)

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


# ===== SSE 事件通知（通过 EventBus 统一分发） =====

def _notify_event(event_type: str, message: str = ''):
    """推送事件到 EventBus（替代旧的 _events 列表）"""
    try:
        from core.events import get_event_bus
        get_event_bus().push_sse(event_type, {
            'type': event_type,
            'message': message,
            'time': datetime.now().strftime('%H:%M:%S'),
        })
    except Exception as e:
        logger.debug(f"[SSE] 推送事件失败: {e}")


def get_sse_events(last_index: int = 0):
    """兼容接口：从 EventBus 队列中非阻塞读取待处理事件。
    返回 (events_list, new_index)，与旧接口签名一致。
    """
    try:
        from core.events import get_event_bus
        bus = get_event_bus()
        events = []
        while True:
            evt = bus.get_sse_events(timeout=0.01)
            if evt is None:
                break
            event_type, data = evt
            events.append(data if isinstance(data, dict) else {'type': event_type, 'message': str(data)})
        return events, 0
    except Exception:
        return [], 0


# ===== 状态查询 =====

def get_update_status() -> dict:
    status = _load_status()
    boards = status.get('boards', {})
    indices = status.get('indices', {})

    idx_success = sum(1 for v in indices.values() if v.get('status') == 'success')
    idx_failed = sum(1 for v in indices.values() if v.get('status') == 'failed')
    idx_skipped = sum(1 for v in indices.values() if v.get('status') == 'skipped')
    board_success = sum(1 for v in boards.values() if v.get('status') == 'success')
    board_failed = sum(1 for v in boards.values() if v.get('status') == 'failed')

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
        'board_stats': {'total': len(boards), 'success': board_success, 'failed': board_failed},
        'stock_count': len(status.get('stocks', {})),
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
