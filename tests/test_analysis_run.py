"""分析场次：一个会话面板 = 一场关联分析（轨迹/水平位/反应点/扫描 + 日历归档）"""
from __future__ import annotations

from datetime import datetime, timedelta

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
    ar._repo = ar.AnnotationRepo(db_path=db)

    import services.analysis_run_service as ars
    ars._svc = None
    return ars.get_analysis_run_service()


def test_visits_record_symbol_and_period_switches(rsvc):
    """切标的/切周期都要留痕——这是关联分析的轨迹"""
    rsvc.start(title="医疗关联")
    rsvc.record_visit("BK0727", "daily", "医疗服务", "industry")
    rsvc.record_visit("sh000933", "daily", "中证医药", "index")
    rsvc.record_visit("sh000933", "weekly", "中证医药", "index")   # 同标的切周期
    run = rsvc.current()
    v = run["visits"]
    assert len(v) == 3
    assert [x["symbol"] for x in v] == ["BK0727", "sh000933", "sh000933"]
    assert [x["period"] for x in v] == ["daily", "daily", "weekly"]


def test_repeated_same_chart_not_duplicated(rsvc):
    """停留在同一张图不重复记，只更新时间"""
    rsvc.start()
    rsvc.record_visit("BK0727", "daily")
    rsvc.record_visit("BK0727", "daily")
    rsvc.record_visit("BK0727", "daily")
    assert len(rsvc.current()["visits"]) == 1


def test_levels_and_reactions_auto_linked(rsvc):
    """同一面板里画的位与反应点自动归入同一场分析"""
    rsvc.start()
    rsvc.record_level({
        "id": "case_1", "symbol": "BK0727", "symbol_name": "医疗服务",
        "period": "daily", "level": {"role": "support", "price": 1180.5},
        "source_bar": {"date": "2025-11-12"}, "notes": "放量反转开盘价",
    })
    rsvc.record_level({      # 同一 case 重复上报不重复计
        "id": "case_1", "symbol": "BK0727", "period": "daily",
        "level": {"role": "support", "price": 1180.5},
    })
    rsvc.record_reaction("case_1", "BK0727", 1182.0, "2026-03-04")
    run = rsvc.current()
    assert len(run["levels"]) == 1
    assert run["levels"][0]["notes"] == "放量反转开盘价"
    assert len(run["reactions"]) == 1


def test_scan_summary_recorded(rsvc):
    rsvc.start()
    rsvc.record_scan({"is_resonance": True, "aligned_count": 2, "member_count": 3,
                      "theme": "医疗", "score": 88.0})
    s = rsvc.current()["scans"]
    assert len(s) == 1 and s[0]["is_resonance"] is True and s[0]["aligned"] == 2


def test_calendar_groups_today_and_yesterday(rsvc):
    """按日期归档：今天 / 昨天 / 更早"""
    rsvc.start(title="今天的分析")
    old = rsvc.start(title="昨天的分析")
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    old["day"] = yday
    rsvc.repo.upsert_run(old)

    cal = rsvc.list_by_date()
    labels = [g["label"] for g in cal["groups"]]
    assert "今天" in labels and "昨天" in labels
    today_group = next(g for g in cal["groups"] if g["label"] == "今天")
    assert today_group["runs"][0]["title"] == "今天的分析"


def test_cross_day_starts_new_run(rsvc):
    """跨天自动开新场，昨天的分析不会混进今天"""
    r = rsvc.start(title="昨天遗留")
    r["day"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    rsvc.repo.upsert_run(r)

    cur = rsvc.current()                     # 跨天 → 关旧场、开新场
    assert cur["id"] != r["id"]
    assert cur["day"] == datetime.now().strftime("%Y-%m-%d")
    assert rsvc.repo.get_run(r["id"])["status"] == "closed"


def test_concurrent_appends_do_not_lose_entries(rsvc):
    """并发追加不得丢更新。

    早期实现是 get_run() → 改内存 dict → upsert_run() 整条覆盖写，无锁无事务。
    Flask threaded=True 下前端的 recordVisit / 画线上报 / 扫描上报会真实并发，
    两个请求各读到同一份 run，后写者覆盖前写者 → 刚画的水平位静默消失。
    """
    from concurrent.futures import ThreadPoolExecutor
    rsvc.start(title="并发测试")
    N = 30

    def add(i):
        rsvc.record_level({
            "id": f"case_{i}", "symbol": f"BK{i:04d}", "period": "daily",
            "level": {"role": "support", "price": 100 + i},
            "source_bar": {"date": "2025-01-01"}, "notes": f"n{i}",
        })

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(add, range(N)))

    levels = rsvc.current()["levels"]
    assert len(levels) == N, f"并发丢更新：应有 {N} 条，实得 {len(levels)}"
    assert len({x["case_id"] for x in levels}) == N


def test_concurrent_visits_and_levels_mixed(rsvc):
    from concurrent.futures import ThreadPoolExecutor
    rsvc.start()

    def work(i):
        if i % 2:
            rsvc.record_visit(f"SYM{i}", "daily", f"名{i}")
        else:
            rsvc.record_level({"id": f"c{i}", "symbol": f"SYM{i}", "period": "daily",
                               "level": {"role": "support", "price": i}})

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, range(24)))
    run = rsvc.current()
    assert len(run["levels"]) == 12
    assert len(run["visits"]) == 12


def test_concurrent_current_creates_single_run(rsvc):
    """并发首次访问只应建出 1 个 open 场次。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        runs = list(ex.map(lambda _: rsvc.current(), range(8)))
    ids = {r["id"] for r in runs if r}
    assert len(ids) == 1, f"并发建出了多个场次: {ids}"
    opens = [r for r in rsvc.repo.list_runs(limit=50) if r.get("status") == "open"]
    assert len(opens) == 1


def test_remove_level_clears_run_reference(rsvc):
    """删除标注后，进行中场次里的悬挂引用要同步清掉。"""
    rsvc.start()
    rsvc.record_level({"id": "case_x", "symbol": "BK0727", "period": "daily",
                       "level": {"role": "support", "price": 100}})
    rsvc.record_reaction("case_x", "BK0727", 101.0, "2026-01-01")
    assert len(rsvc.current()["levels"]) == 1
    rsvc.remove_level("case_x")
    cur = rsvc.current()
    assert cur["levels"] == []
    assert cur["reactions"] == []
