#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
UNIT_FILE="$UNIT_DIR/feishu-io.service"
TEMP_FILE="$UNIT_FILE.tmp.$$"
SERVICE_USER=${USER:-}
if [ -z "$SERVICE_USER" ]; then
  SERVICE_USER=$(id -un)
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is not available; run $PROJECT_ROOT/scripts/run-server.sh directly." >&2
  exit 1
fi
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  echo "Missing $PROJECT_ROOT/.env; copy .env.example and fill in the server settings." >&2
  exit 1
fi
if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "The systemd user manager is unavailable for $SERVICE_USER." >&2
  echo "Enable it with 'sudo loginctl enable-linger $SERVICE_USER', log in again, then retry." >&2
  exit 1
fi

chmod 600 "$PROJECT_ROOT/.env"
FEISHU_IO_FORCE_INSTALL=1 "$PROJECT_ROOT/scripts/run-server.sh" --help >/dev/null

mkdir -p "$UNIT_DIR"
trap 'rm -f "$TEMP_FILE"' EXIT HUP INT TERM
cat >"$TEMP_FILE" <<EOF
[Unit]
Description=FeiShuIO persistent message bridge

[Service]
Type=simple
WorkingDirectory="$PROJECT_ROOT"
EnvironmentFile="$PROJECT_ROOT/.env"
ExecStart="$PROJECT_ROOT/scripts/run-server.sh"
Restart=always
RestartSec=5
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF
chmod 600 "$TEMP_FILE"
mv "$TEMP_FILE" "$UNIT_FILE"
trap - EXIT HUP INT TERM

systemctl --user daemon-reload
systemctl --user enable --now feishu-io.service

echo "FeiShuIO server service installed and started."
echo "Status: systemctl --user status feishu-io.service"
echo "Logs:   journalctl --user -u feishu-io.service -f"
echo "For startup without an active login session, run once:"
echo "  sudo loginctl enable-linger $SERVICE_USER"
