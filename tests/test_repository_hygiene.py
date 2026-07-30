"""Repository hygiene tests.

Guarantees that the working tree stays free of:
- __pycache__ / .pyc
- Hard-coded secrets
- Data / runtime artefacts tracked by git
"""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TRACKED_PATTERNS = (
    re.compile(r"(^|/)__pycache__/"),
    re.compile(r"\.pyc$"),
    re.compile(r"^kline\.db$"),
    re.compile(r"^static/index-local.*\.html$"),
    re.compile(r"^static/_archive/"),
    re.compile(r"\.bak$"),
    re.compile(r"^scripts/_archive/"),
    re.compile(r"^scripts/_probe/"),
    re.compile(r"^scripts/__pycache__/"),
    re.compile(r"^_all_boards\.txt$"),
    # 行情数据缓存 CSV（由数据更新管线生成，非源码）
    re.compile(r"^data/行业板块K线数据/.*\.csv$"),
    re.compile(r"^data/概念板块K线数据/.*\.csv$"),
    re.compile(r"^data/个股数据/.*\.csv$"),
    re.compile(r"^data/个股K线缓存/"),
    re.compile(r"^data/港股K线缓存/"),
    # 构建产物
    re.compile(r"^static/search_index\.json$"),
    # 运行时状态 JSON
    re.compile(r"(^|/)update_status\.json$"),
    re.compile(r"^data/update_logs/"),
    re.compile(r"^scripts/data/update_status\.json$"),
    # WorkBuddy 内部
    re.compile(r"(^|/)\.workbuddy/"),
    # 运行日志
    re.compile(r"\.log$"),
    # 真实配置（只跟踪 .example）
    re.compile(r"^qmt-http-server/deploy/server_config\.json$"),
)

FORBIDDEN_PRESENT_FILES = (
    ROOT / "kline.db",
)

TOKEN_LITERALS = (
    "cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590",
)

BAT_FILE_MAX_LINE = 200


def _tracked_files():
    """Return tracked file paths, using -z to avoid quotePath mangling of non-ASCII names."""
    out = subprocess.check_output(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z"],
        cwd=ROOT,
    )
    return [
        p.decode("utf-8", errors="replace").strip().replace("\\", "/")
        for p in out.split(b"\0")
        if p.strip()
    ]


def _all_files_in_working_tree():
    """Return untracked file paths, using -z to avoid quotePath mangling of non-ASCII names."""
    out = subprocess.check_output(
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    return [
        p.decode("utf-8", errors="replace").strip().replace("\\", "/")
        for p in out.split(b"\0")
        if p.strip()
    ]


def test_forbidden_legacy_files_are_not_tracked():
    offenders = []
    for path in _tracked_files():
        if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS):
            offenders.append(path)
    assert offenders == [], f"Forbidden tracked files: {offenders}"


def test_forbidden_files_not_present():
    offenders = [str(p) for p in FORBIDDEN_PRESENT_FILES if p.exists()]
    assert offenders == [], f"Forbidden files present on disk: {offenders}"


def test_hardcoded_tushare_token_is_not_tracked():
    self_rel = str(Path(__file__).relative_to(ROOT)).replace("\\", "/")
    offenders = []
    for rel in _tracked_files():
        if rel == self_rel:
            continue  # this test file contains the token as a literal assertion
        path = ROOT / rel
        if path.suffix.lower() in {".png", ".ico", ".db", ".sqlite"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for token in TOKEN_LITERALS:
            if token in text:
                offenders.append(rel)
                break
    assert offenders == [], f"Files with hardcoded tokens: {offenders}"


def test_hardcoded_token_not_in_untracked_py():
    """Untracked .py files must also be clean of tokens (exclude this test file)."""
    offenders = []
    self_file = Path(__file__).resolve()
    for rel in _all_files_in_working_tree():
        if not rel.endswith(".py"):
            continue
        path = (ROOT / rel).resolve()
        if path == self_file:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for token in TOKEN_LITERALS:
            if token in text:
                offenders.append(rel)
                break
    assert offenders == [], f"Untracked .py files with hardcoded tokens: {offenders}"


def test_no_new_bak_files():
    """bak files should not appear on disk, anywhere."""
    bak_files = list(ROOT.rglob("*.bak"))
    assert bak_files == [], f".bak files found: {[str(f) for f in bak_files]}"


def test_bat_files_not_tracked():
    """Batch files are convenience scripts, not production code."""
    tracked_bat = [p for p in _tracked_files() if p.endswith(".bat")]
    assert tracked_bat == [], f".bat files tracked: {tracked_bat}"


def test_init_files_exist_in_packages():
    """Python packages must have __init__.py so imports are explicit."""
    expected_packages = ["data", "services", "api", "core"]
    missing = []
    for pkg in expected_packages:
        init_file = ROOT / pkg / "__init__.py"
        if not init_file.exists():
            missing.append(pkg)
    assert missing == [], f"Packages missing __init__.py: {missing}"


def test_no_runtime_data_csv_tracked():
    """行情 CSV 缓存不应被 Git 跟踪（由数据更新管线生成）。"""
    offenders = [
        p for p in _tracked_files()
        if p.endswith(".csv")
        and (
            p.startswith("data/行业板块K线数据/")
            or p.startswith("data/概念板块K线数据/")
            or p.startswith("data/个股数据/")
        )
    ]
    assert offenders == [], f"Runtime CSV files tracked: {offenders}"


def test_no_search_index_tracked():
    """static/search_index.json 是构建产物，不应被跟踪。"""
    offenders = [p for p in _tracked_files() if p == "static/search_index.json"]
    assert offenders == [], f"Search index tracked: {offenders}"


def test_no_workbuddy_tracked():
    """WorkBuddy 内部文件不应被跟踪。"""
    offenders = [p for p in _tracked_files() if ".workbuddy/" in p]
    assert offenders == [], f"WorkBuddy files tracked: {offenders}"


def test_no_runtime_status_json_tracked():
    """运行时状态 JSON（update_status.json）不应被跟踪。"""
    offenders = [
        p for p in _tracked_files()
        if p.endswith("update_status.json")
    ]
    assert offenders == [], f"Runtime status JSON tracked: {offenders}"


def test_no_real_server_config_tracked():
    """qmt-http-server 真实配置（server_config.json）不应被跟踪，只允许 .example。"""
    offenders = [
        p for p in _tracked_files()
        if p == "qmt-http-server/deploy/server_config.json"
    ]
    assert offenders == [], f"Real server config tracked: {offenders}"


def test_no_log_files_tracked():
    """运行日志不应被跟踪。"""
    offenders = [p for p in _tracked_files() if p.endswith(".log")]
    assert offenders == [], f"Log files tracked: {offenders}"
