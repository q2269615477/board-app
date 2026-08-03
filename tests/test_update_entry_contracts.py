"""Regression tests for the unified data-update entry contracts."""

import sqlite3
import threading
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _reset_full_update_guard():
    import data_update_manager as dum

    dum._release_full_update()
    yield
    dum._release_full_update()


def test_update_all_today_forwards_force_and_contains_progress_failures(monkeypatch):
    import data_update_manager as dum

    monkeypatch.setattr(dum, "is_today_updated", lambda: False)
    monkeypatch.setattr(dum, "_is_trading_day", lambda: False)
    monkeypatch.setattr(dum, "_qmt_connect", lambda: True)
    monkeypatch.setattr(dum, "_mark_today_done", lambda: None)
    monkeypatch.setenv("BOARD_HISTORY_REPAIR_BATCH", "0")
    monkeypatch.setattr(
        dum,
        "update_all_indices_qmt",
        lambda max_retries=3: {
            "success": 1,
            "failed": 0,
            "total": 1,
            "completion_ready": True,
        },
    )
    stock_calls = []

    def fake_stocks(max_retries=3, **kwargs):
        stock_calls.append(kwargs)
        return {
            "success": 1,
            "failed": 0,
            "total": 1,
            "completion_ready": True,
            "updated_codes": [],
        }

    monkeypatch.setattr(dum, "qmt_update_all_stocks", fake_stocks)

    events = []

    def callback(*args):
        events.append(args)
        raise RuntimeError("observer must not break the update")

    result = dum.update_all_today(force=True, progress_callback=callback)

    assert result["completion_ready"] is True
    assert stock_calls == [{"cancel_check": None, "force": True}]
    assert [event[0] for event in events] == [
        "indices",
        "indices",
        "stocks",
        "stocks",
        "boards",
        "boards",
    ]
    assert all(len(event) == 4 for event in events)
    assert dum.is_full_update_in_progress() is False


def test_update_all_today_guard_prevents_duplicate_call(monkeypatch):
    import data_update_manager as dum

    started = threading.Event()
    release = threading.Event()

    def blocking_impl(**kwargs):
        started.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    monkeypatch.setattr(dum, "_update_all_today_impl", blocking_impl)
    first_result = []
    worker = threading.Thread(
        target=lambda: first_result.append(dum.update_all_today(force=True)),
        daemon=True,
    )
    worker.start()
    assert started.wait(timeout=2)

    duplicate = dum.update_all_today(force=True)
    assert duplicate["running"] is True
    assert duplicate["skipped"] is True

    release.set()
    worker.join(timeout=2)
    assert first_result == [{"ok": True}]
    assert dum.is_full_update_in_progress() is False


def test_update_all_today_guard_released_when_impl_raises(monkeypatch):
    import data_update_manager as dum

    def fail_impl(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dum, "_update_all_today_impl", fail_impl)
    with pytest.raises(RuntimeError, match="boom"):
        dum.update_all_today()
    assert dum.is_full_update_in_progress() is False


def test_task_factory_reads_the_manager_owned_full_update_guard():
    import data_update_manager as dum
    from services.update_task_factories import _full_update_already_running

    assert _full_update_already_running() is False
    assert dum._claim_full_update() is True
    assert _full_update_already_running() is True
    dum._release_full_update()
    assert _full_update_already_running() is False


def _make_board_meta(path, target, stale=False):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE kline_meta ("
        "code TEXT, period TEXT, rows INTEGER, first_date TEXT, "
        "last_date TEXT, updated_at TEXT, PRIMARY KEY(code, period))"
    )
    last_date = "2020-01-01" if stale else target
    conn.executemany(
        "INSERT INTO kline_meta VALUES (?,?,?,?,?,?)",
        [
            ("BK0001", "daily", 100, "2020-01-01", target, ""),
            ("BK0002", "daily", 100, "2020-01-01", last_date, ""),
        ],
    )
    conn.commit()
    conn.close()


def test_update_all_boards_only_lagging_filters_before_source_fetch(monkeypatch, tmp_path):
    import data_update_manager as dum

    # Deliberately differ from the wall-clock date to prove the catch-up path
    # uses the last trading session rather than a weekend/holiday date.
    target = "20260803"
    db_path = tmp_path / "kline.db"
    _make_board_meta(db_path, target, stale=True)
    monkeypatch.setattr(dum, "_LEDGER_DB", db_path)
    monkeypatch.setattr("data.board_api.get_last_trading_date", lambda: target)
    monkeypatch.setattr(
        dum,
        "_load_classified_boards",
        lambda *_args: [
            ("industry", "Fresh", "BK0001"),
            ("concept", "Stale", "BK0002"),
        ],
    )
    monkeypatch.setattr(dum, "_update_status", lambda mutator: None)
    calls = []
    monkeypatch.setattr(
        dum,
        "_update_single_board",
        lambda board_type, name, code, **kwargs: calls.append(code) or True,
    )

    class FakePro:
        def dc_daily(self, **kwargs):
            return pd.DataFrame([{"ts_code": "BK0002.DC", "trade_date": target}])

    monkeypatch.setattr(dum, "_get_tushare_pro", lambda: FakePro())

    result = dum.update_all_boards(only_lagging=True)

    assert result["requested_total"] == 2
    assert result["total"] == 1
    assert result["lagging_total"] == 1
    assert result["only_lagging"] is True
    assert calls == ["BK0002"]


def test_update_all_boards_only_lagging_skips_source_when_no_debt(monkeypatch, tmp_path):
    import data_update_manager as dum

    target = "20260803"
    db_path = tmp_path / "kline.db"
    _make_board_meta(db_path, target, stale=False)
    monkeypatch.setattr(dum, "_LEDGER_DB", db_path)
    monkeypatch.setattr("data.board_api.get_last_trading_date", lambda: target)
    monkeypatch.setattr(
        dum,
        "_load_classified_boards",
        lambda *_args: [
            ("industry", "Fresh", "BK0001"),
            ("concept", "Fresh2", "BK0002"),
        ],
    )
    monkeypatch.setattr(
        dum,
        "_get_tushare_pro",
        lambda: (_ for _ in ()).throw(AssertionError("source must not be queried")),
    )

    result = dum.update_all_boards(only_lagging=True)

    assert result["skipped"] is True
    assert result["completion_ready"] is True
    assert result["total"] == 0
