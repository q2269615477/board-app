"""
data_loader.py - 统一数据加载层
v4.2：SQLite 主存储 + Tushare 板块 / QMT 指数个股 双源
"""
import sys, io
if 'pytest' not in sys.modules:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass  # 在测试环境或特殊环境中跳过

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
    # 安全：优先从环境变量读取token，不再硬编码
    TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '')
    if not TUSHARE_TOKEN:
        logging.warning('[Tushare] TUSHARE_TOKEN环境变量未设置，Tushare板块数据将不可用。请设置环境变量: set TUSHARE_TOKEN=<your_token>')
    else:
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

_conn_local = threading.local()
def _get_db() -> sqlite3.Connection:
    """获取线程安全的数据库连接（使用 threading.local）"""
    if not hasattr(_conn_local, 'conn'):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _conn_local.conn = conn
    return _conn_local.conn


def _close_db_connections():
    """关闭当前线程缓存的数据库连接"""
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
    """日线 → 周/月/季/年。

    关键：周期右端标签（W-FRI / ME / QE / YE）不可超过源数据最后交易日，
    否则未完结周期会被标成「未来日期」（如 7/17 数据画出 7/31 月线），
    前端 Pro 易出现循环/错位。
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    # 日线去重后再聚合，避免重复 date 污染周/月线
    df = df.sort_values('date').drop_duplicates(subset=['date'], keep='last')
    df = df.set_index('date')
    if df.empty:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    last_obs = df.index.max()
    rule = {
        'weekly': 'W-FRI',
        'monthly': 'ME',
        'quarterly': 'QE',
        'yearly': 'YE',
    }.get(str(freq).lower(), freq)
    # 兼容旧 pandas 'MS' 语义：月末聚合更符合 K 线习惯
    if str(freq).lower() == 'monthly' and rule == 'MS':
        rule = 'ME'
    resampled = df.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna(subset=['open']).reset_index()
    # 未完结周期：标签钳到源序列最后交易日，并去重
    if not resampled.empty:
        mask_future = resampled['date'] > last_obs
        if mask_future.any():
            resampled.loc[mask_future, 'date'] = last_obs
        resampled = resampled.drop_duplicates(subset=['date'], keep='last')
        resampled = resampled.sort_values('date').reset_index(drop=True)
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
    """加载板块K线，SQLite优先，Tushare/东财增量补充"""
    # 先读本地
    df = _db_read_kline(code, period if period == 'daily' else 'daily')
    last_local = str(df['date'].max())[:10] if not df.empty else ''
    today = _today_str()

    # 如果本地已含最新数据（自然日）
    if last_local and last_local.replace('/', '-') >= today:
        if period in ('weekly', 'monthly', 'quarterly', 'yearly'):
            return _resample(df, period) if not df.empty else df
        return df

    # 周末：仅当本地已很新（落后 ≤3 自然日）才跳过；否则仍增量（防图长期停更）
    wkday = datetime.now().weekday()
    if wkday >= 5 and not df.empty and last_local:
        try:
            lag = (datetime.now().date() - pd.to_datetime(last_local).date()).days
            if lag <= 3:
                if period in ('weekly', 'monthly', 'quarterly', 'yearly'):
                    return _resample(df, period)
                return df
        except Exception:
            pass

    # 尝试增量更新（Tushare dc_daily → 东财兜底）
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
        print(f"[回退] {name} 板块K线更新失败({e}), 使用本地缓存", flush=True)

    # 更新失败：回退本地（高周期可重采样）
    if period in ('weekly', 'monthly', 'quarterly', 'yearly') and not df.empty:
        return _resample(df, period)
    return df



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


# ===== 实时行情（qmt_client 子进程模式 + SQLite 回退） =====

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

def _qmt_live_spot(code: str) -> dict:
    """通过 qmt_client 子进程获取实时行情，映射到统一格式"""
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
    """指数实时行情 - qmt_client子进程优先 → SQLite回退"""
    result = _qmt_live_spot(code)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)

def get_spot_stock(code: str) -> dict:
    """个股实时行情 - qmt_client子进程优先 → SQLite回退"""
    result = _qmt_live_spot(code)
    if result and result.get('price', 0) > 0:
        return result
    return _sqlite_spot(code)


# ===== 全球指数行情（腾讯财经API） =====

def get_global_index_spot(code: str) -> dict:
    """
    获取全球指数实时行情 - 使用腾讯财经API
    支持：港股(HSI/HSTECH)、美股(SPX/IXIC/DJI)、亚太(^N225/^KS11/^TWII)
    """
    import requests
    import json
    
    # 代码映射：前端代码 -> 腾讯代码
    tencent_code_map = {
        'HSI': 'hkHSI',      # 恒生指数
        'HSTECH': 'hkHSTECH', # 恒生科技
        'SPX': 'usSPX',      # 标普500
        'IXIC': 'usIXIC',    # 纳斯达克
        'DJI': 'usDJI',      # 道琼斯
        '^N225': 'jpN225',   # 日经225
        '^KS11': 'krKS11',   # KOSPI
        '^TWII': 'twTWII',   # 台湾加权
    }
    
    tencent_code = tencent_code_map.get(code)
    if not tencent_code:
        return {}
    
    try:
        url = f'https://qt.gtimg.cn/q={tencent_code}'
        response = requests.get(url, timeout=5)
        response.encoding = 'gbk'
        
        # 解析腾讯返回的数据格式
        # 格式: v_hkHSI="1;名称;当前价;...;涨跌幅;..."
        text = response.text
        if not text or 'v_' not in text:
            return {}
        
        # 提取数据部分
        start = text.find('"') + 1
        end = text.rfind('"')
        if start <= 0 or end <= start:
            return {}
        
        data_str = text[start:end]
        parts = data_str.split('~')
        
        if len(parts) < 35:
            return {}
        
        # 腾讯字段索引: 1=名称, 2=当前价, 3=昨收, 4=今开, 5=最高, 6=最低, 32=涨跌幅
        price = float(parts[2]) if parts[2] else 0
        change_pct = float(parts[32]) if parts[32] else 0
        
        return {
            'price': price,
            'change_pct': change_pct,
            'name': parts[1],
        }
    except Exception as e:
        logging.warning(f'[全球指数] {code} 获取失败: {e}')
        return {}


# ===== 个股列表（QMT） =====

def get_all_stocks() -> list:
    """通过 qmt_client 子进程获取全市场个股列表"""
    try:
        from data.qmt_client import get_qmt_client
        from core.lifecycle import is_qmt_available
        if not is_qmt_available():
            return []
        client = get_qmt_client()
        stocks = client.get_stock_list()
        return [{'code': s['code'], 'name': s['name']} for s in stocks if s.get('name')]
    except Exception as e:
        print(f"[QMT] 获取股票列表失败: {e}")
        return []

def search_stock(keyword: str) -> list:
    """个股搜索 - 已废弃，使用 /api/search 替代"""
    return []
