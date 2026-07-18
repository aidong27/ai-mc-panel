#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

MAX_INPUT = 64 * 1024
MAX_OUTPUT = 256 * 1024
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
HELPER_CONFIG_PATH = Path("/etc/mc-panel/helper.json")
ALLOWED_PROPERTY_KEYS = {
    "difficulty",
    "gamemode",
    "max-players",
    "pvp",
    "view-distance",
    "simulation-distance",
    "white-list",
    "motd",
}
TARGETS = (
    "world",
    "config",
    "defaultconfigs",
    "kubejs",
    "mods",
    "server.properties",
    "default-server.properties",
    "user_jvm_args.txt",
)
ALLOWED_ARCHIVE_ROOTS = TARGETS + ("eula.txt",)
VERIFICATION_DIRECTORY = ".mc-panel-verifications"
MAX_CHECKSUM_SIZE = 4096


class HelperError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class Paths:
    server_root: Path = Path("/srv/minecraft/server")
    backup_root: Path = Path("/srv/minecraft/backups")
    quarantine_root: Path = Path("/var/lib/mc-panel/quarantine")
    recovery_root: Path = Path("/srv/minecraft/panel-recovery")
    lock_file: Path = Path("/run/lock/mc-panel-action.lock")
    backup_command: Path = Path("/usr/local/bin/minecraft-backup")
    timer_dropin: Path = Path("/etc/systemd/system/minecraft-backup.timer.d/mc-panel.conf")
    backup_service_dropin: Path = Path(
        "/etc/systemd/system/minecraft-backup.service.d/mc-panel.conf"
    )
    server_service: str = "minecraft.service"
    backup_timer: str = "minecraft-backup.timer"
    server_port: int = 25565
    console_user: str = "minecraft"
    console_screen_name: str = "minecraft"
    backup_archive_glob: str = "minecraft-*.tar.gz"

    @classmethod
    def from_config(cls, path: Path = HELPER_CONFIG_PATH, *, expected_uid: int = 0) -> Paths:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT:
                raise HelperError("unsafe_config", "Helper configuration is not a safe file")
            config_stat = path.stat()
            if config_stat.st_uid != expected_uid or stat.S_IMODE(config_stat.st_mode) != 0o600:
                raise HelperError("unsafe_config", "Helper configuration ownership is unsafe")
            raw = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except HelperError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HelperError("invalid_config", "Helper configuration could not be read") from exc
        expected = {
            "server_root",
            "backup_root",
            "quarantine_root",
            "recovery_root",
            "lock_file",
            "backup_command",
            "timer_dropin",
            "backup_service_dropin",
            "server_service",
            "backup_timer",
            "server_port",
            "console_user",
            "console_screen_name",
            "backup_archive_glob",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise HelperError("invalid_config", "Helper configuration fields are invalid")

        def absolute_path(key: str) -> Path:
            value = raw[key]
            if not isinstance(value, str) or any(
                character.isspace() or ord(character) < 32 for character in value
            ):
                raise HelperError("invalid_config", f"{key} is invalid")
            candidate = Path(value)
            if not candidate.is_absolute() or ".." in candidate.parts:
                raise HelperError("invalid_config", f"{key} must be an absolute safe path")
            return candidate

        def unit(key: str, suffix: str) -> str:
            value = raw[key]
            pattern = rf"[A-Za-z0-9][A-Za-z0-9_.@:-]{{0,126}}\.{suffix}"
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise HelperError("invalid_config", f"{key} is invalid")
            return value

        port = raw["server_port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise HelperError("invalid_config", "server_port is invalid")
        console_user = raw["console_user"]
        if not isinstance(console_user, str) or not re.fullmatch(
            r"[a-z_][a-z0-9_-]{0,31}", console_user
        ):
            raise HelperError("invalid_config", "console_user is invalid")
        screen_name = raw["console_screen_name"]
        if not isinstance(screen_name, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,64}", screen_name
        ):
            raise HelperError("invalid_config", "console_screen_name is invalid")
        archive_glob = raw["backup_archive_glob"]
        if (
            not isinstance(archive_glob, str)
            or not archive_glob
            or len(archive_glob) > 160
            or "/" in archive_glob
            or "\\" in archive_glob
            or ".." in archive_glob
            or not re.fullmatch(r"[A-Za-z0-9*?._-]+", archive_glob)
        ):
            raise HelperError("invalid_config", "backup_archive_glob is invalid")
        return cls(
            server_root=absolute_path("server_root"),
            backup_root=absolute_path("backup_root"),
            quarantine_root=absolute_path("quarantine_root"),
            recovery_root=absolute_path("recovery_root"),
            lock_file=absolute_path("lock_file"),
            backup_command=absolute_path("backup_command"),
            timer_dropin=absolute_path("timer_dropin"),
            backup_service_dropin=absolute_path("backup_service_dropin"),
            server_service=unit("server_service", "service"),
            backup_timer=unit("backup_timer", "timer"),
            server_port=port,
            console_user=console_user,
            console_screen_name=screen_name,
            backup_archive_glob=archive_glob,
        )


@dataclass(frozen=True, slots=True)
class VerifiedArchive:
    sha256: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    @classmethod
    def from_stat(cls, digest: str, value: os.stat_result) -> VerifiedArchive:
        return cls(
            sha256=digest,
            size_bytes=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
            device=value.st_dev,
            inode=value.st_ino,
        )

    def matches(self, value: os.stat_result) -> bool:
        return (
            stat.S_ISREG(value.st_mode)
            and self.size_bytes == value.st_size
            and self.mtime_ns == value.st_mtime_ns
            and self.ctime_ns == value.st_ctime_ns
            and self.device == value.st_dev
            and self.inode == value.st_ino
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HelperError("invalid_json", "JSON contains duplicate keys")
        result[key] = value
    return result


def parse_request(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_INPUT:
        raise HelperError("invalid_request", "Request size is invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperError("invalid_json", "Request must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict) or set(value) != {"operation", "params"}:
        raise HelperError("invalid_request", "Request fields are invalid")
    if not isinstance(value["operation"], str) or not isinstance(value["params"], dict):
        raise HelperError("invalid_request", "Request types are invalid")
    return value


def _exact_keys(params: dict[str, Any], keys: set[str]) -> None:
    if set(params) != keys:
        raise HelperError("validation_failed", "Operation parameters are invalid")


def _run(
    argv: list[str],
    timeout: int,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = ENV
    if env_overrides is not None:
        if set(env_overrides) != {"KEEP_BACKUPS"} or not re.fullmatch(
            r"[1-9][0-9]{0,3}", env_overrides["KEEP_BACKUPS"]
        ):
            raise HelperError("validation_failed", "Backup retention override is invalid")
        run_env = {**ENV, **env_overrides}
    try:
        # Every caller supplies a fixed executable and argv list; shell execution is forbidden.
        result = subprocess.run(  # noqa: S603
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            cwd="/",
        )
    except subprocess.TimeoutExpired as exc:
        raise HelperError("timeout", "Operation timed out") from exc
    if result.returncode != 0:
        raise HelperError(
            "command_failed",
            "A fixed system command failed",
            {"returncode": result.returncode, "stderr": result.stderr[-2000:]},
        )
    return result


def _systemctl(action: str, unit: str = "minecraft.service", timeout: int = 180) -> None:
    if action not in {
        "start",
        "stop",
        "restart",
        "reset-failed",
        "is-active",
        "daemon-reload",
    }:
        raise HelperError("validation_failed", "Unsupported systemd action")
    argv = ["/usr/bin/systemctl", action]
    if action != "daemon-reload":
        argv.append(unit)
    _run(argv, timeout)


def _console(command: str, *, allow_unicode: bool = False, paths: Paths | None = None) -> None:
    if not 1 <= len(command) <= 256 or any(ord(char) < 32 for char in command):
        raise HelperError("validation_failed", "Console command is invalid")
    if allow_unicode:
        if not command.isprintable():
            raise HelperError("validation_failed", "Console text contains forbidden characters")
    else:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. :@#~[]{}")
        if any(
            (ord(char) < 128 and char not in allowed)
            or (ord(char) >= 128 and not char.isprintable())
            for char in command
        ):
            raise HelperError("validation_failed", "Console command contains forbidden characters")
    runtime = paths or Paths()
    _run(
        [
            "/usr/sbin/runuser",
            "-u",
            runtime.console_user,
            "--",
            "/usr/bin/screen",
            "-S",
            runtime.console_screen_name,
            "-p",
            "0",
            "-X",
            "stuff",
            command + "\r",
        ],
        10,
    )


def _is_active(unit: str = "minecraft.service") -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - unit is validated by the root-owned profile
            ["/usr/bin/systemctl", "is-active", "--quiet", unit],
            check=False,
            timeout=10,
            env=ENV,
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError(
            "verification_failed", "Minecraft service state could not be checked"
        ) from exc
    return result.returncode == 0


def _wait_for_port(port: int, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(2)
    return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _server_ids(user: str) -> tuple[int, int]:
    account = pwd.getpwnam(user)
    return account.pw_uid, account.pw_gid


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    if path.is_symlink():
        raise HelperError("unsafe_path", "Symlinks are not allowed in restored roots")
    os.chown(path, uid, gid, follow_symlinks=False)
    if path.is_dir():
        for root, directories, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            for name in directories + files:
                child = root_path / name
                if child.is_symlink():
                    raise HelperError("unsafe_path", "Symlinks are not allowed in restored data")
                os.chown(child, uid, gid, follow_symlinks=False)


class ActionHelper:
    def __init__(self, paths: Paths | None = None) -> None:
        self.paths = paths or Paths()

    def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        actions = {
            "start_server": self.start_server,
            "stop_server": self.stop_server,
            "restart_server": self.restart_server,
            "send_announcement": self.send_announcement,
            "send_console_command": self.send_console_command,
            "create_backup": self.create_backup,
            "verify_backup": self.verify_backup,
            "edit_server_property": self.edit_server_property,
            "add_whitelist_player": self.add_whitelist_player,
            "remove_whitelist_player": self.remove_whitelist_player,
            "add_operator": self.add_operator,
            "remove_operator": self.remove_operator,
            "install_mod": self.install_mod,
            "disable_mod": self.disable_mod,
            "set_backup_schedule": self.set_backup_schedule,
            "restore_backup": self.restore_backup,
        }
        action = actions.get(operation)
        if action is None:
            raise HelperError("operation_denied", "Operation is not allowlisted")
        self.paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.lock_file.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HelperError(
                    "operation_in_progress", "Another write operation is running"
                ) from exc
            result = action(params)
        return {"ok": True, "operation": operation, "result": result}

    def start_server(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, set())
        _systemctl("start", self.paths.server_service)
        if not _wait_for_port(self.paths.server_port, 180) or not _is_active(
            self.paths.server_service
        ):
            raise HelperError(
                "verification_failed", "Minecraft service and port did not become ready"
            )
        return {"active": True, "port": self.paths.server_port}

    def stop_server(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, set())
        _systemctl("stop", self.paths.server_service)
        if _is_active(self.paths.server_service):
            raise HelperError("verification_failed", "Minecraft service is still active")
        _systemctl("reset-failed", self.paths.server_service)
        return {"active": False}

    def restart_server(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, set())
        _systemctl("restart", self.paths.server_service)
        if not _wait_for_port(self.paths.server_port, 180) or not _is_active(
            self.paths.server_service
        ):
            raise HelperError("verification_failed", "Minecraft service and port did not recover")
        return {"active": True, "port": self.paths.server_port}

    def send_announcement(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"message"})
        message = params["message"]
        if not isinstance(message, str) or message.startswith("/"):
            raise HelperError("validation_failed", "Announcement is invalid")
        _console("say " + message, allow_unicode=True, paths=self.paths)
        return {"sent": True}

    def send_console_command(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"command"})
        command = params["command"]
        if not isinstance(command, str):
            raise HelperError("validation_failed", "Command is invalid")
        _console(command.lstrip("/"), paths=self.paths)
        return {"sent": True, "command": command.split(" ", 1)[0]}

    def create_backup(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, set())
        before = set(self.paths.backup_root.glob(self.paths.backup_archive_glob))
        try:
            _run(
                [str(self.paths.backup_command)],
                900,
                env_overrides={"KEEP_BACKUPS": str(len(before) + 1)},
            )
            created = sorted(
                set(self.paths.backup_root.glob(self.paths.backup_archive_glob)) - before
            )
            if not created:
                raise HelperError("verification_failed", "Backup command produced no new archive")
            archive = created[-1]
            checksum = Path(str(archive) + ".sha256")
            if not checksum.is_file() or checksum.is_symlink():
                raise HelperError("verification_failed", "Backup checksum was not created safely")
            verified = self._verify_archive(archive)
            verified_at = self._record_verified_archive(archive, verified)
        except HelperError as backup_exc:
            try:
                self._ensure_minecraft_ready(timeout=180)
            except HelperError as recovery_exc:
                raise HelperError(
                    "service_recovery_failed",
                    "Backup failed and Minecraft could not be recovered",
                    {
                        "backup_error": backup_exc.code,
                        "recovery_error": recovery_exc.code,
                    },
                ) from backup_exc
            raise
        try:
            self._ensure_minecraft_ready(timeout=180)
        except HelperError as exc:
            raise HelperError(
                "service_recovery_failed",
                "Backup completed but Minecraft readiness could not be verified",
                {"backup": archive.name},
            ) from exc
        return {
            "filename": archive.name,
            "size_bytes": verified.size_bytes,
            "sha256": verified.sha256,
            "verified": True,
            "verified_at": verified_at,
            "service_active": True,
        }

    def verify_backup(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"backup_id"})
        backup_id = params["backup_id"]
        if not isinstance(backup_id, str) or not re.fullmatch(r"backup_[a-f0-9]{12}", backup_id):
            raise HelperError("validation_failed", "Backup ID is invalid")
        archive = self._find_backup(backup_id)
        verified = self._verify_archive(archive)
        verified_at = self._record_verified_archive(archive, verified)
        return {
            "filename": archive.name,
            "size_bytes": verified.size_bytes,
            "sha256": verified.sha256,
            "verified": True,
            "verified_at": verified_at,
        }

    def _ensure_minecraft_ready(self, *, timeout: int) -> None:
        if not _is_active(self.paths.server_service):
            _systemctl("start", self.paths.server_service)
        if not _is_active(self.paths.server_service) or not _wait_for_port(
            self.paths.server_port, timeout
        ):
            raise HelperError(
                "verification_failed",
                "Minecraft service readiness could not be verified",
            )

    def edit_server_property(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"key", "value"})
        key = params["key"]
        value = params["value"]
        if not isinstance(key, str) or key not in ALLOWED_PROPERTY_KEYS:
            raise HelperError("validation_failed", "Property is not allowlisted")
        serialized = self._validate_property(key, value)
        path = self.paths.server_root / "server.properties"
        if path.is_symlink() or not path.is_file():
            raise HelperError("unsafe_path", "server.properties must be a regular file")
        original = path.read_text(encoding="utf-8")
        event_id = "property-" + uuid.uuid4().hex
        self._ensure_recovery_root()
        recovery = self.paths.recovery_root / event_id
        recovery.mkdir(parents=True, mode=0o700)
        shutil.copy2(path, recovery / "server.properties")
        lines = original.splitlines()
        replacement = f"{key}={serialized}"
        found = False
        for index, line in enumerate(lines):
            if line.startswith(key + "="):
                lines[index] = replacement
                found = True
                break
        if not found:
            lines.append(replacement)
        self._atomic_write(path, "\n".join(lines) + "\n")
        return {"key": key, "value": serialized, "recovery_point": str(recovery)}

    @staticmethod
    def _validate_property(key: str, value: Any) -> str:
        if key in {"pvp", "white-list"}:
            if not isinstance(value, bool):
                raise HelperError("validation_failed", "Boolean property expected")
            return "true" if value else "false"
        if key == "max-players":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
                raise HelperError("validation_failed", "max-players out of range")
            return str(value)
        if key in {"view-distance", "simulation-distance"}:
            if not isinstance(value, int) or isinstance(value, bool) or not 2 <= value <= 32:
                raise HelperError("validation_failed", "distance out of range")
            return str(value)
        if key == "difficulty" and value not in {"peaceful", "easy", "normal", "hard"}:
            raise HelperError("validation_failed", "difficulty is invalid")
        if key == "gamemode" and value not in {
            "survival",
            "creative",
            "adventure",
            "spectator",
        }:
            raise HelperError("validation_failed", "gamemode is invalid")
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 120
            or any(ord(c) < 32 for c in value)
        ):
            raise HelperError("validation_failed", "Text property is invalid")
        return value

    def _atomic_write(self, path: Path, content: str) -> None:
        original = path.stat()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".mc-panel-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copystat(path, temporary, follow_symlinks=False)
            os.chown(temporary, original.st_uid, original.st_gid)
            os.chmod(temporary, stat.S_IMODE(original.st_mode))
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _player_command(self, params: dict[str, Any], command: str) -> dict[str, Any]:
        _exact_keys(params, {"player"})
        player = params["player"]
        if not isinstance(player, str) or not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
            raise HelperError("validation_failed", "Player name is invalid")
        _console(f"{command} {player}", paths=self.paths)
        return {"player": player, "command": command}

    def add_whitelist_player(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._player_command(params, "whitelist add")

    def remove_whitelist_player(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._player_command(params, "whitelist remove")

    def add_operator(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._player_command(params, "op")

    def remove_operator(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._player_command(params, "deop")

    def install_mod(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"upload_id"})
        upload_id = params["upload_id"]
        if not isinstance(upload_id, str) or not re.fullmatch(r"upload_[a-f0-9]{16}", upload_id):
            raise HelperError("validation_failed", "Upload ID is invalid")
        source = self.paths.quarantine_root / f"{upload_id}.jar"
        sidecar = self.paths.quarantine_root / f"{upload_id}.json"
        if (
            self.paths.quarantine_root.is_symlink()
            or not self.paths.quarantine_root.is_dir()
            or not source.is_file()
            or source.is_symlink()
            or not sidecar.is_file()
            or sidecar.is_symlink()
            or sidecar.stat().st_size > MAX_INPUT
        ):
            raise HelperError("not_found", "Quarantined mod was not found")
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or set(metadata) != {
            "filename",
            "sha256",
            "size_bytes",
            "metadata",
        }:
            raise HelperError("validation_failed", "Stored mod metadata is invalid")
        filename = Path(str(metadata.get("filename", ""))).name
        expected_hash = metadata.get("sha256")
        expected_size = metadata.get("size_bytes")
        if (
            filename != metadata.get("filename")
            or filename.startswith(".")
            or len(filename.encode("utf-8")) > 184
            or not filename.lower().endswith(".jar")
            or not filename.isprintable()
            or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size != source.stat().st_size
            or metadata.get("metadata")
            not in {
                "META-INF/mods.toml",
                "META-INF/neoforge.mods.toml",
                "fabric.mod.json",
                "quilt.mod.json",
            }
        ):
            raise HelperError("validation_failed", "Stored mod filename is invalid")
        if _hash_file(source) != expected_hash:
            raise HelperError("verification_failed", "Uploaded mod checksum changed")
        mods_root = self.paths.server_root / "mods"
        if mods_root.is_symlink() or not mods_root.is_dir():
            raise HelperError("unsafe_path", "Mod directory must be a regular directory")
        destination = mods_root / filename
        if destination.exists() or destination.is_symlink():
            raise HelperError("already_exists", "A mod with this filename already exists")
        self._ensure_recovery_root()
        recovery = self.paths.recovery_root / ("install-mod-" + uuid.uuid4().hex)
        recovery.mkdir(parents=True, mode=0o700)
        shutil.copy2(sidecar, recovery / "upload-metadata.json")
        (recovery / "destination-was-absent").write_text(filename + "\n", encoding="utf-8")
        staging = destination.with_name(".mc-panel-" + uuid.uuid4().hex + ".jar")
        try:
            shutil.copy2(source, staging)
            if staging.stat().st_size != expected_size or _hash_file(staging) != expected_hash:
                raise HelperError("verification_failed", "Staged mod checksum changed")
            self._validate_jar(staging)
            uid, gid = _server_ids(self.paths.console_user)
            os.chown(staging, uid, gid)
            os.chmod(staging, 0o644)
            os.replace(staging, destination)
        finally:
            staging.unlink(missing_ok=True)
        return {
            "filename": filename,
            "sha256": _hash_file(destination),
            "restart_required": True,
            "recovery_point": str(recovery),
        }

    @staticmethod
    def _validate_jar(path: Path) -> None:
        if path.stat().st_size > 128 * 1024 * 1024 or not zipfile.is_zipfile(path):
            raise HelperError("validation_failed", "JAR file is invalid")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10_000
                or any(info.flag_bits & 1 for info in entries)
                or sum(info.file_size for info in entries) > 512 * 1024 * 1024
            ):
                raise HelperError("validation_failed", "JAR expands beyond the safety limit")
            if archive.testzip() is not None:
                raise HelperError("validation_failed", "JAR integrity check failed")
            names = {entry.filename for entry in entries}
            metadata = {
                "META-INF/mods.toml",
                "META-INF/neoforge.mods.toml",
                "fabric.mod.json",
                "quilt.mod.json",
            }
            if not names.intersection(metadata):
                raise HelperError("validation_failed", "Mod metadata is missing")

    def disable_mod(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"mod_id"})
        mod_id = params["mod_id"]
        if not isinstance(mod_id, str) or not re.fullmatch(r"mod_[A-Za-z0-9_-]{3,80}", mod_id):
            raise HelperError("validation_failed", "Mod ID is invalid")
        selected: Path | None = None
        mods_root = self.paths.server_root / "mods"
        if mods_root.is_symlink() or not mods_root.is_dir():
            raise HelperError("unsafe_path", "Mod directory must be a regular directory")
        for candidate in mods_root.glob("*.jar"):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            candidate_id = "mod_" + hashlib.sha256(candidate.name.encode()).hexdigest()[:12]
            if candidate_id == mod_id:
                selected = candidate
                break
        if selected is None:
            raise HelperError("not_found", "Mod was not found")
        disabled_root = self.paths.server_root / "mods-disabled"
        if disabled_root.is_symlink() or (disabled_root.exists() and not disabled_root.is_dir()):
            raise HelperError("unsafe_path", "Disabled-mod root is unsafe")
        disabled_root.mkdir(mode=0o755, exist_ok=True)
        panel_removed = disabled_root / "panel-removed"
        if panel_removed.is_symlink() or (panel_removed.exists() and not panel_removed.is_dir()):
            raise HelperError("unsafe_path", "Panel disabled-mod root is unsafe")
        panel_removed.mkdir(mode=0o755, exist_ok=True)
        destination_dir = panel_removed / uuid.uuid4().hex
        destination_dir.mkdir(parents=True, mode=0o755)
        destination = destination_dir / selected.name
        os.replace(selected, destination)
        return {
            "filename": selected.name,
            "moved_to": str(destination),
            "restart_required": True,
            "recovery_point": str(destination_dir),
        }

    def set_backup_schedule(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"hour", "minute", "keep"})
        hour, minute, keep = params["hour"], params["minute"], params["keep"]
        if (
            not isinstance(hour, int)
            or isinstance(hour, bool)
            or not 0 <= hour <= 23
            or not isinstance(minute, int)
            or isinstance(minute, bool)
            or not 0 <= minute <= 59
            or not isinstance(keep, int)
            or isinstance(keep, bool)
            or not 3 <= keep <= 30
        ):
            raise HelperError("validation_failed", "Backup schedule is invalid")
        self.paths.timer_dropin.parent.mkdir(parents=True, exist_ok=True)
        self.paths.backup_service_dropin.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"[Timer]\nOnCalendar=\nOnCalendar=*-*-* {hour:02d}:{minute:02d}:00\nPersistent=true\n"
        )
        self._ensure_recovery_root()
        recovery = self.paths.recovery_root / ("timer-" + uuid.uuid4().hex)
        recovery.mkdir(parents=True, mode=0o700)
        for source, backup_name in (
            (self.paths.timer_dropin, "timer-mc-panel.conf"),
            (self.paths.backup_service_dropin, "service-mc-panel.conf"),
        ):
            if source.is_symlink():
                raise HelperError("unsafe_path", "Systemd drop-in must not be a symlink")
            if source.exists():
                shutil.copy2(source, recovery / backup_name)
            else:
                (recovery / f"{backup_name}.absent").touch(mode=0o600)
        try:
            self._atomic_root_file(self.paths.timer_dropin, content)
            self._atomic_root_file(
                self.paths.backup_service_dropin,
                f"[Service]\nEnvironment=KEEP_BACKUPS={keep}\n",
            )
            _systemctl("daemon-reload")
            _run(["/usr/bin/systemctl", "restart", self.paths.backup_timer], 30)
        except Exception:
            self._restore_schedule_dropins(recovery)
            try:
                _systemctl("daemon-reload")
                _run(["/usr/bin/systemctl", "restart", self.paths.backup_timer], 30)
            except HelperError as rollback_exc:
                raise HelperError(
                    "rollback_failed",
                    "Backup schedule failed and its systemd rollback could not be verified",
                    {"recovery_point": str(recovery)},
                ) from rollback_exc
            raise
        return {
            "hour": hour,
            "minute": minute,
            "keep": keep,
            "recovery_point": str(recovery),
        }

    def _restore_schedule_dropins(self, recovery: Path) -> None:
        for destination, backup_name in (
            (self.paths.timer_dropin, "timer-mc-panel.conf"),
            (self.paths.backup_service_dropin, "service-mc-panel.conf"),
        ):
            backup = recovery / backup_name
            absent = recovery / f"{backup_name}.absent"
            if backup.is_file() and not backup.is_symlink():
                self._atomic_root_file(destination, backup.read_text(encoding="utf-8"))
            elif absent.is_file() and not absent.is_symlink():
                destination.unlink(missing_ok=True)
            else:
                raise HelperError("rollback_failed", "Backup schedule recovery data is incomplete")

    @staticmethod
    def _atomic_root_file(path: Path, content: str) -> None:
        if path.is_symlink():
            raise HelperError("unsafe_path", "Systemd drop-in must not be a symlink")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".mc-panel-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def restore_backup(self, params: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(params, {"backup_id"})
        backup_id = params["backup_id"]
        if not isinstance(backup_id, str) or not re.fullmatch(r"backup_[a-f0-9]{12}", backup_id):
            raise HelperError("validation_failed", "Backup ID is invalid")
        archive = self._find_backup(backup_id)
        verified_archive = self._verify_archive(archive)
        self._record_verified_archive(archive, verified_archive)
        self._ensure_recovery_root()
        for name in TARGETS:
            current = self.paths.server_root / name
            if current.is_symlink():
                raise HelperError("unsafe_path", f"Current restore target is a symlink: {name}")
        required_space = archive.stat().st_size * 3 + 5 * 1024**3
        if shutil.disk_usage(self.paths.server_root).free < required_space:
            raise HelperError("insufficient_disk_space", "Not enough disk space for staged restore")

        before_safety_backup = set(self.paths.backup_root.glob(self.paths.backup_archive_glob))
        _run(
            [str(self.paths.backup_command)],
            900,
            env_overrides={"KEEP_BACKUPS": str(len(before_safety_backup) + 1)},
        )
        safety_backups = sorted(
            set(self.paths.backup_root.glob(self.paths.backup_archive_glob)) - before_safety_backup
        )
        if not safety_backups:
            raise HelperError("verification_failed", "Restore safety backup was not created")
        safety_backup = safety_backups[-1]
        verified_safety_backup = self._verify_archive(safety_backup)
        self._record_verified_archive(safety_backup, verified_safety_backup)
        self._assert_archive_identity(archive, verified_archive)
        try:
            _systemctl("stop", self.paths.server_service)
            if _is_active(self.paths.server_service):
                raise HelperError("verification_failed", "Minecraft did not stop for restore")
        except HelperError as stop_exc:
            try:
                if not _is_active(self.paths.server_service):
                    self._ensure_minecraft_ready(timeout=240)
            except HelperError as recovery_exc:
                raise HelperError(
                    "rollback_failed",
                    "Restore preparation failed and Minecraft could not be recovered",
                    {
                        "safety_backup": safety_backup.name,
                        "stop_error": stop_exc.code,
                        "recovery_error": recovery_exc.code,
                    },
                ) from stop_exc
            raise

        restore_id = "restore-" + uuid.uuid4().hex
        staging = self.paths.server_root.parent / (".mc-panel-" + restore_id)
        recovery = self.paths.recovery_root / restore_id
        failed_new = recovery / "failed-new-data"
        moved_old: list[str] = []
        installed_new: list[str] = []
        try:
            staging.mkdir(mode=0o700)
            recovery.mkdir(parents=True, mode=0o700)
            self._extract_verified_archive(archive, verified_archive, staging)
            uid, gid = _server_ids(self.paths.console_user)
            for name in TARGETS:
                incoming = staging / name
                if not incoming.exists():
                    continue
                _chown_tree(incoming, uid, gid)
                current = self.paths.server_root / name
                if current.exists():
                    os.replace(current, recovery / name)
                    moved_old.append(name)
                os.replace(incoming, current)
                installed_new.append(name)
            _systemctl("start", self.paths.server_service)
            if not _is_active(self.paths.server_service) or not _wait_for_port(
                self.paths.server_port, 240
            ):
                raise HelperError("verification_failed", "Restored server did not become ready")
            return {
                "backup": archive.name,
                "safety_backup": safety_backup.name,
                "recovery_point": str(recovery),
                "restored": installed_new,
                "service_active": True,
            }
        except Exception as original:
            try:
                self._rollback_restore(recovery, failed_new, moved_old, installed_new)
            except Exception as rollback_exc:
                rollback_details = (
                    rollback_exc.details
                    if isinstance(rollback_exc, HelperError)
                    else {"rollback_error": type(rollback_exc).__name__}
                )
                raise HelperError(
                    "rollback_failed",
                    "Restore failed and the original server could not be fully recovered",
                    {
                        **rollback_details,
                        "recovery_point": str(recovery),
                        "failed_new_data": str(failed_new),
                        "safety_backup": safety_backup.name,
                    },
                ) from original
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _rollback_restore(
        self,
        recovery: Path,
        failed_new: Path,
        moved_old: list[str],
        installed_new: list[str],
    ) -> None:
        errors: list[str] = []
        try:
            _systemctl("stop", self.paths.server_service)
        except HelperError as exc:
            errors.append(f"stop: {exc.code}")
        if _is_active(self.paths.server_service):
            errors.append("stop: service_still_active")
        else:
            try:
                recovery.mkdir(parents=True, mode=0o700, exist_ok=True)
                failed_new.mkdir(mode=0o700, exist_ok=True)
                for name in reversed(installed_new):
                    current = self.paths.server_root / name
                    if current.exists():
                        os.replace(current, failed_new / name)
                for name in reversed(moved_old):
                    old = recovery / name
                    if old.exists():
                        os.replace(old, self.paths.server_root / name)
                    else:
                        errors.append(f"restore: missing_{name}")
            except OSError as exc:
                errors.append(f"restore: {type(exc).__name__}")

            try:
                _systemctl("start", self.paths.server_service)
            except HelperError as exc:
                errors.append(f"start: {exc.code}")
            if not _is_active(self.paths.server_service) or not _wait_for_port(
                self.paths.server_port, 240
            ):
                errors.append("start: readiness_verification_failed")

        if errors:
            raise HelperError(
                "rollback_failed",
                "Restore rollback could not be verified",
                {
                    "recovery_point": str(recovery),
                    "failed_new_data": str(failed_new),
                    "errors": errors,
                },
            )

    def _ensure_recovery_root(self) -> None:
        root = self.paths.recovery_root
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise HelperError("unsafe_path", "Recovery root is unsafe")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _find_backup(self, backup_id: str) -> Path:
        for candidate in self.paths.backup_root.glob(self.paths.backup_archive_glob):
            candidate_id = self._backup_id(candidate)
            if candidate_id == backup_id and candidate.is_file() and not candidate.is_symlink():
                return candidate
        raise HelperError("not_found", "Backup was not found")

    @staticmethod
    def _backup_id(archive: Path) -> str:
        return "backup_" + hashlib.sha256(archive.name.encode()).hexdigest()[:12]

    def _verify_archive(self, archive: Path) -> VerifiedArchive:
        checksum = Path(str(archive) + ".sha256")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            checksum_descriptor = os.open(checksum, flags)
        except OSError as exc:
            raise HelperError(
                "backup_verification_failed", "Backup checksum file is missing"
            ) from exc
        try:
            with os.fdopen(checksum_descriptor, "rb") as checksum_handle:
                checksum_stat = os.fstat(checksum_handle.fileno())
                if (
                    not stat.S_ISREG(checksum_stat.st_mode)
                    or checksum_stat.st_size <= 0
                    or checksum_stat.st_size > MAX_CHECKSUM_SIZE
                ):
                    raise HelperError(
                        "backup_verification_failed", "Backup checksum file is invalid"
                    )
                raw_checksum = checksum_handle.read(MAX_CHECKSUM_SIZE + 1)
                if len(raw_checksum) > MAX_CHECKSUM_SIZE:
                    raise HelperError(
                        "backup_verification_failed", "Backup checksum file is invalid"
                    )
                checksum_tokens = raw_checksum.decode("ascii", errors="strict").split()
        except HelperError:
            raise
        except (OSError, UnicodeError) as exc:
            raise HelperError(
                "backup_verification_failed", "Backup checksum file could not be read"
            ) from exc
        if not checksum_tokens or not re.fullmatch(r"[a-f0-9]{64}", checksum_tokens[0]):
            raise HelperError("backup_verification_failed", "Backup checksum file is invalid")
        expected = checksum_tokens[0]

        try:
            descriptor = os.open(archive, flags)
        except OSError as exc:
            raise HelperError("backup_verification_failed", "Backup archive is unsafe") from exc
        try:
            with os.fdopen(descriptor, "rb") as handle:
                initial = os.fstat(handle.fileno())
                if not stat.S_ISREG(initial.st_mode):
                    raise HelperError("backup_verification_failed", "Backup archive is unsafe")
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                hashed = os.fstat(handle.fileno())
                identity = VerifiedArchive.from_stat(digest.hexdigest(), initial)
                if not identity.matches(hashed):
                    raise HelperError(
                        "backup_verification_failed",
                        "Backup changed while it was being checked",
                    )
                if identity.sha256 != expected:
                    raise HelperError(
                        "backup_verification_failed", "Backup checksum does not match"
                    )
                handle.seek(0)
                try:
                    with tarfile.open(fileobj=handle, mode="r:gz") as bundle:
                        members = bundle.getmembers()
                        self._validate_members(members)
                        roots = {
                            PurePosixPath(member.name).parts[0] for member in members if member.name
                        }
                except (OSError, tarfile.TarError) as exc:
                    raise HelperError(
                        "backup_verification_failed", "Backup archive could not be read"
                    ) from exc
                final = os.fstat(handle.fileno())
                if not identity.matches(final):
                    raise HelperError(
                        "backup_verification_failed",
                        "Backup changed while it was being checked",
                    )
        except HelperError:
            raise
        except OSError as exc:
            raise HelperError(
                "backup_verification_failed", "Backup archive could not be read"
            ) from exc
        try:
            path_stat = archive.lstat()
        except OSError as exc:
            raise HelperError("backup_verification_failed", "Backup archive disappeared") from exc
        if not identity.matches(path_stat):
            raise HelperError(
                "backup_verification_failed",
                "Backup changed while it was being checked",
            )
        required = {
            "world",
            "config",
            "defaultconfigs",
            "kubejs",
            "mods",
            "server.properties",
        }
        if not required.issubset(roots):
            raise HelperError("backup_verification_failed", "Backup is missing required data")
        return identity

    @staticmethod
    def _assert_archive_identity(archive: Path, verified: VerifiedArchive) -> None:
        try:
            value = archive.lstat()
        except OSError as exc:
            raise HelperError("backup_verification_failed", "Backup archive disappeared") from exc
        if not verified.matches(value):
            raise HelperError("backup_verification_failed", "Backup changed after it was checked")

    def _extract_verified_archive(
        self, archive: Path, verified: VerifiedArchive, destination: Path
    ) -> None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(archive, flags)
        except OSError as exc:
            raise HelperError("backup_verification_failed", "Backup archive is unsafe") from exc
        try:
            with os.fdopen(descriptor, "rb") as handle:
                if not verified.matches(os.fstat(handle.fileno())):
                    raise HelperError(
                        "backup_verification_failed", "Backup changed before extraction"
                    )
                with tarfile.open(fileobj=handle, mode="r:gz") as bundle:
                    members = bundle.getmembers()
                    self._validate_members(members)
                    bundle.extractall(destination, members=members, filter="data")
                if not verified.matches(os.fstat(handle.fileno())):
                    raise HelperError(
                        "backup_verification_failed", "Backup changed during extraction"
                    )
        except HelperError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise HelperError(
                "backup_verification_failed", "Backup archive could not be extracted"
            ) from exc
        self._assert_archive_identity(archive, verified)

    def _record_verified_archive(self, archive: Path, verified: VerifiedArchive) -> str:
        self._assert_archive_identity(archive, verified)
        root = self.paths.backup_root / VERIFICATION_DIRECTORY
        if self.paths.backup_root.is_symlink() or not self.paths.backup_root.is_dir():
            raise HelperError("unsafe_path", "Backup root is unsafe")
        created = False
        try:
            root.mkdir(mode=0o755)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise HelperError(
                "backup_verification_failed",
                "Backup verification store could not be created",
            ) from exc
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            directory = os.open(root, directory_flags)
        except OSError as exc:
            raise HelperError("unsafe_path", "Backup verification store is unsafe") from exc
        try:
            root_stat = os.fstat(directory)
            if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.geteuid():
                raise HelperError("unsafe_path", "Backup verification store is unsafe")
            if created:
                os.fchmod(directory, 0o755)
                root_stat = os.fstat(directory)
            if stat.S_IMODE(root_stat.st_mode) != 0o755:
                raise HelperError("unsafe_path", "Backup verification store is unsafe")

            verified_at = datetime.now(UTC).isoformat()
            payload = {
                "version": 1,
                "backup_id": self._backup_id(archive),
                "filename": archive.name,
                "size_bytes": verified.size_bytes,
                "mtime_ns": verified.mtime_ns,
                "ctime_ns": verified.ctime_ns,
                "device": verified.device,
                "inode": verified.inode,
                "sha256": verified.sha256,
                "verified_at": verified_at,
            }
            destination_name = f"{payload['backup_id']}.json"
            temporary_name = ".verify-" + uuid.uuid4().hex
            temporary_created = False
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory,
                )
                temporary_created = True
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(
                        payload,
                        handle,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fchmod(handle.fileno(), 0o644)
                    os.fsync(handle.fileno())
                os.replace(
                    temporary_name,
                    destination_name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                temporary_created = False
                os.fsync(directory)
            finally:
                if temporary_created:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary_name, dir_fd=directory)
            visible_root = root.lstat()
            if visible_root.st_dev != root_stat.st_dev or visible_root.st_ino != root_stat.st_ino:
                os.unlink(destination_name, dir_fd=directory)
                raise HelperError("unsafe_path", "Backup verification store moved")
            return verified_at
        except HelperError:
            raise
        except OSError as exc:
            raise HelperError(
                "backup_verification_failed",
                "Backup verification result could not be saved",
            ) from exc
        finally:
            os.close(directory)

    @staticmethod
    def _validate_members(members: list[tarfile.TarInfo]) -> None:
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.name
                or path.is_absolute()
                or ".." in path.parts
                or path.parts[0] not in ALLOWED_ARCHIVE_ROOTS
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise HelperError("unsafe_archive", "Backup contains an unsafe member")
            total += member.size
            if member.size > 8 * 1024**3 or total > 32 * 1024**3:
                raise HelperError("unsafe_archive", "Backup expands beyond the safety limit")


def _fail(error: HelperError) -> NoReturn:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": error.code, "message": error.message},
    }
    if error.details:
        payload["error"]["details"] = error.details
    encoded = json.dumps(payload, ensure_ascii=True)
    sys.stderr.write(encoded[:MAX_OUTPUT] + "\n")
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 1:
        _fail(HelperError("invalid_request", "Command-line arguments are not accepted"))
    if os.geteuid() != 0:
        _fail(HelperError("permission_denied", "Helper must run through the approved sudo rule"))
    try:
        request = parse_request(sys.stdin.buffer.read(MAX_INPUT + 1))
        response = ActionHelper(Paths.from_config()).execute(
            request["operation"], request["params"]
        )
        encoded = json.dumps(response, ensure_ascii=True)
        if len(encoded) > MAX_OUTPUT:
            raise HelperError("output_too_large", "Helper output exceeded the safety limit")
        sys.stdout.write(encoded + "\n")
    except HelperError as exc:
        _fail(exc)
    except Exception as exc:
        _fail(HelperError("internal_error", f"{type(exc).__name__}: operation failed"))


if __name__ == "__main__":
    main()
