"""Exchange-local date attribution for global index bars.

Regression tests for the timestamp/date conversions in
data.global_index_kline.  US index sessions run overnight in Asia/Shanghai,
so a spot or Yahoo timestamp must be attributed to the exchange's local
trading date (America/New_York), never shifted to the next Asia/Shanghai
day.  Sources that already publish an exchange trading date (Eastmoney
history klines, Tencent, Sina) must pass their date strings through
unchanged.

All tests are pure unit tests with fixed UTC instants.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from data.global_index_kline import (
    fetch_eastmoney_global_kline,
    fetch_eastmoney_spot_bar,
    fetch_sina_global_kline,
    fetch_tencent_global_kline,
    fetch_yahoo_global_kline,
    market_timezone_for_index,
)


def _epoch(iso_utc: str) -> int:
    return int(
        datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )


class _SpotResponse:
    def __init__(self, f86):
        self._f86 = f86

    def json(self):
        return {"data": {
            "f43": 6436202,
            "f44": 6536473,
            "f45": 6194823,
            "f46": 6195710,
            "f47": 430195,
            "f48": 0,
            "f86": self._f86,
        }}


class _SpotSession:
    trust_env = True

    def __init__(self, f86):
        self._f86 = f86

    def get(self, *args, **kwargs):
        return _SpotResponse(self._f86)


@pytest.mark.parametrize("code", ["SPX", "IXIC", "DJI"])
def test_us_spot_night_session_keeps_exchange_trading_date(monkeypatch, code):
    # 2026-07-31 20:00 UTC is 16:00 EDT on the same Friday trading date,
    # while Asia/Shanghai is already 2026-08-01 04:00.
    f86 = _epoch("2026-07-31T20:00:00+00:00")
    assert pd.to_datetime(f86, unit="s", utc=True).tz_convert(
        "Asia/Shanghai"
    ).strftime("%Y-%m-%d") == "2026-08-01"

    monkeypatch.setattr(
        "data.global_index_kline.requests.Session",
        lambda: _SpotSession(f86),
    )
    out = fetch_eastmoney_spot_bar(code)

    assert out["date"].tolist() == ["2026-07-31"]
    assert "2026-08-01" not in set(out["date"])


def test_us_spot_winter_after_close_keeps_friday_date(monkeypatch):
    # 2026-01-30 23:00 UTC is 18:00 EST on Friday 2026-01-30; Asia/Shanghai
    # is already 2026-01-31 07:00.
    f86 = _epoch("2026-01-30T23:00:00+00:00")
    monkeypatch.setattr(
        "data.global_index_kline.requests.Session",
        lambda: _SpotSession(f86),
    )

    out = fetch_eastmoney_spot_bar("SPX")

    assert out["date"].tolist() == ["2026-01-30"]
    assert "2026-01-31" not in set(out["date"])


@pytest.mark.parametrize(
    ("code", "iso_utc", "expected"),
    [
        ("HSI", "2026-07-31T08:30:00+00:00", "2026-07-31"),   # 16:30 HKT
        ("HSTECH", "2026-07-31T08:30:00+00:00", "2026-07-31"),  # 16:30 HKT
        ("^N225", "2026-07-31T06:30:00+00:00", "2026-07-31"),  # 15:30 JST
        ("^KS11", "2026-07-31T06:30:00+00:00", "2026-07-31"),  # 15:30 KST
        ("^TWII", "2026-07-31T05:30:00+00:00", "2026-07-31"),  # 13:30 TST
        ("800000", "2026-07-31T07:00:00+00:00", "2026-07-31"),  # 15:00 CST
    ],
)
def test_asia_spot_timestamps_keep_exchange_trading_date(
    monkeypatch, code, iso_utc, expected
):
    f86 = _epoch(iso_utc)
    monkeypatch.setattr(
        "data.global_index_kline.requests.Session",
        lambda: _SpotSession(f86),
    )

    out = fetch_eastmoney_spot_bar(code)

    assert out["date"].tolist() == [expected]


class _YahooResponse:
    def __init__(self, timestamps):
        self._timestamps = timestamps

    def raise_for_status(self):
        return None

    def json(self):
        count = len(self._timestamps)
        return {"chart": {"result": [{
            "timestamp": self._timestamps,
            "indicators": {"quote": [{
                "open": [100.0] * count,
                "high": [101.0] * count,
                "low": [99.0] * count,
                "close": [100.5] * count,
                "volume": [1000] * count,
            }]},
        }]}}


def _monkeypatch_yahoo(monkeypatch, timestamps):
    monkeypatch.setattr(
        "data.global_index_kline.requests.get",
        lambda *args, **kwargs: _YahooResponse(timestamps),
    )


@pytest.mark.parametrize("code", ["SPX", "IXIC", "DJI"])
def test_yahoo_us_timestamps_attribute_to_new_york_trading_date(
    monkeypatch, code
):
    # 2026-07-30 23:30 UTC = 19:30 EDT 07-30; 2026-08-01 03:30 UTC =
    # 23:30 EDT 07-31.  A naive UTC conversion would label the second bar
    # 2026-08-01.
    timestamps = [
        _epoch("2026-07-30T23:30:00+00:00"),
        _epoch("2026-08-01T03:30:00+00:00"),
    ]
    _monkeypatch_yahoo(monkeypatch, timestamps)

    out = fetch_yahoo_global_kline(code)

    assert out["date"].tolist() == ["2026-07-30", "2026-07-31"]
    assert "2026-08-01" not in set(out["date"])


@pytest.mark.parametrize(
    ("code", "iso_utc", "expected"),
    [
        # 2026-07-30 23:30 UTC = 08:30 JST on 07-31; naive UTC would say 07-30.
        ("^N225", "2026-07-30T23:30:00+00:00", "2026-07-31"),
        # 2026-07-30 16:30 UTC = 00:30 TST on 07-31 (pre-open calendar day).
        ("^TWII", "2026-07-30T16:30:00+00:00", "2026-07-31"),
        ("HSI", "2026-07-31T01:30:00+00:00", "2026-07-31"),   # 09:30 HKT
        ("^KS11", "2026-07-31T00:00:00+00:00", "2026-07-31"),  # 09:00 KST
    ],
)
def test_yahoo_asia_timestamps_keep_exchange_trading_date(
    monkeypatch, code, iso_utc, expected
):
    _monkeypatch_yahoo(monkeypatch, [_epoch(iso_utc)])

    out = fetch_yahoo_global_kline(code)

    assert out["date"].tolist() == [expected]


def test_eastmoney_history_date_string_is_not_shifted(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": [
                "2026-07-31,100,100.5,101,99,1000,0,0,0,0,0",
            ]}}

    monkeypatch.setattr(
        "data.global_index_kline.requests.get",
        lambda *args, **kwargs: Response(),
    )

    out = fetch_eastmoney_global_kline("SPX", limit=30)

    assert out["date"].tolist() == ["2026-07-31"]


def test_sina_apac_date_string_is_not_shifted(monkeypatch):
    class Response:
        def json(self):
            return {"result": {"data": [
                {"d": "2026-07-31", "o": "100", "h": "101",
                 "l": "99", "c": "100.5", "v": "0"},
            ]}}

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(
        "data.global_index_kline.requests.Session",
        Session,
    )

    out = fetch_sina_global_kline("^N225")

    assert out["date"].tolist() == ["2026-07-31"]


def test_tencent_date_string_is_not_shifted(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"hkHSI": {"day": [
                ["2026-07-31", "100", "100.5", "101", "99", "1000"],
            ]}}}

    monkeypatch.setattr(
        "data.global_index_kline.requests.get",
        lambda *args, **kwargs: Response(),
    )

    out = fetch_tencent_global_kline("HSI")

    assert out["date"].tolist() == ["2026-07-31"]


@pytest.mark.parametrize(
    ("code", "expected_tz"),
    [
        ("HSI", "Asia/Hong_Kong"),
        ("HSTECH", "Asia/Hong_Kong"),
        ("^N225", "Asia/Tokyo"),
        ("^KS11", "Asia/Seoul"),
        ("^TWII", "Asia/Taipei"),
        ("SPX", "America/New_York"),
        ("IXIC", "America/New_York"),
        ("DJI", "America/New_York"),
        ("800000", "Asia/Shanghai"),
    ],
)
def test_market_timezone_mapping(code, expected_tz):
    assert market_timezone_for_index(code) == expected_tz
