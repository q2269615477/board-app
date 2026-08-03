"""
services/kline_service.py — K线数据业务逻辑
统一K线加载入口：分钟线(QMT子进程)/日线(QMT+缓存)/重采样(月/季/年线)
"""
import math
import re
import time
import atexit
import threading
import logging
import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, List, Dict, Tuple

import pandas as pd

from data.sqlite_repo import get_sqlite_repo
from data.qmt_client import get_qmt_client
from data.board_kline import load_board_kline
from data.global_index_kline import (
    load_global_index_kline,
    load_a_share_index_kline,
    fetch_eastmoney_global_kline,
    is_inactive_a_share_index,
    is_standard_a_share_index_code,
)
from core.cache import get_cache
from core.lifecycle import is_qmt_available

logger = logging.getLogger('kline_service')

MINUTE_PERIODS = {'1m', '5m', '15m', '30m', '60m', '120m', '240m'}
RESAMPLE_PERIODS = {'weekly', 'monthly', 'quarterly', 'yearly'}
GLOBAL_INDEX_TYPES = {'hk_index', 'us', 'global_index'}
EASTMONEY_INDEX_CODES = {'800000'}
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


def _is_a_share_live_session(now=None) -> bool:
    """Return whether an A-share trading session can still change today's bar."""
    current = now or datetime.datetime.now()
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return 9 * 60 + 15 <= minute < 15 * 60


def _is_a_share_index_code(code: str) -> bool:
    return bool(re.match(r'^(sh|sz|bj)\d{6}$', str(code or '').lower()))


_HK_KLINE_CODES = frozenset({'HSI', 'HSTECH'})
_GLOBAL_MARKET_TIMEZONES = {
    'us': 'America/New_York',
    'japan': 'Asia/Tokyo',
    'south_korea': 'Asia/Seoul',
    'taiwan': 'Asia/Taipei',
}


def _kline_market_key(data_type: Optional[str], code: str) -> str:
    """Map a kline (data_type, code) to the exchange-calendar market key.

    A-share symbols use ``a_share``; HSI/HSTECH (hk_index) use ``hong_kong``;
    overseas indices use their own market (``us``/``japan``/...). Kept in
    sync with services.market_session.classify_market.
    """
    raw = str(code or '').strip()
    upper = raw.upper()
    if upper in _HK_KLINE_CODES or data_type == 'hk_index':
        return 'hong_kong'
    if data_type in ('us', 'global_index'):
        from services.market_session import classify_market
        market = classify_market(raw, data_type)
        if market in _GLOBAL_MARKET_TIMEZONES:
            return market
        return 'us'
    return 'a_share'


def _exchange_calendar_fn():
    """Return the exchange-calendar entry point, or None when unavailable.

    services.exchange_calendar_service is provided by a sibling workstream:
    ``latest_expected_session_date(code_or_market, now=None, data_type=None)
    -> str``. The import stays lazy so kline works without that module.
    """
    try:
        from services.exchange_calendar_service import latest_expected_session_date
        return latest_expected_session_date
    except Exception:
        return None


def _weekday_last_session_date(tz_name: str, now=None) -> str:
    """Best-effort fallback: the last weekday in the exchange's local timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None
    if now is None:
        local = datetime.datetime.now(tz or datetime.timezone.utc)
    elif now.tzinfo is None:
        local = now.replace(tzinfo=tz) if tz else now
    else:
        local = now.astimezone(tz) if tz else now
    day = local.date()
    while day.weekday() >= 5:
        day -= datetime.timedelta(days=1)
    return day.strftime('%Y-%m-%d')


def _latest_expected_session_date(
    code: str, data_type: Optional[str] = None, now=None
) -> str:
    """Exchange-local latest expected session date (YYYY-MM-DD).

    Primary source is services.exchange_calendar_service. When it is
    unavailable, A-share symbols keep the existing get_last_trading_date
    behavior and overseas symbols use a weekday fallback in the exchange's
    local timezone (DST-aware via zoneinfo).
    """
    calendar_fn = _exchange_calendar_fn()
    if calendar_fn is not None:
        try:
            result = calendar_fn(code, now=now, data_type=data_type)
            if result:
                return str(result)[:10]
        except Exception as exc:
            logger.warning(
                '[KLine] exchange calendar failed for %s: %s', code, exc
            )

    market = _kline_market_key(data_type, code)
    if market == 'hong_kong':
        return _weekday_last_session_date('Asia/Hong_Kong', now)
    if market in _GLOBAL_MARKET_TIMEZONES:
        return _weekday_last_session_date(_GLOBAL_MARKET_TIMEZONES[market], now)
    try:
        from data.board_api import get_last_trading_date
        if now is None:
            return str(get_last_trading_date())[:10]
        ref = _weekday_last_session_date('Asia/Shanghai', now)
        return str(get_last_trading_date(ref))[:10]
    except Exception:
        return _weekday_last_session_date('Asia/Shanghai', now)


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


def _response_cache_ttl(data_type: str, period: str,
                        code: str = '') -> Optional[int]:
    """Overseas daily bars must not inherit the long generic cache TTL."""
    if ((data_type in GLOBAL_INDEX_TYPES or code in EASTMONEY_INDEX_CODES
         or (data_type == 'index' and _is_a_share_index_code(code)))
            and period in ({'daily'} | RESAMPLE_PERIODS)):
        return 30
    return None


def clean_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize K-line rows and remove invalid OHLC records before JSON/SQLite."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if 'date' not in out.columns:
        return pd.DataFrame()
    out['date'] = out['date'].astype(str)
    out = out[out['date'].str.len() > 0]
    if 'amount' not in out.columns and 'turnover' in out.columns:
        out['amount'] = out['turnover']
    for col in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors='coerce')
    out = out.dropna(subset=['open', 'high', 'low', 'close'], how='all')
    for col in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        out[col] = out[col].where(pd.notna(out[col]), 0)
    return dedupe_kline_df(out)


def _is_safe_a_share_index_daily_cache(df: pd.DataFrame, window: int = 180) -> bool:
    """Validate a local daily tail before using it for cache-first paint."""
    if df is None or df.empty or 'date' not in df.columns:
        return False
    dates = pd.to_datetime(df['date'], errors='coerce')
    if dates.isna().any():
        return False
    tail = df.copy()
    tail['_date'] = dates
    tail = tail.sort_values('_date', kind='stable').tail(max(1, int(window)))
    tail_dates = tail['_date']
    if (tail_dates.dt.dayofweek >= 5).any():
        return False

    columns = ('open', 'high', 'low', 'close')
    for column in columns:
        if column not in tail.columns:
            return False
        tail[column] = pd.to_numeric(tail[column], errors='coerce')
    if tail[list(columns)].isna().any().any():
        return False
    if (tail[list(columns)] <= 0).any().any():
        return False
    if not (
        (tail['low'] <= tail['open'])
        & (tail['low'] <= tail['close'])
        & (tail['open'] <= tail['high'])
        & (tail['close'] <= tail['high'])
    ).all():
        return False

    closes = tail['close'].reset_index(drop=True)
    previous = closes.shift(1)
    jumps = ((closes / previous) - 1).abs()
    return not jumps.iloc[1:].gt(0.25).any()


def _qmt_http_candles(code: str, period: str = '1d', count: int = -1) -> pd.DataFrame:
    """Fetch bars from QMT strategy HTTP (/candles) without touching SQLite."""
    def _bar_volume(value):
        try:
            volume = float(value or 0)
            if -(2 ** 31) <= volume < 0:
                volume += 2 ** 32
            return volume
        except (TypeError, ValueError):
            return 0

    def _bar_date(value):
        if value is None:
            return ''
        raw = str(value).strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) == 8:
            return f'{digits[:4]}-{digits[4:6]}-{digits[6:8]}'
        if len(digits) == 14:
            try:
                return pd.to_datetime(digits, format='%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
        if len(digits) == 12:
            try:
                return pd.to_datetime(digits, format='%Y%m%d%H%M').strftime('%Y-%m-%d %H:%M')
            except Exception:
                pass
        if raw.isdigit() and len(raw) in (10, 13):
            try:
                unit = 's' if len(raw) == 10 else 'ms'
                return str(pd.to_datetime(int(raw), unit=unit))
            except Exception:
                return raw[:10]
        return raw[:10] if len(raw) >= 10 else raw

    try:
        from data.qmt_http_client import get_qmt_http_client
        payload = get_qmt_http_client().candles(code, period=period, count=count)
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
                'volume': _bar_volume(bar.get('volume', 0)),
                'amount': bar.get('amount', bar.get('turnover', 0)),
            })
        result = clean_kline_df(pd.DataFrame(rows))
        # The 18080 full-history endpoint can expose zero-volume synthetic
        # rows before an individual stock was listed. Trim only the leading
        # synthetic prefix; do not remove later zero-volume suspension rows.
        if not result.empty and 'volume' in result.columns:
            positive = result.index[pd.to_numeric(result['volume'], errors='coerce').fillna(0) > 0]
            if len(positive):
                result = result.loc[positive[0]:].reset_index(drop=True)
        return result
    except Exception as e:
        logger.debug(f"[KLine] qmt http candles failed {code} {period}: {e}")
        return pd.DataFrame()


def _qmt_http_daily(code: str, count: int = -1) -> pd.DataFrame:
    """Fetch stock/index daily bars from QMT strategy HTTP (/candles)."""
    explicit_count = 12000 if count is None or count <= 0 else count
    return _qmt_http_candles(code, period='1d', count=explicit_count)


def _qmt_http_ohlc(code: str) -> Dict:
    """Fetch one domestic stock/index OHLC snapshot from QMT HTTP."""
    try:
        from data.qmt_http_client import get_qmt_http_client

        payload = get_qmt_http_client().ohlc_batch([code], timeout=2.0)
        items = payload.get('items') or {}
        row = items.get(code)
        if row is None and items:
            row = next(iter(items.values()))
        if not isinstance(row, dict):
            return {}
        close = row.get('close')
        if close is None:
            close = row.get('price')
        try:
            if float(close or 0) <= 0:
                return {}
        except (TypeError, ValueError):
            return {}
        result = {
            'open': row.get('open'),
            'high': row.get('high'),
            'low': row.get('low'),
            'close': close,
            'volume': row.get('volume', 0),
            'amount': row.get('amount', row.get('turnover', 0)),
            'price': close,
            'channel': row.get('channel') or 'qmt18080',
        }
        return {key: value for key, value in result.items() if value is not None}
    except Exception as e:
        logger.debug(f"[KLine] qmt http ohlc failed {code}: {e}")
        return {}


_LAST_BAR_REFRESH_TTL = 60.0
_last_bar_refresh_ts: Dict[str, float] = {}
_HISTORY_REFRESH_TTL = 6 * 60 * 60.0
_history_refresh_ts: Dict[str, float] = {}


def _should_refresh_last_bar(code: str) -> bool:
    last_ts = _last_bar_refresh_ts.get(str(code))
    return last_ts is None or (time.time() - last_ts) >= _LAST_BAR_REFRESH_TTL


def _mark_last_bar_refreshed(code: str):
    _last_bar_refresh_ts[str(code)] = time.time()


def _should_refresh_history(code: str) -> bool:
    last_ts = _history_refresh_ts.get(str(code))
    return last_ts is None or (time.time() - last_ts) >= _HISTORY_REFRESH_TTL


def _mark_history_refreshed(code: str):
    _history_refresh_ts[str(code)] = time.time()


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


def _overlapping_bars_differ(local: pd.DataFrame, authoritative: pd.DataFrame) -> bool:
    """Return True when the authoritative frame corrects any shared date."""
    if (
        local is None or local.empty
        or authoritative is None or authoritative.empty
    ):
        return False
    left = dedupe_kline_df(local)
    right = dedupe_kline_df(authoritative)
    shared = left.merge(
        right,
        on='date',
        how='inner',
        suffixes=('_local', '_remote'),
    )
    if shared.empty:
        return False
    for column in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        local_name = f'{column}_local'
        remote_name = f'{column}_remote'
        if local_name not in shared or remote_name not in shared:
            continue
        local_values = pd.to_numeric(shared[local_name], errors='coerce').fillna(0)
        remote_values = pd.to_numeric(shared[remote_name], errors='coerce').fillna(0)
        if ((local_values - remote_values).abs() > 1e-6).any():
            return True
    return False


def dedupe_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """按 date 去重（保留最后一条），再按时间升序。"""
    if df is None or df.empty or 'date' not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out['_sort'] = pd.to_datetime(out['date'], errors='coerce')
    # Stable ordering makes an authoritative frame appended later win
    # deterministically when the same date occurs in both frames.
    out = out.sort_values('_sort', kind='stable')
    out = out.drop_duplicates(subset=['date'], keep='last')
    out = out.drop(columns='_sort').reset_index(drop=True)
    return out


def df_to_kline(df: pd.DataFrame) -> List[Dict]:
    """
    共享函数：DataFrame 转前端K线格式（含真实成交额 amount/turnover）。
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

        amount = _finite_float(r.get('amount', r.get('turnover', 0)))
        records.append({
            'timestamp': ts,
            'open': _finite_float(o),
            'high': _finite_float(h),
            'low': _finite_float(l),
            'close': _finite_float(c),
            'volume': int(_finite_float(r.get('volume', 0))),
            'amount': amount,
            'turnover': amount,
        })
    return records


# ---- 线程池 ----

_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='kline_loader')
_bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kline_bg_refresh')
_pending = {}  # cache_key -> Event
_pending_lock = threading.Lock()


def _nonnegative_load_ms(value) -> int:
    """Normalize a load duration for the public response contract.

    Loading paths are allowed to pass a measured duration, but callers and
    tests may also invoke :meth:`_ok_response` directly.  Keep the wire value
    stable even when a clock moves backwards or a source returns an invalid
    value: it is always a non-negative ``int``.
    """
    try:
        number = float(value)
        if not math.isfinite(number):
            return 0
        return max(0, int(number))
    except (TypeError, ValueError, OverflowError):
        return 0


def _fallback_chain_list(value) -> List:
    """Return a defensive list for the public fallback-chain field."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return [value]


def _load_elapsed_ms(start_time: float) -> int:
    """Measure elapsed wall-clock time without exposing negative values."""
    return _nonnegative_load_ms((time.time() - start_time) * 1000)


def _claim_pending(cache_key: str) -> Tuple[threading.Event, bool]:
    """Atomically return the current event or reserve this cache key."""
    with _pending_lock:
        current = _pending.get(cache_key)
        if current is not None:
            return current, False
        evt = threading.Event()
        _pending[cache_key] = evt
        return evt, True


def _release_pending(cache_key: str, evt: threading.Event) -> bool:
    """Release only the reservation owned by ``evt`` and wake its waiters."""
    with _pending_lock:
        owned = _pending.get(cache_key) is evt
        if owned:
            _pending.pop(cache_key, None)
    evt.set()
    return owned

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
    with _pending_lock:
        pending_events = list(_pending.values())
        _pending.clear()
    for event in pending_events:
        event.set()
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
        start_time = time.time()
        try:
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return {
                'error': f'非法 timeout 参数: {timeout}',
                'timeout': False,
                'data': [],
                'count': 0,
                'source': 'invalid_request',
                'stale': False,
                'background_refresh_started': False,
                'load_ms': _load_elapsed_ms(start_time),
                'fallback_chain': [],
            }, 400
        period = normalize_period(period)
        cache_key = f'{data_type}:{code}:{period}'
        fallback_chain = []
        standard_a_index = (
            data_type == 'index' and is_standard_a_share_index_code(code)
        )

        if standard_a_index and is_inactive_a_share_index(code):
            self._cache.delete(cache_key)
            response = self._ok_response(
                [], cache_key, source='unavailable',
                load_ms=_load_elapsed_ms(start_time),
                fallback_chain=['unavailable'],
            )
            response.update({
                'unavailable': True,
                'reason': 'deprecated_no_remote',
            })
            return response, 200

        # 强制刷新
        if force:
            self._cache.delete(cache_key)

        # 缓存命中
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._ok_response(
                cached, cache_key, source='cache',
                load_ms=_load_elapsed_ms(start_time),
                fallback_chain=fallback_chain,
                intraday={},
            ), 200

        if (cache_first and not force
                and (not standard_a_index or period == 'daily')):
            stale = self._read_sqlite_fast(code, period)
            if period == 'daily':
                stale = self._attach_board_snapshot(data_type, code, stale)
            safe_fast_path = (
                not standard_a_index
                or (period == 'daily' and _is_safe_a_share_index_daily_cache(stale))
            )
            if safe_fast_path and stale is not None and not stale.empty:
                data, last_date = self._format_response(stale)
                self._cache.set(
                    cache_key, data,
                    ttl=_response_cache_ttl(data_type, period, code),
                )
                resp = self._ok_response(
                    data, cache_key, last_date=last_date, source='sqlite',
                    load_ms=_load_elapsed_ms(start_time),
                    fallback_chain=['sqlite'], stale=True,
                    intraday={},
                )
                resp['background_refresh_started'] = self._submit_background_refresh(
                    data_type, code, period, board_name, cache_key
                )
                return resp, 200

        # 原子地预留 key：已有加载则等待，否则当前请求负责提交。
        evt, should_submit = _claim_pending(cache_key)
        if not should_submit:
            if evt.wait(min(timeout, 10)):
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return self._ok_response(
                        cached, cache_key, source='pending',
                        load_ms=_load_elapsed_ms(start_time),
                        fallback_chain=fallback_chain,
                        intraday={},
                    ), 200
            # 超时
            return {
                'loading': True,
                'message': '数据加载中',
                'source': 'pending',
                'stale': False,
                'background_refresh_started': False,
                'load_ms': _load_elapsed_ms(start_time),
                'fallback_chain': _fallback_chain_list(fallback_chain),
            }, 202

        try:
            future = _executor.submit(
                self._do_load, data_type, code, period, board_name, cache_key
            )
            df = future.result(timeout=timeout)
            if df is None:
                raise Exception("加载超时")
            # 从 df.attrs 读取数据源元信息
            df_source = getattr(df, 'attrs', {}).get('source', 'load')
            df_fallback = getattr(df, 'attrs', {}).get('fallback_chain', [])
            data, last_date = self._format_response(df)
            self._cache.set(
                cache_key, data,
                ttl=_response_cache_ttl(data_type, period, code),
            )
            _release_pending(cache_key, evt)
            return self._ok_response(
                data, cache_key, last_date=last_date, source=df_source,
                load_ms=_load_elapsed_ms(start_time),
                fallback_chain=df_fallback,
                intraday=self._response_intraday(data_type, code, period, df),
            ), 200
        except Exception as e:
            # 所有提交、加载和超时分支都必须释放当前请求的预留。
            _release_pending(cache_key, evt)
            # 如果出错但缓存有数据（后台刷新过）
            cached = self._cache.get(cache_key)
            if cached is not None:
                return self._ok_response(
                    cached, cache_key, source='cache_stale', stale=True,
                    load_ms=_load_elapsed_ms(start_time),
                    fallback_chain=fallback_chain,
                    intraday={},
                ), 200
            if cache_first:
                stale = self._read_sqlite_fast(code, period)
                if period == 'daily':
                    stale = self._attach_board_snapshot(data_type, code, stale)
                if stale is not None and not stale.empty:
                    data, last_date = self._format_response(stale)
                    resp = self._ok_response(
                        data, cache_key, last_date=last_date, source='sqlite',
                        load_ms=_load_elapsed_ms(start_time),
                        fallback_chain=['sqlite'], stale=True,
                        intraday={},
                    )
                    return resp, 200
            is_timeout = 'timeout' in str(e).lower() or isinstance(e, TimeoutError)
            return {
                'error': str(e), 'timeout': is_timeout,
                'data': [], 'count': 0,
                'source': 'error',
                'stale': False,
                'background_refresh_started': False,
                'load_ms': _load_elapsed_ms(start_time),
                'fallback_chain': _fallback_chain_list(fallback_chain),
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
        evt, should_submit = _claim_pending(cache_key)
        if not should_submit:
            return False
        loader = self._do_load

        def _runner():
            try:
                df = loader(data_type, code, period, board_name, cache_key)
                if df is not None and not df.empty:
                    data, _ = self._format_response(df)
                    self._cache.set(
                        cache_key, data,
                        ttl=_response_cache_ttl(data_type, period, code),
                    )
            except Exception as e:
                logger.warning(
                    "[KLine] 后台刷新失败 %s: %s: %r",
                    cache_key, type(e).__name__, e,
                    exc_info=True,
                )
            finally:
                _release_pending(cache_key, evt)

        try:
            _get_bg_executor().submit(_runner)
        except Exception as e:
            _release_pending(cache_key, evt)
            logger.warning(
                "[KLine] 后台刷新提交失败 %s: %s: %r",
                cache_key, type(e).__name__, e,
            )
            return False
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
        """Load minute bars: QMT HTTP first, xtdata only as a fallback."""
        if data_type in ('stock', 'index'):
            if period == '1m':
                http_df = _qmt_http_candles(code, period='1m', count=2000)
            else:
                # 1m is the authoritative source for session-aware buckets.
                http_df = _qmt_http_candles(code, period='1m', count=2000)
                if http_df is not None and not http_df.empty:
                    result = dedupe_kline_df(self._resample_from_1m(http_df, period))
                    result.attrs['source'] = 'qmt_http'
                    result.attrs['fallback_chain'] = ['qmt_http_1m']
                    return result
                if period not in ('120m', '240m'):
                    http_df = _qmt_http_candles(code, period=period, count=2000)
            if http_df is not None and not http_df.empty:
                result = dedupe_kline_df(http_df)
                result.attrs['source'] = 'qmt_http'
                result.attrs['fallback_chain'] = ['qmt_http']
                return result

        if not is_qmt_available():
            return pd.DataFrame()

        if period in RESAMPLE_FROM_1M:
            df_1m = self._qmt.get_minute_kline(code, data_type, '1m')
            if df_1m is not None and not df_1m.empty:
                result = dedupe_kline_df(self._resample_from_1m(df_1m, period))
                result.attrs['source'] = 'qmt_xtdata'
                result.attrs['fallback_chain'] = ['qmt_http', 'qmt_xtdata']
                return result
            return pd.DataFrame()

        df = self._qmt.get_minute_kline(code, data_type, period)
        result = dedupe_kline_df(df) if df is not None else pd.DataFrame()
        if not result.empty:
            result.attrs['source'] = 'qmt_xtdata'
            result.attrs['fallback_chain'] = ['qmt_http', 'qmt_xtdata']
        return result

    def _save_daily_source(self, code: str, df: pd.DataFrame):
        """Persist settled history, never the changing intraday bar."""
        if df is None or df.empty:
            return
        to_save = df.copy()
        if _is_a_share_live_session():
            today = datetime.date.today().strftime('%Y-%m-%d')
            dates = pd.to_datetime(to_save['date'], errors='coerce').dt.strftime('%Y-%m-%d')
            to_save = to_save[dates < today]
        if to_save.empty:
            return
        self._db.save_kline(code, 'daily', to_save)

    def _attach_intraday(self, data_type: str, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """Attach a transient 18080 snapshot for the frontend overlay only."""
        if data_type in ('stock', 'index') and df is not None and not df.empty:
            snapshot = _qmt_http_ohlc(code)
            if snapshot:
                df.attrs['intraday'] = snapshot
        return df

    def _attach_board_snapshot(self, data_type: str, code: str,
                               df: pd.DataFrame) -> pd.DataFrame:
        """把盘中板块快照临时合并为当日日 K，绝不写入结算数据库。"""
        if data_type not in ('industry', 'concept') or df is None or df.empty:
            return df
        try:
            from services.board_snapshot import get_snapshot_cache

            row = get_snapshot_cache().get_board_today(data_type, code)
            if not row:
                return df
            trade_date = str(row.get('trade_date') or '').strip()
            if len(trade_date) == 8 and trade_date.isdigit():
                trade_date = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
            if not trade_date:
                return df
            close = float(row.get('close') or 0)
            if close <= 0:
                return df

            attrs = dict(getattr(df, 'attrs', {}) or {})
            transient = pd.DataFrame([{
                'date': trade_date,
                'open': float(row.get('open') or close),
                'high': float(row.get('high') or close),
                'low': float(row.get('low') or close),
                'close': close,
                'volume': float(row.get('volume') or 0),
                'amount': float(row.get('amount') or 0),
            }])
            base = df.copy()
            dates = pd.to_datetime(base['date'], errors='coerce').dt.strftime('%Y-%m-%d')
            base = base[dates != trade_date]
            result = dedupe_kline_df(pd.concat([base, transient], ignore_index=True))
            result.attrs.update(attrs)
            result.attrs['board_snapshot_attached'] = True
            return result
        except Exception as e:
            logger.debug('[KLine] 板块盘中快照合并失败 %s:%s: %s', data_type, code, e)
            return df

    def _ensure_complete_history(
        self, data_type: str, code: str, df: pd.DataFrame, force: bool = False
    ) -> pd.DataFrame:
        """Merge QMT 18080 listing-to-date history in the background load.

        cache_first responses return SQLite before this method runs. The full
        source is therefore allowed to repair a truncated head or internal
        missing bars without delaying the first chart paint.
        """
        if data_type not in ('stock', 'index') or df is None or df.empty:
            return df
        if not force and not _should_refresh_history(code):
            return df
        _mark_history_refreshed(code)
        remote = _qmt_http_daily(code, count=12000)
        if remote is None or remote.empty:
            return df
        _mark_last_bar_refreshed(code)
        local_clean = dedupe_kline_df(df)
        remote_clean = dedupe_kline_df(remote)
        merged = dedupe_kline_df(
            pd.concat([local_clean, remote_clean], ignore_index=True)
        )
        local_dates = set(local_clean['date'].astype(str).str[:10])
        remote_dates = set(remote_clean['date'].astype(str).str[:10])
        repaired_dates = remote_dates - local_dates
        if repaired_dates or _overlapping_bars_differ(
            local_clean, remote_clean
        ):
            self._save_daily_source(code, merged)
            logger.info(
                '[HistoryRepair] %s source=%s local=%s merged=%s repaired=%s',
                code, 'qmt18080', len(local_clean), len(merged),
                len(repaired_dates),
            )
        return merged

    def _load_daily(self, data_type: str, code: str, board_name: str) -> pd.DataFrame:
        """Load daily bars, preserving the current source priority.

        Sets ``df.attrs['source']`` and ``df.attrs['fallback_chain']`` for
        observability — consumed by ``get_kline``.
        """
        if data_type in ('industry', 'concept'):
            return self._load_board_daily(data_type, code, board_name)

        fallback_chain = []

        # Every standard A-share index gets a lazy Tushare tail refresh. This
        # runs before SQLite/QMT so a complete but contaminated local cache
        # cannot become authoritative. Deprecated indices return an empty
        # chart instead of manufacturing a current bar from old SQLite.
        if data_type == 'index' and is_standard_a_share_index_code(code):
            fallback_chain.append('tushare_index_daily')
            if is_inactive_a_share_index(code):
                empty = pd.DataFrame()
                empty.attrs['source'] = 'unavailable'
                empty.attrs['fallback_chain'] = fallback_chain
                return empty
            df = load_a_share_index_kline(code, 'daily')
            if df is not None and not df.empty:
                result = dedupe_kline_df(df)
                result.attrs['source'] = 'tushare_index_daily'
                result.attrs['fallback_chain'] = fallback_chain
                return self._attach_intraday(data_type, code, result)

        # Overseas indices own an incremental HTTP refresh path. Reading
        # SQLite first would make any non-empty historical cache permanent.
        if data_type in GLOBAL_INDEX_TYPES or code in EASTMONEY_INDEX_CODES:
            fallback_chain.extend(['sqlite', 'eastmoney_history', 'eastmoney_spot'])
            df = load_global_index_kline(code, 'daily')
            if df is not None and not df.empty:
                result = dedupe_kline_df(df)
                result.attrs['source'] = 'global'
                result.attrs['fallback_chain'] = fallback_chain
                return result

        # 1. SQLite
        fallback_chain.append('sqlite')
        df = self._db.read_kline(code, 'daily')
        if df is not None and not df.empty:
            df = self._ensure_complete_history(data_type, code, df)
            df = self._ensure_latest_kline_bar(data_type, code, 'daily', df)
            result = dedupe_kline_df(df)
            result = self._attach_intraday(data_type, code, result)
            result.attrs['source'] = 'sqlite'
            result.attrs['fallback_chain'] = fallback_chain
            return result

        # 2. Global index (hk/us)
        if data_type in GLOBAL_INDEX_TYPES or code in EASTMONEY_INDEX_CODES:
            fallback_chain.append('global')
            df = load_global_index_kline(code, 'daily')
            if df is not None and not df.empty:
                result = dedupe_kline_df(df)
                result.attrs['source'] = 'global'
                result.attrs['fallback_chain'] = fallback_chain
                return result

        # 3. QMT HTTP
        fallback_chain.append('qmt_http')
        qmt_http_df = _qmt_http_daily(code, count=-1)
        if qmt_http_df is not None and not qmt_http_df.empty:
            self._save_daily_source(code, qmt_http_df)
            df = self._db.read_kline(code, 'daily')
            result = dedupe_kline_df(df if df is not None and not df.empty else qmt_http_df)
            result = self._attach_intraday(data_type, code, result)
            result.attrs['source'] = 'qmt_http'
            result.attrs['fallback_chain'] = fallback_chain
            return result

        # 4. QMT xtdata
        if is_qmt_available():
            fallback_chain.append('qmt_xtdata')
            qmt_code = self._qmt.to_qmt_code(code, data_type)
            qdf = self._qmt.get_daily(qmt_code, start='20200101', count=-1)
            if qdf is not None and not qdf.empty:
                self._save_daily_source(code, qdf)
                df = self._db.read_kline(code, 'daily')
                result = dedupe_kline_df(df if df is not None and not df.empty else qdf)
                result = self._attach_intraday(data_type, code, result)
                result.attrs['source'] = 'qmt_xtdata'
                result.attrs['fallback_chain'] = fallback_chain
                return result

        empty = pd.DataFrame()
        empty.attrs['source'] = 'load'
        empty.attrs['fallback_chain'] = fallback_chain
        return empty

    def _load_board_daily(self, data_type: str, code: str, board_name: str) -> pd.DataFrame:
        """Load board daily bars through the board updater, not stale SQLite only."""
        fallback_chain = []

        fallback_chain.append('board_loader')
        df = load_board_kline(data_type, board_name or code, code, 'daily')
        if df is not None and not df.empty:
            try:
                self._db.save_kline(code, 'daily', df)
            except Exception:
                pass
            result = dedupe_kline_df(df)
            result = self._attach_board_snapshot(data_type, code, result)
            result.attrs['source'] = 'board_loader'
            result.attrs['fallback_chain'] = fallback_chain
            return result

        fallback_chain.append('sqlite')
        df = self._db.read_kline(code, 'daily')
        if df is not None and not df.empty:
            result = dedupe_kline_df(df)
            result = self._attach_board_snapshot(data_type, code, result)
            result.attrs['source'] = 'sqlite'
            result.attrs['fallback_chain'] = fallback_chain
            return result

        empty = pd.DataFrame()
        empty.attrs['source'] = 'load'
        empty.attrs['fallback_chain'] = fallback_chain
        return empty

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
        """Resample 1m bars into A-share morning/afternoon session buckets."""
        if df_1m is None or df_1m.empty:
            return pd.DataFrame()

        try:
            df = df_1m.copy()
            # 解析日期为datetime索引
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')

            minutes = int(str(RESAMPLE_FROM_1M[period]).replace('min', ''))
            result_frames = []
            aggregation = {
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum',
            }
            if 'amount' in df.columns:
                aggregation['amount'] = 'sum'
            elif 'turnover' in df.columns:
                df['amount'] = pd.to_numeric(df['turnover'], errors='coerce').fillna(0)
                aggregation['amount'] = 'sum'
            for date, day_df in df.groupby(df.index.date):
                day_df = day_df.sort_index()
                day_start = pd.Timestamp(date)
                if minutes >= 240:
                    morning = day_df[
                        (day_df.index >= day_start + pd.Timedelta(minutes=570))
                        & (day_df.index < day_start + pd.Timedelta(minutes=690))
                    ]
                    afternoon = day_df[
                        (day_df.index >= day_start + pd.Timedelta(minutes=780))
                        & (day_df.index < day_start + pd.Timedelta(minutes=900))
                    ]
                    trading_day = pd.concat([morning, afternoon]).sort_index()
                    if not trading_day.empty:
                        full_day = pd.DataFrame([{
                            'open': trading_day['open'].iloc[0],
                            'high': trading_day['high'].max(),
                            'low': trading_day['low'].min(),
                            'close': trading_day['close'].iloc[-1],
                            'volume': trading_day['volume'].sum(),
                            **({'amount': trading_day['amount'].sum()} if 'amount' in trading_day.columns else {}),
                        }])
                        full_day.index = [day_start + pd.Timedelta(minutes=570)]
                        full_day.index.name = 'date'
                        result_frames.append(full_day.dropna(subset=['open']))
                    continue
                for start_minute, end_minute in ((570, 690), (780, 900)):
                    session_start = day_start + pd.Timedelta(minutes=start_minute)
                    session_end = day_start + pd.Timedelta(minutes=end_minute)
                    session = day_df[(day_df.index >= session_start) & (day_df.index < session_end)]
                    if session.empty:
                        continue
                    bucket = ((session.index - session_start).total_seconds() // (minutes * 60)).astype(int)
                    grouped = session.assign(_bucket=bucket).groupby('_bucket').agg(aggregation)
                    grouped.index = [
                        session_start + pd.Timedelta(minutes=int(item) * minutes)
                        for item in grouped.index
                    ]
                    grouped.index.name = 'date'
                    result_frames.append(grouped.dropna(subset=['open']))

            if not result_frames:
                return pd.DataFrame()

            result = pd.concat(result_frames)
            result = result.reset_index()
            result['date'] = pd.to_datetime(result['date']).dt.strftime('%Y-%m-%d %H:%M')
            return result

        except Exception as e:
            logger.error(f"[KLine] resample 1m→{period} 失败: {e}")
            return pd.DataFrame()

    def _load_resample(self, data_type: str, code: str, period: str, board_name: str) -> pd.DataFrame:
        """从日线重采样（统一读 SQLite 日线，避免 loader 旁路不一致）。"""
        from data_loader import _resample
        if (data_type in GLOBAL_INDEX_TYPES or code in EASTMONEY_INDEX_CODES
                or (data_type == 'index' and _is_a_share_index_code(code))):
            df = self._load_daily(data_type, code, board_name)
        else:
            df = self._db.read_kline(code, 'daily')
        if df is None or df.empty:
            if data_type not in ('industry', 'concept'):
                daily_df = self._do_load(
                    data_type, code, 'daily', board_name, f'{data_type}:{code}:daily'
                )
                df = daily_df if daily_df is not None else pd.DataFrame()
            if (df is None or df.empty) and data_type in ('industry', 'concept'):
                df = load_board_kline(data_type, board_name, code, 'daily')
        if df is None or df.empty:
            return pd.DataFrame()

        # 派生周期必须从当前日线重算；仅比较最后日期会复用过期的周/月线，
        # 因为日线在同一交易日内的 OHLC 仍可能发生变化。
        out = _resample(df, period)
        try:
            if out is not None and not out.empty:
                self._db.replace_kline_period(code, period, out)
        except Exception:
            pass
        return out if out is not None else pd.DataFrame()

    # ---- 格式化响应 ----

    def _format_response(self, df: pd.DataFrame):
        """
        DataFrame转换为前端期望的格式:
        - timestamp(ms), open, high, low, close, volume, amount/turnover
        - 日线用'%Y-%m-%d'，分钟线用'%Y-%m-%d %H:%M'
        - 使用共享函数 df_to_kline 统一处理
        """
        if df.empty:
            return [], ''
        records = df_to_kline(df)
        last_date = str(df['date'].max()) if 'date' in df.columns else ''
        return records, last_date

    def _response_intraday(self, data_type: str, code: str, period: str,
                           df: pd.DataFrame = None) -> Dict:
        """Return only a transient domestic daily snapshot for chart overlay."""
        if period != 'daily' or data_type not in ('stock', 'index'):
            return {}
        if data_type == 'index' and (code == '800000' or str(code).startswith('BK')):
            return {}
        attrs = getattr(df, 'attrs', {}) if df is not None else {}
        snapshot = attrs.get('intraday')
        if isinstance(snapshot, dict):
            return snapshot
        from services.nav_spot_service import _a_share_nav_phase
        if _a_share_nav_phase() not in ('live_morning', 'live_afternoon'):
            return {}
        return _qmt_http_ohlc(code)

    def _ok_response(self, data, cache_key, last_date='', source='load',
                      load_ms=0, fallback_chain=None, intraday=None,
                      stale=False, background_refresh_started=False):
        """Build a successful K-line response with stable observability fields.

        ``_ok_response`` is the common path for cache, synchronous, pending,
        and stale responses.  Keeping the defaults here prevents a newly
        added success path from silently dropping the metadata contract.
        """
        now = pd.Timestamp.now()
        if not last_date and data:
            try:
                last_date = pd.to_datetime(
                    data[-1].get('timestamp'), unit='ms'
                ).strftime('%Y-%m-%d')
            except Exception:
                last_date = ''
        # ``cache_stale`` is semantically stale even if an older caller did
        # not pass the explicit flag; never let that branch regress to false.
        stale = bool(stale) or source == 'cache_stale'
        response = {
            'data': data,
            'count': len(data),
            'last_date': last_date,
            'today': now.strftime('%Y-%m-%d'),
            'range': f'{data[0]["timestamp"]}~{data[-1]["timestamp"]}' if data else '',
            'cached': source == 'cache',
            'source': source or 'load',
            'stale': bool(stale),
            'background_refresh_started': bool(background_refresh_started),
            'load_ms': _nonnegative_load_ms(load_ms),
            'fallback_chain': _fallback_chain_list(fallback_chain),
        }
        if intraday:
            response['intraday'] = intraday
        return response

    def _ensure_latest_kline_bar(
        self,
        data_type: str,
        code: str,
        period: str,
        df: pd.DataFrame,
        now: Optional[datetime.datetime] = None,
    ) -> pd.DataFrame:
        """数据自动检验与降级补齐机制：
        若加载的数据最新日期早于目标交易日（欠更），仅在内存中合并历史补充。
        盘中 spot 由响应的 intraday 字段提供，绝不写入 SQLite。

        target_date 按交易所日历计算：A股走 a_share，HSI/HSTECH 走香港，
        海外指数走各自本地市场（US/日经等）。绝不把 A 股交易日当作港股或
        美股的目标日期。海外标的只允许对应 global history fetch 补齐，
        绝不调用 QMT（QMT 的代码空间不含海外标的，落库会污染缓存）。
        """
        if period != 'daily' or data_type not in (
            'stock', 'index', 'hk_index', 'us', 'global_index'
        ):
            return df

        market = _kline_market_key(data_type, code)
        target_date = _latest_expected_session_date(code, data_type, now=now)

        last_date = ''
        if df is not None and not df.empty and 'date' in df.columns:
            last_date = str(df['date'].max())[:10]
            if last_date >= target_date:
                # 盘中末 bar 校正只对 A 股安全：QMT 没有海外标的代码，
                # 海外标的的增量刷新由 global history loader 自身负责。
                if market == 'a_share' and _should_refresh_last_bar(code):
                    supp_df = _fetch_stock_supplement(code)
                    _mark_last_bar_refreshed(code)
                    if supp_df is not None and not supp_df.empty and _last_bar_differs(df, supp_df):
                        merged = dedupe_kline_df(pd.concat([df, supp_df], ignore_index=True))
                        # Persist corrected settled bars. During a live session
                        # _save_daily_source removes today's changing bar.
                        self._save_daily_source(code, merged)
                        return merged
                return df

        logger.info(f"[AutoCheck] 检测到 {code} 数据欠更 (最新={last_date or '空'}, 目标={target_date})，启动自动补全...")

        # 海外标的（港股/美股/亚太指数）只走对应 global history fetch。
        # load_global_index_kline 自身会增量刷新并持久化，绝不经由 QMT。
        if market != 'a_share':
            global_df = load_global_index_kline(code, 'daily')
            if global_df is not None and not global_df.empty:
                global_last = str(global_df['date'].max())[:10]
                if global_last >= target_date:
                    merged = dedupe_kline_df(global_df)
                    logger.info(
                        "[AutoCheck] global history 补全 %s 最新 %s",
                        code, global_last,
                    )
                    return merged
            return df

        # ---- A 股路径（保持原有行为）----
        # A-share indices are not all in PREWARM_TARGETS. Use the official
        # Eastmoney history tail before falling back to QMT so a stale SQLite
        # row cannot remain the only source for a selected index.
        if data_type == 'index' and _is_a_share_index_code(code):
            eastmoney_df = fetch_eastmoney_global_kline(code, limit=10)
            if eastmoney_df is not None and not eastmoney_df.empty:
                eastmoney_last = str(eastmoney_df['date'].max())[:10]
                if eastmoney_last >= target_date:
                    merged = dedupe_kline_df(
                        pd.concat([df, eastmoney_df], ignore_index=True)
                    ) if df is not None and not df.empty else eastmoney_df
                    self._save_daily_source(code, merged)
                    logger.info(
                        "[AutoCheck] Eastmoney 补全指数 %s 最新 %s",
                        code, eastmoney_last,
                    )
                    return merged

        # 1. 优先调用 QMT-HTTP-Server (/candles)
        supp_df = _qmt_http_daily(code, count=5)
        if supp_df is not None and not supp_df.empty:
            supp_last_date = str(supp_df['date'].max())[:10]
            if supp_last_date >= target_date:
                if df is not None and not df.empty:
                    df = dedupe_kline_df(pd.concat([df, supp_df], ignore_index=True))
                else:
                    df = supp_df
                self._save_daily_source(code, df)
                logger.info(f"[AutoCheck] QMT-HTTP 补全 {code} 最新 {supp_last_date} 数据")
                return df

        # Spot 只作为 _response_intraday 的临时覆盖，不在这里伪造日线并落库。
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
