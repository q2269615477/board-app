"""Write-set B locks: Tushare factory migration and proxy side-effect removal.

Scope is intentionally limited to this write set:
  data_update_manager.py, data/global_index_kline.py,
  services/index_constituent_service.py, scripts/agent_data_update.py,
  scripts/update_boards.py, scripts/check_system_status.py,
  scripts/update_constituents.py

The factories themselves (data_loader.get_tushare_pro /
core.env_bootstrap.load_env_files) belong to another write set and are
treated here as the shared contract, never modified.
"""
import os
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WRITE_SET = [
    'data_update_manager.py',
    'data/global_index_kline.py',
    'services/index_constituent_service.py',
    'scripts/agent_data_update.py',
    'scripts/update_boards.py',
    'scripts/check_system_status.py',
    'scripts/update_constituents.py',
]


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Static locks: no legacy Tushare client creation, no direct-force side
# effects, no manual proxy cleanup anywhere in this write set.
# ---------------------------------------------------------------------------

def test_write_set_uses_factory_and_has_no_legacy_tushare_creation():
    for rel in WRITE_SET:
        text = _read(rel)
        if rel != 'scripts/agent_data_update.py':
            assert 'get_tushare_pro' in text, f'{rel}: factory entry missing'
        assert 'ts.pro_api(' not in text, f'{rel}: ts.pro_api() remains'
        assert 'set_token' not in text, f'{rel}: ts.set_token remains'
        assert 'import tushare' not in text, f'{rel}: direct tushare import remains'
        assert 'force_direct_network' not in text, (
            f'{rel}: force_direct_network remains'
        )
        assert '_tushare_pro as' not in text, f'{rel}: private client access remains'


def test_agent_and_manager_do_not_clean_proxy_environment_manually():
    for rel in ('scripts/agent_data_update.py', 'data_update_manager.py'):
        text = _read(rel)
        for marker in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                       'http_proxy', 'https_proxy'):
            assert marker not in text, (
                f'{rel}: manual proxy env cleanup remains ({marker})'
            )


def test_global_index_kline_overseas_requests_do_not_force_direct():
    text = _read('data/global_index_kline.py')
    assert 'trust_env = False' not in text
    assert 'proxies=' not in text
    assert 'proxies={' not in text


# ---------------------------------------------------------------------------
# Runtime locks: every wrapper returns the unique factory client.
# ---------------------------------------------------------------------------

def test_data_update_manager_wrapper_returns_factory_client(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    import data_loader
    import data_update_manager as dum

    sentinel = object()
    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: True)
    monkeypatch.setattr(data_loader, 'get_tushare_pro', lambda: sentinel)

    assert dum._get_tushare_pro() is sentinel


def test_data_update_manager_wrapper_degrades_without_token(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    import data_update_manager as dum

    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: False)

    with pytest.raises(RuntimeError, match='TUSHARE_TOKEN'):
        dum._get_tushare_pro()


def test_trade_cal_set_goes_through_factory_wrapper(monkeypatch):
    import data_update_manager as dum

    class Pro:
        def trade_cal(self, **kwargs):
            return pd.DataFrame({'cal_date': ['20260701', '20260702']})

    monkeypatch.setattr(dum, '_get_tushare_pro', lambda: Pro())

    assert dum._get_trade_cal_set('20260731', '20260701') == {
        '2026-07-01', '2026-07-02'
    }


def test_update_failed_boards_has_no_direct_force_side_effect(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    import data_update_manager as dum

    calls = []
    monkeypatch.setattr(
        env_bootstrap,
        'force_direct_network',
        lambda: calls.append('called') or {},
    )

    result = dum.update_failed_boards()

    assert result['message'] == '无失败板块'
    assert calls == []


def test_index_constituent_service_uses_factory_client(monkeypatch, tmp_path):
    import core.env_bootstrap as env_bootstrap
    import data_loader
    from services.index_constituent_service import IndexConstituentService

    sentinel = object()
    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: True)
    monkeypatch.setattr(data_loader, 'get_tushare_pro', lambda: sentinel)

    assert IndexConstituentService(tmp_path / 'idx.db')._get_pro() is sentinel


def test_index_constituent_service_degrades_without_token(monkeypatch, tmp_path):
    import core.env_bootstrap as env_bootstrap
    from services.index_constituent_service import IndexConstituentService

    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: False)

    with pytest.raises(RuntimeError, match='TUSHARE_TOKEN'):
        IndexConstituentService(tmp_path / 'idx.db')._get_pro()


def test_global_index_kline_uses_factory_client(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    import data_loader
    from data import global_index_kline as gik

    sentinel = object()
    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: True)
    monkeypatch.setattr(data_loader, 'get_tushare_pro', lambda: sentinel)

    assert gik._get_tushare_index_pro() is sentinel


def test_global_index_kline_degrades_to_none_without_token(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    from data import global_index_kline as gik

    monkeypatch.setattr(env_bootstrap, 'ensure_tushare_token', lambda: False)

    assert gik._get_tushare_index_pro() is None


# ---------------------------------------------------------------------------
# Runtime locks: global/overseas HTTP requests inherit the process proxy
# environment (default trust_env) instead of being forced direct.
# ---------------------------------------------------------------------------

class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_eastmoney_history_inherits_environment_proxy(monkeypatch):
    from data import global_index_kline as gik

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return _JsonResponse({"data": {"klines": [
            "2026-07-30,1,2,3,1,5,0",
        ]}})

    monkeypatch.setattr(gik.requests, 'get', fake_get)

    out = gik.fetch_eastmoney_global_kline("^N225", limit=10)

    assert not out.empty
    assert calls and 'proxies' not in calls[0]


def test_eastmoney_spot_and_sina_keep_default_trust_env(monkeypatch):
    from data import global_index_kline as gik

    captured = []

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            captured.append((self, kwargs))
            return _JsonResponse({
                "data": {},
                "result": {"data": []},
            })

    monkeypatch.setattr(gik.requests, 'Session', Session)

    assert gik.fetch_eastmoney_spot_bar("^N225").empty
    assert gik.fetch_sina_global_kline("^N225").empty
    assert len(captured) == 2
    for session, kwargs in captured:
        assert session.trust_env is True
        assert 'proxies' not in kwargs


def test_tencent_and_yahoo_inherit_environment_proxy(monkeypatch):
    from data import global_index_kline as gik

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return _JsonResponse({"data": {}, "chart": {"result": []}})

    monkeypatch.setattr(gik.requests, 'get', fake_get)

    assert gik.fetch_tencent_global_kline("HSI").empty
    assert gik.fetch_yahoo_global_kline("SPX").empty
    assert len(calls) == 2
    for kwargs in calls:
        assert 'proxies' not in kwargs


# ---------------------------------------------------------------------------
# Runtime lock: agent_data_update bootstrap preserves proxy env and delegates
# env loading to core.env_bootstrap (no dotenv proxy takeover).
# ---------------------------------------------------------------------------

def test_agent_bootstrap_preserves_proxy_env_and_uses_env_loader(monkeypatch):
    import core.env_bootstrap as env_bootstrap
    import scripts.agent_data_update as agent

    loaded = []
    monkeypatch.setattr(agent.os, 'chdir', lambda _path: None)
    monkeypatch.setattr(
        env_bootstrap,
        'load_env_files',
        lambda: loaded.append('loaded') or {},
    )
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:7688')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7688')
    monkeypatch.setenv('NO_PROXY', 'localhost')

    agent._bootstrap()

    assert os.environ['HTTP_PROXY'] == 'http://127.0.0.1:7688'
    assert os.environ['HTTPS_PROXY'] == 'http://127.0.0.1:7688'
    assert os.environ['NO_PROXY'] == 'localhost'
    assert loaded == ['loaded']


# ---------------------------------------------------------------------------
# Script wiring: update_constituents imports the factory and degrades clearly
# when no client is available. (update_boards / check_system_status perform
# import-time side effects, so they are covered by the static locks above.)
# ---------------------------------------------------------------------------

def test_update_constituents_script_uses_factory_client(monkeypatch):
    sentinel = object()
    # Importing the script assigns module-level ``pro`` to the factory result.
    namespace = _run_update_constituents_script(monkeypatch, sentinel)
    assert namespace['pro'] is sentinel


def test_update_constituents_script_exits_clearly_without_client(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run_update_constituents_script(monkeypatch, None)
    assert exc.value.code == 1


def _run_update_constituents_script(monkeypatch, client):
    import runpy

    stub = types.ModuleType('data_loader')
    stub.get_tushare_pro = lambda: client
    monkeypatch.setitem(sys.modules, 'data_loader', stub)
    return runpy.run_path(
        str(PROJECT_ROOT / 'scripts' / 'update_constituents.py'),
        run_name='__write_set_b_test__',
    )
