"""
services/kline_service.py — K线数据业务逻辑
统一K线加载入口：分钟线(QMT子进程)/日线(QMT+缓存)/重采样(月/季/年线)
"""
import time
import atexit
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, List, Dict, Tuple

import pandas as pd

from data.sqlite_repo import get_sqlite_repo
from data.qmt_client import get_qmt_client
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


def normalize_period(period: str) -> str:
    """统一周期字符串，避免 1H/60m 分叉导致错误缓存或循环加载。"""
    if not period:
        return 'daily'
    p = str(period).strip()
    return PERIOD_ALIASES.get(p, p)


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
        seen_ts.add(ts)
        records.append({
            'timestamp': ts,
            'open': float(r['open']),
            'high': float(r['high']),
            'low': float(r['low']),
            'close': float(r['close']),
            'volume': int(float(r.get('volume', 0)))
        })
    return records


# ---- 线程池 ----

_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='kline_loader')
_pending = {}  # cache_key -> Event
_pending_lock = threading.Lock()

# 进程退出时优雅关闭线程池
atexit.register(lambda: _executor.shutdown(wait=False, cancel_futures=True))


class KLineService:
    """K线数据服务（线程安全）"""

    def __init__(self):
        self._cache = get_cache()
        self._db = get_sqlite_repo()
        self._qmt = get_qmt_client()

    # ---- 公开接口 ----

    def get_kline(self, data_type: str, code: str, period: str,
                  board_name: str = '', force: bool = False, timeout: float = 15):
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
            is_timeout = 'timeout' in str(e).lower() or isinstance(e, TimeoutError)
            with _pending_lock:
                _pending.pop(cache_key, None)
            return {
                'error': str(e), 'timeout': is_timeout,
                'data': [], 'count': 0
            }, 408 if is_timeout else 500

    # ---- 内部实现 ----

    def _do_load(self, data_type: str, code: str, period: str,
                 board_name: str, cache_key: str):
        """线程池中执行的实际数据加载"""
        try:
            period = normalize_period(period)
            minute_periods = MINUTE_PERIODS

            # 分钟级 → QMT（5m/15m/… 统一由 1m resample，避免与 1m 混淆）
            if period in minute_periods:
                if is_qmt_available():
                    if period in RESAMPLE_FROM_1M:
                        df_1m = self._qmt.get_minute_kline(code, data_type, '1m')
                        if (df_1m is None or df_1m.empty) and data_type in (
                            'index', 'stock', 'hk_index'
                        ):
                            df_1m = self._qmt.get_minute_kline(code, 'hk_index', '1m')
                        if df_1m is not None and not df_1m.empty:
                            return dedupe_kline_df(self._resample_from_1m(df_1m, period))
                        return pd.DataFrame()
                    # 仅 1m 直取
                    df = self._qmt.get_minute_kline(code, data_type, period)
                    if (df is None or df.empty) and data_type in (
                        'index', 'stock', 'hk_index'
                    ):
                        df = self._qmt.get_minute_kline(code, 'hk_index', period)
                    return dedupe_kline_df(df) if df is not None else pd.DataFrame()

            # 板块日线：必须走 load_board_kline（SQLite + 增量），不可因本地有旧数据就永久跳过更新
            # 否则列表实时涨跌幅与图表末 bar 脱节（如半导体 -9.87% 有而图停在旧日期）
            if data_type in ('industry', 'concept') and period == 'daily':
                from data_loader import load_board_kline
                df = load_board_kline(data_type, board_name or code, code, period)
                if df is not None and not df.empty:
                    try:
                        self._db.save_kline(code, 'daily', df)
                    except Exception:
                        pass
                    return dedupe_kline_df(df)
                # 仍空则读库兜底
                df = self._db.read_kline(code, period)
                return dedupe_kline_df(df) if df is not None else pd.DataFrame()

            # 日线 → 先 SQLite（快、完整）；QMT 仅在本地空或 force 缺口时补写
            # 策略：指数/个股仅 QMT 写库；读路径不覆盖已有更新的历史
            if period == 'daily':
                df = self._db.read_kline(code, period)
                if df is not None and not df.empty:
                    return dedupe_kline_df(df)
                # 本地无日线：QMT 拉全量写库（板块不走 QMT）
                if is_qmt_available() and data_type not in ('industry', 'concept'):
                    qmt_code = self._qmt.to_qmt_code(code, data_type)
                    qdf = self._qmt.get_daily(qmt_code, start='20200101', count=-1)
                    if qdf is not None and not qdf.empty:
                        self._db.save_kline(code, 'daily', qdf)
                        df = self._db.read_kline(code, period)
                        if df is not None and not df.empty:
                            return dedupe_kline_df(df)
                return pd.DataFrame()

            # 周/月/季/年线 → 从日线重采样（禁止 fallthrough 到错误 loader）
            if period in RESAMPLE_PERIODS:
                return dedupe_kline_df(
                    self._load_resample(data_type, code, period, board_name)
                )

            # 板块非日线（若有直出周期）→ SQLite 或 load_board_kline
            if data_type in ('industry', 'concept'):
                df = self._db.read_kline(code, period)
                if df is not None and not df.empty:
                    return dedupe_kline_df(df)
                from data_loader import load_board_kline
                df = load_board_kline(data_type, board_name, code, period)
                if df is not None and not df.empty:
                    return dedupe_kline_df(df)
                return pd.DataFrame()

            # 其他（hk_index 等）— 仅在未命中上述分支时
            if data_type == 'hk_index' and period == '1m' and is_qmt_available():
                df = self._qmt.get_minute_kline(code, 'hk_index', '1m')
                if df is not None and not df.empty:
                    return dedupe_kline_df(df)

            return pd.DataFrame()

        except Exception as e:
            logger.error(f"[KLine] 加载失败 {data_type}:{code} {period}: {e}")
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
                from data_loader import load_board_kline
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
        return {
            'data': data,
            'count': len(data),
            'last_date': last_date,
            'today': now.strftime('%Y-%m-%d'),
            'range': f'{data[0]["timestamp"]}~{data[-1]["timestamp"]}' if data else '',
            'cached': source == 'cache',
            'source': source
        }


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
