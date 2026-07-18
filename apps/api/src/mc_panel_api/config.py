from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _runtime_path(name: str, default: str, *, absolute: bool) -> Path:
    value = os.getenv(name, default).strip()
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{name} is invalid")
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ValueError(f"{name} must not contain parent traversal")
    if absolute and not path.is_absolute():
        raise ValueError(f"{name} must be absolute in production")
    return path


def _systemd_unit(name: str, default: str, suffix: str) -> str:
    value = os.getenv(name, default).strip()
    pattern = rf"[A-Za-z0-9][A-Za-z0-9_.@:-]{{0,126}}\.{suffix}"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{name} must be a valid systemd {suffix} unit")
    return value


def _safe_name(name: str, default: str, pattern: str) -> str:
    value = os.getenv(name, default).strip()
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{name} is invalid")
    return value


def _archive_glob(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if (
        not value
        or len(value) > 160
        or "/" in value
        or "\\" in value
        or ".." in value
        or not re.fullmatch(r"[A-Za-z0-9*?._-]+", value)
    ):
        raise ValueError(f"{name} must be a filename-only glob")
    return value


def _timezone(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"{name} must be an installed IANA timezone") from exc
    return value


def _identity_hint(name: str) -> str:
    value = os.getenv(name, "").strip()
    if len(value) > 80 or any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} is invalid")
    return value


def _single_line(name: str, default: str = "", *, maximum: int = 4096) -> str:
    value = os.getenv(name, default).strip()
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must be a single-line value")
    return value


def _fingerprint(name: str) -> str:
    value = os.getenv(name, "").strip().lower()
    if value and not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"{name} must be an empty value or a SHA-256 fingerprint")
    return value


def _api_base_url(name: str) -> str:
    value = os.getenv(name, "").rstrip("/")
    if not value:
        return ""
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} contains control characters")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    local_http = parsed.scheme == "http" and hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not hostname
        or (parsed.scheme != "https" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must use HTTPS without credentials, query, or fragment")
    return value


def _http_origin(name: str) -> str:
    value = os.getenv(name, "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if (
        not hostname
        or parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTP origin without credentials or a path")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    bind: str
    port: int
    database_path: Path
    web_dist_path: Path
    adapter: str
    mock_root: Path
    secret_key: str
    session_secure: bool
    panel_public_origin: str
    session_idle_minutes: int
    session_absolute_hours: int
    server_display_name: str
    public_address: str
    server_root: Path
    server_startup_path: Path
    server_service: str
    server_port: int
    server_timezone: str
    backup_root: Path
    backup_archive_glob: str
    backup_service: str
    backup_timer: str
    backup_command: Path
    backup_timer_unit_path: Path
    backup_timer_dropin_path: Path
    backup_service_unit_path: Path
    backup_service_dropin_path: Path
    recovery_root: Path
    console_user: str
    console_screen_name: str
    console_commands_enabled: bool
    helper_config_path: Path
    minecraft_version_hint: str
    loader_hint: str
    loader_version_hint: str
    java_version_hint: str
    approved_fingerprint: str
    production_writes_enabled: bool
    helper_path: Path
    ai_enabled: bool
    ai_api_base_url: str
    ai_api_key: str
    ai_model: str
    ai_temperature: float
    ai_max_tokens: int
    ai_timeout_seconds: int
    ai_requests_per_minute: int
    ai_requests_per_day: int
    ai_tokens_per_day: int

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("MC_PANEL_ENV", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ValueError("MC_PANEL_ENV must be development, test, or production")
        secret_key = _single_line("MC_PANEL_SECRET_KEY")
        if not secret_key:
            if environment == "production":
                raise RuntimeError("MC_PANEL_SECRET_KEY is required in production")
            secret_key = secrets.token_urlsafe(48)

        adapter = os.getenv("MC_PANEL_ADAPTER", "mock").strip().lower()
        if adapter not in {"mock", "systemd"}:
            raise ValueError("MC_PANEL_ADAPTER must be mock or systemd")
        if environment == "production" and adapter != "systemd":
            raise ValueError("MC_PANEL_ADAPTER must be systemd in production")

        bind = os.getenv("MC_PANEL_BIND", "127.0.0.1").strip()
        if not bind or any(character.isspace() or ord(character) < 32 for character in bind):
            raise ValueError("MC_PANEL_BIND is invalid")
        if environment == "production" and bind not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("MC_PANEL_BIND must be loopback-only in production")

        session_secure = _bool("MC_PANEL_SESSION_SECURE", environment == "production")
        if environment == "production" and not session_secure:
            raise ValueError("MC_PANEL_SESSION_SECURE must be true in production")

        panel_public_origin = _http_origin("MC_PANEL_PUBLIC_ORIGIN")
        if environment == "production" and panel_public_origin.startswith("http://"):
            raise ValueError("MC_PANEL_PUBLIC_ORIGIN must use HTTPS in production")

        absolute_runtime_paths = environment == "production"
        server_root = _runtime_path(
            "MC_PANEL_SERVER_ROOT", "/srv/minecraft/server", absolute=absolute_runtime_paths
        )
        backup_timer = _systemd_unit("MC_PANEL_BACKUP_TIMER", "minecraft-backup.timer", "timer")
        backup_service = _systemd_unit(
            "MC_PANEL_BACKUP_SERVICE", "minecraft-backup.service", "service"
        )

        return cls(
            environment=environment,
            bind=bind,
            port=_int("MC_PANEL_PORT", 18080, 1024, 65535),
            database_path=_runtime_path(
                "MC_PANEL_DATABASE_PATH", "./data/panel.db", absolute=absolute_runtime_paths
            ),
            web_dist_path=_runtime_path(
                "MC_PANEL_WEB_DIST_PATH",
                str(Path(__file__).resolve().parents[3] / "web" / "dist"),
                absolute=absolute_runtime_paths,
            ),
            adapter=adapter,
            mock_root=_runtime_path(
                "MC_PANEL_MOCK_ROOT", "./tests/fixtures/mock-server", absolute=False
            ),
            secret_key=secret_key,
            session_secure=session_secure,
            panel_public_origin=panel_public_origin,
            session_idle_minutes=_int("MC_PANEL_SESSION_IDLE_MINUTES", 60, 5, 1440),
            session_absolute_hours=_int("MC_PANEL_SESSION_ABSOLUTE_HOURS", 24, 1, 168),
            server_display_name=_single_line(
                "MC_PANEL_SERVER_NAME", "Minecraft 服务器", maximum=120
            ),
            public_address=_single_line("MC_PANEL_PUBLIC_ADDRESS", maximum=255),
            server_root=server_root,
            server_startup_path=_runtime_path(
                "MC_PANEL_SERVER_STARTUP_PATH",
                str(server_root / "start.sh"),
                absolute=absolute_runtime_paths,
            ),
            server_service=_systemd_unit("MC_PANEL_SERVER_SERVICE", "minecraft.service", "service"),
            server_port=_int("MC_PANEL_SERVER_PORT", 25565, 1, 65535),
            server_timezone=_timezone("MC_PANEL_SERVER_TIMEZONE", "UTC"),
            backup_root=_runtime_path(
                "MC_PANEL_BACKUP_ROOT",
                "/srv/minecraft/backups",
                absolute=absolute_runtime_paths,
            ),
            backup_archive_glob=_archive_glob("MC_PANEL_BACKUP_ARCHIVE_GLOB", "minecraft-*.tar.gz"),
            backup_service=backup_service,
            backup_timer=backup_timer,
            backup_command=_runtime_path(
                "MC_PANEL_BACKUP_COMMAND",
                "/usr/local/bin/minecraft-backup",
                absolute=absolute_runtime_paths,
            ),
            backup_timer_unit_path=_runtime_path(
                "MC_PANEL_BACKUP_TIMER_UNIT_PATH",
                f"/etc/systemd/system/{backup_timer}",
                absolute=absolute_runtime_paths,
            ),
            backup_timer_dropin_path=_runtime_path(
                "MC_PANEL_BACKUP_TIMER_DROPIN_PATH",
                f"/etc/systemd/system/{backup_timer}.d/mc-panel.conf",
                absolute=absolute_runtime_paths,
            ),
            backup_service_unit_path=_runtime_path(
                "MC_PANEL_BACKUP_SERVICE_UNIT_PATH",
                f"/etc/systemd/system/{backup_service}",
                absolute=absolute_runtime_paths,
            ),
            backup_service_dropin_path=_runtime_path(
                "MC_PANEL_BACKUP_SERVICE_DROPIN_PATH",
                f"/etc/systemd/system/{backup_service}.d/mc-panel.conf",
                absolute=absolute_runtime_paths,
            ),
            recovery_root=_runtime_path(
                "MC_PANEL_RECOVERY_ROOT",
                "/srv/minecraft/panel-recovery",
                absolute=absolute_runtime_paths,
            ),
            console_user=_safe_name(
                "MC_PANEL_CONSOLE_USER", "minecraft", r"[a-z_][a-z0-9_-]{0,31}"
            ),
            console_screen_name=_safe_name(
                "MC_PANEL_CONSOLE_SCREEN", "minecraft", r"[A-Za-z0-9_.:-]{1,64}"
            ),
            console_commands_enabled=_bool("MC_PANEL_CONSOLE_COMMANDS_ENABLED", False),
            helper_config_path=_runtime_path(
                "MC_PANEL_HELPER_CONFIG_PATH",
                "/etc/mc-panel/helper.json",
                absolute=absolute_runtime_paths,
            ),
            minecraft_version_hint=_identity_hint("MC_PANEL_MINECRAFT_VERSION"),
            loader_hint=_identity_hint("MC_PANEL_LOADER"),
            loader_version_hint=_identity_hint("MC_PANEL_LOADER_VERSION"),
            java_version_hint=_identity_hint("MC_PANEL_JAVA_VERSION"),
            approved_fingerprint=_fingerprint("MC_PANEL_APPROVED_FINGERPRINT"),
            production_writes_enabled=_bool("MC_PANEL_PRODUCTION_WRITES_ENABLED", False),
            helper_path=_runtime_path(
                "MC_PANEL_HELPER_PATH",
                "/usr/local/libexec/mc-panel-action",
                absolute=absolute_runtime_paths,
            ),
            ai_enabled=_bool("MC_PANEL_AI_ENABLED", False),
            ai_api_base_url=_api_base_url("MC_PANEL_AI_API_BASE_URL"),
            ai_api_key=_single_line("MC_PANEL_AI_API_KEY"),
            ai_model=_single_line("MC_PANEL_AI_MODEL", maximum=160),
            ai_temperature=_float("MC_PANEL_AI_TEMPERATURE", 0.2, 0.0, 2.0),
            ai_max_tokens=_int("MC_PANEL_AI_MAX_TOKENS", 800, 64, 4096),
            ai_timeout_seconds=_int("MC_PANEL_AI_TIMEOUT_SECONDS", 30, 5, 120),
            ai_requests_per_minute=_int("MC_PANEL_AI_REQUESTS_PER_MINUTE", 3, 1, 30),
            ai_requests_per_day=_int("MC_PANEL_AI_REQUESTS_PER_DAY", 40, 1, 1000),
            ai_tokens_per_day=_int("MC_PANEL_AI_TOKENS_PER_DAY", 30_000, 1000, 10_000_000),
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_enabled and self.ai_api_base_url and self.ai_api_key and self.ai_model)
