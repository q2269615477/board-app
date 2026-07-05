"""
build_search_index.py - 构建全市场搜索索引
从 QMT 获取股票+指数+板块数据，计算拼音首字母，输出 JSON
"""
import json
import time
import logging
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


def compute_initials(name: str) -> list[str]:
    """计算中文拼音首字母"""
    from pypinyin import lazy_pinyin, Style
    try:
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
        return [c.upper() if c.isalpha() else '' for c in name]


def build_stock_index() -> list[dict]:
    """从 QMT 获取全市场股票列表并构建索引"""
    try:
        from xtquant import xtdata
        xtdata.connect(port=58610)
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


def build_index_sectors() -> list[dict]:
    """从 QMT 获取全量指数列表"""
    try:
        from xtquant import xtdata
        xtdata.connect(port=58610)
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


def build_index_json():
    """构建完整的搜索索引 JSON"""

    # 1. 从板块分类导入
    result = {}
    cf = BASE_DIR / 'static' / 'board_classification.json'
    try:
        with open(cf, 'r', encoding='utf-8') as f:
            cats = json.load(f).get('categories', [])
        for cat in cats:
            for b in cat.get('boards', []):
                name = b.get('name', '')
                code = b.get('code', '')
                if not code:
                    continue
                initials = compute_initials(name)
                result[code] = {
                    'name': name,
                    'type': b.get('type', ''),
                    'category': cat.get('name', ''),
                    'initials': initials,
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
                }
        logger.info(f"QMT指数: {len([k for k in result if result[k]['type'] == 'index'])} 条")
    except Exception as e:
        logger.warning(f"获取QMT指数失败: {e}")

    # 4. 从 QMT 获取个股
    try:
        stocks = build_stock_index()
        for s in stocks:
            code = s['code']
            if code not in result:
                result[code] = {
                    'name': s['name'],
                    'type': 'stock',
                    'category': '个股',
                    'initials': s['initials'],
                }
        logger.info(f"个股索引: {len([k for k in result if result[k]['type'] == 'stock'])} 条")
    except Exception as e:
        logger.warning(f"获取个股失败: {e}")

    # 4. 补充港股指数
    hk_indices = [
        ('HSI', '恒生指数', '港股指数'),
        ('HSTECH', '恒生科技', '港股指数'),
    ]
    for code, name, cat in hk_indices:
        if code not in result:
            initials = compute_initials(name)
            result[code] = {'name': name, 'type': 'hk_index', 'category': cat, 'initials': initials}

    # 写入文件
    output = {
        'version': 2,
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(result),
        'items': result,
    }

    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    logger.info(f"搜索索引已保存: {INDEX_FILE}")
    logger.info(f"  总计: {len(result)} 条")
    for t in set(v['type'] for v in result.values()):
        count = len([v for v in result.values() if v['type'] == t])
        logger.info(f"  {t}: {count} 条")

    return result


if __name__ == '__main__':
    build_index_json()
