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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from core.config import NAV_QMT_TYPES, PREWARM_TARGETS, WEBSOCKET_UPDATE_INTERVAL
from services.market_session import (
    MARKET_A_SHARE,
    active_market_signature,
    market_phase_signature,
    market_state,
)

logger = logging.getLogger("nav_spot")

_lock = threading.Lock()
_inflight = False
_inflight_evt = threading.Event()
_inflight_evt.set()
_cache: Dict[str, Any] = {}
_cache_ts = 0.0
_cache_meta: Dict[str, Any] = {}
_domestic_phase = ""
_domestic_phase_data: Dict[str, Any] = {}
_refresh_thread = None

_EXTRA_NAV_TARGETS = (
    ('^N225', '日经225', 'us'),
    ('^KS11', 'KOSPI', 'us'),
    ('^TWII', '台湾加权', 'us'),
    ('SPX', '标普500', 'us'),
    ('IXIC', '纳斯达克', 'us'),
    ('DJI', '道琼斯', 'us'),
)


def get_nav_targets() -> List[Tuple[str, str, str]]:
    """Return the complete top-bar universe in stable display order."""
    return list(PREWARM_TARGETS) + list(_EXTRA_NAV_TARGETS)


def get_nav_spot_status(now=None) -> Dict[str, Any]:
    """Return a read-only snapshot of top-bar cache health.

    This function deliberately never refreshes the cache.  It is used by the
    UI health popover, where a hover must not create network or QMT traffic.
    """
    wall_now = now or datetime.now()
    targets = get_nav_targets()
    market_meta = _market_meta(targets=targets, now=wall_now)
    ttl = _nav_cache_ttl(now=wall_now, targets=targets)
    with _lock:
        cache_count = len(_cache)
        cache_ts = float(_cache_ts or 0.0)
        cache_meta = dict(_cache_meta)
        inflight = bool(_inflight)

    age_sec = max(0.0, time.time() - cache_ts) if cache_ts else None
    channels = {}
    for name, value in (cache_meta.get("channels") or {}).items():
        if not isinstance(value, dict):
            continue
        channels[str(name)] = {
            "count": int(value.get("count") or 0),
            "channel": str(value.get("channel") or name),
            "error": bool(value.get("error")),
        }
    return {
        "count": cache_count,
        "cached_at": (
            datetime.fromtimestamp(cache_ts, tz=timezone.utc).astimezone().isoformat()
            if cache_ts else None
        ),
        "age_sec": round(age_sec, 1) if age_sec is not None else None,
        "ttl_sec": round(float(ttl), 1),
        "stale": bool(cache_count and age_sec is not None and age_sec > max(float(ttl) * 2, 15.0)),
        "inflight": inflight,
        "channels": channels,
        **market_meta,
    }


def _a_share_nav_phase(now=None) -> str:
    """Keep the legacy entry point while delegating to the shared clock."""
    return market_state(MARKET_A_SHARE, now=now)["market_phase"]


def _nav_cache_ttl(now=None, targets=None) -> float:
    """Refresh quickly while any navigation market is trading."""
    targets = targets or get_nav_targets()
    if active_market_signature(targets, now=now):
        return max(1.0, float(WEBSOCKET_UPDATE_INTERVAL) - 1.0)
    return 300.0


def _market_meta(targets=None, now=None) -> Dict[str, Any]:
    targets = targets or get_nav_targets()
    active = active_market_signature(targets, now=now)
    signature = market_phase_signature(targets, now=now)
    return {
        "active_markets": list(active),
        "market_signature": list(signature),
        "all_markets_closed": not bool(active),
    }


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
    for code, name, typ in get_nav_targets():
        if _is_qmt_nav_code(code, typ):
            qmt_codes.append(code)
        else:
            http_targets.append((code, name, typ))
    return qmt_codes, http_targets


def _fetch_http_spots(targets: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    from data_loader import get_spot_board, get_spot_index, get_spot_stock, get_global_index_spot

    def _fetch_one(target):
        code, _name, typ = target
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
                return code, None
            price = d.get("price") or d.get("close")
            chg = d.get("changePct")
            if chg is None:
                chg = d.get("change_pct")
            return code, {
                "code": code,
                "price": price,
                "close": price,
                "changePct": chg,
                "change_pct": chg,
                "channel": d.get("channel") or "http",
            }
        except Exception as e:
            logger.debug("[nav_spot] http %s failed: %s", code, e)
            return code, None

    out = {}
    if not targets:
        return out
    workers = max(1, min(6, len(targets)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='nav_spot') as executor:
        futures = [executor.submit(_fetch_one, target) for target in targets]
        for future in as_completed(futures):
            code, row = future.result()
            if row:
                out[code] = row
    return out


def _normalize_row(
    code: str,
    row: Dict[str, Any],
    channel: str,
    data_type: str = None,
    now=None,
) -> Dict[str, Any]:
    price = row.get("price")
    if price is None:
        price = row.get("close")
    chg = row.get("changePct")
    if chg is None:
        chg = row.get("change_pct")
    state = market_state(code, now=now, data_type=data_type)
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
        "market": state["market"],
        "market_phase": state["market_phase"],
        "market_open": state["market_open"],
    }


def _annotate_snapshot(data: Dict[str, Any], targets=None, now=None) -> Dict[str, Any]:
    target_types = {
        code: data_type for code, _name, data_type in (targets or get_nav_targets())
    }
    annotated = {}
    for code, row in data.items():
        if not isinstance(row, dict):
            continue
        item = dict(row)
        state = market_state(code, now=now, data_type=target_types.get(code))
        item.update({
            "market": state["market"],
            "market_phase": state["market_phase"],
            "market_open": state["market_open"],
        })
        annotated[code] = item
    return annotated


def _load_local_nav_spots(now=None) -> Dict[str, Any]:
    """Build an instant cold-start snapshot from the persisted K-line store."""
    from data_loader import get_local_spot

    data = {}
    for code, _name, data_type in get_nav_targets():
        row = get_local_spot(code)
        if row and (row.get('price') or row.get('close')):
            data[code] = _normalize_row(
                code, row, 'sqlite', data_type=data_type, now=now
            )
    return data


def _schedule_nav_refresh(force: bool = False) -> bool:
    """Start at most one remote refresh without blocking the HTTP/UI thread."""
    global _refresh_thread
    with _lock:
        if _inflight or (_refresh_thread and _refresh_thread.is_alive()):
            return False
        _refresh_thread = threading.Thread(
            target=fetch_nav_spots,
            kwargs={'force': force},
            daemon=True,
            name='nav-spot-refresh',
        )
        _refresh_thread.start()
        return True


def fetch_nav_spots_fast(force: bool = False) -> Dict[str, Any]:
    """Return cache/local data immediately and refresh remote sources in background."""
    global _cache, _cache_ts, _cache_meta

    wall_now = datetime.now(timezone.utc)
    now = time.time()
    targets = get_nav_targets()
    ttl = _nav_cache_ttl(now=wall_now, targets=targets)
    current_market_meta = _market_meta(targets=targets, now=wall_now)
    with _lock:
        data = dict(_cache)
        meta = dict(_cache_meta)
        cache_ts = _cache_ts

    seeded = False
    if not data:
        data = _load_local_nav_spots(now=wall_now)
        if data:
            seeded = True
            meta = {
                'ok': True,
                'count': len(data),
                'channels': {'local': {'count': len(data), 'channel': 'sqlite'}},
                'elapsed_ms': 0,
                **current_market_meta,
            }
            with _lock:
                if not _cache:
                    _cache = dict(data)
                    _cache_ts = now
                    _cache_meta = dict(meta)
                    cache_ts = now
                else:
                    data = dict(_cache)
                    meta = dict(_cache_meta)
                    cache_ts = _cache_ts

    data = _annotate_snapshot(data, targets=targets, now=wall_now)
    previous_signature = tuple(meta.get('market_signature') or ())
    current_signature = tuple(current_market_meta['market_signature'])
    boundary_changed = bool(previous_signature) and previous_signature != current_signature
    stale = force or seeded or not cache_ts or boundary_changed or (now - cache_ts) >= ttl
    refresh_started = _schedule_nav_refresh(force=True) if stale else False
    return {
        'data': data,
        'meta': {
            **meta,
            **current_market_meta,
            'market_boundary_changed': boundary_changed,
            'background_refresh_started': refresh_started,
        },
        'stale': stale,
        'from_cache': bool(data),
    }


def fetch_nav_spots(force: bool = False) -> Dict[str, Any]:
    """
    返回 {code: spot_dict}，含 channel / changePct。
    单飞：并发调用共享同一次拉取。
    """
    global _inflight, _cache, _cache_ts, _cache_meta
    global _domestic_phase, _domestic_phase_data

    wall_now = datetime.now(timezone.utc)
    targets = get_nav_targets()
    target_by_code = {
        code: (code, name, data_type) for code, name, data_type in targets
    }
    target_types = {
        code: data_type for code, _name, data_type in targets
    }
    current_market_meta = _market_meta(targets=targets, now=wall_now)
    current_signature = tuple(current_market_meta["market_signature"])
    ttl = _nav_cache_ttl(now=wall_now, targets=targets)
    now = time.time()
    cached_signature = tuple(_cache_meta.get("market_signature") or ())
    boundary_changed = bool(cached_signature) and cached_signature != current_signature
    if not force and _cache and not boundary_changed and (now - _cache_ts) < ttl:
        data = _annotate_snapshot(_cache, targets=targets, now=wall_now)
        return {
            "data": data,
            "meta": {**dict(_cache_meta), **current_market_meta},
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
        # Ordinary polling never waits behind another tab. A manual refresh is
        # different: wait for the in-flight single-flight request so the user
        # does not receive the cache they explicitly asked to replace.
        if force:
            _inflight_evt.wait(timeout=8)
            data = _annotate_snapshot(_cache, targets=targets, now=wall_now)
            return {
                "data": data,
                "meta": {
                    **dict(_cache_meta),
                    **current_market_meta,
                    "waited_for_refresh": True,
                },
                "stale": bool(_cache) and (time.time() - _cache_ts) > ttl,
                "from_cache": True,
            }
        # Another tab is already refreshing. Never make ordinary polling wait
        # behind a slow external quote source.
        if _cache:
            data = _annotate_snapshot(_cache, targets=targets, now=wall_now)
            return {
                "data": data,
                "meta": {
                    **dict(_cache_meta),
                    **current_market_meta,
                    "refresh_inflight": True,
                },
                "stale": (time.time() - _cache_ts) > ttl,
                "from_cache": True,
            }
        _inflight_evt.wait(timeout=8)
        data = _annotate_snapshot(_cache, targets=targets, now=wall_now)
        return {
            "data": data,
            "meta": {**dict(_cache_meta), **current_market_meta},
            "stale": bool(_cache) and (time.time() - _cache_ts) > ttl,
            "from_cache": True,
        }

    with _lock:
        previous_cache = dict(_cache)
    t0 = time.time()
    data: Dict[str, Any] = {}
    refreshed_codes = set()
    meta: Dict[str, Any] = {
        "channels": {},
        "ok": False,
        **current_market_meta,
    }
    try:
        qmt_codes, http_targets = _split_targets()
        phase = market_state(MARKET_A_SHARE, now=wall_now)["market_phase"]
        meta["a_share_phase"] = phase

        # Every cycle starts from the last valid process snapshot, with SQLite
        # filling cold-start gaps. Closed markets never trigger remote work.
        try:
            local_seed = _load_local_nav_spots(now=wall_now)
            meta["channels"]["local"] = {"count": len(local_seed)}
        except Exception as exc:
            # 本地 SQLite 只是冷启动/收盘兜底；读取失败不能阻断 QMT
            # 或各海外 HTTP 行情源继续独立刷新。
            local_seed = {}
            meta["channels"]["local"] = {"count": 0, "error": str(exc)[:160]}
            logger.warning("[nav_spot] local seed failed, continue remote sources: %s", exc)
        data.update(previous_cache)
        for code, row in local_seed.items():
            state = market_state(
                code, now=wall_now, data_type=target_types.get(code)
            )
            # Once a market is closed, the settlement pipeline's SQLite row is
            # authoritative and must replace an older in-process quote cache.
            if code not in data or not state['market_open']:
                data[code] = row

        active_qmt_codes = [
            code for code in qmt_codes
            if market_state(
                code, now=wall_now, data_type=target_types.get(code)
            )["market_open"]
        ]
        closed_qmt_codes = [code for code in qmt_codes if code not in active_qmt_codes]

        refreshed_qmt_codes = set()
        if active_qmt_codes:
            try:
                from services.intraday_cache import refresh_ohlc_batch

                result = refresh_ohlc_batch(
                    active_qmt_codes, force=True, ttl_sec=0
                )
                items = result.get("items") or {}
                channel = (result.get("meta") or {}).get("channel") or "qmt18080"
                normalized = {
                    code: _normalize_row(
                        code,
                        row,
                        channel,
                        data_type=target_types.get(code),
                        now=wall_now,
                    )
                    for code, row in items.items()
                }
                refreshed_qmt_codes = set(normalized)
                refreshed_codes.update(refreshed_qmt_codes)
                data.update(normalized)
                meta["channels"]["qmt"] = {
                    "count": len(normalized),
                    "channel": channel,
                    "elapsed_ms": (result.get("meta") or {}).get("elapsed_ms"),
                    "frozen": False,
                }
            except Exception as exc:
                logger.warning("[nav_spot] qmt batch failed: %s", exc)
                meta["channels"]["qmt"] = {"error": str(exc)}
        elif qmt_codes:
            local_count = sum(1 for code in closed_qmt_codes if code in data)
            meta["channels"]["qmt"] = {
                "count": local_count,
                "channel": "local_close",
                "frozen": True,
                "skipped": True,
            }

        # HTTP sources are called only for markets currently trading. A QMT
        # miss can use the existing HTTP fallback, but only during its session.
        http_need = [
            target for target in http_targets
            if market_state(target[0], now=wall_now, data_type=target[2])["market_open"]
        ]
        for code in active_qmt_codes:
            if code not in refreshed_qmt_codes and code in target_by_code:
                http_need.append(target_by_code[code])
        if http_need:
            http_data = _fetch_http_spots(http_need)
            meta["channels"]["http"] = {"count": len(http_data)}
            for code, row in http_data.items():
                data[code] = _normalize_row(
                    code,
                    row,
                    row.get("channel") or "http",
                    data_type=target_types.get(code),
                    now=wall_now,
                )
            refreshed_codes.update(http_data)
        else:
            meta["channels"]["http"] = {
                "count": 0,
                "skipped": True,
                "reason": "no_active_http_market",
            }

        domestic_snapshot = {
            code: data[code] for code in qmt_codes if code in data
        }
        if domestic_snapshot:
            _domestic_phase = phase
            _domestic_phase_data = dict(domestic_snapshot)

        data = _annotate_snapshot(data, targets=targets, now=wall_now)
        active_codes = {
            code for code, _name, data_type in targets
            if market_state(code, now=wall_now, data_type=data_type)['market_open']
        }
        stale_active_codes = sorted(active_codes - refreshed_codes)
        meta["ok"] = bool(data)
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        meta["count"] = len(data)
        meta["refreshed_codes"] = sorted(refreshed_codes)
        meta["stale_active_codes"] = stale_active_codes

        if data:
            _cache = data
            # A failed remote cycle must not make an old snapshot young again.
            # Partial success may advance the cache while explicitly reporting
            # which active symbols remain stale.
            if refreshed_codes or not active_codes:
                _cache_ts = time.time()
            _cache_meta = meta
            return {
                "data": dict(data),
                "meta": meta,
                "stale": bool(stale_active_codes),
                "from_cache": not bool(refreshed_codes),
            }

        # total failure → last good
        if _cache:
            cached = _annotate_snapshot(_cache, targets=targets, now=wall_now)
            return {
                "data": cached,
                "meta": {
                    **dict(_cache_meta),
                    **current_market_meta,
                    "stale": True,
                    "error": "refresh_failed",
                },
                "stale": True,
                "from_cache": True,
            }
        return {"data": {}, "meta": meta, "stale": True, "from_cache": False}
    finally:
        with _lock:
            _inflight = False
            _inflight_evt.set()
