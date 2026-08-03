"""Low-level board (industry/concept) update operations.

This module owns the small, side-effecting pieces used by the board update
pipeline: CSV append/deduplication, canonical row normalisation, SQLite
writes, classification traversal, and updating a single board.  The manager
module deliberately supplies its runtime seams (Tushare factory, status
writer, path factory, and the two persistence helpers) through
``BoardUpdateDependencies``.  That keeps existing callers and tests which
patch ``data_update_manager`` working while preventing this service from
depending on the manager in the opposite direction.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd


logger = logging.getLogger("data_update")


def append_board_row_csv(csv_path, date_str, row_data) -> bool:
    """Append one board daily row, preserving either legacy CSV shape.

    Existing files may contain either the eleven-column export format or the
    seven-column legacy format.  Rows are keyed by date and sorted after the
    replacement so a repeated update is idempotent.
    """
    fp = Path(csv_path)
    content = fp.read_text(encoding="utf-8-sig") if fp.exists() else ""
    lines = content.splitlines()
    # With a header, its number of columns determines the output shape.  An
    # empty/new file historically defaults to the 11-column export.
    num_cols = 11
    if lines and lines[0].strip():
        num_cols = len(lines[0].split(","))
    elif not content.strip():
        num_cols = 11

    if num_cols >= 11:
        row = [
            date_str,
            row_data.get("open", 0),
            row_data.get("close", 0),
            row_data.get("high", 0),
            row_data.get("low", 0),
            row_data.get("pct_change", 0),
            0,
            row_data.get("vol", 0),
            row_data.get("amount", 0),
            0,
            0,
        ]
        header = [
            "日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额",
            "成交量", "成交额", "振幅", "换手率",
        ]
    else:
        row = [
            date_str,
            row_data.get("open", 0),
            row_data.get("close", 0),
            row_data.get("high", 0),
            row_data.get("low", 0),
            row_data.get("vol", 0),
            row_data.get("amount", 0),
        ]
        header = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]

    existing_rows = []
    if lines:
        for line in lines[1:]:
            if line.strip():
                existing_rows.append(line.split(","))

    dedup = {}
    for parts in existing_rows:
        if parts:
            dedup[parts[0]] = parts
    dedup[date_str] = [str(value) for value in row]
    sorted_rows = sorted(dedup.values(), key=lambda values: values[0] if values else "")

    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        # Preserve the historical behavior: a truly empty file receives only
        # its first data row, while an existing header is rewritten as-is.
        if lines and lines[0].strip():
            writer.writerow(header)
        for values in sorted_rows:
            writer.writerow(values)
    return True


def normalize_board_update_rows(raw: pd.DataFrame, code: str) -> pd.DataFrame:
    """Return canonical board rows plus CSV-only ``amount``/``pct_change``."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    source = raw.copy()
    if "ts_code" in source.columns:
        symbols = source["ts_code"].astype(str).str.upper()
        code_upper = str(code).upper()
        source = source[
            symbols.eq(f"{code_upper}.DC")
            | symbols.eq(f"{code_upper}.TS")
            | symbols.str.split(".").str[0].eq(code_upper)
        ]
    if source.empty:
        return pd.DataFrame()

    from data.board_kline import normalize_board_kline

    canonical = normalize_board_kline(source)
    if canonical.empty:
        return canonical

    source = source.reset_index(drop=True)
    canonical = canonical.reset_index(drop=True)
    if len(source) == len(canonical):
        amount = source["amount"] if "amount" in source.columns else 0
        pct = source["pct_change"] if "pct_change" in source.columns else 0
        canonical["amount"] = (
            pd.to_numeric(amount, errors="coerce").fillna(0)
            if hasattr(amount, "__len__")
            else float(amount or 0)
        )
        canonical["pct_change"] = (
            pd.to_numeric(pct, errors="coerce").fillna(0)
            if hasattr(pct, "__len__")
            else float(pct or 0)
        )
    else:
        canonical["amount"] = 0
        canonical["pct_change"] = 0
    canonical["vol"] = canonical["volume"]
    canonical = canonical.sort_values("date").drop_duplicates(
        subset=["date"], keep="last"
    )
    return canonical.reset_index(drop=True)


def write_board_rows_sqlite(db_path, code: str, rows: pd.DataFrame) -> None:
    """Write canonical daily board rows and refresh metadata from DB truth."""
    if rows is None or rows.empty:
        return
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS kline (
                code TEXT NOT NULL, period TEXT NOT NULL, date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL, updated_at TEXT,
                PRIMARY KEY (code, period, date)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS kline_meta (
                code TEXT NOT NULL, period TEXT NOT NULL, rows INTEGER,
                first_date TEXT, last_date TEXT, updated_at TEXT,
                PRIMARY KEY (code, period)
            )"""
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(kline)").fetchall()
        }
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE kline ADD COLUMN updated_at TEXT")
        now_db = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = []
        for _, row in rows.iterrows():
            payload.append(
                (
                    code,
                    "daily",
                    str(row["date"])[:10],
                    float(row.get("open", 0) or 0),
                    float(row.get("high", 0) or 0),
                    float(row.get("low", 0) or 0),
                    float(row.get("close", 0) or 0),
                    float(row.get("volume", 0) or 0),
                )
            )
        if not payload:
            return
        conn.executemany(
            "INSERT OR REPLACE INTO kline "
            "(code, period, date, open, high, low, close, volume, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [item + (now_db,) for item in payload],
        )
        conn.execute(
            "INSERT OR REPLACE INTO kline_meta "
            "(code, period, rows, first_date, last_date, updated_at) "
            "SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ? "
            "FROM kline WHERE code=? AND period='daily' GROUP BY code",
            (now_db, code),
        )
        conn.commit()
    finally:
        conn.close()


def load_classified_boards(path_factory=Path, include_types=None):
    """Load board metadata from legacy and nested classification schemas."""
    classification = path_factory("static") / "board_classification.json"
    if not classification.exists():
        raise FileNotFoundError("board_classification.json 不存在")
    with classification.open("r", encoding="utf-8") as handle:
        categories = json.load(handle).get("categories", [])

    allowed = set(include_types or ())
    found = []
    seen = set()

    def visit(node):
        if not isinstance(node, dict):
            return
        for board in node.get("boards", []):
            if not isinstance(board, dict):
                continue
            board_type = str(board.get("type") or "")
            code = str(board.get("code") or "")
            if not code or (allowed and board_type not in allowed):
                continue
            key = (board_type, code)
            if key in seen:
                continue
            seen.add(key)
            found.append((board_type, str(board.get("name") or code), code))
        for child in node.get("subcategories", []):
            visit(child)

    for category in categories:
        visit(category)
    return found


# Keep the implementation helpers discoverable under the manager's historical
# names as well.  The manager facade itself remains the compatibility surface;
# these aliases are useful to focused service tests and diagnostics.
_append_board_row_csv = append_board_row_csv
_normalize_board_update_rows = normalize_board_update_rows
_write_board_rows_sqlite = write_board_rows_sqlite
_load_classified_boards = load_classified_boards


@dataclass(frozen=True)
class BoardUpdateDependencies:
    """Runtime seams supplied by ``data_update_manager``."""

    get_tushare_pro: Callable[[], Any]
    update_status: Callable[[Callable[[dict], None]], Any]
    append_board_row_csv: Callable[..., bool] = append_board_row_csv
    normalize_board_update_rows: Callable[..., pd.DataFrame] = normalize_board_update_rows
    write_board_rows_sqlite: Callable[..., None] = write_board_rows_sqlite
    path_factory: Callable[..., Path] = Path
    logger: logging.Logger = logger
    now: Callable[[], datetime] = datetime.now


class BoardUpdateService:
    """Implement low-level board update behavior without manager imports."""

    def __init__(
        self,
        dependencies: Optional[BoardUpdateDependencies] = None,
        **dependency_overrides,
    ):
        """Create a service from an explicit dependency bundle.

        Keyword overrides are accepted as a small convenience for standalone
        callers; the manager uses the bundle form so every patched seam is
        visible at the construction site.
        """
        if dependencies is None:
            dependencies = BoardUpdateDependencies(**dependency_overrides)
        elif dependency_overrides:
            raise TypeError("pass either dependencies or dependency overrides")
        self.dependencies = dependencies

    def normalize_board_update_rows(self, raw: pd.DataFrame, code: str) -> pd.DataFrame:
        return self.dependencies.normalize_board_update_rows(raw, code)

    def load_classified_boards(self, include_types=None):
        return load_classified_boards(self.dependencies.path_factory, include_types)

    def update_single_board(
        self,
        board_type: str,
        name: str,
        code: str,
        raw_override: Optional[pd.DataFrame] = None,
        record_status: bool = True,
    ):
        """Update one board: True success, None empty, False failure."""
        from data_loader import DATA_ROOT, _safe_filename

        deps = self.dependencies
        now = deps.now
        try:
            deps.logger.info("[板块] 更新 %s(%s)", name, code)
            raw = raw_override
            tushare_checked = raw_override is not None
            if raw_override is None:
                try:
                    pro = deps.get_tushare_pro()
                    tushare_checked = True
                    raw = pro.dc_daily(
                        ts_code=f"{code}.DC",
                        start_date=(now() - timedelta(days=30)).strftime("%Y%m%d"),
                        end_date=now().strftime("%Y%m%d"),
                    )
                except Exception as exc:
                    deps.logger.debug("[板块] Tushare %s 跳过: %s", code, exc)

            rows = self.normalize_board_update_rows(raw, code)

            # Only use the live fallback when the local Tushare factory was
            # unavailable or errored.  An empty dc_daily result is a genuine
            # empty cycle and must not be silently replaced.
            if rows.empty and not tushare_checked:
                try:
                    from data.board_api import get_board_kline

                    rows = self.normalize_board_update_rows(
                        get_board_kline(board_type, code), code
                    )
                except Exception as exc:
                    deps.logger.debug("[板块] fallback %s 跳过: %s", code, exc)

            if rows.empty:
                deps.logger.warning("[板块] %s(%s) 返回空数据", name, code)
                return None

            subdir = DATA_ROOT / (
                "行业板块K线数据" if board_type == "industry" else "概念板块K线数据"
            )
            subdir.mkdir(parents=True, exist_ok=True)
            safe_name = _safe_filename(str(name or code))
            csv_path = subdir / f"{safe_name}_{code}.csv"
            legacy = subdir / f"{name}_{code}.csv"
            if not csv_path.exists() and legacy.exists() and legacy != csv_path:
                csv_path = legacy

            for _, row in rows.iterrows():
                deps.append_board_row_csv(csv_path, str(row["date"])[:10], row.to_dict())

            try:
                deps.write_board_rows_sqlite(DATA_ROOT / "kline.db", code, rows)
            except Exception as exc:
                deps.logger.debug("[板块] SQLite 写 %s 跳过: %s", code, exc)

            def mark_board_success(status):
                status.setdefault("boards", {})[code] = {
                    "last_update": now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "success",
                    "name": name,
                }

            if record_status:
                deps.update_status(mark_board_success)
            deps.logger.info("[板块] %s(%s) 更新成功", name, code)
            return True
        except Exception as exc:
            error_msg = str(exc)[:200]

            def mark_board_failure(status):
                status.setdefault("boards", {})[code] = {
                    "last_update": now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "failed",
                    "error": error_msg,
                    "name": name,
                }

            if record_status:
                deps.update_status(mark_board_failure)
            deps.logger.error("[板块] %s(%s) 更新失败: %s", name, code, error_msg)
            return False


__all__ = [
    "BoardUpdateDependencies",
    "BoardUpdateService",
    "append_board_row_csv",
    "normalize_board_update_rows",
    "write_board_rows_sqlite",
    "load_classified_boards",
]
