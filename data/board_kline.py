"""Board K-line loader.

Owns the industry/concept daily update path: SQLite cache, Tushare/Eastmoney
incremental fetch via ``data.board_api``, and higher-period resampling.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from data.kline_resample import resample_ohlcv
from data.sqlite_repo import get_sqlite_repo


RESAMPLE_PERIODS = {"weekly", "monthly", "quarterly", "yearly"}


def load_board_kline(board_type: str, name: str, code: str, period: str = "daily") -> pd.DataFrame:
    """Load board K-line data with a conservative incremental refresh."""
    code = str(code or "").strip()
    period = str(period or "daily").strip()
    if not code:
        return pd.DataFrame()

    repo = get_sqlite_repo()
    daily = _read(repo, code, "daily")
    last_local = str(daily["date"].max())[:10] if daily is not None and not daily.empty else ""

    if _local_is_fresh_enough(last_local, daily):
        return _period_from_daily(repo, code, period, daily)

    try:
        from data.board_api import get_board_kline
        raw = get_board_kline(board_type, code, start_date=last_local or "20000101")
        if raw is not None and not raw.empty:
            incoming = normalize_board_kline(raw)
            merged = _merge_daily(daily, incoming)
            if not merged.empty:
                repo.save_kline(code, "daily", merged, name=name or code, data_type=board_type)
                return _period_from_daily(repo, code, period, merged)
    except Exception:
        pass

    if daily is not None and not daily.empty:
        return _period_from_daily(repo, code, period, daily)
    return pd.DataFrame()


def normalize_board_kline(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize board API rows into OHLCV plus source-provided turnover amount."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    aliases = {
        "date": ("date", "日期", "鏃ユ湡", "trade_date"),
        "open": ("open", "开盘", "寮€鐩?"),
        "close": ("close", "收盘", "鏀剁洏"),
        "high": ("high", "最高", "鏈€楂?"),
        "low": ("low", "最低", "鏈€浣?"),
        "volume": ("volume", "成交量", "鎴愪氦閲?", "vol"),
        "amount": ("amount", "turnover", "成交额", "成交額"),
    }
    source = df.copy()
    out = pd.DataFrame()
    for target, names in aliases.items():
        col = next((name for name in names if name in source.columns), None)
        if col is not None:
            out[target] = source[col]

    if "date" not in out.columns and len(source.columns) >= 1:
        out["date"] = source.iloc[:, 0]
    # Board API rows are documented as date, open, close, high, low, volume...
    positional = {"open": 1, "close": 2, "high": 3, "low": 4, "volume": 5, "amount": 6}
    for target, index in positional.items():
        if target not in out.columns and len(source.columns) > index:
            out[target] = source.iloc[:, index]

    if "date" not in out.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["date", "open", "high", "low", "close"])
    return out[["date", "open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)


def _read(repo, code: str, period: str) -> pd.DataFrame:
    df = repo.read_kline(code, period)
    return df if df is not None else pd.DataFrame()


def _local_is_fresh_enough(last_local: str, daily: pd.DataFrame) -> bool:
    if not last_local or daily is None or daily.empty:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    if last_local.replace("/", "-") >= today:
        return True
    if datetime.now().weekday() >= 5:
        try:
            lag = (datetime.now().date() - pd.to_datetime(last_local).date()).days
            return lag <= 3
        except Exception:
            return False
    return False


def _period_from_daily(repo, code: str, period: str, daily: pd.DataFrame) -> pd.DataFrame:
    if period == "daily":
        return daily if daily is not None else pd.DataFrame()
    cached = _read(repo, code, period)
    if cached is not None and not cached.empty:
        last_daily = str(daily["date"].max())[:10] if daily is not None and not daily.empty else ""
        last_cached = str(cached["date"].max())[:10]
        if last_cached <= last_daily:
            return cached
    if period in RESAMPLE_PERIODS and daily is not None and not daily.empty:
        out = resample_ohlcv(daily, period)
        if out is not None and not out.empty:
            repo.save_kline(code, period, out)
        return out
    return pd.DataFrame()


def _merge_daily(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if existing is not None and not existing.empty:
        frames.append(existing)
    if incoming is not None and not incoming.empty:
        frames.append(incoming)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["date"] = out["date"].astype(str).str[:10]
    return out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
