"""Global index historical K-line loader.

This module owns the top-bar non-A-share index history path.  Spot quotes are
handled elsewhere; this file only deals with historical bars and persistence.
"""
import logging
from urllib.parse import quote

import pandas as pd
import requests

from data.kline_resample import resample_ohlcv
from data.sqlite_repo import get_sqlite_repo


logger = logging.getLogger("global_index_kline")

RESAMPLE_PERIODS = {"weekly", "monthly", "quarterly", "yearly"}

TENCENT_SYMBOLS = {
    "HSI": "hkHSI",
    "HSTECH": "hkHSTECH",
}

YAHOO_SYMBOLS = {
    "HSI": "^HSI",
    "HSTECH": "^HSTECH",
    "^N225": "^N225",
    "^KS11": "^KS11",
    "^TWII": "^TWII",
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
}


def load_global_index_kline(code: str, period: str = "daily") -> pd.DataFrame:
    """Load global index bars from SQLite, fetching and persisting on miss."""
    code = str(code or "").strip()
    period = str(period or "daily").strip()
    if not code:
        return pd.DataFrame()

    repo = get_sqlite_repo()

    if period in RESAMPLE_PERIODS:
        cached = repo.read_kline(code, period)
        if cached is not None and not cached.empty:
            return _dedupe(cached)

        daily = load_global_index_kline(code, "daily")
        if daily is None or daily.empty:
            return pd.DataFrame()

        out = resample_kline(daily, period)
        if not out.empty:
            repo.save_kline(code, period, out)
        return out

    cached = repo.read_kline(code, "daily")
    if cached is not None and not cached.empty:
        return _dedupe(cached)

    df = fetch_tencent_global_kline(code)
    if df is None or df.empty:
        df = fetch_yahoo_global_kline(code)
    if df is None or df.empty:
        return pd.DataFrame()

    df = _dedupe(df)
    repo.save_kline(code, "daily", df)
    return df


def resample_kline(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Resample daily OHLCV bars into week/month/quarter/year periods."""
    return _dedupe(resample_ohlcv(df, period))


def fetch_tencent_global_kline(code: str) -> pd.DataFrame:
    """Fetch Hong Kong index daily bars from Tencent."""
    symbol = TENCENT_SYMBOLS.get(str(code or "").strip())
    if not symbol:
        return pd.DataFrame()
    try:
        response = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{symbol},day,,,800,qfq"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        node = ((response.json().get("data") or {}).get(symbol) or {})
        rows = node.get("qfqday") or node.get("day") or []
        out = []
        for row in rows:
            if len(row) < 6:
                continue
            out.append({
                "date": row[0],
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
            })
        return _clean_http_kline(pd.DataFrame(out))
    except Exception as exc:
        logger.warning("[GlobalKline] Tencent %s failed: %s", code, exc)
        return pd.DataFrame()


def fetch_yahoo_global_kline(code: str) -> pd.DataFrame:
    """Fetch global index daily bars from Yahoo chart API."""
    symbol = YAHOO_SYMBOLS.get(str(code or "").strip())
    if not symbol:
        return pd.DataFrame()
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
            params={
                "range": "10y",
                "interval": "1d",
                "events": "history",
                "includeAdjustedClose": "true",
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [])
        if not result:
            return pd.DataFrame()

        item = result[0]
        timestamps = item.get("timestamp") or []
        quote_data = (((item.get("indicators") or {}).get("quote") or [{}])[0]) or {}
        rows = []
        for index, ts in enumerate(timestamps):
            close = _list_get(quote_data.get("close"), index)
            if close is None:
                continue
            rows.append({
                "date": pd.to_datetime(int(ts), unit="s").strftime("%Y-%m-%d"),
                "open": _list_get(quote_data.get("open"), index),
                "high": _list_get(quote_data.get("high"), index),
                "low": _list_get(quote_data.get("low"), index),
                "close": close,
                "volume": _list_get(quote_data.get("volume"), index) or 0,
            })
        return _clean_http_kline(pd.DataFrame(rows))
    except Exception as exc:
        logger.warning("[GlobalKline] Yahoo %s failed: %s", code, exc)
        return pd.DataFrame()


def _list_get(values, index):
    try:
        return values[index]
    except Exception:
        return None


def _clean_http_kline(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "date" not in out.columns:
        return pd.DataFrame()
    out = out.dropna(subset=["date", "close"])
    if out.empty:
        return pd.DataFrame()
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out["open"] = out["open"].fillna(out["close"])
    out["high"] = out["high"].fillna(out["close"])
    out["low"] = out["low"].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0)
    return _dedupe(out[["date", "open", "high", "low", "close", "volume"]])


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out["_sort"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["_sort"]).sort_values("_sort")
    out = out.drop_duplicates(subset=["date"], keep="last")
    out["date"] = out["_sort"].dt.strftime("%Y-%m-%d")
    return out.drop(columns="_sort").reset_index(drop=True)
