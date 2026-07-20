"""会话乐观锁单测：覆盖 rev 字段、base_rev 校验、409 响应结构。"""
import pytest
import json


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


def test_new_session_has_rev_zero(svc):
    service, _ = svc
    s = service.create_session()
    assert s.get("rev") == 0


def test_save_progress_increments_rev(svc):
    service, _ = svc
    s = service.create_session()
    s = service.save_progress(s, write_vault=False)
    assert s.get("rev") == 1
    s = service.save_progress(s, write_vault=False)
    assert s.get("rev") == 2
    s = service.save_progress(s, write_vault=False)
    assert s.get("rev") == 3


def test_save_with_matching_base_rev_succeeds(svc):
    service, _ = svc
    s = service.create_session()
    # base_rev=None 时跳过校验（向后兼容）
    s = service.save_progress(s, write_vault=False, expected_rev=None)
    assert s.get("rev") == 1
    # 显式 base_rev=0 应通过
    s = service.save_progress(s, write_vault=False, expected_rev=1)
    assert s.get("rev") == 2


def test_save_with_mismatched_base_rev_raises_conflict(svc):
    from services.session_service import RevisionConflictError

    service, _ = svc
    s = service.create_session()
    s = service.save_progress(s, write_vault=False)  # rev=1
    s = service.save_progress(s, write_vault=False)  # rev=2
    # 现在服务端 rev=2，客户端 base_rev=0 应当冲突
    with pytest.raises(RevisionConflictError) as exc:
        service.save_progress(s, write_vault=False, expected_rev=0)
    assert exc.value.current_rev == 2
    assert exc.value.current_session["id"] == s["id"]
    assert exc.value.current_session.get("rev") == 2


def test_actions_bump_rev_without_base_rev(svc):
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)  # action 写
    # save_progress 之前已有 rev
    assert s.get("rev") == 0
    s = service.save_progress(s, write_vault=False)  # rev=1
    # 后续 action → save 链路
    s2 = service.create_cause(s, as_root=False, title="new chain")  # 写新链
    s2 = service.save_progress(s2, write_vault=False)  # rev=2
    assert s2.get("rev") == 2


def test_commit_also_enforces_base_rev(svc):
    from services.session_service import RevisionConflictError

    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    s = service.save_progress(s, write_vault=False)  # rev=1

    # 模拟其他客户端已经把 rev 推到 5
    s2 = service.get_session(s["id"])
    s2 = service.save_progress(s2, write_vault=False)  # rev=2
    s2 = service.save_progress(s2, write_vault=False)  # rev=3
    s2 = service.save_progress(s2, write_vault=False)  # rev=4
    s2 = service.save_progress(s2, write_vault=False)  # rev=5

    # 旧 client (base_rev=1) 提交 commit → 应冲突
    with pytest.raises(RevisionConflictError) as exc:
        service.commit_session(s["id"], s, expected_rev=1)
    assert exc.value.current_rev == 5


def test_normalize_session_backfills_rev(svc):
    service, _ = svc
    s = service.create_session()
    # 模拟旧会话无 rev
    raw = service.get_session(s["id"])
    raw.pop("rev", None)
    normalized = service.normalize_session(raw)
    assert normalized.get("rev") == 0


def test_api_returns_409_on_revision_conflict(svc):
    """API 层把 RevisionConflictError → 409 + REVISION_CONFLICT。"""
    from app import create_app

    app = create_app()
    client = app.test_client()

    # 创建会话
    r = client.post("/api/sessions", json={"title": "test"})
    assert r.status_code == 201
    sid = r.get_json()["data"]["id"]
    assert r.get_json()["data"].get("rev") == 0

    # save 一次 → rev=1
    r2 = client.put(f"/api/sessions/{sid}", json={"rev": 0, "base_rev": 0, "title": "x"})
    assert r2.status_code == 200
    assert r2.get_json()["data"].get("rev") == 1

    # 故意带过期 base_rev=0 → 409
    r3 = client.put(f"/api/sessions/{sid}", json={"rev": 0, "base_rev": 0, "title": "x"})
    assert r3.status_code == 409
    body = r3.get_json()
    assert body.get("code") == "REVISION_CONFLICT"
    assert body.get("current_rev") == 1
    assert body.get("current_session", {}).get("id") == sid
    assert body.get("current_session", {}).get("rev") == 1
