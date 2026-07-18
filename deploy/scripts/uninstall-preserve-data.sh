#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_UNINSTALL:-} != YES ]]; then
    echo "Refusing uninstall. Set MC_PANEL_APPROVE_UNINSTALL=YES after reviewing retention." >&2
    exit 1
fi

STATE=/var/backups/mc-panel
STAMP=$(date +%Y%m%dT%H%M%S)
ARCHIVE=$STATE/uninstalled-$STAMP
ACL_STATE=$STATE/acl-before-install.txt
HELPER_CONFIG=/etc/mc-panel/helper.json

[[ ! -e $ARCHIVE ]] || { echo "Archive already exists." >&2; exit 1; }
[[ -f $HELPER_CONFIG && ! -L $HELPER_CONFIG ]] || {
    echo "Helper runtime profile is missing or unsafe." >&2
    exit 1
}
[[ $(stat -c '%U:%G:%a' "$HELPER_CONFIG") == root:root:600 ]] || {
    echo "Helper runtime profile ownership or mode is unsafe." >&2
    exit 1
}
SERVER_SERVICE=$(python3 - "$HELPER_CONFIG" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["server_service"])
PY
)
[[ $SERVER_SERVICE =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || {
    echo "Configured Minecraft service is invalid." >&2
    exit 1
}
systemctl is-active --quiet "$SERVER_SERVICE" || {
    echo "Minecraft is not active; refusing panel uninstall." >&2
    exit 1
}
install -d -o root -g root -m 0700 "$ARCHIVE"
systemctl disable --now mc-panel.service || true

move_if_present() {
    local source=$1
    local name=$2
    if [[ -e $source || -L $source ]]; then
        mv "$source" "$ARCHIVE/$name"
    fi
}

move_if_present /etc/sudoers.d/mc-panel sudoers-mc-panel
move_if_present /etc/systemd/system/mc-panel.service.d systemd-dropins
move_if_present /etc/systemd/system/mc-panel.service systemd-unit
move_if_present /usr/local/libexec/mc-panel-action privileged-helper
move_if_present /etc/mc-panel etc-mc-panel
move_if_present /var/lib/mc-panel var-lib-mc-panel
move_if_present /opt/mc-panel/current current-release-link

if [[ -f $ACL_STATE ]]; then
    setfacl --restore="$ACL_STATE"
fi
systemctl daemon-reload
systemctl is-active --quiet "$SERVER_SERVICE"

echo "Panel integration removed; data preserved at $ARCHIVE"
echo "Release directories and the mc-panel system account were retained."
echo "Minecraft was not restarted."
