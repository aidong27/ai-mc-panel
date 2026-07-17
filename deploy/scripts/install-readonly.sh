#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_INSTALL:-} != YES ]]; then
    echo "Refusing installation. Set MC_PANEL_APPROVE_INSTALL=YES after reviewing the manifest." >&2
    exit 1
fi
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 RELEASE_SOURCE VERSION" >&2
    exit 1
fi

SOURCE=$(realpath "$1")
VERSION=$2
RELEASE=/opt/mc-panel/releases/$VERSION
STATE=/var/backups/mc-panel
ENV_FILE=/etc/mc-panel/panel.env
HELPER_CONFIG=/etc/mc-panel/helper.json
CURRENT=/opt/mc-panel/current
STAMP=$(date +%Y%m%dT%H%M%S)
FAILED_STATE=$STATE/failed-install-$STAMP
RUNTIME_TMP=$(mktemp -d)

cleanup() {
    rm -rf "$RUNTIME_TMP"
}
trap cleanup EXIT

[[ $VERSION =~ ^[0-9A-Za-z._-]+$ ]] || { echo "Invalid version." >&2; exit 1; }
[[ -f $SOURCE/apps/web/dist/index.html ]] || { echo "Built frontend is missing." >&2; exit 1; }
mapfile -t API_WHEELS < <(find "$SOURCE/wheelhouse" -maxdepth 1 -type f \
    -name 'mc_panel_api-*.whl' -printf '%f\n')
[[ ${#API_WHEELS[@]} -eq 1 ]] || { echo "Expected exactly one API wheel." >&2; exit 1; }
[[ -f $SOURCE/helper/mc_panel_action.py ]] || { echo "Helper source is missing." >&2; exit 1; }
[[ -f $SOURCE/deploy/systemd/mc-panel.service ]] || { echo "Unit file is missing." >&2; exit 1; }
[[ -f $SOURCE/deploy/scripts/render-runtime-profile.py ]] || {
    echo "Runtime profile renderer is missing." >&2
    exit 1
}
[[ -f $SOURCE/SHA256SUMS ]] || { echo "Release checksum manifest is missing." >&2; exit 1; }
[[ ! -e $RELEASE ]] || { echo "Release already exists: $RELEASE" >&2; exit 1; }
command -v setfacl >/dev/null || { echo "Install the acl package first." >&2; exit 1; }
python3 -m venv --help >/dev/null || { echo "Install python3-venv first." >&2; exit 1; }
[[ ! -e /etc/sudoers.d/mc-panel ]] || { echo "Unexpected existing sudoers rule." >&2; exit 1; }
[[ ! -e /usr/local/libexec/mc-panel-action ]] || { echo "Existing helper must be audited." >&2; exit 1; }
[[ ! -e /etc/mc-panel ]] || { echo "Existing panel configuration must be audited." >&2; exit 1; }
[[ ! -e /var/lib/mc-panel ]] || { echo "Existing panel data must be audited." >&2; exit 1; }
[[ ! -e $STATE/acl-before-install.txt ]] || { echo "Existing ACL snapshot must be audited." >&2; exit 1; }
! getent group mc-panel >/dev/null || { echo "Existing panel group must be audited." >&2; exit 1; }
! id mc-panel >/dev/null 2>&1 || { echo "Existing panel user must be audited." >&2; exit 1; }
[[ ! -e /etc/systemd/system/mc-panel.service ]] || {
    echo "Existing mc-panel systemd unit must be audited before installation." >&2
    exit 1
}
[[ ! -e $CURRENT && ! -L $CURRENT ]] || {
    echo "Existing current release link must be audited before installation." >&2
    exit 1
}
[[ ! -e /etc/systemd/system/mc-panel.service.d/write-access.conf ]] || {
    echo "Unexpected existing write-access drop-in." >&2
    exit 1
}
(cd "$SOURCE" && sha256sum --check --strict SHA256SUMS >/dev/null)

for variable in \
    MC_PANEL_SERVER_ROOT \
    MC_PANEL_SERVER_STARTUP_PATH \
    MC_PANEL_BACKUP_ROOT \
    MC_PANEL_BACKUP_COMMAND; do
    if [[ -z ${!variable:-} ]]; then
        echo "Set $variable explicitly after completing the read-only audit." >&2
        exit 1
    fi
done

MC_PANEL_ENV=production python3 "$SOURCE/deploy/scripts/render-runtime-profile.py" \
    "$RUNTIME_TMP/panel.env" "$RUNTIME_TMP/helper.json"

mapfile -t RUNTIME < <(python3 - "$RUNTIME_TMP/helper.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
for key in (
    "server_root",
    "backup_root",
    "server_service",
    "backup_command",
    "console_user",
):
    print(data[key])
PY
)
[[ ${#RUNTIME[@]} -eq 5 ]] || { echo "Runtime profile could not be read." >&2; exit 1; }
SERVER_ROOT=${RUNTIME[0]}
BACKUP_ROOT=${RUNTIME[1]}
SERVER_SERVICE=${RUNTIME[2]}
BACKUP_COMMAND=${RUNTIME[3]}
CONSOLE_USER=${RUNTIME[4]}
PANEL_PORT=${MC_PANEL_PORT:-18080}

[[ -d $SERVER_ROOT && ! -L $SERVER_ROOT ]] || {
    echo "Configured Minecraft root is missing or unsafe: $SERVER_ROOT" >&2
    exit 1
}
[[ -d $BACKUP_ROOT && ! -L $BACKUP_ROOT ]] || {
    echo "Configured backup root is missing or unsafe: $BACKUP_ROOT" >&2
    exit 1
}
[[ -f $BACKUP_COMMAND && ! -L $BACKUP_COMMAND && -x $BACKUP_COMMAND ]] || {
    echo "Configured backup command is missing or unsafe: $BACKUP_COMMAND" >&2
    exit 1
}
systemctl is-active --quiet "$SERVER_SERVICE" || {
    echo "Configured Minecraft service is not active: $SERVER_SERVICE" >&2
    exit 1
}
id "$CONSOLE_USER" >/dev/null 2>&1 || {
    echo "Configured Minecraft account does not exist: $CONSOLE_USER" >&2
    exit 1
}

install -d -o root -g root -m 0700 "$STATE"
install -d -o root -g root -m 0700 "$FAILED_STATE"

rollback_install() {
    local failure_status=$?
    trap - ERR
    set +e
    systemctl disable --now mc-panel.service >/dev/null 2>&1
    if [[ -f $STATE/acl-before-install.txt ]]; then
        setfacl --restore="$STATE/acl-before-install.txt"
    fi
    move_created() {
        local source=$1
        local name=$2
        if [[ -e $source || -L $source ]]; then
            mv "$source" "$FAILED_STATE/$name"
        fi
    }
    move_created /opt/mc-panel/current current-release-link
    move_created "$RELEASE" release
    move_created /usr/local/libexec/mc-panel-action privileged-helper
    move_created /etc/systemd/system/mc-panel.service systemd-unit
    move_created /etc/mc-panel etc-mc-panel
    move_created /var/lib/mc-panel var-lib-mc-panel
    systemctl daemon-reload
    echo "Installation failed; created objects were preserved in $FAILED_STATE" >&2
    echo "The mc-panel system user and group were retained for manual review." >&2
    exit "$failure_status"
}
trap rollback_install ERR

groupadd --system mc-panel
useradd --system --gid mc-panel --home-dir /var/lib/mc-panel --create-home \
    --shell /usr/sbin/nologin mc-panel

install -d -o root -g root -m 0755 /opt/mc-panel/releases
install -d -o mc-panel -g mc-panel -m 0700 /var/lib/mc-panel
install -d -o root -g mc-panel -m 0750 /etc/mc-panel
install -d -o root -g root -m 0755 /usr/local/libexec

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
install -o root -g mc-panel -m 0640 "$RUNTIME_TMP/panel.env" "$ENV_FILE"
install -o root -g root -m 0600 "$RUNTIME_TMP/helper.json" "$HELPER_CONFIG"

acl_state=$STATE/acl-before-install.txt
{
    getfacl -p "$SERVER_ROOT"
    for directory in \
        "$SERVER_ROOT/logs" \
        "$SERVER_ROOT/crash-reports" \
        "$SERVER_ROOT/mods" \
        "$BACKUP_ROOT"; do
        [[ ! -d $directory ]] || getfacl -R -p "$directory"
    done
    for file in \
        "$SERVER_ROOT/server.properties" \
        "$SERVER_ROOT/whitelist.json" \
        "$SERVER_ROOT/ops.json"; do
        [[ ! -e $file ]] || getfacl -p "$file"
    done
} >"$acl_state"
chmod 0600 "$acl_state"

setfacl -m u:mc-panel:--x "$SERVER_ROOT"
for directory in \
    "$SERVER_ROOT/logs" \
    "$SERVER_ROOT/crash-reports" \
    "$SERVER_ROOT/mods" \
    "$BACKUP_ROOT"; do
    if [[ -d $directory ]]; then
        setfacl -R -m u:mc-panel:rX "$directory"
        find "$directory" -type d -exec setfacl -m d:u:mc-panel:r-x {} +
    fi
done
for file in \
    "$SERVER_ROOT/server.properties" \
    "$SERVER_ROOT/whitelist.json" \
    "$SERVER_ROOT/ops.json"; do
    [[ ! -e $file ]] || setfacl -m u:mc-panel:r-- "$file"
done

ln -s "$RELEASE" "$CURRENT.new"
mv -Tf "$CURRENT.new" "$CURRENT"
"$RELEASE/deploy/scripts/fingerprint.sh" >"$STATE/fingerprint-at-install.txt"
chmod 0600 "$STATE/fingerprint-at-install.txt"

systemctl daemon-reload
systemctl enable --now mc-panel.service
health_ready=false
for _ in {1..30}; do
    if curl --fail --silent "http://127.0.0.1:$PANEL_PORT/api/v1/health" >/dev/null 2>&1; then
        health_ready=true
        break
    fi
    sleep 1
done
if [[ $health_ready != true ]]; then
    echo "Panel health check did not become ready within 30 seconds." >&2
    false
fi
trap - ERR
rmdir "$FAILED_STATE"

echo "Read-only panel installed at http://127.0.0.1:$PANEL_PORT"
echo "Production writes remain disabled and no sudoers rule was installed."
echo "Create the first admin with: $RELEASE/deploy/scripts/create-admin.sh <username>"
