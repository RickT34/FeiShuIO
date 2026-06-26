# FeiShuIO

一个长期运行的极简 REST 服务，用飞书应用机器人把其他项目接入飞书群。

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

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```env
FEISHU_IO_API_KEY=change-this-api-key
FEISHU_IO_DB=./data/feishu_io.sqlite3
FEISHU_IO_ENABLE_WS=true
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
FEISHU_EVENT_VERIFY_TOKEN=xxxx
FEISHU_EVENT_ENCRYPT_KEY=
FEISHU_MARK_READ_REACTION=true
FEISHU_READ_REACTION_EMOJI=OK
```

`FEISHU_IO_API_KEY` 是本服务业务接口的访问 key，用来防止信息泄露。

`FEISHU_IO_ENABLE_WS=true` 表示启动 REST 服务时自动启动飞书长连接监听。大多数单机部署保持默认即可。

`FEISHU_MARK_READ_REACTION=true` 表示 `recv_unread` 成功取走缓存消息后，会给飞书原消息添加一个 reaction，默认 emoji 类型是 `OK`。如果飞书侧权限或 emoji 类型不支持，接口仍会返回消息，只在服务日志里记录失败原因。

## 启动

```bash
uvicorn feishu_io.server:app --host 0.0.0.0 --port 8000
```

也可以使用入口命令：

```bash
feishu-io
```

如果想把 REST 服务和飞书长连接监听拆成两个进程，可以这样：

```bash
FEISHU_IO_ENABLE_WS=false uvicorn feishu_io.server:app --host 0.0.0.0 --port 8000
feishu-io-listener
```

单机部署建议只跑一个长连接监听进程。REST 服务内置的监听器会防止同一进程内重复启动，并在服务关闭时尝试关闭底层长连接客户端。多实例同时连接时，飞书会把同一个事件随机投递给其中一个连接。

## API 认证

`/send_markdown` 和 `/recv_unread` 都需要 API key。两种写法任选一种：

```bash
X-API-Key: change-this-api-key
```

或：

```bash
Authorization: Bearer change-this-api-key
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
      "created_at": "2026-06-25 10:00:00"
    }
  ]
}
```

`recv_unread` 会把返回的消息标记为本地已读；同一批消息不会在下一次调用里重复返回。默认还会给飞书原消息添加 `OK` reaction，方便群里用户知道这条消息已经被下游应用取走。

## 消息监听

飞书会通过长连接把消息事件推给本服务。本服务会处理两类消息：

- `/bind alias`：绑定当前群。
- 其他文本消息：写入该群 alias 对应的未读队列。

如果某个群还没有绑定，消息会按原始 `chat_id` 暂存，但业务接口仍要求使用已绑定 alias。

项目仍保留 `POST /feishu/events` 作为兼容入口；如果以后你有公网域名，也可以切回传统 HTTP 回调。但没有公网 IP 时，只需要长连接模式。
