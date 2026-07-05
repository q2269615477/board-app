"""
api/search_routes.py — 搜索和历史相关路由
"""
from flask import Blueprint, request, jsonify

from services.search_service import get_search_service

bp = Blueprint('search', __name__, url_prefix='')


@bp.route('/api/pinyin/all')
def api_pinyin_all():
    """返回全部板块的拼音首字母，供前端排序使用"""
    import json
    from pathlib import Path
    cls_file = Path(__file__).resolve().parent.parent / 'static' / 'board_classification.json'
    with open(cls_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    from pypinyin import lazy_pinyin, Style
    result = []
    for cat in data.get('categories', []):
        for b in cat.get('boards', []):
            name = b['name']
            try:
                py = lazy_pinyin(name, style=Style.FIRST_LETTER)
                initials = ''.join(py).upper()
            except Exception:
                initials = name
            result.append({
                'name': name,
                'type': b.get('type', ''),
                'code': b.get('code', ''),
                'initials': initials
            })
    return jsonify({'data': result})


@bp.route('/api/search')
def api_search_route():
    """全市场搜索 API"""
    service = get_search_service()
    q = request.args.get('q', '').strip()
    history_str = request.args.get('history', '')
    history_codes = [c.strip() for c in history_str.split(',') if c.strip()] if history_str else []

    results = service.search(q, history_codes)
    return jsonify({'data': results})


@bp.route('/api/search/history', methods=['POST'])
def api_search_history_post_route():
    """记录搜索历史"""
    data = request.get_json() or {}
    code = data.get('code', '')
    name = data.get('name', '')
    if not code:
        return jsonify({'ok': False}), 400

    try:
        import json
        from pathlib import Path
        hist_file = Path(__file__).resolve().parent.parent / 'data' / 'search_history.json'
        if hist_file.exists():
            with open(hist_file, 'r', encoding='utf-8') as f:
                hist = json.load(f)
        else:
            hist = []
        hist = [h for h in hist if h.get('code') != code]
        from datetime import datetime
        hist.insert(0, {'code': code, 'name': name, 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        hist = hist[:50]
        with open(hist_file, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/search/history', methods=['GET'])
def api_search_history_get_route():
    """获取搜索历史"""
    try:
        import json
        from pathlib import Path
        hist_file = Path(__file__).resolve().parent.parent / 'data' / 'search_history.json'
        if hist_file.exists():
            with open(hist_file, 'r', encoding='utf-8') as f:
                hist = json.load(f)
            return jsonify({'data': hist})
        return jsonify({'data': []})
    except Exception as e:
        return jsonify({'data': [], 'error': str(e)}), 500
