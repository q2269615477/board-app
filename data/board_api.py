"""
board_api.py — 板块数据 Tushare 客户端（完全洗除东财HTTP API直连）
数据源：tushare dc_index / dc_daily / dc_member
下游需与 _normalize_df() 兼容（输出中文列名如 '日期','开盘','收盘','最高','最低','成交量','成交额'）
"""
import time
import logging
import threading
from typing import Optional
from datetime import datetime
import pandas as pd
import tushare as ts

logger = logging.getLogger('board_api')

# ===== 线程安全限流器（令牌桶） =====
_rate_lock = threading.Lock()
_last_call_time = 0.0
_MIN_INTERVAL = 0.35  # 最小调用间隔（秒），约 170次/分钟，低于 Tushare 200次/分钟限制

def _rate_limit():
    """线程安全的 Tushare API 限流"""
    global _last_call_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_call_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call_time = time.time()


def _ensure_direct():
    """板块/Tushare 请求前清代理（防 127.0.0.1:7688 超时）。"""
    try:
        from core.env_bootstrap import force_direct_network
        force_direct_network()
    except Exception:
        import os
        for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY'):
            os.environ.pop(k, None)
        os.environ['NO_PROXY'] = '*'
        os.environ['no_proxy'] = '*'

# ===== Tushare 初始化（复用 data_loader 的全局配置） =====
_pro = None
try:
    from data_loader import _tushare_pro as _pro
except Exception:
    try:
        import os
        _TOKEN = os.environ.get('TUSHARE_TOKEN', '')
        if not _TOKEN:
            logging.warning('[board_api] TUSHARE_TOKEN环境变量未设置，Tushare板块数据将不可用')
        else:
            try:
                ts.set_token(_TOKEN)
                _pro = ts.pro_api()
            except PermissionError:
                logging.warning('[board_api] Tushare token写入被沙箱拦截')
                _pro = None
            except Exception as e:
                logging.warning(f'[board_api] Tushare初始化失败: {e}')
                _pro = None
    except Exception as e:
        logging.warning(f'[board_api] Tushare初始化失败: {e}')
        _pro = None


def _today() -> str:
    return datetime.now().strftime('%Y%m%d')


def _date_fmt(d: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    if not d or len(d) < 8:
        return d
    return f'{d[:4]}-{d[4:6]}-{d[6:]}'


# ===== 板块列表 =====

def get_industry_boards() -> Optional[pd.DataFrame]:
    """获取行业板块列表（Tushare dc_index）"""
    if _pro is None:
        logger.warning('[Tushare] _pro 未初始化，无法获取行业板块列表')
        return None
    try:
        _rate_limit()
        df = _pro.dc_index(idx_type='行业板块')
        if df is None or df.empty:
            return None
        records = []
        for _, row in df.iterrows():
            code = str(row.get('ts_code', '')).replace('.DC', '')
            if not code:
                continue
            records.append({
                '板块代码': code,
                '板块名称': str(row.get('name', '')),
                '涨跌幅': float(row.get('pct_change', 0) or 0),
            })
        return pd.DataFrame(records) if records else None
    except Exception as e:
        logger.warning(f"[Tushare] get_industry_boards 失败: {e}")
        return None


def get_concept_boards() -> Optional[pd.DataFrame]:
    """获取概念板块列表（Tushare dc_index）"""
    if _pro is None:
        logger.warning('[Tushare] _pro 未初始化，无法获取概念板块列表')
        return None
    try:
        _rate_limit()
        df = _pro.dc_index(idx_type='概念板块')
        if df is None or df.empty:
            return None
        records = []
        for _, row in df.iterrows():
            code = str(row.get('ts_code', '')).replace('.DC', '')
            if not code:
                continue
            records.append({
                '板块代码': code,
                '板块名称': str(row.get('name', '')),
                '涨跌幅': float(row.get('pct_change', 0) or 0),
            })
        return pd.DataFrame(records) if records else None
    except Exception as e:
        logger.warning(f"[Tushare] get_concept_boards 失败: {e}")
        return None


# ===== 板块成分股 =====

import re

def _clean_stock_name(name: str) -> str:
    """清洗股票名称：去掉除权除息临时前缀 XD/DR（保留 ST/*ST 等风险标记）"""
    if not name:
        return name
    # XD=除息, DR=除权除息（含 XR 但东方财富通常不用 XR）
    return re.sub(r'^(XD|DR)', '', name)


def _get_constituents(sector_code: str) -> list:
    """通用成分股获取（Tushare dc_member）"""
    if _pro is None:
        logger.warning('[Tushare] _pro 未初始化，无法获取成分股')
        return []
    try:
        _rate_limit()
        df = _pro.dc_member(ts_code=f'{sector_code}.DC')
        if df is None or df.empty:
            return []
        # 仅取最新交易日
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        records = []
        for _, row in df.iterrows():
            code_with_suffix = str(row.get('con_code', ''))
            if not code_with_suffix:
                continue
            # 去后缀: 601963.SH → 601963
            code = code_with_suffix.split('.')[0] if '.' in code_with_suffix else code_with_suffix
            records.append({
                'code': code,
                'name': _clean_stock_name(str(row.get('name', ''))),
                'close': '-',
                'change_pct': 0,
                'pre_close': '-',
                'volume': 0,
            })
        return records
    except Exception as e:
        logger.warning(f"[Tushare] _get_constituents {sector_code} 失败: {e}")
        return []


def get_industry_constituents(sector_code: str) -> list:
    """获取行业板块成分股"""
    return _get_constituents(sector_code)


def get_concept_constituents(sector_code: str) -> list:
    """获取概念板块成分股"""
    return _get_constituents(sector_code)


# ===== 板块实时行情（从 dc_index 最新数据获取） =====

def _get_spot(board_type: str) -> Optional[dict]:
    """通用板块实时行情"""
    if _pro is None:
        logger.warning('[Tushare] _pro 未初始化，无法获取板块行情')
        return None
    label = '行业板块' if board_type == 'industry' else '概念板块'
    try:
        _rate_limit()
        df = _pro.dc_index(idx_type=label)
        if df is None or df.empty:
            return None
        result = {}
        for _, row in df.iterrows():
            code = str(row.get('ts_code', '')).replace('.DC', '')
            if not code:
                continue
            result[code] = {
                '名称': str(row.get('name', '')),
                '涨跌幅': float(row.get('pct_change', 0) or 0),
                '最新价': float(row.get('close', 0) or 0),
            }
        return result
    except Exception as e:
        logger.warning(f"[Tushare] _get_spot({board_type}) 失败: {e}")
        return None


def get_industry_spot() -> Optional[dict]:
    """获取行业板块实时行情"""
    return _get_spot('industry')


def get_concept_spot() -> Optional[dict]:
    """获取概念板块实时行情"""
    return _get_spot('concept')


# ===== 板块K线数据 =====

def _get_board_kline_eastmoney(code: str, start_date: str = '20000101') -> Optional[pd.DataFrame]:
    """
    东财 push2his 板块日K（secid=90.BKxxxx）。
    Tushare 不可用时的兜底，保证图表与列表涨跌幅同源东财。
    """
    import requests

    _ensure_direct()
    code = str(code or '').strip().upper()
    if not code.startswith('BK'):
        return None
    beg = start_date.replace('-', '') if start_date else '20000101'
    if len(beg) >= 8:
        beg = beg[:8]
    else:
        beg = '20000101'
    try:
        r = requests.get(
            'https://push2his.eastmoney.com/api/qt/stock/kline/get',
            params={
                'secid': f'90.{code}',
                'fields1': 'f1,f2,f3,f4,f5,f6',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
                'klt': '101',  # 日K
                'fqt': '1',
                'beg': beg,
                'end': '20500101',
                'lmt': 100000,
            },
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://quote.eastmoney.com/',
            },
            timeout=20,
        )
        data = (r.json() or {}).get('data') or {}
        klines = data.get('klines') or []
        if not klines:
            logger.warning(f'[EastMoney] {code} kline 无数据')
            return None
        records = []
        for line in klines:
            # date,open,close,high,low,volume,amount,amplitude,pct,change,turnover
            parts = str(line).split(',')
            if len(parts) < 6:
                continue
            d = parts[0].strip()
            if len(d) == 8 and d.isdigit():
                d = _date_fmt(d)
            if not d or len(d) < 10:
                continue
            try:
                records.append({
                    '日期': d[:10],
                    '开盘': float(parts[1] or 0),
                    '收盘': float(parts[2] or 0),
                    '最高': float(parts[3] or 0),
                    '最低': float(parts[4] or 0),
                    '成交量': float(parts[5] or 0),
                    '成交额': float(parts[6] or 0) if len(parts) > 6 else 0.0,
                })
            except (TypeError, ValueError):
                continue
        if not records:
            return None
        logger.info(f'[EastMoney] {code} kline {len(records)} 根, last={records[-1]["日期"]}')
        return pd.DataFrame(records)
    except Exception as e:
        logger.warning(f'[EastMoney] get_board_kline({code}) 失败: {e}')
        return None


def get_board_kline(board_type: str, code: str, start_date: str = '20000101') -> Optional[pd.DataFrame]:
    """
    获取板块日K线：优先 Tushare dc_daily，失败则东财 push2his。
    返回中文列名以兼容 _normalize_df()：['日期','开盘','收盘','最高','最低','成交量','成交额']
    """
    global _pro
    _ensure_direct()
    if _pro is None:
        # 延迟再试一次（token 可能刚 bootstrap）
        try:
            from core.env_bootstrap import ensure_tushare_token
            ensure_tushare_token()
            from data_loader import _tushare_pro
            if _tushare_pro is not None:
                _pro = _tushare_pro
        except Exception:
            pass
    if _pro is not None:
        try:
            _rate_limit()

            end = _today()
            df = _pro.dc_daily(
                ts_code=f'{code}.DC',
                start_date=start_date.replace('-', '') if '-' in start_date else start_date,
                end_date=end,
            )
            if df is not None and not df.empty:
                records = []
                for _, row in df.iterrows():
                    d = _date_fmt(str(row.get('trade_date', '')))
                    if not d or len(d) < 10:
                        continue
                    records.append({
                        '日期': d,
                        '开盘': float(row.get('open', 0) or 0),
                        '收盘': float(row.get('close', 0) or 0),
                        '最高': float(row.get('high', 0) or 0),
                        '最低': float(row.get('low', 0) or 0),
                        '成交量': float(row.get('vol', 0) or 0),
                        '成交额': float(row.get('amount', 0) or 0),
                    })
                if records:
                    return pd.DataFrame(records)
            logger.debug(f"[Tushare] {code} dc_daily 无数据，尝试东财")
        except Exception as e:
            logger.warning(f"[Tushare] get_board_kline({code}) 失败: {e}，尝试东财")

    return _get_board_kline_eastmoney(code, start_date=start_date)


# ===== 交易日历 =====

def get_trade_dates() -> set:
    """获取A股交易日历（Tushare trade_cal）"""
    if _pro is None:
        logger.warning('[Tushare] _pro 未初始化，无法获取交易日历')
        return set()
    try:
        _rate_limit()
        df = _pro.trade_cal(exchange='SSE',
                             start_date='20000101',
                             end_date=_today(),
                             is_open='1')
        if df is None or df.empty:
            return set()
        return set(_date_fmt(d) for d in df['cal_date'].values if d)
    except Exception as e:
        logger.warning(f"[Tushare] get_trade_dates 失败: {e}")
        return set()


# ===== 午休缓存感知的数据获取 =====


def get_board_data_with_fallback(board_code: str) -> Optional[dict]:
    """
    获取板块数据（支持午休缓存）
    
    逻辑：
    1. 13:00 前且午休缓存有效 → 使用缓存
    2. 其他时间 → 实时获取（Tushare）
    3. 失败 → 本地缓存兜底
    
    Args:
        board_code: 板块代码（如 BK1499）
    
    Returns:
        Dict: 板块数据，失败返回 None
    """
    from datetime import datetime
    from services.noon_cache_manager import noon_cache_manager
    """
    获取板块数据（支持午休缓存）
    
    逻辑：
    1. 13:00 前且午休缓存有效 → 使用缓存
    2. 其他时间 → 实时获取（Tushare）
    3. 失败 → 本地缓存兜底
    
    Args:
        board_code: 板块代码（如 BK1499）
    
    Returns:
        Dict: 板块数据，失败返回 None
    """
    from datetime import datetime
    
    now = datetime.now()
    is_afternoon = now.hour >= 13
    
    # 1. 检查是否使用午休缓存（13:00 前且缓存有效）
    if not is_afternoon and noon_cache_manager.is_noon_cache_valid():
        noon_data = noon_cache_manager.load_noon_data()
        if board_code in noon_data:
            logger.debug(f"[板块数据] 使用午休缓存: {board_code}")
            return noon_data[board_code]
    
    # 2. 实时获取（Tushare）
    try:
        # 东财板块走 Tushare
        df = get_board_kline('concept', board_code)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                'price': float(latest.get('收盘', 0)),
                'change_pct': float(latest.get('涨跌幅', 0)) if '涨跌幅' in latest else 0,
                'volume': int(latest.get('成交量', 0)),
                'source': 'tushare_realtime'
            }
    except Exception as e:
        logger.warning(f"[板块数据] Tushare获取失败: {e}")
    
    # 3. 本地缓存兜底
    try:
        from data.sqlite_repo import get_cached_prices
        cached = get_cached_prices([board_code])
        if cached.get(board_code):
            data = cached[board_code]
            data['source'] = 'local_cache'
            return data
    except Exception as e:
        logger.error(f"[板块数据] 本地缓存兜底失败: {e}")
    
    return None


def get_index_data_with_fallback(index_code: str) -> Optional[dict]:
    """
    获取指数数据（支持午休缓存）
    
    逻辑：
    1. 13:00 前且午休缓存有效 → 使用缓存
    2. 其他时间 → 实时获取（QMT）
    3. 失败 → 本地缓存兜底
    
    Args:
        index_code: 指数代码（如 sh000001）
    
    Returns:
        Dict: 指数数据，失败返回 None
    """
    from datetime import datetime
    
    now = datetime.now()
    is_afternoon = now.hour >= 13
    
    # 1. 检查是否使用午休缓存（13:00 前且缓存有效）
    if not is_afternoon and noon_cache_manager.is_noon_cache_valid():
        noon_data = noon_cache_manager.load_noon_data()
        if index_code in noon_data:
            logger.debug(f"[指数数据] 使用午休缓存: {index_code}")
            return noon_data[index_code]
    
    # 2. 实时获取（QMT）
    try:
        from data.qmt_client import get_qmt_client
        client = get_qmt_client()
        data = client.get_constituents_batch([index_code])
        if data and index_code in data:
            result = data[index_code]
            result['source'] = 'qmt_realtime'
            return result
    except Exception as e:
        logger.warning(f"[指数数据] QMT获取失败: {e}")
    
    # 3. 本地缓存兜底
    try:
        from data.sqlite_repo import get_cached_prices
        cached = get_cached_prices([index_code])
        if cached.get(index_code):
            data = cached[index_code]
            data['source'] = 'local_cache'
            return data
    except Exception as e:
        logger.error(f"[指数数据] 本地缓存兜底失败: {e}")
    
    return None


# ===== 最近交易日 =====

_last_trade_date_cache = None
_last_trade_date_cached_at = 0.0
_TRADE_DATE_CACHE_TTL = 3600  # seconds


def get_last_trading_date(ref_date: str = None) -> str:
    """Return the most recent trading day at or before ref_date (default: today).

    Uses Tushare trade_cal when available; falls back to simple weekday logic.
    Result is cached for _TRADE_DATE_CACHE_TTL seconds.
    """
    import time
    global _last_trade_date_cache, _last_trade_date_cached_at

    now = time.time()
    if _last_trade_date_cache is not None and (now - _last_trade_date_cached_at) < _TRADE_DATE_CACHE_TTL:
        cached = _last_trade_date_cache
        if ref_date is None or cached <= ref_date:
            return cached

    # Determine reference date
    if ref_date:
        ref = ref_date.strip()
    else:
        ref = _today()

    # Try trade calendar
    cal = get_trade_dates()
    if cal:
        # Find the latest date in cal that is <= ref
        candidates = sorted(d for d in cal if d <= ref)
        if candidates:
            result = candidates[-1]
            _last_trade_date_cache = result
            _last_trade_date_cached_at = now
            return result

    # Fall back: skip weekends
    from datetime import datetime as dt, timedelta
    try:
        d = dt.strptime(ref, '%Y-%m-%d')
    except Exception:
        return ref

    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    result = d.strftime('%Y-%m-%d')
    _last_trade_date_cache = result
    _last_trade_date_cached_at = now
    return result
