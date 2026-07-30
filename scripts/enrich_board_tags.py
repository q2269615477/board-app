# -*- coding: utf-8 -*-
"""
enrich_board_tags.py — 板块 tags 质量优化脚本

目标:
  - 每个 board 至少 2 个、最多 6 个 tags
  - 使用可复用维度标签（一级主题短标签、二级分类标签、类型标签、产业链/风格/事件/区域等）
  - 降低 single_use_tag_ratio 到 <= 35%
  - 降低 boards_with_1_tag 到 <= 20%

规则:
  1. 一级分类 → 11 个短标签（必加）
  2. 二级分类 → 规范化为短标签（必加）
  3. type → "行业" / "概念"（必加）
  4. 领域维度标签 → 按分类层级添加 1-2 个可复用标签
  5. 保留原 tag 中出现 >= 3 次的（高频可复用）
  6. 删除原 tag 中单次出现且不属于领域维度的专有名词
"""
import json
import sys
from pathlib import Path
from collections import Counter

SRC = Path("static/board_classification.json")

# ====== 1. 一级分类 → 短标签 ======
PRIMARY_SHORT_TAG = {
    "AI 与数字科技": "AI科技",
    "智能终端与电子制造": "电子制造",
    "先进制造与军工装备": "先进制造",
    "新能源与电力设备": "新能源",
    "周期资源与材料": "周期资源",
    "医药生物与健康": "医药健康",
    "消费与服务": "消费服务",
    "金融地产与基建": "金融地产",
    "公用事业与交通运输": "公用交通",
    "农业与乡村振兴": "农业",
    "主题、风格与事件": "主题风格",
}

# ====== 2. 二级分类 → 规范化短标签 ======
# 映射长的二级分类为短且可复用的标签
SECONDARY_SHORT_TAG = {
    "AI 模型与应用": "AI应用",
    "算力基础设施": "算力",
    "芯片半导体": "半导体",
    "数据与安全": "数据安全",
    "软件与IT服务": "软件服务",
    "通信网络": "通信",
    "消费电子终端": "消费电子",
    "显示与光学": "显示光学",
    "安防与传感": "安防传感",
    "PCB 与电子元件": "PCB元件",
    "智能硬件生态": "智能硬件",
    "创新药与 CXO": "创新药",
    "细胞治疗与前沿生物": "前沿生物",
    "中药与化学制药": "化学制药",
    "医疗器械与诊断": "医疗器械",
    "消费医疗与医美": "医美健康",
    "医疗服务与大健康": "大健康",
    "光伏产业链": "光伏",
    "新能源汽车": "新能源车",
    "储能与电池": "储能电池",
    "电池材料": "电池材料",
    "汽车零部件与电动化": "汽车零部件",
    "风电氢能核电": "风电氢能",
    "食品加工与调味品": "食品加工",
    "酒类与饮料": "酒类饮料",
    "化妆品与饰品": "美妆饰品",
    "纺织服装": "纺织",
    "包装造纸轻工": "包装造纸",
    "家电家居": "家电",
    "电商与新零售": "电商零售",
    "商贸零售": "商贸零售",
    "社会服务": "社会服务",
    "旅游酒店餐饮": "旅游餐饮",
    "游戏影视": "游戏影视",
    "体育与文娱": "体育文娱",
    "传媒出版广告": "传媒广告",
    "教育服务": "教育",
    "低空与无人系统": "低空经济",
    "机器人与自动化": "机器人",
    "军工航天": "军工",
    "工业母机与通用设备": "工业母机",
    "船舶与海工": "船舶海工",
    "工程机械与重装": "工程机械",
    "交通物流": "交通物流",
    "电力运营": "电力运营",
    "环保水务": "环保水务",
    "电网设备": "电网设备",
    "钢铁建材": "钢铁建材",
    "贵金属与有色": "有色贵金属",
    "稀土小金属与能源金属": "稀土小金属",
    "化纤塑料与新材料": "化纤材料",
    "基础化工": "基础化工",
    "煤炭油气": "煤炭油气",
    "风格因子": "风格因子",
    "特殊标签": "特殊标签",
    "指数成分股": "指数成分",
    "短期热点": "短期热点",
    "平台与大厂映射": "大厂映射",
    "资金偏好": "资金偏好",
    "改革与资本运作": "资本运作",
    "区域主题": "区域主题",
    "财报事件": "财报事件",
    "养殖与饲料": "养殖饲料",
    "种植与种业": "种植种业",
    "农产品加工": "农产品加工",
    "银行": "银行",
    "券商与非银": "券商非银",
    "金融科技": "金融科技",
    "房地产": "房地产",
    "建筑基建": "建筑基建",
}

# ====== 3. 领域维度标签 ======
# 按 (一级分类, 二级分类) 给出可复用的领域标签
# 这些标签都是高频可复用的通用词
DOMAIN_TAGS = {
    # --- AI 与数字科技 ---
    "AI 模型与应用": ["AI", "AIGC"],
    "算力基础设施": ["算力", "数据中心"],
    "芯片半导体": ["半导体", "芯片"],
    "数据与安全": ["数据", "网络安全"],
    "软件与IT服务": ["软件", "IT服务"],
    "通信网络": ["通信", "5G"],
    # --- 智能终端与电子制造 ---
    "消费电子终端": ["消费电子", "智能终端"],
    "显示与光学": ["显示", "光学"],
    "安防与传感": ["安防", "传感器"],
    "PCB 与电子元件": ["PCB", "电子元件"],
    "智能硬件生态": ["智能硬件", "消费电子"],
    # --- 医药生物与健康 ---
    "创新药与 CXO": ["创新药", "CXO"],
    "细胞治疗与前沿生物": ["细胞治疗", "生物制药"],
    "中药与化学制药": ["制药", "中药"],
    "医疗器械与诊断": ["医疗器械", "体外诊断"],
    "消费医疗与医美": ["医美", "消费医疗"],
    "医疗服务与大健康": ["医疗服务", "养老"],
    # --- 新能源与电力设备 ---
    "光伏产业链": ["光伏", "硅料"],
    "新能源汽车": ["新能源车", "汽车"],
    "储能与电池": ["储能", "锂电池"],
    "电池材料": ["电池材料", "锂电材料"],
    "汽车零部件与电动化": ["汽车零部件", "充电桩"],
    "风电氢能核电": ["风电", "氢能"],
    # --- 消费与服务 ---
    "食品加工与调味品": ["食品", "调味品"],
    "酒类与饮料": ["白酒", "饮料"],
    "化妆品与饰品": ["化妆品", "饰品"],
    "纺织服装": ["纺织", "服装"],
    "包装造纸轻工": ["造纸", "包装"],
    "家电家居": ["家电", "智能家居"],
    "电商与新零售": ["电商", "新零售"],
    "商贸零售": ["零售", "商贸"],
    "社会服务": ["社会服务", "消费"],
    "旅游酒店餐饮": ["旅游", "酒店"],
    "游戏影视": ["游戏", "影视"],
    "体育与文娱": ["体育", "文娱"],
    "传媒出版广告": ["传媒", "广告"],
    "教育服务": ["教育", "在线教育"],
    # --- 先进制造与军工装备 ---
    "低空与无人系统": ["低空经济", "无人机"],
    "机器人与自动化": ["机器人", "自动化"],
    "军工航天": ["军工", "航天"],
    "工业母机与通用设备": ["工业母机", "机床"],
    "船舶与海工": ["船舶", "海工装备"],
    "工程机械与重装": ["工程机械", "重装"],
    # --- 公用事业与交通运输 ---
    "交通物流": ["物流", "航运"],
    "电力运营": ["电力", "火电"],
    "环保水务": ["环保", "水务"],
    "电网设备": ["电网", "电力设备"],
    # --- 周期资源与材料 ---
    "钢铁建材": ["钢铁", "建材"],
    "贵金属与有色": ["有色", "黄金"],
    "稀土小金属与能源金属": ["稀土", "小金属"],
    "化纤塑料与新材料": ["化工", "化纤"],
    "基础化工": ["化工", "化肥"],
    "煤炭油气": ["煤炭", "油气"],
    # --- 主题、风格与事件 ---
    "风格因子": ["风格", "因子投资"],
    "特殊标签": ["特殊标签", "综合"],
    "指数成分股": ["指数", "成分股"],
    "短期热点": ["热点", "涨停"],
    "平台与大厂映射": ["平台经济", "大厂"],
    "资金偏好": ["资金", "机构"],
    "改革与资本运作": ["改革", "资本运作"],
    "区域主题": ["区域", "主题"],
    "财报事件": ["财报", "业绩"],
    # --- 农业与乡村振兴 ---
    "养殖与饲料": ["养殖", "饲料"],
    "种植与种业": ["种植", "种业"],
    "农产品加工": ["农产品", "加工"],
    # --- 金融地产与基建 ---
    "银行": ["银行", "国有银行"],
    "券商与非银": ["券商", "保险"],
    "金融科技": ["金融科技", "支付"],
    "房地产": ["房地产", "物业"],
    "建筑基建": ["基建", "建筑"],
}

# ====== 4. 高频复用标签白名单（原 tag 中出现 >= 3 次则保留） ======
# 这些是已经在使用的、有复用价值的高频标签
REUSABLE_TAG_WHITELIST = {
    "AI", "算力", "半导体", "芯片", "数据", "软件", "通信", "云计算", "物联网",
    "5G", "电子元件", "消费电子", "显示", "LED", "VR", "PCB", "安防", "机器人",
    "自动化", "军工", "航天", "航空", "新能源", "电池", "光伏", "储能", "风电",
    "氢能", "核电", "锂电", "电力", "医药", "制药", "中药", "疫苗", "医美",
    "创新药", "CRO", "医疗器械", "医疗服务", "食品", "家电", "白酒", "零售",
    "电商", "纺织", "旅游", "游戏", "传媒", "教育", "汽车", "化工", "钢铁",
    "有色", "黄金", "煤炭", "油气", "金融", "证券", "银行", "地产", "基建",
    "物流", "环保", "农业", "养殖", "种植", "风格", "指数", "区域", "资金",
    "财报", "事件", "涨停", "热点", "主题题材", "数字经济", "网络安全",
    "智慧城市", "国产替代", "新能源车", "充电桩", "智能终端", "智能家居",
    "新材料", "化纤", "化肥", "工程机械", "船舶", "电网", "稀土", "小金属",
    "食品饮料", "调味品", "化妆品", "服装", "包装", "造纸", "酒店", "影视",
    "广告", "在线教育", "农机", "轨道交通", "摩托车", "电机", "仪器",
    "工业互联网", "工业4.0", "大数据", "区块链", "边缘计算", "算力芯片",
    "光通信", "光刻机", "EDA", "MLCC", "传感器", "面板", "光学", "元宇宙",
    "华为", "小米", "特斯拉", "比亚迪", "宁德时代", "苹果", "英伟达",
    "算力服务器", "数据中心", "信创", "ERP", "SAAS", "智慧城市",
    "智能电网", "特高压", "逆变器", "硅料", "组件", "电站", "分布式",
    "燃料电池", "绿电", "地热", "水电", "火电", "热电", "生物质",
    "充电桩", "换电", "汽车零部件", "轮胎", "汽车电子", "车联网",
    "自动驾驶", "智能座舱", "线控底盘", "空气悬架", "一体化压铸",
    "宠物经济", "婴童", "户外", "免税", "跨境电商", "网红经济", "新零售",
    "团购", "直播", "电商物流", "快递", "航运", "港口", "铁路", "公路",
    "航空", "机场", "公交", "仓储", "供应链", "冷链", "油运", "干散货",
    "保险", "信托", "期货", "基金", "支付", "数字货币", "区块链",
    "REITs", "物业", "租赁", "水利工程", "PPP", "建材", "水泥", "玻璃",
    "陶瓷", "涂料", "钛白粉", "塑料", "橡胶", "民爆", "炼化", "乙烯",
    "焦炭", "燃气", "石油", "天然气", "页岩油", "油服", "勘探",
    "饲料", "养殖", "动物", "猪肉", "鸡肉", "水产", "粮食", "种植",
    "林业", "乡村振兴", "农化", "大麻", "粮油", "食用菌", "加工",
    "QFII", "社保", "转债", "证金", "基金", "股权", "科创板", "化债",
    "中字头", "改革", "京津冀", "一带一路", "自贸", "长三角", "珠三角",
    "大湾区", "西部", "东北", "中部", "浦东", "雄安", "海南", "深圳",
    "茅指数", "宁组合", "微盘", "红利", "破净", "破发", "次新股",
    "高送转", "填权", "增持", "回购", "解禁", "减持", "IPO", "定增",
    "重组", "并购", "借壳", "整体上市", "分拆", "股权激励", "员工持股",
    "业绩", "预增", "预减", "扭亏", "续亏", "首亏", "摘帽", "戴帽",
    "警示", "退市", "停牌", "复牌", "除权", "派息", "分红",
    "新高", "新低", "换手", "振幅", "量比", "委比", "外盘", "内盘",
    "主力", "散户", "机构", "游资", "北向", "南向", "融资", "融券",
    "股权质押", "增持", "减持", "大宗交易", "龙虎榜", "机构调研",
    "抖音", "拼多多", "快手", "阿里", "腾讯", "百度", "京东", "字节",
    "蚂蚁", "美团", "滴滴", "小红书", "B站", "网易",
}


def flatten_boards(data):
    """展平所有 board，兼容 v5 subcategories 与旧版 boards 混合结构"""
    boards = []
    for cat in data.get("categories", []):
        for sub in cat.get("subcategories", []):
            for b in sub.get("boards", []):
                boards.append(b)
        for b in cat.get("boards", []):
            boards.append(b)
    return boards


def clean_tag(tag):
    """清洗 tag：去空白，过滤空字符串和过长标签"""
    if not isinstance(tag, str):
        return None
    t = tag.strip()
    if not t:
        return None
    if len(t) > 8:  # 超过 8 字的标签太长了，过滤
        return None
    return t


def enrich_tags(data):
    """对每个 board 生成优化的 tags"""
    # 先统计原 tag 频率
    tag_counter = Counter()
    boards = flatten_boards(data)
    for b in boards:
        for t in b.get("tags", []):
            cleaned = clean_tag(t)
            if cleaned:
                tag_counter[cleaned] += 1

    # 高频原标签（出现 >= 3 次）自动加入可复用集合
    high_freq_tags = {t for t, n in tag_counter.items() if n >= 3}
    reusable_whitelist = REUSABLE_TAG_WHITELIST | high_freq_tags

    # 遍历所有 board 重新生成 tags（兼容 v5 subcategories 与旧版 boards 混合结构）
    changed = 0
    for cat in data.get("categories", []):
        all_boards = []
        for sub in cat.get("subcategories", []):
            for b in sub.get("boards", []):
                all_boards.append(b)
        for b in cat.get("boards", []):
            all_boards.append(b)
        for b in all_boards:
            original_tags = [clean_tag(t) for t in b.get("tags", [])]
            original_tags = [t for t in original_tags if t is not None]

            new_tags = []

            # 1. 一级分类短标签（必加）
            pc = b.get("primary_category", "")
            primary_tag = PRIMARY_SHORT_TAG.get(pc, pc)
            if primary_tag:
                new_tags.append(primary_tag)

            # 2. 二级分类短标签（必加）
            sc = b.get("secondary_category", "")
            secondary_tag = SECONDARY_SHORT_TAG.get(sc, sc)
            if secondary_tag and secondary_tag != primary_tag:
                new_tags.append(secondary_tag)

            # 3. 类型标签
            btype = b.get("type", "")
            if btype == "industry":
                new_tags.append("行业")
            elif btype == "concept":
                new_tags.append("概念")

            # 4. 领域维度标签
            domain_tags = DOMAIN_TAGS.get(sc, [])
            for dt in domain_tags:
                if dt not in new_tags:
                    new_tags.append(dt)
                if len(new_tags) >= 6:
                    break

            # 5. 保留原高频可复用标签
            for ot in original_tags:
                if ot in reusable_whitelist and ot not in new_tags:
                    new_tags.append(ot)
                if len(new_tags) >= 6:
                    break

            # 6. 如果仍然少于 2 个标签，补加兜底标签
            if len(new_tags) < 2:
                # 从原标签中补充任何可用的
                for ot in original_tags:
                    if ot not in new_tags:
                        new_tags.append(ot)
                    if len(new_tags) >= 2:
                        break
            if len(new_tags) < 2:
                new_tags.append("股票")

            # 最终去重（保持顺序）
            seen = set()
            deduped = []
            for t in new_tags:
                if t not in seen:
                    seen.add(t)
                    deduped.append(t)
            new_tags = deduped[:6]  # 最多 6 个

            # 检查是否有变化
            if set(new_tags) != set(original_tags) or len(new_tags) != len(original_tags):
                changed += 1

            b["tags"] = new_tags

    return changed


def print_stats(data, label=""):
    """打印 tags 统计"""
    boards = flatten_boards(data)
    tag_counter = Counter()
    boards_with_1_tag = 0
    for b in boards:
        tags = b.get("tags", [])
        tag_counter.update(tags)
        if len(tags) == 1:
            boards_with_1_tag += 1

    total = len(boards)
    unique_tags = len(tag_counter)
    single_use = sum(1 for t, n in tag_counter.items() if n == 1)
    single_use_ratio = single_use / max(unique_tags, 1)
    one_tag_ratio = boards_with_1_tag / max(total, 1)

    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    print(f"  总板块数: {total}")
    print(f"  唯一 tag 数: {unique_tags}")
    print(f"  仅 1 个 tag 的板块: {boards_with_1_tag} ({one_tag_ratio:.1%})")
    print(f"  单次 tag 数: {single_use} ({single_use_ratio:.1%})")
    print(f"  Top 15 tags: {dict(tag_counter.most_common(15))}")
    return {
        "total": total,
        "unique_tags": unique_tags,
        "boards_with_1_tag": boards_with_1_tag,
        "one_tag_ratio": one_tag_ratio,
        "single_use": single_use,
        "single_use_ratio": single_use_ratio,
    }


def main():
    print("=" * 60)
    print("enrich_board_tags.py — 板块 tags 质量优化")
    print("=" * 60)

    # 读取
    with open(SRC, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 原统计
    stats_before = print_stats(data, "优化前")

    # 执行 enrichment
    changed = enrich_tags(data)
    print(f"\n  已更新 {changed} 个板块的 tags")

    # 新统计
    stats_after = print_stats(data, "优化后")

    # 更新 taxonomy
    data["taxonomy"] = {
        "schema": "industry_tree_with_tags",
        "primary_count": 11,
        "tag_schema": {
            "dimensions": [
                "primary_short_tag (一级主题短标签, 必加)",
                "secondary_short_tag (二级分类短标签, 必加)",
                "type_tag (行业/概念, 必加)",
                "domain_tags (领域维度标签, 1-2个)",
                "reusable_original_tags (原高频可复用标签, 选加)"
            ],
            "rules": [
                "每个 board 2-6 个 tags",
                "tags 去重、非空",
                "single-use 专有名词 tag 在有足够可复用 tags 时删除",
                "一级分类映射为 11 个短标签：AI科技、电子制造、先进制造、新能源、周期资源、医药健康、消费服务、金融地产、公用交通、农业、主题风格",
                "二级分类规范化为短标签，保证同一二级分类内所有 board 共享",
                "领域维度标签使用高频可复用词，避免一次性专名"
            ]
        },
        "rules": {
            "primary_category": "唯一主产业归属",
            "secondary_category": "产业链环节或业务方向",
            "tags": "一级主题短标签 + 二级分类短标签 + 类型标签 + 领域维度标签"
        }
    }

    # 更新 updated_at
    from datetime import datetime
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d")

    # 写回
    with open(SRC, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"\n  已写入 {SRC}")
    print(f"\n  对比:")
    print(f"    boards_with_1_tag: {stats_before['boards_with_1_tag']} ({stats_before['one_tag_ratio']:.1%}) → "
          f"{stats_after['boards_with_1_tag']} ({stats_after['one_tag_ratio']:.1%})")
    print(f"    single_use_tag_ratio: {stats_before['single_use_ratio']:.1%} → {stats_after['single_use_ratio']:.1%}")

    # 检查是否达标
    ok = True
    if stats_after["one_tag_ratio"] > 0.20:
        print(f"  [WARN] boards_with_1_tag 占比 {stats_after['one_tag_ratio']:.1%} > 20%")
        ok = False
    if stats_after["single_use_ratio"] > 0.35:
        print(f"  [WARN] single_use_tag_ratio {stats_after['single_use_ratio']:.1%} > 35%")
        ok = False
    if ok:
        print(f"  [OK] 所有指标达标!")
    else:
        print(f"  [WARN] 部分指标未达标，需要进一步调整")


if __name__ == "__main__":
    main()
