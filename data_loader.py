"""Unified data loading helpers."""
import sys, io
if 'pytest' not in sys.modules:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass  # 鍦ㄦ祴璇曠幆澧冩垨鐗规畩鐜涓烦杩?
import logging
import json
import pandas as pd
import sqlite3
import re
import os
import requests
from pathlib import Path
from datetime import datetime, timedelta
import time
import atexit
import threading
from functools import partial

# ===== Tushare 直连客户端（惰性单例） =====
_tushare_pro = None
_tushare_lock = threading.Lock()
TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '')


class _DirectTusharePro:
    """兼容 Tushare Pro API 的局部直连客户端。

    Tushare 1.4.29 的 DataApi.query 使用 requests.post 模块函数，无法注入
    Session。这里保留其 query/动态 endpoint 约定，但把请求限制在本实例的
    Session 内，避免改变进程级 requests 或 urllib 行为。
    """

    def __init__(self, token: str, timeout: int = 30):
        try:
            from tushare.pro.client import DataApi
        except ImportError as exc:
            raise RuntimeError('tushare is not installed') from exc

        self._token = token
        self._timeout = timeout
        # Verified against the installed tushare 1.4.29 source. Its URL is a
        # private class attribute because DataApi itself exposes no public
        # session/endpoint injection point.
        self._http_url = getattr(DataApi, '_DataApi__http_url', None)
        if not self._http_url:
            raise RuntimeError(
                'unsupported tushare DataApi: HTTP endpoint is unavailable'
            )
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = {}
        self._session = self.session

    def query(self, api_name, fields='', **kwargs):
        kwargs.setdefault('ts_type_name', self._http_url)
        req_params = {
            'api_name': api_name,
            'token': self._token,
            'params': kwargs,
            'fields': fields,
        }
        response = self._session.post(
            f'{self._http_url}/{api_name}',
            json=req_params,
            timeout=self._timeout,
        )
        if not response:
            return pd.DataFrame()

        try:
            result = json.loads(response.text)
        except (AttributeError, TypeError, json.JSONDecodeError):
            result = response.json()
        if result.get('code') != 0:
            raise Exception(result.get('msg', 'Tushare API request failed'))
        data = result.get('data') or {}
        return pd.DataFrame(
            data.get('items') or [],
            columns=data.get('fields') or [],
        )

    def __getattr__(self, name):
        return partial(self.query, name)


def get_tushare_pro():
    """返回线程安全、惰性创建的唯一 Tushare 直连客户端。

    _tushare_pro 是历史兼容入口：已有调用点预先放入客户端时直接复用，
    否则在锁内只创建一次。没有 token 时返回 None，不触发网络请求。
    """
    global _tushare_pro, TUSHARE_TOKEN
    if _tushare_pro is not None:
        return _tushare_pro

    token = (os.environ.get('TUSHARE_TOKEN') or '').strip()
    TUSHARE_TOKEN = token
    if not token:
        logging.warning('[Tushare] TUSHARE_TOKEN 环境变量未设置，Tushare 数据不可用')
        return None

    with _tushare_lock:
        if _tushare_pro is None:
            try:
                _tushare_pro = _DirectTusharePro(token)
            except PermissionError:
                logging.warning('[Tushare] 无法初始化客户端（权限受限），将使用其他数据源')
                return None
            except Exception as exc:
                logging.warning('[Tushare] 初始化失败: %s', exc)
                return None
            logging.info('[Tushare] direct client initialized')
        return _tushare_pro


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
    from data.global_index_kline import (
        is_standard_a_share_index_code,
        load_a_share_index_kline,
    )
    if is_standard_a_share_index_code(code):
        return load_a_share_index_kline(code, period)
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
            'channel': 'sqlite',
        }
    except Exception as e:
        return {}


def get_local_spot(code: str) -> dict:
    """Return the latest persisted quote without starting any remote request."""
    return _sqlite_spot(str(code or '').strip())

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


def _eastmoney_a_share_index_secid(code: str) -> str:
    """Map a prefixed A-share index code to Eastmoney's secid."""
    from data.global_index_kline import canonical_a_share_index_code

    raw = canonical_a_share_index_code(code)
    if raw.startswith(('sh', 'sz', 'bj')) and raw[2:].isdigit():
        market, bare = raw[:2], raw[2:]
    elif raw.isdigit():
        bare = raw
        market = 'sh' if bare.startswith(('0', '5', '6', '9')) else 'sz'
    else:
        return ''
    market_id = {'sh': '1', 'sz': '0', 'bj': '0'}.get(market)
    return f'{market_id}.{bare}' if market_id else ''


def _fetch_a_share_index_eastmoney(code: str) -> dict:
    """Fetch an official A-share index snapshot from push2delay."""
    secid = _eastmoney_a_share_index_secid(code)
    if not secid:
        return {}
    try:
        import requests
        session = requests.Session()
        session.trust_env = False
        response = session.get(
            'https://push2delay.eastmoney.com/api/qt/stock/get',
            params={'secid': secid, 'fields': 'f43,f57,f58,f60,f169,f170'},
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://quote.eastmoney.com/',
            },
            timeout=6,
        )
        row = (response.json() or {}).get('data')
        if not isinstance(row, dict):
            return {}

        def scaled(field):
            try:
                value = float(row.get(field))
                return value / 100 if value == value else 0.0
            except (TypeError, ValueError):
                return 0.0

        price = scaled('f43')
        previous = scaled('f60')
        if price <= 0:
            return {}
        change = scaled('f169')
        change_pct = scaled('f170')
        if not change and previous > 0:
            change = price - previous
        if not change_pct and previous > 0:
            change_pct = change / previous * 100
        return {
            'name': row.get('f58') or row.get('f57') or str(code),
            'price': price,
            'close': price,
            'pre_close': previous,
            'change': change,
            'change_pct': change_pct,
            'channel': 'eastmoney_push2delay',
        }
    except Exception:
        return {}


EASTMONEY_A_SHARE_INDEX_BATCH_URL = (
    'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
)


def _is_inactive_a_share_index(code: str) -> bool:
    from data.global_index_kline import is_inactive_a_share_index
    return is_inactive_a_share_index(code)


def _parse_a_share_index_snapshot(row: dict, code: str) -> dict:
    """Normalize one ulist.np row to the single-index quote contract."""
    if not isinstance(row, dict):
        return {}

    def number(field):
        try:
            value = float(row.get(field))
            return value if value == value else 0.0
        except (TypeError, ValueError):
            return 0.0

    price = number('f2')
    if price <= 0:
        return {}
    change = number('f4')
    change_pct = number('f3')
    # f152 is Eastmoney's precision marker in this endpoint, not previous
    # close. Derive yesterday's close from the actual price/change fields.
    previous = 0.0
    if change:
        previous = price - change
    elif change_pct:
        previous = price / (1 + change_pct / 100)
    if not change and previous > 0:
        change = price - previous
    if not change_pct and previous > 0:
        change_pct = change / previous * 100
    return {
        'code': code,
        'name': row.get('f14') or code,
        'price': price,
        'close': price,
        'pre_close': previous,
        'change': change,
        'change_pct': change_pct,
        'channel': 'eastmoney_push2delay_batch',
        'source': 'eastmoney_push2delay_batch',
    }


def fetch_a_share_index_spots(codes, chunk_size: int = 50) -> dict:
    """Fetch domestic index spots in chunks through Eastmoney's batch feed.

    The returned keys are the caller's original panel codes. Compatibility
    aliases are translated only for secid construction, so external links and
    classification references remain stable.
    """
    requested = []
    for raw in codes or []:
        code = str(raw or '').strip()
        if (code and code not in requested
                and not _is_inactive_a_share_index(code)):
            requested.append(code)
    if not requested:
        return {}

    try:
        import requests
        session = requests.Session()
        session.trust_env = False
        out = {}
        size = max(1, int(chunk_size or 50))
        for start in range(0, len(requested), size):
            chunk = requested[start:start + size]
            secid_to_codes = {}
            for code in chunk:
                secid = _eastmoney_a_share_index_secid(code)
                if secid:
                    secid_to_codes.setdefault(secid, []).append(code)
            if not secid_to_codes:
                continue
            response = session.get(
                EASTMONEY_A_SHARE_INDEX_BATCH_URL,
                params={
                    'fltt': '2',
                    'invt': '2',
                    'fields': 'f12,f13,f14,f2,f3,f4,f152',
                    'secids': ','.join(secid_to_codes),
                },
                headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://quote.eastmoney.com/',
                },
                timeout=6,
            )
            payload = response.json() or {}
            diff = (payload.get('data') or {}).get('diff') or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            for row in diff:
                if not isinstance(row, dict):
                    continue
                bare = str(row.get('f12') or '').zfill(6)
                market_raw = row.get('f13')
                market = '' if market_raw is None else str(market_raw)
                secid = f'{market}.{bare}'
                for code in secid_to_codes.get(secid, []):
                    parsed = _parse_a_share_index_snapshot(row, code)
                    if parsed:
                        out[code] = parsed
        return out
    except Exception as exc:
        logging.warning('[Eastmoney] batch A-share index spot failed: %s', exc)
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
    from data.global_index_kline import is_inactive_a_share_index

    if is_inactive_a_share_index(code):
        return {
            'code': code,
            'unavailable': True,
            'channel': 'unavailable',
            'reason': 'deprecated_no_remote',
        }
    if str(code or '').strip() == '800000':
        return get_global_index_spot('800000')
    result = _qmt_live_spot(code)
    if result and result.get('price', 0) > 0:
        return result
    result = _fetch_a_share_index_eastmoney(code)
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

_GLOBAL_INDEX_SPOT_CACHE = {}
_GLOBAL_INDEX_SPOT_CACHE_TS = {}
_GLOBAL_INDEX_SPOT_CACHE_TTL = 30.0
_GLOBAL_INDEX_SPOT_LOCK = threading.RLock()
_GLOBAL_INDEX_SPOT_KEY_LOCKS = {}

def _fetch_global_index_spot_uncached(code: str) -> dict:
    """Internal helper."""
    import requests
    from datetime import datetime as _dt
    
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
            proxies={'http': '', 'https': ''},
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
        # Sina's Taiwan endpoint has occasionally returned a months-old
        # snapshot.  An absent value is preferable to presenting stale data
        # as the current index level.
        if sina_code.startswith('b_'):
            date_candidates = parts[4:8]
            parsed_dates = []
            for value in date_candidates:
                try:
                    parsed_dates.append(_dt.strptime(value.strip(), '%Y-%m-%d'))
                except Exception:
                    try:
                        parsed_dates.append(_dt.strptime(value.strip(), '%m/%d/%Y'))
                    except Exception:
                        pass
            if parsed_dates and (_dt.now() - max(parsed_dates)).days > 14:
                return {}
        return {
            'name': parts[0],
            'price': _to_float(parts[1]),
            'change': _to_float(parts[2]),
            'change_pct': _to_float(parts[3]),
            'channel': 'sina',
        }

    def _fetch_yahoo(yahoo_code: str) -> dict:
        """Fetch a current global-index snapshot and derive pct from closes."""
        encoded = requests.utils.quote(yahoo_code, safe='')
        payload = None
        last_error = None
        for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
            try:
                resp = requests.get(
                    f'https://{host}/v8/finance/chart/{encoded}',
                    params={'interval': '1d', 'range': '5d'},
                    timeout=6,
                    headers={'User-Agent': 'Mozilla/5.0'},
                    proxies={'http': '', 'https': ''},
                )
                payload = resp.json()
                if (payload.get('chart') or {}).get('result'):
                    break
            except Exception as exc:
                last_error = exc
        if payload is None:
            raise last_error or RuntimeError('Yahoo returned no payload')
        result = ((payload.get('chart') or {}).get('result') or [None])[0]
        if not isinstance(result, dict):
            return {}
        meta = result.get('meta') or {}
        quote = ((result.get('indicators') or {}).get('quote') or [{}])[0]
        closes = []
        for value in quote.get('close') or []:
            number = _to_float(value)
            if number > 0:
                closes.append(number)
        price = _to_float(meta.get('regularMarketPrice'))
        if price <= 0 and closes:
            price = closes[-1]
        if price <= 0 or len(closes) < 2:
            return {}
        previous = closes[-2]
        change = price - previous
        change_pct = change / previous * 100 if previous else 0
        return {
            'name': meta.get('shortName') or meta.get('symbol') or yahoo_code,
            'price': price,
            'change': change,
            'change_pct': change_pct,
            'time': meta.get('regularMarketTime'),
            'channel': 'yahoo',
        }

    def _fetch_twse() -> dict:
        """Taiwan Weighted Index from the official TWSE realtime API."""
        session = requests.Session()
        session.trust_env = False
        resp = session.get(
            'https://mis.twse.com.tw/stock/api/getStockInfo.jsp',
            params={'ex_ch': 'tse_t00.tw', 'json': '1', 'delay': '0'},
            timeout=8,
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://mis.twse.com.tw/',
            },
        )
        payload = resp.json()
        row = (payload.get('msgArray') or [None])[0]
        if not isinstance(row, dict):
            return {}
        price = _to_float(row.get('z'))
        previous = _to_float(row.get('y'))
        if price <= 0 or previous <= 0:
            return {}
        change = price - previous
        return {
            'name': row.get('n') or '台湾加权指数',
            'price': price,
            'change': change,
            'change_pct': change / previous * 100,
            'time': row.get('tlong') or row.get('t'),
            'channel': 'twse',
        }

    def _fetch_eastmoney(secid: str) -> dict:
        """Unified overseas-index snapshot from Eastmoney push2delay."""
        session = requests.Session()
        session.trust_env = False
        row = None
        last_error = None
        for attempt in range(2):
            try:
                resp = session.get(
                    'https://push2delay.eastmoney.com/api/qt/stock/get',
                    params={
                        'secid': secid,
                        'fields': 'f43,f57,f58,f60,f169,f170',
                    },
                    timeout=6,
                    headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Referer': 'https://quote.eastmoney.com/',
                    },
                )
                row = (resp.json() or {}).get('data')
                if isinstance(row, dict):
                    break
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(0.12)
        if not isinstance(row, dict):
            if last_error:
                raise last_error
            return {}
        price = _to_float(row.get('f43')) / 100
        previous = _to_float(row.get('f60')) / 100
        if price <= 0:
            return {}
        change = _to_float(row.get('f169')) / 100
        change_pct = _to_float(row.get('f170')) / 100
        if not change and previous > 0:
            change = price - previous
        if not change_pct and previous > 0:
            change_pct = change / previous * 100
        return {
            'name': row.get('f58') or row.get('f57') or secid,
            'price': price,
            'change': change,
            'change_pct': change_pct,
            'channel': 'eastmoney_push2delay',
        }

    sina_code_map = {
        'SPX': 'int_sp500',
        'IXIC': 'int_nasdaq',
        'DJI': 'int_dji',
        '^N225': 'b_NKY',
        '^KS11': 'b_KOSPI',
        '^TWII': 'b_TWSE',
    }

    yahoo_code_map = {
        'HSI': '^HSI',
        'SPX': '^GSPC',
        'IXIC': '^IXIC',
        'DJI': '^DJI',
        '^N225': '^N225',
        '^KS11': '^KS11',
        '^TWII': '^TWII',
    }

    eastmoney_secid_map = {
        '800000': '47.800000',
        'HSI': '100.HSI',
        'HSTECH': '124.HSTECH',
        '^N225': '100.N225',
        '^KS11': '100.KS11',
        '^TWII': '100.TWII',
        'SPX': '100.SPX',
        'IXIC': '100.NDX',
        'DJI': '100.DJIA',
    }

    # 浠ｇ爜鏄犲皠锛氬墠绔唬鐮?-> 鑵捐浠ｇ爜
    tencent_code_map = {
        'HSI': 'hkHSI',
        'HSTECH': 'hkHSTECH',
        'SPX': 'usINX',
        'IXIC': 'usIXIC',
        'DJI': 'usDJI',
    }

    tencent_code = tencent_code_map.get(code)

    def _fetch_tencent() -> dict:
        if not tencent_code:
            return {}
        url = f'https://qt.gtimg.cn/q={tencent_code}'
        response = requests.get(url, timeout=4, proxies={'http': '', 'https': ''})
        response.encoding = 'gbk'

        text = response.text
        if text and 'v_' in text and 'pv_none_match' not in text:
            start = text.find('"') + 1
            end = text.rfind('"')
            if start > 0 and end > start:
                parts = text[start:end].split('~')
                if len(parts) >= 33:
                    # Tencent hk/us snapshot: 1=name, 3=last price,
                    # 31=change, 32=change pct.
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
        return {}

    def _attempt(label, fetcher):
        try:
            return fetcher() or {}
        except Exception as e:
            logging.warning(f'[全球指数] {code} {label} 获取失败: {e}')
            return {}

    eastmoney_secid = eastmoney_secid_map.get(code)
    if eastmoney_secid:
        eastmoney_data = _attempt(
            '东财', lambda: _fetch_eastmoney(eastmoney_secid)
        )
        if eastmoney_data:
            return eastmoney_data

    # Exchange/Tencent/Yahoo/Sina are independent fallback channels when the
    # unified Eastmoney snapshot is temporarily unavailable.
    yahoo_attempted = False
    if code == '^TWII':
        twse_data = _attempt('TWSE', _fetch_twse)
        if twse_data:
            return twse_data

    if code in ('^N225', '^KS11'):
        yahoo_attempted = True
        yahoo_data = _attempt(
            'Yahoo', lambda: _fetch_yahoo(yahoo_code_map[code])
        )
        if yahoo_data:
            return yahoo_data

    if tencent_code:
        tencent_data = _attempt('腾讯', _fetch_tencent)
        if tencent_data:
            return tencent_data

    yahoo_code = yahoo_code_map.get(code)
    if yahoo_code and not yahoo_attempted:
        yahoo_data = _attempt('Yahoo', lambda: _fetch_yahoo(yahoo_code))
        if yahoo_data:
            return yahoo_data

    sina_code = sina_code_map.get(code)
    if sina_code:
        return _attempt('新浪', lambda: _fetch_sina(sina_code))
    return {}


def get_global_index_spot(code: str) -> dict:
    """Global-index spot lookup with per-symbol request serialization."""
    key = str(code or '').strip()
    if not key:
        return {}
    now = time.time()
    with _GLOBAL_INDEX_SPOT_LOCK:
        cached = _GLOBAL_INDEX_SPOT_CACHE.get(key)
        cached_at = float(_GLOBAL_INDEX_SPOT_CACHE_TS.get(key, 0) or 0)
        if cached and now - cached_at < _GLOBAL_INDEX_SPOT_CACHE_TTL:
            return dict(cached)

        key_lock = _GLOBAL_INDEX_SPOT_KEY_LOCKS.setdefault(
            key, threading.RLock()
        )

    # Requests for the same symbol share one in-flight fetch. Different
    # symbols remain concurrent so a slow overseas endpoint cannot serialize
    # the whole navigation bar.
    with key_lock:
        now = time.time()
        with _GLOBAL_INDEX_SPOT_LOCK:
            cached = _GLOBAL_INDEX_SPOT_CACHE.get(key)
            cached_at = float(_GLOBAL_INDEX_SPOT_CACHE_TS.get(key, 0) or 0)
            if cached and now - cached_at < _GLOBAL_INDEX_SPOT_CACHE_TTL:
                return dict(cached)

        result = _fetch_global_index_spot_uncached(key)
        if result:
            with _GLOBAL_INDEX_SPOT_LOCK:
                _GLOBAL_INDEX_SPOT_CACHE[key] = dict(result)
                _GLOBAL_INDEX_SPOT_CACHE_TS[key] = time.time()
            return dict(result)

        with _GLOBAL_INDEX_SPOT_LOCK:
            cached = _GLOBAL_INDEX_SPOT_CACHE.get(key)
        if cached:
            stale = dict(cached)
            stale['stale'] = True
            return stale
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
