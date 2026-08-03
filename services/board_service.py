"""
services/board_service.py — 板块数据业务逻辑（Tushare + QMT）
处理板块列表、成分股、涨跌幅、板块K线
- 板块列表/成分股：通过 data.board_api 直接请求东财 HTTP API
- 板块K线：通过 data.board_api.get_board_kline() 请求东财历史行情 API
"""
import os
import json
import logging
import sqlite3
import math
import threading
from pathlib import Path
from typing import Optional, Any

from data.board_api import (
    get_industry_boards, get_concept_boards,
    get_industry_constituents, get_concept_constituents,
    get_eastmoney_constituents,
    get_industry_spot, get_concept_spot,
    get_board_kline,
)
from data.sqlite_repo import get_sqlite_repo
from core.cache import get_cache
from services.index_constituent_service import IndexConstituentService

logger = logging.getLogger('board_service')

_constituents_json_cache = {}
_constituents_json_mtime = {}
_constituents_json_lock = threading.RLock()
_constituents_remote_cache = {}


def _clean_json_value(v: Any) -> Any:
    """递归清理 JSON 不兼容值：NaN/Infinity → None/0"""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return v
    if isinstance(v, dict):
        return {k: _clean_json_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_json_value(x) for x in v]
    return v


def reload_constituents_json_cache() -> dict:
    """Load board -> constituents JSON maps into process memory.

    The data update design requires left-nav constituents to read these local
    JSON maps from memory.  The slow Tushare updater is an offline job, not a
    page-request fallback.
    """
    root = Path(__file__).resolve().parent.parent
    loaded = {}
    mtimes = {}
    for board_type in ('industry', 'concept'):
        path = root / 'data' / f'{board_type}_constituents.json'
        if not path.exists():
            loaded[board_type] = {}
            continue
        with open(path, 'r', encoding='utf-8') as f:
            loaded[board_type] = json.load(f)
        mtimes[board_type] = path.stat().st_mtime
    with _constituents_json_lock:
        _constituents_json_cache.clear()
        _constituents_json_cache.update(loaded)
        _constituents_json_mtime.clear()
        _constituents_json_mtime.update(mtimes)
    logger.info(
        '[board_service] constituents cache loaded: industry=%s concept=%s',
        len(loaded.get('industry') or {}),
        len(loaded.get('concept') or {}),
    )
    return {'counts': {k: len(v or {}) for k, v in loaded.items()}, 'mtimes': mtimes}


def get_constituents_json_cache_status() -> dict:
    with _constituents_json_lock:
        if not _constituents_json_cache:
            reload_constituents_json_cache()
        return {
            'counts': {k: len(v or {}) for k, v in _constituents_json_cache.items()},
            'mtimes': dict(_constituents_json_mtime),
        }


def _get_constituents_cache(board_type: str) -> dict:
    with _constituents_json_lock:
        if not _constituents_json_cache:
            reload_constituents_json_cache()
        return dict(_constituents_json_cache.get(board_type) or {})


class BoardService:
    """板块业务服务（线程安全）"""

    def __init__(self):
        self._db = get_sqlite_repo()
        self._cache = get_cache()
        self._index_constituents = IndexConstituentService()

    # ---- 板块列表 ----

    def get_industry_boards(self) -> Optional[list]:
        """获取行业板块列表（东财HTTP API）"""
        cache_key = 'boards:industry'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        df = get_industry_boards()
        if df is not None and not df.empty:
            data = df.to_dict('records')
            self._cache.set(cache_key, data, ttl=600)
            return data
        return []

    def get_concept_boards(self) -> Optional[list]:
        """获取概念板块列表（东财HTTP API）"""
        cache_key = 'boards:concept'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        df = get_concept_boards()
        if df is not None and not df.empty:
            data = df.to_dict('records')
            self._cache.set(cache_key, data, ttl=600)
            return data
        return []

    # ---- 板块成分股 ----

    def get_constituents(self, board_type: str, code: str, force_refresh: bool = False) -> list:
        """获取板块成分股（Tushare dc_member + SQLite涨跌幅补充）"""
        if board_type == 'index':
            # Interactive reads are deliberately local-only.  The updater is
            # the only component allowed to contact Tushare index_weight.
            cons = self._index_constituents.get_latest(code)
            return self._enrich_constituents(cons, live=force_refresh)
        cache_key = f'constituents:{board_type}:{code}'
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached:
                return cached

        all_data = _get_constituents_cache(board_type)
        entry = all_data.get(f'{board_type}:{code}') or {}
        cons = [dict(x) for x in entry.get('cons', [])]

        if not cons and board_type in ('industry', 'concept'):
            remote_key = f'{board_type}:{code}'
            with _constituents_json_lock:
                remote_cached = remote_key in _constituents_remote_cache
                remote_cons = [dict(x) for x in _constituents_remote_cache.get(remote_key, [])]
            if not remote_cached:
                remote_cons = get_eastmoney_constituents(board_type, code)
                with _constituents_json_lock:
                    _constituents_remote_cache[remote_key] = [dict(x) for x in remote_cons]
            cons = remote_cons

        # 3. 统一用SQLite补全涨跌幅（force_refresh时走QMT实时）
        cons = self._enrich_constituents(cons, live=force_refresh)
        self._cache.set(cache_key, cons, ttl=300)
        return cons

    def get_constituents_sorted(self, board_type: str, code: str, force_refresh: bool = False) -> list:
        """获取板块成分股（默认按市值降序排列）
        force_refresh=True → 使用 QMT 实时盘中数据（临时缓存，不写入本地DB）"""
        cons = self.get_constituents(board_type, code, force_refresh=force_refresh)
        if not cons:
            return []
        # 默认按市值降序
        cons.sort(key=lambda x: x.get('mkt_cap', 0) or 0, reverse=True)
        return _clean_json_value(cons)

    def _enrich_constituents(self, cons: list, live: bool = False) -> list:
        """用SQLite数据补全成分股的收盘价/涨跌幅/市值
        live=False → SQLite 已结算数据（默认，盘后更新）
        live=True  → QMT 实时盘中数据（仅刷新按钮触发，临时缓存）"""
        if not cons:
            return cons
        codes = [c.get('code', '') for c in cons if c.get('code')]
        if not codes:
            return cons

        service_dir = Path(__file__).resolve().parent.parent
        db_path = str(service_dir / 'data' / 'kline.db')
        conn = sqlite3.connect(db_path)
        try:
            # 批量查询：一条SQL取所有股票的最新2条记录（避免逐只循环160次）
            placeholders = ','.join('?' * len(codes))
            cur = conn.execute(
                f"""SELECT code, date, close, volume FROM (
                    SELECT code, date, close, volume,
                        ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
                    FROM kline
                    WHERE code IN ({placeholders}) AND period='daily'
                      AND close IS NOT NULL AND date IS NOT NULL AND date != ''
                ) WHERE rn <= 2 ORDER BY code, date DESC""",
                codes
            )
            price_map = {}
            for row in cur.fetchall():
                cd = row[0]
                if row[2] is None:
                    continue
                if cd not in price_map:
                    price_map[cd] = {'close': float(row[2]), 'pre_close': float(row[2]), 'volume': row[3] or 0}
                else:
                    # 第二条记录 → 设置为 pre_close
                    if 'close' in price_map[cd]:
                        if row[2] is not None:
                            price_map[cd]['pre_close'] = float(row[2])
                    else:
                        price_map[cd]['close'] = float(row[2])

            # 批量取市值缓存
            mkt_map = {}
            cur = conn.execute(
                f"SELECT code, mkt_cap FROM mkt_cap WHERE code IN ({placeholders})",
                codes
            )
            for row in cur.fetchall():
                if row[1] is not None:
                    mkt_map[row[0]] = float(row[1])
        finally:
            conn.close()

        # ===== 补全数据（默认 SQLite 固定；refresh=1 时 QMT 实时） =====
        
        if live:
            # ======= 刷新模式：18080 批量实时数据（临时缓存，不写DB） =======
            all_codes = [c.get('code','') for c in cons if c.get('code')]
            if all_codes:
                try:
                    from data.qmt_http_client import get_qmt_http_client
                    # A 300-500 symbol synchronous request can monopolize the
                    # single QMT strategy service and delay the selected chart.
                    # Refresh the highest-cap visible cohort; remaining rows
                    # keep their settled SQLite values.
                    live_codes = sorted(
                        all_codes,
                        key=lambda code: mkt_map.get(code, 0),
                        reverse=True,
                    )[:80]
                    payload = get_qmt_http_client().ohlc_batch(
                        live_codes, max_batch=80, timeout=5.0
                    )
                    qmt_data = payload.get('items') or {}
                    if qmt_data:
                        for c in cons:
                            cd = c.get('code','')
                            if cd in qmt_data:
                                qd = qmt_data[cd]
                                close = qd.get('close', qd.get('price'))
                                pre_close = qd.get('pre_close')
                                c['close'] = round(float(close), 4) if close is not None else '-'
                                c['pre_close'] = (
                                    round(float(pre_close), 4)
                                    if pre_close is not None else '-'
                                )
                                change_pct = qd.get('change_pct', qd.get('changePct'))
                                if change_pct is None and close is not None and pre_close:
                                    change_pct = (
                                        (float(close) - float(pre_close))
                                        / float(pre_close) * 100
                                    )
                                c['change_pct'] = round(float(change_pct or 0), 2)
                                # 18080 does not own market-cap metadata.
                                c['mkt_cap'] = mkt_map.get(cd, c.get('mkt_cap', 0) or 0)
                                c['volume'] = qd.get('volume', c.get('volume', '-'))
                                c['source'] = 'qmt18080'
                        logger.debug(
                            '[board_service] 18080实时补全 %s/%s 只',
                            len(qmt_data), len(live_codes),
                        )
                except Exception as e:
                    logger.debug(f'[board_service] 18080实时查询失败: {e}')
            return cons
        
        # ======= 默认模式：SQLite已结算数据 =======
        # 预填充：SQLite有的先填
        for c in cons:
            cd = c.get('code', '')
            pm = price_map.get(cd, {})
            if pm:
                close = pm['close']
                pre_close = pm['pre_close']
                c['close'] = round(close, 4)
                c['pre_close'] = round(pre_close, 4)
                c['change_pct'] = round((close - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
            else:
                if 'close' not in c or c['close'] is None:
                    c['close'] = '-'
                if 'pre_close' not in c or c['pre_close'] is None:
                    c['pre_close'] = '-'
                if 'change_pct' not in c or c['change_pct'] is None:
                    c['change_pct'] = 0
            if 'volume' not in c or c['volume'] is None:
                c['volume'] = '-'
            if cd in mkt_map:
                c['mkt_cap'] = mkt_map[cd]
            elif 'mkt_cap' not in c or c['mkt_cap'] is None:
                c['mkt_cap'] = 0

        return cons

    # ---- 板块K线 ----

    def get_board_kline(self, board_type: str, name: str, code: str,
                        period: str = 'daily'):
        """获取板块K线数据（SQLite优先，Tushare dc_daily 增量补充）"""
        import pandas as pd

        # SQLite优先
        df = self._db.read_kline(code, period)
        if df is not None and not df.empty:
            return df

        # SQLite没有则请求东财API
        df = get_board_kline(board_type, code, start_date='20000101')
        if df is not None and not df.empty:
            self._db.save_kline(code, period, df)
            return df
        return pd.DataFrame()

    def get_board_changes(self) -> dict:
        """获取板块实时涨跌幅（从缓存）"""
        from core.lifecycle import get_app_context
        return get_app_context().get_board_changes_cached()

    def get_stock_change(self, code: str) -> float:
        """获取个股今日涨跌幅（QMT实时，通过qmt_client统一接口）"""
        cache_key = f'stock_chg:{code}'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            from data.qmt_client import get_qmt_client
            from core.lifecycle import is_qmt_available
            if not is_qmt_available():
                return 0.0
            client = get_qmt_client()
            raw = client.get_constituents_live([code])
            if raw and code in raw:
                chg = raw[code].get('change_pct', 0)
                self._cache.set(cache_key, chg, ttl=300)
                return chg
        except Exception as e:
            logger.debug(f'[board_service] get_stock_change {code}: {e}')
        return 0.0

    def _get_market_cap(self, code: str) -> float:
        """获取总市值（300秒TTL缓存，通过qmt_client统一接口）"""
        cache_key = f'mkt_cap:{code}'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            from data.qmt_client import get_qmt_client
            from core.lifecycle import is_qmt_available
            if not is_qmt_available():
                return 0.0
            client = get_qmt_client()
            raw = client.get_constituents_batch([code])
            if raw and code in raw:
                v = raw[code].get('mkt_cap', 0) * 1e8  # mkt_cap以亿为单位，转回元
                self._cache.set(cache_key, v, ttl=300)
                return v
        except Exception as e:
            logger.debug(f'[board_service] _get_market_cap {code}: {e}')
        return 0.0


# 全局单例

_board_service: Optional[BoardService] = None


def get_board_service() -> BoardService:
    global _board_service
    if _board_service is None:
        _board_service = BoardService()
    return _board_service
