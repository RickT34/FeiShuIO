#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
VENV_DIR="$PROJECT_ROOT/.client-venv"
CONFIG_DIR="$PROJECT_ROOT/.client"
CONFIG_FILE="$CONFIG_DIR/client.json"
CACHE_DIR="$CONFIG_DIR/cache"

for argument in "$@"; do
  case "$argument" in
    --|send|recv|ack|health|ready|cleanup|configure|config)
      break
      ;;
    --config|--config=*)
      echo "client config is managed inside the repository: $CONFIG_FILE" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$CONFIG_DIR" "$CACHE_DIR"
chmod 700 "$CONFIG_DIR" "$CACHE_DIR"
export FEISHU_IO_CONFIG="$CONFIG_FILE"
export XDG_CACHE_HOME="$CACHE_DIR"
export PIP_CACHE_DIR="$CACHE_DIR/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1

if [ "${FEISHU_IO_FORCE_INSTALL:-0}" = "1" ] \
  || [ ! -x "$VENV_DIR/bin/feishu-ioctl" ] \
  || ! "$VENV_DIR/bin/python" -c "import httpx" >/dev/null 2>&1; then
  "$PYTHON" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install -e "$PROJECT_ROOT"
fi

exec "$VENV_DIR/bin/feishu-ioctl" "$@"
