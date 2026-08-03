"""WebSocket quote snapshots are emitted only on visible/boundary changes."""
import json

import services.nav_spot_service as nav
from services.realtime_websocket import RealtimeWebSocket, _indices_fingerprint


class _Client:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(json.loads(message))


def test_price_fingerprint_ignores_quote_timestamps_and_source_time():
    first = {
        "SPX": {
            "price": 6300,
            "changePct": 0.5,
            "timestamp": 1,
            "time": "10:00:00",
            "channel": "first",
            "market_open": True,
        }
    }
    second = {
        "SPX": {
            "price": 6300,
            "change_pct": 0.5,
            "timestamp": 2,
            "time": "10:00:03",
            "channel": "second",
            "market_open": True,
        }
    }

    assert _indices_fingerprint(first, ["us:live:1"]) == _indices_fingerprint(
        second, ["us:live:1"]
    )


def test_duplicate_snapshot_is_skipped_but_market_boundary_broadcasts(monkeypatch):
    client = _Client()
    websocket = RealtimeWebSocket()
    websocket._clients.add(client)
    snapshots = iter(
        [
            {
                "data": {"SPX": {"price": 6300, "changePct": 0.5, "timestamp": 1}},
                "meta": {"market_signature": ["us:live:1"]},
            },
            {
                "data": {"SPX": {"price": 6300, "changePct": 0.5, "timestamp": 2}},
                "meta": {"market_signature": ["us:live:1"]},
            },
            {
                "data": {"SPX": {"price": 6300, "changePct": 0.5, "timestamp": 3}},
                "meta": {"market_signature": ["us:closed:0"]},
            },
        ]
    )
    monkeypatch.setattr(nav, "fetch_nav_spots_fast", lambda: next(snapshots))

    assert websocket._broadcast_indices() is True
    assert websocket._broadcast_indices() is False
    assert websocket._broadcast_indices() is True
    assert len(client.messages) == 2
    assert client.messages[-1]["type"] == "indices_update"


def test_new_client_invalidates_fingerprint_for_one_initial_snapshot():
    websocket = RealtimeWebSocket()
    websocket._last_indices_fingerprint = "existing-snapshot"
    client = _Client()

    websocket._register_client(client)

    assert client in websocket._clients
    assert websocket._last_indices_fingerprint is None


def test_ping_heartbeat_is_preserved():
    client = _Client()
    websocket = RealtimeWebSocket()

    websocket._handle_message(client, json.dumps({"action": "ping"}))

    assert client.messages == [{"type": "pong"}]
