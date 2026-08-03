from types import SimpleNamespace
from unittest.mock import patch

from services.data_source_health import build_data_source_health


def _stats(value):
    return SimpleNamespace(stats=lambda: value)


def _patch_health_dependencies(*, nav=None, snapshot=None, spot=None, update=None,
                               intraday=None, qmt=True):
    return (
        patch("services.nav_spot_service.get_nav_spot_status", return_value=nav or {}),
        patch("services.board_snapshot.get_snapshot_cache", return_value=_stats(snapshot or {})),
        patch("services.board_spot_cache.get_board_spot_cache", return_value=_stats(spot or {})),
        patch("services.update_status_store.load_status", return_value=update or {}),
        patch("services.intraday_cache.get_intraday_cache", return_value=_stats(intraday or {})),
        patch("core.lifecycle.is_qmt_available", return_value=qmt),
    )


def test_health_contract_contains_exactly_five_user_visible_areas():
    patches = _patch_health_dependencies(
        nav={
            "count": 13,
            "cached_at": "2026-08-03T10:00:00+08:00",
            "age_sec": 2,
            "stale": False,
            "inflight": False,
            "all_markets_closed": False,
            "channels": {"qmt": {"count": 7}, "http": {"count": 6}},
        },
        snapshot={
            "date": "20260803",
            "captured_count_industry": 86,
            "captured_count_concept": 412,
            "captured_at": 1785722400,
            "mode": "live",
            "frozen": False,
        },
        spot={"counts": {}, "timestamps": {}, "frozen": False},
        update={
            "today": "20260803",
            "qmt_daily_done": "20260803",
            "indices": {
                "sh000001": {"status": "success", "local_max": "20260803"},
                "sz399006": {"status": "up_to_date", "target_date": "20260803"},
            },
            "stocks": {},
        },
        intraday={"size": 28, "last_batch_meta": {"ok": True}},
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = build_data_source_health()

    assert result["ok"] is True
    assert [item["label"] for item in result["items"]] == [
        "顶部导航栏", "东财概念板块", "行业板块", "指数", "个股数据源"
    ]
    assert all(item["status"] == "healthy" for item in result["items"])
    assert result["items"][0]["source"] == "QMT + HTTP"
    assert result["items"][3]["last_updated"] == "2026-08-03"


def test_health_reports_closed_and_cached_sources_without_claiming_live_updates():
    patches = _patch_health_dependencies(
        nav={
            "count": 13,
            "cached_at": "2026-08-03T15:10:00+08:00",
            "age_sec": 600,
            "stale": True,
            "inflight": False,
            "all_markets_closed": True,
            "channels": {"http": {"count": 13}},
        },
        snapshot={"captured_count_industry": 0, "captured_count_concept": 0, "mode": "off"},
        spot={
            "counts": {"industry": 86, "concept": 412},
            "timestamps": {"industry": "15:05:00", "concept": "15:05:00"},
            "frozen": False,
        },
        update={"today": "20260731", "qmt_daily_done": "20260731", "indices": {}, "stocks": {}},
        intraday={"size": 0, "last_batch_meta": {}},
        qmt=False,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = build_data_source_health()

    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["top_navigation"]["status_text"] == "休市缓存"
    assert by_id["concept"]["status_text"] == "休市缓存"
    assert by_id["industry"]["status_text"] == "休市缓存"
    assert by_id["stocks"]["status_text"] == "本地缓存可用"


def test_data_source_health_route_uses_read_only_aggregator():
    payload = {"ok": True, "generated_at": "now", "items": []}
    with patch("app.start_app"), patch("app.realtime_websocket"):
        from app import app
        app.config["TESTING"] = True
        with patch("services.data_source_health.build_data_source_health", return_value=payload) as builder:
            response = app.test_client().get("/api/system/data-source-health")

    assert response.status_code == 200
    assert response.get_json() == payload
    builder.assert_called_once_with()
