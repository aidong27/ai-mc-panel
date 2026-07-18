from __future__ import annotations

import gzip
import json
import subprocess
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mc_panel_api.adapters import systemd as systemd_module
from mc_panel_api.adapters.base import AdapterOperationError
from mc_panel_api.adapters.systemd import SystemdForgeAdapter
from mc_panel_api.config import Settings


def _settings_for_root(settings: Settings, root: Path) -> Settings:
    return replace(settings, server_root=root, server_startup_path=root / "start.sh")


def _forge_jar(
    path: Path,
    *,
    mod_id: str,
    name: str,
    version: str,
    manifest_version: str | None = None,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if manifest_version:
            archive.writestr(
                "META-INF/MANIFEST.MF",
                f"Manifest-Version: 1.0\nImplementation-Version: {manifest_version}\n\n",
            )
        archive.writestr(
            "META-INF/mods.toml",
            "\n".join(
                [
                    'modLoader="javafml"',
                    'loaderVersion="[47,)"',
                    "[[mods]]",
                    f'modId="{mod_id}"',
                    f'displayName="{name}"',
                    f'version="{version}"',
                ]
            ),
        )


def test_mod_metadata_and_duplicates_are_detected(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    mods = root / "mods"
    mods.mkdir(parents=True)
    _forge_jar(mods / "example-a.jar", mod_id="example", name="Example Mod", version="1.0.0")
    _forge_jar(mods / "example-b.jar", mod_id="example", name="Example Mod", version="1.1.0")
    (mods / "linked.jar").symlink_to(mods / "example-a.jar")
    settings = _settings_for_root(settings, root)

    result = SystemdForgeAdapter(settings).list_mods()

    assert len(result) == 2
    assert {item["version"] for item in result} == {"1.0.0", "1.1.0"}
    assert all(item["name"] == "Example Mod" for item in result)
    assert all(item["loader"] == "Forge" for item in result)
    assert all(item["duplicate"] is True for item in result)


def test_mod_version_placeholder_uses_jar_manifest(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    mods = root / "mods"
    mods.mkdir(parents=True)
    _forge_jar(
        mods / "example.jar",
        mod_id="example",
        name="Example Mod",
        version="${file.jarVersion}",
        manifest_version="2.4.1",
    )
    settings = _settings_for_root(settings, root)

    result = SystemdForgeAdapter(settings).list_mods()

    assert result[0]["version"] == "2.4.1"


def test_read_surfaces_ignore_symlinks(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    logs = root / "logs"
    reports = root / "crash-reports"
    logs.mkdir(parents=True)
    reports.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("sensitive", encoding="utf-8")
    (logs / "latest.log").symlink_to(external)
    (reports / "crash-good.txt").write_text("safe report", encoding="utf-8")
    (reports / "crash-linked.txt").symlink_to(external)
    settings = _settings_for_root(settings, root)
    adapter = SystemdForgeAdapter(settings)

    logs_result = adapter.read_logs()
    assert logs_result["lines"] == []
    assert logs_result["error"] == "log_unavailable"
    assert [item["filename"] for item in adapter.list_crash_reports()] == ["crash-good.txt"]


def test_production_writes_require_the_approved_fingerprint(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    writable = replace(settings, production_writes_enabled=True, approved_fingerprint="")
    adapter = SystemdForgeAdapter(writable)
    monkeypatch.setattr(adapter, "fingerprint", lambda: "a" * 64)

    with pytest.raises(PermissionError, match="fingerprint"):
        adapter.execute("restart_server", {})


def test_player_list_tracks_join_and_leave_after_console_snapshot(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (root / "server.properties").write_text("max-players=10\n", encoding="utf-8")
    (logs / "latest.log").write_text(
        "\n".join(
            [
                '[Server thread/INFO] Done (2.3s)! For help, type "help"',
                "[Server thread/INFO] There are 1 of a max of 10 players online: Alice",
                "[Server thread/INFO] Bob joined the game",
                "[Server thread/INFO] Alice left the game",
            ]
        ),
        encoding="utf-8",
    )
    settings = _settings_for_root(settings, root)
    adapter = SystemdForgeAdapter(settings)
    monkeypatch.setattr(
        adapter,
        "_systemd",
        lambda: {"ActiveState": "active", "SubState": "running", "MainPID": "0"},
    )

    result = adapter.list_players()

    assert result["online"] == 1
    assert result["players"] == ["Bob"]
    assert result["stale"] is False


def test_missing_tps_is_reported_as_unknown(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text("server started\n", encoding="utf-8")
    settings = _settings_for_root(settings, root)

    assert SystemdForgeAdapter(settings)._latest_tps() is None


def test_stopped_player_list_keeps_configured_maximum(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "server.properties").write_text("max-players=24\n", encoding="utf-8")
    settings = _settings_for_root(settings, root)
    adapter = SystemdForgeAdapter(settings)
    monkeypatch.setattr(
        adapter,
        "_systemd",
        lambda: {"ActiveState": "inactive", "SubState": "dead", "MainPID": "0"},
    )

    assert adapter.list_players() == {
        "online": 0,
        "maximum": 24,
        "players": [],
        "stale": False,
    }


def test_player_activity_is_bounded_and_reports_log_coverage(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    logs = root / "logs"
    logs.mkdir(parents=True)
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone).replace(microsecond=0)
    old = now - timedelta(days=3)
    recent = now - timedelta(days=1)
    with gzip.open(logs / "latest.log.2.gz", "wt", encoding="utf-8") as handle:
        handle.write(f"[{old:%d%b%Y %H:%M:%S}.000] [Server thread/INFO] old log marker\n")
    (logs / "latest.log").write_text(
        "\n".join(
            [
                (
                    f"[{recent:%d%b%Y %H:%M:%S}.000] [Server thread/INFO] "
                    "[net.minecraft.server.players.PlayerList/]: "
                    "Alice [/203.0.113.9:25565] logged in with entity id 1 at (0, 64, 0)"
                ),
                (
                    f"[{now:%d%b%Y %H:%M:%S}.000] [Server thread/INFO] "
                    "[net.minecraft.server.players.PlayerList/]: "
                    "Bob[/198.51.100.2:25565] logged in with entity id 2 at (0, 64, 0)"
                ),
            ]
        ),
        encoding="utf-8",
    )
    settings = _settings_for_root(settings, root)

    result = SystemdForgeAdapter(settings).get_player_activity(2)

    assert result["coverage_complete"] is True
    assert result["sessions"] == 2
    assert result["unique_players"] == 2
    assert {item["name"] for item in result["recent_players"]} == {"Alice", "Bob"}
    assert "203.0.113.9" not in json.dumps(result)


def test_player_activity_prioritizes_newest_rotated_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "latest.log").write_text("current\n", encoding="utf-8")
    (logs / "latest.log.1").write_text("previous\n", encoding="utf-8")
    for age in range(2, 21):
        with gzip.open(logs / f"latest.log.{age}.gz", "wt", encoding="utf-8") as handle:
            handle.write(f"rotated {age}\n")

    selected = SystemdForgeAdapter._activity_paths(logs)

    assert [path.name for path in selected[:3]] == [
        "latest.log",
        "latest.log.1",
        "latest.log.2.gz",
    ]
    assert len(selected) == 14
    assert "latest.log.20.gz" not in {path.name for path in selected}


def test_server_properties_are_returned_with_safe_value_types(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "server.properties").write_text(
        "pvp=false\nwhite-list=true\nmax-players=24\nview-distance=8\nmotd=Friends\n",
        encoding="utf-8",
    )
    settings = _settings_for_root(settings, root)

    result = SystemdForgeAdapter(settings).get_properties()

    assert result["pvp"] is False
    assert result["white-list"] is True
    assert result["max-players"] == 24
    assert result["view-distance"] == 8
    assert result["motd"] == "Friends"


def test_backup_sidecar_presence_is_not_reported_as_hash_verified(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "minecraft-20260714-cold.tar.gz"
    archive.write_bytes(b"fixture")
    Path(str(archive) + ".sha256").write_text("0" * 64 + "  fixture\n", encoding="ascii")
    settings = replace(settings, backup_root=tmp_path)

    result = SystemdForgeAdapter(settings).list_backups()

    assert result[0]["verified"] is False
    assert result[0]["verification_status"] == "checksum_present"


def test_corrupt_player_json_scalar_is_treated_as_empty(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "whitelist.json").write_text("42\n", encoding="utf-8")
    settings = _settings_for_root(settings, root)

    assert SystemdForgeAdapter(settings).list_whitelist() == []


def test_backup_schedule_reads_the_active_dropins(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer = tmp_path / "timer.conf"
    service = tmp_path / "service.conf"
    timer_unit = tmp_path / "timer-unit.conf"
    service_unit = tmp_path / "service-unit.conf"
    backup_command = tmp_path / "mc-backup"
    timer_unit.write_text("[Timer]\nOnCalendar=*-*-* 03:30:00\n", encoding="utf-8")
    service_unit.write_text("[Service]\n", encoding="utf-8")
    backup_command.write_text('KEEP="${KEEP_BACKUPS:-7}"\n', encoding="utf-8")
    timer.write_text(
        "[Timer]\nOnCalendar=\nOnCalendar=*-*-* 04:15:00\nPersistent=true\n",
        encoding="utf-8",
    )
    service.write_text("[Service]\nEnvironment=KEEP_BACKUPS=9\n", encoding="utf-8")
    settings = replace(
        settings,
        backup_timer_dropin_path=timer,
        backup_service_dropin_path=service,
        backup_timer_unit_path=timer_unit,
        backup_service_unit_path=service_unit,
        backup_command=backup_command,
    )

    assert SystemdForgeAdapter(settings).get_backup_schedule() == {
        "hour": 4,
        "minute": 15,
        "keep": 9,
        "kind": "cold",
    }


def test_backup_schedule_reads_base_unit_and_script_default(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer = tmp_path / "timer.conf"
    service = tmp_path / "service.conf"
    command = tmp_path / "mc-backup"
    timer.write_text("[Timer]\nOnCalendar=*-*-* 02:45:00\n", encoding="utf-8")
    service.write_text("[Service]\n", encoding="utf-8")
    command.write_text('KEEP="${KEEP_BACKUPS:-11}"\n', encoding="utf-8")
    settings = replace(
        settings,
        backup_timer_unit_path=timer,
        backup_timer_dropin_path=tmp_path / "missing-timer",
        backup_service_unit_path=service,
        backup_service_dropin_path=tmp_path / "missing-service",
        backup_command=command,
    )

    assert SystemdForgeAdapter(settings).get_backup_schedule() == {
        "hour": 2,
        "minute": 45,
        "keep": 11,
        "kind": "cold",
    }


def test_backup_status_reads_last_result_and_next_run(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        del timeout
        if settings.backup_service in argv:
            output = "\n".join(
                [
                    "ActiveState=inactive",
                    "SubState=dead",
                    "Result=success",
                    "ExecMainStatus=0",
                    "ExecMainStartTimestamp=Fri 2026-07-17 03:30:00 CST",
                    "ExecMainExitTimestamp=Fri 2026-07-17 03:31:44 CST",
                ]
            )
        else:
            output = "\n".join(
                [
                    "ActiveState=active",
                    "UnitFileState=enabled",
                    "LastTriggerUSec=Fri 2026-07-17 03:30:00 CST",
                    "NextElapseUSecRealtime=Sat 2026-07-18 03:30:00 CST",
                ]
            )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(systemd_module, "_run", fake_run)

    result = SystemdForgeAdapter(settings).get_backup_status()

    assert result == {
        "timer_active": True,
        "timer_enabled": True,
        "last_result": "success",
        "last_run_at": "2026-07-17T03:30:00+08:00",
        "last_finished_at": "2026-07-17T03:31:44+08:00",
        "last_exit_code": 0,
        "next_run_at": "2026-07-18T03:30:00+08:00",
    }


def test_backup_status_reports_failed_service(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        del timeout
        output = (
            "ActiveState=inactive\nResult=exit-code\nExecMainStatus=1\n"
            "ExecMainStartTimestamp=Fri 2026-07-17 03:30:00 CST\n"
            if settings.backup_service in argv
            else "ActiveState=active\nUnitFileState=enabled\n"
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(systemd_module, "_run", fake_run)

    result = SystemdForgeAdapter(settings).get_backup_status()

    assert result["last_result"] == "failed"
    assert result["last_exit_code"] == 1


def test_helper_structured_error_details_are_retained(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = "a" * 64
    writable = replace(
        settings,
        production_writes_enabled=True,
        approved_fingerprint=approved,
        helper_path=Path("/usr/bin/true"),
    )
    adapter = SystemdForgeAdapter(writable)
    monkeypatch.setattr(adapter, "fingerprint", lambda: approved)
    failure = {
        "ok": False,
        "error": {
            "code": "rollback_failed",
            "message": "rollback was not verified",
            "details": {"recovery_point": "/srv/minecraft/panel-recovery/restore-test"},
        },
    }
    monkeypatch.setattr(
        systemd_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", json.dumps(failure)),
    )

    with pytest.raises(AdapterOperationError) as error:
        adapter.execute("restart_server", {})

    assert error.value.code == "rollback_failed"
    assert error.value.details["recovery_point"].endswith("restore-test")
