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
import random
import sqlite3
import time
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
    # Orchestration seams.  ``update_all_boards`` and ``update_failed_boards``
    # intentionally receive these from the manager so old monkeypatch points
    # keep working without importing the manager here.
    load_status: Callable[[], dict] = lambda: {}
    save_status: Callable[[dict], Any] = lambda status: None
    load_classified_boards: Callable[..., list] | None = None
    update_single_board: Callable[..., Any] | None = None
    lagging_board_codes: Callable[..., set] | None = None
    get_last_trading_date: Callable[[], Any] | None = None
    sleep: Callable[[float], Any] = time.sleep
    random_uniform: Callable[[float, float], float] = random.uniform
    progress: Callable[..., Any] | None = None
    cancel: Callable[[], bool] | None = None
    claim_update: Callable[[], bool] | None = None
    release_update: Callable[[], Any] | None = None
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
        loader = self.dependencies.load_classified_boards
        if loader is not None:
            return loader(include_types)
        return load_classified_boards(self.dependencies.path_factory, include_types)

    def _update_single_board(self, board_type, name, code, **kwargs):
        updater = self.dependencies.update_single_board
        if updater is not None:
            return updater(board_type, name, code, **kwargs)
        return self.update_single_board(board_type, name, code, **kwargs)

    def _lagging_board_codes(self, boards, target):
        provider = self.dependencies.lagging_board_codes
        if provider is None:
            # A standalone service can still perform the conservative query by
            # accepting a provider from its caller.  There is deliberately no
            # manager import or hidden database path here.
            return {str(code) for _typ, _name, code in boards if code}
        return provider(boards, target)

    def _target_trade_date(self, from_full=False, only_lagging=False):
        now = self.dependencies.now
        target = now().strftime("%Y%m%d")
        if from_full or only_lagging:
            getter = self.dependencies.get_last_trading_date
            if getter is None:
                try:
                    from data.board_api import get_last_trading_date

                    getter = get_last_trading_date
                except Exception:
                    getter = None
            if getter is not None:
                try:
                    candidate = str(getter() or "").replace("-", "")[:8]
                    if candidate:
                        target = candidate
                except Exception:
                    pass
        return target

    def _claim_or_skip(self):
        claim = self.dependencies.claim_update
        if claim is None:
            return True
        if claim():
            return True
        self.dependencies.logger.warning("[板块] 上次更新尚未完成，跳过")
        return False

    def _release(self):
        release = self.dependencies.release_update
        if release is not None:
            release()

    def update_failed_boards(self, max_retries: int = 2, limit: int = 50) -> dict:
        """Retry boards marked ``failed`` in the update status store."""
        deps = self.dependencies
        status = deps.load_status()
        boards_st = status.get("boards") or {}
        failed_codes = [
            code
            for code, value in boards_st.items()
            if isinstance(value, dict) and value.get("status") == "failed"
        ]
        if not failed_codes:
            return {"success": 0, "failed": 0, "total": 0, "message": "无失败板块"}

        try:
            meta = {
                code: (board_type, name, code)
                for board_type, name, code in self.load_classified_boards(
                    ("industry", "concept")
                )
            }
        except Exception:
            return {"success": 0, "failed": 0, "error": "board_classification.json 缺失"}

        targets = [meta[code] for code in failed_codes if code in meta]
        if limit and limit > 0:
            targets = targets[: int(limit)]

        result = {"success": 0, "failed": 0, "total": len(targets)}
        deps.logger.info("[板块重试] 共 %s 个失败项 (limit=%s)", len(targets), limit)
        for board_type, name, code in targets:
            ok = False
            for attempt in range(max_retries):
                ok = self._update_single_board(board_type, name, code)
                if ok:
                    break
                deps.sleep(1.0 + attempt)
            if ok:
                result["success"] += 1
            else:
                result["failed"] += 1
            deps.sleep(deps.random_uniform(0.3, 0.8))
        deps.logger.info(
            "[板块重试] 完成 success=%s failed=%s",
            result["success"],
            result["failed"],
        )
        return result

    def update_all_boards(
        self,
        max_retries: int = 3,
        cancel_check=None,
        from_full=False,
        only_lagging: bool = False,
    ) -> dict:
        """Fetch one target-day snapshot and persist the requested boards."""
        deps = self.dependencies
        if not self._claim_or_skip():
            return {"success": 0, "failed": 0, "total": 0, "error": "上次更新进行中"}

        result = {"success": 0, "failed": 0, "total": 0}
        try:
            boards = self.load_classified_boards(("industry", "concept"))
            requested_total = len(boards)
            requested_codes = {code for _board_type, _name, code in boards}
            result["requested_total"] = requested_total

            target = self._target_trade_date(
                from_full=from_full, only_lagging=only_lagging
            )
            result["only_lagging"] = bool(only_lagging)
            if only_lagging:
                lagging_codes = self._lagging_board_codes(boards, target)
                boards = [board for board in boards if board[2] in lagging_codes]
                result["lagging_total"] = len(boards)
            result["total"] = len(boards)
            deps.logger.info(
                "[板块] 共 %s 个%s",
                len(boards),
                "（分类总数 %s，仅欠更）" % requested_total if only_lagging else "",
            )
            if only_lagging and not boards:
                result.update(
                    {
                        "skipped": True,
                        "target_trade_date": target,
                        "completion_ready": True,
                        "message": "板块日线均已是目标交易日",
                    }
                )
                return result

            pro = deps.get_tushare_pro()
            raw_all = pro.dc_daily(
                trade_date=target,
                start_date=target,
                end_date=target,
            )
            if raw_all is None:
                raw_all = pd.DataFrame()
            if from_full and not raw_all.empty and "trade_date" in raw_all.columns:
                actual_dates = {
                    str(value).replace("-", "")[:8]
                    for value in raw_all["trade_date"].dropna().tolist()
                }
                if actual_dates != {target}:
                    return {
                        **result,
                        "error": (
                            f"Tushare板块日期不匹配 target={target} "
                            f"actual={sorted(actual_dates)}"
                        ),
                        "target_trade_date": target,
                        "completion_ready": False,
                    }

            source_codes = set()
            if not raw_all.empty and "ts_code" in raw_all.columns:
                source_codes = {
                    str(value).upper().split(".")[0]
                    for value in raw_all["ts_code"].dropna().tolist()
                }
            required_codes = {code for _btype, _name, code in boards}
            settled_codes = sorted(required_codes & source_codes)
            unavailable_codes = sorted(required_codes - source_codes)
            coverage = (
                len(settled_codes) / len(required_codes) if required_codes else 0.0
            )
            result.update(
                {
                    "source_coverage": round(coverage, 6),
                    "settled_codes": settled_codes,
                    "unavailable": len(unavailable_codes),
                    "unavailable_codes": unavailable_codes,
                }
            )
            outcomes = {}
            total = len(boards)
            for idx, (board_type, name, code) in enumerate(boards):
                check_cancel = cancel_check or deps.cancel
                if callable(check_cancel) and check_cancel():
                    result["canceled"] = True
                    break
                if code not in source_codes:
                    outcomes[code] = ("unavailable", name)
                    continue
                ok = self._update_single_board(
                    board_type,
                    name,
                    code,
                    raw_override=raw_all,
                    record_status=False,
                )
                if ok is True:
                    result["success"] += 1
                    outcomes[code] = ("success", name)
                elif ok is False:
                    result["failed"] += 1
                    outcomes[code] = ("failed", name)
                else:
                    outcomes[code] = ("unavailable", name)
                if callable(deps.progress):
                    try:
                        deps.progress("boards", idx + 1, total, f"{name}({code})")
                    except Exception as exc:
                        deps.logger.debug("[板块] progress ignored: %s", exc)
                deps.logger.info(
                    "[板块更新] %s/%s %s(%s) %s",
                    idx + 1,
                    total,
                    name,
                    code,
                    "✓" if ok is True else ("✗" if ok is False else "○"),
                )

            if outcomes:
                updated_at = deps.now().strftime("%Y-%m-%d %H:%M:%S")

                def mark_board_batch(status):
                    board_status = status.setdefault("boards", {})
                    valid_codes = (
                        set(requested_codes) if only_lagging else set(required_codes)
                    )
                    for stale_code in list(board_status):
                        if (
                            str(stale_code).startswith("BK")
                            and stale_code not in valid_codes
                        ):
                            board_status.pop(stale_code, None)
                    for board_code, (state, board_name) in outcomes.items():
                        item = {
                            "last_update": updated_at,
                            "status": state,
                            "name": board_name,
                        }
                        if state == "unavailable":
                            item["reason"] = "目标交易日不在Tushare dc_daily活动板块集合"
                        elif state == "failed":
                            item["error"] = "目标交易日写入失败"
                        board_status[board_code] = item

                deps.update_status(mark_board_batch)

            result["target_trade_date"] = target
            result["source_rows"] = len(raw_all)
            source_ready = bool(
                raw_all is not None and not raw_all.empty and coverage >= 0.95
            )
            result["completion_ready"] = (
                not result.get("canceled")
                and source_ready
                and result["failed"] == 0
                and result["success"] == len(settled_codes)
            )
            deps.logger.info(
                "[板块] 完成: 成功=%s, 失败=%s, 共=%s",
                result["success"],
                result["failed"],
                result["total"],
            )
        except Exception as exc:
            deps.logger.error("[板块] 异常: %s", exc)
        finally:
            self._release()

        return result

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
