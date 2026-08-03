import json
from types import SimpleNamespace

from scripts import audit_board_constituents as audit_module


def test_remote_empty_local_covered_is_nonfatal_and_reported(monkeypatch, capsys):
    rows = [
        {'board_type': 'concept', 'code': 'BK1677', 'name': '特殊一'},
        {'board_type': 'concept', 'code': 'BK2000', 'name': '缺失一'},
    ]
    monkeypatch.setattr(audit_module, 'discover_boards', lambda _path: rows)
    monkeypatch.setattr(audit_module, 'load_local_maps', lambda _root: {
        'industry': {},
        'concept': {
            'concept:BK1677': {'cons': [{'code': '600001'}]},
        },
    })
    monkeypatch.setattr(audit_module, 'get_eastmoney_constituents', lambda *_args: [])

    code = audit_module.audit(SimpleNamespace(
        classification='unused.json', remote_all=True, workers=2,
    ))
    report = json.loads(capsys.readouterr().out)

    assert code == 1
    assert report['remote_checked'] == 2
    assert [x['code'] for x in report['remote_empty_but_local_covered']] == ['BK1677']
    assert [x['code'] for x in report['empty']] == ['BK2000']


def test_remote_empty_local_covered_only_does_not_fail(monkeypatch, capsys):
    row = {'board_type': 'concept', 'code': 'BK1679', 'name': '特殊三'}
    monkeypatch.setattr(audit_module, 'discover_boards', lambda _path: [row])
    monkeypatch.setattr(audit_module, 'load_local_maps', lambda _root: {
        'industry': {},
        'concept': {'concept:BK1679': {'cons': [{'code': '600002'}]}},
    })
    monkeypatch.setattr(audit_module, 'get_eastmoney_constituents', lambda *_args: [])

    code = audit_module.audit(SimpleNamespace(
        classification='unused.json', remote_all=True, workers=1,
    ))
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report['empty'] == []
    assert report['error'] == []
    assert report['remote_empty_but_local_covered'][0]['code'] == 'BK1679'
