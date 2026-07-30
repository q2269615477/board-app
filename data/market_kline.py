"""SQLite-backed stock and index K-line loaders."""
from __future__ import annotations

import pandas as pd

from data.kline_resample import resample_ohlcv
from data.sqlite_repo import get_sqlite_repo


RESAMPLE_PERIODS = {"weekly", "monthly", "quarterly", "yearly"}


def load_stock_kline(code: str, period: str = "daily") -> pd.DataFrame:
    """Load cached stock K-line rows; resample from daily on higher-period miss."""
    return _load_cached_kline(str(code or "").strip(), period)


def load_stock_data(code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
    """Load cached daily stock rows with optional date bounds."""
    df = _read(str(code or "").strip(), "daily")
    return _filter_dates(df, start_date, end_date)


def load_index_kline(code: str, period: str = "daily") -> pd.DataFrame:
    """Load cached A-share index K-line rows."""
    return _load_cached_kline(str(code or "").strip(), period)


def load_hk_index_kline(symbol: str, period: str = "daily") -> pd.DataFrame:
    """Load cached Hong Kong index K-line rows."""
    return _load_cached_kline(str(symbol or "").strip(), period)


def load_hk_kline(symbol: str, period: str = "daily") -> pd.DataFrame:
    """Load cached Hong Kong stock rows; symbols are stored as five digits."""
    return _load_cached_kline(str(symbol or "").zfill(5), period)


def _load_cached_kline(code: str, period: str = "daily") -> pd.DataFrame:
    if not code:
        return pd.DataFrame()
    period = str(period or "daily").strip()
    cached = _read(code, period)
    if cached is not None and not cached.empty:
        return cached
    if period in RESAMPLE_PERIODS:
        daily = _read(code, "daily")
        if daily is not None and not daily.empty:
            out = resample_ohlcv(daily, period)
            if out is not None and not out.empty:
                _save(code, period, out)
            return out
    return pd.DataFrame()


def _read(code: str, period: str) -> pd.DataFrame:
    df = get_sqlite_repo().read_kline(code, period)
    return df if df is not None else pd.DataFrame()


def _save(code: str, period: str, df: pd.DataFrame):
    get_sqlite_repo().save_kline(code, period, df)


def _filter_dates(df: pd.DataFrame, start_date: str = "", end_date: str = "") -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["date"] = out["date"].astype(str).str[:10]
    if start_date:
        out = out[out["date"] >= str(start_date)[:10]]
    if end_date:
        out = out[out["date"] <= str(end_date)[:10]]
    return out.reset_index(drop=True)
