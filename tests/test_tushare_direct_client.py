import json
from concurrent.futures import ThreadPoolExecutor

import requests


def test_get_tushare_pro_is_thread_safe_lazy_singleton(monkeypatch):
    import data_loader

    monkeypatch.setenv('TUSHARE_TOKEN', 'test-token')
    monkeypatch.setattr(data_loader, '_tushare_pro', None)
    real_session = requests.Session
    original_session_init = real_session.__init__
    created_sessions = []

    class CountingSession(real_session):
        def __init__(self, *args, **kwargs):
            created_sessions.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(data_loader.requests, 'Session', CountingSession)

    with ThreadPoolExecutor(max_workers=16) as executor:
        clients = list(executor.map(lambda _index: data_loader.get_tushare_pro(), range(64)))

    assert clients
    assert all(client is clients[0] for client in clients)
    assert data_loader._tushare_pro is clients[0]
    assert len(created_sessions) == 1
    assert clients[0]._session is created_sessions[0]
    assert clients[0]._session.trust_env is False
    assert clients[0]._session.proxies == {}
    assert real_session.__init__ is original_session_init
    other_session = real_session()
    assert other_session.trust_env is True


def test_tushare_query_uses_local_direct_session_and_parses_fields_items(monkeypatch):
    import data_loader

    monkeypatch.setenv('TUSHARE_TOKEN', 'test-token')
    monkeypatch.setattr(data_loader, '_tushare_pro', None)
    calls = []

    class Response:
        text = json.dumps({
            'code': 0,
            'data': {
                'fields': ['ts_code', 'close'],
                'items': [['000001.SZ', 12.34]],
            },
        })

        def __bool__(self):
            return True

    class DirectSession:
        def __init__(self):
            self.trust_env = 'unset'
            self.proxies = {'inherited': 'must be removed'}

        def post(self, url, **kwargs):
            calls.append((self, url, kwargs))
            return Response()

    monkeypatch.setattr(data_loader.requests, 'Session', DirectSession)
    monkeypatch.setattr(
        data_loader.requests,
        'post',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('module-level requests.post must not be used')
        ),
    )

    client = data_loader.get_tushare_pro()
    frame = client.daily(ts_code='000001.SZ', fields='ts_code,close')

    assert len(calls) == 1
    session, url, request_kwargs = calls[0]
    assert session is client._session
    assert session.trust_env is False
    assert session.proxies == {}
    assert url == 'http://api.waditu.com/dataapi/daily'
    assert request_kwargs['timeout'] == 30
    assert request_kwargs['json'] == {
        'api_name': 'daily',
        'token': 'test-token',
        'params': {
            'ts_code': '000001.SZ',
            'ts_type_name': 'http://api.waditu.com/dataapi',
        },
        'fields': 'ts_code,close',
    }
    assert frame.to_dict(orient='records') == [
        {'ts_code': '000001.SZ', 'close': 12.34},
    ]


def test_get_tushare_pro_without_token_returns_none_without_initializing(
    monkeypatch,
):
    import data_loader

    monkeypatch.delenv('TUSHARE_TOKEN', raising=False)
    monkeypatch.setattr(data_loader, '_tushare_pro', None)
    monkeypatch.setattr(
        data_loader,
        '_DirectTusharePro',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('client must not be created without a token')
        ),
    )

    assert data_loader.get_tushare_pro() is None
