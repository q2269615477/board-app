"""qmt_http_client + symbol map unit tests (no live 18080 required)."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from data.qmt_http_client import QmtHttpClient, from_qmt_symbol, to_qmt_symbol


def test_to_qmt_symbol_variants():
    assert to_qmt_symbol("600519") == "600519.SH"
    assert to_qmt_symbol("sh600519") == "600519.SH"
    assert to_qmt_symbol("000001.SZ") == "000001.SZ"
    assert to_qmt_symbol("sh000001") == "000001.SH"
    assert to_qmt_symbol("sz399006") == "399006.SZ"
    assert to_qmt_symbol("HSI") is None


def test_from_qmt_symbol_index():
    assert from_qmt_symbol("000001.SH") == "sh000001"
    assert from_qmt_symbol("399006.SZ") == "sz399006"
    assert from_qmt_symbol("600519.SH") == "600519"


def test_ohlc_batch_maps_keys_and_aliases():
    session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "channel": "qmt_native_batch",
        "mode": "native",
        "items": {
            "000001.SH": {
                "symbol": "000001.SH",
                "open": 3000.0,
                "high": 3100.0,
                "low": 2950.0,
                "close": 3050.0,
                "change_pct": 1.2,
                "time": "20260728",
            }
        },
        "errors": [],
        "elapsed_ms": 12,
    }
    session.get.return_value = resp
    client = QmtHttpClient(base_url="http://127.0.0.1:18080", timeout=2, session=session)
    out = client.ohlc_batch(["sh000001"])
    assert out["ok"] is True
    assert "sh000001" in out["items"]
    row = out["items"]["sh000001"]
    assert row["price"] == 3050.0
    assert row["changePct"] == 1.2
    assert out["channel"] == "qmt_native_batch"


def test_ohlc_batch_down_does_not_raise():
    session = MagicMock()
    session.get.side_effect = Exception("connection refused")
    client = QmtHttpClient(base_url="http://127.0.0.1:9", timeout=0.2, session=session)
    out = client.ohlc_batch(["600519"])
    assert out["ok"] is False
    assert out["items"] == {}


def test_ohlc_batch_failure_opens_short_circuit():
    session = MagicMock()
    session.get.side_effect = Exception("connection refused")
    client = QmtHttpClient(base_url="http://127.0.0.1:9", timeout=0.2, session=session)

    first = client.ohlc_batch(["600519"])
    second = client.ohlc_batch(["sh000001"])

    assert first["ok"] is False
    assert second["error"] == "qmt_http_circuit_open"
    assert second["retry_after_ms"] > 0
    assert session.get.call_count == 1


def test_health_ok_flag():
    session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"status": "ok"}
    session.get.return_value = resp
    client = QmtHttpClient(session=session)
    h = client.health()
    assert h["_ok"] is True


def test_default_session_is_explicitly_direct_and_global_defaults_untouched():
    real_session_cls = requests.Session
    original_init = real_session_cls.__init__

    client = QmtHttpClient(base_url="http://127.0.0.1:9", timeout=0.2)
    try:
        assert client._session.trust_env is False
        assert client._session.proxies == {}
    finally:
        client._session.close()

    assert requests.Session.__init__ is original_init
    probe = real_session_cls()
    try:
        assert probe.trust_env is True
        assert probe.proxies == {}
    finally:
        probe.close()


def test_passed_in_session_is_used_as_is():
    session = requests.Session()
    session.trust_env = True
    session.proxies = {"http": "http://corp.example:3128"}
    try:
        client = QmtHttpClient(session=session)
        assert client._session is session
        assert session.trust_env is True
        assert session.proxies == {"http": "http://corp.example:3128"}
    finally:
        session.close()
