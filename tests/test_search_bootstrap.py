"""tests/test_search_bootstrap.py — 搜索索引自举测试

验证 fresh-clone 场景：当 static/search_index.json 不存在时，
SearchService 能自动重建索引并正常搜索。

包含两类测试：
1. Mock 测试：验证 _ensure_index_file / _load_index 的调用逻辑
2. Integration 测试：调用真实 build_index_json()，验证从 Git 跟踪的
   数据种子（board_classification.json + constituents）确实能生成
   可用索引，且 "ymkd" 能搜到药明康德。
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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
    svc._index = None
    svc._index_mtime = None
    svc._index_path = None
    return svc


def _mock_build_index(tmp_path: Path):
    """模拟 build_index_json，生成包含药明康德的索引文件。

    接受 output_path 参数以匹配真实函数签名。
    """
    def _build(output_path=None, **kwargs):
        target = Path(output_path) if output_path else tmp_path / "search_index.json"
        data = {
            "version": 2,
            "built_at": "2026-07-30 00:00:00",
            "total": 2,
            "items": {
                "sh000001": {
                    "name": "上证指数",
                    "type": "index",
                    "category": "指数",
                    "initials": ["S", "Z", "Z", "S"],
                    "tags": ["指数", "上证"],
                },
                "603259": {
                    "name": "药明康德",
                    "type": "stock",
                    "category": "个股",
                    "initials": ["Y", "M", "K", "D"],
                    "tags": ["个股", "药明康德"],
                },
            },
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return _build


# ---- Mock 测试：验证调用逻辑 ----

def test_index_missing_triggers_auto_rebuild(tmp_path):
    """索引文件不存在时，_load_index 应触发自动重建。"""
    svc = _fresh_service()
    idx_file = tmp_path / "search_index.json"
    svc.index_path = idx_file

    assert not idx_file.exists()
    assert svc._index is None

    with patch("build_search_index.build_index_json", _mock_build_index(tmp_path)):
        idx = svc._load_index()

    assert idx is not None, "重建后应返回索引字典"
    assert idx_file.exists(), "索引文件应已被创建"
    assert "603259" in idx


def test_search_ymkd_finds_yaomingkangde_after_rebuild(tmp_path):
    """删除/隔离 search_index.json 后，搜索 ymkd 能搜到药明康德。"""
    svc = _fresh_service()
    idx_file = tmp_path / "search_index.json"
    svc.index_path = idx_file

    # 确保索引文件不存在（模拟 fresh clone）
    assert not idx_file.exists()

    with patch("build_search_index.build_index_json", _mock_build_index(tmp_path)):
        results = svc.search("ymkd")

    assert results, "搜索 ymkd 应返回结果（非空）"
    assert any(r["name"] == "药明康德" for r in results), \
        f"搜索结果应包含药明康德，实际: {[r['name'] for r in results]}"


def test_search_returns_empty_when_rebuild_fails(tmp_path):
    """索引文件不存在且重建失败时，搜索应返回空列表，不崩溃。"""
    svc = _fresh_service()
    idx_file = tmp_path / "search_index.json"
    svc.index_path = idx_file

    def _failing_build(output_path=None, **kwargs):
        raise RuntimeError("QMT not available")

    with patch("build_search_index.build_index_json", _failing_build):
        results = svc.search("ymkd")

    assert results == [], "重建失败时搜索应返回空列表"


def test_index_missing_with_cache_does_not_rebuild(tmp_path):
    """已有缓存时索引文件被删除，不应触发重建（保留缓存）。"""
    svc = _fresh_service()
    idx_file = tmp_path / "search_index.json"

    # 先创建索引并加载
    _mock_build_index(tmp_path)(output_path=idx_file)
    svc.index_path = idx_file
    svc.search("药明康德")
    assert svc._index is not None

    # 删除文件
    idx_file.unlink()

    rebuild_called = False

    def _should_not_be_called(output_path=None, **kwargs):
        nonlocal rebuild_called
        rebuild_called = True

    with patch("build_search_index.build_index_json", _should_not_be_called):
        idx = svc._load_index()

    assert idx is not None, "有缓存时应返回缓存"
    assert not rebuild_called, "有缓存时不应触发重建"
    assert "603259" in idx


def test_ensure_index_file_passes_index_path(tmp_path):
    """_ensure_index_file 应将 self.index_path 传给 build_index_json。"""
    svc = _fresh_service()
    idx_file = tmp_path / "custom" / "search_index.json"
    svc.index_path = idx_file

    received_path = []

    def _capture_path(output_path=None, **kwargs):
        received_path.append(output_path)

    with patch("build_search_index.build_index_json", _capture_path):
        svc._ensure_index_file()

    assert len(received_path) == 1
    assert Path(received_path[0]) == idx_file, \
        f"应传入 self.index_path ({idx_file})，实际传入 {received_path[0]}"


# ---- Integration 测试：调用真实 build_index_json ----

def test_real_build_index_json_produces_usable_index(tmp_path):
    """真实 build_index_json() 从 Git 跟踪的数据种子生成可用索引。

    不 mock 任何东西。验证：
    1. 从 board_classification.json + constituents 能生成索引文件
    2. 索引包含板块条目
    3. 索引包含个股条目（来自本地成分股缓存）
    4. 603259 药明康德在索引中
    """
    from build_search_index import build_index_json

    idx_file = tmp_path / "search_index.json"

    # 调用真实构建器，输出到临时路径
    result = build_index_json(output_path=idx_file)

    # 索引文件应存在
    assert idx_file.exists(), "索引文件应已被创建"

    # 返回值和文件内容一致
    with open(idx_file, "r", encoding="utf-8") as f:
        file_data = json.load(f)
    assert file_data["items"] == result or len(file_data["items"]) == len(result)

    # 应包含板块条目
    board_codes = [c for c, v in result.items() if v.get("type") in ("industry", "concept")]
    assert len(board_codes) > 0, "索引应包含板块条目"

    # 应包含个股条目（来自本地成分股缓存）
    stock_codes = [c for c, v in result.items() if v.get("type") == "stock"]
    assert len(stock_codes) > 0, "索引应包含个股条目"

    # 药明康德（603259）应在索引中
    assert "603259" in result, \
        f"索引应包含 603259 药明康德，实际个股数: {len(stock_codes)}"
    assert result["603259"]["name"] == "药明康德", \
        f"603259 名称应为药明康德，实际: {result['603259']['name']}"


def test_real_build_then_search_ymkd_finds_yaomingkangde(tmp_path):
    """End-to-end: 真实构建索引 → SearchService 加载 → 搜索 ymkd → 药明康德。

    模拟 fresh clone：索引文件不存在 → 自动调用真实 build_index_json →
    搜索 ymkd 返回药明康德。
    """
    from build_search_index import build_index_json

    svc = _fresh_service()
    idx_file = tmp_path / "search_index.json"
    svc.index_path = idx_file

    assert not idx_file.exists()

    # 不 mock build_index_json，让 _ensure_index_file 调用真实构建器
    # QMT 不可用时会跳过 QMT 数据源，但仍从本地 constituents 构建
    results = svc.search("ymkd")

    assert results, "搜索 ymkd 应返回结果（非空）"
    assert any(r["name"] == "药明康德" for r in results), \
        f"搜索结果应包含药明康德，实际: {[r['name'] for r in results]}"
    assert idx_file.exists(), "索引文件应已被自动创建"
