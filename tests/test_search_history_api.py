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
