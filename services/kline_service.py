"""
services/kline_service.py — K线数据业务逻辑
统一K线加载入口：分钟线(QMT子进程)/日线(QMT+缓存)/重采样(月/季/年线)
"""
import time
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
# 需resample的目标周期及对应分钟数
RESAMPLE_FROM_1M = {'15m': '15min', '30m': '30min', '60m': '60min', '120m': '120min', '240m': '240min'}

logger = logging.getLogger('kline_service')


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

    records = []
    for _, r in df.iterrows():
        date_str = str(r['date'])
        if len(date_str) > 10 and ' ' in date_str:
            ts = int(pd.Timestamp(date_str).timestamp() * 1000)
        else:
            ts = int(pd.Timestamp(date_str).timestamp() * 1000)
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
            minute_periods = MINUTE_PERIODS

            # 分钟级 → QMT
            if period in minute_periods:
                if is_qmt_available():
                    # 15m/30m/60m/120m/240m需要先获取1m再resample
                    if period in RESAMPLE_FROM_1M:
                        df_1m = self._qmt.get_minute_kline(code, data_type, '1m')
                        if df_1m is not None and not df_1m.empty:
                            return self._resample_from_1m(df_1m, period)
                    else:
                        df = self._qmt.get_minute_kline(code, data_type, period)
                        if df is None or df.empty and data_type in ('index', 'stock', 'hk_index'):
                            df = self._qmt.get_minute_kline(code, 'hk_index', period)
                        return df

            # 日线 → QMT优先+SQLite回退（行业/概念板块不经过QMT，直接用本地数据）
            if period == 'daily':
                if is_qmt_available() and data_type not in ('industry', 'concept'):
                    qmt_code = self._qmt.to_qmt_code(code, data_type)
                    df = self._qmt.get_daily_local(qmt_code)
                    if df is not None and not df.empty:
                        self._db.save_kline(code, 'daily', df)
                        # 从SQLite读取完整数据（含QMT最新数据 + 备份恢复的历史数据）
                        df = self._db.read_kline(code, period)
                        if df is not None and not df.empty:
                            return df
                # 回退SQLite
                df = self._db.read_kline(code, period)
                if df is not None and not df.empty:
                    return df

            # 周/月/季/年线 → 从日线重采样
            if period in RESAMPLE_PERIODS:
                return self._load_resample(data_type, code, period, board_name)

            # 板块数据（行业/概念）→ SQLite或磁盘CSV/tushare
            if data_type in ('industry', 'concept'):
                df = self._db.read_kline(code, period)
                if df is not None and not df.empty:
                    return df
                # SQLite 没有则尝试 CSV/tushare 增量拉取
                from data_loader import load_board_kline
                df = load_board_kline(data_type, board_name, code, period)
                if df is not None and not df.empty:
                    return df
                return pd.DataFrame()

            # 其他（hk_index, us, etc）
            if data_type == 'hk_index':
                if is_qmt_available():
                    df = self._qmt.get_minute_kline(code, 'hk_index', period)
                    if df is not None and not df.empty:
                        return df

            # 兜底：QMT分钟线
            if is_qmt_available():
                return self._qmt.get_minute_kline(code, data_type, period)

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
        """从日线重采样"""
        from data_loader import load_stock_data, load_index_kline, load_hk_index_kline, _resample
        df = None
        if data_type == 'stock':
            df = load_stock_data(code)
        elif data_type == 'index':
            df = load_index_kline(code)
        elif data_type == 'hk_index':
            df = load_hk_index_kline(code)
        elif data_type in ('industry', 'concept'):
            df = self._db.read_kline(code, 'daily')

        if df is not None and not df.empty:
            return _resample(df, period)
        return pd.DataFrame()

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
