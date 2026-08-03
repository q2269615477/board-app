import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd

from services.index_constituent_service import (
    IndexConstituentService,
    discover_indices,
    normalize_weights,
    source_code_candidates,
)


def test_source_code_mapping_covers_sh_sz_and_new_sz_index():
    assert source_code_candidates('sh000300') == ['000300.SH', '000300.CSI', '000300.SZ']
    assert source_code_candidates('sz399811') == ['399811.SZ', '399811.CSI', '399811.SH']
    assert source_code_candidates('399811') == ['399811.SZ', '399811.CSI', '399811.SH']


def test_discover_indices_recurses_and_deduplicates(tmp_path):
    path = tmp_path / 'classification.json'
    path.write_text(json.dumps({
        'categories': [{
            'boards': [
                {'type': 'index', 'code': 'sz399811', 'name': 'CSSW电子'},
                {'type': 'concept', 'code': 'BK0001', 'name': '不是指数'},
            ],
            'nested': [{'type': 'index', 'code': 'sz399811', 'name': '重复'}],
        }]
    }), encoding='utf-8')
    rows = discover_indices(path)
    assert rows == [{
        'panel_code': 'sz399811',
        'name': 'CSSW电子',
        'type': 'index',
        'source_candidates': ['399811.SZ', '399811.CSI', '399811.SH'],
    }]


def test_normalize_weights_keeps_latest_date_and_fields():
    frame = pd.DataFrame([
        {'trade_date': '20260701', 'con_code': '600519.SH', 'con_name': '贵州茅台', 'weight': 2.1},
        {'trade_date': '20260731', 'con_code': '600519.SH', 'con_name': '贵州茅台', 'weight': 2.2},
        {'trade_date': '20260731', 'con_code': '603259.SH', 'con_name': '药明康德', 'weight': 1.2},
    ])
    rows = normalize_weights(frame, 'tushare.index_weight')
    assert [row['code'] for row in rows] == ['600519', '603259']
    assert rows[0]['as_of_date'] == '2026-07-31'
    assert rows[0]['weight'] == 2.2


def test_success_replaces_snapshot_atomically_and_reads_local_only(tmp_path):
    service = IndexConstituentService(tmp_path / 'kline.db')
    item = {'panel_code': 'sz399811', 'name': 'CSSW电子'}

    def fetcher(source):
        assert source == '399811.SZ'
        return [{
            'code': '600519', 'name': '贵州茅台', 'weight': 4.5,
            'as_of_date': '2026-07-31', 'source': 'tushare.index_weight',
        }]

    result = service.update_index(item, fetcher=fetcher)
    assert result['status'] == 'ready'
    assert service.get_latest('sz399811') == [{
        'code': '600519', 'name': '贵州茅台', 'weight': 4.5,
        'source': 'tushare.index_weight', 'as_of_date': '2026-07-31',
    }]
    catalog = service.get_catalog('sz399811')[0]
    assert catalog['source_code'] == '399811.SZ'
    assert catalog['member_count'] == 1


def test_empty_result_records_unsupported_without_erasing_old_snapshot(tmp_path):
    service = IndexConstituentService(tmp_path / 'kline.db')
    item = {'panel_code': 'sh000300', 'name': '沪深300'}
    service.update_index(item, fetcher=lambda _: [{
        'code': '600519', 'name': '贵州茅台', 'weight': 1,
        'as_of_date': '2026-07-31', 'source': 'tushare.index_weight',
    }])
    result = service.update_index(item, fetcher=lambda _: [])
    assert result['status'] == 'unsupported'
    assert service.get_latest('sh000300')[0]['code'] == '600519'
    assert service.get_catalog('sh000300')[0]['status'] == 'unsupported'


def test_board_service_index_uses_snapshot_and_existing_enrichment(monkeypatch):
    import services.board_service as board_module

    class FakeCache:
        def get(self, _key):
            return None
        def set(self, *_args, **_kwargs):
            pass

    class FakeIndexService:
        def get_latest(self, code):
            assert code == 'sz399811'
            return [{'code': '600519', 'name': '贵州茅台', 'weight': 4.5,
                     'source': 'tushare.index_weight', 'as_of_date': '2026-07-31'}]

    service = board_module.BoardService.__new__(board_module.BoardService)
    service._cache = FakeCache()
    service._index_constituents = FakeIndexService()
    monkeypatch.setattr(service, '_enrich_constituents', lambda rows, live=False: rows)
    assert service.get_constituents_sorted('index', 'sz399811') == [{
        'code': '600519', 'name': '贵州茅台', 'weight': 4.5,
        'source': 'tushare.index_weight', 'as_of_date': '2026-07-31',
    }]


def test_board_service_falls_back_to_eastmoney_when_local_entry_is_missing(monkeypatch):
    import services.board_service as board_module

    class FakeCache:
        def __init__(self): self.data = {}
        def get(self, key): return self.data.get(key)
        def set(self, key, value, **_kwargs): self.data[key] = value

    service = board_module.BoardService.__new__(board_module.BoardService)
    service._cache = FakeCache()
    with board_module._constituents_json_lock:
        board_module._constituents_remote_cache.clear()
    monkeypatch.setattr(board_module, '_get_constituents_cache', lambda _type: {})
    monkeypatch.setattr(service, '_enrich_constituents', lambda rows, live=False: rows)
    calls = []
    monkeypatch.setattr(board_module, 'get_eastmoney_constituents',
                        lambda board_type, code: calls.append((board_type, code)) or [
                            {'code': '600001', 'name': '测试一', 'close': 10,
                             'change_pct': 1, 'mkt_cap': 2, 'volume': 3,
                             'source': 'eastmoney_push2delay'}
                        ])

    assert service.get_constituents('industry', 'BK0922')[0]['code'] == '600001'
    assert service.get_constituents('industry', 'BK0922')[0]['source'] == 'eastmoney_push2delay'
    assert calls == [('industry', 'BK0922')]


def test_board_service_prefers_nonempty_local_entry_without_remote_request(monkeypatch):
    import services.board_service as board_module

    class FakeCache:
        def get(self, _key): return None
        def set(self, *_args, **_kwargs): pass

    service = board_module.BoardService.__new__(board_module.BoardService)
    service._cache = FakeCache()
    monkeypatch.setattr(board_module, '_get_constituents_cache', lambda _type: {
        'industry:BK0922': {'cons': [{'code': '600002', 'name': '本地'}]}
    })
    monkeypatch.setattr(service, '_enrich_constituents', lambda rows, live=False: rows)
    monkeypatch.setattr(board_module, 'get_eastmoney_constituents',
                        lambda *_args: (_ for _ in ()).throw(AssertionError('remote called')))

    assert service.get_constituents('industry', 'BK0922') == [
        {'code': '600002', 'name': '本地'}
    ]


def test_enrich_keeps_remote_quote_fields_when_sqlite_is_empty(monkeypatch, tmp_path):
    import services.board_service as board_module
    import sqlite3

    db = tmp_path / 'kline.db'
    conn = sqlite3.connect(db)
    conn.executescript('CREATE TABLE kline (code TEXT, period TEXT, date TEXT, close REAL, volume REAL);'
                       'CREATE TABLE mkt_cap (code TEXT, mkt_cap REAL);')
    conn.commit(); conn.close()
    real_connect = sqlite3.connect
    monkeypatch.setattr(board_module.sqlite3, 'connect', lambda _path: real_connect(db))
    rows = [{'code': '600001', 'name': '远端', 'close': 12.3, 'change_pct': 4.5,
             'mkt_cap': 123.4, 'volume': 77, 'source': 'eastmoney_push2delay'}]
    service = board_module.BoardService.__new__(board_module.BoardService)
    result = service._enrich_constituents(rows)
    assert result[0]['close'] == 12.3
    assert result[0]['change_pct'] == 4.5
    assert result[0]['mkt_cap'] == 123.4
    assert result[0]['volume'] == 77


def test_update_script_help_is_available():
    result = subprocess.run(
        [sys.executable, 'scripts/update_index_constituents.py', '--help'],
        capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert '--dry-run' in result.stdout
    assert '--index' in result.stdout


def test_board_constituents_audit_help_is_available():
    result = subprocess.run(
        [sys.executable, 'scripts/audit_board_constituents.py', '--help'],
        capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert '--remote-all' in result.stdout
    assert '--workers' in result.stdout
