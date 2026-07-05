"""
board_api.py — 板块数据 Tushare 客户端（完全洗除东财HTTP API直连）
数据源：tushare dc_index / dc_daily / dc_member
下游需与 _normalize_df() 兼容（输出中文列名如 '日期','开盘','收盘','最高','最低','成交量','成交额'）
"""
import time
import logging
from typing import Optional
from datetime import datetime
import pandas as pd
import tushare as ts

logger = logging.getLogger('board_api')

# ===== Tushare 初始化（复用 data_loader 的全局配置） =====
_pro = None
try:
    from data_loader import _tushare_pro as _pro
except Exception:
    try:
        import os
        _TOKEN = os.environ.get('TUSHARE_TOKEN', 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590')
        try:
            ts.set_token(_TOKEN)
            _pro = ts.pro_api()
        except PermissionError:
            logging.warning('[board_api] Tushare token写入被沙箱拦截')
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
    try:
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
    try:
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
    try:
        time.sleep(0.35)  # Tushare限流
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
    label = '行业板块' if board_type == 'industry' else '概念板块'
    try:
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

def get_board_kline(board_type: str, code: str, start_date: str = '20000101') -> Optional[pd.DataFrame]:
    """
    获取板块日K线（Tushare dc_daily）
    返回中文列名以兼容 _normalize_df()：['日期','开盘','收盘','最高','最低','成交量','成交额']
    """
    try:
        # 限流间隔
        time.sleep(0.35)

        end = _today()
        df = _pro.dc_daily(ts_code=f'{code}.DC',
                            start_date=start_date.replace('-', '') if '-' in start_date else start_date,
                            end_date=end)
        if df is None or df.empty:
            logger.debug(f"[Tushare] {code} dc_daily 无数据")
            return None

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
        if not records:
            return None
        return pd.DataFrame(records)
    except Exception as e:
        logger.warning(f"[Tushare] get_board_kline({code}) 失败: {e}")
        return None


# ===== 交易日历 =====

def get_trade_dates() -> set:
    """获取A股交易日历（Tushare trade_cal）"""
    try:
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
