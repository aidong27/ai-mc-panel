#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from mc_panel_api.config import Settings  # noqa: E402


def _quoted(value: object) -> str:
    text = str(value)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("Environment values must be single-line")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def _write(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise SystemExit(f"Refusing to overwrite {path}")
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a validated mc-panel runtime profile")
    parser.add_argument("panel_env", type=Path)
    parser.add_argument("helper_config", type=Path)
    args = parser.parse_args()

    defaults = {
        "MC_PANEL_ENV": "production",
        "MC_PANEL_BIND": "127.0.0.1",
        "MC_PANEL_PORT": "18080",
        "MC_PANEL_DATABASE_PATH": "/var/lib/mc-panel/panel.db",
        "MC_PANEL_WEB_DIST_PATH": "/opt/mc-panel/current/apps/web/dist",
        "MC_PANEL_ADAPTER": "systemd",
        "MC_PANEL_SESSION_SECURE": "true",
        "MC_PANEL_HELPER_PATH": "/usr/local/libexec/mc-panel-action",
        "MC_PANEL_HELPER_CONFIG_PATH": "/etc/mc-panel/helper.json",
        "MC_PANEL_SECRET_KEY": secrets.token_urlsafe(48),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    settings = Settings.from_env()

    environment = {
        "MC_PANEL_ENV": settings.environment,
        "MC_PANEL_BIND": settings.bind,
        "MC_PANEL_PORT": settings.port,
        "MC_PANEL_DATABASE_PATH": settings.database_path,
        "MC_PANEL_WEB_DIST_PATH": settings.web_dist_path,
        "MC_PANEL_ADAPTER": settings.adapter,
        "MC_PANEL_SECRET_KEY": settings.secret_key,
        "MC_PANEL_SESSION_SECURE": str(settings.session_secure).lower(),
        "MC_PANEL_PUBLIC_ORIGIN": settings.panel_public_origin,
        "MC_PANEL_SESSION_IDLE_MINUTES": settings.session_idle_minutes,
        "MC_PANEL_SESSION_ABSOLUTE_HOURS": settings.session_absolute_hours,
        "MC_PANEL_SERVER_NAME": settings.server_display_name,
        "MC_PANEL_PUBLIC_ADDRESS": settings.public_address,
        "MC_PANEL_SERVER_ROOT": settings.server_root,
        "MC_PANEL_SERVER_STARTUP_PATH": settings.server_startup_path,
        "MC_PANEL_SERVER_SERVICE": settings.server_service,
        "MC_PANEL_SERVER_PORT": settings.server_port,
        "MC_PANEL_SERVER_TIMEZONE": settings.server_timezone,
        "MC_PANEL_BACKUP_ROOT": settings.backup_root,
        "MC_PANEL_BACKUP_ARCHIVE_GLOB": settings.backup_archive_glob,
        "MC_PANEL_BACKUP_SERVICE": settings.backup_service,
        "MC_PANEL_BACKUP_TIMER": settings.backup_timer,
        "MC_PANEL_BACKUP_COMMAND": settings.backup_command,
        "MC_PANEL_BACKUP_TIMER_UNIT_PATH": settings.backup_timer_unit_path,
        "MC_PANEL_BACKUP_TIMER_DROPIN_PATH": settings.backup_timer_dropin_path,
        "MC_PANEL_BACKUP_SERVICE_UNIT_PATH": settings.backup_service_unit_path,
        "MC_PANEL_BACKUP_SERVICE_DROPIN_PATH": settings.backup_service_dropin_path,
        "MC_PANEL_RECOVERY_ROOT": settings.recovery_root,
        "MC_PANEL_CONSOLE_USER": settings.console_user,
        "MC_PANEL_CONSOLE_SCREEN": settings.console_screen_name,
        "MC_PANEL_HELPER_CONFIG_PATH": settings.helper_config_path,
        "MC_PANEL_MINECRAFT_VERSION": settings.minecraft_version_hint,
        "MC_PANEL_LOADER": settings.loader_hint,
        "MC_PANEL_LOADER_VERSION": settings.loader_version_hint,
        "MC_PANEL_JAVA_VERSION": settings.java_version_hint,
        "MC_PANEL_APPROVED_FINGERPRINT": settings.approved_fingerprint,
        "MC_PANEL_PRODUCTION_WRITES_ENABLED": str(settings.production_writes_enabled).lower(),
        "MC_PANEL_HELPER_PATH": settings.helper_path,
        "MC_PANEL_AI_ENABLED": str(settings.ai_enabled).lower(),
        "MC_PANEL_AI_API_BASE_URL": settings.ai_api_base_url,
        "MC_PANEL_AI_API_KEY": settings.ai_api_key,
        "MC_PANEL_AI_MODEL": settings.ai_model,
        "MC_PANEL_AI_TEMPERATURE": settings.ai_temperature,
        "MC_PANEL_AI_MAX_TOKENS": settings.ai_max_tokens,
        "MC_PANEL_AI_TIMEOUT_SECONDS": settings.ai_timeout_seconds,
        "MC_PANEL_AI_REQUESTS_PER_MINUTE": settings.ai_requests_per_minute,
        "MC_PANEL_AI_REQUESTS_PER_DAY": settings.ai_requests_per_day,
        "MC_PANEL_AI_TOKENS_PER_DAY": settings.ai_tokens_per_day,
    }
    panel_content = "".join(f"{key}={_quoted(value)}\n" for key, value in environment.items())

    helper = {
        "server_root": str(settings.server_root),
        "backup_root": str(settings.backup_root),
        "quarantine_root": str(settings.database_path.parent / "quarantine"),
        "recovery_root": str(settings.recovery_root),
        "lock_file": "/run/lock/mc-panel-action.lock",
        "backup_command": str(settings.backup_command),
        "timer_dropin": str(settings.backup_timer_dropin_path),
        "backup_service_dropin": str(settings.backup_service_dropin_path),
        "server_service": settings.server_service,
        "backup_timer": settings.backup_timer,
        "server_port": settings.server_port,
        "console_user": settings.console_user,
        "console_screen_name": settings.console_screen_name,
        "backup_archive_glob": settings.backup_archive_glob,
    }
    _write(args.panel_env, panel_content)
    _write(args.helper_config, json.dumps(helper, indent=2, ensure_ascii=False) + "\n")

    print(f"server_root={settings.server_root}")
    print(f"server_service={settings.server_service}")
    print(f"backup_root={settings.backup_root}")
    print(f"backup_timer={settings.backup_timer}")
    print("Secrets were written only to the requested panel environment file.")


if __name__ == "__main__":
    main()
