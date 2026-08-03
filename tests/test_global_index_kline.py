import pandas as pd
import pytest

from data.global_index_kline import (
    fetch_eastmoney_global_kline,
    fetch_eastmoney_spot_bar,
    fetch_sina_global_kline,
    load_a_share_index_kline,
    load_global_index_kline,
    resample_kline,
)


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


def test_eastmoney_history_parser(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": [
                "2026-07-29,65164.98,64611.15,65720.81,64201.03,135400000,0,0,0,0,0",
                "2026-07-30,64539.92,62364.92,64573.18,61923.60,187000000,0,0,0,0,0",
            ]}}

    monkeypatch.setattr(
        "data.global_index_kline.requests.get",
        lambda *args, **kwargs: Response(),
    )

    out = fetch_eastmoney_global_kline("^N225", limit=180)

    assert out["date"].tolist() == ["2026-07-29", "2026-07-30"]
    assert out.iloc[-1]["close"] == pytest.approx(62364.92)
    assert out.iloc[-1]["low"] == pytest.approx(61923.60)


def test_eastmoney_all_a_history_uses_choice_market(monkeypatch):
    requested = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": [
                "2026-07-30,6200.00,6266.83,6280.00,6180.00,100,0,0,0,0,0",
            ]}}

    def fake_get(*args, **kwargs):
        requested.append(kwargs["params"]["secid"])
        return Response()

    monkeypatch.setattr("data.global_index_kline.requests.get", fake_get)

    out = fetch_eastmoney_global_kline("800000", limit=30)

    assert requested == ["47.800000"]
    assert out.iloc[0]["close"] == pytest.approx(6266.83)


def test_eastmoney_all_a_uses_fallback_and_rejects_weekend_bar(monkeypatch):
    requested = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": [
                "2026-07-03,6847.09,6878.87,6936.67,6838.88,100,0,0,0,0,0",
                "2026-07-04,6847.27,7271.51,7314.76,6839.78,100,0,0,0,0,0",
                "2026-07-06,6905.92,6841.42,6916.22,6789.89,100,0,0,0,0,0",
            ]}}

    def fake_get(url, **kwargs):
        requested.append(url)
        if "push2his" in url:
            raise ConnectionError("production history unavailable")
        return Response()

    monkeypatch.setattr("data.global_index_kline.requests.get", fake_get)

    out = fetch_eastmoney_global_kline("800000", limit=30)

    assert len(requested) == 2
    assert "push2test" in requested[-1]
    assert out["date"].tolist() == ["2026-07-03", "2026-07-06"]


def test_eastmoney_history_never_uses_push2test_for_standard_a_share(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append((url, kwargs["params"]["secid"]))
        return pytest.fail("standard A-share history must not call Eastmoney")

    monkeypatch.setattr("data.global_index_kline.requests.get", fake_get)
    out = fetch_eastmoney_global_kline("sh000039", limit=10)

    assert out.empty
    assert requested == []


def test_tushare_a_share_tail_replaces_dirty_range_and_weekend_rows(monkeypatch):
    import data.global_index_kline as index_kline

    local = pd.DataFrame([
        {'date': '2026-07-01', 'open': 4900, 'high': 5000, 'low': 4800,
         'close': 4950, 'volume': 1},
        {'date': '2026-07-21', 'open': 10000, 'high': 10000, 'low': 10000,
         'close': 10000, 'volume': 1},
        {'date': '2026-07-22', 'open': 10000, 'high': 10000, 'low': 10000,
         'close': 10000, 'volume': 1},
        {'date': '2026-07-24', 'open': 10000, 'high': 10000, 'low': 10000,
         'close': 10000, 'volume': 1},
        {'date': '2026-07-25', 'open': 10000, 'high': 10000, 'low': 10000,
         'close': 10000, 'volume': 1},
        {'date': '2026-07-27', 'open': 10000, 'high': 10000, 'low': 10000,
         'close': 10000, 'volume': 1},
    ])
    remote = pd.DataFrame([
        {'trade_date': '20260727', 'open': 5200, 'high': 5250, 'low': 5180,
         'close': 5216.2783, 'vol': 12},
        {'trade_date': '20260724', 'open': 5150, 'high': 5220, 'low': 5100,
         'close': 5175.0, 'vol': 11},
        {'trade_date': '20260722', 'open': 5050, 'high': 5120, 'low': 5000,
         'close': 5080.0, 'vol': 10},
        {'trade_date': '20260721', 'open': 5000, 'high': 5080, 'low': 4950,
         'close': 5020.0, 'vol': 9},
    ])

    class Pro:
        def index_daily(self, **kwargs):
            assert kwargs['ts_code'] == '000906.SH'
            return remote

    class Repo:
        def __init__(self):
            self.replaced = []

        def read_kline(self, code, period):
            assert (code, period) == ('sh000906', 'daily')
            return local

        def replace_kline_period(self, code, period, frame):
            self.replaced.append((code, period, frame.copy()))

    repo = Repo()
    monkeypatch.setattr(index_kline, 'get_sqlite_repo', lambda: repo)
    monkeypatch.setattr(index_kline, '_get_tushare_index_pro', lambda: Pro())

    out = load_a_share_index_kline('sh000906')

    assert '2026-07-25' not in set(out['date'])
    assert out.loc[out['date'] == '2026-07-27', 'close'].iloc[0] == pytest.approx(5216.2783)
    assert out.loc[out['date'] == '2026-07-01', 'close'].iloc[0] == pytest.approx(4950)
    assert repo.replaced and len(repo.replaced[0][2]) == len(out)


def test_tushare_sh_index_falls_back_to_csi_when_sh_is_empty(monkeypatch):
    import data.global_index_kline as index_kline
    calls = []

    class Pro:
        def index_daily(self, **kwargs):
            calls.append(kwargs['ts_code'])
            if kwargs['ts_code'] == '000988.SH':
                return pd.DataFrame()
            return pd.DataFrame([{
                'trade_date': '20260731', 'open': 3750, 'high': 3800,
                'low': 3700, 'close': 3789.8751, 'vol': 1,
            }])

    monkeypatch.setattr(index_kline, '_get_tushare_index_pro', lambda: Pro())
    out = index_kline.fetch_tushare_a_share_index_tail('sh000988')

    assert calls == ['000988.SH', '000988.CSI']
    assert out.iloc[-1]['close'] == pytest.approx(3789.8751)


def test_tushare_sh_index_with_sh_data_does_not_query_csi(monkeypatch):
    import data.global_index_kline as index_kline
    calls = []

    class Pro:
        def index_daily(self, **kwargs):
            calls.append(kwargs['ts_code'])
            return pd.DataFrame([{
                'trade_date': '20260731', 'open': 5150, 'high': 5250,
                'low': 5100, 'close': 5216.2783, 'vol': 1,
            }])

    monkeypatch.setattr(index_kline, '_get_tushare_index_pro', lambda: Pro())
    out = index_kline.fetch_tushare_a_share_index_tail('sh000906')

    assert calls == ['000906.SH']
    assert out.iloc[-1]['close'] == pytest.approx(5216.2783)


@pytest.mark.parametrize(
    ('code', 'expected'),
    [('sh000853', ['932000.CSI']), ('sh000985', ['000985.CSI'])],
)
def test_tushare_explicit_overrides_do_not_probe_market_fallback(code, expected, monkeypatch):
    import data.global_index_kline as index_kline
    calls = []

    class Pro:
        def index_daily(self, **kwargs):
            calls.append(kwargs['ts_code'])
            return pd.DataFrame([{
                'trade_date': '20260731', 'open': 1, 'high': 2,
                'low': 1, 'close': 1.5, 'vol': 1,
            }])

    monkeypatch.setattr(index_kline, '_get_tushare_index_pro', lambda: Pro())
    index_kline.fetch_tushare_a_share_index_tail(code)

    assert calls == expected


def test_inactive_a_share_index_has_no_kline_fallback(monkeypatch):
    import data.global_index_kline as index_kline

    monkeypatch.setattr(
        index_kline, 'get_sqlite_repo',
        lambda: pytest.fail('inactive index must not read SQLite'),
    )
    assert load_a_share_index_kline('sh000938').empty


def test_eastmoney_spot_bar_uses_complete_ohlc(monkeypatch):
    class Response:
        def json(self):
            return {"data": {
                "f43": 6436202,
                "f44": 6536473,
                "f45": 6194823,
                "f46": 6195710,
                "f47": 430195,
                "f86": 1785479400,
            }}

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("data.global_index_kline.requests.Session", Session)

    out = fetch_eastmoney_spot_bar("^N225")

    assert out.iloc[0].to_dict() == {
        "date": "2026-07-31",
        "open": pytest.approx(61957.10),
        "high": pytest.approx(65364.73),
        "low": pytest.approx(61948.23),
            "close": pytest.approx(64362.02),
            "volume": pytest.approx(430195),
            "amount": pytest.approx(0),
        }


def test_sina_apac_history_fills_missing_trading_days(monkeypatch):
    class Response:
        def json(self):
            return {"result": {"data": [
                {"d": "2026-07-29", "o": "62734.68", "h": "63138.04",
                 "l": "60448.90", "c": "61434.19", "v": "0"},
                {"d": "2026-07-30", "o": "61258.34", "h": "62924.84",
                 "l": "61049.70", "c": "61867.43", "v": "0"},
            ]}}

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("data.global_index_kline.requests.Session", Session)

    out = fetch_sina_global_kline("^N225")

    assert out["date"].tolist() == ["2026-07-29", "2026-07-30"]
    assert out.iloc[-1]["open"] == pytest.approx(61258.34)
    assert out.iloc[-1]["close"] == pytest.approx(61867.43)


def test_cached_history_is_refreshed_and_merged_by_date(monkeypatch):
    saved = []

    class Repo:
        def read_kline(self, code, period):
            assert (code, period) == ("^N225", "daily")
            return pd.DataFrame({
                "date": ["2026-07-28", "2026-07-29"],
                "open": [61000, 1],
                "high": [62000, 1],
                "low": [60000, 1],
                "close": [61500, 1],
                "volume": [100, 0],
            })

        def save_kline(self, code, period, df):
            saved.append((code, period, df.copy()))

    history = pd.DataFrame({
        "date": ["2026-07-29", "2026-07-30"],
        "open": [65164.98, 64539.92],
        "high": [65720.81, 64573.18],
        "low": [64201.03, 61923.60],
        "close": [64611.15, 62364.92],
        "volume": [135400000, 187000000],
    })
    spot = pd.DataFrame({
        "date": ["2026-07-31"],
        "open": [61957.10],
        "high": [65364.73],
        "low": [61948.23],
        "close": [64362.02],
        "volume": [430195],
    })

    monkeypatch.setattr("data.global_index_kline.get_sqlite_repo", lambda: Repo())
    monkeypatch.setattr(
        "data.global_index_kline.fetch_eastmoney_global_kline",
        lambda code, limit=180: history,
    )
    monkeypatch.setattr(
        "data.global_index_kline.fetch_eastmoney_spot_bar",
        lambda code: spot,
    )

    out = load_global_index_kline("^N225", "daily")

    assert out["date"].tolist() == [
        "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"
    ]
    assert out.loc[out["date"] == "2026-07-29", "close"].iloc[0] == pytest.approx(64611.15)
    assert saved and saved[0][2]["date"].tolist() == out["date"].tolist()


def test_eastmoney_all_a_cache_merges_history_and_current_bar(monkeypatch):
    saved = []
    replaced = []

    class Repo:
        def read_kline(self, code, period):
            assert (code, period) == ("800000", "daily")
            return pd.DataFrame([{
                "date": "2026-07-29", "open": 4000, "high": 4050,
                "low": 3990, "close": 3999.42, "volume": 10,
            }])

        def save_kline(self, code, period, df):
            saved.append(df.copy())

        def replace_kline_period(self, code, period, df):
            replaced.append(df.copy())

    history = pd.DataFrame([{
        "date": "2026-07-30", "open": 6200, "high": 6280,
        "low": 6180, "close": 6266.83, "volume": 20,
    }])
    spot = pd.DataFrame([{
        "date": "2026-07-31", "open": 6352.96, "high": 6398.74,
        "low": 6345.24, "close": 6350.92, "volume": 30,
    }])
    monkeypatch.setattr("data.global_index_kline.get_sqlite_repo", lambda: Repo())
    monkeypatch.setattr(
        "data.global_index_kline.fetch_eastmoney_global_kline",
        lambda code, limit=180: history,
    )
    monkeypatch.setattr(
        "data.global_index_kline.fetch_eastmoney_spot_bar",
        lambda code: spot,
    )

    out = load_global_index_kline("800000", "daily")

    assert out["date"].tolist() == ["2026-07-30", "2026-07-31"]
    assert out.iloc[-1]["close"] == pytest.approx(6350.92)
    assert not saved
    assert replaced and replaced[0]["date"].tolist() == out["date"].tolist()
