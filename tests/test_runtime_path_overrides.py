"""tests/test_runtime_path_overrides.py — 浏览器测试运行时路径注入

验证（均在隔离子进程中、import 之前设置环境变量）：
- BOARD_APP_DATA_DIR 生效后 core.config.DATA_DIR 指向注入路径；
- BOARD_APP_SEARCH_INDEX_PATH 生效后 SearchService.index_path 指向注入路径；
- 未设置（或为空）时默认路径保持不变。

core.config 的 DATA_DIR 在 import 时固化，同一 pytest 进程内
后设 env + reload 会污染共享模块状态，因此统一用子进程证明
「启动前设置 env」的完整行为。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 与 tests/conftest.py 一致：子进程内避免启动重量级生命周期
_SUBPROCESS_GUARD_ENV = {
    'BOARD_APP_AUTO_BOOTSTRAP': '0',
    'QMT_ENABLED': '0',
    'QMT_AUTO_START': '0',
}

_OVERRIDE_KEYS = ('BOARD_APP_DATA_DIR', 'BOARD_APP_SEARCH_INDEX_PATH')


def _run_py(code: str, env_extra: dict) -> str:
    """在隔离的 Python 子进程中执行 code，返回去除首尾空白后的 stdout。"""
    env = os.environ.copy()
    for key in _OVERRIDE_KEYS:
        env.pop(key, None)
    env.update(_SUBPROCESS_GUARD_ENV)
    env.update(env_extra)
    env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    assert result.returncode == 0, (
        f"子进程执行失败:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return result.stdout.strip()


class TestDataDirOverride:
    """core.config.DATA_DIR 的 BOARD_APP_DATA_DIR 注入"""

    def test_env_override_applies_before_import(self, tmp_path):
        """启动前设置 BOARD_APP_DATA_DIR，import 后 DATA_DIR 指向注入路径。"""
        target = tmp_path / 'runtime-data'
        out = _run_py(
            "import core.config as cfg; "
            "print(cfg.DATA_DIR); print(cfg.Config.DATA_DIR)",
            {'BOARD_APP_DATA_DIR': str(target)},
        )
        data_dir, config_data_dir = out.splitlines()
        assert Path(data_dir) == target
        assert Path(config_data_dir) == target

    def test_update_manager_uses_overridden_market_database(self, tmp_path):
        """日更调度与行情加载器必须检查同一个注入数据库。"""
        target = tmp_path / 'runtime-data'
        out = _run_py(
            "from pathlib import Path; "
            "import data_update_manager as manager; "
            "from core.config import SQLITE_PATH; "
            "print(Path(manager._LEDGER_DB)); print(SQLITE_PATH)",
            {'BOARD_APP_DATA_DIR': str(target)},
        )
        manager_db, config_db = out.splitlines()
        assert Path(manager_db) == target / 'kline.db'
        assert Path(manager_db) == Path(config_db)

    def test_default_unchanged_without_env(self):
        """未设置环境变量时，DATA_DIR 保持 BASE_DIR/data 不变。"""
        out = _run_py(
            "import core.config as cfg; "
            "print(cfg.DATA_DIR); print(cfg.BASE_DIR)",
            {},
        )
        data_dir, base_dir = out.splitlines()
        assert Path(data_dir) == Path(base_dir) / 'data'

    def test_empty_env_falls_back_to_default(self):
        """BOARD_APP_DATA_DIR 为空字符串时回退默认路径。"""
        out = _run_py(
            "import core.config as cfg; "
            "print(cfg.DATA_DIR); print(cfg.BASE_DIR)",
            {'BOARD_APP_DATA_DIR': ''},
        )
        data_dir, base_dir = out.splitlines()
        assert Path(data_dir) == Path(base_dir) / 'data'


class TestSearchIndexPathOverride:
    """SearchService 默认索引路径的 BOARD_APP_SEARCH_INDEX_PATH 注入"""

    def test_env_override_applies_before_import(self, tmp_path):
        """启动前设置 BOARD_APP_SEARCH_INDEX_PATH，index_path 指向注入路径。"""
        target = tmp_path / 'runtime' / 'search_index.json'
        out = _run_py(
            "from services.search_service import SearchService; "
            "print(SearchService().index_path)",
            {'BOARD_APP_SEARCH_INDEX_PATH': str(target)},
        )
        assert Path(out) == target

    def test_default_unchanged_without_env(self):
        """未设置环境变量时，index_path 保持 static/search_index.json 不变。"""
        out = _run_py(
            "from services.search_service import SearchService; "
            "print(SearchService().index_path)",
            {},
        )
        assert Path(out) == ROOT / 'static' / 'search_index.json'

    def test_empty_env_falls_back_to_default(self):
        """BOARD_APP_SEARCH_INDEX_PATH 为空字符串时回退默认路径。"""
        out = _run_py(
            "from services.search_service import SearchService; "
            "print(SearchService().index_path)",
            {'BOARD_APP_SEARCH_INDEX_PATH': ''},
        )
        assert Path(out) == ROOT / 'static' / 'search_index.json'


@pytest.fixture(autouse=True)
def _keep_runtime_override_keys_clean():
    """确保 pytest 进程自身不被注入键污染（无副作用清理）。"""
    saved = {key: os.environ.get(key) for key in _OVERRIDE_KEYS}
    for key in _OVERRIDE_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
