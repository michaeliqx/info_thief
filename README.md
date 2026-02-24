# AI Daily Brief Agent

每天自动抓取 AI 相关公开信息，在北京时间 09:30 执行，并默认保存到本地归档。

## 1. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

推荐生产环境使用 Python 3.11。

## 2. 配置环境变量（Doubao）

```bash
export ARK_API_KEY="你的火山引擎ARK API Key"
```

如需启用企业微信推送，再额外配置：

```bash
export WECOM_WEBHOOK_URL="你的企业微信机器人Webhook"
```

如需启用飞书（指令 + 推送），再额外配置：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_VERIFICATION_TOKEN="你在飞书事件订阅里设置的token"
export FEISHU_ENCRYPT_KEY="" # 可选，若飞书事件配置了加密Key则必须填写
```

如需启用 news.google.com 等需代理的源（数字生命卡兹克、MindCode 等），在 `.env` 或环境中配置：

```bash
export HTTP_PROXY="http://127.0.0.1:7890"   # 或你的代理地址
```

## 3. 大模型接入测试

```bash
python -m app.test_llm
```

## 4. 单次运行（采集+整理+本地归档）

```bash
python -m app.run_daily
```

## 5. 启动后端服务

```bash
python -m app.server --host 127.0.0.1 --port 8000
```

可用接口：
- `GET /health`
- `POST /run-today`
- `GET /latest`
- `POST /feishu/events`（飞书事件回调）

## 6. 常驻调度

```bash
python -m app.scheduler
```

## 7. 测试

```bash
python -m pytest -q
```

## 8. 飞书接入（只接飞书场景）

1. 在 `config/settings.yaml` 打开飞书配置：

```yaml
push_enabled: true
feishu_enabled: true
feishu_app_id: ${FEISHU_APP_ID}
feishu_app_secret: ${FEISHU_APP_SECRET}
feishu_verification_token: ${FEISHU_VERIFICATION_TOKEN}
feishu_encrypt_key: ${FEISHU_ENCRYPT_KEY}
feishu_connection_mode: websocket
feishu_push_targets:
  - "oc_xxx" # 群聊 chat_id，或 open_id（取决于 feishu_receive_id_type）
feishu_receive_id_type: chat_id
feishu_require_mention: true
```

2. 在飞书开放平台配置事件订阅（推荐长连接）：
- 订阅方式选：`使用长连接接收事件`
- 添加事件：`im.message.receive_v1`
- 无需配置公网 URL

3. 启动服务（会自动启动飞书长连接）：

```bash
python3 -m app.server --host 0.0.0.0 --port 8000
```

4. 飞书里给机器人发命令：
- `/run`：立即执行一次并回传结果
- `/latest`：查看最新归档摘要
- `/status`：查看服务状态和当前会话 ID
- `/help`：查看命令帮助

5. 把 `/status` 返回的 `当前会话ID` 配到 `feishu_push_targets`，即可只推送到你的私聊会话。

## 9. 飞书 Webhook 备选模式（需要公网 URL）

如果你必须使用“将事件发送至开发者服务器”，可改为：

```yaml
feishu_connection_mode: webhook
```

然后在飞书事件订阅页配置：
- 请求地址：`https://你的公网域名/feishu/events`
- Verification Token：与 `FEISHU_VERIFICATION_TOKEN` 一致
- 事件：`im.message.receive_v1`
