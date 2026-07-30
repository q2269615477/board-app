import pandas as pd

from data import market_kline


def _daily_df():
    return pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28", "2026-07-29"],
        "open": [10, 11, 12],
        "high": [12, 13, 14],
        "low": [9, 10, 11],
        "close": [11, 12, 13],
        "volume": [100, 200, 300],
    })


class FakeRepo:
    def __init__(self):
        self.saved = []

    def read_kline(self, code, period):
        if period == "daily":
            return _daily_df()
        return pd.DataFrame()

    def save_kline(self, code, period, df):
        self.saved.append((code, period, df.copy()))


def test_load_stock_data_filters_dates(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(market_kline, "get_sqlite_repo", lambda: repo)

    out = market_kline.load_stock_data("600519", "2026-07-28", "2026-07-29")

    assert list(out["date"]) == ["2026-07-28", "2026-07-29"]


def test_higher_period_resamples_from_daily_and_persists(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(market_kline, "get_sqlite_repo", lambda: repo)

    out = market_kline.load_index_kline("sh000001", "weekly")

    assert len(out) == 1
    assert out.iloc[0]["date"] == "2026-07-29"
    assert repo.saved
    assert repo.saved[0][0:2] == ("sh000001", "weekly")


def test_hk_stock_symbol_is_zero_padded(monkeypatch):
    seen = {}

    class Repo(FakeRepo):
        def read_kline(self, code, period):
            seen["code"] = code
            return super().read_kline(code, period)

    monkeypatch.setattr(market_kline, "get_sqlite_repo", lambda: Repo())

    market_kline.load_hk_kline("700")

    assert seen["code"] == "00700"
