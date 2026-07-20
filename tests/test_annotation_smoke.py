"""Case / Relation / vault 双写烟雾测试"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# conftest 已设置环境；此处再强制隔离 vault/db
PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_svc(tmp_path, monkeypatch):
    vault = tmp_path / "TradingVault"
    db = tmp_path / "annotation_index.sqlite"
    monkeypatch.setenv("ANNOTATION_VAULT_PATH", str(vault))
    # 强制重载 config 与单例
    import core.config as cfg

    monkeypatch.setattr(cfg, "ANNOTATION_VAULT_PATH", vault)
    monkeypatch.setattr(cfg, "ANNOTATION_INDEX_DB", db)
    monkeypatch.setattr(cfg, "OBSIDIAN_VAULT_NAME", "TradingVault")

    import data.annotation_repo as ar
    import services.annotation_service as asvc

    monkeypatch.setattr(ar, "ANNOTATION_INDEX_DB", db)
    ar._repo = None
    asvc._svc = None

    svc = asvc.get_annotation_service()
    # repo 指向 tmp db
    svc.repo = ar.AnnotationRepo(db_path=db)
    # 确认 vault_root 跟 tmp 对齐
    from services import vault_writer as vw

    assert vw.vault_root() == vault
    return svc, vault


def test_create_level_origin_writes_vault(isolated_svc):
    svc, vault = isolated_svc
    case = svc.create_case(
        {
            "type": "level_origin",
            "symbol": "sh000001",
            "symbol_name": "上证指数",
            "asset_type": "index",
            "period": "daily",
            "source_bar": {
                "date": "2024-01-15",
                "ohlc": {"open": 2900.0, "high": 2920.0, "low": 2880.5, "close": 2910.0},
                "price_element": "low",
            },
            "level": {"role": "support"},
            "notes": "测试支撑位",
            "reminders": [
                {"at": "2020-01-01T00:00:00", "message": "已过期提醒", "status": "pending"}
            ],
        }
    )
    assert case["id"]
    assert case["level"]["price"] == 2880.5
    assert case["overlays"]
    md = Path(case["vault"]["abs_md"])
    assert md.is_file()
    text = md.read_text(encoding="utf-8")
    assert "level_origin" in text
    assert "2880.5" in text
    assert "测试支撑位" in text
    ov_path = vault / case["vault"]["overlays_relpath"]
    assert ov_path.is_file()
    got = svc.get_case(case["id"])
    assert got and got["notes"] == "测试支撑位"
    due = svc.list_due_reminders()
    assert any(d.get("message") == "已过期提醒" for d in due)


def test_chart_annotation_captures_overlays_to_vault(isolated_svc):
    """绘图几何 → chart-annotation → vault md + overlays.json"""
    svc, vault = isolated_svc
    case = svc.create_case(
        {
            "type": "chart-annotation",
            "symbol": "600519",
            "symbol_name": "贵州茅台",
            "asset_type": "stock",
            "period": "daily",
            "notes": "水平支撑画线采集测试",
            "overlays": [
                {
                    "id": "ov_hl_1",
                    "type": "horizontalLine",
                    "points": [{"timestamp": 1721260800000, "value": 1500.5}],
                    "styles": {},
                },
                {
                    "id": "ov_seg_1",
                    "type": "segment",
                    "points": [
                        {"timestamp": 1721260800000, "value": 1500.5},
                        {"timestamp": 1723939200000, "value": 1600.0},
                    ],
                },
            ],
            "intent": "drawing_capture",
            "tags": ["采集/图表画线"],
        }
    )
    assert case["id"].startswith("case_")
    assert len(case["overlays"]) == 2
    md = Path(case["vault"]["abs_md"])
    assert md.is_file()
    text = md.read_text(encoding="utf-8")
    assert "画线几何" in text
    assert "horizontalLine" in text
    assert "水平支撑画线采集测试" in text
    assert "Agent 学习说明" in text
    ov_path = vault / case["vault"]["overlays_relpath"]
    assert ov_path.is_file()
    ov_data = json.loads(ov_path.read_text(encoding="utf-8"))
    assert ov_data["case_id"] == case["id"]
    assert len(ov_data["overlays"]) == 2
    # 检索
    hits = svc.search_cases("茅台", limit=10)
    assert any(h["id"] == case["id"] for h in hits)


def test_chart_annotation_requires_overlays(isolated_svc):
    svc, _ = isolated_svc
    with pytest.raises(ValueError, match="overlays"):
        svc.create_case(
            {
                "type": "chart-annotation",
                "symbol": "000001",
                "period": "daily",
                "notes": "无几何应失败",
            }
        )


def test_relation_note_no_auto_fields(isolated_svc):

    svc, vault = isolated_svc
    c1 = svc.create_case(
        {
            "type": "level_origin",
            "symbol": "BK1158",
            "symbol_name": "微盘股",
            "period": "daily",
            "source_bar": {
                "date": "2024-06-01",
                "ohlc": {"open": 1, "high": 2, "low": 0.9, "close": 1.1},
                "price_element": "low",
            },
        }
    )
    rel = svc.create_relation(
        {
            "relation_note": "红利见底≈科创见顶（用户声明）",
            "members": [
                {
                    "case_id": c1["id"],
                    "symbol": "BK1158",
                    "symbol_name": "微盘股",
                    "asset_type": "concept",
                    "period": "daily",
                }
            ],
            "auto_score": 0.99,  # 应被剥离
            "correlation": 0.8,
        }
    )
    assert "auto_score" not in rel
    assert "correlation" not in rel
    assert rel["relation_note"].startswith("红利见底")
    md = Path(rel["vault"]["abs_md"])
    assert md.is_file()
    assert "红利见底" in md.read_text(encoding="utf-8")
    c1b = svc.get_case(c1["id"])
    assert rel["id"] in (c1b.get("relation_ids") or [])


def test_level_origin_validation(isolated_svc):
    svc, _ = isolated_svc
    with pytest.raises(ValueError):
        svc.create_case(
            {
                "type": "level_origin",
                "symbol": "x",
                "period": "daily",
                "source_bar": {"ohlc": {"open": 1}, "price_element": "low"},
            }
        )
