"""
build_search_index.py - 构建全市场搜索索引
从 QMT 获取股票+指数+板块数据，计算拼音首字母，输出 JSON
"""
import json
import time
import logging
import re
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[索引] %(message)s')
logger = logging.getLogger('search_index')

BASE_DIR = Path(__file__).parent
INDEX_FILE = BASE_DIR / 'static' / 'search_index.json'

# 已知的指数代码映射（面板内部代码 → QMT代码）
KNOWN_INDICES = {
    'sh000001': ('上证指数', '000001.SH', '指数'),
    'sz399006': ('创业板指', '399006.SZ', '指数'),
    'sh000688': ('科创50', '000688.SH', '指数'),
    'sh000300': ('沪深300', '000300.SH', '指数'),
    'sh000016': ('上证50', '000016.SH', '指数'),
    'sh000852': ('中证1000', '000852.SH', '指数'),
    'sh000853': ('中证2000', '000853.SH', '指数'),
    'sh000985': ('中证全指', '000985.SH', '指数'),
    'HSI': ('恒生指数', 'HSI.HK', '港股指数'),
    'HSTECH': ('恒生科技', 'HSTECH.HK', '港股指数'),
}

_CHAR_MAP = None


def _load_char_map() -> dict:
    """Load the local Chinese initial map used by the browser.

    The pinyin_helper.js file uses single-quoted JS object syntax, which is
    not valid JSON. We parse it with a regex that extracts individual
    'char': 'initial' pairs instead of relying on json.loads.
    """
    global _CHAR_MAP
    if _CHAR_MAP is not None:
        return _CHAR_MAP
    helper = BASE_DIR / 'static' / 'js' / 'pinyin_helper.js'
    try:
        text = helper.read_text(encoding='utf-8')
        # Extract the object block first
        m = re.search(
            r'(?:_CHAR_MAP|FALLBACK_INITIALS)\s*=\s*(\{[^}]+\})',
            text, re.S,
        )
        if not m:
            _CHAR_MAP = {}
            return _CHAR_MAP
        block = m.group(1)
        # Parse 'char': 'letter' pairs (single or double quoted keys/values)
        pairs = re.findall(
            r"""['"](.{1,4})['"]\s*:\s*['"]([a-zA-Z])['"]""",
            block,
        )
        _CHAR_MAP = {k: v for k, v in pairs}
    except Exception:
        _CHAR_MAP = {}
    return _CHAR_MAP


def compute_initials(name: str) -> list[str]:
    """计算中文拼音首字母"""
    try:
        from pypinyin import lazy_pinyin, Style
        py = lazy_pinyin(name, style=Style.FIRST_LETTER)
        initials = []
        for i, ch in enumerate(name):
            if ord(ch) < 128:  # ASCII
                initials.append(ch.upper())
            else:
                p = py[i] if i < len(py) else ''
                initials.append(p.upper() if p else '')
        return initials
    except Exception:
        char_map = _load_char_map()
        initials = []
        for ch in str(name or ''):
            if ch.isascii() and ch.isalnum():
                initials.append(ch.upper())
            else:
                initials.append(str(char_map.get(ch, '')).upper())
        return initials


def build_stock_index() -> list[dict]:
    """从 QMT 获取全市场股票列表并构建索引"""
    try:
        from xtquant import xtdata
        xtdata.connect(port=58600)  # 58610 已废弃
        xtdata.enable_hello = False
    except Exception as e:
        logger.warning(f"QMT连接失败: {e}")
        return []

    # 获取所有A股列表
    sectors_to_fetch = [
        ('沪深A股', 'stock'),
        ('上证A股', 'stock'),
        ('深证A股', 'stock'),
        ('创业板', 'stock'),
        ('京市A股', 'stock'),
    ]

    seen_codes = set()
    all_stocks = []

    for sector_name, stype in sectors_to_fetch:
        try:
            codes = xtdata.get_stock_list_in_sector(sector_name)
            if not codes:
                continue
            for full_code in codes:
                # 去重
                parts = full_code.split('.')
                code = parts[0]
                if code in seen_codes:
                    continue
                seen_codes.add(code)

                # 获取名称
                try:
                    detail = xtdata.get_instrument_detail(full_code)
                    name = detail.get('InstrumentName', '') if detail else ''
                except Exception:
                    name = ''

                if not name:
                    continue

                # 映射面板代码
                panel_code = code  # A股直接用6位代码

                initials = compute_initials(name)
                all_stocks.append({
                    'code': panel_code,
                    'name': name,
                    'type': 'stock',
                    'category': '个股',
                    'initials': initials,
                    'full_code': full_code,
                })

                if len(all_stocks) % 500 == 0:
                    logger.info(f"  已获取 {len(all_stocks)} 只股票...")

        except Exception as e:
            logger.warning(f"获取板块 {sector_name} 失败: {e}")

    logger.info(f"股票获取完成: 共 {len(all_stocks)} 只")
    return all_stocks


def build_stock_index_from_constituents() -> list[dict]:
    """从本地板块成分股缓存构建个股索引，作为 QMT 不可用时的权威兜底。"""
    stocks = {}
    for filename in ('industry_constituents.json', 'concept_constituents.json'):
        path = BASE_DIR / 'data' / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f"读取 {filename} 失败: {e}")
            continue
        for board in payload.values():
            for item in board.get('cons') or []:
                code = str(item.get('code') or '').strip()
                name = str(item.get('name') or '').strip()
                if len(code) == 6 and name and not name.startswith('个股 '):
                    stocks[code] = name

    result = []
    for code, name in sorted(stocks.items()):
        result.append({
            'code': code,
            'name': name,
            'type': 'stock',
            'category': '个股',
            'initials': compute_initials(name),
            'full_code': code,
        })
    logger.info(f"本地成分股个股索引: {len(result)} 条")
    return result


def build_index_sectors() -> list[dict]:
    """从 QMT 获取全量指数列表"""
    try:
        from xtquant import xtdata
        xtdata.connect(port=58600)  # 58610 已废弃
        xtdata.enable_hello = False
    except Exception as e:
        logger.warning(f"QMT连接失败: {e}")
        return []

    result = []
    seen_names = set()

    # 面板代码映射
    def panel_code(qmt_code: str) -> str:
        parts = qmt_code.split('.')
        code = parts[0]
        exchange = parts[1] if len(parts) > 1 else 'SH'
        if exchange == 'SH':
            return f'sh{code}'
        elif exchange == 'SZ':
            return f'sz{code}'
        elif exchange == 'HK':
            return code  # HSI, HSTECH 保留原样
        return code

    try:
        codes = xtdata.get_stock_list_in_sector('沪深指数')
        if not codes:
            return result
        for full_code in codes:
            try:
                detail = xtdata.get_instrument_detail(full_code)
                name = detail.get('InstrumentName', '') if detail else ''
            except Exception:
                name = ''  # 获取股票详情失败，跳过该代码
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            pcode = panel_code(full_code)
            initials = compute_initials(name)
            result.append({
                'code': pcode,
                'name': name,
                'type': 'index',
                'category': '指数',
                'initials': initials,
                'full_code': full_code,
            })

            if len(result) % 100 == 0:
                logger.info(f"  已获取 {len(result)} 个指数...")

    except Exception as e:
        logger.warning(f"获取指数列表失败: {e}")

    logger.info(f"指数获取完成: 共 {len(result)} 个")
    return result


def _generate_tags(name: str, category: str, board_type: str) -> list[str]:
    """根据板块名称和分类自动生成标签列表"""
    tags = []
    # 板块名称本身作为标签
    if name:
        tags.append(name)
    # 分类名作为标签
    if category:
        tags.append(category)
    # 从名称中提取子关键词（去掉常见后缀后的核心词）
    for suffix in ('概念', 'Ⅱ', 'Ⅲ', '概念Ⅱ', '概念Ⅲ'):
        name = name.replace(suffix, '')
    # 按常见分隔符拆分
    for part in [p.strip() for p in name.replace('（', '(').replace('）', ')').split('/') if p.strip()]:
        if part and part not in tags:
            tags.append(part)
    # AI 相关板块添加通用标签
    upper_name = name.upper()
    if 'AI' in upper_name or '人工智能' in name:
        for t in ('AI', '科技', 'AI科技'):
            if t not in tags:
                tags.append(t)
    # 半导体相关
    if '半导体' in name or '芯片' in name:
        if '半导体' not in tags:
            tags.append('半导体')
    return tags


def _iter_classification_boards(nodes):
    """Yield board dicts from nested classification nodes."""
    for node in nodes or []:
        for board in node.get('boards') or []:
            yield board
        for key in ('subcategories', 'children', 'categories'):
            yield from _iter_classification_boards(node.get(key) or [])


def build_index_json(output_path=None):
    """构建完整的搜索索引 JSON

    Args:
        output_path: 可选输出路径。为 None 时使用模块级 INDEX_FILE（默认
            static/search_index.json）。传入路径允许测试或自定义部署
            将索引写到非默认位置。
    """
    index_file = Path(output_path) if output_path is not None else INDEX_FILE

    # 1. 从板块分类导入
    result = {}
    cf = BASE_DIR / 'static' / 'board_classification.json'
    try:
        with open(cf, 'r', encoding='utf-8') as f:
            cats = json.load(f).get('categories', [])
        for b in _iter_classification_boards(cats):
            name = b.get('name', '')
            code = b.get('code', '')
            if not code:
                continue
            cat_name = b.get('primary_category') or b.get('secondary_category') or b.get('category') or ''
            initials = compute_initials(name)
            tags = list(dict.fromkeys((b.get('tags') or []) + _generate_tags(name, cat_name, b.get('type', ''))))
            result[code] = {
                'name': name,
                'type': b.get('type', ''),
                'category': cat_name,
                'initials': initials,
                'tags': tags,
            }
        logger.info(f"板块索引: {len([k for k in result if result[k]['type'] in ('industry','concept')])} 条")
    except Exception as e:
        logger.warning(f"读取板块分类失败: {e}")

    # 2. 添加已知指数（启动时快速加载，不含QMT时作为备用）
    for code, (name, qmt_code, cat) in KNOWN_INDICES.items():
        if code not in result:
            initials = compute_initials(name)
            result[code] = {
                'name': name,
                'type': 'index',
                'category': cat,
                'initials': initials,
                'tags': _generate_tags(name, cat, 'index'),
            }
    logger.info(f"已知指数: {len([k for k in result if result[k]['type'] == 'index'])} 条")

    # 3. 从 QMT 获取全量指数（覆盖中证/上证/深证/国证等）
    try:
        qmt_indices = build_index_sectors()
        for idx in qmt_indices:
            code = idx['code']
            if code not in result:
                result[code] = {
                    'name': idx['name'],
                    'type': 'index',
                    'category': '指数',
                    'initials': idx['initials'],
                    'tags': _generate_tags(idx['name'], '指数', 'index'),
                }
        logger.info(f"QMT指数: {len([k for k in result if result[k]['type'] == 'index'])} 条")
    except Exception as e:
        logger.warning(f"获取QMT指数失败: {e}")

    # 4. 从 QMT 获取个股
    try:
        stocks = build_stock_index()
        for s in stocks:
            code = s['code']
            if code not in result or str(result[code].get('name', '')).startswith('个股 '):
                result[code] = {
                    'name': s['name'],
                    'type': 'stock',
                    'category': '个股',
                    'initials': s['initials'],
                    'tags': _generate_tags(s['name'], '个股', 'stock'),
                }
        logger.info(f"个股索引: {len([k for k in result if result[k]['type'] == 'stock'])} 条")
    except Exception as e:
        logger.warning(f"获取个股失败: {e}")

    # 5. 从本地成分股缓存补全个股名称/拼音。此路径不依赖 QMT。
    try:
        local_stocks = build_stock_index_from_constituents()
        for s in local_stocks:
            code = s['code']
            if code not in result or result[code].get('type') == 'stock':
                result[code] = {
                    'name': s['name'],
                    'type': 'stock',
                    'category': '个股',
                    'initials': s['initials'],
                    'tags': _generate_tags(s['name'], '个股', 'stock'),
                }
        logger.info(f"个股索引(含本地补全): {len([k for k in result if result[k]['type'] == 'stock'])} 条")
    except Exception as e:
        logger.warning(f"本地成分股个股索引失败: {e}")

    # 6. 补充港股指数
    hk_indices = [
        ('HSI', '恒生指数', '港股指数'),
        ('HSTECH', '恒生科技', '港股指数'),
    ]
    for code, name, cat in hk_indices:
        if code not in result:
            initials = compute_initials(name)
            result[code] = {'name': name, 'type': 'hk_index', 'category': cat, 'initials': initials,
                            'tags': _generate_tags(name, cat, 'hk_index')}

    # 写入文件
    output = {
        'version': 2,
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(result),
        'items': result,
    }

    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    logger.info(f"搜索索引已保存: {index_file}")
    logger.info(f"  总计: {len(result)} 条")
    for t in set(v['type'] for v in result.values()):
        count = len([v for v in result.values() if v['type'] == t])
        logger.info(f"  {t}: {count} 条")

    return result


if __name__ == '__main__':
    build_index_json()
