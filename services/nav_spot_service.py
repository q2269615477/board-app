"""
S1 顶部导航行情聚合：
- A 股指数（PREWARM type=index）→ QMT 18080 /ohlc_batch
- 港股/板块/特殊码 → HTTP spot 降级
- 单飞 + 短 TTL，防频控与多客户端倍增请求
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Tuple

from core.config import NAV_QMT_TYPES, PREWARM_TARGETS, WEBSOCKET_UPDATE_INTERVAL

logger = logging.getLogger("nav_spot")

_lock = threading.Lock()
_inflight = False
_inflight_evt = threading.Event()
_inflight_evt.set()
_cache: Dict[str, Any] = {}
_cache_ts = 0.0
_cache_meta: Dict[str, Any] = {}


def _is_qmt_nav_code(code: str, typ: str) -> bool:
    """A-share index only; exclude 800000 / BK* / bare specials."""
    if typ not in NAV_QMT_TYPES:
        return False
    c = str(code).strip()
    if c in ('800000',) or c.startswith('BK'):
        return False
    if c.startswith(('sh', 'sz', 'bj')) and len(c) >= 8:
        return True
    return False


def _split_targets() -> Tuple[List[str], List[Tuple[str, str, str]]]:
    qmt_codes = []
    http_targets = []
    for code, name, typ in PREWARM_TARGETS:
        if _is_qmt_nav_code(code, typ):
            qmt_codes.append(code)
        else:
            http_targets.append((code, name, typ))
    return qmt_codes, http_targets


def _fetch_http_spots(targets: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    from data_loader import get_spot_board, get_spot_index, get_spot_stock, get_global_index_spot

    out = {}
    for code, _name, typ in targets:
        try:
            d = None
            if typ in ("hk_index", "us") or code in (
                "HSI", "HSTECH", "SPX", "IXIC", "DJI", "^N225", "^KS11", "^TWII", "800000"
            ):
                d = get_global_index_spot(code)
            elif typ in ("concept", "industry") or str(code).startswith("BK"):
                d = get_spot_board("concept" if not typ == "industry" else "industry", code)
            elif typ == "stock":
                d = get_spot_stock(code)
            else:
                d = get_spot_index(code) or get_global_index_spot(code)
            if not d:
                continue
            price = d.get("price") or d.get("close")
            chg = d.get("changePct")
            if chg is None:
                chg = d.get("change_pct")
            out[code] = {
                "code": code,
                "price": price,
                "close": price,
                "changePct": chg,
                "change_pct": chg,
                "channel": d.get("channel") or "http",
            }
        except Exception as e:
            logger.debug("[nav_spot] http %s failed: %s", code, e)
    return out


def _normalize_row(code: str, row: Dict[str, Any], channel: str) -> Dict[str, Any]:
    price = row.get("price")
    if price is None:
        price = row.get("close")
    chg = row.get("changePct")
    if chg is None:
        chg = row.get("change_pct")
    return {
        "code": code,
        "price": price,
        "close": price,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "pre_close": row.get("pre_close"),
        "change": row.get("change"),
        "changePct": chg,
        "change_pct": chg,
        "time": row.get("time"),
        "channel": channel or row.get("channel") or row.get("_channel") or "unknown",
        "ephemeral": True,
    }


def fetch_nav_spots(force: bool = False) -> Dict[str, Any]:
    """
    返回 {code: spot_dict}，含 channel / changePct。
    单飞：并发调用共享同一次拉取。
    """
    global _inflight, _cache, _cache_ts, _cache_meta

    ttl = max(1.0, float(WEBSOCKET_UPDATE_INTERVAL) - 1.0)
    now = time.time()
    if not force and _cache and (now - _cache_ts) < ttl:
        return {
            "data": dict(_cache),
            "meta": dict(_cache_meta),
            "stale": False,
            "from_cache": True,
        }

    with _lock:
        if _inflight:
            wait = True
        else:
            wait = False
            _inflight = True
            _inflight_evt.clear()

    if wait:
        _inflight_evt.wait(timeout=8)
        return {
            "data": dict(_cache),
            "meta": dict(_cache_meta),
            "stale": bool(_cache) and (time.time() - _cache_ts) > ttl,
            "from_cache": True,
        }

    t0 = time.time()
    data: Dict[str, Any] = {}
    meta: Dict[str, Any] = {"channels": {}, "ok": False}
    try:
        qmt_codes, http_targets = _split_targets()
        # QMT batch
        if qmt_codes:
            try:
                from services.intraday_cache import refresh_ohlc_batch

                result = refresh_ohlc_batch(qmt_codes, force=True, ttl_sec=0)
                items = result.get("items") or {}
                ch = (result.get("meta") or {}).get("channel") or "qmt18080"
                meta["channels"]["qmt"] = {
                    "count": len(items),
                    "channel": ch,
                    "elapsed_ms": (result.get("meta") or {}).get("elapsed_ms"),
                }
                for code, row in items.items():
                    data[code] = _normalize_row(code, row, ch)
            except Exception as e:
                logger.warning("[nav_spot] qmt batch failed: %s", e)
                meta["channels"]["qmt"] = {"error": str(e)}

        # HTTP fallback for non-QMT + fill QMT misses
        http_need = list(http_targets)
        for code, name, typ in PREWARM_TARGETS:
            if typ in NAV_QMT_TYPES and code not in data:
                http_need.append((code, name, typ))
        if http_need:
            http_data = _fetch_http_spots(http_need)
            meta["channels"]["http"] = {"count": len(http_data)}
            for code, row in http_data.items():
                if code not in data:
                    data[code] = _normalize_row(code, row, row.get("channel") or "http")

        meta["ok"] = bool(data)
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        meta["count"] = len(data)

        if data:
            _cache = data
            _cache_ts = time.time()
            _cache_meta = meta
            return {"data": dict(data), "meta": meta, "stale": False, "from_cache": False}

        # total failure → last good
        if _cache:
            return {
                "data": dict(_cache),
                "meta": {**dict(_cache_meta), "stale": True, "error": "refresh_failed"},
                "stale": True,
                "from_cache": True,
            }
        return {"data": {}, "meta": meta, "stale": True, "from_cache": False}
    finally:
        with _lock:
            _inflight = False
            _inflight_evt.set()
