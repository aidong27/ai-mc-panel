from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
from typing import Any

from .adapters.systemd import SystemdMinecraftAdapter
from .auth import AuthService
from .config import Settings
from .database import Database
from .models import Role


def _doctor(settings: Settings) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, *, critical: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "critical": critical, "detail": detail})

    add(
        "loopback_bind",
        settings.bind in {"127.0.0.1", "::1", "localhost"} or not settings.is_production,
        critical=True,
        detail=settings.bind,
    )
    add(
        "server_root",
        settings.server_root.is_dir() and not settings.server_root.is_symlink(),
        critical=True,
        detail=str(settings.server_root),
    )
    add(
        "startup_file",
        settings.server_startup_path.is_file() and not settings.server_startup_path.is_symlink(),
        critical=True,
        detail=str(settings.server_startup_path),
    )
    add(
        "latest_log",
        (settings.server_root / "logs/latest.log").is_file(),
        critical=False,
        detail=str(settings.server_root / "logs/latest.log"),
    )
    add(
        "server_properties",
        (settings.server_root / "server.properties").is_file(),
        critical=False,
        detail=str(settings.server_root / "server.properties"),
    )
    add(
        "backup_root",
        settings.backup_root.is_dir() and not settings.backup_root.is_symlink(),
        critical=True,
        detail=str(settings.backup_root),
    )
    add(
        "backup_command",
        settings.backup_command.is_file()
        and not settings.backup_command.is_symlink()
        and os.access(settings.backup_command, os.X_OK),
        critical=settings.production_writes_enabled,
        detail=str(settings.backup_command),
    )
    if settings.production_writes_enabled:
        try:
            helper_stat = settings.helper_config_path.stat()
            helper_safe = (
                not settings.helper_config_path.is_symlink()
                and helper_stat.st_uid == 0
                and stat.S_IMODE(helper_stat.st_mode) & 0o022 == 0
            )
        except OSError:
            helper_safe = False
        add(
            "helper_config",
            helper_safe,
            critical=True,
            detail=str(settings.helper_config_path),
        )
    try:
        status = SystemdMinecraftAdapter(settings).get_status()
        add(
            "server_service",
            status["state"] in {"running", "inactive"},
            critical=True,
            detail=f"{settings.server_service}: {status['state']}",
        )
        identity = {
            "minecraft_version": status["minecraft_version"],
            "loader": status["loader"],
            "loader_version": status["loader_version"],
            "java_version": status["java_version"],
            "sources": status["identity_sources"],
        }
    except Exception as exc:
        add(
            "server_service",
            False,
            critical=True,
            detail=f"{settings.server_service}: {type(exc).__name__}",
        )
        identity = {}
    ready = all(item["ok"] for item in checks if item["critical"])
    return {"ready": ready, "checks": checks, "identity": identity}


def main() -> None:
    parser = argparse.ArgumentParser(prog="mc-panel-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user", help="Create a local panel user")
    create.add_argument("username")
    create.add_argument("--role", choices=[role.value for role in Role], default="owner")
    reset = subparsers.add_parser("set-password", help="Reset a local user's password")
    reset.add_argument("username")
    subparsers.add_parser("list-users", help="List users without password data")
    subparsers.add_parser("fingerprint", help="Print the current audited server fingerprint")
    doctor = subparsers.add_parser("doctor", help="Validate the configured runtime profile")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.command == "fingerprint":
        if settings.adapter != "systemd":
            raise SystemExit("Fingerprint is available only for the systemd adapter")
        print(SystemdMinecraftAdapter(settings).fingerprint())
        return
    if args.command == "doctor":
        report = _doctor(settings)
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for item in report["checks"]:
                label = "PASS" if item["ok"] else "FAIL" if item["critical"] else "WARN"
                print(f"{label}\t{item['name']}\t{item['detail']}")
            if report["identity"]:
                print("IDENTITY\t" + json.dumps(report["identity"], ensure_ascii=False))
        if not report["ready"]:
            raise SystemExit(1)
        return

    database = Database(settings.database_path)
    database.migrate()
    auth = AuthService(database, settings)

    if args.command == "create-user":
        first = getpass.getpass("Password: ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            raise SystemExit("Passwords do not match")
        user_id = auth.create_user(args.username, first, Role(args.role))
        print(f"Created user {args.username!r} with id {user_id}")
    elif args.command == "set-password":
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Repeat new password: ")
        if first != second:
            raise SystemExit("Passwords do not match")
        if not auth.reset_password(args.username, first):
            raise SystemExit("User was not found or is disabled")
        print(f"Password reset for {args.username!r}; all sessions were revoked")
    elif args.command == "list-users":
        for row in database.fetch_all(
            "SELECT id, username, role, disabled, created_at FROM users ORDER BY id"
        ):
            print(f"{row['id']}\t{row['username']}\t{row['role']}\tdisabled={row['disabled']}")
