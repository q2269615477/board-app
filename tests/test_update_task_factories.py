"""test_update_task_factories.py — 任务工厂 service 层测试

同步捕获 runner 执行，不依赖后台线程，避免状态污染。
"""
import os
import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')


@pytest.fixture(autouse=True)
def _reset_task_service():
    """每个测试前清空任务服务状态"""
    from services.update_task_service import update_task_service
    with update_task_service._lock:
        update_task_service._tasks.clear()
        update_task_service._cancel_flags.clear()
        update_task_service._running_types.clear()
    yield
    with update_task_service._lock:
        update_task_service._tasks.clear()
        update_task_service._cancel_flags.clear()
        update_task_service._running_types.clear()


def _capture_runner(monkeypatch):
    """Patch create_task 以捕获 runner，不启动后台线程。返回 captured dict。"""
    from services.update_task_service import update_task_service, UpdateTask

    captured = {}

    def capture_create(task_type, runner, detail=None):
        captured['fn'] = runner
        captured['type'] = task_type
        task = UpdateTask(
            id='test000',
            type=task_type,
            status='running',
            message='test',
            started_at='2026-01-01 00:00:00',
            detail=detail or {},
        )
        return task

    monkeypatch.setattr(update_task_service, 'create_task', capture_create)
    return captured


def _mock_debt(summary='发现欠更：stocks lagging=3', needs_catchup=True):
    return {
        'summary': summary,
        'needs_catchup': needs_catchup,
        'stocks': {'total': 100, 'lagging': 3, 'max_lag': 5},
        'indices': {'total': 10, 'lagging': 0, 'max_lag': 0},
        'boards': {'total': 50, 'lagging': 1, 'max_lag': 2},
    }


def _patch_force_deps(monkeypatch, debt=None, full_running=False, update_all_today=None):
    """force runner 依赖：scan_update_debt + cache + 内存 full 标志 + optional update_all_today。"""
    debt_fn = (lambda: debt) if debt is not None else (lambda: _mock_debt())
    monkeypatch.setattr('data_update_manager.scan_update_debt', debt_fn, raising=False)
    # force 只读内存标志，不再走 get_update_status（会扫欠更）
    monkeypatch.setattr(
        'services.update_task_factories._full_update_already_running',
        lambda: bool(full_running),
    )
    monkeypatch.setattr(
        'core.cache.get_cache',
        lambda: type('MC', (), {'clear': lambda *a, **k: None})(),
    )
    # 轻量 spot：不打网
    monkeypatch.setattr(
        'services.update_task_factories._quick_spot_refresh',
        lambda: None,
    )
    if update_all_today is not None:
        monkeypatch.setattr(
            'data_update_manager.update_all_today',
            update_all_today,
            raising=False,
        )


class TestForceFactoryInstant:
    """create_force_update_task：超即时路径 — 同步只清缓存/spot，欠更与全量全在后台"""

    def test_sync_path_instant_no_debt_scan(self, monkeypatch):
        """同步路径不调用 scan_update_debt；progress=1.0，message 含即时刷新"""
        from services.update_task_factories import create_force_update_task

        def boom():
            raise AssertionError('scan_update_debt must not be called on sync path')

        monkeypatch.setattr('data_update_manager.scan_update_debt', boom, raising=False)
        _patch_force_deps(monkeypatch, debt=None, full_running=False)
        # 覆盖 _patch 里的 debt stub，确保 sync 若误调会炸
        monkeypatch.setattr('data_update_manager.scan_update_debt', boom, raising=False)

        started = []
        monkeypatch.setattr(
            'services.update_task_factories.threading.Thread',
            lambda *a, **k: type('T', (), {
                'start': lambda self: started.append(1),
            })(),
        )

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)

        assert task.progress == 1.0
        assert task.status != 'failed'
        assert '即时刷新' in (task.message or '')
        # 同步路径 debt_before 仍为默认 None（后台才写）
        assert task.detail.get('debt_before') is None

    def test_reuses_success_task_while_its_background_is_active(self):
        from services.update_task_factories import create_force_update_task
        from services.update_task_service import update_task_service, UpdateTask

        existing = UpdateTask(
            id='activebg',
            type='force',
            status='success',
            message='即时部分完成',
            started_at='2026-08-04 09:00:00',
            detail={'background_state': 'running'},
        )
        with update_task_service._lock:
            update_task_service._tasks[existing.id] = existing

        assert create_force_update_task() is existing

    def test_sync_path_does_not_call_update_all_today(self, monkeypatch):
        """同步 runner 路径不调用 update_all_today（仅 bg target 才可能调用）"""
        from services.update_task_factories import create_force_update_task

        called = {'n': 0}

        def mock_update_all_today(*a, **k):
            called['n'] += 1
            return {}

        _patch_force_deps(
            monkeypatch, debt=_mock_debt(needs_catchup=True), full_running=False,
            update_all_today=mock_update_all_today,
        )

        # 不真正跑 bg
        monkeypatch.setattr(
            'services.update_task_factories.threading.Thread',
            lambda *a, **k: type('T', (), {'start': lambda self: None})(),
        )

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)

        assert called['n'] == 0
        assert task.detail.get('background_catchup') is False
        assert task.progress == 1.0
        assert task.status != 'failed'

    def test_background_thread_started(self, monkeypatch):
        """同步路径始终起一个后台线程"""
        from services.update_task_factories import create_force_update_task

        started = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **kw):
                self.target = target

            def start(self):
                started.append(1)

        _patch_force_deps(monkeypatch, debt=_mock_debt(needs_catchup=True), full_running=False)
        monkeypatch.setattr(
            'services.update_task_factories.threading.Thread',
            FakeThread,
        )

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)

        assert len(started) == 1
        assert task.progress == 1.0
        assert task.status != 'failed'

    def test_bg_target_writes_debt_and_catchup(self, monkeypatch):
        """后台 target：needs_catchup + 全量未跑 → 写 debt_before、update_all_today(force=True)、background_catchup"""
        from services.update_task_factories import create_force_update_task

        bg_targets = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **kw):
                bg_targets.append(target)

            def start(self):
                pass

        debt = _mock_debt(summary='发现欠更：stocks lagging=3', needs_catchup=True)
        update_calls = []

        def mock_update_all_today(*a, **k):
            update_calls.append({'force': k.get('force', a[0] if a else None)})
            return {'marked_today_done': True}

        _patch_force_deps(
            monkeypatch, debt=debt, full_running=False,
            update_all_today=mock_update_all_today,
        )
        monkeypatch.setattr(
            'services.update_task_factories.threading.Thread',
            FakeThread,
        )

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)

        # 同步路径尚未写 debt / 未调全量
        assert task.detail.get('debt_before') is None
        assert update_calls == []
        assert len(bg_targets) == 1 and callable(bg_targets[0])

        bg_targets[0]()

        db = task.detail.get('debt_before')
        assert isinstance(db, dict)
        assert db.get('summary') == '发现欠更：stocks lagging=3'
        assert db.get('needs_catchup') is True
        assert db.get('stocks', {}).get('lagging') == 3
        assert len(update_calls) == 1
        assert update_calls[0].get('force') is True
        assert task.detail.get('background_catchup') is True
        assert task.detail.get('background_state') == 'complete'

    def test_bg_target_exposes_independent_area_failures(self, monkeypatch):
        from services.update_task_factories import create_force_update_task

        bg_targets = []

        class FakeThread:
            def __init__(self, target=None, **_kwargs):
                bg_targets.append(target)

            def start(self):
                pass

        result = {
            'indices': {'success': 2, 'failed': 0, 'completion_ready': True},
            'stocks': {'success': 0, 'failed': 1, 'error': 'QMT unavailable'},
            'boards': {'success': 3, 'failed': 0, 'formal_ready': True},
            'weekly_monthly': {'success': 3, 'failed': 0, 'completion_ready': True},
            'pending_stages': ['stocks'],
            'completion_ready': False,
        }
        _patch_force_deps(
            monkeypatch,
            debt=_mock_debt(needs_catchup=True),
            update_all_today=lambda **_kwargs: result,
        )
        monkeypatch.setattr('services.update_task_factories.threading.Thread', FakeThread)

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)
        bg_targets[0]()

        areas = task.detail['areas']
        assert areas['indices']['status'] == 'fresh'
        assert areas['stocks']['status'] == 'failed'
        assert areas['stocks']['source'] == 'QMT'
        assert areas['boards']['status'] == 'fresh'
        assert task.detail['background_state'] == 'complete'

    def test_debt_scan_failure_forces_best_effort_catchup(self, monkeypatch):
        from services.update_task_factories import _scan_update_debt_safe

        monkeypatch.setattr(
            'data_update_manager.scan_update_debt',
            lambda: (_ for _ in ()).throw(RuntimeError('db busy')),
        )

        debt = _scan_update_debt_safe()

        assert debt['needs_catchup'] is True
        assert '不可用' in debt['summary']

    def test_deferred_daily_result_keeps_schedule_reason_per_area(self):
        from services.update_task_factories import _result_area_statuses

        areas = _result_area_statuses({
            'deferred': True,
            'completion_ready': False,
            'pending_stages': ['indices', 'stocks', 'boards'],
            'message': '等待15:30后执行盘后增量',
        })

        assert all(area['status'] == 'deferred' for area in areas.values())
        assert all('15:30' in area['message'] for area in areas.values())

    def test_unavailable_universe_is_not_reported_fresh(self):
        from services.update_task_factories import _debt_area_statuses

        areas = _debt_area_statuses({
            'indices': {'available': True, 'total': 1, 'lagging': 0},
            'stocks': {'available': False, 'error': 'ledger busy'},
            'boards': {'available': False, 'error': 'taxonomy busy'},
        })

        assert areas['indices']['status'] == 'fresh'
        assert areas['stocks']['status'] == 'unavailable'
        assert areas['boards']['status'] == 'unavailable'

        legacy_error = _debt_area_statuses({
            'indices': {'error': 'db busy', 'total': 0, 'lagging': 0},
        })
        assert legacy_error['indices']['status'] == 'unavailable'

    def test_full_already_running_succeeds(self, monkeypatch):
        """全量已在进行 → force 仍成功；bg 起线程但 target 不调 update_all_today"""
        from services.update_task_factories import create_force_update_task

        bg_targets = []
        started = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **kw):
                bg_targets.append(target)

            def start(self):
                started.append(1)

        debt = _mock_debt(needs_catchup=True)
        update_calls = []

        def mock_update_all_today(*a, **k):
            update_calls.append(1)
            return {}

        _patch_force_deps(
            monkeypatch, debt=debt, full_running=True,
            update_all_today=mock_update_all_today,
        )
        monkeypatch.setattr(
            'services.update_task_factories.threading.Thread',
            FakeThread,
        )

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, lambda: False)

        assert len(started) == 1
        assert task.detail.get('background_catchup') is False
        assert task.detail.get('full_already_running') is True
        assert task.progress == 1.0
        assert task.status != 'failed'
        assert '进行中' in task.message or '即时' in task.message

        assert len(bg_targets) == 1 and callable(bg_targets[0])
        bg_targets[0]()
        assert update_calls == []
        assert task.detail.get('background_catchup') is False
        assert task.detail.get('full_already_running') is True

    def test_cancel_mid_quick_path(self, monkeypatch):
        """cancel_check 在快速路径开头返回 True → canceled"""
        from services.update_task_factories import create_force_update_task

        _patch_force_deps(monkeypatch, debt=_mock_debt(needs_catchup=True), full_running=False)

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        # 第一次 cancel 检查即取消
        cap['fn'](task, lambda: True)

        assert task.status == 'canceled'
        assert '取消' in task.message

    def test_cancel_after_cache_clear(self, monkeypatch):
        """第二次 cancel 检查（清缓存后）也能取消"""
        from services.update_task_factories import create_force_update_task

        _patch_force_deps(monkeypatch, debt=_mock_debt(needs_catchup=False), full_running=False)

        n = [0]

        def cancel_check():
            n[0] += 1
            return n[0] >= 2  # 第二次检查取消

        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        cap['fn'](task, cancel_check)

        assert task.status == 'canceled'

    def test_task_description_instant(self, monkeypatch):
        """任务 detail description 标明即时刷新"""
        from services.update_task_factories import create_force_update_task

        _patch_force_deps(monkeypatch, debt=_mock_debt(needs_catchup=False))
        cap = _capture_runner(monkeypatch)
        task = create_force_update_task()
        assert '即时' in (task.detail.get('description') or '')


class TestBoardsFactory:
    """验证 create_boards_update_task 只调 update_all_boards 并正确终态"""

    def test_calls_update_all_boards_not_today(self, monkeypatch):
        from services.update_task_factories import create_boards_update_task

        called = {'boards': 0, 'today': 0}

        def mock_boards(cancel_check=None):
            called['boards'] += 1
            assert cancel_check is not None
            return {'success': 10, 'failed': 0, 'total': 10}

        def mock_today(*a, **k):
            called['today'] += 1
            return {}

        monkeypatch.setattr('data_update_manager.update_all_boards', mock_boards)
        monkeypatch.setattr('data_update_manager.update_all_today', mock_today)

        cap = _capture_runner(monkeypatch)
        task = create_boards_update_task()
        cap['fn'](task, lambda: False)

        assert called['boards'] == 1
        assert called['today'] == 0
        assert task.detail.get('stage') == 'boards'
        assert task.status == 'running'  # wrapper 会标 success；runner 不抢标
        assert task.progress == 1.0

    def test_success_zero_fails(self, monkeypatch):
        """success=0 → task 终态 failed"""
        from services.update_task_factories import create_boards_update_task

        def mock_boards(cancel_check=None):
            return {'success': 0, 'failed': 0, 'total': 5}

        monkeypatch.setattr('data_update_manager.update_all_boards', mock_boards)

        cap = _capture_runner(monkeypatch)
        task = create_boards_update_task()
        cap['fn'](task, lambda: False)

        assert task.status == 'failed'
        assert '失败' in task.message or 'success=0' in task.message

    def test_error_fails(self, monkeypatch):
        from services.update_task_factories import create_boards_update_task

        def mock_boards(cancel_check=None):
            return {'success': 0, 'failed': 0, 'total': 0, 'error': '上次更新进行中'}

        monkeypatch.setattr('data_update_manager.update_all_boards', mock_boards)

        cap = _capture_runner(monkeypatch)
        task = create_boards_update_task()
        cap['fn'](task, lambda: False)

        assert task.status == 'failed'
        assert '进行中' in task.message or '失败' in task.message

    def test_canceled_result(self, monkeypatch):
        from services.update_task_factories import create_boards_update_task

        def mock_boards(cancel_check=None):
            return {'success': 3, 'canceled': True}

        monkeypatch.setattr('data_update_manager.update_all_boards', mock_boards)

        cap = _capture_runner(monkeypatch)
        task = create_boards_update_task()
        cap['fn'](task, lambda: False)

        assert task.status == 'canceled'


class TestStockFactoryCancelCheck:
    """验证单只股票任务在 cancel_check 返回 True 时提前退出"""

    def test_stock_runner_cancels_at_start(self, monkeypatch):
        """直接调用 runner，cancel_check 始终返回 True，验证不调用 fetch_qmt_kline"""
        from services.update_task_factories import create_stock_update_task

        fetch_called = False

        def mock_fetch_qmt_kline(code, start_date):
            nonlocal fetch_called
            fetch_called = True
            return []

        monkeypatch.setattr('data_update_manager.fetch_qmt_kline', mock_fetch_qmt_kline)

        cap = _capture_runner(monkeypatch)
        task = create_stock_update_task('600519')

        assert cap['fn'] is not None
        cap['fn'](task, lambda: True)

        assert task.status == 'canceled'
        assert not fetch_called

    def test_stock_runner_cancels_after_update(self, monkeypatch):
        """runner 执行完 fetch 后 cancel_check 返回 True，验证标记 canceled"""
        from services.update_task_factories import create_stock_update_task

        call_count = [0]

        def mock_fetch_qmt_kline(code, start_date):
            call_count[0] += 1
            return []

        monkeypatch.setattr('data_update_manager.fetch_qmt_kline', mock_fetch_qmt_kline)
        monkeypatch.setattr('data.sqlite_repo.get_sqlite_repo',
                            lambda: type('MR', (), {'save_kline': lambda *a, **k: None})())
        monkeypatch.setattr('core.cache.get_cache',
                            lambda: type('MC', (), {'delete': lambda *a, **k: None})())

        cap = _capture_runner(monkeypatch)
        task = create_stock_update_task('600519')

        check_count = [0]
        def cancel_check():
            check_count[0] += 1
            return check_count[0] > 1

        cap['fn'](task, cancel_check)

        assert call_count[0] == 1
        assert task.status == 'canceled'
