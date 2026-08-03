"""Local daily K-line quality checks.

This module does not call the network and does not infer that a long gap means
停牌. It only reports suspicious data that needs repair or confirmation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


OHLC_COLUMNS = ("open", "high", "low", "close")
DEFAULT_GAP_CALENDAR_DAYS = 30
DEFAULT_GAP_BUSINESS_DAYS = 15


def _date_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _business_days_between(start: str, end: str) -> int:
    if not start or not end:
        return 0
    try:
        left = pd.Timestamp(start).normalize()
        right = pd.Timestamp(end).normalize()
        if right <= left:
            return 0
        return max(0, len(pd.bdate_range(left + pd.Timedelta(days=1), right)) - 1)
    except Exception:
        return 0


def scan_daily_frame(
    df: Optional[pd.DataFrame],
    *,
    code: str = "",
    expected_trade_dates: Optional[Iterable[str]] = None,
    gap_calendar_days: int = DEFAULT_GAP_CALENDAR_DAYS,
    gap_business_days: int = DEFAULT_GAP_BUSINESS_DAYS,
) -> dict:
    """Return a serializable report for one daily frame.

    suspicious_gaps means "needs repair or confirmation", not "confirmed
    suspension". If a trading calendar is supplied, missing calendar dates
    inside the observed range are reported as suspicious too.
    """
    empty = {
        "code": code,
        "first_date": None,
        "last_date": None,
        "rows": 0,
        "abnormal_ohlc": [],
        "suspicious_gaps": [],
        "blocking_failure": True,
        "repair_pending": True,
        "status": "empty",
    }
    if df is None or df.empty:
        return empty

    work = df.copy()
    if "date" not in work.columns:
        return {
            **empty,
            "abnormal_ohlc": [{"reason": "missing_date_column"}],
            "status": "invalid",
        }

    work["_date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    abnormal = []
    bad_dates = int(work["_date"].isna().sum())
    if bad_dates:
        abnormal.append({"reason": "invalid_date", "rows": bad_dates})

    valid = work.loc[work["_date"].notna()].copy()
    for column in OHLC_COLUMNS + ("volume",):
        if column not in valid.columns:
            abnormal.append({"reason": "missing_column", "column": column})
            continue
        valid[column] = pd.to_numeric(valid[column], errors="coerce")
        invalid = int(valid[column].isna().sum())
        if invalid:
            abnormal.append({"reason": "non_numeric", "column": column, "rows": invalid})

    numeric = valid.dropna(subset=list(OHLC_COLUMNS)).copy()
    if not numeric.empty:
        non_positive = (numeric[list(OHLC_COLUMNS)] <= 0).any(axis=1)
        if non_positive.any():
            abnormal.append({"reason": "non_positive_ohlc", "rows": int(non_positive.sum())})
        invalid_range = (
            (numeric["high"] < numeric["low"])
            | (numeric["open"] < numeric["low"])
            | (numeric["open"] > numeric["high"])
            | (numeric["close"] < numeric["low"])
            | (numeric["close"] > numeric["high"])
        )
        if invalid_range.any():
            abnormal.append({"reason": "ohlc_out_of_range", "rows": int(invalid_range.sum())})
    if "volume" in valid.columns:
        negative_volume = valid["volume"].notna() & (valid["volume"] < 0)
        if negative_volume.any():
            abnormal.append({"reason": "negative_volume", "rows": int(negative_volume.sum())})

    dates = sorted(set(valid["_date"].dt.strftime("%Y-%m-%d")))
    duplicate_rows = len(valid) - len(dates)
    if duplicate_rows:
        abnormal.append({"reason": "duplicate_date", "rows": int(duplicate_rows)})

    gaps = []
    for left, right in zip(dates, dates[1:]):
        calendar_days = (pd.Timestamp(right) - pd.Timestamp(left)).days
        business_days = _business_days_between(left, right)
        if calendar_days >= gap_calendar_days or business_days >= gap_business_days:
            gaps.append({
                "from": left,
                "to": right,
                "calendar_days": int(calendar_days),
                "business_days": int(business_days),
                "reason": "suspicious_gap_unexplained",
            })

    if expected_trade_dates is not None and dates:
        expected = sorted({_date_text(value) for value in expected_trade_dates if _date_text(value)})
        observed = set(dates)
        missing = [value for value in expected if dates[0] < value < dates[-1] and value not in observed]
        if missing:
            gaps.append({
                "from": missing[0],
                "to": missing[-1],
                "missing_trade_dates": missing,
                "reason": "suspicious_missing_trade_dates",
            })

    first_date = dates[0] if dates else None
    last_date = dates[-1] if dates else None
    # A long gap can be a suspension or a real source gap.  Keep it visible,
    # but do not block the whole daily pipeline without a trading calendar
    # proving that bars are missing. Invalid rows and calendar-confirmed
    # missing dates are hard failures.
    confirmed_gap = any(
        item.get("reason") == "suspicious_missing_trade_dates"
        for item in gaps
    )
    blocking_failure = bool(abnormal or confirmed_gap or not dates)
    repair_pending = bool(blocking_failure or gaps)
    return {
        "code": code,
        "first_date": first_date,
        "last_date": last_date,
        "rows": int(len(df)),
        "abnormal_ohlc": abnormal,
        "suspicious_gaps": gaps,
        "blocking_failure": blocking_failure,
        "repair_pending": repair_pending,
        "status": "suspect" if repair_pending else "ok",
    }


def scan_daily_quality(
    db_path: str | Path,
    *,
    codes: Optional[Iterable[str]] = None,
    expected_trade_dates: Optional[Iterable[str]] = None,
) -> dict:
    """Scan SQLite daily rows without modifying the database."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        params = []
        where = "period='daily'"
        selected = [str(code) for code in codes] if codes is not None else None
        if selected:
            placeholders = ",".join("?" for _ in selected)
            where += f" AND code IN ({placeholders})"
            params.extend(selected)
        rows = conn.execute(
            f"SELECT code, date, open, high, low, close, volume "
            f"FROM kline WHERE {where} ORDER BY code, date",
            params,
        ).fetchall()
    finally:
        conn.close()

    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row[1:])
    reports = {}
    for code, values in grouped.items():
        reports[code] = scan_daily_frame(
            pd.DataFrame(values, columns=["date", "open", "high", "low", "close", "volume"]),
            code=code,
            expected_trade_dates=expected_trade_dates,
        )
    if selected:
        for code in selected:
            reports.setdefault(code, scan_daily_frame(None, code=code))
    pending = [code for code, report in reports.items() if report["repair_pending"]]
    return {
        "reports": reports,
        "total": len(reports),
        "repair_pending": len(pending),
        "repair_pending_codes": pending,
    }
