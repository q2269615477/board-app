"""
services/kline_service.py — K线数据业务逻辑
统一K线加载入口：分钟线(QMT子进程)/日线(QMT+缓存)/重采样(月/季/年线)
"""
import math
import time
import atexit
import threading
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, List, Dict, Tuple

import pandas as pd

from data.sqlite_repo import get_sqlite_repo
from data.qmt_client import get_qmt_client
from data.board_kline import load_board_kline
from data.global_index_kline import load_global_index_kline
from core.cache import get_cache
from core.lifecycle import is_qmt_available

logger = logging.getLogger('kline_service')

MINUTE_PERIODS = {'1m', '5m', '15m', '30m', '60m', '120m', '240m'}
RESAMPLE_PERIODS = {'weekly', 'monthly', 'quarterly', 'yearly'}
# 需 resample 的目标周期及对应分钟数（含 5m，避免 QMT 直出与 1m 混淆）
RESAMPLE_FROM_1M = {
    '5m': '5min',
    '15m': '15min',
    '30m': '30min',
    '60m': '60min',
    '120m': '120min',
    '240m': '240min',
}
# 前端/口语别名 → 内部 period
PERIOD_ALIASES = {
    '1H': '60m', '1h': '60m', '60min': '60m',
    '2H': '120m', '2h': '120m',
    '4H': '240m', '4h': '240m',
    'day': 'daily', '1d': 'daily',
    'week': 'weekly', '1w': 'weekly',
    'month': 'monthly', '1M': 'monthly',
}


def _split_market_code(code: str) -> Tuple[str, str, str]:
    """Return (panel_symbol, bare_code, market) without double-prefixing."""
    raw = str(code or '').strip()
    low = raw.lower()
    if low.startswith(('sh', 'sz', 'bj')) and len(raw) >= 8:
        market = low[:2]
        bare = raw[2:]
        return f'{market}{bare}', bare, market
    bare = raw.split('.')[0]
    if raw.upper().endswith('.SH'):
        return f'sh{bare}', bare, 'sh'
    if raw.upper().endswith('.SZ'):
        return f'sz{bare}', bare, 'sz'
    if raw.upper().endswith('.BJ'):
        return f'bj{bare}', bare, 'bj'
    market = 'bj' if bare.startswith(('83', '87', '88', '92', '43')) else 'sh' if bare.startswith(('6', '90', '5')) else 'sz'
    return f'{market}{bare}', bare, market


def normalize_period(period: str) -> str:
    """统一周期字符串，避免 1H/60m 分叉导致错误缓存或循环加载。"""
    if not period:
        return 'daily'
    p = str(period).strip()
    return PERIOD_ALIASES.get(p, p)


def clean_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize K-line rows and remove invalid OHLC records before JSON/SQLite."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if 'date' not in out.columns:
        return pd.DataFrame()
    out['date'] = out['date'].astype(str)
    out = out[out['date'].str.len() > 0]
    for col in ('open', 'high', 'low', 'close', 'volume'):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors='coerce')
    out = out.dropna(subset=['open', 'high', 'low', 'close'], how='all')
    for col in ('open', 'high', 'low', 'close', 'volume'):
        out[col] = out[col].where(pd.notna(out[col]), 0)
    return dedupe_kline_df(out)


def _qmt_http_daily(code: str, count: int = -1) -> pd.DataFrame:
    """Fetch stock/index daily bars from QMT strategy HTTP (/candles)."""
    def _bar_date(value):
        if value is None:
            return ''
        raw = str(value).strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) == 8:
            return f'{digits[:4]}-{digits[4:6]}-{digits[6:8]}'
        if raw.isdigit() and len(raw) >= 12:
            try:
                return str(pd.to_datetime(int(raw), unit='ms').date())
            except Exception:
                return raw[:10]
        return raw[:10] if len(raw) >= 10 else raw

    try:
        from data.qmt_http_client import get_qmt_http_client
        payload = get_qmt_http_client().candles(code, period='1d', count=count)
        if not payload or not payload.get('ok'):
            return pd.DataFrame()
        rows = []
        for bar in payload.get('bars') or []:
            if not isinstance(bar, dict):
                continue
            rows.append({
                'date': _bar_date(
                    bar.get('date') or bar.get('time') or bar.get('datetime')
                    or bar.get('trade_date') or bar.get('timestamp')
                ),
                'open': bar.get('open'),
                'high': bar.get('high'),
                'low': bar.get('low'),
                'close': bar.get('close') if bar.get('close') is not None else bar.get('price'),
                'volume': bar.get('volume', 0),
            })
        return clean_kline_df(pd.DataFrame(rows))
    except Exception as e:
        logger.debug(f"[KLine] qmt http daily failed {code}: {e}")
        return pd.DataFrame()


_LAST_BAR_REFRESH_TTL = 60.0
_last_bar_refresh_ts: Dict[str, float] = {}


def _should_refresh_last_bar(code: str) -> bool:
    last_ts = _last_bar_refresh_ts.get(str(code))
    return last_ts is None or (time.time() - last_ts) >= _LAST_BAR_REFRESH_TTL


def _mark_last_bar_refreshed(code: str):
    _last_bar_refresh_ts[str(code)] = time.time()


def _fetch_stock_supplement(code: str) -> pd.DataFrame:
    """Fetch a small authoritative daily supplement for the last bar."""
    return _qmt_http_daily(code, count=5)


def _last_bar_differs(local: pd.DataFrame, supplement: pd.DataFrame) -> bool:
    """Return True when the latest overlapping bar has different OHLCV values."""
    if local is None or local.empty or supplement is None or supplement.empty:
        return False
    if 'date' not in local.columns or 'date' not in supplement.columns:
        return False

    left = dedupe_kline_df(local).copy()
    right = dedupe_kline_df(supplement).copy()
    if left.empty or right.empty:
        return False
    left['date'] = left['date'].astype(str).str[:10]
    right['date'] = right['date'].astype(str).str[:10]

    last_date = str(left['date'].max())[:10]
    lrow = left[left['date'] == last_date]
    rrow = right[right['date'] == last_date]
    if lrow.empty or rrow.empty:
        return False
    lrow = lrow.iloc[-1]
    rrow = rrow.iloc[-1]

    for col in ('open', 'high', 'low', 'close', 'volume'):
        try:
            lv = float(lrow.get(col, 0) or 0)
            rv = float(rrow.get(col, 0) or 0)
        except Exception:
            return True
        if abs(lv - rv) > 1e-6:
            return True
    return False


def dedupe_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """按 date 去重（保留最后一条），再按时间升序。"""
    if df is None or df.empty or 'date' not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out['_sort'] = pd.to_datetime(out['date'], errors='coerce')
    out = out.sort_values('_sort')
    out = out.drop_duplicates(subset=['date'], keep='last')
    out = out.drop(columns='_sort').reset_index(drop=True)
    return out


def df_to_kline(df: pd.DataFrame) -> List[Dict]:
    """
    共享函数：DataFrame 转前端K线格式 [{timestamp, open, high, low, close, volume}]
    统一处理混合日期格式（YYYY-MM-DD / YYYYMMDD）、排序、分钟线/日线兼容。
    此函数是唯一的格式转换入口，禁止在其他文件中重复实现相同逻辑。
    """
    if df is None or df.empty:
        return []
    # 始终按日期升序排列（处理混合日期格式）
    df = df.copy()
    df['_sort_date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.sort_values('_sort_date').drop(columns='_sort_date').reset_index(drop=True)
    df = dedupe_kline_df(df)

    records = []
    seen_ts = set()
    for _, r in df.iterrows():
        date_str = str(r['date'])
        ts = int(pd.Timestamp(date_str).timestamp() * 1000)
        if ts in seen_ts:
            continue
        o = r.get('open')
        h = r.get('high')
        l = r.get('low')
        c = r.get('close')
        if pd.isna(o) and pd.isna(h) and pd.isna(l) and pd.isna(c):
            continue
        seen_ts.add(ts)

        def _finite_float(value, default=0.0):
            try:
                f = float(value)
                return f if math.isfinite(f) else default
            except Exception:
                return default

        records.append({
            'timestamp': ts,
            'open': _finite_float(o),
            'high': _finite_float(h),
            'low': _finite_float(l),
            'close': _finite_float(c),
            'volume': int(_finite_float(r.get('volume', 0)))
        })
    return records


# ---- 线程池 ----

_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='kline_loader')
_bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kline_bg_refresh')
_pending = {}  # cache_key -> Event
_pending_lock = threading.Lock()

# 进程退出时优雅关闭线程池
atexit.register(lambda: _executor.shutdown(wait=False, cancel_futures=True))
atexit.register(lambda: _bg_executor.shutdown(wait=False, cancel_futures=True))


def _get_bg_executor():
    """Return the module-level background executor (overridable for tests)."""
    return _bg_executor


def reset_bg_executor():
    """Replace the module-level background executor with a fresh one.

    Useful for test isolation so that stale futures from a previous test
    cannot bleed into the next test.
    """
    global _bg_executor
    try:
        _bg_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    _bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kline_bg_refresh')


class KLineService:
    """K线数据服务（线程安全）"""

    def __init__(self):
        self._cache = get_cache()
        self._db = get_sqlite_repo()
        self._qmt = get_qmt_client()

    # ---- 公开接口 ----

    def get_kline(self, data_type: str, code: str, period: str,
                  board_name: str = '', force: bool = False, timeout: float = 15,
                  cache_first: bool = False):
        """
        主入口：获取K线数据（同步等待或返回loading信号）
        返回: (result_dict, status_code)
        """
        period = normalize_period(period)
        cache_key = f'{data_type}:{code}:{period}'

        # 强制刷新
        if force:
            self._cache.delete(cache_key)

        # 缓存命中
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._ok_response(cached, cache_key, source='cache'), 200

        if cache_first and not force:
            stale = self._read_sqlite_fast(code, period)
            if stale is not None and not stale.empty:
                data, last_date = self._format_response(stale)
                self._cache.set(cache_key, data)
                resp = self._ok_response(
                    data, cache_key, last_date=last_date, source='sqlite'
                )
                resp['stale'] = True
                resp['background_refresh_started'] = self._submit_background_refresh(
                    data_type, code, period, board_name, cache_key
                )
                return resp, 200

        # 正在加载中 → 等待
        evt = None
        with _pending_lock:
            if cache_key in _pending:
                evt = _pending[cache_key]
        if evt is not None:
            if evt.wait(min(timeout, 10)):
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return self._ok_response(cached, cache_key, source='pending'), 200
            # 超时
            return {'loading': True, 'message': '数据加载中'}, 202

        # 提交到线程池
        evt = threading.Event()
        with _pending_lock:
            _pending[cache_key] = evt

        future = _executor.submit(self._do_load, data_type, code, period, board_name, cache_key)

        try:
            df = future.result(timeout=timeout)
            if df is None:
                raise Exception("加载超时")
            data, last_date = self._format_response(df)
            self._cache.set(cache_key, data)
            with _pending_lock:
                _pending.pop(cache_key, None)
            return self._ok_response(data, cache_key, last_date=last_date), 200
        except Exception as e:
            # 如果出错但缓存有数据（后台刷新过）
            cached = self._cache.get(cache_key)
            if cached is not None:
                return self._ok_response(cached, cache_key, source='cache_stale'), 200
            if cache_first:
                stale = self._read_sqlite_fast(code, period)
                if stale is not None and not stale.empty:
                    data, last_date = self._format_response(stale)
                    resp = self._ok_response(
                        data, cache_key, last_date=last_date, source='sqlite'
                    )
                    resp['stale'] = True
                    resp['background_refresh_started'] = False
                    return resp, 200
            is_timeout = 'timeout' in str(e).lower() or isinstance(e, TimeoutError)
            with _pending_lock:
                _pending.pop(cache_key, None)
            return {
                'error': str(e), 'timeout': is_timeout,
                'data': [], 'count': 0
            }, 408 if is_timeout else 500

    # ---- 内部实现 ----

    def _read_sqlite_fast(self, code: str, period: str) -> pd.DataFrame:
        """快速读取本地 K 线；只做去重清洗，不触发外部数据源。"""
        try:
            df = self._db.read_kline(code, period)
            return dedupe_kline_df(df) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    def _submit_background_refresh(self, data_type: str, code: str, period: str,
                                   board_name: str, cache_key: str) -> bool:
        with _pending_lock:
            if cache_key in _pending:
                return False
            evt = threading.Event()
            _pending[cache_key] = evt
        loader = self._do_load

        def _runner():
            try:
                df = loader(data_type, code, period, board_name, cache_key)
                if df is not None and not df.empty:
                    data, _ = self._format_response(df)
                    self._cache.set(cache_key, data)
            except Exception as e:
                logger.warning(
                    "[KLine] 后台刷新失败 %s: %s: %r",
                    cache_key, type(e).__name__, e,
                    exc_info=True,
                )
            finally:
                with _pending_lock:
                    pending_evt = _pending.pop(cache_key, None)
                if pending_evt is not None:
                    pending_evt.set()

        _get_bg_executor().submit(_runner)
        return True

    def _do_load(self, data_type: str, code: str, period: str,
                 board_name: str, cache_key: str):
        """Load K-line data inside the worker pool."""
        try:
            period = normalize_period(period)
            if period in MINUTE_PERIODS:
                return self._load_minute(data_type, code, period)
            if period == 'daily':
                return self._load_daily(data_type, code, board_name)
            if period in RESAMPLE_PERIODS:
                return dedupe_kline_df(self._load_resample(data_type, code, period, board_name))
            if data_type in ('industry', 'concept'):
                return self._load_board_non_daily(data_type, code, period, board_name)
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"[KLine] load failed {data_type}:{code} {period}: {e}")
            return pd.DataFrame()

    def _load_minute(self, data_type: str, code: str, period: str) -> pd.DataFrame:
        """Load minute bars from QMT, resampling from 1m where required."""
        if not is_qmt_available():
            return pd.DataFrame()
        if period in RESAMPLE_FROM_1M:
            df_1m = self._qmt.get_minute_kline(code, data_type, '1m')
            if (df_1m is None or df_1m.empty) and data_type in ('index', 'stock', 'hk_index'):
                df_1m = self._qmt.get_minute_kline(code, 'hk_index', '1m')
            if df_1m is not None and not df_1m.empty:
                return dedupe_kline_df(self._resample_from_1m(df_1m, period))
            return pd.DataFrame()

        df = self._qmt.get_minute_kline(code, data_type, period)
        if (df is None or df.empty) and data_type in ('index', 'stock', 'hk_index'):
            df = self._qmt.get_minute_kline(code, 'hk_index', period)
        return dedupe_kline_df(df) if df is not None else pd.DataFrame()

    def _load_daily(self, data_type: str, code: str, board_name: str) -> pd.DataFrame:
        """Load daily bars, preserving the current source priority."""
        if data_type in ('industry', 'concept'):
            return self._load_board_daily(data_type, code, board_name)

        df = self._db.read_kline(code, 'daily')
        if df is not None and not df.empty:
            df = self._ensure_latest_kline_bar(data_type, code, 'daily', df)
            return dedupe_kline_df(df)

        if data_type in ('hk_index', 'us'):
            df = load_global_index_kline(code, 'daily')
            if df is not None and not df.empty:
                return dedupe_kline_df(df)

        qmt_http_df = _qmt_http_daily(code, count=-1)
        if qmt_http_df is not None and not qmt_http_df.empty:
            self._db.save_kline(code, 'daily', qmt_http_df)
            df = self._db.read_kline(code, 'daily')
            if df is not None and not df.empty:
                return dedupe_kline_df(df)

        if is_qmt_available():
            qmt_code = self._qmt.to_qmt_code(code, data_type)
            qdf = self._qmt.get_daily(qmt_code, start='20200101', count=-1)
            if qdf is not None and not qdf.empty:
                self._db.save_kline(code, 'daily', qdf)
                df = self._db.read_kline(code, 'daily')
                if df is not None and not df.empty:
                    return dedupe_kline_df(df)
        return pd.DataFrame()

    def _load_board_daily(self, data_type: str, code: str, board_name: str) -> pd.DataFrame:
        """Load board daily bars through the board updater, not stale SQLite only."""
        df = load_board_kline(data_type, board_name or code, code, 'daily')
        if df is not None and not df.empty:
            try:
                self._db.save_kline(code, 'daily', df)
            except Exception:
                pass
            return dedupe_kline_df(df)
        df = self._db.read_kline(code, 'daily')
        return dedupe_kline_df(df) if df is not None else pd.DataFrame()

    def _load_board_non_daily(self, data_type: str, code: str, period: str, board_name: str) -> pd.DataFrame:
        """Load direct non-daily board bars before falling back to the board loader."""
        df = self._db.read_kline(code, period)
        if df is not None and not df.empty:
            return dedupe_kline_df(df)
        df = load_board_kline(data_type, board_name, code, period)
        if df is not None and not df.empty:
            df = self._ensure_latest_kline_bar(data_type, code, period, df)
            return dedupe_kline_df(df)
        return pd.DataFrame()

    def _resample_from_1m(self, df_1m: pd.DataFrame, period: str) -> pd.DataFrame:
        """Resample 1m K线到目标周期（15m/30m/60m/120m/240m）"""
        if df_1m is None or df_1m.empty:
            return pd.DataFrame()

        try:
            df = df_1m.copy()
            # 解析日期为datetime索引
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')

            # 按交易日分组resample
            result_frames = []
            for date, day_df in df.groupby(df.index.date):
                if len(day_df) < 2:
                    continue
                day_df = day_df.sort_index()
                rule = RESAMPLE_FROM_1M[period]
                resampled = day_df.resample(rule).agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }).dropna(subset=['open'])
                result_frames.append(resampled)

            if not result_frames:
                return pd.DataFrame()

            result = pd.concat(result_frames)
            result = result.reset_index()
            result['date'] = result['date'].dt.strftime('%Y-%m-%d %H:%M')
            # 过滤交易时间 9:30-11:30, 13:00-15:00
            dt = pd.to_datetime(result['date'])
            time_val = dt.dt.hour * 100 + dt.dt.minute
            mask = ((time_val >= 930) & (time_val <= 1130)) | ((time_val >= 1300) & (time_val <= 1500))
            result = result[mask]
            return result

        except Exception as e:
            logger.error(f"[KLine] resample 1m→{period} 失败: {e}")
            return pd.DataFrame()

    def _load_resample(self, data_type: str, code: str, period: str, board_name: str) -> pd.DataFrame:
        """从日线重采样（统一读 SQLite 日线，避免 loader 旁路不一致）。"""
        from data_loader import _resample
        df = self._db.read_kline(code, 'daily')
        if df is None or df.empty:
            if data_type not in ('industry', 'concept') and is_qmt_available():
                daily_df = self._do_load(
                    data_type, code, 'daily', board_name, f'{data_type}:{code}:daily'
                )
                df = daily_df if daily_df is not None else pd.DataFrame()
            if (df is None or df.empty) and data_type in ('industry', 'concept'):
                df = load_board_kline(data_type, board_name, code, 'daily')
        if df is None or df.empty:
            return pd.DataFrame()

        last_daily = str(df['date'].max())[:10].replace('/', '-')
        # 缓存可用且末 bar 不超过日线末交易日 → 直接用；否则按日线重算（修未来月末标签）
        cached = self._db.read_kline(code, period)
        if cached is not None and not cached.empty:
            last_c = str(cached['date'].max())[:10].replace('/', '-')
            if last_c <= last_daily and len(cached) >= 1:
                return cached

        out = _resample(df, period)
        try:
            if out is not None and not out.empty:
                self._db.save_kline(code, period, out)
        except Exception:
            pass
        return out if out is not None else pd.DataFrame()

    # ---- 格式化响应 ----

    def _format_response(self, df: pd.DataFrame):
        """
        DataFrame转换为前端期望的格式:
        - timestamp(ms), open, high, low, close, volume
        - 日线用'%Y-%m-%d'，分钟线用'%Y-%m-%d %H:%M'
        - 使用共享函数 df_to_kline 统一处理
        """
        if df.empty:
            return [], ''
        records = df_to_kline(df)
        last_date = str(df['date'].max()) if 'date' in df.columns else ''
        return records, last_date

    def _ok_response(self, data, cache_key, last_date='', source='load'):
        now = pd.Timestamp.now()
        if not last_date and data:
            try:
                last_date = pd.to_datetime(
                    data[-1].get('timestamp'), unit='ms'
                ).strftime('%Y-%m-%d')
            except Exception:
                last_date = ''
        return {
            'data': data,
            'count': len(data),
            'last_date': last_date,
            'today': now.strftime('%Y-%m-%d'),
            'range': f'{data[0]["timestamp"]}~{data[-1]["timestamp"]}' if data else '',
            'cached': source == 'cache',
            'source': source
        }

    def _ensure_latest_kline_bar(self, data_type: str, code: str, period: str, df: pd.DataFrame) -> pd.DataFrame:
        """数据自动检验与降级补齐机制：
        若加载的数据最新日期早于目标交易日（欠更），自动通过 qmt-http-server (/candles) 或 Spot 在线接口补齐最新交易日 K 线并自动持久化写库。
        """
        if period != 'daily' or data_type not in ('stock', 'index', 'hk_index'):
            return df

        try:
            from data.board_api import get_last_trading_date
            target_date = str(get_last_trading_date())[:10]
        except Exception:
            target_date = datetime.date.today().strftime('%Y-%m-%d')

        last_date = ''
        if df is not None and not df.empty and 'date' in df.columns:
            last_date = str(df['date'].max())[:10]
            if last_date >= target_date:
                if _should_refresh_last_bar(code):
                    supp_df = _fetch_stock_supplement(code)
                    _mark_last_bar_refreshed(code)
                    if supp_df is not None and not supp_df.empty and _last_bar_differs(df, supp_df):
                        self._db.save_kline(code, period, supp_df)
                        refreshed = self._db.read_kline(code, period)
                        if refreshed is not None and not refreshed.empty:
                            return dedupe_kline_df(refreshed)
                        return dedupe_kline_df(pd.concat([df, supp_df], ignore_index=True))
                return df

        logger.info(f"[AutoCheck] 检测到 {code} 数据欠更 (最新={last_date or '空'}, 目标={target_date})，启动 QMT-HTTP/Spot 自动补全...")

        # 1. 优先调用 QMT-HTTP-Server (/candles)
        supp_df = _qmt_http_daily(code, count=5)
        if supp_df is not None and not supp_df.empty:
            supp_last_date = str(supp_df['date'].max())[:10]
            if supp_last_date >= target_date:
                self._db.save_kline(code, period, supp_df)
                if df is not None and not df.empty:
                    df = dedupe_kline_df(pd.concat([df, supp_df], ignore_index=True))
                else:
                    df = supp_df
                logger.info(f"[AutoCheck] QMT-HTTP 成功自动补全 {code} 最新 {supp_last_date} 数据！")
                return df

        # 2. 降级通过 Spot 在线行情抓取
        try:
            from data_loader import get_spot_stock
            spot = get_spot_stock(code)
            if spot and spot.get('price', 0) > 0:
                px = spot['price']
                open_px = spot.get('open') or px
                high_px = spot.get('high') or px
                low_px = spot.get('low') or px
                vol = spot.get('volume', 0)
                
                spot_df = pd.DataFrame([{
                    'date': target_date,
                    'open': open_px,
                    'high': high_px,
                    'low': low_px,
                    'close': px,
                    'volume': vol
                }])
                self._db.save_kline(code, period, spot_df)
                if df is not None and not df.empty:
                    df = dedupe_kline_df(pd.concat([df, spot_df], ignore_index=True))
                else:
                    df = spot_df
                logger.info(f"[AutoCheck] Spot 接口成功自动补全 {code} 最新 {target_date} 数据！")
                return df
        except Exception as e:
            logger.warning(f"[AutoCheck] 自动补全 {code} 异常: {e}")

        return df


def _format_kline_helper(df: pd.DataFrame):
    """模块级辅助：DataFrame转为前端格式（使用共享函数 df_to_kline）"""
    return df_to_kline(df)


# 全局单例

_kline_service: Optional[KLineService] = None


def get_kline_service() -> KLineService:
    global _kline_service
    if _kline_service is None:
        _kline_service = KLineService()
    return _kline_service
