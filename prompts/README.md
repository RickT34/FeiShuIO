# Codex MessageIO 控制提示词

`AGENTS.md` 是可复制到其他项目或服务器的通用模板。它只定义 Codex 的自主执行和 MessageIO 通信协议，不绑定飞书或其他具体平台，也不包含机器路径、target 或密钥。

## 1. 安装模板

```bash
cp /path/to/MessageIO/prompts/AGENTS.md /path/to/target-project/AGENTS.md
```

如果目标项目已有 `AGENTS.md`，应合并相关章节，不要覆盖已有项目规则。

## 2. 配置 Client

```bash
cd /path/to/MessageIO
printf '%s' 'your-api-key' | \
  ./scripts/client.sh configure https://message-io.example.com --key-stdin
./scripts/client.sh ready
```

在任一已接入平台的目标会话发送 `/bind alias`，其中 `alias` 应与下一步的 `MESSAGE_IO_TARGET` 相同。

## 3. 启动 Codex

为每台服务器创建本地包装器：

```sh
#!/bin/sh
set -eu

export MESSAGE_IO_TARGET="gpu-a"
export MESSAGE_IO_CLIENT="/path/to/MessageIO/scripts/client.sh"

exec codex "$@"
```

不同服务器使用不同的 `MESSAGE_IO_TARGET`。对于 systemd、容器或其他守护进程，应在对应服务环境中设置变量。

## 安全边界

- 不要把 API key 写入 `AGENTS.md`、启动包装器或版本库。
- 使用 MessageIO Client 的本地配置保存 Server URL 和 API key。
- target 对应的会话只应包含可信控制者和机器人。
- 每台服务器使用独立 target，避免多个 Agent 消费同一消息队列。
