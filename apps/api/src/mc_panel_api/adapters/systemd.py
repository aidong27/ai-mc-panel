from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import re
import shutil
import stat
import subprocess
import time
import tomllib
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from email.parser import Parser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psutil

from ..activity import summarize_activity
from ..config import Settings
from ..detection import detect_server_identity
from .base import AdapterOperationError

MAX_PLAYER_LOG_BYTES = 16 * 1024 * 1024
MAX_ACTIVITY_LOG_BYTES = 32 * 1024 * 1024
MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _run(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    # Callers provide fixed executable paths and argv; no shell is involved.
    return subprocess.run(  # noqa: S603
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
    )


class SystemdMinecraftAdapter:
    """Configuration-driven adapter for one local systemd Minecraft service."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.local_timezone = ZoneInfo(settings.server_timezone)

    def _unit_fragment(self) -> Path | None:
        result = _run(
            [
                "/usr/bin/systemctl",
                "show",
                self.settings.server_service,
                "--property=FragmentPath",
                "--value",
            ]
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        path = Path(value) if value else None
        return path if path is not None and path.is_absolute() else None

    def fingerprint(self) -> str:
        parts = [
            self.settings.server_service,
            str(self.settings.server_root),
            str(self.settings.server_startup_path),
            "minecraft",
        ]
        for path in (self._unit_fragment(), self.settings.server_startup_path):
            try:
                if path is None or path.is_symlink() or not path.is_file():
                    raise OSError
                parts.append(hashlib.sha256(path.read_bytes()).hexdigest())
            except OSError:
                parts.append("missing")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _systemd(self) -> dict[str, str]:
        result = _run(
            [
                "/usr/bin/systemctl",
                "show",
                self.settings.server_service,
                "--property=ActiveState,SubState,MainPID,NRestarts,MemoryCurrent",
            ]
        )
        if result.returncode != 0:
            return {"ActiveState": "unknown", "SubState": "unknown", "MainPID": "0"}
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def get_status(self) -> dict[str, Any]:
        values = self._systemd()
        running = values.get("ActiveState") == "active" and values.get("SubState") == "running"
        pid = int(values.get("MainPID", "0") or 0)
        uptime_seconds = 0
        if running and pid and psutil.pid_exists(pid):
            try:
                uptime_seconds = max(0, int(time.time() - psutil.Process(pid).create_time()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                uptime_seconds = 0
        current_fingerprint = self.fingerprint()
        identity = detect_server_identity(
            server_root=self.settings.server_root,
            pid=pid,
            minecraft_version_hint=self.settings.minecraft_version_hint,
            loader_hint=self.settings.loader_hint,
            loader_version_hint=self.settings.loader_version_hint,
            java_version_hint=self.settings.java_version_hint,
        )
        structure_approved = bool(self.settings.approved_fingerprint) and hmac.compare_digest(
            current_fingerprint, self.settings.approved_fingerprint
        )
        return {
            "state": "running" if running else values.get("ActiveState", "unknown"),
            "healthy": running,
            "friendly_status": (
                "一切正常"
                if running
                else "服务器已停止"
                if values.get("ActiveState") == "inactive"
                else "服务器需要检查"
            ),
            "service": self.settings.server_service,
            "minecraft_version": identity.minecraft_version,
            "loader": identity.loader,
            "loader_version": identity.loader_version,
            "java_version": identity.java_version,
            "identity_sources": identity.sources,
            "uptime_seconds": uptime_seconds,
            "nrestarts": int(values.get("NRestarts", "0") or 0),
            "fingerprint": current_fingerprint,
            "adapter": "systemd",
            "structure_approved": structure_approved,
            "writes_enabled": self.settings.production_writes_enabled and structure_approved,
        }

    def get_metrics(self) -> dict[str, Any]:
        values = self._systemd()
        pid = int(values.get("MainPID", "0") or 0)
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk_target = self.settings.server_root if self.settings.server_root.exists() else Path("/")
        disk = shutil.disk_usage(disk_target)
        try:
            process_rss = int(values.get("MemoryCurrent", "0") or 0)
        except ValueError:
            process_rss = 0
        if not process_rss and pid and psutil.pid_exists(pid):
            try:
                process = psutil.Process(pid)
                processes = [process, *process.children(recursive=True)]
                for item in processes:
                    try:
                        if item.is_running():
                            process_rss += item.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_rss = 0
        tps = self._latest_tps()
        return {
            "cpu_percent": cpu,
            "memory_used_bytes": memory.used,
            "memory_total_bytes": memory.total,
            "minecraft_rss_bytes": process_rss,
            "disk_used_bytes": disk.used,
            "disk_total_bytes": disk.total,
            "tps": tps,
            "summary": {
                "cpu": "CPU 使用正常" if cpu < 70 else "CPU 使用偏高",
                "memory": "内存充足" if memory.percent < 75 else "内存偏高",
                "disk": "磁盘空间充足" if disk.free / disk.total > 0.15 else "磁盘空间不足",
                "performance": (
                    "暂无 TPS 数据"
                    if tps is None
                    else "服务器运行流畅"
                    if tps >= 19
                    else "服务器可能有些卡"
                ),
            },
        }

    def _latest_tps(self) -> float | None:
        log = self.settings.server_root / "logs/latest.log"
        try:
            if log.is_symlink() or not log.is_file():
                return None
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 256_000))
                tail = handle.read(256_000).decode("utf-8", errors="replace")
        except OSError:
            return None
        matches = re.findall(r"Overall: Mean tick time: .*? Mean TPS: ([0-9.]+)", tail)
        return float(matches[-1]) if matches else None

    def list_players(self) -> dict[str, Any]:
        try:
            maximum = int(self.get_properties().get("max-players", 10))
        except (TypeError, ValueError):
            maximum = 10
        if self._systemd().get("ActiveState") != "active":
            return {"online": 0, "maximum": maximum, "players": [], "stale": False}
        path = self.settings.server_root / "logs/latest.log"
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - MAX_PLAYER_LOG_BYTES))
                raw = handle.read(MAX_PLAYER_LOG_BYTES)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            complete = size <= MAX_PLAYER_LOG_BYTES
            if not complete and lines:
                lines = lines[1:]
        except OSError:
            lines, complete = [], False

        players: set[str] = set()
        online = 0
        known = complete
        listing = re.compile(r"There are (\d+) of a max of (\d+) players online: ?(.*)$")
        joined = re.compile(r"\b([A-Za-z0-9_]{1,16}) joined the game$")
        left = re.compile(r"\b([A-Za-z0-9_]{1,16}) left the game$")
        for line in lines:
            if "Done (" in line and "For help" in line:
                players.clear()
                online = 0
                known = True
                continue
            match = listing.search(line)
            if match:
                players = {name.strip() for name in match.group(3).split(",") if name.strip()}
                online = int(match.group(1))
                maximum = int(match.group(2))
                known = True
                continue
            match = joined.search(line)
            if match and known:
                players.add(match.group(1))
                online = len(players)
                continue
            match = left.search(line)
            if match and known:
                players.discard(match.group(1))
                online = len(players)
        return {
            "online": online if known else 0,
            "maximum": maximum,
            "players": sorted(players) if known else [],
            "stale": not known,
            "source": "log_events",
        }

    def _parse_log_timestamp(self, line: str) -> datetime | None:
        match = re.match(
            r"^\[(\d{2})([A-Z][a-z]{2})(\d{4}) "
            r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\]",
            line,
        )
        if not match:
            return None
        day, month_name, year, hour, minute, second, fraction = match.groups()
        month = MONTHS.get(month_name)
        if month is None:
            return None
        try:
            return datetime(
                int(year),
                month,
                int(day),
                int(hour),
                int(minute),
                int(second),
                int((fraction or "0").ljust(6, "0")),
                tzinfo=self.local_timezone,
            )
        except ValueError:
            return None

    @staticmethod
    def _activity_paths(log_root: Path) -> list[Path]:
        candidates = [log_root / "latest.log", log_root / "latest.log.1"]
        candidates.extend(log_root.glob("latest.log.*.gz"))

        def age(path: Path) -> int:
            match = re.search(r"latest\.log\.(\d+)(?:\.gz)?$", path.name)
            return int(match.group(1)) if match else 0

        safe_candidates = []
        for path in candidates:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024**2:
                    safe_candidates.append(path)
            except OSError:
                # Log rotation can replace a candidate between glob and stat.
                continue
        # Read the newest files first so a global byte limit never hides recent activity.
        return sorted(safe_candidates, key=age)[:14]

    def scan_player_activity(self, days: int = 30) -> dict[str, Any]:
        if not 1 <= days <= 30:
            raise ValueError("activity days must be between 1 and 30")
        now = datetime.now(self.local_timezone)
        first_day = now.date() - timedelta(days=days - 1)
        requested_start = datetime.combine(first_day, datetime.min.time(), self.local_timezone)
        timestamp_start: datetime | None = None
        timestamp_end: datetime | None = None
        events: dict[str, dict[str, str]] = {}
        bytes_read = 0
        read_errors = 0
        files_read = 0

        login_pattern = re.compile(
            r"\[net\.minecraft\.server\.players\.PlayerList/\]: "
            r"([A-Za-z0-9_]{1,16})\s*\[/.*? logged in with entity id (-?\d+)\b"
        )
        paths = self._activity_paths(self.settings.server_root / "logs")
        for path in paths:
            opener = gzip.open if path.suffix == ".gz" else open
            try:
                with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                    files_read += 1
                    for line in handle:
                        bytes_read += len(line.encode("utf-8", errors="replace"))
                        if bytes_read > MAX_ACTIVITY_LOG_BYTES:
                            break
                        timestamp = self._parse_log_timestamp(line)
                        if timestamp is None:
                            continue
                        timestamp_start = (
                            timestamp
                            if timestamp_start is None or timestamp < timestamp_start
                            else timestamp_start
                        )
                        timestamp_end = (
                            timestamp
                            if timestamp_end is None or timestamp > timestamp_end
                            else timestamp_end
                        )
                        match = login_pattern.search(line)
                        if match and timestamp >= requested_start:
                            player, entity_id = match.groups()
                            digest = hashlib.sha256(
                                f"{timestamp.isoformat()}|{player}|{entity_id}".encode()
                            ).hexdigest()
                            event_id = f"activity_{digest}"
                            events[event_id] = {
                                "id": event_id,
                                "player": player,
                                "joined_at": timestamp.isoformat(),
                            }
            except (OSError, EOFError, gzip.BadGzipFile):
                read_errors += 1
                continue
            if bytes_read > MAX_ACTIVITY_LOG_BYTES:
                break
        return {
            "events": list(events.values()),
            "generated_at": now.isoformat(),
            "coverage_start": timestamp_start.isoformat() if timestamp_start else None,
            "coverage_end": timestamp_end.isoformat() if timestamp_end else None,
            "truncated": bytes_read > MAX_ACTIVITY_LOG_BYTES,
            "scan_complete": bool(paths)
            and files_read == len(paths)
            and read_errors == 0
            and bytes_read <= MAX_ACTIVITY_LOG_BYTES,
            "files_read": files_read,
            "files_total": len(paths),
            "source": "bounded_minecraft_login_logs",
        }

    def get_player_activity(self, days: int = 7) -> dict[str, Any]:
        scan = self.scan_player_activity(days)
        now = datetime.fromisoformat(str(scan["generated_at"]))
        first_day = now.date() - timedelta(days=days - 1)
        requested_start = datetime.combine(first_day, datetime.min.time(), self.local_timezone)
        coverage_start = (
            datetime.fromisoformat(str(scan["coverage_start"]))
            if scan.get("coverage_start")
            else None
        )
        coverage_end = (
            datetime.fromisoformat(str(scan["coverage_end"])) if scan.get("coverage_end") else None
        )
        return summarize_activity(
            scan["events"],
            days=days,
            now=now,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            coverage_complete=bool(scan["scan_complete"])
            and coverage_start is not None
            and coverage_start <= requested_start,
            truncated=bool(scan["truncated"]),
            source="bounded_minecraft_login_logs",
        )

    def read_logs(
        self, source: str = "latest", lines: int = 200, severity: list[str] | None = None
    ) -> dict[str, Any]:
        paths = {
            "latest": self.settings.server_root / "logs/latest.log",
            "kubejs": self.settings.server_root / "logs/kubejs/server.log",
        }
        if source == "systemd":
            result = _run(
                [
                    "/usr/bin/journalctl",
                    "-u",
                    self.settings.server_service,
                    "-n",
                    str(lines),
                    "--no-pager",
                ]
            )
            if result.returncode != 0:
                return {
                    "source": source,
                    "lines": [],
                    "next_cursor": None,
                    "error": "log_unavailable",
                }
            selected = result.stdout.splitlines()
        else:
            path = paths[source]
            if path.is_symlink() or not path.is_file():
                return {
                    "source": source,
                    "lines": [],
                    "next_cursor": None,
                    "error": "log_unavailable",
                }
            try:
                with path.open("rb") as handle:
                    handle.seek(max(0, path.stat().st_size - 512_000))
                    selected = handle.read(512_000).decode("utf-8", errors="replace").splitlines()
            except OSError:
                return {
                    "source": source,
                    "lines": [],
                    "next_cursor": None,
                    "error": "log_unavailable",
                }
            selected = selected[-lines:]
        if severity:
            selected = [
                line for line in selected if any(f"/{level}]" in line for level in severity)
            ]
        return {"source": source, "lines": selected[-lines:], "next_cursor": None}

    def list_crash_reports(self) -> list[dict[str, Any]]:
        reports = []
        report_root = self.settings.server_root / "crash-reports"
        candidates = [
            path for path in report_root.glob("*.txt") if path.is_file() and not path.is_symlink()
        ]
        for path in sorted(candidates, reverse=True)[:50]:
            stat = path.stat()
            report_id = "crash_" + hashlib.sha256(path.name.encode()).hexdigest()[:12]
            reports.append(
                {
                    "id": report_id,
                    "filename": path.name,
                    "created_at": stat.st_mtime,
                    "size_bytes": stat.st_size,
                }
            )
        return reports

    def read_crash_report(self, report_id: str) -> dict[str, Any]:
        for report in self.list_crash_reports():
            if report["id"] == report_id:
                path = self.settings.server_root / "crash-reports" / str(report["filename"])
                text = path.read_text(encoding="utf-8", errors="replace")[:64_000]
                return {**report, "excerpt": text}
        raise KeyError("crash report not found")

    def list_mods(self) -> list[dict[str, Any]]:
        result = [
            self._mod_record(path)
            for path in sorted((self.settings.server_root / "mods").glob("*.jar"))
            if path.is_file() and not path.is_symlink()
        ]
        counts = Counter(str(item["_identity"]) for item in result)
        for item in result:
            identity = str(item.pop("_identity"))
            item["duplicate"] = counts[identity] > 1
        return result

    @staticmethod
    def _mod_record(path: Path) -> dict[str, Any]:
        name = path.stem
        version = "unknown"
        manifest_version = ""
        loader = "Forge-compatible"
        identity = path.stem.lower()
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "META-INF/MANIFEST.MF" in names:
                    manifest_info = archive.getinfo("META-INF/MANIFEST.MF")
                    if manifest_info.file_size <= 1024 * 1024:
                        manifest = Parser().parsestr(
                            archive.read(manifest_info).decode("utf-8", errors="replace")
                        )
                        manifest_version = str(
                            manifest.get("Implementation-Version")
                            or manifest.get("Specification-Version")
                            or ""
                        ).strip()
                if "META-INF/neoforge.mods.toml" in names:
                    metadata_name, loader = "META-INF/neoforge.mods.toml", "NeoForge"
                elif "META-INF/mods.toml" in names:
                    metadata_name, loader = "META-INF/mods.toml", "Forge"
                elif "fabric.mod.json" in names:
                    metadata_name, loader = "fabric.mod.json", "Fabric"
                elif "quilt.mod.json" in names:
                    metadata_name, loader = "quilt.mod.json", "Quilt"
                else:
                    metadata_name = ""
                if metadata_name:
                    info = archive.getinfo(metadata_name)
                    if info.file_size <= 1024 * 1024:
                        raw = archive.read(info)
                        if metadata_name.endswith(".toml"):
                            metadata = tomllib.loads(raw.decode("utf-8", errors="strict"))
                            mods = metadata.get("mods")
                            if isinstance(mods, list) and mods and isinstance(mods[0], dict):
                                first = mods[0]
                                identity = str(first.get("modId") or identity).lower()
                                name = str(first.get("displayName") or identity)
                                version = str(first.get("version") or version)
                        else:
                            metadata = json.loads(raw)
                            if metadata_name == "fabric.mod.json" and isinstance(metadata, dict):
                                identity = str(metadata.get("id") or identity).lower()
                                name = str(metadata.get("name") or identity)
                                version = str(metadata.get("version") or version)
                            elif isinstance(metadata, dict):
                                quilt = metadata.get("quilt_loader")
                                if isinstance(quilt, dict):
                                    identity = str(quilt.get("id") or identity).lower()
                                    version = str(quilt.get("version") or version)
                                    nested = quilt.get("metadata")
                                    if isinstance(nested, dict):
                                        name = str(nested.get("name") or identity)
                if not version or version == "unknown" or re.fullmatch(r"\$\{[^}]+\}", version):
                    version = manifest_version or "unknown"
        except (OSError, ValueError, zipfile.BadZipFile, tomllib.TOMLDecodeError):
            pass
        return {
            "id": "mod_" + hashlib.sha256(path.name.encode()).hexdigest()[:12],
            "name": name[:160],
            "version": version[:80],
            "loader": loader,
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "enabled": True,
            "duplicate": False,
            "_identity": identity[:160],
        }

    def get_properties(self) -> dict[str, Any]:
        allowed = {
            "difficulty",
            "gamemode",
            "max-players",
            "pvp",
            "view-distance",
            "simulation-distance",
            "white-list",
            "motd",
        }
        values: dict[str, Any] = {}
        path = self.settings.server_root / "server.properties"
        if path.is_symlink() or not path.is_file():
            return values
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in allowed:
                if key in {"pvp", "white-list"} and value.lower() in {"true", "false"}:
                    values[key] = value.lower() == "true"
                elif key in {"max-players", "view-distance", "simulation-distance"}:
                    try:
                        values[key] = int(value)
                    except ValueError:
                        values[key] = value
                else:
                    values[key] = value
        return values

    @staticmethod
    def _names(path: Path) -> list[str]:
        if path.is_symlink() or not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item["name"]) for item in data if isinstance(item, dict) and "name" in item]

    def list_whitelist(self) -> list[str]:
        return self._names(self.settings.server_root / "whitelist.json")

    def list_operators(self) -> list[str]:
        return self._names(self.settings.server_root / "ops.json")

    def list_backups(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(
            self.settings.backup_root.glob(self.settings.backup_archive_glob), reverse=True
        ):
            if not path.is_file() or path.is_symlink():
                continue
            digest = hashlib.sha256(path.name.encode()).hexdigest()
            checksum = path.with_suffix(path.suffix + ".sha256")
            checksum_present = checksum.is_file() and not checksum.is_symlink()
            result.append(
                {
                    "id": f"backup_{digest[:12]}",
                    "filename": path.name,
                    "created_at": path.stat().st_mtime,
                    "size_bytes": path.stat().st_size,
                    "verified": False,
                    "verification_status": "checksum_present" if checksum_present else "missing",
                    "kind": "cold",
                }
            )
        return result

    @staticmethod
    def _read_dropin(path: Path) -> str | None:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
                return None
            return path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            return None

    def get_backup_schedule(self) -> dict[str, Any]:
        schedule: dict[str, Any] = {"hour": 3, "minute": 30, "keep": 7, "kind": "cold"}
        calendars: list[str] = []
        for path in (
            self.settings.backup_timer_unit_path,
            self.settings.backup_timer_dropin_path,
        ):
            content = self._read_dropin(path)
            if content is None:
                continue
            for value in re.findall(r"^OnCalendar=(.*?)\s*$", content, re.MULTILINE):
                if not value:
                    calendars.clear()
                else:
                    calendars.append(value)
        if calendars:
            match = re.fullmatch(r"\*-\*-\* ([01][0-9]|2[0-3]):([0-5][0-9]):00", calendars[-1])
            if match:
                schedule["hour"], schedule["minute"] = (int(value) for value in match.groups())

        keep: int | None = None
        for path in (
            self.settings.backup_service_unit_path,
            self.settings.backup_service_dropin_path,
        ):
            content = self._read_dropin(path)
            if content is None:
                continue
            for value in re.findall(r"^Environment=(.*?)\s*$", content, re.MULTILINE):
                if not value:
                    keep = None
                    continue
                match = re.search(r'(?:^|[ "\'])KEEP_BACKUPS=([1-9][0-9]?)(?:$|[ "\'])', value)
                if match:
                    keep = int(match.group(1))
        if keep is None:
            command = self._read_dropin(self.settings.backup_command)
            if command is not None:
                match = re.search(r'KEEP="\$\{KEEP_BACKUPS:-([1-9][0-9]?)\}"', command)
                if match:
                    keep = int(match.group(1))
        if keep is not None and 3 <= keep <= 30:
            schedule["keep"] = keep
        return schedule

    def _systemd_timestamp(self, value: str | None) -> datetime | None:
        if not value or value == "n/a":
            return None
        match = re.fullmatch(
            r"(?:[A-Z][a-z]{2} )?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)? "
            r"([A-Za-z]+|[+-]\d{4})",
            value.strip(),
        )
        if match is None:
            return None
        timestamp, zone = match.groups()
        try:
            parsed = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        if zone in {"UTC", "GMT"}:
            return parsed.replace(tzinfo=UTC)
        if re.fullmatch(r"[+-]\d{4}", zone):
            sign = 1 if zone[0] == "+" else -1
            offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5])) * sign
            return parsed.replace(tzinfo=timezone(offset))
        return parsed.replace(tzinfo=self.local_timezone)

    @staticmethod
    def _unit_properties(unit: str, properties: list[str]) -> dict[str, str]:
        result = _run(
            [
                "/usr/bin/systemctl",
                "show",
                unit,
                *(f"--property={property_name}" for property_name in properties),
                "--no-pager",
            ]
        )
        if result.returncode != 0:
            return {}
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def get_backup_status(self) -> dict[str, Any]:
        service = self._unit_properties(
            self.settings.backup_service,
            [
                "ActiveState",
                "SubState",
                "Result",
                "ExecMainStatus",
                "ExecMainStartTimestamp",
                "ExecMainExitTimestamp",
            ],
        )
        timer = self._unit_properties(
            self.settings.backup_timer,
            ["ActiveState", "UnitFileState", "LastTriggerUSec", "NextElapseUSecRealtime"],
        )
        started = self._systemd_timestamp(service.get("ExecMainStartTimestamp"))
        finished = self._systemd_timestamp(service.get("ExecMainExitTimestamp"))
        triggered = self._systemd_timestamp(timer.get("LastTriggerUSec"))
        next_run = self._systemd_timestamp(timer.get("NextElapseUSecRealtime"))
        running = service.get("ActiveState") in {"active", "activating", "reloading"}
        exit_code: int | None
        try:
            exit_code = int(service["ExecMainStatus"]) if "ExecMainStatus" in service else None
        except ValueError:
            exit_code = None
        if not service:
            last_result = "unknown"
        elif running:
            last_result = "running"
        elif started is None and triggered is None:
            last_result = "never"
        elif service.get("Result") == "success" and exit_code == 0:
            last_result = "success"
        else:
            last_result = "failed"
        last_run = started or triggered
        return {
            "timer_active": timer.get("ActiveState") == "active" if timer else None,
            "timer_enabled": timer.get("UnitFileState") == "enabled" if timer else None,
            "last_result": last_result,
            "last_run_at": last_run.isoformat() if last_run else None,
            "last_finished_at": finished.isoformat() if finished else None,
            "last_exit_code": exit_code,
            "next_run_at": next_run.isoformat() if next_run else None,
        }

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.production_writes_enabled:
            raise PermissionError("Production writes are disabled")
        if not self.settings.approved_fingerprint or not hmac.compare_digest(
            self.fingerprint(), self.settings.approved_fingerprint
        ):
            raise PermissionError("Server fingerprint is not approved or has changed")
        helper = self.settings.helper_path
        if helper.is_symlink() or not helper.is_file():
            raise RuntimeError("Privileged helper is not installed")
        helper_stat = helper.stat()
        if helper_stat.st_uid != 0 or stat.S_IMODE(helper_stat.st_mode) & 0o022:
            raise RuntimeError("Privileged helper ownership or mode is unsafe")
        request = json.dumps({"operation": action, "params": params}, ensure_ascii=True)
        timeout = {
            "start_server": 300,
            "restart_server": 300,
            "create_backup": 1200,
            "restore_backup": 2400,
        }.get(action, 600)
        # The helper path comes from root-owned service configuration; JSON stays on stdin.
        result = subprocess.run(  # noqa: S603
            ["/usr/bin/sudo", str(helper)],
            input=request,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        if result.returncode != 0:
            try:
                failure = json.loads(result.stderr.splitlines()[-1])
                error = failure.get("error", {})
                code = str(error.get("code", "helper_failed"))
                message = str(error.get("message", "helper failed"))
                details = error.get("details", {})
                raise AdapterOperationError(
                    code,
                    message,
                    dict(details) if isinstance(details, dict) else {},
                )
            except (IndexError, AttributeError, json.JSONDecodeError):
                raise AdapterOperationError("helper_failed", "privileged action failed") from None
        payload: Any = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("helper returned an invalid response")
        return dict(payload)


# Backward-compatible import for deployments and extensions created before 0.4.0.
SystemdForgeAdapter = SystemdMinecraftAdapter
