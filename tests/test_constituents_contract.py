"""Constituents loading contract.

The data-update architecture requires board constituents to come from the
process-resident JSON cache during normal page requests.  Tushare dc_member is
an offline updater, not an interactive fallback.
"""

import os

os.environ.setdefault("BOARD_APP_AUTO_BOOTSTRAP", "0")


def test_get_constituents_uses_memory_cache(monkeypatch):
    import services.board_service as bs

    svc = bs.get_board_service()
    svc._cache.clear()

    with bs._constituents_json_lock:
        bs._constituents_json_cache.clear()
        bs._constituents_json_cache.update({
            "concept": {
                "concept:BKTEST": {
                    "name": "测试板块",
                    "cons": [{"code": "000001", "name": "平安银行"}],
                }
            }
        })

    monkeypatch.setattr(
        bs,
        "get_concept_constituents",
        lambda code: (_ for _ in ()).throw(AssertionError("must not call Tushare fallback")),
    )
    monkeypatch.setattr(bs.BoardService, "_enrich_constituents", lambda self, cons, live=False: cons)

    rows = svc.get_constituents("concept", "BKTEST")
    assert rows == [{"code": "000001", "name": "平安银行"}]


def test_board_service_has_no_interactive_tushare_sleep_loop():
    from pathlib import Path

    source = Path("services/board_service.py").read_text(encoding="utf-8")
    assert "sleep(0.35)" not in source
    assert "still_missing[:50]" not in source
