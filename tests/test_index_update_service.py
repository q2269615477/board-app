"""Focused tests for the extracted low-level index update helpers."""

import sqlite3
from datetime import datetime
from types import SimpleNamespace

from services.index_update_service import (
    IndexUpdateDependencies,
    IndexUpdateService,
    local_weekday_session_date,
    normalize_session_date,
)


def _service(*, fetch=None, calendar=None, now=None, market=None):
    return IndexUpdateService(
        IndexUpdateDependencies(
            tushare_index_api_map={"sh000001": ("index_daily", "000001.SH")},
            fetch_tushare_index_df=fetch or (lambda *_args: SimpleNamespace(empty=True)),
            exchange_calendar_api=calendar or (lambda: None),
            normalize_session_date=normalize_session_date,
            local_weekday_session_date=lambda code, current=None: local_weekday_session_date(
                code,
                current,
                index_exchange_market=market or {"HSI": "hong_kong"},
                index_exchange_tz={
                    "a_share": "Asia/Shanghai",
                    "hong_kong": "Asia/Hong_Kong",
                },
                now_factory=now or (lambda: datetime(2026, 8, 3, 12, 0)),
            ),
            index_exchange_market=market or {"sh000001": "a_share", "HSI": "hong_kong"},
            now=now or (lambda: datetime(2026, 8, 3, 12, 0)),
        )
    )


def test_normalize_session_date_accepts_calendar_shapes():
    assert normalize_session_date(datetime(2026, 8, 3)) == "20260803"
    assert normalize_session_date("2026/08/03") == "20260803"
    assert normalize_session_date("20260803") == "20260803"
    assert normalize_session_date("n/a") == ""


def test_session_target_uses_injected_calendar_and_market_state():
    calls = []

    def latest(code, now=None):
        calls.append(("latest", code, now))
        return "2026-08-03"

    def state(code, now=None):
        calls.append(("state", code, now))
        return {"market_open": True}

    service = _service(calendar=lambda: (latest, state))
    instant = datetime(2026, 8, 3, 15, 35)

    assert service.index_session_target("HSI", now=instant) == ("20260803", True)
    assert [call[0] for call in calls] == ["latest", "state"]


def test_session_target_preserves_a_share_fallback_when_calendar_unavailable():
    service = _service(calendar=lambda: None)
    assert service.index_session_target(
        "sh000001",
        now=datetime(2026, 8, 1, 12, 0),
        fallback_td_norm="20260731",
    ) == ("20260731", False)


def test_tushare_fallback_uses_injected_fetcher_and_commits_rows():
    class Frame:
        empty = False

        def to_dict(self, _orient):
            return [
                {
                    "date": "2026-07-11",
                    "open": 105,
                    "high": 115,
                    "low": 95,
                    "close": 110,
                    "volume": 2000,
                }
            ]

    fetch_calls = []

    def fetch(api, ts_code, start, end):
        fetch_calls.append((api, ts_code, start, end))
        return Frame()

    service = _service(
        fetch=fetch,
        now=lambda: datetime(2026, 7, 15, 12, 0),
    )
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, high REAL, "
        "low REAL, close REAL, volume REAL, updated_at TEXT)"
    )
    insert_sql = (
        "INSERT INTO kline (code, period, date, open, high, low, close, volume, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    assert service.tushare_fallback_single_index(
        "sh000001",
        "上证指数",
        "20260710",
        "20260715",
        cur,
        insert_sql,
        "20260715 120000",
    ) == 1
    assert fetch_calls == [("index_daily", "000001.SH", "20260711", "20260715")]
    assert cur.execute("SELECT date, close FROM kline").fetchall() == [
        ("2026-07-11", 110.0)
    ]
    conn.close()

