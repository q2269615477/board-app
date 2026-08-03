import app  # noqa: F401 - initialize the application import graph first
from api import system_routes


def test_legacy_sse_connections_are_limited_to_one(monkeypatch):
    monkeypatch.setattr(system_routes, "_sse_stream_token", None)
    first = system_routes._claim_sse_stream(managed=False)
    assert first
    assert system_routes._claim_sse_stream(managed=False) is None
    assert system_routes._owns_sse_stream(first)
    system_routes._release_sse_stream(first)
    assert not system_routes._owns_sse_stream(first)


def test_managed_sse_connection_supersedes_legacy(monkeypatch):
    monkeypatch.setattr(system_routes, "_sse_stream_token", None)
    legacy = system_routes._claim_sse_stream(managed=False)
    managed = system_routes._claim_sse_stream(managed=True)
    assert managed and managed != legacy
    assert not system_routes._owns_sse_stream(legacy)
    assert system_routes._owns_sse_stream(managed)
    system_routes._release_sse_stream(legacy)
    assert system_routes._owns_sse_stream(managed)
    system_routes._release_sse_stream(managed)
