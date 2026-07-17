#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[A-Za-z0-9_-]{3,64}$ ]]; then
    echo "Usage: $0 USERNAME" >&2
    exit 1
fi

ENV_FILE=/etc/mc-panel/panel.env
[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || {
    echo "Panel environment file is missing or unsafe." >&2
    exit 1
}
[[ $(stat -c '%U:%G:%a' "$ENV_FILE") == root:mc-panel:640 ]] || {
    echo "Panel environment ownership or mode is unsafe." >&2
    exit 1
}
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
[[ -n ${MC_PANEL_ENV:-} && -n ${MC_PANEL_SECRET_KEY:-} && -n ${MC_PANEL_DATABASE_PATH:-} ]] || {
    echo "Required panel environment values are missing." >&2
    exit 1
}
export MC_PANEL_ENV MC_PANEL_SECRET_KEY MC_PANEL_DATABASE_PATH

sudo --preserve-env=MC_PANEL_ENV,MC_PANEL_SECRET_KEY,MC_PANEL_DATABASE_PATH \
    -u mc-panel /opt/mc-panel/current/.venv/bin/mc-panel-admin create-user "$1"
