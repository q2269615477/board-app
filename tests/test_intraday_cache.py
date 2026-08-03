"""盘中 OHLC 缓存：TTL / clear / 不持久。"""
import time

from services.intraday_cache import IntradayOhlcCache, clear_intraday_cache, get_intraday_cache


def test_put_get_ttl_and_clear():
    c = IntradayOhlcCache(default_ttl_sec=0.3)
    c.put("600519", {"close": 100.0, "open": 99.0}, channel="qmt18080")
    hit = c.get("600519")
    assert hit is not None
    assert hit["close"] == 100.0
    assert hit["ephemeral"] is True
    time.sleep(0.35)
    assert c.get("600519") is None
    c.put("600519", {"close": 101.0})
    assert c.clear(["600519"]) == 1
    assert c.get("600519") is None


def test_global_clear():
    cache = get_intraday_cache()
    cache.put("_test_tmp_", {"close": 1})
    out = clear_intraday_cache(["_test_tmp_"])
    assert out["cleared"] >= 0
    assert cache.get("_test_tmp_") is None


def test_refresh_batch_uses_client(monkeypatch):
    from services import intraday_cache as mod

    class FakeClient:
        def ohlc_batch(self, codes, period="1d", max_batch=500):
            return {
                "ok": True,
                "channel": "qmt_native_batch",
                "items": {codes[0]: {"close": 12.3, "open": 12.0, "high": 13.0, "low": 11.0}},
                "elapsed_ms": 5,
            }

    monkeypatch.setattr(mod, "get_intraday_cache", lambda: IntradayOhlcCache(default_ttl_sec=30))
    # re-import path inside refresh
    import data.qmt_http_client as qc

    monkeypatch.setattr(qc, "get_qmt_http_client", lambda: FakeClient())
    # refresh imports get_qmt_http_client from data.qmt_http_client inside function
    from services.intraday_cache import refresh_ohlc_batch

    # bind fake via patching the import target used inside function
    monkeypatch.setattr(
        "data.qmt_http_client.get_qmt_http_client",
        lambda: FakeClient(),
    )
    # use fresh cache instance by clearing global
    mod._cache = IntradayOhlcCache(default_ttl_sec=30)
    out = refresh_ohlc_batch(["600519"], force=True)
    assert out["ok"] is True
    assert out["items"]["600519"]["close"] == 12.3
    assert out["items"]["600519"]["ephemeral"] is True
