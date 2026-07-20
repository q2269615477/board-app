"""会话 committed 只读 + clone 测试。"""
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


def _build_committed(svc):
    """构造一个已 committed 的会话（含 1 因 + 1 事件）。"""
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True, title="根因")
    s = service.save_progress(s, write_vault=False)
    s = service.commit_session(s["id"], s)
    assert s["status"] == "committed"
    return s


def test_save_progress_on_committed_raises_readonly(svc):
    from services.session_service import ReadOnlySessionError

    service, _ = svc
    s = _build_committed(svc)
    # 任意后续 save_progress 应当抛 ReadOnlySessionError
    with pytest.raises(ReadOnlySessionError):
        service.save_progress(s, write_vault=False)


def test_mutating_actions_on_committed_raise_readonly(svc):
    from services.session_service import ReadOnlySessionError

    service, _ = svc
    s = _build_committed(svc)
    # create_cause / start_event / append_note / append_kbars / overlays / delete 全部要拦
    with pytest.raises(ReadOnlySessionError):
        service.create_cause(s, as_root=True, title="after-commit")
    # start_event
    with pytest.raises(ReadOnlySessionError):
        service.start_event(s, cause_id=s["causes"][0]["id"])
    # append_kbars
    with pytest.raises(ReadOnlySessionError):
        service.append_kbars(s, [{"date": "2024-01-02", "close": 1.0}])
    # append_note
    with pytest.raises(ReadOnlySessionError):
        service.append_note(s, "after-commit")
    # overlays
    with pytest.raises(ReadOnlySessionError):
        service.set_overlays_on_active(
            s, [{"id": "ov", "type": "horizontalStraightLine"}]
        )
    # delete_cause
    with pytest.raises(ReadOnlySessionError):
        service.delete_cause(s, s["causes"][0]["id"])
    # click_effect
    with pytest.raises(ReadOnlySessionError):
        service.click_effect(s, s["effects"][0]["id"])


def test_focus_actions_allowed_when_committed(svc):
    """只读：聚焦/切换等不修改状态的操作仍允许（UI 仍可浏览）。"""
    service, _ = svc
    s = _build_committed(svc)
    # focus_cause 不应抛（虽是 committed，但仍允许切焦点）
    s2 = service.focus_cause(s, s["causes"][0]["id"])
    assert s2["ui"]["active_cause_id"] == s["causes"][0]["id"]
    # 先建一个事件再 focus_event（committed 后建事件应被 readonly 拦；测试 focus 路径用
    # 现有结构：focus_event 在没有 active_cause 时不抛 readonly 异常，焦点可保持）


def test_recommit_idempotent(svc):
    """已 committed 的会话再次 commit 是幂等的（重新写 vault + bump rev）。"""
    service, _ = svc
    s = _build_committed(svc)
    rev0 = s["rev"]
    s2 = service.commit_session(s["id"], s)
    assert s2["status"] == "committed"
    assert s2["rev"] >= rev0 + 1


def test_clone_creates_new_drafting_session(svc):
    service, _ = svc
    s = _build_committed(svc)
    # clone：基于 committed 会话创建新 drafting 会话，复用 charts/causes/effects/events
    cloned = service.clone_session(s["id"])
    assert cloned["id"] != s["id"]
    assert cloned["status"] == "drafting"
    assert cloned.get("rev") == 0
    # 因果链结构应继承
    assert len(cloned.get("causes") or []) == len(s.get("causes") or [])
    assert len(cloned.get("effects") or []) == len(s.get("effects") or [])
    # ui 重置（无 active 焦点）
    ui = cloned.get("ui") or {}
    assert ui.get("active_cause_id") is None
    assert ui.get("active_event_id") is None
    # 标题加 "(副本)"
    assert "副本" in cloned.get("title", "")


def test_api_save_on_committed_returns_409(svc):
    from app import create_app

    app = create_app()
    client = app.test_client()
    # 建一个 committed 会话
    r = client.post("/api/sessions", json={"title": "to-commit"})
    sid = r.get_json()["data"]["id"]
    # commit
    r2 = client.post(f"/api/sessions/{sid}/commit", json={})
    assert r2.status_code == 200
    assert r2.get_json()["data"]["status"] == "committed"
    # 后续 save 应 409
    r3 = client.put(f"/api/sessions/{sid}", json={"title": "try modify"})
    assert r3.status_code == 409
    body = r3.get_json()
    assert body.get("code") == "COMMITTED_READONLY"
    assert body.get("current_status") == "committed"
    # 错误信息中提示 clone
    assert "克隆" in (body.get("error") or "") or "clone" in (body.get("error") or "")


def test_api_action_on_committed_returns_409(svc):
    from app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/sessions", json={"title": "x"})
    sid = r.get_json()["data"]["id"]
    # 先建一个因，再 commit
    client.post(
        f"/api/sessions/{sid}/actions",
        json={"action": "major_cause", "title": "因"},
    )
    r2 = client.post(f"/api/sessions/{sid}/commit", json={})
    assert r2.status_code == 200
    # 后续 action 应 409
    r3 = client.post(
        f"/api/sessions/{sid}/actions",
        json={"action": "major_cause", "title": "after-commit"},
    )
    assert r3.status_code == 409
    body = r3.get_json()
    assert body.get("code") == "COMMITTED_READONLY"


def test_api_clone_endpoint(svc):
    from app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/sessions", json={"title": "original"})
    sid = r.get_json()["data"]["id"]
    client.post(f"/api/sessions/{sid}/commit", json={})
    r2 = client.post(f"/api/sessions/{sid}/clone")
    assert r2.status_code == 201
    new_sess = r2.get_json()["data"]
    assert new_sess["id"] != sid
    assert new_sess["status"] == "drafting"
    assert "副本" in new_sess["title"]
