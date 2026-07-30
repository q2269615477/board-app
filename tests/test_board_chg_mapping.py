"""test_board_chg_mapping.py — 板块涨跌幅 key 映射回归测试

覆盖 _reload_board_changes 的键映射逻辑：
  - industry → industry:code + bare code（不污染 concept:code）
  - concept  → concept:code + bare code（仅当 industry 未占用时）
  - BK 代码重叠时 industry 优先占用 bare key
"""
from unittest.mock import patch
import pytest


@pytest.fixture
def app_ctx():
    """构造 AppContext 实例（无需完整启动）"""
    from core.lifecycle import AppContext
    return AppContext()


class TestBoardChgMapping:
    """_reload_board_changes 键映射测试"""

    def test_industry_only(self, app_ctx):
        """纯行业板块 → 产出 industry:code + bare key"""
        ind = {'BK1001': {'涨跌幅': 1.5}}
        con = {}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        assert cached['industry:BK1001'] == 1.5
        assert cached['BK1001'] == 1.5
        assert 'concept:BK1001' not in cached

    def test_concept_only(self, app_ctx):
        """纯概念板块 → 产出 concept:code + bare key"""
        ind = {}
        con = {'BK2001': {'涨跌幅': -2.3}}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        assert cached['concept:BK2001'] == -2.3
        assert cached['BK2001'] == -2.3
        assert 'industry:BK2001' not in cached

    def test_overlapping_code_industry_wins_bare_key(self, app_ctx):
        """行业与概念 BK 代码重叠 → bare key 归 industry"""
        ind = {'BK0001': {'涨跌幅': 3.0}}
        con = {'BK0001': {'涨跌幅': -1.0}}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        assert cached['industry:BK0001'] == 3.0
        assert cached['concept:BK0001'] == -1.0
        assert cached['BK0001'] == 3.0  # industry wins

    def test_industry_does_not_pollute_concept_typed_key(self, app_ctx):
        """行业数据绝不出现在 concept: typed key"""
        ind = {'BK1001': {'涨跌幅': 1.5}, 'BK1002': {'涨跌幅': 2.5}}
        con = {'BK2001': {'涨跌幅': -1.0}}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        # 无任何 industry code 泄漏到 concept: 前缀
        for k in cached:
            if k.startswith('concept:'):
                assert k in ('concept:BK2001',)

    def test_concept_does_not_pollute_industry_typed_key(self, app_ctx):
        """概念数据绝不出现在 industry: typed key"""
        ind = {'BK1001': {'涨跌幅': 1.5}}
        con = {'BK2001': {'涨跌幅': -1.0}, 'BK2002': {'涨跌幅': 0.5}}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        for k in cached:
            if k.startswith('industry:'):
                assert k in ('industry:BK1001',)

    def test_missing_涨跌幅_skipped(self, app_ctx):
        """无涨跌幅字段的条目应跳过"""
        ind = {'BK1001': {'最新价': 10.0}}   # 无涨跌幅
        con = {'BK2001': {'涨跌幅': 1.0}}
        with patch('data.board_api.get_industry_spot', return_value=ind), \
             patch('data.board_api.get_concept_spot', return_value=con):
            app_ctx._reload_board_changes()

        cached = app_ctx.board_chg_cache
        assert 'industry:BK1001' not in cached
        assert 'BK1001' not in cached
        assert cached['concept:BK2001'] == 1.0

    def test_both_empty_falls_back_to_csv(self, app_ctx, tmp_path):
        """spot 均为空 → result 空 → 触发 CSV 兜底（此处只验证不崩溃）"""
        with patch('data.board_api.get_industry_spot', return_value={}), \
             patch('data.board_api.get_concept_spot', return_value={}), \
             patch('core.lifecycle.DATA_DIR', tmp_path):
            app_ctx._reload_board_changes()
        # 无 CSV 文件 → 缓存为空
        assert app_ctx.board_chg_cache == {}
