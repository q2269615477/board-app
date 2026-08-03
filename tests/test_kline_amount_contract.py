from pathlib import Path

import pandas as pd

from data.board_kline import normalize_board_kline
from data.kline_resample import resample_ohlcv
from data.sqlite_repo import SqliteRepo
from services import kline_service


def _daily_rows():
    return pd.DataFrame([
        {
            'date': '2026-07-30', 'open': 10, 'high': 12, 'low': 9,
            'close': 11, 'volume': 100, 'amount': 1_000,
        },
        {
            'date': '2026-07-31', 'open': 11, 'high': 13, 'low': 10,
            'close': 12, 'volume': 200, 'amount': 2_500,
        },
    ])


def test_df_to_kline_exposes_amount_and_turnover():
    records = kline_service.df_to_kline(_daily_rows())

    assert records[-1]['amount'] == 2_500
    assert records[-1]['turnover'] == 2_500


def test_sqlite_amount_sidecar_survives_legacy_ohlcv_save(tmp_path):
    repo = SqliteRepo(Path(tmp_path) / 'market.sqlite')
    repo.save_kline('sh000001', 'daily', _daily_rows())
    repo.save_kline(
        'sh000001',
        'daily',
        _daily_rows().drop(columns=['amount']).assign(close=[11.1, 12.1]),
    )

    loaded = repo.read_kline('sh000001', 'daily')
    assert loaded['amount'].tolist() == [1_000, 2_500]
    assert loaded['close'].tolist() == [11.1, 12.1]


def test_sqlite_amount_sidecar_survives_replace_with_missing_amount(tmp_path):
    repo = SqliteRepo(Path(tmp_path) / 'market.sqlite')
    repo.replace_kline_period('sh000001', 'daily', _daily_rows())
    missing = _daily_rows().assign(amount=[0, None], close=[11.2, 12.2])

    repo.replace_kline_period('sh000001', 'daily', missing)

    loaded = repo.read_kline('sh000001', 'daily')
    assert loaded['amount'].tolist() == [1_000, 2_500]
    assert loaded['close'].tolist() == [11.2, 12.2]


def test_higher_period_resample_sums_real_amount():
    weekly = resample_ohlcv(_daily_rows(), 'weekly')

    assert weekly.iloc[0]['volume'] == 300
    assert weekly.iloc[0]['amount'] == 3_500


def test_board_normalization_keeps_chinese_amount_column():
    raw = pd.DataFrame([{
        '日期': '2026-07-31', '开盘': 10, '最高': 13, '最低': 9,
        '收盘': 12, '成交量': 200, '成交额': 2_500,
    }])

    normalized = normalize_board_kline(raw)

    assert normalized.iloc[0]['volume'] == 200
    assert normalized.iloc[0]['amount'] == 2_500


def test_qmt_http_candles_keeps_source_amount(monkeypatch):
    class FakeClient:
        def candles(self, code, period='1d', count=-1):
            return {
                'ok': True,
                'bars': [{
                    'time': '20260731', 'open': 10, 'high': 13, 'low': 9,
                    'close': 12, 'volume': 200, 'amount': 2_500,
                }],
            }

    monkeypatch.setattr(
        'data.qmt_http_client.get_qmt_http_client', lambda: FakeClient()
    )

    frame = kline_service._qmt_http_candles('sh000001', count=1)

    assert frame.iloc[0]['amount'] == 2_500


def test_chart_datafeed_keeps_amount_fields():
    source = Path('static/js/chart-core.js').read_text(encoding='utf-8')

    assert 'amount: amount' in source
    assert 'turnover: amount' in source
    assert '.map(_normalizeChartBar)' in source
