"""Regression tests for the QMT HTTP chart and spot paths."""
from unittest.mock import MagicMock, patch

import pandas as pd

from data.qmt_http_client import QmtHttpClient
from services.kline_service import (
    KLineService,
    _history_refresh_ts,
    _overlapping_bars_differ,
    _qmt_http_candles,
    dedupe_kline_df,
)


def _client():
    with patch('app.start_app'), patch('app.realtime_websocket'):
        from app import app
        app.config['TESTING'] = True
        return app.test_client()


def _minute_rows():
    rows = []
    for index, minute in enumerate(('09:30', '10:30', '11:29', '13:00', '14:59')):
        rows.append({
            'date': f'2026-07-31 {minute}',
            'open': 10 + index,
            'high': 11 + index,
            'low': 9 + index,
            'close': 10.5 + index,
            'volume': 100 + index,
        })
    return pd.DataFrame(rows)


def test_kline_route_accepts_frontend_cache_first_flag():
    service = MagicMock()
    service.get_kline.return_value = ({'data': [], 'count': 0}, 200)
    with patch('api.kline_routes.get_kline_service', return_value=service):
        response = _client().get('/api/kline/stock/600519?period=daily&cache_first=1')
    assert response.status_code == 200
    assert service.get_kline.call_args.kwargs['cache_first'] is True


def test_domestic_spot_prefers_qmt_http_18080():
    qmt = MagicMock()
    qmt.ohlc_batch.return_value = {
        'ok': True,
        'items': {'600519': {'open': 100, 'high': 105, 'low': 99, 'close': 104}},
    }
    with patch('services.nav_spot_service._a_share_nav_phase', return_value='live_morning'), \
         patch('data.qmt_http_client.get_qmt_http_client', return_value=qmt), \
         patch('data_loader.get_spot_stock', return_value={'price': 1}):
        response = _client().get('/api/spot/stock/600519')
    assert response.status_code == 200
    assert response.get_json()['data']['close'] == 104
    qmt.ohlc_batch.assert_called_once()


def test_closed_market_domestic_spot_skips_qmt_http():
    qmt = MagicMock()
    with patch('services.nav_spot_service._a_share_nav_phase', return_value='closed'), \
         patch('data.qmt_http_client.get_qmt_http_client', return_value=qmt), \
         patch('data_loader.get_spot_index', return_value={'price': 3800}):
        response = _client().get('/api/spot/index/sh000001')
    assert response.status_code == 200
    assert response.get_json()['data']['price'] == 3800
    qmt.ohlc_batch.assert_not_called()


def test_overseas_spot_does_not_call_qmt_http():
    qmt = MagicMock()
    with patch('data.qmt_http_client.get_qmt_http_client', return_value=qmt), \
         patch('data_loader.get_global_index_spot', return_value={'price': 18000}):
        response = _client().get('/api/spot/hk_index/HSI')
    assert response.status_code == 200
    assert response.get_json()['data']['price'] == 18000
    qmt.ohlc_batch.assert_not_called()


def test_eastmoney_all_a_spot_does_not_call_qmt_http():
    qmt = MagicMock()
    with patch('data.qmt_http_client.get_qmt_http_client', return_value=qmt), \
         patch('data_loader.get_global_index_spot', return_value={'price': 6300}):
        response = _client().get('/api/spot/index/800000')
    assert response.status_code == 200
    assert response.get_json()['data']['price'] == 6300
    qmt.ohlc_batch.assert_not_called()


def test_minute_http_1m_is_resampled_into_a_share_sessions():
    service = KLineService.__new__(KLineService)
    service._qmt = MagicMock()
    calls = []

    def candles(code, period='1m', count=-1):
        calls.append(period)
        return _minute_rows()

    with patch('services.kline_service._qmt_http_candles', side_effect=candles), \
         patch('services.kline_service.is_qmt_available', return_value=False):
        result = service._load_minute('stock', '600519', '120m')

    assert calls == ['1m']
    assert list(result['date']) == ['2026-07-31 09:30', '2026-07-31 13:00']
    service._qmt.get_minute_kline.assert_not_called()


def test_qmt_http_candles_parses_compact_minute_timestamp():
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        'bars': [{
            'time': 20260731093000,
            'open': 10,
            'high': 11,
            'low': 9,
            'close': 10.5,
            'volume': 100,
        }]
    }
    session.get.return_value = response
    client = QmtHttpClient(session=session)
    with patch('data.qmt_http_client.get_qmt_http_client', return_value=client):
        result = _qmt_http_candles('600519', period='1m', count=-1)
    assert list(result['date']) == ['2026-07-31 09:30:00']


def test_qmt_http_full_history_trims_leading_zero_volume_synthetic_rows():
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        'bars': [
            {'time': '20000104', 'open': 10, 'high': 10, 'low': 10,
             'close': 10, 'volume': 0},
            {'time': '20180508', 'open': 20, 'high': 21, 'low': 19,
             'close': 20.5, 'volume': 100},
        ]
    }
    session.get.return_value = response
    client = QmtHttpClient(session=session)
    with patch('data.qmt_http_client.get_qmt_http_client', return_value=client):
        result = _qmt_http_candles('sh000300', period='1d', count=12000)
    assert list(result['date']) == ['2018-05-08']


def test_qmt_http_restores_signed_int32_volume_overflow():
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        'bars': [{
            'time': '20240930',
            'open': 4472,
            'high': 4728,
            'low': 4410,
            'close': 4706,
            'volume': -1959715090,
        }]
    }
    session.get.return_value = response
    client = QmtHttpClient(session=session)
    with patch('data.qmt_http_client.get_qmt_http_client', return_value=client):
        result = _qmt_http_candles('sh000985', period='1d', count=12000)
    assert result.iloc[0]['volume'] == 2335252206


def test_authoritative_duplicate_date_deterministically_wins():
    local = pd.DataFrame([{
        'date': '2024-09-30', 'open': 1, 'high': 2, 'low': 0.5,
        'close': 1.5, 'volume': -10,
    }])
    remote = local.copy()
    remote.loc[0, 'volume'] = 4294967286
    merged = dedupe_kline_df(pd.concat([local, remote], ignore_index=True))
    assert merged.iloc[0]['volume'] == 4294967286
    assert _overlapping_bars_differ(local, remote) is True


def test_complete_history_repairs_truncated_head_and_internal_missing_bar():
    _history_refresh_ts.clear()
    service = KLineService.__new__(KLineService)
    service._db = MagicMock()
    local = pd.DataFrame([
        {'date': '2020-01-02', 'open': 10, 'high': 11, 'low': 9,
         'close': 10.5, 'volume': 100},
        {'date': '2020-01-06', 'open': 12, 'high': 13, 'low': 11,
         'close': 12.5, 'volume': 100},
    ])
    remote = pd.DataFrame([
        {'date': '2019-12-31', 'open': 9, 'high': 10, 'low': 8,
         'close': 9.5, 'volume': 100},
        {'date': '2020-01-02', 'open': 10, 'high': 11, 'low': 9,
         'close': 10.5, 'volume': 100},
        {'date': '2020-01-03', 'open': 11, 'high': 12, 'low': 10,
         'close': 11.5, 'volume': 100},
        {'date': '2020-01-06', 'open': 12, 'high': 13, 'low': 11,
         'close': 12.5, 'volume': 100},
    ])
    with patch('services.kline_service._qmt_http_daily', return_value=remote):
        merged = service._ensure_complete_history('stock', '600519', local)
    assert list(merged['date']) == [
        '2019-12-31', '2020-01-02', '2020-01-03', '2020-01-06',
    ]
    saved = service._db.save_kline.call_args.args
    assert saved[0:2] == ('600519', 'daily')
    assert len(saved[2]) == 4


def test_complete_history_persists_internal_value_correction():
    _history_refresh_ts.clear()
    service = KLineService.__new__(KLineService)
    service._db = MagicMock()
    local = pd.DataFrame([
        {'date': '2024-09-30', 'open': 10, 'high': 11, 'low': 9,
         'close': 10.5, 'volume': -10},
        {'date': '2024-10-08', 'open': 12, 'high': 13, 'low': 11,
         'close': 12.5, 'volume': 100},
    ])
    remote = local.copy()
    remote.loc[0, 'volume'] = 4294967286
    with patch('services.kline_service._qmt_http_daily', return_value=remote):
        merged = service._ensure_complete_history(
            'index', 'sh000985', local, force=True
        )
    assert merged.iloc[0]['volume'] == 4294967286
    service._db.save_kline.assert_called_once()


def test_minute_240m_combines_morning_and_afternoon_into_one_bar():
    service = KLineService.__new__(KLineService)
    service._qmt = MagicMock()
    with patch('services.kline_service._qmt_http_candles', return_value=_minute_rows()), \
         patch('services.kline_service.is_qmt_available', return_value=False):
        result = service._load_minute('index', 'sh000001', '240m')
    assert list(result['date']) == ['2026-07-31 09:30']
    assert float(result.iloc[0]['open']) == 10.0
    assert float(result.iloc[0]['close']) == 14.5


def test_daily_response_contains_ephemeral_intraday_without_persisting_it():
    service = KLineService.__new__(KLineService)
    service._cache = MagicMock()
    service._cache.get.return_value = None
    service._cache.set = MagicMock()
    service._db = MagicMock()
    service._qmt = MagicMock()
    daily = pd.DataFrame([{
        'date': '2026-07-30', 'open': 10, 'high': 11,
        'low': 9, 'close': 10.5, 'volume': 100,
    }])
    daily.attrs['intraday'] = {
        'open': 10.5, 'high': 12, 'low': 10.4,
        'close': 11.8, 'volume': 200, 'channel': 'qmt18080',
    }
    service._do_load = MagicMock(return_value=daily)

    with patch('services.kline_service._qmt_http_ohlc') as ohlc:
        result, status = service.get_kline('stock', '600519', 'daily', timeout=1)

    assert status == 200
    assert result['intraday']['close'] == 11.8
    ohlc.assert_not_called()
    service._db.save_kline.assert_not_called()


def test_ensure_latest_bar_never_persists_spot_fallback():
    service = KLineService.__new__(KLineService)
    service._db = MagicMock()
    local = pd.DataFrame([{
        'date': '2026-07-30', 'open': 10, 'high': 11,
        'low': 9, 'close': 10.5, 'volume': 100,
    }])
    with patch('services.kline_service._qmt_http_daily', return_value=pd.DataFrame()), \
         patch('data.board_api.get_last_trading_date', return_value='2026-07-31'):
        result = service._ensure_latest_kline_bar('stock', '600519', 'daily', local)
    assert result.equals(local)
    service._db.save_kline.assert_not_called()


def test_daily_change_forces_derived_period_recalculation():
    service = KLineService.__new__(KLineService)
    daily = pd.DataFrame([{
        'date': '2026-07-31', 'open': 10, 'high': 12,
        'low': 9, 'close': 11.5, 'volume': 100,
    }])
    stale_derived = pd.DataFrame([{
        'date': '2026-07-31', 'open': 1, 'high': 2,
        'low': 0.5, 'close': 1.5, 'volume': 10,
    }])
    service._db = MagicMock()
    service._db.read_kline.side_effect = [daily, stale_derived]
    derived = pd.DataFrame([{
        'date': '2026-07-31', 'open': 10, 'high': 12,
        'low': 9, 'close': 11.5, 'volume': 100,
    }])

    with patch('data_loader._resample', return_value=derived) as resample:
        result = service._load_resample('stock', '600519', 'weekly', '')

    resample.assert_called_once_with(daily, 'weekly')
    assert result.iloc[-1]['close'] == 11.5
