"""test_auth_guard.py — 写接口本地 Token/IP 白名单守卫测试

覆盖：
- 未配置 BOARD_APP_WRITE_TOKEN 时所有请求放行。
- 配置后 localhost / 127.0.0.1 / ::1 无 token 放行。
- 配置后非本地无 token → 403。
- 配置后非本地正确 token → 通过到业务逻辑。
- 配置后非本地错误 token → 403。
"""
import os
import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')

from app import app


# 业务 mock：让被保护接口走到 "通过" 时可观测
def _mock_force(monkeypatch):
    from services.update_task_service import update_task_service

    def fake_force():
        return update_task_service.create_task(
            'force', lambda t, c: None, detail={'description': 'mock force'}
        )

    monkeypatch.setattr('api.task_routes.create_force_update_task', fake_force)


def _mock_boards(monkeypatch):
    from services.update_task_service import update_task_service

    def fake_boards():
        return update_task_service.create_task(
            'boards', lambda t, c: None, detail={'description': 'mock boards'}
        )

    monkeypatch.setattr('api.task_routes.create_boards_update_task', fake_boards)


def _mock_stock(monkeypatch):
    from services.update_task_service import update_task_service

    def fake_stock(code):
        return update_task_service.create_task(
            f'stock:{code}', lambda t, c: None, detail={'code': code}
        )

    monkeypatch.setattr('api.task_routes.create_stock_update_task', fake_stock)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_task_service():
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


class TestNoTokenConfigured:
    """未配置 BOARD_APP_WRITE_TOKEN 时所有写请求放行"""

    def test_ai_result_post_no_token_configured(self, client):
        # 默认 conftest 不设 WRITE_TOKEN；直接打应穿透到业务逻辑 → 400（缺参数）
        resp = client.post('/api/ai/result', json={'board_code': 'x'})
        # 未配置 token 不拦截；业务层返回 400（缺少 summary）
        assert resp.status_code == 400

    def test_force_update_no_token_configured(self, client, monkeypatch):
        _mock_force(monkeypatch)
        resp = client.post('/api/tasks/update/force')
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_cancel_no_token_configured(self, client):
        resp = client.post('/api/tasks/nonexistent/cancel')
        # 未配置 token → 穿透到业务逻辑 → 404
        assert resp.status_code == 404


class TestWithTokenConfigured:
    """配置 BOARD_APP_WRITE_TOKEN 后的行为"""

    TOKEN = 'test-secret-123'

    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setenv('BOARD_APP_WRITE_TOKEN', self.TOKEN)

    # ---- 本地回环放行 ----

    def test_localhost_no_token_passes(self, client, monkeypatch):
        """127.0.0.1 无 token → 穿透到业务逻辑（默认 test client remote_addr 即 127.0.0.1）"""
        resp = client.post('/api/ai/result', json={'board_code': 'x'})
        assert resp.status_code == 400  # 业务层校验

    def test_localhost_force_passes(self, client, monkeypatch):
        _mock_force(monkeypatch)
        resp = client.post('/api/tasks/update/force')
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_localhost_cancel_passes(self, client):
        resp = client.post('/api/tasks/nonexistent/cancel')
        assert resp.status_code == 404  # 业务层返回

    def test_localhost_clear_passes(self, client):
        """本地回环访问 clear 接口穿透到业务逻辑（不会 403）"""
        resp = client.post('/api/ai/result/BK0001/clear')
        assert resp.status_code == 200

    # ---- 非本地无 token → 403 ----

    def test_remote_no_token_forbidden(self, client):
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'x', 'summary': 'y'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data['error'] == 'forbidden'

    def test_remote_no_token_force_forbidden(self, client, monkeypatch):
        _mock_force(monkeypatch)
        resp = client.post(
            '/api/tasks/update/force',
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 403

    def test_remote_no_token_boards_forbidden(self, client, monkeypatch):
        _mock_boards(monkeypatch)
        resp = client.post(
            '/api/tasks/update/boards',
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 403

    def test_remote_no_token_stock_forbidden(self, client, monkeypatch):
        _mock_stock(monkeypatch)
        resp = client.post(
            '/api/tasks/update/stock/600519',
            environ_base={'REMOTE_ADDR': '172.16.0.1'},
        )
        assert resp.status_code == 403

    def test_remote_no_token_cancel_forbidden(self, client):
        resp = client.post(
            '/api/tasks/abc123/cancel',
            environ_base={'REMOTE_ADDR': '172.16.0.1'},
        )
        assert resp.status_code == 403

    def test_remote_no_token_clear_forbidden(self, client):
        resp = client.post(
            '/api/ai/result/BK0001/clear',
            environ_base={'REMOTE_ADDR': '203.0.113.1'},
        )
        assert resp.status_code == 403

    # ---- 非本地正确 token → 通过 ----

    def test_remote_correct_token_passes(self, client):
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'BK0001', 'summary': '看涨'},
            headers={'X-Board-App-Token': self.TOKEN},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_remote_correct_token_force_passes(self, client, monkeypatch):
        _mock_force(monkeypatch)
        resp = client.post(
            '/api/tasks/update/force',
            headers={'X-Board-App-Token': self.TOKEN},
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 200

    def test_remote_correct_token_stock_passes(self, client, monkeypatch):
        _mock_stock(monkeypatch)
        resp = client.post(
            '/api/tasks/update/stock/600519',
            headers={'X-Board-App-Token': self.TOKEN},
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 200

    def test_remote_correct_token_cancel_passes(self, client, monkeypatch):
        import time as _t
        from services.update_task_service import update_task_service

        # 创建一个长时间运行的任务（模拟 running 状态）
        def slow_runner(task, cancel_check):
            import time
            task.progress = 0.1
            task.message = 'mock running'
            time.sleep(5)

        task = update_task_service.create_task('cancel_me', slow_runner, detail={})
        _t.sleep(0.2)  # 等待任务进入 running
        resp = client.post(
            f'/api/tasks/{task.id}/cancel',
            headers={'X-Board-App-Token': self.TOKEN},
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 200

    # ---- 非本地错误 token → 403 ----

    def test_remote_wrong_token_forbidden(self, client):
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'BK0001', 'summary': '看涨'},
            headers={'X-Board-App-Token': 'wrong-token'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 403

    def test_remote_wrong_token_force_forbidden(self, client, monkeypatch):
        _mock_force(monkeypatch)
        resp = client.post(
            '/api/tasks/update/force',
            headers={'X-Board-App-Token': 'not-the-token'},
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.status_code == 403


class TestIpv6Localhost:
    """IPv6 ::1 回环放行"""

    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setenv('BOARD_APP_WRITE_TOKEN', 'tok')

    def test_ipv6_localhost_passes(self, client):
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'BK0001', 'summary': 'ok'},
            environ_base={'REMOTE_ADDR': '::1'},
        )
        assert resp.status_code == 200


class TestXForwardedForNotTrusted:
    """X-Forwarded-For 不可被客户端伪造 — 安全加固验证"""

    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setenv('BOARD_APP_WRITE_TOKEN', 'tok')

    def test_xff_spoofed_localhost_still_forbidden(self, client):
        """remote_addr 非本地 + X-Forwarded-For:127.0.0.1 + 无 token → 403"""
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'BK0001', 'summary': 'ok'},
            headers={'X-Forwarded-For': '127.0.0.1'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data['error'] == 'forbidden'

    def test_xff_spoofed_with_token_still_works(self, client):
        """即使带 XFF，正确 token 仍应通过（token 校验独立于 IP）"""
        resp = client.post(
            '/api/ai/result',
            json={'board_code': 'BK0001', 'summary': 'ok'},
            headers={
                'X-Forwarded-For': '127.0.0.1',
                'X-Board-App-Token': 'tok',
            },
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 200


class TestSignalPostWriteProtected:
    """POST /api/signals/<board_code> 写保护验证"""

    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setenv('BOARD_APP_WRITE_TOKEN', 'signal-tok')

    def test_signal_post_remote_no_token_forbidden(self, client):
        """非本地无 token → 403"""
        resp = client.post(
            '/api/signals/BK0001',
            json={'skill': 'analyst', 'signals': [{'type': 'buy', 'date': '2026-01-01'}]},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'forbidden'

    def test_signal_post_remote_correct_token_passes(self, client):
        """非本地正确 token → 穿透到业务逻辑"""
        resp = client.post(
            '/api/signals/BK0001',
            json={'skill': 'analyst', 'signals': [{'type': 'buy', 'date': '2026-01-01'}]},
            headers={'X-Board-App-Token': 'signal-tok'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_signal_post_local_bypass(self, client):
        """本地回环无 token → 穿透到业务逻辑"""
        resp = client.post(
            '/api/signals/BK0001',
            json={'skill': 'analyst', 'signals': [{'type': 'buy', 'date': '2026-01-01'}]},
        )
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
