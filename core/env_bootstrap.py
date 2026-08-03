"""
env_bootstrap.py — 启动时加载本地环境变量（不提交密钥到代码）

优先级（后者不覆盖已有 os.environ）：
1. 进程环境变量（最高，已存在则保留）
2. 项目根目录 `.env`（KEY=VALUE，# 注释）
3. 可选：用户级 `~/.board-app.env`（跨项目副本）

设计原则：
- 不把 token 写进 py 源码
- 若环境已设置 TUSHARE_TOKEN，则不覆盖
- 缺省文件不存在时安静跳过
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger('env_bootstrap')

_BASE = Path(__file__).resolve().parent.parent
_CANDIDATES = (
    _BASE / '.env',
    Path.home() / '.board-app.env',
)

def _parse_env_line(line: str):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    if line.startswith('export '):
        line = line[7:].strip()
    if '=' not in line:
        return None
    key, _, val = line.partition('=')
    key = key.strip()
    val = val.strip().strip('"').strip("'")
    if not key:
        return None
    return key, val


def load_env_files(force: bool = False) -> dict:
    """
    从候选 .env 文件注入环境变量。
    force=False 时：仅填充当前 os.environ 中缺失的 key。
    返回实际新写入的 {key: '***'} 摘要（值脱敏）。

    优先使用 python-dotenv（若已安装），失败则回退手写解析。
    """
    applied = {}

    # 优先 dotenv：与 Flask 的 .env 提示一致，支持多行/转义等
    try:
        from dotenv import dotenv_values
        for path in _CANDIDATES:
            if not path.is_file():
                continue
            try:
                values = dotenv_values(path) or {}
            except Exception as e:
                logger.warning(f"[env] dotenv 读取 {path} 失败: {e}")
                continue
            count = 0
            for key, val in values.items():
                if not key or val is None:
                    continue
                val = str(val).strip()
                if not val:
                    continue
                if not force and os.environ.get(key):
                    continue
                os.environ[key] = val
                applied[key] = (
                    '***' if any(s in key.upper() for s in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD'))
                    else val
                )
                count += 1
            if count:
                logger.info(f"[env] 自 {path.name} 加载 {count} 项 (dotenv)")
        if applied:
            return applied
    except ImportError:
        pass

    # 回退：手写 KEY=VALUE 解析（无 python-dotenv 时仍可用）
    for path in _CANDIDATES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"[env] 读取 {path} 失败: {e}")
            continue
        count = 0
        for line in text.splitlines():
            parsed = _parse_env_line(line)
            if not parsed:
                continue
            key, val = parsed
            if not force and os.environ.get(key):
                continue
            if not val:
                continue
            os.environ[key] = val
            applied[key] = '***' if any(s in key.upper() for s in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD')) else val
            count += 1
        if count:
            logger.info(f"[env] 自 {path.name} 加载 {count} 项")
    if not applied:
        logger.debug("[env] 未加载新环境变量（文件缺失或均已存在）")
    return applied


def force_direct_network(for_domestic: bool = True) -> dict:
    """Deprecated no-op retained for compatibility with old callers.

    Direct routing is now scoped to ``data_loader.get_tushare_pro``. This
    compatibility function intentionally does not change proxy environment
    variables, urllib's default opener, or ``requests.Session`` globally.
    """
    del for_domestic
    return {}


def ensure_tushare_token() -> bool:
    """加载并校验 TUSHARE_TOKEN，然后交给 data_loader 的唯一工厂。"""
    load_env_files()
    token = (os.environ.get('TUSHARE_TOKEN') or '').strip()
    if not token:
        logger.warning("[env] TUSHARE_TOKEN 仍未设置（请写 .env 或系统环境变量）")
        return False
    try:
        from data_loader import get_tushare_pro
        client = get_tushare_pro()
        if client is None:
            logger.warning("[env] Tushare 客户端初始化失败")
            return False
        return True
    except Exception as e:
        logger.warning(f"[env] Tushare 初始化失败: {e}")
        return False
