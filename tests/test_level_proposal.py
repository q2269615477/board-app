"""Phase 3：候选支撑位提议 + 用户画法画像 + 反馈闭环"""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture()
def psvc(tmp_path, monkeypatch):
    vault = tmp_path / "TradingVault"
    db = tmp_path / "annotation_index.sqlite"
    import core.config as cfg
    monkeypatch.setattr(cfg, "ANNOTATION_VAULT_PATH", vault)
    monkeypatch.setattr(cfg, "ANNOTATION_INDEX_DB", db)
    monkeypatch.setattr(cfg, "OBSIDIAN_VAULT_NAME", "TradingVault")
    monkeypatch.setenv("ANNOTATION_VAULT_PATH", str(vault))

    import data.annotation_repo as ar
    monkeypatch.setattr(ar, "ANNOTATION_INDEX_DB", db)
    ar._repo = ar.AnnotationRepo(db_path=db)

    import services.annotation_service as asvc
    import services.level_proposal_service as lps
    asvc._svc = None
    lps._svc = None

    # 造一段带明显局部低点的 K 线
    rows = []
    base = 100.0
    for i in range(120):
        # 每 20 根制造一个深坑
        dip = -6.0 if i % 20 == 10 else 0.0
        c = base + (i * 0.15) + dip
        rows.append({
            "date": (pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": c + 0.5, "high": c + 1.2, "low": c - 1.0, "close": c,
            "volume": 1000 + (900 if dip else 0) + i,
        })
    df = pd.DataFrame(rows)
    monkeypatch.setattr(lps, "_load_bars", lambda sym, per: df.to_dict("records"))

    return asvc.get_annotation_service(), lps.get_level_proposal_service()


def test_propose_returns_ranked_candidates_with_evidence(psvc):
    _, prop = psvc
    r = prop.propose("BK0727", "daily", top_n=5)
    assert r["candidates"], "应产出候选"
    assert len(r["candidates"]) <= 5
    scores = [c["score"] for c in r["candidates"]]
    assert scores == sorted(scores, reverse=True), "候选应按分数降序"
    for c in r["candidates"]:
        # 候选价必须来自真实K的价格要素，不能凭空造价
        ohlc = c["source_bar"]["ohlc"]
        assert c["price"] == pytest.approx(ohlc[c["source_bar"]["price_element"]], abs=1e-6)
        assert c["evidence"], "每条候选必须有可复述的依据"
        assert "feature_snapshot" in c


def test_cold_start_confidence_is_low(psvc):
    _, prop = psvc
    r = prop.propose("BK0727", "daily")
    assert r["user_style"]["sample_count"] == 0
    assert r["user_style"]["confidence"] == "low"
    assert "样本" in r["agent_hint"]      # 必须如实告知冷启动不准


def test_style_learns_price_element_preference(psvc):
    """用户偏好用 close 画线 → 画像里 close 占比应最高"""
    ann, prop = psvc
    for i in range(4):
        ann.create_case({
            "type": "level_origin", "symbol": "BK0727", "period": "daily",
            "source_bar": {
                "date": f"2025-02-0{i+1}",
                "ohlc": {"open": 10, "high": 12, "low": 9, "close": 11},
                "price_element": "close",
            },
            "level": {"role": "support"},
            "feature_snapshot": {"is_local_extremum": True, "volume_percentile": 0.9},
        })
    style = prop.user_style()
    assert style["sample_count"] == 4
    assert max(style["price_element_pref"], key=style["price_element_pref"].get) == "close"
    assert style["extremum_rate"] == 1.0


def test_existing_levels_not_reproposed(psvc):
    """已画过的位不应再次被提议"""
    ann, prop = psvc
    first = prop.propose("BK0727", "daily", top_n=3)["candidates"][0]
    ann.create_case({
        "type": "level_origin", "symbol": "BK0727", "period": "daily",
        "source_bar": first["source_bar"],
        "level": {"role": first["role"]},
    })
    again = prop.propose("BK0727", "daily", top_n=5)
    for c in again["candidates"]:
        assert abs(c["price"] - first["price"]) / first["price"] >= 0.005


def test_feedback_records_negative_sample(psvc):
    """reject 必须落库为负样本（用户只画正例，负例更稀缺）"""
    _, prop = psvc
    cand = prop.propose("BK0727", "daily", top_n=1)["candidates"][0]
    prop.repo.record_proposal_feedback({
        "symbol": "BK0727", "period": "daily", "price": cand["price"],
        "role": cand["role"], "verdict": "rejected", "reason": "量能不够",
        "candidate": cand,
    })
    negs = prop.repo.list_proposal_feedback(verdict="rejected")
    assert len(negs) == 1
    assert negs[0]["reason"] == "量能不够"
    assert prop.repo.list_proposal_feedback(verdict="accepted") == []


def test_style_learns_role_preference(psvc):
    """用户只画支撑 → 支撑候选的相似度应高于阻力候选（否则会推一堆阻力位）"""
    ann, prop = psvc
    for i in range(5):
        ann.create_case({
            "type": "level_origin", "symbol": "BK0727", "period": "daily",
            "source_bar": {
                "date": f"2025-02-1{i}",
                "ohlc": {"open": 10, "high": 12, "low": 9, "close": 11},
                "price_element": "low",
            },
            "level": {"role": "support"},
            "feature_snapshot": {"is_local_extremum": True, "volume_percentile": 0.8},
        })
    style = prop.user_style()
    assert style["role_pref"] == {"support": 5}

    feats = {"is_local_extremum": True, "volume_percentile": 0.8}
    sup = prop._style_similarity(feats, "low", style, "support")
    res = prop._style_similarity(feats, "low", style, "resistance")
    assert sup > res, "支撑候选应比阻力候选更像用户画法"

    r = prop.propose("BK0727", "daily", top_n=6)
    sups = [c for c in r["candidates"] if c["role"] == "support"]
    assert sups, "以画支撑为主的用户应能拿到支撑候选"
