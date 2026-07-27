#!/bin/sh
set -u
payload="$(cat)"
status="$(printf '%s' "$payload" | jq -r '.tool_input.status // empty')"

case "$status" in
  complete)
    title="Codex Goal completed"
    ;;
  blocked)
    title="Codex Goal blocked"
    ;;
  *)
    exit 0
    ;;
esac

cwd="$(printf '%s' "$payload" | jq -r '.cwd // ""')"

"$FEISHU_IO_CLIENT" send "$FEISHU_AGENT_ID" "**$title** in $cwd" >/dev/null