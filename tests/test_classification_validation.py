"""tests/test_classification_validation.py — 分类校验脚本严格模式测试"""
import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)


def test_validate_board_classification_strict_tags():
    """以 subprocess 运行 validate_board_classification.py --strict-tags，断言 returncode 0 且 stdout 含 PASS"""
    result = subprocess.run(
        [sys.executable, 'scripts/validate_board_classification.py', '--strict-tags'],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
    )
    assert result.returncode == 0, (
        f"validate_board_classification --strict-tags 应返回 0\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[PASS]" in result.stdout, (
        f"strict-tags 验证通过时 stdout 应包含 [PASS]\n"
        f"stdout:\n{result.stdout}"
    )


def test_validate_board_classification_normal_report_ascii():
    """普通 validate（无 --strict-tags）输出 ASCII 主报告不乱码，检查关键英文字段"""
    result = subprocess.run(
        [sys.executable, 'scripts/validate_board_classification.py'],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
    )
    # 普通模式不应因编码问题崩溃，returncode 可为 0 或 1（取决于数据质量）
    # 但 stdout 必须包含 ASCII 主报告的关键字段
    assert result.returncode in (0, 1), (
        f"普通 validate 应正常退出（returncode 0 或 1），实际 {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # 关键英文字段检查（ASCII 主报告不乱码）
    expected_fields = [
        "board classification v5.0 validation report",
        "total boards:",
        "primary categories:",
        "secondary categories:",
        "tag total:",
        "single-use tags:",
    ]
    for field in expected_fields:
        assert field in result.stdout, (
            f"ASCII 主报告缺少关键字段 '{field}'\n"
            f"stdout:\n{result.stdout}"
        )
    # 确保没有 UnicodeDecodeError 等编码异常出现在 stderr
    assert "UnicodeDecodeError" not in result.stderr, (
        f"不应出现 UnicodeDecodeError\nstderr:\n{result.stderr}"
    )
