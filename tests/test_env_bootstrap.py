import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import requests

from core import env_bootstrap


def test_load_env_files_preserves_existing_proxy_environment_and_keeps_contract(
    monkeypatch, tmp_path
):
    env_file = tmp_path / '.env'
    env_file.write_text(
        '\n'.join([
            'HTTP_PROXY=http://from-file.example:1',
            'HTTPS_PROXY=http://from-file.example:2',
            'ALL_PROXY=socks5://from-file.example:3',
            'NO_PROXY=from-file.example',
            'BOARD_ENV_BOOTSTRAP_TEST=loaded',
            'BOARD_ENV_BOOTSTRAP_EXISTING=from-file',
        ]),
        encoding='utf-8',
    )
    monkeypatch.setattr(env_bootstrap, '_CANDIDATES', (env_file,))
    proxy_values = {
        'HTTP_PROXY': 'http://existing.example:1',
        'HTTPS_PROXY': 'http://existing.example:2',
        'ALL_PROXY': 'socks5://existing.example:3',
        'NO_PROXY': 'existing.example',
    }
    for key, value in proxy_values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('BOARD_ENV_BOOTSTRAP_EXISTING', 'existing')

    original_session_init = requests.Session.__init__
    original_opener = urllib.request._opener

    applied = env_bootstrap.load_env_files(force=False)
    assert env_bootstrap.force_direct_network() == {}

    assert {key: os.environ[key] for key in proxy_values} == proxy_values
    assert os.environ['BOARD_ENV_BOOTSTRAP_TEST'] == 'loaded'
    assert os.environ['BOARD_ENV_BOOTSTRAP_EXISTING'] == 'existing'
    assert applied == {'BOARD_ENV_BOOTSTRAP_TEST': 'loaded'}
    assert requests.Session.__init__ is original_session_init
    assert urllib.request._opener is original_opener

    # force=True remains the explicit opt-in path for overwriting dotenv keys.
    forced = env_bootstrap.load_env_files(force=True)
    assert forced['HTTP_PROXY'] == 'http://from-file.example:1'
    assert forced['HTTPS_PROXY'] == 'http://from-file.example:2'
    assert forced['ALL_PROXY'] == 'socks5://from-file.example:3'
    assert forced['NO_PROXY'] == 'from-file.example'
    assert os.environ['HTTP_PROXY'] == 'http://from-file.example:1'
    assert os.environ['NO_PROXY'] == 'from-file.example'


def test_ensure_tushare_token_only_delegates_to_factory(monkeypatch):
    calls = []
    sentinel_client = object()

    monkeypatch.setattr(env_bootstrap, 'load_env_files', lambda: {})
    monkeypatch.setenv('TUSHARE_TOKEN', 'test-token')
    proxy_values = {
        'HTTP_PROXY': 'http://existing.example:1',
        'HTTPS_PROXY': 'http://existing.example:2',
        'ALL_PROXY': 'socks5://existing.example:3',
        'NO_PROXY': 'existing.example',
    }
    for key, value in proxy_values.items():
        monkeypatch.setenv(key, value)
    original_session_init = requests.Session.__init__
    original_opener = urllib.request._opener

    import data_loader

    monkeypatch.setattr(
        data_loader,
        'get_tushare_pro',
        lambda: calls.append('factory') or sentinel_client,
    )

    assert env_bootstrap.ensure_tushare_token() is True
    assert calls == ['factory']
    assert {key: os.environ[key] for key in proxy_values} == proxy_values
    assert requests.Session.__init__ is original_session_init
    assert urllib.request._opener is original_opener


def test_ensure_tushare_token_without_token_is_explicit_and_side_effect_free(
    monkeypatch,
):
    monkeypatch.setattr(env_bootstrap, 'load_env_files', lambda: {})
    monkeypatch.delenv('TUSHARE_TOKEN', raising=False)

    import data_loader

    factory_calls = []
    monkeypatch.setattr(
        data_loader,
        'get_tushare_pro',
        lambda: factory_calls.append('factory'),
    )

    assert env_bootstrap.ensure_tushare_token() is False
    assert factory_calls == []


def test_import_app_succeeds_with_legacy_force_direct_network_call():
    project_root = str(Path(__file__).resolve().parent.parent)
    child_env = os.environ.copy()
    child_env.update({
        'BOARD_APP_AUTO_BOOTSTRAP': '0',
        'QMT_ENABLED': '0',
        'QMT_AUTO_START': '0',
    })
    result = subprocess.run(
        [sys.executable, '-c', 'import app'],
        cwd=project_root,
        env=child_env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
