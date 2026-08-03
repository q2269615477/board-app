"""test_direct_session_contract.py — 副作用迁移写集 A 的精确契约测试。

验证：
1. 迁移目标模块不再引用 force_direct_network / _ensure_direct（全局网络改写入口）。
2. board_routes / board_snapshot 的东财请求走局部直连 Session
   （trust_env=False、proxies={}，请求结束关闭）。
3. 上述路径执行后，全局 requests.Session 默认行为未被改写。
"""
import requests
from pathlib import Path

import pytest
from flask import Flask

from api import board_routes
from services.board_snapshot import BoardSnapshotCache

ROOT = Path(__file__).resolve().parents[1]

_SOURCE_FORBIDDEN = {
    'app.py': ('force_direct_network',),
    'data/board_api.py': ('force_direct_network', '_ensure_direct'),
    'services/board_snapshot.py': ('force_direct_network',),
    'services/board_spot_cache.py': ('force_direct_network', '_ensure_direct'),
    'api/board_routes.py': ('force_direct_network',),
}


def test_migrated_modules_no_longer_reference_global_network_hijack_symbols():
    for rel, symbols in _SOURCE_FORBIDDEN.items():
        text = (ROOT / rel).read_text(encoding='utf-8')
        for symbol in symbols:
            assert symbol not in text, f'{rel} still references {symbol}'

    app_text = (ROOT / 'app.py').read_text(encoding='utf-8')
    assert 'load_env_files' in app_text

    board_api_text = (ROOT / 'data/board_api.py').read_text(encoding='utf-8')
    assert 'ts.pro_api' not in board_api_text
    assert 'from data_loader import _tushare_pro' not in board_api_text


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.get_calls = []
        self.trust_env = True
        self.proxies = {'inherited': 'must be replaced'}
        self.closed = False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return _FakeResponse({})

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_snapshot_singleton():
    BoardSnapshotCache._instance = None
    BoardSnapshotCache._initialized = False
    yield
    BoardSnapshotCache._instance = None
    BoardSnapshotCache._initialized = False


def test_board_routes_index_list_uses_local_direct_session(monkeypatch):
    payload = {
        'data': {'diff': [
            {'f12': 'sh000001', 'f14': '上证指数', 'f2': 3000.0, 'f3': 1.0},
        ]},
    }
    fake = _FakeSession([_FakeResponse(payload)])
    monkeypatch.setattr('requests.Session', lambda: fake)
    monkeypatch.setattr(board_routes.time, 'sleep', lambda _s: None)

    flask_app = Flask('direct_session_contract')
    with flask_app.app_context():
        resp = board_routes.get_boards_route('index')

    assert resp.get_json()['data'] == [
        {'code': 'sh000001', 'name': '上证指数',
         'price': 3000.0, 'change_pct': 1.0},
    ]
    url, kwargs = fake.get_calls[0]
    assert 'push2.eastmoney.com' in url
    assert kwargs['params']['fs'] == 'm:1+s:2,m:0+t:5'
    assert fake.trust_env is False
    assert fake.proxies == {}
    assert fake.closed is True


def test_board_snapshot_capture_uses_local_direct_session(monkeypatch):
    payload = {
        'data': {
            'total': 1,
            'diff': [{
                'f12': 'BK0800', 'f14': '测试板块',
                'f2': 1234.56, 'f3': 1.23, 'f4': 15.67,
                'f5': 100000, 'f6': 500000000, 'f7': 2.5, 'f8': 3.14,
                'f15': 1250.0, 'f16': 1200.0, 'f17': 1210.0, 'f18': 1220.0,
            }],
        },
    }
    fake = _FakeSession([_FakeResponse(payload)])
    monkeypatch.setattr('requests.Session', lambda: fake)
    monkeypatch.setattr('services.board_snapshot._is_lunch_break', lambda: False)

    count = BoardSnapshotCache().capture_all('industry')

    assert count == 1
    url, _kwargs = fake.get_calls[0]
    assert 'eastmoney.com' in url
    assert fake.trust_env is False
    assert fake.proxies == {}
    assert fake.closed is True


def test_market_http_paths_leave_global_requests_defaults_untouched(monkeypatch):
    real_session_cls = requests.Session
    original_init = real_session_cls.__init__

    fake = _FakeSession([_FakeResponse({'data': {'diff': []}})])
    monkeypatch.setattr('requests.Session', lambda: fake)
    monkeypatch.setattr(board_routes.time, 'sleep', lambda _s: None)
    flask_app = Flask('direct_session_contract')
    with flask_app.app_context():
        board_routes.get_boards_route('index')

    fake2 = _FakeSession([_FakeResponse({'data': {'total': 0, 'diff': []}})])
    monkeypatch.setattr('requests.Session', lambda: fake2)
    monkeypatch.setattr('services.board_snapshot._is_lunch_break', lambda: False)
    BoardSnapshotCache().capture_all('industry')

    assert real_session_cls.__init__ is original_init
    probe = real_session_cls()
    try:
        assert probe.trust_env is True
        assert probe.proxies == {}
    finally:
        probe.close()
