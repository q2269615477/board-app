"""
data_loader.py - 统一数据加载层
v4.2：SQLite 主存储 + Tushare 板块 / QMT 指数个股 双源
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import logging
import pandas as pd
import sqlite3
import re
import os
from pathlib import Path
from datetime import datetime, timedelta
import time
import atexit
import threading

# ===== Tushare 配置（获取东财板块数据） =====
_tushare_pro = None
try:
    import tushare as ts
    TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590')
    try:
        ts.set_token(TUSHARE_TOKEN)
        _tushare_pro = ts.pro_api()
        logging.info('[Tushare] 初始化成功')
    except PermissionError:
        logging.warning('[Tushare] 无法写入token文件（沙箱限制），将使用MCP替代')
        _tushare_pro = None
    except Exception as e:
        logging.warning(f'[Tushare] 初始化失败: {e}')
        _tushare_pro = None
except ImportError:
    logging.warning('[Tushare] tushare模块未安装')



# 绝对路径：避免 Flask 工作目录导致路径错位
DATA_ROOT = Path(__file__).resolve().parent / 'data'
CACHE_DIR = DATA_ROOT / '个股K线缓存'
HK_CACHE_DIR = DATA_ROOT / '港股K线缓存'
STOCK_DATA_ROOT = DATA_ROOT / '个股数据'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
STOCK_DATA_ROOT.mkdir(parents=True, exist_ok=True)

DB_PATH = str(DATA_ROOT / 'kline.db')
_WIN_BAD = re.compile(r'[\\/:*?"<>|]')

def _safe_filename(name: str) -> str:
    return _WIN_BAD.sub('_', name)

_COLUMN_REMAP_RAW = {
    '日期': 'date', '开盘': 'open', '收盘': 'close',
    '最高': 'high', '最低': 'low', '成交量': 'volume',
    '成交额': 'amount', '涨跌幅': 'change_pct', '换手率': 'turnover',
}

_spot_cache = {}
_spot_cache_time = 0

_conn_cache = {}
def _get_db() -> sqlite3.Connection:
    """获取线程安全的数据库连接"""
    tid = threading.get_ident()
    if tid not in _conn_cache:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _conn_cache[tid] = conn
    return _conn_cache[tid]


def _close_db_connections():
    """关闭所有线程缓存的数据库连接"""
    for conn in _conn_cache.values():
        try:
            conn.close()
        except Exception:
            pass
    _conn_cache.clear()

atexit.register(_close_db_connections)

import threading

def _today_str() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df = df.rename(columns=_COLUMN_REMAP_RAW)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    for col in ['open', 'close', 'high', 'low', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df[['date', 'open', 'high', 'low', 'close', 'volume']].dropna()


def _resample(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    if df.empty:
        return df  # 周末直接返回本地缓存
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    rule = {'weekly': 'W-FRI', 'monthly': 'MS', 'quarterly': 'QE', 'yearly': 'YE'}.get(freq.lower(), freq)
    resampled = df.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna().reset_index()
    resampled['date'] = resampled['date'].dt.strftime('%Y-%m-%d')
    return resampled


# ===== SQLite 核心读写 =====

def _db_write_kline(code: str, period: str, df: pd.DataFrame):
    """将DataFrame写入SQLite（INSERT OR REPLACE）"""
    if df.empty:
        return
    from data.sqlite_repo import normalize_date
    conn = _get_db()
    cur = conn.cursor()
    rows = 0
    for _, r in df.iterrows():
        d = normalize_date(r.get('date', ''))
        if not d:
            continue
        cur.execute('''
            INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code, period, d,
            float(r['open']) if pd.notna(r.get('open', 0)) else 0,
            float(r['high']) if pd.notna(r.get('high', 0)) else 0,
            float(r['low']) if pd.notna(r.get('low', 0)) else 0,
            float(r['close']) if pd.notna(r.get('close', 0)) else 0,
            float(r['volume']) if pd.notna(r.get('volume', 0)) else 0,
        ))
        rows += 1
    conn.commit()
    return rows


def _db_read_kline(code: str, period: str = 'daily',
                    start_date: str = '', end_date: str = '') -> pd.DataFrame:
    """从SQLite读取K线数据"""
    conn = _get_db()
    sql = "SELECT date, open, high, low, close, volume FROM kline WHERE code=? AND period=?"
    params = [code, period]
    if start_date:
        sql += " AND date>=?"
        params.append(start_date)
    if end_date:
        sql += " AND date<=?"
        params.append(end_date)
    sql += " ORDER BY date ASC"
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    return df  # 周末直接返回本地缓存


def _db_get_last_date(code: str, period: str = 'daily') -> str:
    """获取本地数据的最后日期"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM kline WHERE code=? AND period=?", (code, period))
    row = cur.fetchone()
    return row[0] if row and row[0] else ''


def _db_update_meta(code: str, period: str, name: str = '', btype: str = '',
                    first_date: str = '', last_date: str = ''):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO meta (code, period, name, type, rows, first_date, last_date, updated_at)
        VALUES (?, ?, ?, ?, (SELECT COUNT(*) FROM kline WHERE code=? AND period=?), ?, ?, datetime('now','localtime'))
    ''', (code, period, name, btype, code, period, first_date, last_date))
    conn.commit()


# ===== 板块数据（SQLite + Tushare 增量） =====

def load_board_kline(board_type: str, name: str, code: str, period: str = 'daily') -> pd.DataFrame:
    """加载板块K线，SQLite优先，Tushare dc_daily 增量补充"""
    # 先读本地
    df = _db_read_kline(code, period)
    last_local = df['date'].max() if not df.empty else ''
    today = _today_str()

    # 如果本地已含最新数据
    if last_local >= today:
        if period in ('weekly', 'monthly', 'quarterly', 'yearly'):
            return df  # 周末直接返回本地缓存
        return df  # 周末直接返回本地缓存

    # 非交易日跳过增量更新，但允许首次加载
    wkday = datetime.now().weekday()
    if wkday >= 5 and not df.empty:  # 5=周六, 6=周日，且本地已有数据
        return df  # 周末直接返回本地缓存

    # 尝试增量更新（Tushare dc_daily）
    print(f"[数据] 更新: {board_type} {name} ({code}) period={period}", flush=True)
    try:
        from data.board_api import get_board_kline
        start = last_local if last_local else '20000101'
        raw = get_board_kline(board_type, code, start_date=start)

        if raw is not None and not raw.empty:
            ndf = _normalize_df(raw)
            if not df.empty:
                merged = pd.concat([df, ndf], ignore_index=True)
                merged = merged.drop_duplicates(subset=['date'], keep='last')
                merged = merged.sort_values('date').reset_index(drop=True)
            else:
                merged = ndf
            _db_write_kline(code, 'daily', merged)
            _db_update_meta(code, 'daily', name, board_type,
                            merged['date'].min(), merged['date'].max())

            if period in ('weekly', 'monthly', 'quarterly', 'yearly'):
                resampled = _resample(merged, period)
                _db_write_kline(code, period, resampled)
                return resampled
            return merged
    except Exception as e:
        print(f"[回退] {name} Tushare失败({e}), 使用本地缓存", flush=True)

    # Tushare失败或超时，回退到本地缓存重采样
    if period in ('weekly', 'monthly', 'quarterly', 'yearly') and not df.empty:
        return _resample(df, period)
    return df  # 周末直接返回本地缓存



# ===== 板块代码查询 =====

_BOARD_CODE_CACHE = None

def _get_board_code(name: str, board_type: str) -> str:
    """从board_classification.json或数据库查询板块的BK代码"""
    global _BOARD_CODE_CACHE
    if _BOARD_CODE_CACHE is None:
        _BOARD_CODE_CACHE = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("SELECT code, name FROM meta WHERE code LIKE 'BK%'")
            for r in cur.fetchall():
                _BOARD_CODE_CACHE[r[1]] = r[0]
            conn.close()
        except Exception:
            pass
    return _BOARD_CODE_CACHE.get(name, '')


# ===== 个股数据（按需全量覆盖） =====

def load_stock_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """加载个股K线 - 始终从SQLite读取（QMT负责更新）"""
    df = _db_read_kline(code, period)
    if df.empty and period in ('weekly', 'monthly'):
        daily = _db_read_kline(code, 'daily')
        if not daily.empty:
            return _resample(daily, period)
    return df  # 周末直接返回本地缓存


def load_stock_data(code: str, start_date: str = '', end_date: str = '') -> pd.DataFrame:
    """个股日线数据查询（从SQLite读取）"""
    return _db_read_kline(code, 'daily', start_date, end_date)


# ===== 指数数据 =====

def load_index_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """指数K线 - 始终从SQLite读取（QMT负责更新）"""
    if period in ('weekly', 'monthly'):
        per_df = _db_read_kline(code, period)
        if not per_df.empty and per_df['date'].max() >= _today_str():
            return per_df
        daily = load_index_kline(code, 'daily')
        if not daily.empty:
            resampled = _resample(daily, period)
            _db_write_kline(code, period, resampled)
            return resampled
        return daily

    df = _db_read_kline(code, 'daily')
    return df  # 周末直接返回本地缓存


def load_hk_index_kline(symbol: str, period: str = 'daily') -> pd.DataFrame:
    """港股指数K线 - 始终从SQLite读取（QMT负责更新）"""
    if period in ('weekly', 'monthly'):
        per_df = _db_read_kline(symbol, period)
        if not per_df.empty and per_df['date'].max() >= _today_str():
            return per_df
        daily = load_hk_index_kline(symbol, 'daily')
        if not daily.empty:
            resampled = _resample(daily, period)
            _db_write_kline(symbol, period, resampled)
            return resampled
        return daily

    df = _db_read_kline(symbol, 'daily')
    return df  # 周末直接返回本地缓存


def load_hk_kline(symbol: str, period: str = 'daily') -> pd.DataFrame:
    """港股个股K线 - 始终从SQLite读取（QMT负责更新）"""
    symbol = str(symbol).zfill(5)
    return _db_read_kline(symbol, 'daily')


# ===== 实时行情（QMT） =====

_PRICE_CACHE = {}  # code -> (price, change_pct, ts)

def _qmt_code_map(code: str, data_type: str) -> str:
    """本地代码 → QMT标准代码"""
    idx_map = {
        'sh000001': '000001.SH', 'sz399006': '399006.SZ',
        'sh000688': '000688.SH', 'sh000300': '000300.SH',
        'sh000016': '000016.SH', 'sh000852': '000852.SH',
        'sh000853': '000853.SH', 'sh000985': '000985.SH',
        'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
        '800000': '800000.SH',
    }
    if code in idx_map:
        return idx_map[code]
    if data_type == 'stock' and len(code) == 6:
        return f'{code}.SH' if code.startswith(('6','9')) else f'{code}.SZ'
    if data_type == 'stock' and len(code) in (4, 5):
        return str(code).zfill(5) + '.HK'
    return ''

_qmt_connected = False
_qmt_available = False   # Python版本不匹配时直接禁用QMT
_qmt_connect_lock = threading.Lock()

def _check_xtquant_available():
    """启动时检测xtquant是否可导入（Python版本必须3.6-3.9）"""
    global _qmt_available
    try:
        import importlib
        # 尝试导入，但不执行连接
        xtquant_spec = importlib.util.find_spec('xtquant')
        if xtquant_spec is None:
            logging.warning('[QMT] xtquant模块不存在于当前Python环境')
            return False
        # 检查Python版本
        import sys
        if sys.version_info >= (3, 10):
            logging.warning(f'[QMT] Python {sys.version_info.major}.{sys.version_info.minor} 不兼容xtquant（需要3.6-3.9）')
            return False
        # 实际import试试
        from xtquant import xtdata as _xt_test
        return True
    except Exception as e:
        logging.warning(f'[QMT] xtquant不可用: {e}')
        return False

_qmt_available = _check_xtquant_available()
if not _qmt_available:
    logging.info('[QMT] ⚠️ QMT行情不可用，将使用SQLite缓存数据')

def _ensure_qmt_connected():
    """确保QMT已连接（幂等：已连接则跳过）"""
    global _qmt_connected, _qmt_available
    if not _qmt_available:
        return  # Python版本不兼容，直接跳过
    if _qmt_connected:
        return
    with _qmt_connect_lock:
        if _qmt_connected:
            return
        try:
            from xtquant import xtdata as _xt
            _xt.connect(port=58600)
            _qmt_connected = True
            logging.info('[QMT] 连接已建立')
        except Exception as e:
            logging.warning(f'[QMT] 连接失败: {e}')
            _qmt_available = False  # 连接失败不再重试
            # 不raise，避免影响上层调用

def _qmt_disconnect():
    """主动断开QMT连接（应用退出时调用）"""
    global _qmt_connected
    if not _qmt_connected:
        return
    try:
        from xtquant import xtdata as _xt
        _xt.disconnect()
        _qmt_connected = False
        logging.info('[QMT] 连接已断开')
    except Exception as e:
        logging.warning(f'[QMT] 断开失败: {e}')

def _qmt_spot(code: str, data_type: str) -> dict:
    """通过QMT get_market_data 获取快照。
    xtquant 返回格式: {'close': DataFrame(index=日期, columns=[代码]), ...}
    """
    try:
        _ensure_qmt_connected()

        from xtquant import xtdata as _xt

        qmt_code = _qmt_code_map(code, data_type)
        if not qmt_code:
            return {}

        # xtquant get_market_data 返回 dict{字段名: DataFrame}
        md = _xt.get_market_data(
            fields=['close', 'open', 'high', 'low', 'volume', 'preClose'],
            stock_code=[qmt_code],
            count=2,
        )
        if not md or not isinstance(md, dict):
            return {}

        close_df = md.get('close')
        preClose_df = md.get('preClose')
        if close_df is None or close_df.empty:
            return {}

        # 取最近一个交易日
        close_vals = close_df[qmt_code]
        preClose_vals = preClose_df[qmt_code] if preClose_df is not None and qmt_code in preClose_df.columns else None

        price = float(close_vals.iloc[-1])
        pre_close = float(preClose_vals.iloc[-1]) if preClose_vals is not None else price

        open_df = md.get('open')
        high_df = md.get('high')
        low_df = md.get('low')
        vol_df = md.get('volume')

        open_p = float(open_df[qmt_code].iloc[-1]) if open_df is not None and qmt_code in open_df.columns else price
        high   = float(high_df[qmt_code].iloc[-1]) if high_df is not None and qmt_code in high_df.columns else price
        low    = float(low_df[qmt_code].iloc[-1]) if low_df is not None and qmt_code in low_df.columns else price
        volume = float(vol_df[qmt_code].iloc[-1]) if vol_df is not None and qmt_code in vol_df.columns else 0

        if pre_close == 0:
            pre_close = price
        change_pct = (price - pre_close) / pre_close * 100 if pre_close else 0

        out = {
            'price': round(price, 4),
            'change_pct': round(change_pct, 2),
            'pre_close': round(pre_close, 4),
            'open': round(open_p, 4),
            'high': round(high, 4),
            'low': round(low, 4),
            'volume': volume,
        }
        _PRICE_CACHE[code] = (out['price'], out['change_pct'], time.time())
        return out
    except Exception as e:
        # 连接异常时重置状态，下次调用会自动重连
        global _qmt_connected
        _qmt_connected = False

        cached = _PRICE_CACHE.get(code)
        if cached and time.time() - cached[2] < 600:
            return {'price': cached[0], 'change_pct': cached[1],
                    'pre_close': cached[0], 'open': cached[0],
                    'high': cached[0], 'low': cached[0], 'volume': 0}
        return {}


_spot_cache = {}
_spot_cache_time = 0

def _sqlite_spot(code: str) -> dict:
    """从 SQLite 取最近收盘价作为盘后/离线行情"""
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT date,open,high,low,close,volume FROM kline WHERE code=? AND period='daily' ORDER BY date DESC LIMIT 2",
            (code,)
        )
        rows = cur.fetchall()
        if not rows:
            return {}
        date_today, o, h, l, c, v = rows[0]
        pre_close = rows[1][4] if len(rows) > 1 else c
        change = (c - pre_close) / pre_close * 100 if pre_close else 0
        return {
            'price': float(c),
            'change_pct': round(change, 2),
            'pre_close': float(pre_close),
            'open': float(o) if o else float(c),
            'high': float(h) if h else float(c),
            'low': float(l) if l else float(c),
            'volume': float(v) if v else 0,
        }
    except Exception as e:
        return {}

def _qmt_spot_v2(code: str, data_type: str) -> dict:
    """QMT 优先, 失败或空数据回退到 SQLite"""
    result = _qmt_spot(code, data_type)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)

# ===== Tushare 行情数据源 =====

def _tushare_code_map(code: str, data_type: str) -> str:
    """本地代码 → tushare ts_code"""
    idx_map = {
        'sh000001': '000001.SH', 'sz399006': '399006.SZ',
        'sh000688': '000688.SH', 'sh000300': '000300.SH',
        'sh000016': '000016.SH', 'sh000852': '000852.SH',
        'sh000853': '000853.SH', 'sh000985': '000985.SH',
        '800000': '800000.SH',
        'HSI': 'HSI.HONGKONG', 'HSTECH': 'HSTECH.HONGKONG',
    }
    if code in idx_map:
        return idx_map[code]
    if data_type == 'stock' and len(code) == 6:
        return f'{code}.SH' if code.startswith(('6','9')) else f'{code}.SZ'
    return ''

def _tushare_spot(code: str, data_type: str) -> dict:
    """通过 tushare 获取最近交易日行情数据"""
    try:
        ts_code = _tushare_code_map(code, data_type)
        if not ts_code:
            return {}

        if data_type == 'index':
            df = _tushare_pro.index_daily(ts_code=ts_code, start_date='', end_date='')
        else:
            df = _tushare_pro.daily(ts_code=ts_code, start_date='', end_date='')

        if df is None or df.empty:
            return {}

        row = df.iloc[0]  # 最近一个交易日
        close = float(row['close'])
        pre_close = float(row.get('pre_close', close))
        pct_chg = float(row.get('pct_chg', 0))

        return {
            'price': round(close, 4),
            'change_pct': round(pct_chg, 2),
            'pre_close': round(pre_close, 4),
            'open': float(row.get('open', close)),
            'high': float(row.get('high', close)),
            'low': float(row.get('low', close)),
            'volume': float(row.get('vol', 0)),
        }
    except Exception as e:
        logging.warning(f'[tushare] 获取 {code} 失败: {e}')
        return {}

def _qmt_spot_v3(code: str, data_type: str) -> dict:
    """QMT优先 → tushare → SQLite 三级回退"""
    result = _qmt_spot(code, data_type)
    if result and result.get('price', 0) > 0:
        return result
    # QMT 不可用，尝试 tushare
    result = _tushare_spot(code, data_type)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)

def get_spot_board(board_type: str, code: str) -> dict:
    """板块实时行情 - 通过东财HTTP API获取"""
    try:
        from data.board_api import get_industry_spot, get_concept_spot
        spot = get_industry_spot() if board_type == 'industry' else get_concept_spot()
        if not spot or code not in spot:
            return {}
        data = spot[code]
        return {
            'price': data.get('最新价', 0),
            'change_pct': data.get('涨跌幅', 0),
        }
    except Exception as e:
        logging.warning(f'[板块行情] {code} 失败: {e}')
        return {}

def get_spot_index(code: str) -> dict:
    """指数实时行情 - QMT优先 → tushare次 → SQLite回退"""
    return _qmt_spot_v3(code, 'index')

def get_spot_stock(code: str) -> dict:
    """个股实时行情 - QMT优先 → tushare次 → SQLite回退"""
    return _qmt_spot_v3(code, 'stock')


# ===== 个股列表（QMT） =====

def get_all_stocks() -> list:
    """通过QMT获取全市场个股列表"""
    try:
        from xtquant import xtdata as _xt
        _xt.connect(port=58610)
        stocks = _xt.get_stock_list_in_sector('沪深A股')
        result = []
        for full_code in stocks:
            code = full_code.split('.')[0]
            try:
                detail = _xt.get_instrument_detail(full_code)
                name = detail.get('InstrumentName', '') if detail else ''
            except Exception:
                name = ''  # 获取股票名称失败不影响整体流程
            if name:
                result.append({'code': code, 'name': name})
        return result
    except Exception as e:
        print(f"[QMT] 获取股票列表失败: {e}")
        return []

def search_stock(keyword: str) -> list:
    """个股搜索 - 已废弃，使用 /api/search 替代"""
    return []
