"""Exchange-local market session contracts for top navigation quotes."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import services.exchange_calendar_service as calendar_service
from services.market_session import (
    MARKET_A_SHARE,
    MARKET_HONG_KONG,
    MARKET_JAPAN,
    MARKET_SOUTH_KOREA,
    MARKET_STATIC,
    MARKET_TAIWAN,
    MARKET_US,
    active_market_signature,
    classify_market,
    market_is_live,
    market_phase_signature,
    market_state,
)


@pytest.mark.parametrize(
    ("code", "market", "timezone_name", "local_hour", "local_minute"),
    [
        ("sh000001", MARKET_A_SHARE, "Asia/Shanghai", 10, 0),
        ("800000", MARKET_A_SHARE, "Asia/Shanghai", 10, 0),
        ("BK1158", MARKET_A_SHARE, "Asia/Shanghai", 10, 0),
        ("HSI", MARKET_HONG_KONG, "Asia/Hong_Kong", 10, 0),
        ("HSTECH", MARKET_HONG_KONG, "Asia/Hong_Kong", 15, 30),
        ("^N225", MARKET_JAPAN, "Asia/Tokyo", 13, 0),
        ("^KS11", MARKET_SOUTH_KOREA, "Asia/Seoul", 10, 0),
        ("^TWII", MARKET_TAIWAN, "Asia/Taipei", 10, 0),
        ("SPX", MARKET_US, "America/New_York", 10, 0),
        ("IXIC", MARKET_US, "America/New_York", 15, 59),
        ("DJI", MARKET_US, "America/New_York", 9, 30),
    ],
)
def test_each_supported_market_opens_on_its_local_clock(
    code, market, timezone_name, local_hour, local_minute
):
    local = datetime(
        2026, 7, 31, local_hour, local_minute, tzinfo=ZoneInfo(timezone_name)
    )

    state = market_state(code, now=local)

    assert classify_market(code) == market
    assert state["market"] == market
    assert state["market_open"] is True


@pytest.mark.parametrize(
    ("code", "timezone_name", "hour", "minute", "phase"),
    [
        ("sh000001", "Asia/Shanghai", 12, 0, "lunch"),
        ("HSI", "Asia/Hong_Kong", 12, 30, "lunch"),
        ("^N225", "Asia/Tokyo", 12, 0, "lunch"),
        ("^KS11", "Asia/Seoul", 15, 30, "closed"),
        ("^TWII", "Asia/Taipei", 13, 30, "closed"),
        ("SPX", "America/New_York", 16, 0, "closed"),
    ],
)
def test_lunch_and_close_boundaries_are_not_live(
    code, timezone_name, hour, minute, phase
):
    local = datetime(2026, 7, 31, hour, minute, tzinfo=ZoneInfo(timezone_name))

    state = market_state(code, now=local)

    assert state["market_phase"] == phase
    assert state["market_open"] is False


def test_south_korea_has_no_fallback_lunch_break(monkeypatch):
    monkeypatch.setattr(calendar_service, "mcal", None)
    calendar_service.clear_caches()
    seoul = ZoneInfo("Asia/Seoul")

    state = market_state(
        "^KS11", now=datetime(2026, 7, 31, 12, 30, tzinfo=seoul)
    )

    assert state["market_phase"] == "live"
    assert state["market_open"] is True


@pytest.mark.skipif(
    calendar_service.mcal is None,
    reason="pandas-market-calendars is optional in the test environment",
)
def test_south_korea_discontinued_break_is_ignored_by_exact_calendar():
    seoul = ZoneInfo("Asia/Seoul")

    state = market_state(
        "^KS11", now=datetime(2026, 7, 31, 12, 30, tzinfo=seoul)
    )

    assert state["certainty"] == "high"
    assert state["market_phase"] == "live"
    assert state["market_open"] is True


def test_weekend_and_unknown_symbols_are_closed():
    saturday = datetime(2026, 8, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert market_is_live("sh000001", now=saturday) is False
    assert market_state("UNKNOWN", now=saturday) == {
        "market": MARKET_STATIC,
        "market_phase": "closed",
        "market_open": False,
        "market_timezone": None,
    }


def test_us_session_uses_zoneinfo_for_summer_and_winter_utc_offsets():
    summer_open = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    summer_preopen = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
    winter_open = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
    winter_preopen = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

    assert market_is_live("SPX", now=summer_open) is True
    assert market_is_live("SPX", now=summer_preopen) is False
    assert market_is_live("SPX", now=winter_open) is True
    assert market_is_live("SPX", now=winter_preopen) is False


@pytest.mark.skipif(
    calendar_service.mcal is None,
    reason="pandas-market-calendars is optional in the test environment",
)
def test_us_christmas_eve_early_close_uses_exchange_local_schedule():
    local_tz = ZoneInfo("America/New_York")
    before_close = market_state(
        "SPX", now=datetime(2026, 12, 24, 12, 59, tzinfo=local_tz)
    )
    at_close = market_state(
        "SPX", now=datetime(2026, 12, 24, 13, 0, tzinfo=local_tz)
    )

    assert before_close["market_open"] is True
    assert before_close["certainty"] == "high"
    assert at_close["market_open"] is False
    assert at_close["market_phase"] == "closed"


def test_market_state_reports_weekday_fallback_provenance(monkeypatch):
    monkeypatch.setattr(calendar_service, "mcal", None)
    calendar_service.clear_caches()

    state = market_state(
        "sh000001", now=datetime(2026, 2, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert state["market_open"] is True
    assert state["certainty"] == "low"
    assert state["source"] == "weekday_fallback"


def test_active_and_phase_signatures_change_at_lunch_boundary():
    targets = [("sh000001", "上证指数", "index"), ("HSI", "恒指", "hk_index")]
    before = datetime(2026, 7, 31, 3, 29, tzinfo=timezone.utc)
    after = datetime(2026, 7, 31, 3, 30, tzinfo=timezone.utc)

    assert active_market_signature(targets, now=before) == (
        MARKET_A_SHARE,
        MARKET_HONG_KONG,
    )
    assert active_market_signature(targets, now=after) == (MARKET_HONG_KONG,)
    assert market_phase_signature(targets, now=before) != market_phase_signature(
        targets, now=after
    )
