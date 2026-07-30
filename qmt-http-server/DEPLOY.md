# QMT HTTP Server 部署清单

> 源项目: https://github.com/Xorcerer/qmt
> 本地路径: `D:\.workbuddy\2026-06-27-21-35-52\board-app\qmt-http-server\`
> 服务文件目录（loader.py 默认监听）: `D:\.workbuddy\2026-06-27-21-35-52\board-app\qmt-http-server\deploy\`

## 目录结构

```
qmt-http-server/
├── repo/                    # git clone 完整仓库（含 docs/ API 文档）
│   ├── loader.py            # 入口：贴进 QMT 策略
│   ├── server.py            # 主服务：HTTP 路由 + 业务逻辑
│   ├── server_http_utils.py # HTTP 解析 + WebSocket 握手
│   ├── server_socket_utils.py # 非阻塞 socket I/O
│   ├── server_market_utils.py # K线/龙虎榜/信号点
│   ├── server_runtime_utils.py # 运行时状态
│   ├── server_config.json.example
│   └── docs/                # QMT 官方 API 文档
├── deploy/                  # ★ 服务文件目录（loader.py 直接监听此处，无需复制到 C:\server）
│   ├── loader.py            # 贴进 QMT 策略
│   ├── server.py
│   ├── server_http_utils.py
│   ├── server_market_utils.py
│   ├── server_runtime_utils.py
│   ├── server_socket_utils.py
│   ├── server_config.json  # 已预填（需补 account_id）
│   └── README.md
└── DEPLOY.md                # 本文件
```

## 你需要做的（4 步）

> **无需创建 `C:\server`**，loader.py 已改为直接监听 `deploy\` 目录。

### 第 1 步：确认 server_config.json

打开 `D:\.workbuddy\2026-06-27-21-35-52\board-app\qmt-http-server\deploy\server_config.json`，按需修改：

| 字段 | 当前值 | 说明 |
|---|---|---|
| `account_id` | `""` (空) | 留空=自动从 ContextInfo 发现；如自动发现失败，填你的资金账号 |
| `account_type` | `STOCK` | 股票账户，一般不用改 |
| `auth_token` | `""` (空) | 空=不鉴权（仅 127.0.0.1 本地访问，安全）；对外开放需填强随机 token |
| `quote_symbols` | 沪深300/上证50/中证500 | 启动时自动订阅的行情代码 |
| `quote_period` | `tick` | 行情周期 |
| `quote_dividend_type` | `none` | 复权类型 |

### 第 2 步：在 QMT GUI 创建策略

1. 打开 QMT 客户端（大同证券 QMT 实盘）
2. 进入「策略」→「新建策略」
3. 策略名随意，如 `qmt_http_server`
4. 把 `deploy/loader.py` 的**全部内容**粘贴到策略代码编辑器
5. 保存策略
6. （可选）右键策略 → 设为「随 GUI 启动」

### 第 3 步：启动策略

1. 在 QMT 策略列表中，点击该策略的「运行」
2. 观察 QMT 输出栏，应看到：
   ```
   [qmt-loader] server reloaded: D:\.workbuddy\2026-06-27-21-35-52\board-app\qmt-http-server\deploy\server.py
   [qmt-server] listener bound 127.0.0.1:18080
   ```
3. 如果看到报错，检查 `deploy\` 下 5 个 .py 文件是否齐全

### 第 4 步：验证

在浏览器或命令行访问：

```bash
# 服务信息
curl http://127.0.0.1:18080/

# 健康检查
curl http://127.0.0.1:18080/health

# 获取 K 线（日线 240 根）
curl "http://127.0.0.1:18080/candles?symbol=000300.SH&period=1d&count=240"

# 获取行情
curl "http://127.0.0.1:18080/quote?symbol=000300.SH"

# 订阅新标的
curl "http://127.0.0.1:18080/subscribe?symbol=600519.SH"

# 查看持仓
curl http://127.0.0.1:18080/positions

# 查看账户
curl http://127.0.0.1:18080/accounts
```

## 端口说明

| 端口 | 用途 |
|---|---|
| 18080 | HTTP/WebSocket 服务（本项目） |
| 5000 | board-app Flask 面板（已有） |
| 58600 | QMT RPC（公式口，已有） |
| 58610 | MiniQMT xtdata（不再需要） |

## 与 board-app 的集成方向

部署成功后，board-app 可以：
1. **替代 qmt_bridge.py**：把 `xtdata.connect(58610)` 改为 `requests.get('http://127.0.0.1:18080/candles?...')`
2. **替代 qmt_fetch.py**：直接 HTTP 调用，不需要 MiniQMT
3. **不再依赖 MiniQMT**：不需要 PIN 验证、不需要共享内存
4. **实时行情**：WebSocket `ws://127.0.0.1:18080/ws` 推送 quote_snapshot

## 注意事项

1. **Python 版本**：QMT 策略脚本环境是 Python 3.6.8，loader.py 和 server.py 已兼容
2. **无线程**：QMT 的 Python 是单线程，server 通过 `run_time()` 定时器驱动，不能开线程
3. **热加载**：修改 `deploy\` 下的 .py 文件后，QMT 自动重新加载，不需要重启
4. **配置热加载**：修改 `server_config.json` 后也不需要重启
5. **数据下载**：`/candles` 端点调 `ContextInfo.get_market_data()` 读本地缓存；如需下载历史数据，用 QMT GUI 的「智能下载」功能先下载，再通过 HTTP 读取
