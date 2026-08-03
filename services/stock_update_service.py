"""Low-level helpers for the QMT stock daily-update pipeline.

The public manager historically exposed a handful of small helpers which are
also useful in isolation (for example when building a pending ledger or
refreshing ``kline_meta``).  This module owns those helpers without importing
``data_update_manager``.  The manager keeps thin compatibility facades and
supplies its runtime seams through :class:`StockUpdateDependencies`, so tests
and callers which patch the old manager names continue to work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import pandas as pd

from services.kline_quality_service import scan_daily_frame as _scan_daily_frame


logger = logging.getLogger("data_update")


def target_trade_day_str(now: Callable[[], datetime] = datetime.now) -> str:
    """Return the local calendar day as ``YYYYMMDD``.

    The manager historically returned this value with no exchange-calendar
    adjustment; callers decide whether the current day is a trading session.
    ``now`` is injectable for deterministic tests.
    """

    return now().strftime("%Y%m%d")


def classify_stock_daily_status(
    max_date_str,
    row_count,
    target_norm,
    quality_report=None,
):
    """Classify a stock's daily ledger state.

    Historical sparsity is maintained by the bounded history-repair cursor and
    therefore does not block target-day settlement.  A quality report only
    blocks selection when it carries an explicit ``blocking_failure`` flag.
    """

    if quality_report and quality_report.get("blocking_failure"):
        return "repair_pending"
    if max_date_str is None or row_count == 0:
        return "date_lag"
    normalized = str(max_date_str).replace("-", "")
    if normalized != str(target_norm):
        return "date_lag"
    return "up_to_date"


def build_stock_pending_from_ledger(
    stocks,
    max_dates,
    row_counts,
    target_norm,
    quality_reports=None,
    classify=classify_stock_daily_status,
):
    """Build the pending stock list from ledger metadata.

    ``classify`` is injectable so the manager facade can preserve a historical
    monkeypatch seam without making this module import the manager.
    """

    pending = []
    skipped_up_to_date = 0
    pending_date_lag = 0
    pending_sparse = 0
    pending_repair = 0
    repair_pending_codes = []
    quality_reports = quality_reports or {}
    for code, name in stocks:
        status = classify(
            max_dates.get(code),
            row_counts.get(code, 0),
            target_norm,
            quality_reports.get(code),
        )
        if status == "up_to_date":
            skipped_up_to_date += 1
        elif status == "date_lag":
            pending.append((code, name))
            pending_date_lag += 1
        elif status == "sparse":
            pending.append((code, name))
            pending_sparse += 1
        elif status == "repair_pending":
            pending.append((code, name))
            pending_repair += 1
            repair_pending_codes.append(code)
    return {
        "pending": pending,
        "skipped_up_to_date": skipped_up_to_date,
        "pending_date_lag": pending_date_lag,
        "pending_sparse": pending_sparse,
        "pending_repair": pending_repair,
        "repair_pending_codes": repair_pending_codes,
        "total": len(stocks),
    }


def scan_daily_quality_cursor(cur, codes, scanner=_scan_daily_frame):
    """Read selected daily rows and run the local quality scanner."""

    reports = {}
    for code in codes:
        cur.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM kline WHERE code=? AND period='daily' ORDER BY date",
            (code,),
        )
        rows = cur.fetchall()
        frame = pd.DataFrame(
            rows,
            columns=["date", "open", "high", "low", "close", "volume"],
        )
        reports[code] = scanner(frame, code=code)
    return reports


def spot_trade_date(row) -> str:
    """Extract an eight-digit trade date from a QMT spot row."""

    raw = str((row or {}).get("time") or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8 and digits[:2] in {"19", "20"}:
        return digits[:8]
    return ""


def valid_settlement_row(row) -> bool:
    """Validate OHLCV values before writing a target-day settlement bar."""

    try:
        open_ = float(row.get("open", 0) or 0)
        high = float(row.get("high", 0) or 0)
        low = float(row.get("low", 0) or 0)
        close = float(row.get("close", 0) or 0)
        volume = float(row.get("volume", 0) or 0)
        return (
            open_ > 0
            and high > 0
            and low > 0
            and close > 0
            and high >= max(open_, low, close)
            and low <= min(open_, high, close)
            and volume >= 0
        )
    except (TypeError, ValueError, AttributeError):
        return False


def verify_no_bar_candidates(
    codes,
    target_norm,
    *,
    ensure_tushare_token: Optional[Callable[[], bool]] = None,
    get_tushare_pro: Optional[Callable[[], Any]] = None,
    logger_: logging.Logger = logger,
):
    """Return ``{code: traded_on_target_day}`` for stale QMT rows.

    ``None`` means that the independent verification source was unavailable;
    callers must keep those codes pending rather than treating them as
    suspended.  Both token and client factories are injected to keep the
    manager's existing patch points intact.
    """

    codes = [str(code) for code in codes if code]
    if not codes:
        return {}
    try:
        if ensure_tushare_token is None:
            from core.env_bootstrap import ensure_tushare_token as token_check

            ensure_tushare_token = token_check
        if not ensure_tushare_token():
            return None
        if get_tushare_pro is None:
            raise RuntimeError("Tushare client factory unavailable")
        pro = get_tushare_pro()
        frame = pro.daily(
            trade_date=str(target_norm).replace("-", "")[:8],
            fields="ts_code",
        )
        if frame is None:
            return None
        values = frame.get("ts_code", pd.Series(dtype=str))
        traded = {str(value).split(".")[0] for value in values.dropna()}
        return {code: code in traded for code in codes}
    except Exception as exc:
        logger_.warning("[QMT个股日更] 无bar校验失败: %s", exc)
        return None


def refresh_daily_meta_cursor(cur, codes, updated_at):
    """Refresh daily ``kline_meta`` rows from SQLite truth."""

    codes = [str(code) for code in codes if code]
    if not codes:
        return
    cur.execute(
        """CREATE TABLE IF NOT EXISTS kline_meta (
            code TEXT NOT NULL, period TEXT NOT NULL, rows INTEGER,
            first_date TEXT, last_date TEXT, updated_at TEXT,
            PRIMARY KEY (code, period)
        )"""
    )
    placeholders = ",".join("?" for _ in codes)
    cur.execute(
        f"""INSERT OR REPLACE INTO kline_meta
            (code, period, rows, first_date, last_date, updated_at)
            SELECT code, 'daily', COUNT(*), MIN(date), MAX(date), ?
            FROM kline
            WHERE code IN ({placeholders}) AND period='daily'
            GROUP BY code""",
        [updated_at] + codes,
    )


@dataclass(frozen=True)
class StockUpdateDependencies:
    """Runtime seams for :class:`StockUpdateService`.

    ``path_factory`` and ``db_path`` are intentionally carried here even
    though this first extraction only operates on an existing cursor.  They
    provide a stable seam for the next stock-update slice without hiding the
    manager's data path in this service.
    """

    now: Callable[[], datetime] = datetime.now
    scan_daily_frame: Callable[..., Mapping[str, Any]] = _scan_daily_frame
    classify_stock_daily_status: Callable[..., str] = classify_stock_daily_status
    ensure_tushare_token: Optional[Callable[[], bool]] = None
    get_tushare_pro: Optional[Callable[[], Any]] = None
    logger: logging.Logger = logger
    path_factory: Callable[..., Path] = Path
    db_path: Optional[str] = None
    spot_trade_date: Callable[[Any], str] = spot_trade_date
    valid_settlement_row: Callable[[Any], bool] = valid_settlement_row


class StockUpdateService:
    """Small, dependency-injected stock update operations."""

    def __init__(self, dependencies: Optional[StockUpdateDependencies] = None, **overrides):
        if dependencies is None:
            dependencies = StockUpdateDependencies(**overrides)
        elif overrides:
            raise TypeError("pass either dependencies or dependency overrides")
        self.dependencies = dependencies

    def target_trade_day_str(self) -> str:
        return target_trade_day_str(self.dependencies.now)

    def classify_stock_daily_status(self, max_date_str, row_count, target_norm, quality_report=None):
        return self.dependencies.classify_stock_daily_status(
            max_date_str, row_count, target_norm, quality_report
        )

    def build_stock_pending_from_ledger(
        self, stocks, max_dates, row_counts, target_norm, quality_reports=None
    ):
        return build_stock_pending_from_ledger(
            stocks,
            max_dates,
            row_counts,
            target_norm,
            quality_reports,
            classify=self.dependencies.classify_stock_daily_status,
        )

    def scan_daily_quality_cursor(self, cur, codes):
        return scan_daily_quality_cursor(cur, codes, self.dependencies.scan_daily_frame)

    def spot_trade_date(self, row) -> str:
        return self.dependencies.spot_trade_date(row)

    def valid_settlement_row(self, row) -> bool:
        return self.dependencies.valid_settlement_row(row)

    def verify_no_bar_candidates(self, codes, target_norm):
        return verify_no_bar_candidates(
            codes,
            target_norm,
            ensure_tushare_token=self.dependencies.ensure_tushare_token,
            get_tushare_pro=self.dependencies.get_tushare_pro,
            logger_=self.dependencies.logger,
        )

    def refresh_daily_meta_cursor(self, cur, codes, updated_at):
        return refresh_daily_meta_cursor(cur, codes, updated_at)


# Historical service-local names are useful to focused tests and diagnostics.
_target_trade_day_str = target_trade_day_str
_scan_daily_quality_cursor = scan_daily_quality_cursor
_spot_trade_date = spot_trade_date
_valid_settlement_row = valid_settlement_row
_verify_no_bar_candidates = verify_no_bar_candidates
_refresh_daily_meta_cursor = refresh_daily_meta_cursor


__all__ = [
    "StockUpdateDependencies",
    "StockUpdateService",
    "target_trade_day_str",
    "classify_stock_daily_status",
    "build_stock_pending_from_ledger",
    "scan_daily_quality_cursor",
    "spot_trade_date",
    "valid_settlement_row",
    "verify_no_bar_candidates",
    "refresh_daily_meta_cursor",
]
