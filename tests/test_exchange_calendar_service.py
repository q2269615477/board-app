"""Trading-day and calendar provenance contracts."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import services.exchange_calendar_service as calendar_service
from services.exchange_calendar_service import (
    MARKET_A_SHARE,
    MARKET_HONG_KONG,
    MARKET_JAPAN,
    MARKET_SOUTH_KOREA,
    MARKET_TAIWAN,
    MARKET_US,
    clear_caches,
    is_trading_day,
    latest_expected_session_date,
    market_calendar_name,
)


@pytest.fixture(autouse=True)
def clear_calendar_caches():
    clear_caches()
    yield
    clear_caches()


@pytest.mark.parametrize(
    ("market", "calendar_name"),
    [
        (MARKET_A_SHARE, "SSE"),
        (MARKET_HONG_KONG, "XHKG"),
        (MARKET_JAPAN, "JPX"),
        (MARKET_SOUTH_KOREA, "XKRX"),
        (MARKET_TAIWAN, "XTAI"),
        (MARKET_US, "XNYS"),
    ],
)
def test_supported_markets_map_to_requested_exchange_calendars(
    market, calendar_name
):
    assert market_calendar_name(market) == calendar_name


@pytest.mark.skipif(
    calendar_service.mcal is None,
    reason="pandas-market-calendars is optional in the test environment",
)
def test_sse_spring_festival_is_closed_and_previous_session_is_returned():
    assert is_trading_day(MARKET_A_SHARE, date(2026, 2, 17)) is False
    assert latest_expected_session_date(
        MARKET_A_SHARE, date(2026, 2, 17)
    ) == date(2026, 2, 13)

    details = is_trading_day(
        MARKET_A_SHARE, date(2026, 2, 17), return_metadata=True
    )
    assert details["is_trading_day"] is False
    assert details["certainty"] == "high"
    assert details["source"] == "pandas_market_calendars"


@pytest.mark.skipif(
    calendar_service.mcal is None,
    reason="pandas-market-calendars is optional in the test environment",
)
def test_us_independence_day_observed_holiday_and_christmas_eve_session():
    assert is_trading_day(MARKET_US, date(2026, 7, 3)) is False
    assert is_trading_day(MARKET_US, date(2026, 7, 4)) is False
    assert is_trading_day(MARKET_US, date(2026, 7, 6)) is True
    assert is_trading_day(MARKET_US, date(2026, 12, 25)) is False


@pytest.mark.parametrize(
    "market",
    [
        MARKET_A_SHARE,
        MARKET_HONG_KONG,
        MARKET_JAPAN,
        MARKET_SOUTH_KOREA,
        MARKET_TAIWAN,
        MARKET_US,
    ],
)
def test_weekend_is_closed_for_every_supported_market(market):
    details = is_trading_day(market, date(2026, 8, 1), return_metadata=True)
    assert details["is_trading_day"] is False
    assert details["session_date"] == date(2026, 8, 1)


def test_latest_expected_session_date_uses_exchange_local_date_for_aware_datetime():
    # 00:30 UTC on Monday is still Sunday in New York.
    expected = date(2026, 7, 2) if calendar_service.mcal is not None else date(2026, 7, 3)
    assert latest_expected_session_date(
        MARKET_US, datetime(2026, 7, 6, 0, 30, tzinfo=calendar_service.timezone.utc)
    ) == expected


def test_preopen_datetime_uses_previous_session_but_date_only_includes_today():
    preopen = datetime(
        2026, 7, 6, 8, 0, tzinfo=ZoneInfo("America/New_York")
    )
    expected_previous = (
        date(2026, 7, 2) if calendar_service.mcal is not None else date(2026, 7, 3)
    )

    assert latest_expected_session_date(MARKET_US, preopen) == expected_previous
    assert latest_expected_session_date(
        MARKET_US, date(2026, 7, 6)
    ) == date(2026, 7, 6)


def test_missing_calendar_uses_weekday_fallback_with_low_certainty(monkeypatch):
    monkeypatch.setattr(calendar_service, "mcal", None)
    clear_caches()

    holiday_like_weekday = is_trading_day(
        MARKET_A_SHARE, date(2026, 2, 17), return_metadata=True
    )
    assert holiday_like_weekday == {
        "market": MARKET_A_SHARE,
        "session_date": date(2026, 2, 17),
        "is_trading_day": True,
        "certainty": "low",
        "source": "weekday_fallback",
    }
    assert latest_expected_session_date(
        MARKET_A_SHARE, date(2026, 2, 17), return_metadata=True
    )["source"] == "weekday_fallback"
