import pandas as pd

from data.global_index_kline import resample_kline


def _daily_df():
    return pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28", "2026-07-29"],
        "open": [10, 11, 12],
        "high": [12, 13, 14],
        "low": [9, 10, 11],
        "close": [11, 12, 13],
        "volume": [100, 200, 300],
    })


def test_resample_weekly_clamps_incomplete_period_to_last_observed_date():
    out = resample_kline(_daily_df(), "weekly")
    assert len(out) == 1
    assert out.iloc[0]["date"] == "2026-07-29"
    assert out.iloc[0]["open"] == 10
    assert out.iloc[0]["high"] == 14
    assert out.iloc[0]["low"] == 9
    assert out.iloc[0]["close"] == 13
    assert out.iloc[0]["volume"] == 600


def test_resample_monthly_clamps_incomplete_period_to_last_observed_date():
    out = resample_kline(_daily_df(), "monthly")
    assert len(out) == 1
    assert out.iloc[0]["date"] == "2026-07-29"
