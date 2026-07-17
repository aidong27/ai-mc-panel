#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_AI_CONFIG:-} != YES ]]; then
    echo "Refusing AI configuration without MC_PANEL_APPROVE_AI_CONFIG=YES." >&2
    exit 1
fi
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 API_BASE_URL MODEL" >&2
    exit 1
fi

BASE_URL=${1%/}
MODEL=$2
ENV_FILE=/etc/mc-panel/panel.env
STATE=/var/backups/mc-panel
STAMP=$(date +%Y%m%dT%H%M%S)
BACKUP=$STATE/panel.env.before-ai.$STAMP

[[ $BASE_URL == https://* && $BASE_URL =~ ^[A-Za-z0-9._:/-]+$ ]] || {
    echo "API Base URL must be a simple HTTPS URL without credentials or query text." >&2
    exit 1
}
[[ $MODEL =~ ^[A-Za-z0-9._:/-]{1,120}$ ]] || {
    echo "Model name is invalid." >&2
    exit 1
}
[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || {
    echo "Panel environment file is missing or unsafe." >&2
    exit 1
}
[[ $(stat -c '%U:%G:%a' "$ENV_FILE") == root:mc-panel:640 ]] || {
    echo "Panel environment ownership or mode is unsafe." >&2
    exit 1
}

read -r -s -p "AI API key: " API_KEY
printf '\n'
[[ $API_KEY =~ ^[A-Za-z0-9._-]{20,256}$ ]] || {
    unset API_KEY
    echo "API key format is invalid." >&2
    exit 1
}

install -d -o root -g root -m 0700 "$STATE"
cp -a "$ENV_FILE" "$BACKUP"
chown root:root "$BACKUP"
chmod 0600 "$BACKUP"

TEMPORARY=$(mktemp --tmpdir="$STATE" panel.env.XXXXXX)
cleanup() {
    unset API_KEY
    rm -f "$TEMPORARY"
}
trap cleanup EXIT

seen_enabled=false
seen_url=false
seen_key=false
seen_model=false
while IFS= read -r line || [[ -n $line ]]; do
    case $line in
        MC_PANEL_AI_ENABLED=*)
            printf 'MC_PANEL_AI_ENABLED=true\n'
            seen_enabled=true
            ;;
        MC_PANEL_AI_API_BASE_URL=*)
            printf 'MC_PANEL_AI_API_BASE_URL=%s\n' "$BASE_URL"
            seen_url=true
            ;;
        MC_PANEL_AI_API_KEY=*)
            printf 'MC_PANEL_AI_API_KEY=%s\n' "$API_KEY"
            seen_key=true
            ;;
        MC_PANEL_AI_MODEL=*)
            printf 'MC_PANEL_AI_MODEL=%s\n' "$MODEL"
            seen_model=true
            ;;
        *) printf '%s\n' "$line" ;;
    esac
done <"$ENV_FILE" >"$TEMPORARY"

[[ $seen_enabled == true ]] || printf 'MC_PANEL_AI_ENABLED=true\n' >>"$TEMPORARY"
[[ $seen_url == true ]] || printf 'MC_PANEL_AI_API_BASE_URL=%s\n' "$BASE_URL" >>"$TEMPORARY"
[[ $seen_key == true ]] || printf 'MC_PANEL_AI_API_KEY=%s\n' "$API_KEY" >>"$TEMPORARY"
[[ $seen_model == true ]] || printf 'MC_PANEL_AI_MODEL=%s\n' "$MODEL" >>"$TEMPORARY"

install -o root -g mc-panel -m 0640 "$TEMPORARY" "$ENV_FILE"
unset API_KEY
systemctl restart mc-panel.service
PANEL_PORT=$(awk -F= '$1 == "MC_PANEL_PORT" {gsub(/^"|"$/, "", $2); print $2; exit}' "$ENV_FILE")
PANEL_PORT=${PANEL_PORT:-18080}

health_ready=false
for _ in {1..30}; do
    if curl --fail --silent "http://127.0.0.1:$PANEL_PORT/api/v1/health" >/dev/null 2>&1; then
        health_ready=true
        break
    fi
    sleep 1
done
if [[ $health_ready != true ]]; then
    cp -a "$BACKUP" "$ENV_FILE"
    chown root:mc-panel "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    systemctl restart mc-panel.service
    echo "AI configuration failed; the previous environment was restored." >&2
    exit 1
fi

echo "AI provider configured; API key was not printed."
echo "Previous environment: $BACKUP"
