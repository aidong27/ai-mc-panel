#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_ROLLBACK:-} != YES ]]; then
    echo "Refusing rollback. Set MC_PANEL_APPROVE_ROLLBACK=YES after reviewing the target." >&2
    exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "Usage: $0 VERSION" >&2
    exit 1
fi

VERSION=$1
TARGET=/opt/mc-panel/releases/$VERSION
CURRENT=/opt/mc-panel/current
STATE=/var/backups/mc-panel
STAMP=$(date +%Y%m%dT%H%M%S)
ROLLBACK_STATE=$STATE/rollback-$STAMP
OLD_RELEASE=$(readlink -f "$CURRENT")
ENV_FILE=/etc/mc-panel/panel.env
HELPER_CONFIG=/etc/mc-panel/helper.json

[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || {
    echo "Panel environment is missing or unsafe." >&2
    exit 1
}
[[ -f $HELPER_CONFIG && ! -L $HELPER_CONFIG ]] || {
    echo "Helper runtime profile is missing or unsafe." >&2
    exit 1
}
[[ $(stat -c '%U:%G:%a' "$ENV_FILE") == root:mc-panel:640 ]] || {
    echo "Panel environment ownership or mode is unsafe." >&2
    exit 1
}
[[ $(stat -c '%U:%G:%a' "$HELPER_CONFIG") == root:root:600 ]] || {
    echo "Helper runtime profile ownership or mode is unsafe." >&2
    exit 1
}
mapfile -t RUNTIME < <(python3 - "$HELPER_CONFIG" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data["server_service"])
PY
)
SERVER_SERVICE=${RUNTIME[0]:-minecraft.service}
PANEL_PORT=$(awk -F= '$1 == "MC_PANEL_PORT" {gsub(/^"|"$/, "", $2); print $2; exit}' "$ENV_FILE")
PANEL_PORT=${PANEL_PORT:-18080}

[[ -d $TARGET && -f $TARGET/apps/web/dist/index.html ]] || {
    echo "Rollback target is incomplete." >&2
    exit 1
}
[[ -f $TARGET/helper/mc_panel_action.py ]] || { echo "Target helper is missing." >&2; exit 1; }
[[ $TARGET != "$OLD_RELEASE" ]] || { echo "Target release is already active." >&2; exit 1; }
systemctl is-active --quiet "$SERVER_SERVICE" || { echo "Minecraft is not active." >&2; exit 1; }

install -d -o root -g root -m 0700 "$ROLLBACK_STATE"
cp -a /usr/local/libexec/mc-panel-action "$ROLLBACK_STATE/helper"
cp -a /etc/systemd/system/mc-panel.service "$ROLLBACK_STATE/mc-panel.service"
printf '%s\n' "$OLD_RELEASE" >"$ROLLBACK_STATE/previous-release.txt"

/opt/mc-panel/current/deploy/scripts/disable-writes.sh

undo() {
    ln -s "$OLD_RELEASE" "$CURRENT.undo"
    mv -Tf "$CURRENT.undo" "$CURRENT"
    install -o root -g root -m 0755 "$ROLLBACK_STATE/helper" /usr/local/libexec/mc-panel-action
    install -o root -g root -m 0644 "$ROLLBACK_STATE/mc-panel.service" \
        /etc/systemd/system/mc-panel.service
    systemctl daemon-reload
    systemctl restart mc-panel.service || true
}
trap undo ERR

install -o root -g root -m 0755 "$TARGET/helper/mc_panel_action.py" \
    /usr/local/libexec/mc-panel-action
install -o root -g root -m 0644 "$TARGET/deploy/systemd/mc-panel.service" \
    /etc/systemd/system/mc-panel.service
ln -s "$TARGET" "$CURRENT.new"
mv -Tf "$CURRENT.new" "$CURRENT"
systemctl daemon-reload
systemctl restart mc-panel.service
curl --fail --silent --show-error "http://127.0.0.1:$PANEL_PORT/api/v1/health" >/dev/null
systemctl is-active --quiet "$SERVER_SERVICE"
trap - ERR

echo "Panel rolled back to $VERSION with production writes disabled."
echo "Minecraft was not restarted."
