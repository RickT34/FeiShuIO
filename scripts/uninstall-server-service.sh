#!/bin/sh
set -eu

UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
UNIT_FILE="$UNIT_DIR/message-io.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is not available." >&2
  exit 1
fi

systemctl --user disable --now message-io.service 2>/dev/null || true
rm -f "$UNIT_FILE"
systemctl --user daemon-reload
systemctl --user reset-failed message-io.service 2>/dev/null || true
echo "MessageIO server service removed. Data and .env were kept."
