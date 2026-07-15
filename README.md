# FeiShuIO

一个 server/client 分离的极简飞书消息桥。Server 在服务器上常驻，负责飞书长连接、消息队列和 REST API；Client 只在需要发送或接收消息时启动，命令结束后退出。

它支持飞书开放平台的长连接事件接收模式，服务器不需要公网 IP，只需要能主动访问飞书开放平台。

业务接口只保留两个：

- `send_markdown(text, id)`：向 `id` 绑定的飞书群发送 Markdown。
- `recv_unread(id)`：取出 `id` 绑定群的未读消息，返回后即标记为已读。

群绑定不再手动填写 `chat_id`。把应用机器人拉进群后，在群里发送：

```text
/bind test
```

服务会自动记录：

```text
test -> 当前群 chat_id
```

之后其他项目只需要使用 `test`。

## 飞书应用配置

在飞书开放平台创建企业自建应用，并开启机器人能力。

需要配置：

- `APP_ID`
- `APP_SECRET`
- 事件订阅方式选择长连接
- 事件订阅 Verification Token
- 接收消息事件，例如 `im.message.receive_v1`
- 发送消息所需权限，例如向群聊发送消息的权限
- 添加消息 reaction 所需权限，用于 `recv_unread` 后给原消息标记已处理

把机器人添加到目标群后，在群里发 `/bind alias` 完成绑定。
同一个群再次绑定新的 `alias` 时，会替换旧 alias，避免一个群同时占用多个业务 id。

`alias` 只能包含字母、数字、下划线、横线和点，最长 64 个字符，例如：

```text
/bind research
/bind paper-alert
/bind exp.v1
```

## 架构和使用方式

- Server：只部署一份，持久连接飞书并保存 SQLite 消息队列。
- Client：可安装在任意 agent 或任务机器上，只依赖 HTTP 客户端，不需要飞书 SDK、数据库或服务端配置。
- 调用端只需要知道 Server URL、API key 和群 alias。

API key 会随 HTTP 请求发送。跨机器部署时应使用 HTTPS 反向代理或可信内网，不要把明文 HTTP 直接暴露到公网。

## Server 常驻部署

Server 使用独立 Python 虚拟环境，不需要 Docker，也不需要手动激活环境。先准备配置：

```bash
cp .env.example .env
# 编辑 .env，填入 API key 和飞书应用凭证
```

Linux 服务器使用 systemd 用户服务常驻运行，只需执行：

```bash
./scripts/install-server-service.sh
```

安装脚本会自动创建 `.server-venv`、安装 Server 依赖、写入当前用户的 systemd service，并立即启动；安装服务本身不需要 root。为了保证服务器重启或用户退出登录后仍然运行，需要为该用户启用 linger：

```bash
sudo loginctl enable-linger "$USER"
```

日常管理：

```bash
systemctl --user status feishu-io.service
systemctl --user restart feishu-io.service
journalctl --user -u feishu-io.service -f
curl http://127.0.0.1:8000/ready
```

没有 systemd 或只想前台运行时，使用同一个轻量启动器：

```bash
./scripts/run-server.sh
```

它会在首次运行时自动创建环境，后续直接启动。卸载常驻服务但保留配置和数据：

```bash
./scripts/uninstall-server-service.sh
```

## Server 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```env
FEISHU_IO_API_KEY=change-this-api-key
FEISHU_IO_DB=./data/feishu_io.sqlite3
FEISHU_IO_ENABLE_WS=true
FEISHU_IO_HOST=0.0.0.0
FEISHU_IO_PORT=8000
FEISHU_IO_LOG_LEVEL=info
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
FEISHU_EVENT_VERIFY_TOKEN=xxxx
FEISHU_EVENT_ENCRYPT_KEY=
FEISHU_MARK_READ_REACTION=true
FEISHU_READ_REACTION_EMOJI=Get
FEISHU_RETRY_ATTEMPTS=3
FEISHU_RETRY_BASE_DELAY=0.5
FEISHU_LISTENER_RETRY_BASE_DELAY=1
FEISHU_LISTENER_RETRY_MAX_DELAY=60
FEISHU_MESSAGE_LEASE_SECONDS=300
FEISHU_DELIVERED_RETENTION_DAYS=30
FEISHU_PROCESSED_RETENTION_DAYS=30
```

`FEISHU_IO_API_KEY` 是本服务业务接口的访问 key，用来防止信息泄露。

`FEISHU_IO_ENABLE_WS=true` 表示启动 REST 服务时自动启动飞书长连接监听。大多数单机部署保持默认即可。

`FEISHU_MARK_READ_REACTION=true` 表示 `recv_unread` 成功取走缓存消息后，会给飞书原消息添加一个 reaction，默认 emoji 类型是 `Get`。如果飞书侧权限或 emoji 类型不支持，接口仍会返回消息，只在服务日志里记录失败原因。

`FEISHU_RETRY_ATTEMPTS` 和 `FEISHU_RETRY_BASE_DELAY` 控制飞书 HTTP API 的有限重试，用于处理临时网络错误、429 和 5xx。发送消息会携带飞书 `uuid` 幂等键后再重试；没有幂等保证的写操作不会自动重放。

`FEISHU_LISTENER_RETRY_BASE_DELAY` 和 `FEISHU_LISTENER_RETRY_MAX_DELAY` 控制长连接监听异常退出后的自动重连退避。

`FEISHU_MESSAGE_LEASE_SECONDS` 控制可靠读取模式下消息租约时长。租约过期且未确认的消息会重新出现在 `recv_unread` 结果里。

`FEISHU_DELIVERED_RETENTION_DAYS` 和 `FEISHU_PROCESSED_RETENTION_DAYS` 控制启动和手动清理时保留已投递消息、去重记录的天数。

`FEISHU_IO_HOST`、`FEISHU_IO_PORT` 和 `FEISHU_IO_LOG_LEVEL` 控制服务端监听地址、端口和日志级别。

如果想把 REST 服务和飞书长连接监听拆成两个进程，可以这样：

```bash
FEISHU_IO_ENABLE_WS=false uvicorn feishu_io.server:app --host 0.0.0.0 --port 8000
feishu-io-listener
```

单机部署建议只跑一个长连接监听进程。REST 服务内置的监听器会防止同一进程内重复启动，并在服务关闭时关闭底层长连接和监听线程。多实例同时连接时，飞书会把同一个事件随机投递给其中一个连接。

## API 认证

`/send_markdown` 和 `/recv_unread` 都需要 API key。两种写法任选一种：

```bash
X-API-Key: change-this-api-key
```

或：

```bash
Authorization: Bearer change-this-api-key
```

## Client 单命令调用

在需要调用消息的机器上，只安装 client：

```bash
./scripts/install-client.sh
```

脚本把隔离环境安装到 `~/.local/share/feishu-io-client`，并创建 `~/.local/bin/feishu-ioctl`。也可以直接使用 `pipx install .` 或 `pip install .`；默认安装不包含任何 Server 依赖。

首次配置一次 URL 和 key。用 stdin 可以避免 key 出现在 shell history 中：

```bash
printf '%s' 'change-this-api-key' | \
  feishu-ioctl configure https://feishu-io.example.com --key-stdin
feishu-ioctl ready
```

配置默认保存在 `~/.config/feishu-io/client.json`，权限为 `0600`。`FEISHU_IO_URL`、`FEISHU_IO_API_KEY` 或命令行 `--url`、`--key` 可以临时覆盖它；`FEISHU_IO_CONFIG` 可以指定另一份配置文件。

发送消息只需要一条命令：

```bash
feishu-ioctl send test '**训练完成**'
cat report.md | feishu-ioctl send test -
```

立即读取当前未读消息：

```bash
feishu-ioctl recv test
```

等待最多 60 秒，适合 agent 等待下一条指令：

```bash
feishu-ioctl recv test --wait 60
```

需要“处理成功后才删除”的无人值守任务，应使用租约模式：

```bash
feishu-ioctl recv test --wait 60 --no-ack
feishu-ioctl ack test LEASE_TOKEN 1 2 3
```

所有命令输出 JSON，失败时返回非零退出码并把错误写到 stderr，便于 agent 或 shell 脚本判断。

Python Client 使用相同配置文件，也可以显式传参：

```python
from feishu_io import FeishuIO

bot = FeishuIO()  # 读取已保存的 client 配置
bot.send_markdown("**训练完成**\n\n结果已写入 `runs/latest`。", "test")

messages = bot.recv_unread("test")
for message in messages:
    print(message["text"])
```

或者显式传入：

```python
bot = FeishuIO("https://feishu-io.example.com", api_key="change-this-api-key")
```

`LEASE_TOKEN` 是 `recv --no-ack` 返回的消息中的 `lease_token`。它只对当前租约有效，租约过期或消息被重新租出后不能再确认。

健康检查：

```bash
feishu-ioctl ready
```

## 发送 Markdown

```bash
curl -X POST http://127.0.0.1:8000/send_markdown \
  -H "X-API-Key: change-this-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"test","text":"**训练完成**\n\n结果已写入 `runs/latest`。"}'
```

返回：

```json
{
  "ok": true,
  "id": "test",
  "feishu_code": 0,
  "message": "success"
}
```

如果 `id` 还没绑定，会返回 `404`，并提示你在群里发送 `/bind id`。

## 读取未读消息

```bash
curl -X POST http://127.0.0.1:8000/recv_unread \
  -H "X-API-Key: change-this-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"test","limit":100}'
```

返回：

```json
{
  "ok": true,
  "id": "test",
  "messages": [
    {
      "message_id": 1,
      "external_message_id": "om_xxx",
      "id": "test",
      "sender_id": "ou_xxx",
      "sender_name": "user",
      "message_type": "text",
      "text": "hello",
      "raw": {},
      "created_at": "2026-06-25 10:00:00",
      "lease_token": null
    }
  ]
}
```

`recv_unread` 会把返回的消息标记为本地已读；同一批消息不会在下一次调用里重复返回。默认还会给飞书原消息添加 `Get` reaction，方便群里用户知道这条消息已经被下游应用取走。

如果下游程序需要更可靠的无人值守读取，可以传 `ack=false`：

```bash
curl -X POST http://127.0.0.1:8000/recv_unread \
  -H "X-API-Key: change-this-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"test","limit":100,"ack":false}'
```

这时消息只会被临时租出，不会立刻标记为已读。每条返回消息都包含本批次的 `lease_token`。下游处理成功后使用同一个令牌调用：

```bash
curl -X POST http://127.0.0.1:8000/ack_messages \
  -H "X-API-Key: change-this-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"test","message_ids":[1,2,3],"lease_token":"0123456789abcdef0123456789abcdef"}'
```

如果下游崩溃，没有及时确认，租约过期后这些消息会再次返回，并生成新的租约令牌。领取和确认都在 SQLite 单条原子更新中完成，因此多个消费者不会领取或确认彼此的租约。

## 健康检查和维护

`GET /health` 是浅健康检查，只表示 HTTP 进程仍能响应。

`GET /ready` 会检查 SQLite，并在启用内置长连接监听时检查飞书 WebSocket 是否真实连接；仅有重试线程存活不会被视为就绪。无人值守部署建议把 `/ready` 接到外部监控。

可以手动清理历史数据：

```bash
curl -X POST http://127.0.0.1:8000/maintenance/cleanup \
  -H "X-API-Key: change-this-api-key"
```

服务启动时也会按保留天数自动清理一次已投递消息和去重记录。

## 消息监听

飞书会通过长连接把消息事件推给本服务。本服务会处理两类消息：

- `/bind alias`：绑定当前群。
- 其他文本消息：按稳定的飞书 `chat_id` 写入未读队列，读取时再解析 alias。

如果某个群还没有绑定，消息会按原始 `chat_id` 暂存；之后完成绑定或更换 alias，不会丢失已经排队的消息。

项目仍保留 `POST /feishu/events` 作为兼容入口；如果以后你有公网域名，也可以切回传统 HTTP 回调。该入口只在配置了 `FEISHU_EVENT_VERIFY_TOKEN` 时启用，并由飞书 SDK 统一完成 token、签名和加密载荷校验。没有公网 IP 时，只需要长连接模式。
