from unittest.mock import MagicMock, patch
from pathlib import Path

import pandas as pd
import pytest


def test_a_share_eastmoney_snapshot_scales_fields_and_market_code(monkeypatch):
    import data_loader

    class Response:
        def json(self):
            return {'data': {'f43': 34600, 'f57': '000039', 'f58': '上证信息',
                             'f60': 33443, 'f169': 1157, 'f170': 346}}

    class Session:
        trust_env = True

        def get(self, url, **kwargs):
            assert kwargs['params']['secid'] == '1.000039'
            return Response()

    monkeypatch.setattr('requests.Session', Session)
    result = data_loader._fetch_a_share_index_eastmoney('sh000039')
    assert result['price'] == pytest.approx(346.0)
    assert result['pre_close'] == pytest.approx(334.43)
    assert result['change'] == pytest.approx(11.57)
    assert result['change_pct'] == pytest.approx(3.46)


@pytest.mark.parametrize('code', ['sz000038', 'sz000903', 'sz000987'])
def test_legacy_a_share_index_prefixes_use_shanghai_eastmoney_market(monkeypatch, code):
    import data_loader

    class Response:
        def json(self):
            return {'data': {'f43': 10000, 'f60': 9900, 'f169': 100,
                             'f170': 101}}

    class Session:
        trust_env = True

        def get(self, _url, **kwargs):
            assert kwargs['params']['secid'] == f"1.{code[2:]}"
            return Response()

    monkeypatch.setattr('requests.Session', Session)
    assert data_loader._fetch_a_share_index_eastmoney(code)['price'] == 100


def test_bulk_a_share_index_spot_uses_one_request_and_preserves_alias_codes(monkeypatch):
    import data_loader
    calls = []

    class Response:
        def json(self):
            return {'data': {'diff': [
                {'f12': '000039', 'f13': 1, 'f14': '上证信息',
                 'f2': 2274.06, 'f3': 0.81, 'f4': 18.32, 'f152': 2},
                {'f12': '000903', 'f13': 1, 'f14': '中证A100',
                 'f2': 100.0, 'f3': 1.0, 'f4': 1.0, 'f152': 2},
                {'f12': '399006', 'f13': 0, 'f14': '创业板指',
                 'f2': 2274.06, 'f3': 0.81, 'f4': 18.32, 'f152': 2},
            ]}}

    class Session:
        trust_env = True

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr('requests.Session', Session)
    result = data_loader.fetch_a_share_index_spots(
        ['sh000039', 'sz000903', 'sz399006'], chunk_size=50
    )

    assert len(calls) == 1
    assert calls[0][0].endswith('/api/qt/ulist.np/get')
    assert calls[0][1]['params']['secids'] == '1.000039,1.000903,0.399006'
    assert result['sh000039']['price'] == pytest.approx(2274.06)
    assert result['sh000039']['change'] == pytest.approx(18.32)
    assert result['sh000039']['pre_close'] == pytest.approx(2255.74)
    assert result['sz000903']['price'] == pytest.approx(100.0)
    assert result['sz399006']['price'] == pytest.approx(2274.06)
    assert result['sz399006']['change_pct'] == pytest.approx(0.81)


def test_deprecated_index_never_falls_back_to_sqlite():
    import data_loader

    result = data_loader.get_spot_index('sh000803')
    assert result['unavailable'] is True
    assert result['reason'] == 'deprecated_no_remote'


def test_stale_sqlite_is_not_used_when_eastmoney_index_quote_exists(monkeypatch):
    import data_loader

    monkeypatch.setattr(data_loader, '_qmt_live_spot', lambda _code: {})
    monkeypatch.setattr(data_loader, '_sqlite_spot', lambda _code: {
        'price': 334.43, 'change_pct': -0.22,
    })
    monkeypatch.setattr(data_loader, '_fetch_a_share_index_eastmoney', lambda _code: {
        'price': 346.0, 'change_pct': 3.46, 'pre_close': 334.43,
        'channel': 'eastmoney_push2delay',
    })
    result = data_loader.get_spot_index('sh000039')
    assert result['price'] == 346.0
    assert result['change_pct'] == 3.46


def test_eastmoney_failure_falls_back_to_sqlite(monkeypatch):
    import data_loader

    monkeypatch.setattr(data_loader, '_qmt_live_spot', lambda _code: {})
    monkeypatch.setattr(data_loader, '_fetch_a_share_index_eastmoney', lambda _code: {})
    monkeypatch.setattr(data_loader, '_sqlite_spot', lambda _code: {
        'price': 334.43, 'change_pct': -0.22,
    })
    assert data_loader.get_spot_index('sh000039')['price'] == 334.43


def test_closed_nav_refresh_uses_local_snapshot_without_remote_request(monkeypatch):
    import services.nav_spot_service as nav

    nav._cache = {}
    nav._cache_ts = 0
    nav._cache_meta = {}
    nav._domestic_phase_data = {}
    nav._inflight = False
    nav._inflight_evt.set()
    monkeypatch.setattr(
        nav,
        'get_nav_targets',
        lambda: [('sh000039', '上证信息', 'index')],
    )
    monkeypatch.setattr(
        nav,
        'market_state',
        lambda *_args, **_kwargs: {
            'market': 'a_share',
            'market_phase': 'closed',
            'market_open': False,
        },
    )
    monkeypatch.setattr(nav, '_split_targets', lambda: (['sh000039'], []))
    monkeypatch.setattr('data_loader.get_local_spot', lambda _code: {
        'price': 346.0, 'change_pct': 3.46, 'channel': 'sqlite',
    })
    monkeypatch.setattr(
        'data_loader.get_spot_index',
        lambda code: pytest.fail(f'closed market requested remotely: {code}'),
    )
    result = nav.fetch_nav_spots(force=True)
    assert result['data']['sh000039']['price'] == 346.0
    assert result['data']['sh000039']['changePct'] == 3.46
    assert result['data']['sh000039']['channel'] == 'sqlite'
    assert result['data']['sh000039']['market_open'] is False


def test_a_share_kline_service_uses_tushare_authoritative_path(monkeypatch):
    from services.kline_service import KLineService

    remote = pd.DataFrame([{
        'date': '2026-07-31', 'open': 340, 'high': 347, 'low': 339,
        'close': 346.0, 'volume': 2,
    }])
    svc = KLineService.__new__(KLineService)
    with patch('services.kline_service.load_a_share_index_kline', return_value=remote), \
         patch('services.kline_service._qmt_http_ohlc', return_value={}):
        out = svc._load_daily('index', 'sh000039', '')
    assert out.iloc[-1]['date'] == '2026-07-31'
    assert out.iloc[-1]['close'] == 346.0
    assert out.attrs['source'] == 'tushare_index_daily'


def test_standard_a_share_history_does_not_use_eastmoney(monkeypatch):
    import data.global_index_kline as index_kline

    def fake_get(*_args, **_kwargs):
        pytest.fail('standard A-share history must not use Eastmoney')

    monkeypatch.setattr(index_kline.requests, 'get', fake_get)
    out = index_kline.fetch_eastmoney_global_kline('sh000039', limit=10)
    assert out.empty


def test_index_audit_includes_prewarm_and_asia_us_targets():
    from scripts.audit_index_quotes import discover_indices

    rows = discover_indices(Path('static/board_classification.json'))
    codes = {row['code'] for row in rows}
    assert {'HSI', 'HSTECH', '800000', '^N225', '^KS11', '^TWII',
            'SPX', 'IXIC', 'DJI'} <= codes


def test_index_audit_accepts_global_change_as_price_previous_consistency():
    from scripts.audit_index_quotes import _audit_row

    bucket, row = _audit_row(
        {'code': '^N225', 'name': '日经225'},
        {'price': 105, 'change': 5, 'change_pct': 5, 'channel': 'eastmoney_push2delay'},
    )
    assert bucket == 'checked'
    assert row['price'] == 105


def test_index_audit_separates_deprecated_from_active_failures():
    from scripts.audit_index_quotes import _audit_row

    bucket, row = _audit_row(
        {'code': 'sh000803', 'name': '300波动'},
        {'unavailable': True, 'reason': 'deprecated_no_remote'},
    )
    assert bucket == 'inactive'
    assert row['status'] == 'deprecated'


def test_batch_route_has_authoritative_a_share_index_refresh():
    source = Path('api/board_routes.py').read_text(encoding='utf-8')
    assert 'a_share_index_codes' in source
    assert 'active_a_share_index_codes' in source
    assert 'fetch_a_share_index_spots(active_a_share_index_codes)' in source
    assert "get_spot_index(code)" in source
    assert "result[code] = packed" in source
    assert "not market_states[code]['market_open']" in source


def test_standard_a_share_index_response_cache_is_reused_until_force(monkeypatch):
    from services.kline_service import KLineService

    class Cache:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ttl=None):
            self.values[key] = value

        def delete(self, key):
            self.values.pop(key, None)

    frame = pd.DataFrame([{
        'date': '2026-07-31', 'open': 2270, 'high': 2280,
        'low': 2260, 'close': 2274.06, 'volume': 1,
    }])
    svc = KLineService.__new__(KLineService)
    svc._cache = Cache()
    loader = MagicMock(return_value=frame)
    svc._do_load = loader
    svc._response_intraday = MagicMock(return_value={})

    first, _ = svc.get_kline('index', 'sh000039', 'daily', timeout=5)
    second, _ = svc.get_kline('index', 'sh000039', 'daily', timeout=5)
    forced, _ = svc.get_kline('index', 'sh000039', 'daily', force=True, timeout=5)

    assert first['count'] == 1
    assert second['source'] == 'cache'
    assert forced['count'] == 1
    assert loader.call_count == 2


def _cache_first_service(local, loader_frame=None):
    from services.kline_service import KLineService

    class Cache:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ttl=None):
            self.values[key] = value

        def delete(self, key):
            self.values.pop(key, None)

    frame = loader_frame if loader_frame is not None else local
    svc = KLineService.__new__(KLineService)
    svc._cache = Cache()
    svc._read_sqlite_fast = MagicMock(return_value=local)
    svc._submit_background_refresh = MagicMock(return_value=True)
    svc._do_load = MagicMock(return_value=frame)
    svc._response_intraday = MagicMock(return_value={})
    return svc


def _healthy_index_tail():
    return pd.DataFrame([
        {'date': '2026-07-28', 'open': 99, 'high': 102, 'low': 98,
         'close': 100, 'volume': 1},
        {'date': '2026-07-29', 'open': 100, 'high': 103, 'low': 99,
         'close': 101, 'volume': 1},
        {'date': '2026-07-30', 'open': 101, 'high': 104, 'low': 100,
         'close': 102, 'volume': 1},
    ])


def test_standard_index_cache_first_uses_healthy_daily_and_starts_background():
    svc = _cache_first_service(_healthy_index_tail())

    response, status = svc.get_kline(
        'index', 'sh000906', 'daily', cache_first=True, timeout=5
    )

    assert status == 200
    assert response['stale'] is True
    assert response['background_refresh_started'] is True
    svc._read_sqlite_fast.assert_called_once()
    svc._submit_background_refresh.assert_called_once()
    svc._do_load.assert_not_called()


@pytest.mark.parametrize('bad_kind', ['weekend', 'jump', 'ohlc'])
def test_standard_index_cache_first_rejects_unsafe_daily_tail(bad_kind):
    local = _healthy_index_tail()
    if bad_kind == 'weekend':
        local.loc[local.index[-1], 'date'] = '2026-07-25'
    elif bad_kind == 'jump':
        local.loc[local.index[-1], ['open', 'high', 'close']] = [150, 155, 150]
    else:
        local.loc[local.index[-1], 'low'] = 200
    loader_frame = _healthy_index_tail()
    svc = _cache_first_service(local, loader_frame)

    response, status = svc.get_kline(
        'index', 'sh000906', 'daily', cache_first=True, timeout=5
    )

    assert status == 200
    assert response['count'] == len(loader_frame)
    svc._do_load.assert_called_once()
    svc._submit_background_refresh.assert_not_called()


def test_standard_index_force_skips_cache_first_even_when_daily_is_healthy():
    local = _healthy_index_tail()
    svc = _cache_first_service(local, local)

    response, status = svc.get_kline(
        'index', 'sh000906', 'daily', cache_first=True, force=True, timeout=5
    )

    assert status == 200
    assert response['count'] == len(local)
    svc._read_sqlite_fast.assert_not_called()
    svc._do_load.assert_called_once()
    svc._submit_background_refresh.assert_not_called()


def test_batch_route_marks_deprecated_and_keeps_active_eastmoney_quote(monkeypatch):
    from flask import Flask
    from api.board_routes import bp

    app = Flask(__name__)
    app.register_blueprint(bp)

    monkeypatch.setattr(
        'services.market_session.market_state',
        lambda _code, **_kwargs: {
            'market': 'a_share',
            'market_phase': 'live_morning',
            'market_open': True,
            'market_timezone': 'Asia/Shanghai',
        },
    )

    monkeypatch.setattr(
        'data_loader.fetch_a_share_index_spots',
        lambda _codes: {
            'sh000039': {
                'price': 346.0,
                'change_pct': 3.46,
                'channel': 'eastmoney_push2delay_batch',
            },
        },
    )
    monkeypatch.setattr(
        'data_loader.get_spot_index',
        lambda _code: pytest.fail('A-share batch quote fell back to single request'),
    )
    monkeypatch.setattr(
        'data_loader.get_local_spot',
        lambda _code: pytest.fail('deprecated index reached SQLite fallback'),
    )
    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots_fast',
        lambda **_kwargs: {'data': {}},
    )

    response = app.test_client().get(
        '/api/spot/indices?tickers=sh000803,sh000039'
    )
    data = response.get_json()['data']
    assert data['sh000803']['unavailable'] is True
    assert 'price' not in data['sh000803']
    assert data['sh000039']['price'] == pytest.approx(346.0)
    assert data['sh000039']['changePct'] == pytest.approx(3.46)


def test_batch_route_keeps_closed_local_and_only_refreshes_active_global(monkeypatch):
    from flask import Flask
    from api.board_routes import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    remote_calls = []

    def state(code, **_kwargs):
        is_global = code == 'HSI'
        return {
            'market': 'hong_kong' if is_global else 'a_share',
            'market_phase': 'live_morning' if is_global else 'closed',
            'market_open': is_global,
            'market_timezone': 'Asia/Hong_Kong' if is_global else 'Asia/Shanghai',
        }

    monkeypatch.setattr('services.market_session.market_state', state)
    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots_fast',
        lambda **_kwargs: {'data': {}},
    )
    monkeypatch.setattr(
        'data_loader.fetch_a_share_index_spots',
        lambda codes: pytest.fail(f'closed A-share batch requested: {codes}'),
    )
    monkeypatch.setattr(
        'data_loader.get_local_spot',
        lambda code: {'price': 7000, 'change_pct': 3.46, 'channel': 'sqlite'}
        if code == 'sh000039' else {},
    )

    def global_spot(code):
        remote_calls.append(code)
        return {'price': 25000, 'change_pct': 0.5, 'channel': 'tencent'}

    monkeypatch.setattr('data_loader.get_global_index_spot', global_spot)

    response = app.test_client().get(
        '/api/spot/indices?tickers=sh000039,HSI'
    )
    data = response.get_json()['data']

    assert remote_calls == ['HSI']
    assert data['sh000039']['channel'] == 'sqlite'
    assert data['sh000039']['market_open'] is False
    assert data['HSI']['price'] == pytest.approx(25000)
    assert data['HSI']['market'] == 'hong_kong'
    assert data['HSI']['market_open'] is True
