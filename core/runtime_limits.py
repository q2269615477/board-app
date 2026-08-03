"""Process resource defaults applied before importing numerical libraries."""
from __future__ import annotations

import ctypes
import os
from collections.abc import MutableMapping
from ctypes import wintypes


_NATIVE_THREAD_LIMITS = (
    'OMP_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'NUMEXPR_MAX_THREADS',
    'BLIS_NUM_THREADS',
)


def configure_native_thread_limits(
    env: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Default numerical runtimes to one worker without overriding user choices."""
    target = os.environ if env is None else env
    for name in _NATIVE_THREAD_LIMITS:
        target.setdefault(name, '1')
    return {name: target[name] for name in _NATIVE_THREAD_LIMITS}


def lower_windows_process_priority() -> bool:
    """Let interactive desktop applications win CPU contention on Windows."""
    if os.name != 'nt' or os.environ.get('BOARD_APP_LOW_PRIORITY', '1') != '1':
        return False
    try:
        below_normal_priority_class = 0x00004000
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        return bool(kernel32.SetPriorityClass(handle, below_normal_priority_class))
    except (AttributeError, OSError):
        return False


def configure_runtime_limits() -> dict[str, object]:
    """Apply all lightweight limits before Flask imports pandas/numpy."""
    return {
        'native_threads': configure_native_thread_limits(),
        'low_priority': lower_windows_process_priority(),
    }
