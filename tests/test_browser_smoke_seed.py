"""tests/test_browser_smoke_seed.py — 浏览器 smoke 临时运行环境单元测试。

覆盖要求：
1. ``seed_smoke_environment`` 只把文件写到 ``tmp_path``，绝不写项目
   data/ 或 static/；
2. SQLite 复用生产 ``data.sqlite_repo.SqliteRepo``，四个标的日线
   amount>0、日期为工作日、支持周线重采样；
3. 搜索索引包含四个标的，603259 initials 支持 ymkd；
4. env 键值正确；
5. ``browser_smoke_server`` 模块 import 无启动副作用，且
   ``create_app(start_runtime=False)`` + ``FLASK_PORT`` 接线正确。
"""
import json
import os
import socket
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from browser_smoke_seed import SMOKE_SYMBOLS, seed_smoke_environment  # noqa: E402

SMOKE_CODES = {symbol["code"] for symbol in SMOKE_SYMBOLS}
ENV_KEYS = {
    "BOARD_APP_DATA_DIR",
    "BOARD_APP_SEARCH_INDEX_PATH",
    "QMT_ENABLED",
    "QMT_AUTO_START",
    "QMT_STARTUP_HISTORY_SYNC",
    "BOARD_APP_STARTUP_PREWARM",
    "BOARD_APP_AUTO_BOOTSTRAP",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(autouse=True)
def _keep_smoke_env_keys_clean(monkeypatch):
    """测试期间隔离运行时路径/开关 env，避免污染共享 pytest 进程。"""
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_seed_creates_files_only_under_runtime_root(tmp_path):
    """seed 创建的全部文件都位于调用方给定的 runtime_root。"""
    runtime = tmp_path / "runtime"
    env = seed_smoke_environment(runtime)

    created_paths = [path.resolve() for path in runtime.rglob("*") if path.is_file()]
    assert created_paths
    assert all(path.is_relative_to(runtime.resolve()) for path in created_paths)
    created = {path.relative_to(runtime.resolve()) for path in created_paths}
    assert Path("data/kline.db") in created
    assert Path("data/search_index.json") in created

    resolved_runtime = runtime.resolve()
    assert Path(env["BOARD_APP_DATA_DIR"]).is_relative_to(resolved_runtime)
    assert Path(env["BOARD_APP_SEARCH_INDEX_PATH"]).is_relative_to(resolved_runtime)


def test_seed_env_contains_expected_keys_and_values(tmp_path):
    """env dict 包含全部运行开关且取值为 0，路径指向 runtime_root。"""
    runtime = tmp_path / "env-runtime"
    env = seed_smoke_environment(runtime)

    assert ENV_KEYS <= set(env)
    assert env["BOARD_APP_DATA_DIR"] == str((runtime / "data").resolve())
    assert env["BOARD_APP_SEARCH_INDEX_PATH"] == str(
        (runtime / "data" / "search_index.json").resolve()
    )
    for key in (
        "QMT_ENABLED",
        "QMT_AUTO_START",
        "QMT_STARTUP_HISTORY_SYNC",
        "BOARD_APP_STARTUP_PREWARM",
        "BOARD_APP_AUTO_BOOTSTRAP",
    ):
        assert env[key] == "0", f"{key} 应为 0，实际 {env[key]!r}"


def test_seed_db_rows_amounts_and_weekly_resample(tmp_path):
    """数据库行数、amount、工作日与周线重采样都正确。"""
    import sqlite3

    from data.kline_resample import resample_ohlcv
    from data.sqlite_repo import SqliteRepo

    runtime = tmp_path / "db-runtime"
    env = seed_smoke_environment(runtime)
    db_path = Path(env["BOARD_APP_DATA_DIR"]) / "kline.db"

    # 生产 SqliteRepo 直接读写：表结构即生产表结构
    repo = SqliteRepo(db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"kline", "kline_amount", "kline_meta"} <= tables

        for symbol in SMOKE_SYMBOLS:
            code = symbol["code"]
            daily = repo.read_kline(code, "daily")
            assert daily is not None and len(daily) >= 400, f"{code} 日线不足"

            dates = pd.to_datetime(daily["date"])
            assert (dates.dt.dayofweek < 5).all(), f"{code} 存在非工作日"
            assert (daily["close"] > 0).all(), f"{code} close 必须为正"
            assert (daily["volume"] > 0).all(), f"{code} volume 必须为正"
            assert (daily["amount"] > 0).all(), f"{code} amount 必须为正"

            # 与生产读库路径一致：kline 行数 = kline_amount 行数
            kline_rows = conn.execute(
                "SELECT COUNT(*) FROM kline WHERE code=? AND period='daily'", (code,)
            ).fetchone()[0]
            amount_rows = conn.execute(
                "SELECT COUNT(*) FROM kline_amount "
                "WHERE code=? AND period='daily' AND amount>0",
                (code,),
            ).fetchone()[0]
            assert kline_rows == len(daily)
            assert amount_rows == len(daily)

            meta = conn.execute(
                "SELECT rows FROM kline_meta WHERE code=? AND period='daily'", (code,)
            ).fetchone()
            assert meta is not None and meta[0] == len(daily)

            weekly = resample_ohlcv(daily, "weekly")
            assert len(weekly) >= 80, f"{code} 周线重采样不足"
            assert (weekly["amount"] > 0).all(), f"{code} 周线 amount 必须为正"
            assert weekly["date"].is_monotonic_increasing
    finally:
        conn.close()


def test_seed_search_index_contains_symbols_and_ymkd(tmp_path, monkeypatch):
    """搜索索引包含四个标的，且 ymkd / 创新药 / 代码均可检索。"""
    from services.search_service import SearchService

    runtime = tmp_path / "idx-runtime"
    env = seed_smoke_environment(runtime)
    index_path = Path(env["BOARD_APP_SEARCH_INDEX_PATH"])

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    items = payload["items"]
    assert set(items) == SMOKE_CODES
    for symbol in SMOKE_SYMBOLS:
        entry = items[symbol["code"]]
        assert entry["name"] == symbol["name"]
        assert entry["type"] == symbol["type"]
        assert entry["category"] == symbol["category"]
        assert entry["initials"] == symbol["initials"]

    monkeypatch.setenv("BOARD_APP_SEARCH_INDEX_PATH", str(index_path))
    service = SearchService()
    service.reload()

    ymkd = service.search("ymkd")
    assert ymkd and ymkd[0]["code"] == "603259"
    assert ymkd[0]["name"] == "药明康德"

    by_name = service.search("创新药")
    assert any(result["code"] == "BK1106" for result in by_name)

    by_code = service.search("sh000001")
    assert by_code and by_code[0]["code"] == "sh000001"


def test_server_module_import_has_no_startup_side_effects(tmp_path):
    """全新解释器 import browser_smoke_server：不 import app、不开端口。"""
    port = _free_port()
    code = """
import os
import socket
import sys

import browser_smoke_server

print("app_in_modules", "app" in sys.modules)
print(
    "has_entrypoints",
    callable(getattr(browser_smoke_server, "create_smoke_app", None))
    and callable(getattr(browser_smoke_server, "main", None)),
)
try:
    sock = socket.create_connection(("127.0.0.1", int(os.environ["FLASK_PORT"])), timeout=1)
except OSError:
    print("listening", False)
else:
    sock.close()
    print("listening", True)
"""
    env = os.environ.copy()
    for key in ENV_KEYS:
        env.pop(key, None)
    env.update(
        {
            "FLASK_PORT": str(port),
            "PYTHONPATH": str(ROOT) + os.pathsep + str(ROOT / "tests"),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, f"子进程失败:\nstdout={result.stdout}\nstderr={result.stderr}"

    lines = dict(line.split(" ", 1) for line in result.stdout.strip().splitlines())
    assert lines["app_in_modules"] == "False", "import 不应连带导入 app"
    assert lines["has_entrypoints"] == "True"
    assert lines["listening"] == "False", "import 不应启动 Flask 服务"


def test_create_smoke_app_passes_start_runtime_false(monkeypatch):
    """create_smoke_app 必须使用 create_app(start_runtime=False)。"""
    import browser_smoke_server

    captured = {}
    fake_app_module = types.ModuleType("app")

    def _fake_create_app(**kwargs):
        captured.update(kwargs)
        return object()

    fake_app_module.create_app = _fake_create_app
    monkeypatch.setitem(sys.modules, "app", fake_app_module)

    assert browser_smoke_server.create_smoke_app() is not None
    assert captured == {"start_runtime": False}


def test_server_main_seeds_env_and_runs_from_flask_port(monkeypatch, tmp_path):
    """main() 先 seed 临时环境，再以 start_runtime=False 从 FLASK_PORT 启动。"""
    import browser_smoke_server
    import browser_smoke_seed

    port = _free_port()
    runtime_root = tmp_path / "server-runtime"
    monkeypatch.setenv("BOARD_APP_SMOKE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("FLASK_PORT", str(port))

    create_calls = {}
    run_calls = []
    fake_app = types.SimpleNamespace(run=lambda **kwargs: run_calls.append(kwargs))
    fake_app_module = types.ModuleType("app")

    def _fake_create_app(**kwargs):
        create_calls.update(kwargs)
        return fake_app

    fake_app_module.create_app = _fake_create_app
    monkeypatch.setitem(sys.modules, "app", fake_app_module)

    fake_config = types.ModuleType("core.config")
    fake_config.FLASK_HOST = "127.0.0.1"
    fake_config.FLASK_PORT = port
    fake_config.DEBUG = False
    monkeypatch.setitem(sys.modules, "core.config", fake_config)

    seed_roots = []

    def _fake_seed(root):
        seed_roots.append(Path(root))
        return {
            "BOARD_APP_DATA_DIR": str(Path(root) / "data"),
            "BOARD_APP_SEARCH_INDEX_PATH": str(Path(root) / "data" / "search_index.json"),
            "QMT_ENABLED": "0",
            "QMT_AUTO_START": "0",
            "QMT_STARTUP_HISTORY_SYNC": "0",
            "BOARD_APP_STARTUP_PREWARM": "0",
            "BOARD_APP_AUTO_BOOTSTRAP": "0",
        }

    monkeypatch.setattr(browser_smoke_seed, "seed_smoke_environment", _fake_seed)

    browser_smoke_server.main()

    assert seed_roots == [runtime_root]
    assert os.environ["QMT_ENABLED"] == "0"
    assert os.environ["BOARD_APP_AUTO_BOOTSTRAP"] == "0"
    assert os.environ["BOARD_APP_DATA_DIR"] == str(runtime_root / "data")
    assert create_calls == {"start_runtime": False}
    assert run_calls and run_calls[0]["port"] == port
    assert run_calls[0]["host"] == "127.0.0.1"
    assert run_calls[0]["use_reloader"] is False
