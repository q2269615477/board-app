"""派生周期（weekly/monthly/…）的重算与落库正确性

锁住两个曾在生产数据里造成大面积错误的 bug：

1. **缓存判据方向反了** —— `_load_resample` 原本写的是
   `if last_c <= last_daily: return cached`。而 `_resample` 会把「进行中周期」
   的标签钳到日线最后交易日，落库后 `last_c == last_daily`；此后日线每前进
   一天该条件依旧成立 → 永远命中缓存、永不重算。
   实测：969/981 个标的的周线因此冻结，茅台 600519 连一根周线都没有。

2. **只 INSERT OR REPLACE、从不删除** —— 每个交易日重算都会产出一根标着
   「当天日期」的进行中周期 bar，于是同一个自然周里按天堆积出多根「周线」。
   实测：sh000001 的 2026-07-20 那一周曾有 5 根周线，7 月有 5 根月线。
"""
from __future__ import annotations

import pandas as pd
import pytest

from data_loader import _resample


def _daily(start: str, days: int) -> pd.DataFrame:
    """构造连续工作日日线。"""
    idx = pd.bdate_range(start, periods=days)
    return pd.DataFrame({
        "date": idx.strftime("%Y-%m-%d"),
        "open": [10.0 + i * 0.1 for i in range(days)],
        "high": [10.5 + i * 0.1 for i in range(days)],
        "low": [9.5 + i * 0.1 for i in range(days)],
        "close": [10.2 + i * 0.1 for i in range(days)],
        "volume": [1000 + i for i in range(days)],
    })


class TestResampleShape:
    def test_one_bar_per_natural_week(self):
        """两个完整自然周 → 恰好 2 根周线，不是 10 根。"""
        df = _daily("2026-07-13", 10)          # 周一起，10 个工作日 = 2 周
        out = _resample(df, "weekly")
        assert len(out) == 2, f"应为2根周线，实得{len(out)}：{list(out['date'])}"

    def test_in_progress_week_clamped_to_last_trading_day(self):
        """进行中的一周，标签钳到日线最后交易日（不得是未来日期）。"""
        df = _daily("2026-07-20", 3)           # 周一~周三，本周未完结
        out = _resample(df, "weekly")
        assert len(out) == 1
        assert out["date"].iloc[-1] == df["date"].iloc[-1]

    def test_weekly_bar_aggregates_ohlc(self):
        df = _daily("2026-07-20", 5)
        out = _resample(df, "weekly")
        assert len(out) == 1
        r = out.iloc[0]
        assert r["open"] == df["open"].iloc[0]
        assert r["close"] == df["close"].iloc[-1]
        assert r["high"] == df["high"].max()
        assert r["low"] == df["low"].min()
        assert r["volume"] == df["volume"].sum()

    def test_monthly_one_bar_per_month(self):
        df = _daily("2026-05-01", 45)          # 跨 3 个月
        out = _resample(df, "monthly")
        months = {d[:7] for d in out["date"]}
        assert len(out) == len(months), f"每月应仅1根：{list(out['date'])}"


@pytest.fixture()
def repo(tmp_path):
    """隔离的 SqliteRepo。

    注意：路径由构造参数决定并存在 `self._path`，monkeypatch 实例上不存在的
    `db_path` 属性**不会**改变落库位置——早期版本这么写，导致测试数据被写进了
    生产库 data/kline.db。必须显式构造。
    """
    from data.sqlite_repo import SqliteRepo
    r = SqliteRepo(db_path=tmp_path / "test_kline.db")
    assert str(r._path).startswith(str(tmp_path)), "测试必须使用隔离数据库"
    return r


class TestPersistenceNoGhostBars:
    """逐日重算并落库，不得在同一自然周堆出多根。"""

    def test_daily_recompute_does_not_accumulate(self, repo):
        code = "TEST001"
        full = _daily("2026-07-20", 5)                     # 周一~周五
        # 模拟：周一收盘算一次、周二再算一次…… 每次都「先删后写」
        for n in range(1, 6):
            part = full.iloc[:n]
            out = _resample(part, "weekly")
            repo.delete_kline(code, "weekly")
            repo.save_kline(code, "weekly", out)

        got = repo.read_kline(code, "weekly")
        assert got is not None and len(got) == 1, (
            f"同一自然周只应留 1 根，实得 {0 if got is None else len(got)} 根："
            f"{[] if got is None else list(got['date'])}"
        )
        assert str(got["date"].iloc[-1])[:10] == full["date"].iloc[-1]

    def test_delete_kline_removes_only_target_period(self, repo):
        code = "TEST002"
        repo.save_kline(code, "daily", _daily("2026-07-01", 10))
        repo.save_kline(code, "weekly", _resample(_daily("2026-07-01", 10), "weekly"))

        repo.delete_kline(code, "weekly")
        assert (repo.read_kline(code, "weekly") is None
                or repo.read_kline(code, "weekly").empty)
        d = repo.read_kline(code, "daily")
        assert d is not None and len(d) == 10, "删除 weekly 不得影响 daily"
