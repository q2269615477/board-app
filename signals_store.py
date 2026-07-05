"""
signals_store.py - 线程安全的信号持久化（原子写 + filelock）
"""
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_SIGNALS_FILE = Path(__file__).resolve().parent / 'signals.json'
_MAX_SIGNALS_PER_BOARD = 200


def load_signals() -> dict:
    if not _SIGNALS_FILE.exists():
        return {}
    try:
        with open(_SIGNALS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[警告] 信号文件损坏，已备份并重置: {e}")
        backup_path = Path(__file__).resolve().parent / f'signals_backup_{int(time.time())}.json'
        _SIGNALS_FILE.rename(backup_path)
        return {}


def save_signals(signals: dict):
    """原子写入：先写临时文件再 rename 替换"""
    with _lock:
        for board_code in list(signals.keys()):
            for skill in list(signals[board_code].keys()):
                if len(signals[board_code][skill]) > _MAX_SIGNALS_PER_BOARD:
                    signals[board_code][skill] = signals[board_code][skill][-_MAX_SIGNALS_PER_BOARD:]

        tmp_path = _SIGNALS_FILE.with_suffix('.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(signals, f, ensure_ascii=False, indent=2)
            tmp_path.replace(_SIGNALS_FILE)
        except IOError as e:
            print(f"[错误] 信号文件写入失败: {e}")
            if tmp_path.exists():
                tmp_path.unlink()


def append_signals(board_code: str, skill: str, new_signals: list, replace: bool = False):
    """追加/替换信号"""
    signals = load_signals()
    if board_code not in signals:
        signals[board_code] = {}

    if replace or skill not in signals[board_code]:
        signals[board_code][skill] = new_signals
    else:
        signals[board_code][skill].extend(new_signals)

    save_signals(signals)
