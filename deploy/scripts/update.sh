#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_UPDATE:-} != YES ]]; then
    echo "Refusing update. Set MC_PANEL_APPROVE_UPDATE=YES after reviewing the release." >&2
    exit 1
fi
if [[ $# -ne 2 || ! $2 =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "Usage: $0 RELEASE_SOURCE VERSION" >&2
    exit 1
fi

SOURCE=$(realpath "$1")
VERSION=$2
RELEASE=/opt/mc-panel/releases/$VERSION
CURRENT=/opt/mc-panel/current
STATE=/var/backups/mc-panel
STAMP=$(date +%Y%m%dT%H%M%S)
UPDATE_STATE=$STATE/update-$STAMP
OLD_RELEASE=$(readlink -f "$CURRENT")
DATABASE=/var/lib/mc-panel/panel.db
ENV_FILE=/etc/mc-panel/panel.env
HELPER_CONFIG=/etc/mc-panel/helper.json
RUNTIME_PATHS=/etc/systemd/system/mc-panel.service.d/runtime-paths.conf
RUNTIME_PATHS_EXISTED=false

[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || { echo "Panel environment is unsafe." >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' "$ENV_FILE") == root:mc-panel:640 ]] || {
    echo "Panel environment ownership or mode is unsafe." >&2
    exit 1
}
[[ -f $HELPER_CONFIG && ! -L $HELPER_CONFIG ]] || {
    echo "Helper runtime profile is missing or unsafe." >&2
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
for key in (
    "server_root",
    "backup_root",
    "server_service",
    "recovery_root",
    "timer_dropin",
    "backup_service_dropin",
):
    print(data[key])
PY
)
[[ ${#RUNTIME[@]} -eq 6 ]] || { echo "Runtime profile could not be read." >&2; exit 1; }
SERVER_ROOT=${RUNTIME[0]}
BACKUP_ROOT=${RUNTIME[1]}
SERVER_SERVICE=${RUNTIME[2]}
RECOVERY_ROOT=${RUNTIME[3]}
TIMER_DROPIN=${RUNTIME[4]}
BACKUP_SERVICE_DROPIN=${RUNTIME[5]}
PANEL_PORT=$(awk -F= '$1 == "MC_PANEL_PORT" {gsub(/^"|"$/, "", $2); print $2; exit}' "$ENV_FILE")
PANEL_PORT=${PANEL_PORT:-18080}

[[ -n $OLD_RELEASE && -d $OLD_RELEASE ]] || { echo "Current release is invalid." >&2; exit 1; }
[[ ! -e $RELEASE ]] || { echo "Release already exists: $RELEASE" >&2; exit 1; }
[[ -f $SOURCE/SHA256SUMS ]] || { echo "Release checksum manifest is missing." >&2; exit 1; }
[[ -f $SOURCE/apps/web/dist/index.html ]] || { echo "Built frontend is missing." >&2; exit 1; }
[[ -f $SOURCE/helper/mc_panel_action.py ]] || { echo "Helper is missing." >&2; exit 1; }
systemctl is-active --quiet "$SERVER_SERVICE" || { echo "Minecraft is not active." >&2; exit 1; }
systemctl is-active --quiet mc-panel.service || { echo "Panel is not active." >&2; exit 1; }
(cd "$SOURCE" && sha256sum --check --strict SHA256SUMS >/dev/null)

mapfile -t API_WHEELS < <(find "$SOURCE/wheelhouse" -maxdepth 1 -type f \
    -name 'mc_panel_api-*.whl' -printf '%f\n')
[[ ${#API_WHEELS[@]} -eq 1 ]] || { echo "Expected exactly one API wheel." >&2; exit 1; }

install -d -o root -g root -m 0700 "$UPDATE_STATE"
cp -a /usr/local/libexec/mc-panel-action "$UPDATE_STATE/helper"
cp -a /etc/systemd/system/mc-panel.service "$UPDATE_STATE/mc-panel.service"
cp -a "$HELPER_CONFIG" "$UPDATE_STATE/helper.json"
if [[ -f $RUNTIME_PATHS ]]; then
    cp -a "$RUNTIME_PATHS" "$UPDATE_STATE/runtime-paths.conf"
    RUNTIME_PATHS_EXISTED=true
fi
printf '%s\n' "$OLD_RELEASE" >"$UPDATE_STATE/previous-release.txt"
if [[ -f $DATABASE ]]; then
    "$OLD_RELEASE/.venv/bin/python" - "$DATABASE" "$UPDATE_STATE/panel.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
destination = sqlite3.connect(sys.argv[2])
with destination:
    source.backup(destination)
destination.close()
source.close()
PY
    chmod 0600 "$UPDATE_STATE/panel.db"
    sha256sum "$UPDATE_STATE/panel.db" >"$UPDATE_STATE/panel.db.sha256"
fi

rollback() {
    systemctl stop mc-panel.service || true
    if [[ -f $UPDATE_STATE/panel.db ]]; then
        for suffix in '' -wal -shm; do
            if [[ -e $DATABASE$suffix ]]; then
                mv "$DATABASE$suffix" "$UPDATE_STATE/failed-panel.db$suffix"
            fi
        done
        install -o mc-panel -g mc-panel -m 0600 "$UPDATE_STATE/panel.db" "$DATABASE"
    fi
    ln -s "$OLD_RELEASE" "$CURRENT.rollback"
    mv -Tf "$CURRENT.rollback" "$CURRENT"
    install -o root -g root -m 0755 "$UPDATE_STATE/helper" /usr/local/libexec/mc-panel-action
    install -o root -g root -m 0644 "$UPDATE_STATE/mc-panel.service" \
        /etc/systemd/system/mc-panel.service
    if [[ $RUNTIME_PATHS_EXISTED == true ]]; then
        install -o root -g root -m 0644 "$UPDATE_STATE/runtime-paths.conf" "$RUNTIME_PATHS"
    else
        rm -f "$RUNTIME_PATHS"
    fi
    systemctl daemon-reload
    systemctl restart mc-panel.service || true
}
trap rollback ERR

install -d -o root -g root -m 0755 "$RELEASE"
cp -a "$SOURCE"/. "$RELEASE"/
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install --no-index \
    --find-links "$RELEASE/wheelhouse" "$RELEASE/wheelhouse/${API_WHEELS[0]}"
chown -R root:root "$RELEASE"
chmod -R go-w "$RELEASE"
install -o root -g root -m 0755 "$RELEASE/helper/mc_panel_action.py" \
    /usr/local/libexec/mc-panel-action
install -o root -g root -m 0644 "$RELEASE/deploy/systemd/mc-panel.service" \
    /etc/systemd/system/mc-panel.service
if [[ -f /etc/systemd/system/mc-panel.service.d/write-access.conf ]]; then
    temporary_paths=$(mktemp)
    {
        echo "[Service]"
        echo "ReadWritePaths=-/run/lock"
        printf 'ReadWritePaths=-%s\n' "$SERVER_ROOT"
        printf 'ReadWritePaths=-%s\n' "$BACKUP_ROOT"
        printf 'ReadWritePaths=-%s\n' "$RECOVERY_ROOT"
        printf 'ReadWritePaths=-%s\n' "$(dirname "$TIMER_DROPIN")"
        printf 'ReadWritePaths=-%s\n' "$(dirname "$BACKUP_SERVICE_DROPIN")"
    } >"$temporary_paths"
    install -o root -g root -m 0644 "$temporary_paths" "$RUNTIME_PATHS"
    rm -f "$temporary_paths"
fi
ln -s "$RELEASE" "$CURRENT.new"
mv -Tf "$CURRENT.new" "$CURRENT"
systemctl daemon-reload
systemctl restart mc-panel.service
health_ready=false
for _ in {1..30}; do
    if curl --fail --silent "http://127.0.0.1:$PANEL_PORT/api/v1/health" >/dev/null 2>&1; then
        health_ready=true
        break
    fi
    sleep 1
done
[[ $health_ready == true ]] || {
    echo "Panel health check did not become ready within 30 seconds." >&2
    false
}
systemctl is-active --quiet "$SERVER_SERVICE"
trap - ERR

echo "Panel updated to $VERSION. Minecraft was not restarted."
