#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
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
temporary=$(mktemp)
awk '
    BEGIN { seen=0 }
    /^MC_PANEL_PRODUCTION_WRITES_ENABLED=/ {
        print "MC_PANEL_PRODUCTION_WRITES_ENABLED=false"; seen=1; next
    }
    { print }
    END { if (!seen) print "MC_PANEL_PRODUCTION_WRITES_ENABLED=false" }
' "$ENV_FILE" >"$temporary"
install -o root -g mc-panel -m 0640 "$temporary" "$ENV_FILE"
rm -f "$temporary"

rm -f /etc/sudoers.d/mc-panel
rm -f /etc/systemd/system/mc-panel.service.d/write-access.conf
rm -f /etc/systemd/system/mc-panel.service.d/runtime-paths.conf
systemctl daemon-reload
systemctl restart mc-panel.service

echo "Production writes disabled and sudo authorization removed."
echo "Minecraft was not restarted."
