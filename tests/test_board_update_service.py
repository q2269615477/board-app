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
