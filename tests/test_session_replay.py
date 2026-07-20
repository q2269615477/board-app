"""会话回放测试：replay 端点 + 复用到 activate 流程。"""
import pytest


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    import core.config as cfg
    from data.session_repo import SessionRepo
    import data.session_repo as sr
    import services.session_service as ss

    vault = tmp_path / "TradingVault"
    db = tmp_path / "session_index.sqlite"
    monkeypatch.setattr(cfg, "ANNOTATION_VAULT_PATH", vault)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sr, "SESSION_INDEX_DB", db)
    sr._repo = None
    ss._svc = None

    service = ss.get_session_service()
    service.repo = SessionRepo(db_path=db)
    return service, vault


def _build_session_with_chart(svc):
    service, _ = svc
    s = service.create_session()
    s, ch = service.ensure_chart(
        s,
        symbol="sh000001",
        period="daily",
        symbol_name="上证指数",
        asset_type="index",
    )
    s = service.save_progress(s, write_vault=False)
    return s, ch


def test_replay_endpoint_returns_chart_and_overlays(svc):
    from app import create_app

    app = create_app()
    client = app.test_client()
    s, ch = _build_session_with_chart(svc)
    sid = s["id"]
    # 模拟添加一些 overlays 到 chart
    s2 = client.put(
        f"/api/sessions/{sid}",
        json={
            "rev": s["rev"],
            "base_rev": s["rev"],
            "charts": [
                {
                    "id": ch["id"],
                    "symbol": "sh000001",
                    "period": "daily",
                    "overlays": [
                        {"id": "ov1", "type": "horizontalStraightLine", "points": [{"value": 3000}]},
                        {"id": "ov2", "type": "segment", "points": [{"value": 1}, {"value": 2}]},
                    ],
                    "visible_range": {"from": 100, "to": 200},
                }
            ],
            "current_chart_id": ch["id"],
        },
    )
    assert s2.status_code == 200
    # 调 replay 端点
    r = client.get(f"/api/sessions/{sid}/replay")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["chart"]["symbol"] == "sh000001"
    assert body["chart"]["period"] == "daily"
    assert len(body["overlays"]) == 2
    ov_ids = {o["id"] for o in body["overlays"]}
    assert ov_ids == {"ov1", "ov2"}
    assert body["visible_range"] == {"from": 100, "to": 200}


def test_replay_with_event_id_returns_event_chart(svc):
    """传 event_id 时返回该事件所在 chart 的 overlays。"""
    from app import create_app
    from services.session_service import get_session_service

    app = create_app()
    client = app.test_client()
    s, ch = _build_session_with_chart(svc)
    service = get_session_service()
    s2 = service.create_cause(s, as_root=True, title="根因")
    s2 = service.start_event(s2, cause_id=s2["causes"][0]["id"])
    s2 = service.append_kbars(
        s2, [{"date": "2024-01-02", "close": 1.0, "open": 1, "high": 2, "low": 0.5, "volume": 100}]
    )
    s2 = service.save_progress(s2, write_vault=False)
    sid = s2["id"]
    eid = s2["ui"]["active_event_id"]

    r = client.get(f"/api/sessions/{sid}/replay?event_id={eid}")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["chart"]["symbol"] == "sh000001"
    # event 有 kbar 元素 → 至少返回 1 个 kbar
    assert len(body["elements"]) >= 1
    assert body["elements"][0]["kind"] == "kbar"


def test_replay_with_specific_chart_id(svc):
    """chart_id 参数支持多图场景。"""
    from app import create_app

    app = create_app()
    client = app.test_client()
    s, ch1 = _build_session_with_chart(svc)
    # 加第二个 chart
    from services.session_service import get_session_service

    service = get_session_service()
    s2, ch2 = service.ensure_chart(
        s, symbol="sz399006", period="daily", symbol_name="创业板指"
    )
    s2 = service.save_progress(s2, write_vault=False)
    sid = s2["id"]
    # 调 replay for ch2
    r = client.get(f"/api/sessions/{sid}/replay?chart_id={ch2['id']}")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["chart"]["symbol"] == "sz399006"


def test_replay_no_chart_returns_404_or_empty(svc):
    from app import create_app

    app = create_app()
    client = app.test_client()
    # 无 chart 的会话
    r = client.post("/api/sessions", json={"title": "empty"})
    sid = r.get_json()["data"]["id"]
    r2 = client.get(f"/api/sessions/{sid}/replay")
    # 没 chart 时返 200 + empty payload（前端可走 ensure_chart 创建）
    assert r2.status_code == 200
    body = r2.get_json()["data"]
    assert body["chart"] is None
    assert body["overlays"] == []
