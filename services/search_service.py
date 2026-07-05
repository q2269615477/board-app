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

    def search(self, query: str, history_codes: list = None) -> list:
        """
        全市场搜索
        支持：拼音首字母/名称包含/代码匹配
        """
        query = query.strip()
        if not query:
            return []

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
            score = 0

            # 拼音首字母前缀匹配（Phase 3.2 修复：更宽松的匹配）
            if is_pinyin and initials and len(q_upper) <= len(initials):
                match = all(
                    i < len(initials) and initials[i].upper().startswith(ch)
                    for i, ch in enumerate(q_upper)
                )
                if match:
                    score = 100 + len(initials) - len(q_upper)
            # 也支持首字母连续子串匹配（如"gs"匹配"公用事业"）
            if is_pinyin and initials:
                initials_combined = ''.join(i.upper() for i in initials)
                if q_upper in initials_combined:
                    score = max(score, 70)

            # 名称包含匹配
            if q_upper in name.upper():
                pos = name.upper().index(q_upper)
                score = max(score, 80 - pos * 2 - len(name) * 0.5)

            # 完整拼音首字母包含
            if is_pinyin and q_upper in initials_str:
                score = max(score, 60)

            # 代码匹配
            if code_upper == q_upper:
                score = 200
            elif code_upper.startswith(q_upper):
                score = max(score, 70)
            elif q_upper in code_upper:
                score = max(score, 30)

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
                    'score': score,
                })

        results.sort(key=lambda x: -x['score'])
        return results[:20]

    def _load_index(self) -> Optional[dict]:
        """加载搜索索引"""
        if self._index is not None:
            return self._index
        from pathlib import Path
        idx_file = Path(__file__).resolve().parent.parent / 'static' / 'search_index.json'
        if idx_file.exists():
            with open(idx_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._index = data.get('items', {})
            return self._index
        return None


# 全局单例

_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service
