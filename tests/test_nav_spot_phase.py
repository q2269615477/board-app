"""Top navigation phase-aware refresh contracts."""

import time
from datetime import datetime, timezone

import services.nav_spot_service as nav


def _reset():
    nav._cache = {}
    nav._cache_ts = 0
    nav._cache_meta = {}
    nav._domestic_phase = ""
    nav._domestic_phase_data = {}
    nav._inflight = False
    nav._refresh_thread = None
    nav._inflight_evt.set()


def _set_utc_now(monkeypatch, value):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(nav, "datetime", _FixedDateTime)


def test_lunch_freezes_domestic_qmt_but_refreshes_overseas(monkeypatch):
    _reset()
    qmt_calls = []
    http_calls = []
    _set_utc_now(
        monkeypatch, datetime(2026, 7, 31, 3, 45, tzinfo=timezone.utc)
    )

    monkeypatch.setattr(
        nav,
        "get_nav_targets",
        lambda: [
            ("sh000001", "上证指数", "index"),
            ("HSI", "恒生指数", "hk_index"),
        ],
    )
    monkeypatch.setattr(
        nav,
        "_split_targets",
        lambda: (["sh000001"], [("HSI", "恒生指数", "hk_index")]),
    )

    def refresh(codes, **kwargs):
        qmt_calls.append(list(codes))
        return {
            "items": {"sh000001": {"close": 3800, "change_pct": 1}},
            "meta": {"channel": "qmt18080"},
        }

    monkeypatch.setattr("services.intraday_cache.refresh_ohlc_batch", refresh)
    monkeypatch.setattr(
        "data_loader.get_local_spot",
        lambda code: {"price": 3800, "change_pct": 1} if code == "sh000001" else {},
    )
    def http(targets):
        http_calls.append([target[0] for target in targets])
        return {
            "HSI": {
                "price": 25000 + len(http_calls),
                "changePct": 0.5,
                "channel": "tencent",
            },
        }

    monkeypatch.setattr(nav, "_fetch_http_spots", http)
    first = nav.fetch_nav_spots(force=True)
    second = nav.fetch_nav_spots(force=True)

    assert len(qmt_calls) == 0
    assert len(http_calls) == 2
    assert http_calls == [["HSI"], ["HSI"]]
    assert first["data"]["sh000001"]["price"] == 3800
    assert second["data"]["sh000001"]["price"] == 3800
    assert second["data"]["HSI"]["price"] == 25002
    assert second["meta"]["channels"]["qmt"]["frozen"] is True


def test_live_phase_refreshes_domestic_each_cycle(monkeypatch):
    _reset()
    qmt_calls = []
    _set_utc_now(
        monkeypatch, datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        nav,
        "get_nav_targets",
        lambda: [("sh000001", "上证指数", "index")],
    )
    monkeypatch.setattr(nav, "_split_targets", lambda: (["sh000001"], []))
    monkeypatch.setattr("data_loader.get_local_spot", lambda _code: {})

    def refresh(codes, **kwargs):
        qmt_calls.append(True)
        return {
            "items": {"sh000001": {"close": 3800 + len(qmt_calls)}},
            "meta": {"channel": "qmt18080"},
        }

    monkeypatch.setattr("services.intraday_cache.refresh_ohlc_batch", refresh)
    nav.fetch_nav_spots(force=True)
    second = nav.fetch_nav_spots(force=True)

    assert len(qmt_calls) == 2
    assert second["data"]["sh000001"]["price"] == 3802


def test_concurrent_refresh_returns_stale_snapshot_without_waiting():
    _reset()
    nav._cache = {"sh000001": {"price": 3800}}
    nav._cache_ts = 0
    nav._cache_meta = {"count": 1}
    nav._inflight = True
    nav._inflight_evt.clear()

    started = time.perf_counter()
    result = nav.fetch_nav_spots()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert result["data"]["sh000001"]["price"] == 3800
    assert result["meta"]["refresh_inflight"] is True
    assert result["from_cache"] is True
    _reset()


def test_closed_phase_uses_local_close_without_domestic_remote_fetch(monkeypatch):
    _reset()
    _set_utc_now(
        monkeypatch, datetime(2026, 7, 31, 8, 30, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        nav,
        "get_nav_targets",
        lambda: [("sh000001", "上证指数", "index")],
    )
    monkeypatch.setattr(
        nav, "_split_targets", lambda: (["sh000001"], [])
    )
    monkeypatch.setattr(
        "data_loader.get_local_spot",
        lambda code: {"price": 3800, "change_pct": 1, "channel": "sqlite"},
    )
    monkeypatch.setattr(
        nav,
        "_fetch_http_spots",
        lambda targets: (_ for _ in ()).throw(
            AssertionError(f"unexpected remote targets: {targets}")
        ),
    )

    result = nav.fetch_nav_spots(force=True)

    assert result["data"]["sh000001"]["price"] == 3800
    assert result["meta"]["channels"]["qmt"]["channel"] == "local_close"
    assert result["meta"]["channels"]["qmt"]["frozen"] is True
    assert result["data"]["sh000001"]["market_open"] is False
