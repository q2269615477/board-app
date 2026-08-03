"""
api/search_routes.py — 搜索和历史相关路由
"""
from flask import Blueprint, request, jsonify
from pathlib import Path
import json
import threading

from services.search_service import get_search_service
from api.auth_guard import write_protected

bp = Blueprint('search', __name__, url_prefix='')

_SEARCH_HISTORY_FILE = Path(__file__).resolve().parent.parent / 'data' / 'search_history.json'
_SEARCH_HISTORY_LIMIT = 5
_SEARCH_HISTORY_LOCK = threading.Lock()


def _load_search_history():
    if not _SEARCH_HISTORY_FILE.exists():
        return []
    with open(_SEARCH_HISTORY_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _save_search_history(history):
    _SEARCH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = _SEARCH_HISTORY_FILE.with_suffix('.json.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(history[:_SEARCH_HISTORY_LIMIT], f, ensure_ascii=False, indent=1)
    temp_file.replace(_SEARCH_HISTORY_FILE)


def _iter_classification_boards(nodes):
    """Yield board dicts from nested board_classification.json nodes."""
    for node in nodes or []:
        for board in node.get('boards') or []:
            yield board
        for key in ('subcategories', 'children', 'categories'):
            yield from _iter_classification_boards(node.get(key) or [])


@bp.route('/api/pinyin/all')
def api_pinyin_all():
    """返回全部板块的拼音首字母，供前端排序使用"""
    import json
    from pathlib import Path
    cls_file = Path(__file__).resolve().parent.parent / 'static' / 'board_classification.json'
    with open(cls_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    try:
        from pypinyin import lazy_pinyin, Style

        def initials_of(name):
            return ''.join(lazy_pinyin(name, style=Style.FIRST_LETTER)).upper()
    except Exception:
        try:
            from build_search_index import compute_initials

            def initials_of(name):
                return ''.join(compute_initials(name)).upper()
        except Exception:
            def initials_of(name):
                return ''.join(ch.upper() for ch in str(name) if ch.isascii() and ch.isalnum())

    result = []
    for b in _iter_classification_boards(data.get('categories', [])):
        name = b.get('name', '')
        try:
            initials = initials_of(name)
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
@write_protected
def api_search_history_post_route():
    """记录搜索历史"""
    data = request.get_json() or {}
    code = data.get('code', '')
    name = data.get('name', '')
    if not code:
        return jsonify({'ok': False}), 400

    try:
        from datetime import datetime
        item = {
            'code': str(code).strip(),
            'name': str(name).strip(),
            'value': str(data.get('value') or name or code).strip(),
            'type': str(data.get('type') or '').strip(),
            'category': str(data.get('category') or '').strip(),
            'display_code': str(data.get('display_code') or '').strip(),
            'initials': str(data.get('initials') or '').strip(),
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with _SEARCH_HISTORY_LOCK:
            hist = [h for h in _load_search_history() if h.get('code') != item['code']]
            hist.insert(0, item)
            hist = hist[:_SEARCH_HISTORY_LIMIT]
            _save_search_history(hist)
        return jsonify({'ok': True, 'data': hist})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/search/history', methods=['GET'])
def api_search_history_get_route():
    """获取搜索历史"""
    try:
        with _SEARCH_HISTORY_LOCK:
            return jsonify({'data': _load_search_history()[:_SEARCH_HISTORY_LIMIT]})
    except Exception as e:
        return jsonify({'data': [], 'error': str(e)}), 500
