"""K 线周期不变量：resample 与去重（不依赖 QMT 实盘）"""
import pandas as pd
from services.kline_service import KLineService, RESAMPLE_FROM_1M, normalize_period


def _make_1m_session() -> pd.DataFrame:
    """构造一个交易日 9:30-11:30 + 13:00-15:00 的 1m bar（共 241 根）"""
    morning = pd.date_range("2024-01-02 09:30", "2024-01-02 11:30", freq="1min")
    afternoon = pd.date_range("2024-01-02 13:00", "2024-01-02 15:00", freq="1min")
    idx = morning.append(afternoon)
    n = len(idx)
    return pd.DataFrame(
        {
            "date": idx.strftime("%Y-%m-%d %H:%M"),
            "open": range(n),
            "high": [x + 1 for x in range(n)],
            "low": range(n),
            "close": [x + 0.5 for x in range(n)],
            "volume": [100] * n,
        }
    )


def test_normalize_period_aliases():
    assert normalize_period("1H") == "60m"
    assert normalize_period("2H") == "120m"
    assert normalize_period("4H") == "240m"
    assert normalize_period("5m") == "5m"
    assert normalize_period("daily") == "daily"


def test_resample_5m_from_1m_fewer_bars():
    svc = KLineService.__new__(KLineService)
    df_1m = _make_1m_session()
    assert "5m" in RESAMPLE_FROM_1M
    out = svc._resample_from_1m(df_1m, "5m")
    assert not out.empty
    assert len(out) < len(df_1m)
    # 约 1/5：241 → ~48–60
    assert len(out) <= len(df_1m) // 4
    assert len(out) >= len(df_1m) // 6


def test_resample_15m_ohlc_agg():
    svc = KLineService.__new__(KLineService)
    df_1m = _make_1m_session()
    out = svc._resample_from_1m(df_1m, "15m")
    assert not out.empty
    # 首 bar open 应等于时段内 first open
    first_ts = pd.to_datetime(out.iloc[0]["date"])
    window = df_1m[
        (pd.to_datetime(df_1m["date"]) >= first_ts)
        & (pd.to_datetime(df_1m["date"]) < first_ts + pd.Timedelta(minutes=15))
    ]
    if not window.empty:
        assert float(out.iloc[0]["open"]) == float(window.iloc[0]["open"])
        assert float(out.iloc[0]["high"]) == float(window["high"].max())


def test_resample_240m_combines_both_a_share_sessions_into_one_daily_bar():
    svc = KLineService.__new__(KLineService)
    df_1m = _make_1m_session()
    in_session = df_1m[
        ~pd.to_datetime(df_1m["date"]).dt.strftime("%H:%M").isin(["11:30", "15:00"])
    ]

    out = svc._resample_from_1m(df_1m, "240m")

    assert list(out["date"]) == ["2024-01-02 09:30"]
    assert float(out.iloc[0]["open"]) == float(in_session.iloc[0]["open"])
    assert float(out.iloc[0]["close"]) == float(in_session.iloc[-1]["close"])
    assert float(out.iloc[0]["high"]) == float(in_session["high"].max())
    assert float(out.iloc[0]["low"]) == float(in_session["low"].min())
    assert float(out.iloc[0]["volume"]) == float(in_session["volume"].sum())


def test_daily_drop_duplicate_dates():
    from services.kline_service import dedupe_kline_df

    df = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-02", "2024-01-03"],
            "open": [1, 2, 3],
            "high": [1, 2, 3],
            "low": [1, 2, 3],
            "close": [1, 2, 3],
            "volume": [1, 1, 1],
        }
    )
    out = dedupe_kline_df(df)
    assert len(out) == 2
    assert list(out["date"]) == ["2024-01-02", "2024-01-03"]


def test_df_to_kline_unique_timestamps_and_sorted():
    """后端出口：timestamp 唯一且严格升序（防前端日期循环的数据前提）"""
    from services.kline_service import df_to_kline

    df = pd.DataFrame(
        {
            "date": ["2024-01-03", "2024-01-02", "2024-01-02", "2024-01-04"],
            "open": [3, 1, 9, 4],
            "high": [3, 1, 9, 4],
            "low": [3, 1, 9, 4],
            "close": [3, 1, 9, 4],
            "volume": [1, 1, 1, 1],
        }
    )
    rows = df_to_kline(df)
    tss = [r["timestamp"] for r in rows]
    assert len(tss) == len(set(tss)) == 3
    assert tss == sorted(tss)
    assert all(tss[i] < tss[i + 1] for i in range(len(tss) - 1))


def test_weekly_monthly_resample_unique_and_fewer():
    """日线 → 周/月线：bar 数减少、日期唯一升序、无同日循环"""
    from data_loader import _resample
    from services.kline_service import df_to_kline, dedupe_kline_df

    dates = pd.bdate_range("2024-01-02", periods=60)
    daily = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": range(60),
            "high": [x + 1 for x in range(60)],
            "low": range(60),
            "close": [x + 0.5 for x in range(60)],
            "volume": [1000] * 60,
        }
    )
    # 注入重复日线，验证 resample 前去重
    daily = pd.concat([daily, daily.iloc[[-1]]], ignore_index=True)
    last_daily = str(daily["date"].max())[:10]

    for period in ("weekly", "monthly", "quarterly", "yearly"):
        out = _resample(daily, period)
        assert out is not None and not out.empty, period
        assert len(out) < len(daily.drop_duplicates(subset=["date"])), period
        out = dedupe_kline_df(out)
        # 未完结周期不得标成「未来日期」
        assert str(out["date"].max())[:10] <= last_daily, period
        rows = df_to_kline(out)
        tss = [r["timestamp"] for r in rows]
        assert len(tss) == len(set(tss)) == len(rows), period
        assert tss == sorted(tss), period


def test_resample_clips_month_end_to_last_obs():
    """月中最后一根日线 → 月线末 bar 日期 = 最后交易日，不是日历月末。"""
    from data_loader import _resample

    dates = pd.bdate_range("2026-07-01", "2026-07-17")
    daily = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100,
        }
    )
    out = _resample(daily, "monthly")
    assert not out.empty
    assert str(out.iloc[-1]["date"])[:10] == "2026-07-17"
