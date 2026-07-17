#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=${MC_PANEL_SERVER_ROOT:-/srv/minecraft/server}
BACKUPS=${MC_PANEL_BACKUP_ROOT:-/srv/minecraft/backups}
SERVER_SERVICE=${MC_PANEL_SERVER_SERVICE:-minecraft.service}
BACKUP_TIMER=${MC_PANEL_BACKUP_TIMER:-minecraft-backup.timer}
BACKUP_GLOB=${MC_PANEL_BACKUP_ARCHIVE_GLOB:-minecraft-*.tar.gz}
SERVER_PORT=${MC_PANEL_SERVER_PORT:-25565}
PANEL_PORT=${MC_PANEL_PORT:-18080}
STARTUP=${MC_PANEL_SERVER_STARTUP_PATH:-$ROOT/start.sh}
OS_NAME=unknown
OS_VERSION=unknown
if [[ -r /etc/os-release ]]; then
    # The target Linux host provides this standard metadata file.
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_NAME=${NAME:-unknown}
    OS_VERSION=${VERSION_ID:-unknown}
fi

printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
printf 'host=%s\n' "$(hostname)"
printf 'os=%s %s\n' "$OS_NAME" "$OS_VERSION"
printf 'kernel=%s\n' "$(uname -r)"
printf 'minecraft_active=%s\n' "$(systemctl is-active "$SERVER_SERVICE" || true)"
printf 'minecraft_enabled=%s\n' "$(systemctl is-enabled "$SERVER_SERVICE" || true)"
printf 'backup_timer_active=%s\n' "$(systemctl is-active "$BACKUP_TIMER" || true)"
printf 'root_present=%s\n' "$(test -d "$ROOT" && echo yes || echo no)"
printf 'latest_log_present=%s\n' "$(test -f "$ROOT/logs/latest.log" && echo yes || echo no)"
printf 'backup_count=%s\n' "$(find "$BACKUPS" -maxdepth 1 -type f -name "$BACKUP_GLOB" 2>/dev/null | wc -l | tr -d ' ')"
printf 'disk_free_bytes=%s\n' "$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
printf 'listener_minecraft=%s\n' "$(ss -ltnH "sport = :$SERVER_PORT" | wc -l | tr -d ' ')"
printf 'listener_panel=%s\n' "$(ss -ltnH "sport = :$PANEL_PORT" | wc -l | tr -d ' ')"
fragment=$(systemctl show "$SERVER_SERVICE" --property=FragmentPath --value 2>/dev/null || true)
printf 'unit_sha256=%s\n' "$(test -f "$fragment" && sha256sum "$fragment" | awk '{print $1}' || echo missing)"
printf 'startup_sha256=%s\n' "$(test -f "$STARTUP" && sha256sum "$STARTUP" | awk '{print $1}' || echo missing)"

printf '\nNo files or services were changed.\n'
