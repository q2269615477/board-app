import pandas as pd

from data import board_kline


class FakeRepo:
    def __init__(self, daily=None):
        self.daily = daily if daily is not None else pd.DataFrame()
        self.saved = []

    def read_kline(self, code, period):
        if period == "daily":
            return self.daily.copy()
        return pd.DataFrame()

    def save_kline(self, code, period, df, name="", data_type=""):
        self.saved.append((code, period, df.copy(), name, data_type))
        if period == "daily":
            self.daily = df.copy()


def test_normalize_board_kline_supports_positional_mojibake_columns():
    raw = pd.DataFrame({
        "鏃ユ湡": ["2026-07-29"],
        "寮€鐩?": [10],
        "鏀剁洏": [11],
        "鏈€楂?": [12],
        "鏈€浣?": [9],
        "鎴愪氦閲?": [100],
    })

    out = board_kline.normalize_board_kline(raw)

    assert list(out.columns) == [
        "date", "open", "high", "low", "close", "volume", "amount"
    ]
    assert out.iloc[0].to_dict() == {
        "date": "2026-07-29",
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "volume": 100,
        "amount": 0,
    }


def test_load_board_kline_merges_incremental_rows(monkeypatch):
    existing = pd.DataFrame({
        "date": ["2026-07-28"],
        "open": [1],
        "high": [2],
        "low": [0.5],
        "close": [1.5],
        "volume": [10],
    })
    repo = FakeRepo(existing)
    raw = pd.DataFrame({
        "日期": ["2026-07-29"],
        "开盘": [10],
        "收盘": [11],
        "最高": [12],
        "最低": [9],
        "成交量": [100],
    })

    monkeypatch.setattr(board_kline, "get_sqlite_repo", lambda: repo)
    monkeypatch.setattr("data.board_api.get_board_kline", lambda *a, **k: raw)

    out = board_kline.load_board_kline("industry", "测试板块", "BK0001", "daily")

    assert list(out["date"]) == ["2026-07-28", "2026-07-29"]
    assert repo.saved[0][0:2] == ("BK0001", "daily")


def test_load_board_kline_resamples_and_persists(monkeypatch):
    repo = FakeRepo(pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28", "2026-07-29"],
        "open": [10, 11, 12],
        "high": [12, 13, 14],
        "low": [9, 10, 11],
        "close": [11, 12, 13],
        "volume": [100, 200, 300],
    }))

    monkeypatch.setattr(board_kline, "get_sqlite_repo", lambda: repo)

    out = board_kline.load_board_kline("concept", "测试概念", "BK0002", "weekly")

    assert len(out) == 1
    assert out.iloc[0]["date"] == "2026-07-29"
    assert repo.saved[0][0:2] == ("BK0002", "weekly")
