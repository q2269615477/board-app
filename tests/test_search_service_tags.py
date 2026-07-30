"""tests/test_search_service_tags.py — SearchService tags 字段测试"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 测试环境变量（避免启动重量级依赖）
os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)

from services.search_service import SearchService


def _fresh_service():
    """创建独立的 SearchService 实例（绕过全局单例缓存）"""
    svc = SearchService()
    svc._index = None  # 强制重新加载索引
    return svc


def test_search_ai_tech_returns_results_with_tags():
    """search('AI科技') 返回结果，且结果含 tags 字段"""
    svc = _fresh_service()
    results = svc.search('AI科技')
    assert results, "search('AI科技') 应返回至少一条结果"
    for r in results:
        assert 'tags' in r, "结果缺少 tags 字段"
        assert isinstance(r['tags'], list), "tags 必须是 list"


def test_search_hash_ai_tech_all_results_contain_tag():
    """search('#AI科技') 的所有返回结果 tags 中都包含 AI科技"""
    svc = _fresh_service()
    results = svc.search('#AI科技')
    assert results, "search('#AI科技') 应返回至少一条结果"
    for r in results:
        assert 'AI科技' in r['tags'], (
            f"结果 {r.get('name')} tags={r['tags']} 未包含 'AI科技'"
        )


def test_search_semiconductor_hits_tag_related_boards():
    """search('半导体') 能命中 tags/分类相关板块且返回 tags"""
    svc = _fresh_service()
    results = svc.search('半导体')
    assert results, "search('半导体') 应返回至少一条结果"
    for r in results:
        assert 'tags' in r, "结果缺少 tags 字段"
        assert isinstance(r['tags'], list) and len(r['tags']) > 0, "tags 应非空"
