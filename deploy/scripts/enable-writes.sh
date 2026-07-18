#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_WRITES:-} != YES ]]; then
    echo "Refusing write enablement. Set MC_PANEL_APPROVE_WRITES=YES after explicit approval." >&2
    exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[a-f0-9]{64}$ ]]; then
    echo "Usage: $0 APPROVED_FINGERPRINT" >&2
    exit 1
fi

APPROVED=$1
CURRENT=$(/opt/mc-panel/current/deploy/scripts/fingerprint.sh)
ENV_FILE=/etc/mc-panel/panel.env
HELPER_CONFIG=/etc/mc-panel/helper.json
STATE=/var/backups/mc-panel
STAMP=$(date +%Y%m%dT%H%M%S)
CREATED_DIRS=()

[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || {
    echo "Panel environment file is missing or unsafe." >&2
    exit 1
}
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
    "backup_archive_glob",
    "recovery_root",
    "timer_dropin",
    "backup_service_dropin",
):
    print(data[key])
PY
)
[[ ${#RUNTIME[@]} -eq 7 ]] || { echo "Runtime profile could not be read." >&2; exit 1; }
SERVER_ROOT=${RUNTIME[0]}
BACKUP_ROOT=${RUNTIME[1]}
SERVER_SERVICE=${RUNTIME[2]}
BACKUP_GLOB=${RUNTIME[3]}
RECOVERY_ROOT=${RUNTIME[4]}
TIMER_DROPIN=${RUNTIME[5]}
BACKUP_SERVICE_DROPIN=${RUNTIME[6]}
PANEL_PORT=$(awk -F= '$1 == "MC_PANEL_PORT" {gsub(/^"|"$/, "", $2); print $2; exit}' "$ENV_FILE")
PANEL_PORT=${PANEL_PORT:-18080}

[[ $APPROVED == "$CURRENT" ]] || { echo "Fingerprint changed; rerun read-only audit." >&2; exit 1; }
systemctl is-active --quiet "$SERVER_SERVICE" || { echo "Minecraft is not active." >&2; exit 1; }
LATEST_BACKUP=$(find "$BACKUP_ROOT" -maxdepth 1 -type f \
    -name "$BACKUP_GLOB" -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)
[[ -n $LATEST_BACKUP ]] || { echo "No cold backup is available." >&2; exit 1; }
CHECKSUM_FILE=$LATEST_BACKUP.sha256
[[ -f $CHECKSUM_FILE ]] || { echo "Latest cold backup has no checksum sidecar." >&2; exit 1; }
EXPECTED_SUM=$(awk 'NR == 1 { print $1 }' "$CHECKSUM_FILE")
[[ $EXPECTED_SUM =~ ^[a-fA-F0-9]{64}$ ]] || { echo "Invalid backup checksum." >&2; exit 1; }
ACTUAL_SUM=$(sha256sum "$LATEST_BACKUP" | awk '{ print $1 }')
[[ ${EXPECTED_SUM,,} == "$ACTUAL_SUM" ]] || { echo "Latest cold backup checksum failed." >&2; exit 1; }

install -d -o root -g root -m 0755 /etc/systemd/system/mc-panel.service.d
cp -a "$ENV_FILE" "$STATE/panel.env.before-writes.$STAMP"
chmod 0600 "$STATE/panel.env.before-writes.$STAMP"

rewrite_env() {
    local temporary
    temporary=$(mktemp)
    awk -v fingerprint="$APPROVED" '
        BEGIN { seen_fp=0; seen_writes=0 }
        /^MC_PANEL_APPROVED_FINGERPRINT=/ {
            print "MC_PANEL_APPROVED_FINGERPRINT=" fingerprint; seen_fp=1; next
        }
        /^MC_PANEL_PRODUCTION_WRITES_ENABLED=/ {
            print "MC_PANEL_PRODUCTION_WRITES_ENABLED=true"; seen_writes=1; next
        }
        { print }
        END {
            if (!seen_fp) print "MC_PANEL_APPROVED_FINGERPRINT=" fingerprint
            if (!seen_writes) print "MC_PANEL_PRODUCTION_WRITES_ENABLED=true"
        }
    ' "$ENV_FILE" >"$temporary"
    install -o root -g mc-panel -m 0640 "$temporary" "$ENV_FILE"
    rm -f "$temporary"
}

rollback() {
    cp -a "$STATE/panel.env.before-writes.$STAMP" "$ENV_FILE"
    chown root:mc-panel "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    rm -f /etc/sudoers.d/mc-panel
    rm -f /etc/systemd/system/mc-panel.service.d/write-access.conf
    rm -f /etc/systemd/system/mc-panel.service.d/runtime-paths.conf
    for directory in "${CREATED_DIRS[@]}"; do
        rmdir "$directory" 2>/dev/null || true
    done
    systemctl daemon-reload
    systemctl restart mc-panel.service || true
}
trap rollback ERR

for directory in \
    "$RECOVERY_ROOT" \
    "$(dirname "$TIMER_DROPIN")" \
    "$(dirname "$BACKUP_SERVICE_DROPIN")"; do
    if [[ -L $directory || (-e $directory && ! -d $directory) ]]; then
        echo "Unsafe helper directory: $directory" >&2
        exit 1
    fi
    if [[ -d $directory ]]; then
        [[ $(stat -c '%U' "$directory") == root ]] || {
            echo "Helper directory is not root-owned: $directory" >&2
            exit 1
        }
        directory_mode=$(stat -c '%a' "$directory")
        [[ $directory_mode =~ ^[0-7]{3,4}$ ]] || {
            echo "Helper directory mode is invalid: $directory" >&2
            exit 1
        }
        (( (8#$directory_mode & 8#022) == 0 )) || {
            echo "Helper directory is group/world writable: $directory" >&2
            exit 1
        }
        continue
    fi
    mode=0755
    [[ $directory != "$RECOVERY_ROOT" ]] || mode=0700
    install -d -o root -g root -m "$mode" "$directory"
    CREATED_DIRS+=("$directory")
done

rewrite_env
install -o root -g root -m 0440 /opt/mc-panel/current/deploy/sudoers/mc-panel \
    /etc/sudoers.d/mc-panel
visudo -cf /etc/sudoers.d/mc-panel
install -o root -g root -m 0644 \
    /opt/mc-panel/current/deploy/systemd/mc-panel-write-access.conf \
    /etc/systemd/system/mc-panel.service.d/write-access.conf
RUNTIME_PATHS=$(mktemp)
{
    echo "[Service]"
    echo "ReadWritePaths=-/run/lock"
    printf 'ReadWritePaths=-%s\n' "$SERVER_ROOT"
    printf 'ReadWritePaths=-%s\n' "$BACKUP_ROOT"
    printf 'ReadWritePaths=-%s\n' "$RECOVERY_ROOT"
    printf 'ReadWritePaths=-%s\n' "$(dirname "$TIMER_DROPIN")"
    printf 'ReadWritePaths=-%s\n' "$(dirname "$BACKUP_SERVICE_DROPIN")"
} >"$RUNTIME_PATHS"
install -o root -g root -m 0644 "$RUNTIME_PATHS" \
    /etc/systemd/system/mc-panel.service.d/runtime-paths.conf
rm -f "$RUNTIME_PATHS"
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
trap - ERR

echo "Constrained production writes enabled for fingerprint $CURRENT"
echo "Minecraft was not restarted."
