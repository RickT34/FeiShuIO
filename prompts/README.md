# Codex 飞书控制提示词

`AGENTS.md` 是可复制到其他项目或服务器的通用模板。它只定义 Codex 的自主执行和飞书通信协议，不包含机器路径、群别名或密钥。

## 1. 安装模板

Codex 只会自动发现项目目录层级中的 `AGENTS.md`。`prompts/AGENTS.md` 在本仓库中只是模板；使用时应复制到目标项目根目录：

```bash
cp /path/to/FeiShuIO/prompts/AGENTS.md /path/to/target-project/AGENTS.md
```

如果目标项目已经有 `AGENTS.md`，应合并相关章节，不要直接覆盖现有项目规则。

## 2. 配置飞书 Client

在目标服务器安装或克隆 FeiShuIO Client，并配置 Server URL 和 API key。使用仓库启动器时：

```bash
cd /path/to/FeiShuIO
printf '%s' 'your-api-key' | \
  ./scripts/client.sh configure https://feishu-io.example.com --key-stdin
./scripts/client.sh ready
```

在目标飞书群中发送 `/bind alias`，其中 `alias` 应与下一步的 `FEISHU_AGENT_ID` 相同。

## 3. 启动 Codex

推荐为每台服务器创建一个本地启动包装器，例如 `~/.local/bin/codex-feishu`：

```sh
#!/bin/sh
set -eu

export FEISHU_AGENT_ID="gpu-a"
export FEISHU_IO_CLIENT="/path/to/FeiShuIO/scripts/client.sh"

exec codex "$@"
```

赋予执行权限后，通过这个命令启动 Codex：

```bash
chmod 700 ~/.local/bin/codex-feishu
codex-feishu
```

不同服务器只需使用不同的 `FEISHU_AGENT_ID` 和 `FEISHU_IO_CLIENT`。如果已经把 `feishu-ioctl` 安装到 `PATH`，也可以设置：

```bash
export FEISHU_IO_CLIENT="feishu-ioctl"
```

对于 systemd、容器或其他守护进程，应在对应服务的环境配置中设置这两个变量。只写入交互式 shell 的配置文件不一定会被后台启动的 Codex 继承。

## 安全边界

- 不要把 API key 写入 `AGENTS.md`、启动包装器或版本库。
- 使用 FeiShuIO Client 的本地配置保存 Server URL 和 API key。
- 使用仅包含可信控制者和机器人的专用飞书群。
- 每台服务器使用独立 alias，避免多个 Codex 实例消费同一个消息队列。
