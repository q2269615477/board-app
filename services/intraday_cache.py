"""
盘中 OHLC 快照缓存（可清理，不写权威 kline.db）

用途：S1 导航 / S2 点股末 bar / 板块成分现价
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("intraday_cache")


class IntradayOhlcCache:
    def __init__(self, default_ttl_sec: float = 15.0):
        self.default_ttl_sec = float(default_ttl_sec)
        self._lock = threading.RLock()
        self._items: Dict[str, Dict[str, Any]] = {}  # code -> {data, ts, channel}
        self._last_batch_meta: Dict[str, Any] = {}

    def _key(self, code: str) -> str:
        return str(code).strip()

    def get(self, code: str, max_age_sec: float = None) -> Optional[Dict[str, Any]]:
        ttl = self.default_ttl_sec if max_age_sec is None else float(max_age_sec)
        k = self._key(code)
        with self._lock:
            row = self._items.get(k)
            if not row:
                return None
            if time.time() - row["ts"] > ttl:
                return None
            data = dict(row["data"])
            data["_cache_age_sec"] = round(time.time() - row["ts"], 3)
            data["_channel"] = row.get("channel")
            data["ephemeral"] = True
            return data

    def get_many(self, codes: Sequence[str], max_age_sec: float = None) -> Dict[str, Dict[str, Any]]:
        out = {}
        for c in codes:
            v = self.get(c, max_age_sec=max_age_sec)
            if v is not None:
                out[str(c).strip()] = v
        return out

    def put(self, code: str, data: Dict[str, Any], channel: str = None) -> None:
        if not code or not isinstance(data, dict):
            return
        k = self._key(code)
        payload = dict(data)
        payload["ephemeral"] = True
        with self._lock:
            self._items[k] = {
                "data": payload,
                "ts": time.time(),
                "channel": channel or data.get("channel") or "qmt18080",
            }

    def put_many(self, items: Dict[str, Dict[str, Any]], channel: str = None) -> int:
        n = 0
        for code, data in (items or {}).items():
            self.put(code, data, channel=channel)
            n += 1
        return n

    def clear(self, codes: Sequence[str] = None) -> int:
        with self._lock:
            if codes is None:
                n = len(self._items)
                self._items.clear()
                self._last_batch_meta = {}
                return n
            n = 0
            for c in codes:
                k = self._key(c)
                if k in self._items:
                    del self._items[k]
                    n += 1
            return n

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._items),
                "default_ttl_sec": self.default_ttl_sec,
                "last_batch_meta": dict(self._last_batch_meta),
            }

    def set_batch_meta(self, meta: Dict[str, Any]) -> None:
        with self._lock:
            self._last_batch_meta = dict(meta or {})


_cache: Optional[IntradayOhlcCache] = None
_cache_lock = threading.Lock()


def get_intraday_cache() -> IntradayOhlcCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            from core.config import INTRADAY_OHLC_TTL_SEC
            _cache = IntradayOhlcCache(default_ttl_sec=INTRADAY_OHLC_TTL_SEC)
        return _cache


def refresh_ohlc_batch(
    codes: Sequence[str],
    force: bool = False,
    ttl_sec: float = None,
) -> Dict[str, Any]:
    """
    拉 18080 /ohlc_batch 写入盘中缓存。
    force=False 时跳过仍在 TTL 内的 code。
    """
    cache = get_intraday_cache()
    ttl = cache.default_ttl_sec if ttl_sec is None else float(ttl_sec)
    codes = [str(c).strip() for c in codes if c and str(c).strip()]
    if not codes:
        return {"ok": False, "error": "empty_codes", "items": {}, "from_cache": {}}

    from_cache = {}
    need = []
    if force:
        need = list(codes)
    else:
        for c in codes:
            hit = cache.get(c, max_age_sec=ttl)
            if hit is not None:
                from_cache[c] = hit
            else:
                need.append(c)

    fetched = {}
    meta: Dict[str, Any] = {
        "requested": len(codes),
        "cache_hits": len(from_cache),
        "fetched": 0,
        "channel": None,
        "ok": True,
    }
    if need:
        from data.qmt_http_client import get_qmt_http_client

        client = get_qmt_http_client()
        result = client.ohlc_batch(need)
        meta["channel"] = result.get("channel")
        meta["mode"] = result.get("mode")
        meta["elapsed_ms"] = result.get("elapsed_ms")
        meta["ok"] = bool(result.get("ok"))
        if result.get("ok"):
            cache.put_many(result.get("items") or {}, channel=result.get("channel"))
            fetched = dict(result.get("items") or {})
            meta["fetched"] = len(fetched)
        else:
            meta["error"] = result.get("error")
            meta["errors"] = result.get("errors")
        cache.set_batch_meta(meta)

    items = {}
    items.update(from_cache)
    items.update(fetched)
    for _k, _v in list(items.items()):
        if isinstance(_v, dict):
            _v = dict(_v)
            _v["ephemeral"] = True
            items[_k] = _v
    return {
        "ok": bool(items),
        "items": items,
        "from_cache": from_cache,
        "fetched": fetched,
        "meta": meta,
        "ephemeral": True,
    }


def clear_intraday_cache(codes: Sequence[str] = None) -> Dict[str, Any]:
    n = get_intraday_cache().clear(codes)
    return {"cleared": n, "ephemeral": True}
