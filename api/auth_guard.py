"""
api/auth_guard.py — 可选写保护公共 helper

保护逻辑：
- BOARD_APP_WRITE_TOKEN 未设置时不拦截（默认开放）。
- 设置后 localhost/127.0.0.1/::1 直接放行。
- 非本地请求必须携带正确 X-Board-App-Token header。

安全说明：
- X-Forwarded-For 可被客户端伪造，默认不信任。
- 仅当设置 TRUSTED_PROXY_IPS 环境变量且 remote_addr 在可信代理列表内时，
  才读取 X-Forwarded-For 判断原始客户端 IP。
"""
import os
import logging
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger('auth_guard')

WRITE_TOKEN_ENV = 'BOARD_APP_WRITE_TOKEN'
WRITE_TOKEN_HEADER = 'X-Board-App-Token'

_LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', '0:0:0:0:0:0:0:1'}


def _get_trusted_proxies():
    """从 TRUSTED_PROXY_IPS 环境变量读取可信代理 IP 列表。"""
    raw = os.environ.get('TRUSTED_PROXY_IPS', '')
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(',') if ip.strip()}


def _is_local_request(req) -> bool:
    """判断请求是否来自本机。

    仅基于 request.remote_addr 判断 localhost/127.0.0.1/::1。
    除非设置 TRUSTED_PROXY_IPS 且 remote_addr 在可信代理内，才读取 XFF。
    """
    remote = req.remote_addr or ''
    if remote in _LOCAL_HOSTS:
        return True
    # IPv6 可能带方括号
    if remote.startswith('[') and remote.endswith(']'):
        inner = remote[1:-1]
        if inner in _LOCAL_HOSTS:
            return True
    # 仅在明确配置可信代理时才信任 X-Forwarded-For
    trusted = _get_trusted_proxies()
    if trusted and remote in trusted:
        fwd = req.headers.get('X-Forwarded-For', '')
        if fwd:
            first = fwd.split(',')[0].strip()
            if first in _LOCAL_HOSTS:
                return True
    return False


def write_protected(f):
    """POST 写接口装饰器：按 BOARD_APP_WRITE_TOKEN 决定是否拦截。"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = os.environ.get(WRITE_TOKEN_ENV)
        # 未设置 token → 不拦截
        if not token:
            return f(*args, **kwargs)

        req = request
        # 本地请求直接放行
        if _is_local_request(req):
            return f(*args, **kwargs)

        # 非本地：校验 header
        provided = req.headers.get(WRITE_TOKEN_HEADER, '')
        if provided != token:
            logger.warning(
                "write_protected 拒绝: remote=%s 未提供正确 %s",
                req.remote_addr, WRITE_TOKEN_HEADER,
            )
            return jsonify({'error': 'forbidden', 'message': '缺少正确的写保护 token'}), 403

        return f(*args, **kwargs)

    return wrapper
