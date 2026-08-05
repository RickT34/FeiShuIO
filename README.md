# MessageIO

MessageIO 是面向自主 Agent 和长任务的多平台消息桥。Server 负责连接飞书等消息平台、保存可靠队列并提供统一 REST API；Client 只理解平台无关的 target、sender 和 content，无需安装平台 SDK，也不会收到平台原始事件。

当前内置飞书 adapter。新增 Slack、Discord、Telegram 等平台时，现有 Client、CLI、REST DTO 和队列协议无需修改。

## 通用数据契约

发送请求：

```json
{"target":"gpu-a","content":{"type":"markdown","text":"**训练完成**"}}
```

发送成功时会返回同名 alias 实际触达的目的地数量：

```json
{"ok":true,"target":"gpu-a","sent":2}
```

接收响应：

```json
{
  "ok": true,
  "target": "gpu-a",
  "messages": [{
    "message_id": 42,
    "sender": {"id": "user-1", "name": "Rick"},
    "content": {"type": "text", "text": "继续运行"},
    "received_at": "2026-08-05 10:00:00"
  }],
  "ack_required": true,
  "lease_token": "..."
}
```

Client 看不到 `chat_id`、飞书响应 code、原始 webhook payload 或平台专用字段。`sender.id` 是平台提供的不透明标识，但字段语义和结构在所有平台一致。

规范化内容类型为 `text`、`markdown`、`image`、`file`、`audio`、`video` 和 `unknown`。当前飞书 adapter 支持发送 `text` 与 `markdown`；无法发送的通用类型会在 adapter 边界明确报错。

## 架构

```text
Client / CLI
    -> platform-neutral REST models
    -> target routing + reliable SQLite queue
    -> PlatformRegistry
       -> FeishuAdapter + Feishu listener
       -> future adapters/listeners
```

- `message_io/domain.py`：平台无关的 destination、sender、content 和内部投递引用。
- `message_io/platforms/base.py`：`PlatformAdapter` 与 `PlatformListener` 协议。
- `message_io/platforms/registry.py`：按 `(platform, account_id)` 注册和选择 adapter。
- `message_io/platforms/feishu.py`：飞书 API、payload 转换和入站事件规范化。
- `message_io/store.py`：通用目的地、去重、租约、确认和清理。
- `message_io/server.py`：只编排通用协议、store 和 registry。

数据库中的目的地由 `(platform, account_id, conversation_id)` 唯一标识，alias 只是对 Client 暴露的 target。同一 alias 可以绑定多个平台、账号和会话；接收时合并所有同名目的地的消息，按全局消息 ID 排序，并对合并结果应用一次 `limit`；发送时向所有同名目的地广播。广播是尽力而为且非原子的：服务会尝试全部目的地，任一失败时返回不含平台细节的成功数与失败数。消息在绑定前也可入队；稍后执行 `/bind alias` 后即可读取。外部消息 ID 按平台和账号隔离，不会因不同平台使用相同 ID 而错误去重。

## 新增平台

1. 实现 `PlatformAdapter.send()` 和 `mark_delivered()`。
2. 将平台事件转换为 `domain.IncomingMessage`，原始 payload 只写入私有存储字段。
3. 如需监听，实现 `PlatformListener` 的 `start()`、`stop()` 和 `status()`。
4. 在 `get_platforms()` 中注册 adapter/listener，并加入该平台自己的 callback route 和配置。

不得在通用 models、Client 或 CLI 中增加 provider 字段。平台能力差异应在 adapter 中映射到规范化内容类型，无法无损支持时返回明确错误。

## Server 配置

```bash
cp .env.example .env
```

最小配置：

```env
MESSAGE_IO_API_KEY=change-this-api-key
MESSAGE_IO_DB=./data/message_io.sqlite3
MESSAGE_IO_HOST=0.0.0.0
MESSAGE_IO_PORT=8000
FEISHU_ACCOUNT_ID=default
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
FEISHU_LISTENER_ENABLED=true
FEISHU_EVENT_VERIFY_TOKEN=xxxx
```

完整选项见 [`.env.example`](.env.example)。飞书长连接不需要公网 IP；HTTP callback 使用 `/platforms/feishu/events`，只有配置 Verification Token 后才启用。

启动前台服务：

```bash
./scripts/run-server.sh
```

安装 systemd 用户服务：

```bash
./scripts/install-server-service.sh
systemctl --user status message-io.service
```

跨机器访问时应使用 HTTPS 反向代理或可信内网，不要把带 API key 的明文 HTTP 暴露到公网。

## Client

所有自动化统一使用自举脚本：

```bash
printf '%s' 'change-this-api-key' | \
  ./scripts/client.sh configure https://message-io.example.com --key-stdin
./scripts/client.sh send gpu-a '**状态**：阶段 3/5 已完成'
./scripts/client.sh send gpu-a 'plain text' --type text
./scripts/client.sh recv gpu-a --wait 60 --no-ack
./scripts/client.sh ack gpu-a LEASE_TOKEN 1 2 3
```

`recv` 默认立即确认。无人值守 Agent 应使用 `--no-ack` 获取租约，在成功理解和处理消息后再 `ack`；租约过期的消息会重新投递。CLI 默认输出紧凑单行 JSON，`--full` 输出完整的通用响应，仍不会包含平台私有数据。

Python Client：

```python
from message_io import MessageIO

client = MessageIO("https://message-io.example.com", "api-key")
client.send("gpu-a", "**完成**")
messages = client.receive("gpu-a", ack=True)
```

## 飞书绑定

把机器人加入群聊后发送 `/bind gpu-a`。同一会话绑定新 alias 会替换该会话的旧 alias，但不会影响其他会话；不同平台、账号和会话可以共享同一个 alias。对该 alias 的接收会汇总所有绑定会话，对该 alias 的发送会广播到所有绑定会话。alias 允许字母、数字、下划线、横线和点，最长 64 个字符。

## 从 v3 迁移

新服务不会自动读取或修改 v3 数据库。停止旧服务后执行：

```bash
python3 scripts/migrate_v3_to_v5.py data/message_io_v3.sqlite3 \
  --output data/message_io.sqlite3 \
  --env-file .env \
  --env-output .env.v5
```

脚本不会覆盖源文件。它保留绑定、消息 ID、已投递状态、有效租约和去重记录，并把旧数据归入 `feishu/default`。检查 `.env.v5` 后，用它替换部署配置再启动 MessageIO。输出数据库或配置已存在时脚本会拒绝运行。运行时不包含旧 schema 兼容逻辑。

## REST API

- `POST /messages/send`
- `POST /messages/receive`
- `POST /messages/acknowledge`
- `GET /health`
- `GET /ready`
- `POST /maintenance/cleanup`
- `POST /platforms/feishu/events`

业务接口接受 `X-API-Key` 或 `Authorization: Bearer ...`。健康检查不包含消息内容；readiness 只报告数据库和消息后端聚合状态，不向 Client 暴露具体平台。

## 验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q message_io scripts/migrate_v3_to_v5.py
```

可复用的自主 Agent 指令模板位于 [`prompts/AGENTS.md`](prompts/AGENTS.md)。
