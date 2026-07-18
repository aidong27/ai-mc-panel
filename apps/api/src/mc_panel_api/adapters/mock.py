from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..activity import summarize_activity


class MockMinecraftAdapter:
    """Deterministic adapter used for development and destructive-flow tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._running = True
        self._players = ["Alex"]
        self._properties: dict[str, Any] = {
            "difficulty": "normal",
            "gamemode": "survival",
            "max-players": 10,
            "pvp": False,
            "view-distance": 8,
            "simulation-distance": 3,
            "white-list": False,
            "motd": "星光好友服 - Java 版",
        }
        self._whitelist: list[str] = []
        self._operators: list[str] = []
        self._mods = self._load_mods(root)
        now = datetime.now(UTC)
        activity_times = [now - timedelta(days=2), now - timedelta(hours=2)]
        self._activity_events = []
        for index, joined_at in enumerate(activity_times, start=1):
            digest = hashlib.sha256(f"Alex|{joined_at.isoformat()}|{index}".encode()).hexdigest()
            self._activity_events.append(
                {
                    "id": f"activity_{digest}",
                    "player": "Alex",
                    "joined_at": joined_at.isoformat(),
                }
            )
        self._backups = [
            self._backup_record(now - timedelta(days=index), 2_200_000_000 + index * 1024)
            for index in range(3)
        ]
        self._backups[0]["verified"] = False
        self._backups[0]["verification_status"] = "checksum_present"
        self._backup_schedule = {"hour": 3, "minute": 30, "keep": 7, "kind": "cold"}
        local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        next_backup = local_now.replace(hour=3, minute=30, second=0, microsecond=0)
        if next_backup <= local_now:
            next_backup += timedelta(days=1)
        last_backup = next_backup - timedelta(days=1)
        self._backup_status = {
            "timer_active": True,
            "timer_enabled": True,
            "last_result": "success",
            "last_run_at": last_backup.isoformat(),
            "last_finished_at": (last_backup + timedelta(minutes=2)).isoformat(),
            "last_exit_code": 0,
            "next_run_at": next_backup.isoformat(),
        }
        self._log_lines = self._load_lines(
            root / "logs" / "latest.log",
            [
                "[Server thread/INFO] Done (2.360s)! For help, type help",
                "[Server thread/INFO] There are 1 of a max of 10 players online: Alex",
            ],
        )

    @staticmethod
    def _backup_record(created: datetime, size: int) -> dict[str, Any]:
        digest = hashlib.sha256(created.isoformat().encode()).hexdigest()
        return {
            "id": f"backup_{digest[:12]}",
            "created_at": created.isoformat(),
            "size_bytes": size,
            "verified": True,
            "verification_status": "verified",
            "kind": "cold",
        }

    @staticmethod
    def _load_lines(path: Path, fallback: list[str]) -> list[str]:
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return fallback.copy()

    @staticmethod
    def _load_mods(root: Path) -> list[dict[str, Any]]:
        fixture = root / "mods.json"
        if fixture.exists():
            raw: Any = json.loads(fixture.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
                raise ValueError("mock mods fixture must be a list of objects")
            return [dict(item) for item in raw]
        return [
            {
                "id": "mod_critters",
                "name": "Critters and Companions",
                "version": "2.2.2",
                "loader": "Forge",
                "filename": "crittersandcompanions-forge-2.2.2.jar",
                "enabled": True,
                "duplicate": False,
            },
            {
                "id": "mod_modernfix",
                "name": "ModernFix",
                "version": "5.24.3",
                "loader": "Forge",
                "filename": "modernfix-forge-5.24.3.jar",
                "enabled": True,
                "duplicate": False,
            },
        ]

    def _append_log(self, message: str, level: str = "INFO") -> None:
        timestamp = datetime.now(UTC).strftime("%d%b%Y %H:%M:%S")
        self._log_lines.append(f"[{timestamp}] [Server thread/{level}] {message}")
        self._log_lines = self._log_lines[-2000:]

    def fingerprint(self) -> str:
        payload = "minecraft.service|/mock/example-server|minecraft|1.20.1|47.4.0|17"
        return hashlib.sha256(payload.encode()).hexdigest()

    def get_status(self) -> dict[str, Any]:
        state = "running" if self._running else "stopped"
        return {
            "state": state,
            "healthy": self._running,
            "friendly_status": "一切正常" if self._running else "服务器已停止",
            "service": "minecraft.service",
            "minecraft_version": "1.20.1",
            "loader": "Forge",
            "loader_version": "47.4.0",
            "java_version": "17.0.19",
            "uptime_seconds": 62_400 if self._running else 0,
            "fingerprint": self.fingerprint(),
            "adapter": "mock",
        }

    def get_metrics(self) -> dict[str, Any]:
        return {
            "cpu_percent": 12.4 if self._running else 1.2,
            "memory_used_bytes": 5_600_000_000 if self._running else 900_000_000,
            "memory_total_bytes": 16_000_000_000,
            "disk_used_bytes": 26_000_000_000,
            "disk_total_bytes": 270_000_000_000,
            "tps": 20.0 if self._running else None,
            "summary": {
                "cpu": "CPU 使用正常",
                "memory": "内存充足",
                "disk": "磁盘空间充足",
                "performance": "服务器运行流畅" if self._running else "服务器当前未运行",
            },
        }

    def list_players(self) -> dict[str, Any]:
        players = self._players if self._running else []
        return {"online": len(players), "maximum": 10, "players": players.copy()}

    def get_player_activity(self, days: int = 7) -> dict[str, Any]:
        scan = self.scan_player_activity(days)
        now = datetime.fromisoformat(str(scan["generated_at"]))
        return summarize_activity(
            scan["events"],
            days=days,
            now=now,
            coverage_start=datetime.fromisoformat(str(scan["coverage_start"])),
            coverage_end=datetime.fromisoformat(str(scan["coverage_end"])),
            coverage_complete=True,
            truncated=False,
            source="mock_login_logs",
        )

    def scan_player_activity(self, days: int = 30) -> dict[str, Any]:
        if not 1 <= days <= 30:
            raise ValueError("activity days must be between 1 and 30")
        now = datetime.now(UTC)
        first_day = now.date() - timedelta(days=days - 1)
        coverage_start = datetime.combine(first_day, datetime.min.time(), UTC)
        return {
            "events": [
                event
                for event in self._activity_events
                if datetime.fromisoformat(str(event["joined_at"])) >= coverage_start
            ],
            "generated_at": now.isoformat(),
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": now.isoformat(),
            "truncated": False,
            "scan_complete": True,
            "files_read": 1,
            "files_total": 1,
            "source": "mock_login_logs",
        }

    def read_logs(
        self, source: str = "latest", lines: int = 200, severity: list[str] | None = None
    ) -> dict[str, Any]:
        selected = self._log_lines
        if severity:
            selected = [line for line in selected if any(f"/{item}]" in line for item in severity)]
        selected = selected[-lines:]
        return {"source": source, "lines": selected, "next_cursor": str(len(self._log_lines))}

    def list_crash_reports(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "crash_20260615_201528",
                "created_at": "2026-06-15T20:15:28+08:00",
                "size_bytes": 154_681,
                "summary": "Ticking entity: grappling_hook",
            }
        ]

    def read_crash_report(self, report_id: str) -> dict[str, Any]:
        if report_id != "crash_20260615_201528":
            raise KeyError("crash report not found")
        return {
            "id": report_id,
            "summary": "一个抓钩实体在游戏刻更新时出错。",
            "likely_mod": "Critters and Companions",
            "excerpt": "Description: Ticking entity\nEntity: crittersandcompanions:grappling_hook",
        }

    def list_mods(self) -> list[dict[str, Any]]:
        return [item.copy() for item in self._mods]

    def get_mod_upload(self, upload_id: str) -> dict[str, Any]:
        if not upload_id.startswith("upload_"):
            raise KeyError("mod upload not found")
        return {
            "id": upload_id,
            "filename": "uploaded-test-mod.jar",
            "size_bytes": 8 * 1024 * 1024,
            "metadata": "META-INF/mods.toml",
        }

    def get_properties(self) -> dict[str, Any]:
        return self._properties.copy()

    def list_whitelist(self) -> list[str]:
        return self._whitelist.copy()

    def list_operators(self) -> list[str]:
        return self._operators.copy()

    def list_backups(self) -> list[dict[str, Any]]:
        return [item.copy() for item in self._backups]

    def get_backup_schedule(self) -> dict[str, Any]:
        return self._backup_schedule.copy()

    def get_backup_status(self) -> dict[str, Any]:
        return self._backup_status.copy()

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action == "start_server":
                self._running = True
                self._append_log("Done (2.100s)! For help, type help")
            elif action == "stop_server":
                self._append_log("Stopping server")
                self._running = False
                self._players = []
            elif action == "restart_server":
                self._append_log("Stopping server for a safe restart")
                self._running = False
                self._running = True
                self._append_log("Done (2.180s)! For help, type help")
            elif action == "send_announcement":
                self._append_log(f"[Server] {params['message']}")
            elif action == "send_console_command":
                self._append_log(f"Executed console command: {params['command']}")
            elif action == "create_backup":
                created = datetime.now(UTC)
                backup = self._backup_record(created, 2_200_000_000)
                self._backups.insert(0, backup)
                self._backups = self._backups[:7]
                return {"action": action, "backup": backup, "verified": True}
            elif action == "verify_backup":
                selected_backup: dict[str, Any] | None = None
                for item in self._backups:
                    if item["id"] == params["backup_id"]:
                        selected_backup = item
                        break
                if selected_backup is None:
                    raise ValueError("backup not found")
                selected_backup["verified"] = True
                selected_backup["verification_status"] = "verified"
                return {
                    "action": action,
                    "backup": selected_backup.copy(),
                    "verified": True,
                }
            elif action == "edit_server_property":
                self._properties[str(params["key"])] = params["value"]
            elif action == "add_whitelist_player":
                if params["player"] not in self._whitelist:
                    self._whitelist.append(params["player"])
            elif action == "remove_whitelist_player":
                self._whitelist = [name for name in self._whitelist if name != params["player"]]
            elif action == "add_operator":
                if params["player"] not in self._operators:
                    self._operators.append(params["player"])
            elif action == "remove_operator":
                self._operators = [name for name in self._operators if name != params["player"]]
            elif action == "install_mod":
                mod_id = "mod_uploaded_" + params["upload_id"][-8:]
                self._mods.append(
                    {
                        "id": mod_id,
                        "name": "Uploaded test mod",
                        "version": "1.0.0",
                        "loader": "Forge",
                        "filename": f"{mod_id}.jar",
                        "enabled": True,
                        "duplicate": False,
                    }
                )
            elif action == "disable_mod":
                found = False
                for mod in self._mods:
                    if mod["id"] == params["mod_id"]:
                        mod["enabled"] = False
                        found = True
                if not found:
                    raise ValueError("mod not found")
            elif action == "set_backup_schedule":
                self._backup_schedule.update(params)
                return {"action": action, "verified": True, "schedule": params.copy()}
            elif action == "restore_backup":
                if not any(item["id"] == params["backup_id"] for item in self._backups):
                    raise ValueError("backup not found")
                self._append_log("Mock backup restored and verified")
            else:
                raise ValueError(f"unsupported action: {action}")
            return {"action": action, "verified": True, "status": self.get_status()}
