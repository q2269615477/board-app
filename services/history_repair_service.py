"""Staged QMT full-history repair for domestic stocks and indices."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from data.sqlite_repo import SqliteRepo
from services.kline_quality_service import scan_daily_frame
from services.kline_service import KLineService, _overlapping_bars_differ
from services.update_status_store import load_status, save_status


DEFAULT_DB_PATH = Path("data") / "kline.db"
DEFAULT_STATE_PATH = Path("data") / "history_repair_status.json"


def list_repair_codes(db_path: str | Path = DEFAULT_DB_PATH) -> list[str]:
    """Return the deterministic domestic history-maintenance universe."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT code FROM stock_ledger "
            "UNION SELECT DISTINCT code FROM kline "
            "WHERE period='daily' AND "
            "(code LIKE 'sh%' OR code LIKE 'sz%' OR code LIKE 'bj%') "
            "ORDER BY code"
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row and row[0]]


def _data_type(code: str) -> str:
    return "index" if str(code).lower().startswith(("sh", "sz", "bj")) else "stock"


def repair_history_batch(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    limit: int = 25,
    reset: bool = False,
    materialize: bool = True,
    repair_func: Optional[Callable[[str, str, object], object]] = None,
    materialize_func: Optional[Callable[..., dict]] = None,
) -> dict:
    """Repair a bounded slice and persist a cursor for the next daily run."""
    db_path = Path(db_path)
    state_path = Path(state_path)
    codes = list_repair_codes(db_path)
    if not codes:
        return {
            "processed": 0, "repaired": 0, "failed": 0,
            "total_universe": 0, "completion_ready": True,
        }

    state = {} if reset else load_status(state_path)
    cursor = 0 if reset else int(state.get("cursor", 0) or 0)
    if cursor >= len(codes):
        cursor = 0
    count = min(max(1, int(limit)), len(codes))
    selected = codes[cursor:cursor + count]
    if len(selected) < count:
        selected += codes[:count - len(selected)]

    repo = SqliteRepo(db_path=db_path)
    service = KLineService()
    service._db = repo

    if repair_func is None:
        def repair_func(code, data_type, local):
            return service._ensure_complete_history(
                data_type, code, local, force=True
            )

    details = []
    repaired_codes = []
    failed = 0
    for index, code in enumerate(selected):
        try:
            local = repo.read_kline(code, "daily")
            before = 0 if local is None else len(local)
            merged = repair_func(code, _data_type(code), local)
            after = 0 if merged is None else len(merged)
            report = scan_daily_frame(merged, code=code)
            added = max(0, after - before)
            corrected = _overlapping_bars_differ(local, merged)
            if added or corrected:
                repaired_codes.append(code)
            details.append({
                "code": code,
                "before": before,
                "after": after,
                "added": added,
                "corrected": corrected,
                "first_date": report.get("first_date"),
                "last_date": report.get("last_date"),
                "blocking_failure": report.get("blocking_failure", True),
                "suspected_gaps": len(report.get("suspicious_gaps") or []),
            })
        except Exception as exc:
            failed += 1
            details.append({"code": code, "error": str(exc)[:200]})
        checkpoint_cursor = (cursor + index + 1) % len(codes)
        save_status({
            "cursor": checkpoint_cursor,
            "total_universe": len(codes),
            "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_processed": [code],
            "last_repaired": list(repaired_codes),
            "last_failed": failed,
            "cycles_completed": int(state.get("cycles_completed", 0) or 0),
            "status": "running",
        }, state_path)

    materialized = None
    if repaired_codes and materialize:
        if materialize_func is None:
            from data_update_manager import materialize_higher_periods
            materialize_func = materialize_higher_periods
        materialized = materialize_func(codes=repaired_codes)

    next_cursor = (cursor + len(selected)) % len(codes)
    completed_cycle = cursor + len(selected) >= len(codes)
    new_state = {
        "cursor": next_cursor,
        "total_universe": len(codes),
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_processed": selected,
        "last_repaired": repaired_codes,
        "last_failed": failed,
        "cycles_completed": int(state.get("cycles_completed", 0) or 0)
        + (1 if completed_cycle else 0),
        "status": "complete",
    }
    save_status(new_state, state_path)
    return {
        "processed": len(selected),
        "repaired": len(repaired_codes),
        "repaired_codes": repaired_codes,
        "failed": failed,
        "total_universe": len(codes),
        "cursor": next_cursor,
        "completed_cycle": completed_cycle,
        "details": details,
        "materialized": materialized,
        "completion_ready": failed == 0,
    }
