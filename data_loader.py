"""Unified data loading helpers."""
import sys, io
if 'pytest' not in sys.modules:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass  # 鍦ㄦ祴璇曠幆澧冩垨鐗规畩鐜涓烦杩?
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

# ===== Tushare 閰嶇疆锛堣幏鍙栦笢璐㈡澘鍧楁暟鎹級 =====
_tushare_pro = None
try:
    import tushare as ts
    # 瀹夊叏锛氫紭鍏堜粠鐜鍙橀噺璇诲彇token锛屼笉鍐嶇‖缂栫爜
    TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '')
    if not TUSHARE_TOKEN:
        logging.warning('[Tushare] TUSHARE_TOKEN鐜鍙橀噺鏈缃紝Tushare鏉垮潡鏁版嵁灏嗕笉鍙敤銆傝璁剧疆鐜鍙橀噺: set TUSHARE_TOKEN=<your_token>')
    else:
        try:
            ts.set_token(TUSHARE_TOKEN)
            _tushare_pro = ts.pro_api()
            logging.info('[Tushare] initialized')
        except PermissionError:
            logging.warning('[Tushare] 鏃犳硶鍐欏叆token鏂囦欢锛堟矙绠遍檺鍒讹級锛屽皢浣跨敤MCP鏇夸唬')
            _tushare_pro = None
        except Exception as e:
            logging.warning(f'[Tushare] 鍒濆鍖栧け璐? {e}')
            _tushare_pro = None
except ImportError:
    logging.warning('[Tushare] tushare module not installed')



# 缁濆璺緞锛氶伩鍏?Flask 宸ヤ綔鐩綍瀵艰嚧璺緞閿欎綅
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

_conn_local = threading.local()
def _get_db() -> sqlite3.Connection:
    """Internal helper."""
    if not hasattr(_conn_local, 'conn'):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _conn_local.conn = conn
    return _conn_local.conn


def _close_db_connections():
    """Internal helper."""
    if hasattr(_conn_local, 'conn'):
        try:
            _conn_local.conn.close()
        except Exception:
            pass
        del _conn_local.conn

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
    """Compatibility wrapper for the shared OHLCV resampler."""
    from data.kline_resample import resample_ohlcv
    return resample_ohlcv(df, freq)


# ===== SQLite 鏍稿績璇诲啓 =====

def _db_write_kline(code: str, period: str, df: pd.DataFrame):
    """Internal helper."""
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
    """Internal helper."""
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
    return df  # 鍛ㄦ湯鐩存帴杩斿洖鏈湴缂撳瓨


def _db_get_last_date(code: str, period: str = 'daily') -> str:
    """Internal helper."""
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


# ===== 鏉垮潡鏁版嵁锛圫QLite + Tushare 澧為噺锛?=====

def load_board_kline(board_type: str, name: str, code: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for board K-line loading."""
    from data.board_kline import load_board_kline as _load
    return _load(board_type, name, code, period)



# ===== 鏉垮潡浠ｇ爜鏌ヨ =====

_BOARD_CODE_CACHE = None

def _get_board_code(name: str, board_type: str) -> str:
    """Internal helper."""
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


# ===== 涓偂鏁版嵁锛堟寜闇€鍏ㄩ噺瑕嗙洊锛?=====

def load_stock_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for cached stock K-line loading."""
    from data.market_kline import load_stock_kline as _load
    return _load(code, period)


def load_stock_data(code: str, start_date: str = '', end_date: str = '') -> pd.DataFrame:
    """Compatibility wrapper for cached stock daily loading."""
    from data.market_kline import load_stock_data as _load
    return _load(code, start_date, end_date)


def load_index_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for cached A-share index K-line loading."""
    from data.market_kline import load_index_kline as _load
    return _load(code, period)


def load_hk_index_kline(symbol: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for cached Hong Kong index K-line loading."""
    from data.market_kline import load_hk_index_kline as _load
    return _load(symbol, period)


def load_hk_kline(symbol: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for cached Hong Kong stock K-line loading."""
    from data.market_kline import load_hk_kline as _load
    return _load(symbol, period)


def load_global_index_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """Compatibility wrapper for the extracted global index loader."""
    from data.global_index_kline import load_global_index_kline as _load
    return _load(code, period)
# ===== 瀹炴椂琛屾儏锛坬mt_client 瀛愯繘绋嬫ā寮?+ SQLite 鍥為€€锛?=====

_spot_cache = {}
_spot_cache_time = 0

def _sqlite_spot(code: str) -> dict:
    """Internal helper."""
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

def _qmt_live_spot(code: str) -> dict:
    """Internal helper."""
    try:
        from data.qmt_client import get_qmt_client
        from core.lifecycle import is_qmt_available
        if not is_qmt_available():
            return {}
        client = get_qmt_client()
        raw = client.get_constituents_live([code])
        if not raw or code not in raw:
            raw = client.get_constituents_batch([code])
        if not raw or code not in raw:
            return {}
        info = raw[code]
        price = info.get('close', 0)
        if price <= 0:
            return {}
        return {
            'price': round(price, 4),
            'change_pct': round(info.get('change_pct', 0), 2),
            'pre_close': round(price / (1 + info.get('change_pct', 0) / 100), 4) if info.get('change_pct') else price,
            'open': price,
            'high': price,
            'low': price,
            'volume': info.get('volume', 0),
        }
    except Exception:
        return {}

def get_spot_board(board_type: str, code: str) -> dict:
    """Internal helper."""
    try:
        from data.board_api import get_industry_spot, get_concept_spot
        spot = get_industry_spot() if board_type == 'industry' else get_concept_spot()
        if not spot or code not in spot:
            return {}
        data = spot[code]
        return {
            'price': data.get('price', data.get('最新价', 0)),
            'change_pct': data.get('change_pct', data.get('涨跌幅', 0)),
        }
    except Exception as e:
        logging.warning(f'[鏉垮潡琛屾儏] {code} 澶辫触: {e}')
        return {}

def get_spot_index(code: str) -> dict:
    """Internal helper."""
    result = _qmt_live_spot(code)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)

def get_spot_stock(code: str) -> dict:
    """Internal helper."""
    result = _qmt_live_spot(code)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)


# ===== 鍏ㄧ悆鎸囨暟琛屾儏锛堣吘璁储缁廇PI锛?=====

def get_global_index_spot(code: str) -> dict:
    """Internal helper."""
    import requests
    
    def _to_float(value, default=0.0):
        try:
            if value is None or value == '':
                return default
            return float(value)
        except Exception:
            return default

    def _fetch_sina(sina_code: str) -> dict:
        sina_url = f'https://hq.sinajs.cn/list={sina_code}'
        resp = requests.get(
            sina_url,
            timeout=4,
            headers={'Referer': 'https://finance.sina.com.cn'},
            proxies={'http': None, 'https': None},
        )
        resp.encoding = 'gbk'
        text = resp.text or ''
        start = text.find('"') + 1
        end = text.rfind('"')
        if start <= 0 or end <= start:
            return {}
        parts = text[start:end].split(',')
        if len(parts) < 4:
            return {}
        return {
            'name': parts[0],
            'price': _to_float(parts[1]),
            'change': _to_float(parts[2]),
            'change_pct': _to_float(parts[3]),
            'channel': 'sina',
        }

    sina_code_map = {
        'SPX': 'int_sp500',
        'IXIC': 'int_nasdaq',
        'DJI': 'int_dji',
        '^N225': 'b_NKY',
        '^KS11': 'b_KOSPI',
        '^TWII': 'b_TWSE',
    }

    # 浠ｇ爜鏄犲皠锛氬墠绔唬鐮?-> 鑵捐浠ｇ爜
    tencent_code_map = {
        'HSI': 'hkHSI',
        'HSTECH': 'hkHSTECH',
        'IXIC': 'usIXIC',
        'DJI': 'usDJI',
    }

    tencent_code = tencent_code_map.get(code)

    try:
        if tencent_code:
            url = f'https://qt.gtimg.cn/q={tencent_code}'
            response = requests.get(url, timeout=4, proxies={'http': None, 'https': None})
            response.encoding = 'gbk'

            text = response.text
            if text and 'v_' in text and 'pv_none_match' not in text:
                start = text.find('"') + 1
                end = text.rfind('"')
                if start > 0 and end > start:
                    parts = text[start:end].split('~')
                    if len(parts) >= 33:
                        # Tencent hk/us snapshot: 1=name, 3=last price, 31=change, 32=change pct.
                        price = _to_float(parts[3])
                        change_pct = _to_float(parts[32])
                        if price > 0:
                            return {
                                'price': price,
                                'change_pct': change_pct,
                                'change': _to_float(parts[31]),
                                'name': parts[1],
                                'channel': 'tencent',
                            }

        sina_code = sina_code_map.get(code)
        if sina_code:
            return _fetch_sina(sina_code)
        return {}
    except Exception as e:
        logging.warning(f'[鍏ㄧ悆鎸囨暟] {code} 鑾峰彇澶辫触: {e}')
        try:
            sina_code = sina_code_map.get(code)
            if sina_code:
                return _fetch_sina(sina_code)
        except Exception as fallback_e:
            logging.warning(f'[鍏ㄧ悆鎸囨暟] {code} 鏂版氮鍏滃簳澶辫触: {fallback_e}')
        return {}


# ===== 涓偂鍒楄〃锛圦MT锛?=====

def get_all_stocks() -> list:
    """Internal helper."""
    try:
        from data.qmt_client import get_qmt_client
        from core.lifecycle import is_qmt_available
        if not is_qmt_available():
            return []
        client = get_qmt_client()
        stocks = client.get_stock_list()
        return [{'code': s['code'], 'name': s['name']} for s in stocks if s.get('name')]
    except Exception as e:
        print(f"[QMT] 鑾峰彇鑲＄エ鍒楄〃澶辫触: {e}")
        return []

def search_stock(keyword: str) -> list:
    """Internal helper."""
    return []

