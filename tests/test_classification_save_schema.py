"""tests/test_classification_save_schema.py — 分类保存 schema 校验测试

通过 Flask test_client POST /api/classification/save 验证，
monkeypatch STATIC_DIR 到 tmp_path，避免真实覆盖用户 saved 文件。
覆盖合法最小 v5、tags 为空、primary/secondary 与容器不一致等场景。
同时覆盖保存后 build_index_json / clear_cache 调用、write token 保护行为。
"""
import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)


@pytest.fixture(scope='module')
def client():
    """创建 Flask test_client，并将 STATIC_DIR 重定向到临时目录。

    注意：直接 import api.system_routes 会触发循环导入，
    因此先导入 app（它通过 register_routes 间接加载 system_routes），
    再从 sys.modules 取出已初始化模块进行 STATIC_DIR patch。
    """
    # 先触发 app 导入，使 api.system_routes 正确初始化并进入 sys.modules
    from app import app  # noqa: F401
    import api.system_routes as sr
    from core import config as core_config

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        orig_config_static = core_config.STATIC_DIR
        orig_sr_static = sr.STATIC_DIR
        core_config.STATIC_DIR = tmp_path
        sr.STATIC_DIR = tmp_path
        try:
            with app.test_client() as c:
                yield c
        finally:
            core_config.STATIC_DIR = orig_config_static
            sr.STATIC_DIR = orig_sr_static


def _minimal_valid_payload():
    """构造合法最小 v5 payload"""
    return {
        'version': '5.0',
        'updated_at': '2026-07-24',
        'taxonomy': {'schema': 'industry_tree_with_tags'},
        'categories': [
            {
                'name': 'AI 与数字科技',
                'subcategories': [
                    {
                        'name': 'AI 模型与应用',
                        'boards': [
                            {
                                'code': 'BK0800',
                                'name': '人工智能',
                                'type': 'concept',
                                'primary_category': 'AI 与数字科技',
                                'secondary_category': 'AI 模型与应用',
                                'tags': ['AI科技', 'AI应用', '概念', 'AI', 'AIGC'],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _post(client, payload):
    return client.post(
        '/api/classification/save',
        data=json.dumps(payload),
        content_type='application/json',
    )


def test_valid_minimal_v5_returns_200(client):
    """合法最小 v5 payload 返回 200"""
    resp = _post(client, _minimal_valid_payload())
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.data.decode()}"


def test_tags_empty_returns_400(client):
    """tags=[] 返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['tags'] = []
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_tags_single_returns_400(client):
    """tags 只有 1 个（<2）返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['tags'] = ['AI科技']
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_tags_duplicate_returns_400(client):
    """tags 有重复返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['tags'] = [
        'AI科技', 'AI科技', '概念'
    ]
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_tags_too_many_returns_400(client):
    """tags 超过 6 个返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['tags'] = [
        'AI科技', 'AI应用', '概念', 'AI', 'AIGC', '大模型', '多模态'
    ]
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_primary_mismatch_returns_400(client):
    """board.primary_category 与 cat.name 不一致返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['primary_category'] = '其他分类'
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_secondary_mismatch_returns_400(client):
    """board.secondary_category 与 sub.name 不一致返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['secondary_category'] = '其他子类'
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_type_invalid_returns_400(client):
    """type 不是 industry/concept 返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['type'] = 'foo'
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_missing_required_field_returns_400(client):
    """缺少必备字段（如 code）返回 400"""
    payload = _minimal_valid_payload()
    del payload['categories'][0]['subcategories'][0]['boards'][0]['code']
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_version_mismatch_returns_400(client):
    """version 不是 5.0 返回 400"""
    payload = _minimal_valid_payload()
    payload['version'] = '4.1'
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_tags_with_empty_string_returns_400(client):
    """tags 中包含空字符串返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'][0]['tags'] = [
        'AI科技', '  ', '概念'
    ]
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_multiple_boards_all_valid_returns_200(client):
    """多个 board 全部合法时返回 200"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'].append({
        'code': 'BK0809',
        'name': 'AI智能体',
        'type': 'concept',
        'primary_category': 'AI 与数字科技',
        'secondary_category': 'AI 模型与应用',
        'tags': ['AI科技', 'AI应用', '概念', 'AI'],
    })
    resp = _post(client, payload)
    assert resp.status_code == 200


def test_multiple_boards_one_invalid_returns_400(client):
    """多个 board 中有 1 个不合法时返回 400"""
    payload = _minimal_valid_payload()
    payload['categories'][0]['subcategories'][0]['boards'].append({
        'code': 'BK0809',
        'name': 'AI智能体',
        'type': 'concept',
        'primary_category': 'AI 与数字科技',
        'secondary_category': 'AI 模型与应用',
        'tags': ['AI科技'],  # 仅 1 个 tag，不合法
    })
    resp = _post(client, payload)
    assert resp.status_code == 400


def test_saved_file_written_to_tmp_not_real(client):
    """验证保存成功且 ok=True"""
    payload = _minimal_valid_payload()
    resp = _post(client, payload)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('ok') is True


# ---- 保存后索引重建 / 缓存清理测试 ----

def test_save_success_triggers_build_index_json_and_clear_cache(client):
    """保存成功后应调用 build_index_json 和 clear_cache"""
    payload = _minimal_valid_payload()

    mock_build_index = MagicMock(return_value={})
    mock_svc = MagicMock()

    with patch('api.system_routes.build_index_json', mock_build_index), \
         patch('api.system_routes.get_search_service', return_value=mock_svc):
        resp = _post(client, payload)

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('ok') is True
    # build_index_json 应被调用
    mock_build_index.assert_called_once()
    # clear_cache 应被调用
    mock_svc.clear_cache.assert_called_once()
    # 返回结果中应包含 search_index_rebuilt=true
    assert data.get('search_index_rebuilt') is True
    assert data.get('search_index_error') is None


def test_save_with_build_index_failure_still_succeeds(client):
    """build_index_json 失败不应影响保存，但应返回 search_index_rebuilt=false 和 error"""
    payload = _minimal_valid_payload()

    mock_build_index = MagicMock(side_effect=Exception("QMT not available"))
    mock_svc = MagicMock()

    with patch('api.system_routes.build_index_json', mock_build_index), \
         patch('api.system_routes.get_search_service', return_value=mock_svc):
        resp = _post(client, payload)

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('ok') is True
    assert data.get('search_index_rebuilt') is False
    assert data.get('search_index_error') == "QMT not available"
    # clear_cache 仍应被调用（即使 build_index 失败）
    mock_svc.clear_cache.assert_called_once()


def test_save_with_clear_cache_failure_still_succeeds(client):
    """clear_cache 失败不应影响保存"""
    payload = _minimal_valid_payload()

    mock_build_index = MagicMock(return_value={})
    mock_svc = MagicMock()
    mock_svc.clear_cache.side_effect = Exception("cache locked")

    with patch('api.system_routes.build_index_json', mock_build_index), \
         patch('api.system_routes.get_search_service', return_value=mock_svc):
        resp = _post(client, payload)

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('ok') is True
    assert data.get('search_index_rebuilt') is True


def test_save_with_service_without_clear_cache_still_succeeds(client):
    """SearchService 没有 clear_cache 方法时应兼容处理"""
    payload = _minimal_valid_payload()

    mock_build_index = MagicMock(return_value={})
    mock_svc = MagicMock(spec=[])  # 无 clear_cache 属性

    with patch('api.system_routes.build_index_json', mock_build_index), \
         patch('api.system_routes.get_search_service', return_value=mock_svc):
        resp = _post(client, payload)

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('ok') is True


# ---- 非法 payload 仍 400（与索引重建无关） ----

def test_invalid_payload_returns_400_even_with_index_rebuild(client):
    """非法 payload 返回 400，不触发索引重建"""
    payload = _minimal_valid_payload()
    payload['version'] = '4.1'

    mock_build_index = MagicMock(return_value={})
    mock_svc = MagicMock()

    with patch('api.system_routes.build_index_json', mock_build_index), \
         patch('api.system_routes.get_search_service', return_value=mock_svc):
        resp = _post(client, payload)

    assert resp.status_code == 400
    # 非法 payload 不应触发索引重建
    mock_build_index.assert_not_called()
    mock_svc.clear_cache.assert_not_called()


# ---- write token 保护测试 ----

def test_write_protection_no_token_set_allows_request(client):
    """BOARD_APP_WRITE_TOKEN 未设置时不应拦截"""
    # 确保环境变量未设置
    token = os.environ.pop('BOARD_APP_WRITE_TOKEN', None)
    try:
        payload = _minimal_valid_payload()
        mock_build_index = MagicMock(return_value={})
        mock_svc = MagicMock()

        with patch('api.system_routes.build_index_json', mock_build_index), \
             patch('api.system_routes.get_search_service', return_value=mock_svc):
            resp = _post(client, payload)

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get('ok') is True
    finally:
        # 恢复环境变量（如果有）
        if token is not None:
            os.environ['BOARD_APP_WRITE_TOKEN'] = token


def test_write_protection_with_token_local_bypass(client):
    """设置 token 后 localhost 请求应放行"""
    os.environ['BOARD_APP_WRITE_TOKEN'] = 'secret123'
    try:
        payload = _minimal_valid_payload()
        mock_build_index = MagicMock(return_value={})
        mock_svc = MagicMock()

        with patch('api.system_routes.build_index_json', mock_build_index), \
             patch('api.system_routes.get_search_service', return_value=mock_svc):
            # test_client 默认 remote_addr 是 127.0.0.1（werkzeug test client 默认值），视为本地
            resp = _post(client, payload)

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get('ok') is True
    finally:
        os.environ.pop('BOARD_APP_WRITE_TOKEN', None)


def test_write_protection_with_token_remote_without_header_blocked(client):
    """设置 token 后非本地无 header 应 403"""
    os.environ['BOARD_APP_WRITE_TOKEN'] = 'secret123'
    try:
        payload = _minimal_valid_payload()

        with patch('api.system_routes.build_index_json') as mock_build_index, \
             patch('api.system_routes.get_search_service') as mock_get_svc:
            # 模拟远程请求（非本地）
            with patch('api.auth_guard._is_local_request', return_value=False):
                resp = _post(client, payload)

        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert data.get('error') == 'forbidden'
        # 保护生效，索引重建不应被调用
        mock_build_index.assert_not_called()
        mock_get_svc.assert_not_called()
    finally:
        os.environ.pop('BOARD_APP_WRITE_TOKEN', None)


def test_write_protection_with_token_remote_with_correct_header_allowed(client):
    """设置 token 后非本地携带正确 header 应放行"""
    os.environ['BOARD_APP_WRITE_TOKEN'] = 'secret123'
    try:
        payload = _minimal_valid_payload()
        mock_build_index = MagicMock(return_value={})
        mock_svc = MagicMock()

        with patch('api.system_routes.build_index_json', mock_build_index), \
             patch('api.system_routes.get_search_service', return_value=mock_svc), \
             patch('api.auth_guard._is_local_request', return_value=False):
            resp = client.post(
                '/api/classification/save',
                data=json.dumps(payload),
                content_type='application/json',
                headers={'X-Board-App-Token': 'secret123'},
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get('ok') is True
        mock_build_index.assert_called_once()
        mock_svc.clear_cache.assert_called_once()
    finally:
        os.environ.pop('BOARD_APP_WRITE_TOKEN', None)


def test_write_protection_with_token_remote_with_wrong_header_blocked(client):
    """设置 token 后非本地携带错误 header 应 403"""
    os.environ['BOARD_APP_WRITE_TOKEN'] = 'secret123'
    try:
        payload = _minimal_valid_payload()

        with patch('api.system_routes.build_index_json') as mock_build_index, \
             patch('api.system_routes.get_search_service') as mock_get_svc, \
             patch('api.auth_guard._is_local_request', return_value=False):
            resp = client.post(
                '/api/classification/save',
                data=json.dumps(payload),
                content_type='application/json',
                headers={'X-Board-App-Token': 'wrong_token'},
            )

        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert data.get('error') == 'forbidden'
        mock_build_index.assert_not_called()
        mock_get_svc.assert_not_called()
    finally:
        os.environ.pop('BOARD_APP_WRITE_TOKEN', None)
