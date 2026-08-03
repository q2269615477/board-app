import json

import api.search_routes as search_routes
from app import create_app


def test_search_history_keeps_latest_five_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(search_routes, '_SEARCH_HISTORY_FILE', tmp_path / 'search_history.json')
    client = create_app().test_client()

    for index in range(6):
        response = client.post('/api/search/history', json={
            'code': f'code-{index}',
            'name': f'name-{index}',
            'value': f'query-{index}',
            'type': 'stock',
            'category': '个股',
            'display_code': f'display-{index}',
            'initials': f'PY{index}',
        })
        assert response.status_code == 200

    history = client.get('/api/search/history').get_json()['data']
    assert len(history) == 5
    assert [item['code'] for item in history] == [f'code-{i}' for i in range(5, 0, -1)]
    assert history[0]['value'] == 'query-5'
    assert history[0]['type'] == 'stock'
    assert history[0]['display_code'] == 'display-5'


def test_search_history_refreshes_duplicate_recency(tmp_path, monkeypatch):
    monkeypatch.setattr(search_routes, '_SEARCH_HISTORY_FILE', tmp_path / 'search_history.json')
    client = create_app().test_client()

    client.post('/api/search/history', json={'code': '603259', 'name': '药明康德', 'value': 'ymkd'})
    client.post('/api/search/history', json={'code': '600519', 'name': '贵州茅台', 'value': 'gzmt'})
    client.post('/api/search/history', json={'code': '603259', 'name': '药明康德', 'value': '药明'})

    history = client.get('/api/search/history').get_json()['data']
    assert [item['code'] for item in history] == ['603259', '600519']
    assert history[0]['value'] == '药明'


def test_search_history_explicit_file_overrides_data_dir_for_reads_and_writes(tmp_path, monkeypatch):
    explicit_file = tmp_path / 'explicit' / 'history.json'
    data_dir_file = tmp_path / 'data-dir' / 'search_history.json'
    explicit_file.parent.mkdir()
    data_dir_file.parent.mkdir()
    explicit_file.write_text(json.dumps([{'code': 'explicit-seed'}]), encoding='utf-8')
    data_dir_file.write_text(json.dumps([{'code': 'data-dir-seed'}]), encoding='utf-8')
    monkeypatch.setenv('BOARD_APP_SEARCH_HISTORY_FILE', str(explicit_file))
    monkeypatch.setenv('BOARD_APP_DATA_DIR', str(data_dir_file.parent))
    client = create_app().test_client()

    history = client.get('/api/search/history').get_json()['data']
    assert [item['code'] for item in history] == ['explicit-seed']

    response = client.post('/api/search/history', json={'code': 'explicit-write', 'name': '显式路径'})
    assert response.status_code == 200
    assert [item['code'] for item in json.loads(explicit_file.read_text(encoding='utf-8'))] == [
        'explicit-write',
        'explicit-seed',
    ]
    assert json.loads(data_dir_file.read_text(encoding='utf-8')) == [{'code': 'data-dir-seed'}]


def test_search_history_data_dir_isolates_reads_and_writes(tmp_path, monkeypatch):
    fallback_file = tmp_path / 'fallback' / 'search_history.json'
    data_dir_file = tmp_path / 'isolated-data' / 'search_history.json'
    fallback_file.parent.mkdir()
    data_dir_file.parent.mkdir()
    fallback_file.write_text(json.dumps([{'code': 'fallback-seed'}]), encoding='utf-8')
    data_dir_file.write_text(json.dumps([{'code': 'data-dir-seed'}]), encoding='utf-8')
    monkeypatch.setattr(search_routes, '_SEARCH_HISTORY_FILE', fallback_file)
    monkeypatch.delenv('BOARD_APP_SEARCH_HISTORY_FILE', raising=False)
    monkeypatch.setenv('BOARD_APP_DATA_DIR', str(data_dir_file.parent))
    client = create_app().test_client()

    history = client.get('/api/search/history').get_json()['data']
    assert [item['code'] for item in history] == ['data-dir-seed']

    response = client.post('/api/search/history', json={'code': 'data-dir-write', 'name': '数据目录'})
    assert response.status_code == 200
    assert [item['code'] for item in json.loads(data_dir_file.read_text(encoding='utf-8'))] == [
        'data-dir-write',
        'data-dir-seed',
    ]
    assert json.loads(fallback_file.read_text(encoding='utf-8')) == [{'code': 'fallback-seed'}]
