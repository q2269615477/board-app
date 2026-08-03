"""tests/test_search_service_reload.py — SearchService mtime 热更新 / reload 测试"""
import json
import os
import sys
import time
from pathlib import Path

import pytest
from unittest.mock import patch

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


@pytest.fixture
def tmp_index(tmp_path):
    """创建临时索引文件，返回文件路径"""
    data = {
        "version": 1,
        "items": {
            "BK0001": {
                "name": "测试板块A",
                "type": "concept",
                "category": "测试",
                "primary_category": "测试分类",
                "secondary_category": "测试子类",
                "tags": ["测试标签", "A"],
                "initials": ["C", "S", "B", "K", "A"],
            },
            "BK0002": {
                "name": "测试板块B",
                "type": "concept",
                "category": "测试",
                "primary_category": "测试分类",
                "secondary_category": "测试子类",
                "tags": ["测试标签", "B"],
                "initials": ["C", "S", "B", "K", "B"],
            },
        },
    }
    idx_file = tmp_path / "search_index.json"
    idx_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return idx_file


def _fresh_service():
    """创建独立的 SearchService 实例（绕过全局单例缓存）"""
    svc = SearchService()
    svc._index = None
    svc._index_mtime = None
    svc._index_path = None
    return svc


# ---- clear_cache 测试 ----

def test_clear_cache_resets_index(tmp_index):
    """clear_cache 后再次 search 应重新加载"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    # 首次加载
    results1 = svc.search("测试")
    assert results1, "首次搜索应返回结果"
    assert svc._index is not None
    assert svc._index_mtime is not None

    # 清除缓存
    svc.clear_cache()
    assert svc._index is None
    assert svc._index_mtime is None

    # 再次搜索应重新加载
    results2 = svc.search("测试")
    assert results2, "clear_cache 后搜索应重新加载并返回结果"
    assert svc._index is not None


def test_clear_cache_with_no_prior_load(tmp_index):
    """未加载过时调用 clear_cache 不应报错"""
    svc = _fresh_service()
    svc.index_path = tmp_index
    svc.clear_cache()
    assert svc._index is None


# ---- reload 测试 ----

def test_reload_forces_reread(tmp_index):
    """reload 强制重新读取索引"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    # 首次加载
    svc.search("测试")
    assert svc._index is not None
    old_mtime = svc._index_mtime

    # 修改文件内容（确保 mtime 变化）
    time.sleep(0.05)
    new_data = {
        "version": 2,
        "items": {
            "BK9999": {
                "name": "新板块",
                "type": "concept",
                "category": "新分类",
                "primary_category": "新分类",
                "secondary_category": "新子类",
                "tags": ["新标签"],
                "initials": ["X", "K"],
            },
        },
    }
    tmp_index.write_text(json.dumps(new_data, ensure_ascii=False), encoding="utf-8")

    # reload 应读取新内容
    idx = svc.reload()
    assert idx is not None
    assert "BK9999" in idx, "reload 后应包含新条目"
    assert "BK0001" not in idx, "reload 后不应包含旧条目"


# ---- mtime 热更新测试 ----

def test_mtime_unchanged_uses_cache(tmp_index):
    """mtime 未变化时直接使用缓存"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    svc.search("测试")
    cached_index = svc._index

    # 再次调用 _load_index（mtime 未变）
    result = svc._load_index()
    assert result is cached_index, "mtime 未变应返回同一缓存对象"


def test_mtime_changed_triggers_reload(tmp_index):
    """mtime 变化后应触发重新加载"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    # 首次加载
    svc.search("测试")
    assert "BK0001" in svc._index

    # 等待并修改文件
    time.sleep(0.05)
    new_data = {
        "version": 2,
        "items": {
            "BK9999": {
                "name": "新板块",
                "type": "concept",
                "category": "新分类",
                "primary_category": "新分类",
                "secondary_category": "新子类",
                "tags": ["新标签"],
                "initials": ["X", "K"],
            },
        },
    }
    tmp_index.write_text(json.dumps(new_data, ensure_ascii=False), encoding="utf-8")

    # search 应返回新数据
    results = svc.search("新板块")
    assert results, "mtime 变化后 search 应返回新数据"
    assert results[0]["code"] == "BK9999"
    assert "BK9999" in svc._index
    assert "BK0001" not in svc._index


def test_mtime_changed_search_reflects_update(tmp_index):
    """mtime 改变后搜索结果反映更新（验证搜索行为正确性）"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    results_before = svc.search("测试板块A")
    assert results_before, "修改前应能搜索到'测试板块A'"

    # 修改文件，移除 BK0001
    time.sleep(0.05)
    new_data = {
        "version": 2,
        "items": {
            "BK0002": {
                "name": "测试板块B",
                "type": "concept",
                "category": "测试",
                "primary_category": "测试分类",
                "secondary_category": "测试子类",
                "tags": ["测试标签", "B"],
                "initials": ["C", "S", "B", "K", "B"],
            },
        },
    }
    tmp_index.write_text(json.dumps(new_data, ensure_ascii=False), encoding="utf-8")

    results_after = svc.search("测试板块A")
    assert not results_after, "修改后不应再搜索到已删除的'测试板块A'"
    assert svc.search("测试板块B")


# ---- index_path 可覆盖测试 ----

def test_index_path_can_be_overridden():
    """index_path 可通过 setter 覆盖"""
    svc = _fresh_service()
    custom_path = Path("/tmp/fake_index.json")
    svc.index_path = custom_path
    assert svc.index_path == custom_path


def test_index_path_reset_to_default():
    """设置 index_path 为 None 后应恢复默认路径"""
    svc = _fresh_service()
    svc.index_path = Path("/tmp/fake.json")
    svc.index_path = None
    # 默认路径应指向 static/search_index.json
    expected = Path(__file__).resolve().parent.parent / "static" / "search_index.json"
    assert svc.index_path == expected


# ---- 边界情况测试 ----

def test_missing_file_with_cache_keeps_cache(tmp_index):
    """文件被删除但已有缓存时应保留缓存"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    svc.search("测试")
    assert svc._index is not None

    # 删除文件
    tmp_index.unlink()

    # 再次加载应保留缓存
    result = svc._load_index()
    assert result is not None
    assert "BK0001" in result


def test_missing_file_without_cache_returns_none(tmp_index):
    """文件被删除且无缓存且重建失败时返回 None。

    注意：_load_index 现在会尝试自动重建索引。要测试 "返回 None" 的
    行为，需要 mock 构建器使其失败。
    """
    svc = _fresh_service()
    svc.index_path = tmp_index
    tmp_index.unlink()

    # mock 构建器失败，确保 _load_index 返回 None
    with patch("build_search_index.build_index_json",
               side_effect=RuntimeError("test: build failed")):
        result = svc._load_index()
    assert result is None


def test_search_empty_index_no_crash(tmp_index):
    """索引文件 items 为空时 search 不崩溃"""
    svc = _fresh_service()
    svc.index_path = tmp_index

    # 写入空 items
    tmp_index.write_text(json.dumps({"items": {}}, ensure_ascii=False), encoding="utf-8")
    results = svc.search("测试")
    assert results == []
