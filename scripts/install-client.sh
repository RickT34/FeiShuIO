#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_DIR=${FEISHU_IO_BIN_DIR:-"$HOME/.local/bin"}
VENV_DIR=${FEISHU_IO_CLIENT_VENV:-"$DATA_HOME/feishu-io-client"}

"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade "$PROJECT_ROOT"
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/feishu-ioctl" "$BIN_DIR/feishu-ioctl"

echo "Installed client command: $BIN_DIR/feishu-ioctl"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to PATH before calling feishu-ioctl." ;;
esac
