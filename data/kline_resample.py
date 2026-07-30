"""Shared OHLCV resampling helpers."""
import pandas as pd


RESAMPLE_RULES = {
    "weekly": "W-FRI",
    "monthly": "ME",
    "quarterly": "QE",
    "yearly": "YE",
}


def resample_ohlcv(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Resample daily OHLCV rows while clamping incomplete periods to last data date."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df.copy()
    if "date" not in out.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    if out.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    last_obs = out["date"].max()
    rule = RESAMPLE_RULES.get(str(period).lower(), period)
    resampled = (
        out.set_index("date")
        .resample(rule)
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna(subset=["open"])
        .reset_index()
    )
    if resampled.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    future = resampled["date"] > last_obs
    if future.any():
        resampled.loc[future, "date"] = last_obs
    resampled = resampled.drop_duplicates(subset=["date"], keep="last")
    resampled = resampled.sort_values("date").reset_index(drop=True)
    resampled["date"] = resampled["date"].dt.strftime("%Y-%m-%d")
    return resampled[["date", "open", "high", "low", "close", "volume"]]
