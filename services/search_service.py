"""
services/search_service.py — 搜索业务逻辑
"""
import os
import json
import logging
from typing import Optional

from core.cache import get_cache

logger = logging.getLogger('search_service')


class SearchService:
    """搜索服务"""

    def __init__(self):
        self._cache = get_cache()
        self._index = None
        self._index_mtime = None
        self._custom_index_path = None

    @property
    def index_path(self):
        """Return the path to the search index JSON file."""
        from pathlib import Path
        if self._custom_index_path is not None:
            return self._custom_index_path
        env_path = os.environ.get('BOARD_APP_SEARCH_INDEX_PATH')
        if env_path:
            return Path(env_path)
        return Path(__file__).resolve().parent.parent / 'static' / 'search_index.json'

    @index_path.setter
    def index_path(self, value):
        """Allow overriding the index path. Set to None to reset to default."""
        self._custom_index_path = value

    def search(self, query: str, history_codes: list = None) -> list:
        """
        全市场搜索
        支持：拼音首字母/名称包含/代码匹配/#tag搜索
        """
        query = query.strip()
        if not query:
            return []

        # Support #tag search: if query starts with #, search tags instead
        is_tag_search = False
        tag_query = ''
        if query.startswith('#'):
            is_tag_search = True
            tag_query = query[1:].strip().lower()

        idx = self._load_index()
        if not idx:
            return []

        priority = set(history_codes or [])
        results = []
        q_upper = query.upper()
        is_pinyin = q_upper.isalnum()

        for code, info in idx.items():
            name = info.get('name', '')
            initials = info.get('initials', [])
            initials_str = ''.join(initials).upper()
            code_upper = code.upper()
            name_upper = name.upper()
            score = 0

            # Tag search
            if is_tag_search:
                tags = info.get('tags', [])
                tags_lower = [t.lower() for t in tags]
                if tag_query in tags_lower:
                    score = max(score, 90)
                elif any(tag_query in t for t in tags_lower):
                    score = max(score, 60)

            # 拼音全匹配 / 前缀匹配 / 包含匹配 / 子序列匹配
            if is_pinyin and initials_str:
                if q_upper == initials_str:
                    score = max(score, 180)
                elif initials_str.startswith(q_upper):
                    score = max(score, 150)
                elif q_upper in initials_str:
                    score = max(score, 120)
                elif len(q_upper) <= len(initials_str) and self._is_subsequence(q_upper, initials_str):
                    score = max(score, 80)

            # 名称包含匹配
            if q_upper in name_upper:
                pos = name_upper.index(q_upper)
                score = max(score, 160 - pos * 2 - len(name) * 0.5)

            # 标签包含匹配
            if not is_tag_search:
                tags = info.get('tags', [])
                q_lower = query.lower()
                tags_lower = [t.lower() for t in tags]
                if q_lower in tags_lower:
                    score = max(score, 85)
                elif any(q_lower in t for t in tags_lower):
                    score = max(score, 55)

            # 代码匹配
            if code_upper == q_upper:
                score = 200
            elif code_upper.startswith(q_upper):
                score = max(score, 130)
            elif q_upper in code_upper:
                score = max(score, 70)

            if score > 0:
                if code in priority:
                    score += 1000
                display_code = code
                if code.startswith('sh') and len(code) == 8:
                    display_code = code[2:] + '.SH'
                elif code.startswith('sz') and len(code) == 8:
                    display_code = code[2:] + '.SZ'
                elif code.startswith('bj') and len(code) == 8:
                    display_code = code[2:] + '.BJ'
                results.append({
                    'code': code,
                    'display_code': display_code,
                    'name': name,
                    'type': info.get('type', ''),
                    'category': info.get('category', ''),
                    'initials': ''.join(initials),
                    'tags': info.get('tags', []),
                    'score': score,
                })

        results.sort(key=lambda x: -x['score'])
        if results:
            return results[:20]

        # 兜底模糊推荐：未精确全命中时，提供常用热搜/指数推荐，绝不上锁空白！
        fallback = []
        for code in ['sh000001', 'sz399006', 'HSI', '301236', '600519', 'BK1158']:
            if code in idx:
                info = idx[code]
                fallback.append({
                    'code': code,
                    'display_code': code,
                    'name': info.get('name', ''),
                    'type': info.get('type', 'index'),
                    'category': info.get('category', '热搜推荐'),
                    'initials': ''.join(info.get('initials', [])),
                    'tags': ['推荐'],
                    'score': 1,
                })
        return fallback

    def _is_subsequence(self, sub: str, s: str) -> bool:
        """子序列匹配（如 rst 匹配 RTDL）"""
        it = iter(s)
        return all(c in it for c in sub)

    def _ensure_index_file(self) -> bool:
        """If the index file is missing, attempt to rebuild it.

        Delegates to ``build_search_index.build_index_json()`` which reads
        ``static/board_classification.json`` (tracked) and local constituent
        caches (tracked) to produce a working index even without QMT.

        Returns True if the file exists after the call.
        """
        p = self.index_path
        if p.exists():
            return True
        logger.warning("[搜索] 索引文件不存在 (%s)，尝试自动重建...", p)
        try:
            from build_search_index import build_index_json
            build_index_json(output_path=p)
        except Exception as e:
            logger.error("[搜索] 索引自动重建失败: %s", e)
        if p.exists():
            logger.info("[搜索] 索引自动重建成功")
            return True
        logger.error("[搜索] 索引重建后文件仍不存在，搜索将返回空结果")
        return False

    def _load_index(self) -> Optional[dict]:
        """Load search index, auto-reloading if file mtime changed.

        If the index file is missing and no cache exists, attempts an
        automatic rebuild via ``_ensure_index_file`` before giving up.
        """
        p = self.index_path
        if not p.exists():
            # No cache → try auto-rebuild before returning empty
            if self._index is None:
                self._ensure_index_file()
            if not p.exists():
                return self._index  # file still gone - return cache (None if no cache)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = None
        if self._index is not None and self._index_mtime == mtime and mtime is not None:
            return self._index
        # File changed or first load
        if mtime is not None:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and 'items' in data:
                    self._index = data['items']
                elif isinstance(data, dict):
                    self._index = data
                else:
                    self._index = {}
                self._index_mtime = mtime
                return self._index
            except Exception:
                if self._index is not None:
                    return self._index  # keep old cache on read error
                return None
        return self._index

    def reload(self):
        """Force reload the index from disk."""
        self._index = None
        self._index_mtime = None
        return self._load_index()

    def clear_cache(self):
        """Clear the cached index, forcing reload on next search."""
        self._index = None
        self._index_mtime = None


# 全局单例

_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service
