# -*- coding: utf-8 -*-
"""会话 / 列式因果链 / 策略乙 / 显式事件"""
from __future__ import annotations

import pytest


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    import core.config as cfg

    vault = tmp_path / "TradingVault"
    db = tmp_path / "session_index.sqlite"
    monkeypatch.setattr(cfg, "ANNOTATION_VAULT_PATH", vault)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)

    import data.session_repo as sr
    import services.session_service as ss

    monkeypatch.setattr(sr, "SESSION_INDEX_DB", db)
    sr._repo = None
    ss._svc = None

    service = ss.get_session_service()
    service.repo = sr.SessionRepo(db_path=db)
    return service, vault


def test_new_session_pauses_previous(svc):
    service, _ = svc
    a = service.create_session(title="A")
    assert a["status"] == "drafting"
    b = service.create_session(title="B")
    assert b["id"] != a["id"]
    a2 = service.get_session(a["id"])
    assert a2["status"] == "paused"
    assert service.repo.get_active_id() == b["id"]


def test_create_cause_does_not_auto_event(svc):
    """点因建链 ≠ 事件。"""
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True, title="根因")
    assert len(s["causes"]) == 1
    assert len(s["effects"]) == 1
    assert len(s.get("events") or []) == 0
    assert s["ui"].get("active_event_id") is None
    assert s["ui"]["side"] == "cause"
    assert any(x.get("id") == s["causes"][0]["id"] for x in s.get("root_order") or [])


def test_nested_cause_under_active_chain(svc):
    """在已有链上再建因 = 子链（子集缩进），不是兄弟。"""
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True, title="根")
    root = s["causes"][0]
    # 模拟前端：有 active 时 child_cause(parent=active)
    s = service.create_cause(s, parent_id=root["id"], title="子")
    child = next(c for c in s["causes"] if c["title"] == "子")
    assert child["parent_id"] == root["id"]
    assert child["depth"] == 1
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    assert any(
        x.get("type") == "chain" and x.get("id") == child["id"]
        for x in root["children_order"]
    )
    # 事件仍未预制
    assert len(s.get("events") or []) == 0


def test_focus_cause_and_effect_not_events(svc):
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    root = s["causes"][0]
    s = service.focus_cause(s, root["id"])
    assert s["ui"]["active_event_id"] is None
    assert len(s.get("events") or []) == 0

    ef = next(e for e in s["effects"] if e["cause_id"] == root["id"])
    s = service.click_effect(s, ef["id"])  # collecting
    assert ef["phase"] == "collecting" or next(
        e for e in s["effects"] if e["id"] == ef["id"]
    )["phase"] == "collecting"
    assert s["ui"]["side"] == "effect"
    assert s["ui"]["active_event_id"] is None
    assert len(s.get("events") or []) == 0


def test_explicit_event_on_chain_order(svc):
    """事件显式创建，写入 children_order，可与子链穿插。"""
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    root = s["causes"][0]
    s = service.start_event(s, cause_id=root["id"])
    e1 = s["ui"]["active_event_id"]
    assert e1
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    assert any(x.get("type") == "event" and x.get("id") == e1 for x in root["children_order"])

    s = service.create_cause(s, parent_id=root["id"], title="子1")
    child = [c for c in s["causes"] if c["title"] == "子1"][0]
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    types = [x["type"] for x in root["children_order"]]
    assert "event" in types and "chain" in types
    # 事件在子链之前（先建事件再嵌套）
    assert types.index("event") < types.index("chain")

    s = service.start_event(s, cause_id=root["id"])
    e2 = s["ui"]["active_event_id"]
    assert e2 != e1
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    assert [x["type"] for x in root["children_order"]] == ["event", "chain", "event"]


def test_collect_to_event_or_summary(svc):
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    root = s["causes"][0]
    # 无事件：写因汇总
    s = service.append_kbars(
        s,
        [
            {
                "date": "2024-01-02",
                "price_element": "low",
                "price": 1.0,
                "symbol": "x",
                "period": "daily",
                "volume": 1000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
            }
        ],
    )
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    assert len(root["kbars"]) == 1
    assert root["kbars"][0].get("volume") == 1000
    assert len(s.get("events") or []) == 0

    s = service.start_event(s, cause_id=root["id"])
    eid = s["ui"]["active_event_id"]
    # 同一根K选两次 → 两个并列 kbar 元素
    kb = {
        "date": "2024-01-03",
        "timestamp": 3,
        "price_element": "high",
        "price": 2.0,
        "symbol": "x",
        "period": "daily",
        "vol": 5000,
        "open": 2,
        "high": 3,
        "low": 1,
        "close": 2.5,
    }
    s = service.append_kbars(s, [kb])
    s = service.append_kbars(s, [kb])
    s = service.append_note(s, "事件备注")
    s = service.set_overlays_on_active(
        s, [{"id": "ov1", "type": "horizontalStraightLine", "points": [{"value": 10}]}]
    )
    s = service.set_overlays_on_active(
        s,
        [
            {"id": "ov1", "type": "horizontalStraightLine", "points": [{"value": 11}]},
            {"id": "ov2", "type": "segment", "points": [{"value": 1}, {"value": 2}]},
        ],
    )
    ev = next(e for e in s["events"] if e["id"] == eid)
    kinds = [e["kind"] for e in ev["elements"]]
    assert kinds.count("kbar") == 2  # 并列可重复
    assert kinds.count("note") == 1
    assert kinds.count("overlay") == 2  # ov1 更新不新增，ov2 新增
    assert all(e["kbars"][i].get("volume") == 5000 for i in range(2) for e in [ev])
    # 有 active 事件时不写因汇总
    root = next(c for c in s["causes"] if c["id"] == root["id"])
    assert len(root["kbars"]) == 1
    assert not any(n.get("text") == "事件备注" for n in (root.get("notes") or []))

    # 删除单个元素
    el_id = ev["elements"][0]["id"]
    s = service.delete_element(s, event_id=eid, element_id=el_id)
    ev = next(e for e in s["events"] if e["id"] == eid)
    assert len([e for e in ev["elements"] if e["kind"] == "kbar"]) == 1


def test_delete_event_and_cause(svc):
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    root = s["causes"][0]
    s = service.start_event(s, cause_id=root["id"])
    e1 = s["ui"]["active_event_id"]
    s = service.create_cause(s, parent_id=root["id"], title="子")
    child = next(c for c in s["causes"] if c["title"] == "子")
    s = service.start_event(s, cause_id=child["id"])
    e2 = s["ui"]["active_event_id"]
    assert len(s["events"]) == 2

    s = service.delete_event(s, e2)
    assert not any(e["id"] == e2 for e in s["events"])
    assert any(e["id"] == e1 for e in s["events"])

    s = service.delete_cause(s, child["id"], recursive=True)
    assert not any(c["id"] == child["id"] for c in s["causes"])
    # 子链事件已随删
    assert all(e.get("cause_id") != child["id"] for e in s["events"])

    s = service.delete_cause(s, root["id"], recursive=True)
    assert s["causes"] == []
    assert s["effects"] == []
    assert s["events"] == []


def test_multi_depth_effect_close_guard(svc):
    service, vault = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True, title="根因")
    root = s["causes"][0]
    assert root["depth"] == 0

    s = service.create_cause(s, parent_id=root["id"], title="子1")
    c1 = [c for c in s["causes"] if c["title"] == "子1"][0]
    assert c1["depth"] == 1

    s = service.create_cause(s, parent_id=c1["id"], title="孙1")
    c2 = [c for c in s["causes"] if c["title"] == "孙1"][0]
    assert c2["depth"] == 2

    e_root = next(e for e in s["effects"] if e["cause_id"] == root["id"])
    e1 = next(e for e in s["effects"] if e["cause_id"] == c1["id"])
    e2 = next(e for e in s["effects"] if e["cause_id"] == c2["id"])

    s = service.click_effect(s, e_root["id"])
    assert next(e for e in s["effects"] if e["id"] == e_root["id"])["phase"] == "collecting"
    with pytest.raises(ValueError, match="未闭合"):
        service.click_effect(s, e_root["id"])

    s = service.click_effect(s, e2["id"])
    s = service.click_effect(s, e2["id"])
    s = service.click_effect(s, e1["id"])
    s = service.click_effect(s, e1["id"])
    s = service.click_effect(s, e_root["id"])
    assert next(e for e in s["effects"] if e["id"] == e_root["id"])["phase"] == "closed"

    s = service.commit_session(s["id"], s)
    from pathlib import Path

    assert Path(s["vault"]["abs_md"]).is_file()


def test_reactivate_paused(svc):
    service, _ = svc
    a = service.create_session(title="A")
    service.create_cause(a, as_root=True)
    service.save_progress(a, write_vault=False)
    service.create_session(title="B")
    a2 = service.activate_session(a["id"])
    assert a2["status"] == "drafting"
    assert len(a2.get("causes") or []) >= 1


def test_focus_event(svc):
    service, _ = svc
    s = service.create_session()
    s = service.create_cause(s, as_root=True)
    root = s["causes"][0]
    s = service.start_event(s, cause_id=root["id"])
    e1 = s["ui"]["active_event_id"]
    s = service.start_event(s, cause_id=root["id"])
    e2 = s["ui"]["active_event_id"]
    assert e2 != e1
    s = service.focus_event(s, e1)
    assert s["ui"]["active_event_id"] == e1
    assert s["ui"]["active_cause_id"] == root["id"]
