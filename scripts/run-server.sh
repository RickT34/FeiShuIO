#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
VENV_DIR=${FEISHU_IO_SERVER_VENV:-"$PROJECT_ROOT/.server-venv"}

if [ ! -f "$PROJECT_ROOT/.env" ]; then
  echo "Missing $PROJECT_ROOT/.env; copy .env.example and fill in the server settings." >&2
  exit 1
fi

if [ "${FEISHU_IO_FORCE_INSTALL:-0}" = "1" ] \
  || [ ! -x "$VENV_DIR/bin/feishu-io-server" ] \
  || ! "$VENV_DIR/bin/python" -c \
    "import fastapi, lark_oapi, pydantic_settings, uvicorn" >/dev/null 2>&1; then
  "$PYTHON" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install -e "$PROJECT_ROOT[server]"
fi

mkdir -p "$PROJECT_ROOT/data"
cd "$PROJECT_ROOT"
exec "$VENV_DIR/bin/feishu-io-server" "$@"
