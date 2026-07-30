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
    # .env 若误写 HTTP_PROXY=7688，加载后强制清掉（国内行情永远直连）
    try:
        force_direct_network()
    except Exception:
        pass
    return applied


_DIRECT_PATCHED = False


def force_direct_network(for_domestic: bool = True) -> dict:
    """国内行情/Tushare **必须直连，不走 7688 VPN 代理**。

    交接约定：WorkBuddy/面板访问国内行情 proxySupport:off。
    - 清除进程内 HTTP(S)_PROXY / ALL_PROXY
    - NO_PROXY=*
    - 禁用 requests/urllib 读取系统代理（含 Windows 注册表代理）

    返回被清理的环境变量名列表。
    """
    global _DIRECT_PATCHED
    cleared = {}
    keys = (
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
        'all_proxy', 'FTP_PROXY', 'ftp_proxy',
    )
    for k in keys:
        if k in os.environ and os.environ.get(k):
            cleared[k] = os.environ.get(k)
            del os.environ[k]
    # * = 全部主机不走代理
    os.environ['NO_PROXY'] = '*'
    os.environ['no_proxy'] = '*'
    if cleared:
        logger.info(f"[env] 已清除代理（国内直连，不走 7688）: {list(cleared.keys())}")

    if not _DIRECT_PATCHED:
        # urllib：空 ProxyHandler = 强制直连
        try:
            import urllib.request
            _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            urllib.request.install_opener(_opener)
        except Exception as e:
            logger.debug(f"[env] urllib 直连 patch 跳过: {e}")
        # requests：Session 默认 trust_env 会读系统代理
        try:
            import requests
            _orig_init = requests.Session.__init__

            def _session_init_no_proxy(self, *a, **kw):
                _orig_init(self, *a, **kw)
                self.trust_env = False
                self.proxies = {}

            requests.Session.__init__ = _session_init_no_proxy  # type: ignore
            _DIRECT_PATCHED = True
            logger.info("[env] requests Session 已强制 trust_env=False（不走系统代理）")
        except Exception as e:
            logger.debug(f"[env] requests 直连 patch 跳过: {e}")
    return cleared


def ensure_tushare_token() -> bool:
    """确保 TUSHARE_TOKEN 可用；必要时重初始化 data_loader._tushare_pro。"""
    load_env_files()
    force_direct_network()
    token = (os.environ.get('TUSHARE_TOKEN') or '').strip()
    if not token:
        logger.warning("[env] TUSHARE_TOKEN 仍未设置（请写 .env 或系统环境变量）")
        return False
    try:
        import tushare as ts
        import data_loader as dl
        if getattr(dl, '_tushare_pro', None) is None:
            ts.set_token(token)
            dl._tushare_pro = ts.pro_api()
            logger.info("[env] data_loader._tushare_pro 已延迟初始化")
        return True
    except Exception as e:
        logger.warning(f"[env] Tushare 初始化失败: {e}")
        return False
