#!/usr/bin/env bash
set -Eeuo pipefail

failures=0
ENV_FILE=/etc/mc-panel/panel.env
HELPER_CONFIG=/etc/mc-panel/helper.json
if [[ -f $HELPER_CONFIG && ! -L $HELPER_CONFIG ]]; then
    SERVER_SERVICE=$(python3 - "$HELPER_CONFIG" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["server_service"])
PY
)
else
    SERVER_SERVICE=minecraft.service
fi
PANEL_PORT=$(awk -F= '$1 == "MC_PANEL_PORT" {gsub(/^"|"$/, "", $2); print $2; exit}' "$ENV_FILE")
PANEL_PORT=${PANEL_PORT:-18080}
check() {
    local label=$1
    shift
    if "$@"; then
        printf 'PASS %s\n' "$label"
    else
        printf 'FAIL %s\n' "$label"
        failures=$((failures + 1))
    fi
}

check minecraft_active systemctl is-active --quiet "$SERVER_SERVICE"
check panel_active systemctl is-active --quiet mc-panel.service
check panel_enabled systemctl is-enabled --quiet mc-panel.service
check health curl --fail --silent "http://127.0.0.1:$PANEL_PORT/api/v1/health"
check env_owner test "$(stat -c '%U:%G' /etc/mc-panel/panel.env)" = root:mc-panel
check env_mode test "$(stat -c '%a' /etc/mc-panel/panel.env)" = 640
check helper_owner test "$(stat -c '%U:%G' /usr/local/libexec/mc-panel-action)" = root:root
check helper_mode test "$(stat -c '%a' /usr/local/libexec/mc-panel-action)" = 755
check helper_config_owner test "$(stat -c '%U:%G' "$HELPER_CONFIG")" = root:root
check helper_config_mode test "$(stat -c '%a' "$HELPER_CONFIG")" = 600
check frontend test -f /opt/mc-panel/current/apps/web/dist/index.html
check database_parent test -d /var/lib/mc-panel

listeners=$(ss -ltnH "sport = :$PANEL_PORT" || true)
if [[ -n $listeners ]] && ! grep -Evq "127\\.0\\.0\\.1:$PANEL_PORT|\\[::1\\]:$PANEL_PORT" <<<"$listeners"; then
    printf 'PASS loopback_only\n'
else
    printf 'FAIL loopback_only\n'
    failures=$((failures + 1))
fi

if grep -RIEq 'sk-[A-Za-z0-9_-]{12,}' /opt/mc-panel/current/apps/web/dist; then
    printf 'FAIL frontend_secret_scan\n'
    failures=$((failures + 1))
else
    printf 'PASS frontend_secret_scan\n'
fi

exit "$failures"
