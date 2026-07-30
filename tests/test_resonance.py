"""Phase 2：跨板块共振支撑（确定性扫描）测试"""
from __future__ import annotations

import pytest


@pytest.fixture()
def rsvc(tmp_path, monkeypatch):
    vault = tmp_path / "TradingVault"
    db = tmp_path / "annotation_index.sqlite"
    import core.config as cfg

    monkeypatch.setattr(cfg, "ANNOTATION_VAULT_PATH", vault)
    monkeypatch.setattr(cfg, "ANNOTATION_INDEX_DB", db)
    monkeypatch.setattr(cfg, "OBSIDIAN_VAULT_NAME", "TradingVault")
    monkeypatch.setenv("ANNOTATION_VAULT_PATH", str(vault))

    import data.annotation_repo as ar
    monkeypatch.setattr(ar, "ANNOTATION_INDEX_DB", db)
    ar._repo = ar.AnnotationRepo(db_path=db)  # 共享单例 → tmp

    import services.annotation_service as asvc
    import services.resonance_service as rs
    asvc._svc = None
    rs._svc = None

    prices: dict = {}
    monkeypatch.setattr(rs, "get_current_price", lambda sym, period="daily": prices.get(sym))

    return asvc.get_annotation_service(), rs.get_resonance_service(), prices


def _mk_support(ann, sym, low, note):
    return ann.create_case({
        "type": "level_origin",
        "symbol": sym, "symbol_name": sym, "asset_type": "industry", "period": "daily",
        "source_bar": {
            "date": "2025-01-01",
            "ohlc": {"open": low + 5, "high": low + 8, "low": low, "close": low + 3},
            "price_element": "low",
        },
        "level": {"role": "support"},
        "notes": note,
    })


def test_resonance_two_of_three_aligned(rsvc):
    ann, res, prices = rsvc
    _mk_support(ann, "BK0727", 1000.0, "医疗龙头板块前低放量支撑")
    _mk_support(ann, "sh000933", 800.0, "医药指数箱体下沿")
    _mk_support(ann, "300760", 200.0, "迈瑞前高回踩")

    prices["BK0727"] = 1010.0    # 距支撑 1.0% → 到位
    prices["sh000933"] = 805.0   # 距支撑 0.625% → 到位
    prices["300760"] = 230.0     # 距支撑 15% → 远离

    group = res.create_group({
        "theme": "医疗", "threshold_pct": 3.0, "min_aligned": 2,
        "members": [
            {"symbol": "BK0727", "symbol_name": "医疗服务", "period": "daily", "role": "leader_board"},
            {"symbol": "sh000933", "symbol_name": "医药", "period": "daily", "role": "sector_index"},
            {"symbol": "300760", "symbol_name": "迈瑞医疗", "period": "daily", "role": "leader_stock"},
        ],
    })
    r = res.scan_by_id(group["id"])
    assert r["is_resonance"] is True
    assert r["aligned_count"] == 2
    assert {a["symbol"] for a in r["aligned"]} == {"BK0727", "sh000933"}
    # 每个到位成员附命中支撑的用户原话
    assert all(a["nearest_support"]["note"] for a in r["aligned"])
    assert any("箱体下沿" in a["nearest_support"]["note"] for a in r["aligned"])
    # 无自动判定字段（守铁律精神）
    for banned in ("correlation", "auto_score", "system_hypothesis"):
        assert banned not in r
    assert "disclaimer" in r


def test_resonance_below_min_aligned(rsvc):
    ann, res, prices = rsvc
    _mk_support(ann, "BK0727", 1000.0, "支撑A")
    _mk_support(ann, "sh000933", 800.0, "支撑B")
    prices["BK0727"] = 1010.0   # 到位
    prices["sh000933"] = 900.0  # 距 800 支撑 12.5% → 远
    group = res.create_group({
        "theme": "医疗", "threshold_pct": 3.0, "min_aligned": 2,
        "members": [
            {"symbol": "BK0727", "period": "daily"},
            {"symbol": "sh000933", "period": "daily"},
        ],
    })
    r = res.scan_by_id(group["id"])
    assert r["aligned_count"] == 1
    assert r["is_resonance"] is False


def test_group_rejects_auto_fields(rsvc):
    _, res, _ = rsvc
    g = res.create_group({"theme": "x", "members": [], "correlation": 0.9, "auto_score": 1.0})
    assert "correlation" not in g
    assert "auto_score" not in g


def test_matrix_cross_period_same_symbol(rsvc):
    """矩阵：同一标的的跨周期共振（用户明确要求支持的场景）"""
    ann, res, prices = rsvc
    # 同一标的在日线与周线各画一条支撑
    for period, low in (("daily", 1000.0), ("weekly", 980.0)):
        ann.create_case({
            "type": "level_origin", "symbol": "BK0727", "period": period,
            "source_bar": {
                "date": "2025-01-01",
                "ohlc": {"open": low + 5, "high": low + 8, "low": low, "close": low + 3},
                "price_element": "low",
            },
            "level": {"role": "support"},
            "notes": f"{period} 支撑",
        })
    prices["BK0727"] = 1010.0   # 距日线支撑 1.0%；距周线支撑 3.06%

    m = res.scan_matrix(
        {"theme": "医疗", "threshold_pct": 3.0, "min_aligned": 1,
         "members": [{"symbol": "BK0727", "symbol_name": "医疗服务"}]},
        periods=["daily", "weekly"],
    )
    assert m["periods"] == ["daily", "weekly"]
    row = m["rows"][0]
    cells = {c["period"]: c for c in row["cells"]}
    assert cells["daily"]["at_support"] is True
    assert cells["weekly"]["at_support"] is False      # 3.06% > 3%
    assert row["aligned_periods"] == ["daily"]
    assert m["per_period"]["daily"]["count"] == 1
    assert m["is_resonance"] is True                    # daily 达到 min_aligned=1


def test_matrix_require_periods(rsvc):
    """require_periods：用户指定必须同时到位的周期"""
    ann, res, prices = rsvc
    for sym in ("BK0727", "sh000933"):
        ann.create_case({
            "type": "level_origin", "symbol": sym, "period": "daily",
            "source_bar": {
                "date": "2025-01-01",
                "ohlc": {"open": 105, "high": 108, "low": 100, "close": 103},
                "price_element": "low",
            },
            "level": {"role": "support"}, "notes": f"{sym} 日线支撑",
        })
    prices["BK0727"] = 101.0
    prices["sh000933"] = 101.0

    group = {
        "threshold_pct": 3.0, "min_aligned": 2,
        "members": [{"symbol": "BK0727"}, {"symbol": "sh000933"}],
    }
    # 只要求 daily → 成立
    m1 = res.scan_matrix({**group, "require_periods": ["daily"]}, periods=["daily", "weekly"])
    assert m1["is_resonance"] is True
    assert m1["resonant_periods"] == ["daily"]
    # 要求 daily+weekly 同时 → 周线无支撑，不成立
    m2 = res.scan_matrix({**group, "require_periods": ["daily", "weekly"]}, periods=["daily", "weekly"])
    assert m2["is_resonance"] is False
    assert m2["per_period"]["weekly"]["count"] == 0


def test_matrix_no_auto_verdict_fields(rsvc):
    _, res, _ = rsvc
    m = res.scan_matrix({"members": [{"symbol": "X"}]}, periods=["daily"])
    for banned in ("correlation", "auto_score", "system_hypothesis", "verified_by_backtest"):
        assert banned not in m
    assert "disclaimer" in m


def test_broken_level_excluded_from_scan(rsvc):
    """生命周期：非 active 的位不参与共振扫描（避免旧位污染判定）"""
    ann, res, prices = rsvc
    c = ann.create_case({
        "type": "level_origin", "symbol": "BK0727", "period": "daily",
        "source_bar": {
            "date": "2025-01-01",
            "ohlc": {"open": 105, "high": 108, "low": 100, "close": 103},
            "price_element": "low",
        },
        "level": {"role": "support"}, "notes": "会被标记为已跌破",
    })
    assert c["level"]["status"] == "active"        # 默认 active
    prices["BK0727"] = 101.0                        # 距 100 支撑 1% → 到位

    group = {"threshold_pct": 3.0, "min_aligned": 1, "members": [{"symbol": "BK0727"}]}
    assert res.scan(group)["is_resonance"] is True

    # 用户标记为已跌破 → 不再参与
    ann.update_case(c["id"], {"level": {**c["level"], "status": "broken"}})
    r2 = res.scan(group)
    assert r2["is_resonance"] is False
    assert r2["members"][0]["support_count"] == 0
    assert r2["members"][0]["nearest_support"] is None


def test_auto_groups_only_include_symbols_with_levels(rsvc):
    """Phase 4：自动分组的范围 = 用户画过支撑位的标的"""
    ann, res, prices = rsvc
    for sym in ("BK0727", "BK0728"):
        ann.create_case({
            "type": "level_origin", "symbol": sym, "symbol_name": sym, "period": "daily",
            "source_bar": {
                "date": "2025-01-01",
                "ohlc": {"open": 105, "high": 108, "low": 100, "close": 103},
                "price_element": "low",
            },
            "level": {"role": "support"}, "notes": f"{sym} 支撑",
        })
    syms = {m["symbol"] for m in res.symbols_with_levels()}
    assert syms == {"BK0727", "BK0728"}
    groups = res.auto_groups()
    assert groups and sum(len(g["members"]) for g in groups) == 2


def test_scan_all_returns_only_resonant_sorted(rsvc):
    ann, res, prices = rsvc
    for sym in ("BK0727", "BK0728", "BK0729"):
        ann.create_case({
            "type": "level_origin", "symbol": sym, "symbol_name": sym, "period": "daily",
            "source_bar": {
                "date": "2025-01-01",
                "ohlc": {"open": 105, "high": 108, "low": 100, "close": 103},
                "price_element": "low",
            },
            "level": {"role": "support"}, "notes": f"{sym} 支撑",
        })
    prices["BK0727"] = 101.0   # 到位
    prices["BK0728"] = 101.5   # 到位
    prices["BK0729"] = 150.0   # 远离
    r = res.scan_all(threshold_pct=3.0, min_aligned=2)
    assert r["scanned_groups"] >= 1
    assert r["resonant_groups"] == len(r["results"])
    for g in r["results"]:
        assert g["is_resonance"] is True
    scores = [g["score"] for g in r["results"]]
    assert scores == sorted(scores, reverse=True)


def test_scan_all_excludes_broken_levels(rsvc):
    """被标记 broken 的位不进入扫描范围"""
    ann, res, prices = rsvc
    c = ann.create_case({
        "type": "level_origin", "symbol": "BK0727", "symbol_name": "BK0727", "period": "daily",
        "source_bar": {
            "date": "2025-01-01",
            "ohlc": {"open": 105, "high": 108, "low": 100, "close": 103},
            "price_element": "low",
        },
        "level": {"role": "support", "status": "broken"},
    })
    assert res.symbols_with_levels() == []


def test_price_below_support_is_not_at_support(rsvc):
    """跌破支撑不得判为「到位」。

    早期用 abs(price-lp) 算距离，于是"刚跌破2%"与"上方2%"同样进 aligned，
    一组标的同时破位反而被报成"共振成立" —— 与 MCP 给 Agent 的话术
    （"现价逼近支撑"）相反，会让 AI 复述出反向结论。
    """
    ann, res, prices = rsvc
    ann.create_case({
        "type": "level_origin", "symbol": "BK0727", "period": "daily",
        "source_bar": {
            "date": "2025-01-01",
            "ohlc": {"open": 1005, "high": 1008, "low": 1000, "close": 1003},
            "price_element": "low",
        },
        "level": {"role": "support"}, "notes": "前低支撑",
    })
    group = {"threshold_pct": 3.0, "min_aligned": 1, "members": [{"symbol": "BK0727"}]}

    prices["BK0727"] = 1020.0                      # 上方 2% → 到位
    m = res.scan(group)["members"][0]
    assert m["at_support"] is True
    assert m["nearest_support"]["side"] == "above"
    assert m["broken_through"] is False

    prices["BK0727"] = 980.0                       # 跌破 2% → 不是到位
    m2 = res.scan(group)["members"][0]
    assert m2["at_support"] is False, "跌破支撑不应算作到位"
    assert m2["broken_through"] is True
    assert m2["nearest_support"]["side"] == "below"
    assert m2["nearest_support"]["signed_pct"] < 0
    assert res.scan(group)["is_resonance"] is False


def test_exactly_at_support_counts_as_at_support(rsvc):
    ann, res, prices = rsvc
    ann.create_case({
        "type": "level_origin", "symbol": "BK0727", "period": "daily",
        "source_bar": {
            "date": "2025-01-01",
            "ohlc": {"open": 1005, "high": 1008, "low": 1000, "close": 1003},
            "price_element": "low",
        },
        "level": {"role": "support"},
    })
    prices["BK0727"] = 1000.0                      # 正好触及
    m = res.scan({"threshold_pct": 3.0, "min_aligned": 1,
                  "members": [{"symbol": "BK0727"}]})["members"][0]
    assert m["at_support"] is True
    assert m["nearest_support"]["side"] == "at"


def test_single_symbol_cross_period_resonance_works(rsvc):
    """单标的跨周期共振必须能成立。

    早期 is_resonance 只看「某周期内到位的标的数 >= min_aligned」，
    成员只有 1 个时默认 min_aligned=2 让它**永远为 False**，
    而用户明确要求支持「同一标的不同周期共振」。
    """
    ann, res, prices = rsvc
    for period, low in (("daily", 1000.0), ("weekly", 995.0)):
        ann.create_case({
            "type": "level_origin", "symbol": "BK0727", "period": period,
            "source_bar": {
                "date": "2025-01-01",
                "ohlc": {"open": low + 5, "high": low + 8, "low": low, "close": low + 3},
                "price_element": "low",
            },
            "level": {"role": "support"}, "notes": f"{period}支撑",
        })
    prices["BK0727"] = 1010.0        # 距日线支撑1.0%、距周线支撑1.5%，都在上方

    m = res.scan_matrix(
        {"threshold_pct": 3.0, "min_aligned": 2,        # 默认值，单标的下本不可能满足
         "members": [{"symbol": "BK0727", "symbol_name": "医疗服务"}]},
        periods=["daily", "weekly"],
    )
    assert m["is_resonance"] is True, "单标的跨周期共振应成立"
    assert m["cross_symbol_resonance"] is False
    assert m["cross_period_symbols"] and m["cross_period_symbols"][0]["symbol"] == "BK0727"
    assert set(m["cross_period_symbols"][0]["periods"]) == {"daily", "weekly"}


def test_require_periods_outside_periods_is_reported(rsvc):
    """require 了没扫的周期要显式暴露，而不是静默判为不共振。"""
    _, res, _ = rsvc
    m = res.scan_matrix(
        {"members": [{"symbol": "X"}], "require_periods": ["monthly"]},
        periods=["daily"],
    )
    assert m["invalid_require_periods"] == ["monthly"]
    assert m["is_resonance"] is False


def test_empty_members_never_resonant(rsvc):
    _, res, _ = rsvc
    m = res.scan_matrix({"members": [], "min_aligned": 0}, periods=["daily"])
    assert m["is_resonance"] is False, "空成员组不得报共振成立"
