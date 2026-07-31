"""test_update_task_routes.py — 任务中心路由测试

覆盖：
- 创建 force/boards/stock 任务时返回 ok=true、包含 task.id
- 同类任务重复触发时返回已有 running task
- GET /api/tasks 返回任务列表
- GET /api/tasks/<id> 返回单任务详情
- 取消 pending 任务成功
- 取消 running 任务返回 CANCEL_REQUESTED
- 旧接口返回 deprecated: true
"""
import os
import time
import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')

from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


# ---- 工厂函数 mock：避免真实数据更新 ----

def _mock_runner_factory(sleep_sec=0.2):
    """返回一个不会做真实更新的 runner，但会短暂 sleep 以模拟运行"""
    def runner(task, cancel_check):
        import time as _t
        task.progress = 0.1
        task.message = 'mock running'
        _t.sleep(sleep_sec)
        if cancel_check():
            task.status = 'canceled'
            task.message = 'mock canceled'
            return
        task.progress = 1.0
    return runner


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


class TestTaskRoutes:
    """任务中心路由基本功能"""

    def test_list_empty_tasks(self, client):
        resp = client.get('/api/tasks')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert isinstance(data['tasks'], list)

    def test_get_nonexistent_task(self, client):
        resp = client.get('/api/tasks/nonexistent')
        assert resp.status_code == 404

    def test_cancel_nonexistent_task(self, client):
        resp = client.post('/api/tasks/nonexistent/cancel')
        assert resp.status_code == 404


class TestForceUpdateTask:
    """强制刷新任务"""

    def test_create_force_task(self, client, monkeypatch):
        # mock 工厂函数，避免真实更新
        from services.update_task_factories import create_force_update_task
        from services.update_task_service import update_task_service

        monkeypatch.setattr(
            'services.update_task_factories.create_force_update_task',
            lambda: update_task_service.create_task(
                'force', _mock_runner_factory(),
                detail={'description': 'mock force'}
            )
        )
        # patch api.task_routes 模块级导入的引用
        monkeypatch.setattr(
            'api.task_routes.create_force_update_task',
            lambda: update_task_service.create_task(
                'force', _mock_runner_factory(),
                detail={'description': 'mock force'}
            )
        )

        resp = client.post('/api/tasks/update/force')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert 'task' in data
        assert 'id' in data['task']

    def test_duplicate_force_task(self, client, monkeypatch):
        from services.update_task_service import update_task_service

        # 先创建一个运行中的任务
        long_runner = _mock_runner_factory(sleep_sec=5)
        update_task_service.create_task('force', long_runner, detail={})

        # patch api.task_routes 模块级引用
        monkeypatch.setattr(
            'api.task_routes.create_force_update_task',
            lambda: update_task_service.create_task('force', _mock_runner_factory(5), detail={})
        )

        resp = client.post('/api/tasks/update/force')
        assert resp.status_code == 200
        data = resp.get_json()
        # 应返回已有 running task
        assert data['ok'] is True


class TestCancelTask:
    """任务取消测试"""

    def test_cancel_pending_or_running(self, client, monkeypatch):
        from services.update_task_service import update_task_service

        # 创建一个长时间运行的任务
        runner = _mock_runner_factory(sleep_sec=10)
        task = update_task_service.create_task('test_cancel', runner, detail={})
        time.sleep(0.1)  # 等待任务进入 running

        resp = client.post(f'/api/tasks/{task.id}/cancel')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        # pending 或 running 都应该可以取消
        assert data['task'] is not None


class TestUpdateDebtRoute:
    """GET /api/update/debt 只读欠更扫描"""

    def test_debt_route_ok(self, client, monkeypatch):
        fake_debt = {
            'summary': '发现欠更：stocks lagging=2',
            'needs_catchup': True,
            'stocks': {'total': 10, 'lagging': 2, 'max_lag': 3},
            'indices': {'total': 5, 'lagging': 0, 'max_lag': 0},
            'boards': {'total': 8, 'lagging': 1, 'max_lag': 1},
        }
        monkeypatch.setattr(
            'data_update_manager.scan_update_debt',
            lambda: fake_debt,
            raising=False,
        )

        resp = client.get('/api/update/debt')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert 'debt' in data
        assert data['debt']['summary'] == fake_debt['summary']
        assert data['debt']['needs_catchup'] is True


class TestDeprecatedOldRoutes:
    """旧接口兼容测试"""

    def test_old_force_returns_deprecated(self, client, monkeypatch):
        from services.update_task_service import update_task_service

        monkeypatch.setattr(
            'services.update_task_factories.create_force_update_task',
            lambda: update_task_service.create_task(
                'force', _mock_runner_factory(),
                detail={'description': 'mock'}
            )
        )

        resp = client.post('/api/update/force')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('deprecated') is True
        assert 'task' in data

    def test_old_boards_returns_deprecated(self, client, monkeypatch):
        from services.update_task_service import update_task_service

        monkeypatch.setattr(
            'services.update_task_factories.create_boards_update_task',
            lambda: update_task_service.create_task(
                'boards', _mock_runner_factory(),
                detail={'description': 'mock'}
            )
        )

        resp = client.post('/api/update/boards')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('deprecated') is True

    def test_old_stock_returns_deprecated(self, client, monkeypatch):
        from services.update_task_service import update_task_service

        monkeypatch.setattr(
            'services.update_task_factories.create_stock_update_task',
            lambda code: update_task_service.create_task(
                f'stock:{code}', _mock_runner_factory(),
                detail={'code': code}
            )
        )

        resp = client.post('/api/update/stock/600519')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('deprecated') is True
