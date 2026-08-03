import json
import os
import sqlite3
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE, 'data', 'kline.db')
TARGET_FILE = os.path.join(_BASE, 'static', 'board_classification.json')

# 1. 从 DB / 备份中获取全量板块列表
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
rows = cur.execute("SELECT code, name, type FROM meta WHERE period='daily' AND code LIKE 'BK%' ORDER BY code").fetchall()
conn.close()

if not rows:
    src = os.path.join(_BASE, 'static', 'board_classification.json.bak')
    if os.path.exists(src):
        old_data = json.load(open(src, encoding='utf-8'))
        boards_list = []
        for cat in (old_data.get('categories', []) if isinstance(old_data, dict) else old_data):
            for b in cat.get('boards', []):
                boards_list.append(b)
    else:
        boards_list = []
else:
    boards_list = [{'code': r[0], 'name': r[1], 'type': r[2]} for r in rows]

seen_codes = set()
unique_boards = []
for b in boards_list:
    code = b.get('code') or b.get('板块代码')
    name = b.get('name') or b.get('板块名称')
    btype = b.get('type') or ('industry' if (code.startswith('BK0') or code.startswith('BK1')) and len(code)==6 else 'concept')
    if code and code not in seen_codes:
        seen_codes.add(code)
        unique_boards.append({'code': code, 'name': name, 'type': btype})

print(f"Total unique boards found: {len(unique_boards)}")

# 2. 定义 10 大专业产业与主题分类树（全量涵盖 990 个板块的精准关键词体系）
CATEGORIES_TREE = [
    {
        "name": "💻 大科技与泛 TMT",
        "subcategories": [
            {
                "name": "AI与算力",
                "keywords": ["AI", "人工智能", "算力", "CPO", "光模块", "智算", "大模型", "液冷", "边缘计算", "ChatGPT", "AIGC", "神经网络", "生成式", "NLP", "英伟达", "Kimi", "DeepSeek", "Sora", "OpenAI", "多模态", "人脸识别", "语音", "智能体", "Agent", "智谱", "文生视频", "算法", "脑机", "智能控制", "字控", "机器视觉"]
            },
            {
                "name": "半导体与芯片",
                "keywords": ["半导体", "芯片", "第三代半导体", "封测", "MCU", "存储芯片", "汽车芯片", "光刻", "集成电路", "晶圆", "EDA", "IGBT", "硅片", "MEMS", "RISC-V", "闪存", "洁净室", "分立器件", "被动元件", "电子化学品", "光电子", "超导材料", "极大规模", "MLCC", "电子器件", "电子元件"]
            },
            {
                "name": "软件与 IT 服务",
                "keywords": ["软件", "信创", "数据", "网络安全", "云计算", "鸿蒙", "工业软件", "IT服务", "区块链", "智慧城市", "数字经济", "大数据", "数据中心", "ERP", "MLOps", "SaaS", "云服务", "操作系统", "中间件", "数据库", "信息安全", "东数西算", "政务", "财税", "算力网", "综合信息", "信息技术", "互联网服务", "软件开发", "系统集成", "知识产权", "Web3", "VPN", "IPv6", "安全"]
            },
            {
                "name": "消费电子与智能硬件",
                "keywords": ["消费电子", "折叠屏", "苹果", "小米", "华为", "智能穿戴", "虚拟现实", "MR", "VR", "AR", "元宇宙", "显示", "面板", "PCB", "印制电路板", "LED", "OLED", "MicroLED", "无线耳机", "摄像头", "红外", "被动器件", "玻璃基板", "数码", "智能终端", "安防", "智能音箱", "全息", "传感器", "光学", "触控"]
            },
            {
                "name": "通信与 5G/6G",
                "keywords": ["通信", "5G", "6G", "卫星", "光纤", "光缆", "量子", "无线", "通信设备", "雷达", "基站", "UWB", "星闪", "厘米波", "毫米波", "物联网", "车联网", "专网", "通信网络", "光通信", "移远", "宽带", "WiFi", "ETC", "车路云"]
            }
        ]
    },
    {
        "name": "🤖 先进制造与硬科技",
        "subcategories": [
            {
                "name": "机器人与具身智能",
                "keywords": ["机器人", "减速器", "具身智能", "伺服", "自动化", "智能制造", "减速机", "谐波", "行星", "丝杠", "人形机器人", "工业机器人", "服务机器人", "柔性制造", "无人驾驶", "智驾"]
            },
            {
                "name": "低空经济与商业航天",
                "keywords": ["低空经济", "飞行汽车", "eVTOL", "无人机", "空管", "商业航天", "军工", "航天", "航空", "国防", "通用航空", "航母", "兵器", "大飞机", "北斗", "空间站", "火箭", "卫星导航", "军工电子", "大船", "海防", "雷达防护"]
            },
            {
                "name": "高端装备与工业母机",
                "keywords": ["工业母机", "机床", "自动化设备", "工业机器人", "激光", "工程机械", "装备", "通用设备", "专用设备", "仪器仪表", "电机", "五金", "模具", "机械", "3D打印", "PLC", "检验检测", "阀门", "泵", "轴承", "压铸", "风机", "重机", "制冷设备", "起重", "制造", "高精", "锻造"]
            },
            {
                "name": "轨道交通与船舶重工",
                "keywords": ["轨交", "铁路", "船舶", "海工", "造船", "交通设备", "港口设备", "高铁", "客车", "动车", "航道", "水运"]
            }
        ]
    },
    {
        "name": "⚡ 新能源与绿色低碳",
        "subcategories": [
            {
                "name": "光伏与储能",
                "keywords": ["光伏", "储能", "逆变器", "组件", "太阳能", "TOPCon", "HJT", "钙钛矿", "光伏建筑", "BIPV", "电池片", "光伏设备", "分布式光伏"]
            },
            {
                "name": "锂电与电池技术",
                "keywords": ["锂", "电池", "固态电池", "钠电池", "电解液", "正极", "负极", "隔膜", "电池回收", "盐湖提锂", "PVDF", "全固态", "石墨烯", "液流电池", "锂资源", "锂矿", "碳酸锂", "蓄电池"]
            },
            {
                "name": "新能源汽车及零部件",
                "keywords": ["新能源车", "汽车", "自动驾驶", "智能驾驶", "零部件", "热管理", "快充", "充电桩", "特斯拉", "比亚迪", "赛力斯", "蔚来", "理想", "EDR", "智能座舱", "车载", "汽车一体化", "胎压", "汽车整车", "汽车零部件", "商用车", "乘用车", "底盘"]
            },
            {
                "name": "风电与氢能",
                "keywords": ["风电", "风能", "海上风电", "氢", "燃料电池", "绿色电力", "地热", "生物质", "核能", "核电", "核聚变", "核污染", "清洁能源", "节能环保", "减碳"]
            }
        ]
    },
    {
        "name": "⛏️ 周期与上游资源",
        "subcategories": [
            {
                "name": "有色金属与小金属",
                "keywords": ["有色", "黄金", "贵金属", "稀土", "永磁", "铜", "铝", "锌", "钛", "小金属", "金属", "镍", "钴", "镁", "锑", "钨", "钼", "锡", "铅", "锗", "铟", "超导", "矿业", "采掘", "矿产", "稀缺资源", "矿"]
            },
            {
                "name": "基础化工与新材料",
                "keywords": ["化工", "有机硅", "氟化工", "新材料", "塑料", "胶", "涂料", "化肥", "农药", "橡胶", "涤纶", "氨纶", "钛白粉", "民爆", "磷化工", "氯碱", "纯碱", "PEEK", "碳纤维", "膜材料", "玻纤", "耐火", "化学制品", "精细化工", "化学原料", "高分子", "合成树脂", "化学纤维", "基础化学", "合成"]
            },
            {
                "name": "能源资源(煤炭石油)",
                "keywords": ["煤炭", "石油", "石化", "油服", "页岩气", "天然气", "燃气", "采掘", "炼油", "可燃冰", "油气", "焦炭", "煤化工", "油气设服", "能源"]
            },
            {
                "name": "钢铁与基础建材",
                "keywords": ["钢铁", "水泥", "玻璃", "建材", "装修", "管材", "防水", "陶瓷", "金刚石", "培育钻石", "耐火材料", "饰材", "建筑材料"]
            }
        ]
    },
    {
        "name": "🛒 大消费与商业零售",
        "subcategories": [
            {
                "name": "食品饮料与白酒",
                "keywords": ["酿酒", "白酒", "啤酒", "饮料", "食品", "调味品", "预制菜", "乳业", "零食", "生鲜", "水产", "预包装", "烘焙", "茶叶", "农副食品", "休闲食品", "软饮料"]
            },
            {
                "name": "家用电器与家居",
                "keywords": ["家电", "电器", "智能家居", "家具", "小家电", "照明", "厨卫", "黑电", "白电", "厨电", "家居用品", "定制家居"]
            },
            {
                "name": "纺织服装与美妆轻工",
                "keywords": ["服装", "纺织", "造纸", "包装", "户外", "美妆", "化妆品", "珠宝", "鞋帽", "皮具", "婴童", "育儿", "宠物", "跨境电商", "轻工造纸", "文娱用品", "箱包"]
            },
            {
                "name": "商贸零售与文旅影音",
                "keywords": ["零售", "百货", "超市", "免税", "旅游", "酒店", "影院", "游戏", "动漫", "网红", "新零售", "文化", "体育", "娱乐", "彩票", "影视", "媒体", "出版", "广告", "会展", "潮玩", "谷子", "演艺", "商贸", "购物", "院线", "电竞", "C2M", "拼多多", "短剧", "微短剧", "字节", "快手"]
            }
        ]
    },
    {
        "name": "💊 医药生物与大健康",
        "subcategories": [
            {
                "name": "创新药与生物制品",
                "keywords": ["创新药", "CRO", "CXO", "生物", "疫苗", "基因", "血制品", "单抗", "靶向药", "抗体", "细胞治疗", "ADC", "合成生物", "生物医药", "生物技术"]
            },
            {
                "name": "中药与化学制剂",
                "keywords": ["中药", "化学制剂", "原料药", "仿制药", "医药", "药业", "制药", "蒙药", "藏药", "医药制品", "化学药", "药品"]
            },
            {
                "name": "医疗器械与设备",
                "keywords": ["医疗器械", "耗材", "诊断", "体外诊断", "医美", "眼科", "牙科", "康复", "防护", "SPD", "超声", "内窥镜", "医疗设备"]
            },
            {
                "name": "医疗服务与大健康",
                "keywords": ["医疗服务", "医院", "药店", "医药商业", "健康", "养老", "护理", "辅助生殖", "月子", "医疗信息化", "互联网医疗", "DRG", "DIP"]
            }
        ]
    },
    {
        "name": "🏦 大金融与地产基建",
        "subcategories": [
            {
                "name": "银行业",
                "keywords": ["银行", "城商行", "农商行", "大型银行", "商业银行"]
            },
            {
                "name": "证券与非银金融",
                "keywords": ["证券", "券商", "期货", "多元金融", "信托", "创投", "金融", "租赁", "资产管理", "债转股", "AMC", "互联金融", "移动支付", "数字货币", "跨境支付", "金融科技", "参股金融", "GDR", "QFII"]
            },
            {
                "name": "保险业",
                "keywords": ["保险", "人寿保险"]
            },
            {
                "name": "房地产与建筑基建",
                "keywords": ["房地产", "房产", "住宅", "物业", "建筑", "基建", "工程", "水利", "园林", "地下管网", "城市更新", "旧改", "不动产", "REITs", "装配式", "钢结构", "房屋建设", "施工", "PPP模式", "PPP"]
            }
        ]
    },
    {
        "name": "💡 公用事业与交通运输",
        "subcategories": [
            {
                "name": "电力与电网",
                "keywords": ["电力", "火电", "水电", "核电", "电网", "特高压", "智能电网", "发电机", "虚拟电厂", "配电", "输配电气", "热力", "智慧用电"]
            },
            {
                "name": "水务与环保",
                "keywords": ["水务", "环保", "垃圾", "固废", "污水", "环境", "碳中和", "节能", "减排", "土壤修护", "空气净化", "环卫", "资源循环", "水利环保"]
            },
            {
                "name": "交通运输与物流",
                "keywords": ["航空", "机场", "航运", "港口", "公路", "高速", "物流", "快递", "运力", "冷链", "仓储", "集装箱", "交运", "铁路运输", "物流仓储"]
            }
        ]
    },
    {
        "name": "🌾 现代农业与乡村振兴",
        "subcategories": [
            {
                "name": "农林种植与种子",
                "keywords": ["农业", "农林", "种植", "种子", "粮食", "乡村振兴", "农机", "转基因", "木业", "林业", "花卉", "农林牧渔", "化肥农药", "水利农业", "农产品"]
            },
            {
                "name": "畜牧养殖与水产",
                "keywords": ["养殖", "猪肉", "鸡肉", "肉禽", "水产养殖", "饲料", "兽药", "海产", "饲料加工", "畜牧"]
            }
        ]
    },
    {
        "name": "🎯 风格与主题热点",
        "subcategories": [
            {
                "name": "宏观与区域改革",
                "keywords": ["国企改革", "央企", "中字头", "一带一路", "地方国资", "上海国企", "深圳国企", "特区", "自贸区", "大湾区", "雄安", "海南", "西部大开发", "长三角", "京津冀", "东北", "成渝", "新疆", "西藏", "福建", "统一大市场"]
            },
            {
                "name": "资本运作与股权",
                "keywords": ["重组", "并购", "壳资源", "IPO", "股权", "分拆", "借壳", "阿里概念", "腾讯概念", "华为概念", "百度概念", "小米概念", "参股"]
            },
            {
                "name": "宽基指数与风格指标",
                "keywords": ["红利", "高股息", "破净", "破发", "微盘股", "高送转", "低市盈率", "绩优", "权重", "成分股", "龙头", "社保", "证金", "外资", "MSCI", "AH股", "富时罗素", "沪深300", "中证500", "上证50", "中证1000", "创业板50", "科创50", "指数", "成份", "标的", "ST", "B股", "AB股", "转债"]
            },
            {
                "name": "前沿与特色题材",
                "keywords": ["前沿题材", "特色题材", "百元股", "次新股", "炒作题材", "热门题材", "预增", "扭亏", "预盈", "业绩大增", "年报预增", "季报预增"]
            }
        ]
    }
]

# 3. 精准分配板块到分类树
category_map = {}
for cat in CATEGORIES_TREE:
    cat_name = cat["name"]
    category_map[cat_name] = {
        "name": cat_name,
        "subcategories": [
            {"name": sub["name"], "boards": []}
            for sub in cat["subcategories"]
        ],
        "boards": []
    }

unassigned = []

for b in unique_boards:
    name = b["name"]
    assigned = False

    # 清洗名称后缀以做更精确的匹配
    clean_name = name.replace("概念", "").replace("板块", "").strip()

    for cat in CATEGORIES_TREE:
        if assigned:
            break
        cat_name = cat["name"]
        for sub in cat["subcategories"]:
            sub_name = sub["name"]
            for kw in sub["keywords"]:
                if kw in name or kw in clean_name:
                    target_sub = next(s for s in category_map[cat_name]["subcategories"] if s["name"] == sub_name)
                    target_sub["boards"].append(b)
                    category_map[cat_name]["boards"].append(b)
                    assigned = True
                    break
            if assigned:
                break

    if not assigned:
        unassigned.append(b)

print(f"Assigned: {len(unique_boards) - len(unassigned)}, Unassigned fallback: {len(unassigned)}")

OFFICIAL_INDICES = [
    # 消费 & 酒 & 畜牧
    {"code": "sz399997", "name": "中证白酒(中证酒)", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "食品饮料与白酒"},
    {"code": "sh000932", "name": "中证主要消费", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "食品饮料与白酒"},
    {"code": "sh000931", "name": "上证主要消费", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "食品饮料与白酒"},
    {"code": "sh000912", "name": "300消费", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "食品饮料与白酒"},
    {"code": "sh000911", "name": "300可选", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "商贸零售与文旅影音"},
    {"code": "sz399996", "name": "医药消费50", "type": "index", "category": "🛒 大消费与商业零售", "subcategory": "食品饮料与白酒"},

    # 农业 & 畜牧
    {"code": "sz399813", "name": "中证畜牧", "type": "index", "category": "🌾 现代农业与乡村振兴", "subcategory": "畜牧养殖与水产"},
    {"code": "sz399812", "name": "中证大农业", "type": "index", "category": "🌾 现代农业与乡村振兴", "subcategory": "农林种植与种子"},
    {"code": "sh000949", "name": "CS农业", "type": "index", "category": "🌾 现代农业与乡村振兴", "subcategory": "农林种植与种子"},

    # 医药生物与健康
    {"code": "sh000933", "name": "中证医药", "type": "index", "category": "💊 医药生物与大健康", "subcategory": "中药与化学制剂"},
    {"code": "sz399989", "name": "中证医疗", "type": "index", "category": "💊 医药生物与大健康", "subcategory": "医疗器械与设备"},
    {"code": "sz399970", "name": "医疗器械", "type": "index", "category": "💊 医药生物与大健康", "subcategory": "医疗器械与设备"},
    {"code": "sz399986", "name": "中证中药", "type": "index", "category": "💊 医药生物与大健康", "subcategory": "中药与化学制剂"},
    {"code": "sh000913", "name": "300医药", "type": "index", "category": "💊 医药生物与大健康", "subcategory": "创新药与生物制品"},

    # AI 与大科技 / TMT
    {"code": "sh000993", "name": "全指半导体", "type": "index", "category": "💻 大科技与泛 TMT", "subcategory": "半导体与芯片"},
    {"code": "sh000988", "name": "CS计算机", "type": "index", "category": "💻 大科技与泛 TMT", "subcategory": "软件与 IT 服务"},
    {"code": "sz399971", "name": "CS传媒", "type": "index", "category": "💻 大科技与泛 TMT", "subcategory": "软件与 IT 服务"},
    {"code": "sz399811", "name": "CSSW电子", "type": "index", "category": "💻 大科技与泛 TMT", "subcategory": "消费电子与智能硬件"},
    {"code": "sh000915", "name": "300信息", "type": "index", "category": "💻 大科技与泛 TMT", "subcategory": "软件与 IT 服务"},

    # 新新能源与电力设备
    {"code": "sz399808", "name": "新能电池", "type": "index", "category": "⚡ 新新能源与电力设备", "subcategory": "锂电与电池技术"},
    {"code": "sz399976", "name": "CS新能车", "type": "index", "category": "⚡ 新新能源与电力设备", "subcategory": "新能源汽车及零部件"},
    {"code": "sh000941", "name": "CS新能源", "type": "index", "category": "⚡ 新新能源与电力设备", "subcategory": "光伏与储能"},

    # 先进制造与军工
    {"code": "sz399959", "name": "CS军工", "type": "index", "category": "🤖 先进制造与硬科技", "subcategory": "低空经济与商业航天"},
    {"code": "sh000910", "name": "300工业", "type": "index", "category": "🤖 先进制造与硬科技", "subcategory": "高端装备与工业母机"},

    # 周期与上游资源
    {"code": "sh000819", "name": "CS有色", "type": "index", "category": "⛏️ 周期与上游资源", "subcategory": "有色金属与小金属"},
    {"code": "sz399998", "name": "中证煤炭", "type": "index", "category": "⛏️ 周期与上游资源", "subcategory": "能源资源(煤炭石油)"},
    {"code": "sh000909", "name": "300原材料", "type": "index", "category": "⛏️ 周期与上游资源", "subcategory": "基础化工与新材料"},

    # 大金融与地产基建
    {"code": "sz399987", "name": "中证银行", "type": "index", "category": "🏦 大金融与地产基建", "subcategory": "银行业"},
    {"code": "sz399975", "name": "证券公司", "type": "index", "category": "🏦 大金融与地产基建", "subcategory": "证券与非银金融"},
    {"code": "sz399974", "name": "CS保险", "type": "index", "category": "🏦 大金融与地产基建", "subcategory": "保险业"},
    {"code": "sz399995", "name": "中证基建", "type": "index", "category": "🏦 大金融与地产基建", "subcategory": "房地产与建筑基建"},
    {"code": "sh000914", "name": "300金融", "type": "index", "category": "🏦 大金融与地产基建", "subcategory": "银行业"},

    # 宽基与大盘指数
    {"code": "sh000300", "name": "沪深300", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sh000905", "name": "中证500", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sh000852", "name": "中证1000", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sh000922", "name": "中证红利", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sh000001", "name": "上证指数", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sz399001", "name": "深证成指", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sz399006", "name": "创业板指", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sh000688", "name": "科创50", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sz399321", "name": "国证红利", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
    {"code": "sz399413", "name": "国证转债", "type": "index", "category": "🎯 风格与主题热点", "subcategory": "宽基指数与风格指标"},
]

# 挂载官方指数到分类树
added_indices_count = 0
for idx in OFFICIAL_INDICES:
    cat_name = idx["category"]
    sub_name = idx["subcategory"]
    item = {"code": idx["code"], "name": idx["name"], "type": "index"}
    
    if cat_name in category_map:
        target_sub = next((s for s in category_map[cat_name]["subcategories"] if s["name"] == sub_name), None)
        if target_sub:
            target_sub["boards"].insert(0, item) # 插入在最上方，突出官方指数
            category_map[cat_name]["boards"].insert(0, item)
            added_indices_count += 1

print(f"Successfully added {added_indices_count} official indices (type='index') into categories.")

final_categories = list(category_map.values())

result_payload = {
    "version": "5.0",
    "updated_at": "2026-07-26",
    "categories": final_categories
}

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    json.dump(result_payload, f, ensure_ascii=False, indent=2)

print(f"Successfully wrote 3-level classification to {TARGET_FILE}")
