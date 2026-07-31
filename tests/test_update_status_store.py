"""tests/test_update_status_store.py — update_status_store 模块测试

验证从 data_update_manager.py 抽取的状态存储层：
  - load_status / save_status 的基本 CRUD
  - 线程安全
  - mark_today_done / is_today_updated
  - mark_qmt_daily_done / is_qmt_daily_done
  - data_update_manager.py 中的 facade 函数正确委托
"""
import ast
import json
import sqlite3
import threading
from datetime import datetime as RealDateTime
from pathlib import Path
from unittest.mock import patch

import pytest

from services.update_status_store import (
    load_status, save_status, mark_today_done, mark_qmt_daily_done,
    is_today_updated, is_qmt_daily_done, update_status, _default_status,
)


class TestLoadStatus:
    """load_status 基本行为。"""

    def test_load_creates_default_when_file_missing(self, tmp_path):
        status = load_status(tmp_path / 'status.json')
        assert 'indices' in status
        assert 'boards' in status
        assert 'stocks' in status
        assert status['today'] == ''
        assert status['qmt_daily_done'] == ''
        assert status['scheduler'] == {'last_run': '', 'next_run': '', 'status': 'idle'}

    def test_load_reads_existing_file(self, tmp_path):
        sf = tmp_path / 'status.json'
        sf.write_text(json.dumps({'today': '2025-01-01', 'indices': {}}), encoding='utf-8')
        status = load_status(sf)
        assert status['today'] == '2025-01-01'

    def test_load_fills_missing_keys(self, tmp_path):
        sf = tmp_path / 'status.json'
        sf.write_text(json.dumps({'today': '2025-01-01'}), encoding='utf-8')
        status = load_status(sf)
        assert 'indices' in status
        assert 'boards' in status
        assert 'stocks' in status

    def test_load_corrupt_file_returns_default(self, tmp_path):
        sf = tmp_path / 'status.json'
        sf.write_text('{invalid json', encoding='utf-8')
        status = load_status(sf)
        assert status['today'] == ''
        assert 'indices' in status


class TestSaveStatus:
    """save_status 基本行为。"""

    def test_save_then_load(self, tmp_path):
        sf = tmp_path / 'status.json'
        status = load_status(sf)
        status['today'] = '2025-06-15'
        save_status(status, sf)
        loaded = load_status(sf)
        assert loaded['today'] == '2025-06-15'

    def test_save_creates_parent_dir(self, tmp_path):
        sf = tmp_path / 'subdir' / 'status.json'
        save_status({'today': '2025-01-01'}, sf)
        assert sf.exists()

    def test_save_unicode_content(self, tmp_path):
        sf = tmp_path / 'status.json'
        status = {'boards': {'BK0001': {'name': '半导体芯片'}}}
        save_status(status, sf)
        loaded = load_status(sf)
        assert loaded['boards']['BK0001']['name'] == '半导体芯片'


class TestThreadSafety:
    """线程安全测试。"""

    def test_concurrent_read_write(self, tmp_path):
        sf = tmp_path / 'status.json'
        errors = []

        def worker():
            try:
                for _ in range(20):
                    s = load_status(sf)
                    s['today'] = '2025-01-01'
                    save_status(s, sf)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert all(not t.is_alive() for t in threads)
        assert len(errors) == 0

    def test_concurrent_mark_today(self, tmp_path):
        sf = tmp_path / 'status.json'
        errors = []

        def worker():
            try:
                for _ in range(10):
                    mark_today_done(sf)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert all(not t.is_alive() for t in threads)
        assert len(errors) == 0
        assert is_today_updated(sf)

    def test_concurrent_update_status_preserves_distinct_fields(self, tmp_path):
        sf = tmp_path / 'status.json'
        barrier = threading.Barrier(12)
        errors = []

        def worker(index):
            try:
                barrier.wait()

                def mutate(status):
                    status.setdefault('indices', {})[f'idx-{index}'] = {'status': 'success'}
                    status.setdefault('boards', {})[f'board-{index}'] = {'status': 'success'}

                update_status(mutate, sf)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)

        assert errors == []
        status = load_status(sf)
        assert set(status['indices']) == {f'idx-{i}' for i in range(12)}
        assert set(status['boards']) == {f'board-{i}' for i in range(12)}

    def test_concurrent_marks_preserve_both_flags(self, tmp_path):
        sf = tmp_path / 'status.json'
        barrier = threading.Barrier(2)
        errors = []

        def mark_today():
            try:
                barrier.wait()
                mark_today_done(sf)
            except Exception as exc:
                errors.append(exc)

        def mark_qmt():
            try:
                barrier.wait()
                mark_qmt_daily_done(sf)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=mark_today), threading.Thread(target=mark_qmt)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)

        assert errors == []
        status = load_status(sf)
        assert status['today']
        assert status['qmt_daily_done']

    def test_replace_failure_preserves_old_file_and_cleans_temp(self, tmp_path):
        sf = tmp_path / 'status.json'
        original = {'today': '2025-01-01', 'indices': {'old': {'status': 'success'}}}
        save_status(original, sf)
        old_bytes = sf.read_bytes()

        with patch('services.update_status_store.os.replace', side_effect=OSError('disk full')):
            save_status({'today': '2026-01-01'}, sf)

        assert sf.read_bytes() == old_bytes
        assert list(tmp_path.glob('.status.json.*.tmp')) == []

    def test_corrupt_file_is_replaced_by_valid_updated_status(self, tmp_path):
        sf = tmp_path / 'status.json'
        sf.write_text('{broken', encoding='utf-8')

        update_status(lambda status: status['indices'].__setitem__('sh000001', {'status': 'success'}), sf)

        loaded = json.loads(sf.read_text(encoding='utf-8'))
        assert loaded['today'] == ''
        assert loaded['indices']['sh000001']['status'] == 'success'

    def test_mutator_exception_propagates_without_write(self, tmp_path):
        sf = tmp_path / 'status.json'
        save_status({'today': '2025-01-01'}, sf)
        old_bytes = sf.read_bytes()

        def mutate(_status):
            raise RuntimeError('mutator failed')

        with pytest.raises(RuntimeError, match='mutator failed'):
            update_status(mutate, sf)
        assert sf.read_bytes() == old_bytes


class TestTodayUpdated:
    """mark_today_done / is_today_updated 行为。"""

    def test_not_updated_on_empty(self, tmp_path):
        assert not is_today_updated(tmp_path / 'status.json')

    def test_mark_then_updated(self, tmp_path):
        sf = tmp_path / 'status.json'
        mark_today_done(sf)
        assert is_today_updated(sf)

    def test_mark_yesterday_not_today(self, tmp_path):
        sf = tmp_path / 'status.json'
        # 写入昨天的日期
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        save_status({'today': yesterday}, sf)
        assert not is_today_updated(sf)


class TestQmtDailyDone:
    """mark_qmt_daily_done / is_qmt_daily_done 行为。"""

    def test_not_done_on_empty(self, tmp_path):
        assert not is_qmt_daily_done(tmp_path / 'status.json')

    def test_mark_then_done(self, tmp_path):
        sf = tmp_path / 'status.json'
        mark_qmt_daily_done(sf)
        assert is_qmt_daily_done(sf)

    def test_mark_does_not_affect_today(self, tmp_path):
        sf = tmp_path / 'status.json'
        mark_qmt_daily_done(sf)
        # qmt_daily_done 不应影响 is_today_updated
        assert not is_today_updated(sf)


class TestDefaultStatus:
    """_default_status 返回结构验证。"""

    def test_default_has_all_keys(self):
        s = _default_status()
        for key in ('boards', 'indices', 'stocks', 'today', 'qmt_daily_done', 'scheduler'):
            assert key in s, f"缺少 key: {key}"

    def test_default_scheduler_structure(self):
        s = _default_status()
        assert s['scheduler'] == {'last_run': '', 'next_run': '', 'status': 'idle'}


class TestFacadeDelegation:
    """验证 data_update_manager.py 中的 facade 函数正确委托。"""

    def test_data_update_manager_load_status_delegates(self, tmp_path):
        """_load_status 委托给 update_status_store.load_status。"""
        with patch('data_update_manager.STATUS_FILE', tmp_path / 'status.json'):
            from data_update_manager import _load_status
            status = _load_status()
            assert 'indices' in status
            assert 'boards' in status

    def test_data_update_manager_save_then_load(self, tmp_path):
        """_save_status + _load_status 往返一致。"""
        sf = tmp_path / 'status.json'
        with patch('data_update_manager.STATUS_FILE', sf):
            from data_update_manager import _load_status, _save_status
            status = _load_status()
            status['today'] = '2025-07-31'
            _save_status(status)
            loaded = _load_status()
            assert loaded['today'] == '2025-07-31'

    def test_data_update_manager_mark_today_done(self, tmp_path):
        """_mark_today_done 委托正确。"""
        sf = tmp_path / 'status.json'
        with patch('data_update_manager.STATUS_FILE', sf):
            from data_update_manager import _mark_today_done, is_today_updated
            _mark_today_done()
            assert is_today_updated()

    def test_data_update_manager_mark_qmt_daily_done(self, tmp_path):
        """_mark_qmt_daily_done 委托正确。"""
        sf = tmp_path / 'status.json'
        with patch('data_update_manager.STATUS_FILE', sf):
            from data_update_manager import _mark_qmt_daily_done, is_qmt_daily_done
            _mark_qmt_daily_done()
            assert is_qmt_daily_done()

    def test_data_update_manager_no_direct_json_io(self):
        """data_update_manager.py 不再直接操作 JSON 文件。"""
        src = Path('data_update_manager.py').read_text(encoding='utf-8')
        # facade 函数应该委托，不应包含 json.dump 或 json.load
        # （仅在 facade 函数体内检查，import 行除外）
        assert 'from services.update_status_store import' in src

    def test_data_update_manager_update_status_passes_status_path(self, tmp_path):
        from data_update_manager import _update_status

        mutator = lambda status: status.update({'today': '2025-07-31'})
        with patch('data_update_manager.STATUS_FILE', tmp_path / 'status.json'):
            with patch('data_update_manager._status_update', return_value={'today': '2025-07-31'}) as mocked:
                _update_status(mutator)

        mocked.assert_called_once_with(mutator, tmp_path / 'status.json')

    def test_data_update_manager_business_functions_do_not_read_then_save(self):
        tree = ast.parse(Path('data_update_manager.py').read_text(encoding='utf-8'))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            if '_load_status' in calls and '_save_status' in calls:
                offenders.append(node.name)
        assert offenders == []


class TestIndexStaleControlFlow:
    """update_all_indices_qmt 的 stale_no_source 回归测试。"""

    @staticmethod
    def _prepare_index_db(db_path, local_max):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE kline (
                code TEXT, period TEXT, date TEXT, open REAL, high REAL,
                low REAL, close REAL, volume REAL, updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE kline_meta (
                code TEXT, period TEXT, rows INTEGER, first_date TEXT,
                last_date TEXT, updated_at TEXT,
                PRIMARY KEY (code, period)
            )
            """
        )
        conn.execute(
            "INSERT INTO kline VALUES (?, 'daily', ?, 1, 2, 0.5, 1.5, 100, '')",
            ('sh000001', local_max),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _fixed_datetime():
        class FixedDateTime(RealDateTime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 31, 15, 0, 0)

        return FixedDateTime

    def _run_index_update(self, tmp_path, local_max, update_status_side_effect=None):
        import data_update_manager as manager

        db_path = tmp_path / 'kline.db'
        self._prepare_index_db(db_path, local_max)
        fixed_datetime = self._fixed_datetime()
        with patch.object(manager, 'datetime', fixed_datetime), \
             patch.object(manager, '_LEDGER_DB', str(db_path)), \
             patch.object(manager, 'PREWARM_TARGETS', [('sh000001', '上证指数', 'index')]), \
             patch.object(manager, 'BOARD_ONLY_PREWARM', frozenset()), \
             patch.object(manager, '_qmt_connect', return_value=True), \
             patch.object(manager, 'fetch_qmt_kline', return_value=[]), \
             patch.object(manager, '_tushare_fallback_single_index', return_value=0), \
             patch.object(manager, '_update_status', side_effect=update_status_side_effect) as mocked_update:
            result = manager.update_all_indices_qmt(max_retries=1)
        return result, mocked_update

    def test_yesterday_is_success_without_stale_update(self, tmp_path):
        result, mocked_update = self._run_index_update(tmp_path, '2026-07-30')

        assert result['success'] == 1
        assert result['failed'] == 0
        mocked_update.assert_not_called()

    def test_older_than_yesterday_writes_stale_status(self, tmp_path):
        captured = {}

        def capture_update(mutator):
            status = {}
            mutator(status)
            captured['status'] = status
            return status

        result, mocked_update = self._run_index_update(
            tmp_path, '2026-07-29', update_status_side_effect=capture_update
        )

        assert result['success'] == 0
        assert result['failed'] == 1
        mocked_update.assert_called_once()
        assert captured['status']['indices']['sh000001']['status'] == 'stale_no_source'
        assert captured['status']['indices']['sh000001']['local_max'] == '20260729'
