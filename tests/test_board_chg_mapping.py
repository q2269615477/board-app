"""覆盖 lifecycle 到统一 BoardSpotCache 的委托链路。"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app_ctx():
    from core.lifecycle import AppContext
    return AppContext()


class TestBoardChgMapping:
    """生命周期层只消费 BoardSpotCache 的派生涨跌幅。"""

    @staticmethod
    def _reload(app_ctx, result):
        cache = MagicMock()
        cache.get_chgs.return_value = result
        with patch('services.board_spot_cache.get_board_spot_cache', return_value=cache):
            app_ctx._reload_board_changes()
        cache.get.assert_any_call('industry')
        cache.get.assert_any_call('concept')
        return cache

    def test_industry_only(self, app_ctx):
        cached = self._reload(app_ctx, {
            'industry:BK1001': 1.5, 'BK1001': 1.5,
        })
        assert app_ctx.board_chg_cache['industry:BK1001'] == 1.5
        assert app_ctx.board_chg_cache['BK1001'] == 1.5
        assert 'concept:BK1001' not in app_ctx.board_chg_cache
        assert cached.get_chgs.called

    def test_concept_only(self, app_ctx):
        self._reload(app_ctx, {
            'concept:BK2001': -2.3, 'BK2001': -2.3,
        })
        assert app_ctx.board_chg_cache['concept:BK2001'] == -2.3
        assert app_ctx.board_chg_cache['BK2001'] == -2.3

    def test_overlapping_code_industry_wins_bare_key(self, app_ctx):
        self._reload(app_ctx, {
            'industry:BK0001': 3.0, 'concept:BK0001': -1.0, 'BK0001': 3.0,
        })
        assert app_ctx.board_chg_cache['industry:BK0001'] == 3.0
        assert app_ctx.board_chg_cache['concept:BK0001'] == -1.0
        assert app_ctx.board_chg_cache['BK0001'] == 3.0

    def test_cache_failure_does_not_recreate_direct_tushare_chain(self, app_ctx):
        app_ctx.board_chg_cache = {'industry:BK0001': 1.0}
        with patch('services.board_spot_cache.get_board_spot_cache',
                   side_effect=RuntimeError('cache unavailable')):
            app_ctx._reload_board_changes()
        assert app_ctx.board_chg_cache == {'industry:BK0001': 1.0}

    def test_industry_failure_does_not_block_concept_refresh(self, app_ctx):
        cache = MagicMock()
        cache.get.side_effect = [RuntimeError('industry unavailable'), {'BK2001': {}}]
        cache.get_chgs.return_value = {'concept:BK2001': 2.5, 'BK2001': 2.5}

        with patch('services.board_spot_cache.get_board_spot_cache', return_value=cache):
            app_ctx._reload_board_changes(force=True)

        assert cache.get.call_count == 2
        cache.get.assert_any_call('industry', force=True)
        cache.get.assert_any_call('concept', force=True)
        assert app_ctx.board_chg_cache['concept:BK2001'] == 2.5

    def test_derived_view_failure_preserves_last_success(self, app_ctx):
        app_ctx.board_chg_cache = {'concept:BK2001': -1.2}
        cache = MagicMock()
        cache.get_chgs.side_effect = RuntimeError('derive failed')

        with patch('services.board_spot_cache.get_board_spot_cache', return_value=cache):
            app_ctx._reload_board_changes()

        assert app_ctx.board_chg_cache == {'concept:BK2001': -1.2}
