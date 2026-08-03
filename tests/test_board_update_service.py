"""Focused tests for the dependency-injected board update service."""

import json
import sqlite3
from pathlib import Path

import pandas as pd

from services.board_update_service import (
    BoardUpdateDependencies,
    BoardUpdateService,
    append_board_row_csv,
    load_classified_boards,
    normalize_board_update_rows,
    write_board_rows_sqlite,
)


def _raw_board(code="BK1001"):
    return pd.DataFrame(
        [
            {
                "ts_code": f"{code}.DC",
                "trade_date": "20260722",
                "open": 10,
                "close": 11,
                "high": 12,
                "low": 9,
                "vol": 100,
                "amount": 1000,
                "pct_change": 10,
            }
        ]
    )


def _deps(**overrides):
    values = {
        "get_tushare_pro": lambda: None,
        "update_status": lambda mutator: None,
    }
    values.update(overrides)
    return BoardUpdateDependencies(**values)


def test_append_csv_is_idempotent_for_existing_header(tmp_path):
    path = tmp_path / "industry" / "board.csv"
    path.parent.mkdir(parents=True)
    path.write_text(
        "日期,开盘,收盘,最高,最低,涨跌幅,涨跌额,成交量,成交额,振幅,换手率\n",
        encoding="utf-8-sig",
    )
    row = {
        "open": 10,
        "close": 11,
        "high": 12,
        "low": 9,
        "vol": 100,
        "amount": 1000,
    }

    assert append_board_row_csv(path, "2026-07-22", row) is True
    assert append_board_row_csv(path, "2026-07-22", {**row, "close": 12}) is True

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 2
    assert lines[1].split(",")[0] == "2026-07-22"
    assert float(lines[1].split(",")[2]) == 12


def test_normalize_filters_code_and_keeps_csv_extras():
    raw = pd.concat([_raw_board("BK1001"), _raw_board("BK1002")], ignore_index=True)

    rows = normalize_board_update_rows(raw, "BK1001")

    assert rows["date"].tolist() == ["2026-07-22"]
    assert rows["volume"].tolist() == [100]
    assert rows["vol"].tolist() == [100]
    assert rows["amount"].tolist() == [1000]
    assert rows["pct_change"].tolist() == [10]


def test_write_sqlite_creates_kline_and_meta_from_db_truth(tmp_path):
    db_path = tmp_path / "cache" / "kline.db"
    rows = normalize_board_update_rows(_raw_board(), "BK1001")

    write_board_rows_sqlite(db_path, "BK1001", rows)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT date, close FROM kline WHERE code=? AND period='daily'",
            ("BK1001",),
        ).fetchone() == ("2026-07-22", 11.0)
        assert conn.execute(
            "SELECT rows, first_date, last_date FROM kline_meta WHERE code=?",
            ("BK1001",),
        ).fetchone() == (1, "2026-07-22", "2026-07-22")
    finally:
        conn.close()


def test_load_classified_boards_supports_nested_schema(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "board_classification.json").write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "subcategories": [
                            {
                                "boards": [
                                    {"type": "industry", "name": "行业A", "code": "BK1001"},
                                    {"type": "concept", "name": "概念B", "code": "BK2001"},
                                ]
                            }
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_classified_boards(lambda name: tmp_path / name, ("industry",)) == [
        ("industry", "行业A", "BK1001")
    ]


def test_single_board_success_uses_injected_persistence_and_status(monkeypatch, tmp_path):
    import data_loader

    monkeypatch.setattr(data_loader, "DATA_ROOT", tmp_path / "data")
    appended = []
    written = []
    statuses = []

    def append(path, date, row):
        appended.append((Path(path), date, row["close"]))
        return True

    def write(path, code, rows):
        written.append((Path(path), code, len(rows)))

    def update_status(mutator):
        status = {}
        mutator(status)
        statuses.append(status)

    service = BoardUpdateService(
        _deps(append_board_row_csv=append, write_board_rows_sqlite=write, update_status=update_status)
    )
    result = service.update_single_board("industry", "行业A", "BK1001", raw_override=_raw_board())

    assert result is True
    assert appended == [(tmp_path / "data" / "行业板块K线数据" / "行业A_BK1001.csv", "2026-07-22", 11)]
    assert written == [(tmp_path / "data" / "kline.db", "BK1001", 1)]
    assert statuses[0]["boards"]["BK1001"]["status"] == "success"


def test_single_board_empty_does_not_persist_or_update_status(tmp_path):
    calls = []
    service = BoardUpdateService(
        _deps(
            update_status=lambda mutator: calls.append("status"),
            append_board_row_csv=lambda *args: calls.append("csv"),
            write_board_rows_sqlite=lambda *args: calls.append("sqlite"),
        )
    )

    assert service.update_single_board("concept", "空板", "BK0000", raw_override=pd.DataFrame()) is None
    assert calls == []


def test_single_board_failure_records_failure_status(monkeypatch, tmp_path):
    import data_loader

    monkeypatch.setattr(data_loader, "DATA_ROOT", tmp_path / "data")
    statuses = []

    def update_status(mutator):
        status = {}
        mutator(status)
        statuses.append(status)

    def fail_append(*args):
        raise OSError("csv unavailable")

    service = BoardUpdateService(
        _deps(append_board_row_csv=fail_append, update_status=update_status)
    )

    assert service.update_single_board("industry", "行业A", "BK1001", raw_override=_raw_board()) is False
    assert statuses[0]["boards"]["BK1001"]["status"] == "failed"
    assert "csv unavailable" in statuses[0]["boards"]["BK1001"]["error"]


def test_failed_board_retry_uses_injected_status_and_sleep_seams():
    attempts = []
    sleeps = []
    outcomes = iter([False, True])

    def update_single(*args, **kwargs):
        attempts.append(args[-1])
        return next(outcomes)

    service = BoardUpdateService(
        _deps(
            load_status=lambda: {
                "boards": {
                    "BK1001": {"status": "failed"},
                    "BK2001": {"status": "success"},
                }
            },
            load_classified_boards=lambda include_types: [
                ("industry", "行业A", "BK1001"),
            ],
            update_single_board=update_single,
            sleep=sleeps.append,
            random_uniform=lambda low, high: 0.4,
        )
    )

    result = service.update_failed_boards(max_retries=2)

    assert result == {"success": 1, "failed": 0, "total": 1}
    assert attempts == ["BK1001", "BK1001"]
    assert sleeps == [1.0, 0.4]


def test_all_board_orchestration_injects_lagging_cancel_progress_and_claim():
    raw = pd.DataFrame(
        [
            {"ts_code": "BK1001.DC", "trade_date": "20260803"},
            {"ts_code": "BK2001.DC", "trade_date": "20260803"},
        ]
    )
    updated = []
    statuses = []
    progress = []
    claims = []
    cancel_calls = []

    def update_status(mutator):
        state = {"boards": {"BK9999": {"status": "success"}}}
        mutator(state)
        statuses.append(state)

    def cancel():
        cancel_calls.append(True)
        return len(cancel_calls) > 1

    service = BoardUpdateService(
        _deps(
            get_tushare_pro=lambda: type(
                "Pro", (), {"dc_daily": lambda self, **kwargs: raw}
            )(),
            update_status=update_status,
            load_classified_boards=lambda include_types: [
                ("industry", "行业A", "BK1001"),
                ("concept", "概念B", "BK2001"),
            ],
            lagging_board_codes=lambda boards, target: {"BK1001", "BK2001"},
            update_single_board=lambda board_type, name, code, **kwargs: updated.append(
                code
            )
            or True,
            get_last_trading_date=lambda: "20260803",
            now=lambda: pd.Timestamp("2026-08-04").to_pydatetime(),
            claim_update=lambda: claims.append("claim") or True,
            release_update=lambda: claims.append("release"),
            progress=lambda *args: progress.append(args),
            cancel=cancel,
        )
    )

    result = service.update_all_boards(only_lagging=True)

    assert result["canceled"] is True
    assert result["success"] == 1
    assert result["total"] == 2
    assert updated == ["BK1001"]
    assert claims == ["claim", "release"]
    assert progress[0][:3] == ("boards", 1, 2)
    assert statuses[0]["boards"]["BK1001"]["status"] == "success"
