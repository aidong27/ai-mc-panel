from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

HELPER_PATH = Path(__file__).parents[3] / "helper" / "mc_panel_action.py"
SPEC = importlib.util.spec_from_file_location("mc_panel_action", HELPER_PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)


def _write_helper_config(tmp_path: Path) -> Path:
    config = tmp_path / "helper.json"
    data = {
        "server_root": str(tmp_path / "server"),
        "backup_root": str(tmp_path / "backups"),
        "quarantine_root": str(tmp_path / "quarantine"),
        "recovery_root": str(tmp_path / "recovery"),
        "lock_file": str(tmp_path / "action.lock"),
        "backup_command": str(tmp_path / "minecraft-backup"),
        "timer_dropin": str(tmp_path / "timer.d/mc-panel.conf"),
        "backup_service_dropin": str(tmp_path / "service.d/mc-panel.conf"),
        "server_service": "friends.service",
        "backup_timer": "friends-backup.timer",
        "server_port": 25570,
        "console_user": "minecraft",
        "console_screen_name": "friends",
        "backup_archive_glob": "friends-*.tar.gz",
    }
    config.write_text(json.dumps(data), encoding="utf-8")
    config.chmod(0o600)
    return config


def test_helper_loads_only_a_validated_root_owned_runtime_profile(tmp_path: Path) -> None:
    config = _write_helper_config(tmp_path)

    paths = helper.Paths.from_config(config, expected_uid=os.getuid())

    assert paths.server_service == "friends.service"
    assert paths.server_port == 25570
    assert paths.backup_archive_glob == "friends-*.tar.gz"


def test_helper_rejects_group_writable_runtime_profile(tmp_path: Path) -> None:
    config = _write_helper_config(tmp_path)
    config.chmod(0o620)

    with pytest.raises(helper.HelperError) as error:
        helper.Paths.from_config(config, expected_uid=os.getuid())

    assert error.value.code == "unsafe_config"


def test_helper_rejects_world_readable_runtime_profile(tmp_path: Path) -> None:
    config = _write_helper_config(tmp_path)
    config.chmod(0o604)

    with pytest.raises(helper.HelperError) as error:
        helper.Paths.from_config(config, expected_uid=os.getuid())

    assert error.value.code == "unsafe_config"


def test_rejects_duplicate_json_keys() -> None:
    with pytest.raises(helper.HelperError) as error:
        helper.parse_request(b'{"operation":"start_server","operation":"stop_server","params":{}}')
    assert error.value.code == "invalid_json"


def test_rejects_unknown_operation(tmp_path: Path) -> None:
    paths = helper.Paths(lock_file=tmp_path / "action.lock")
    with pytest.raises(helper.HelperError) as error:
        helper.ActionHelper(paths).execute("run_shell", {})
    assert error.value.code == "operation_denied"


def test_rejects_path_traversal_archive_member() -> None:
    member = tarfile.TarInfo("../../etc/shadow")
    with pytest.raises(helper.HelperError) as error:
        helper.ActionHelper._validate_members([member])
    assert error.value.code == "unsafe_archive"


def test_property_range_validation() -> None:
    assert helper.ActionHelper._validate_property("view-distance", 8) == "8"
    with pytest.raises(helper.HelperError):
        helper.ActionHelper._validate_property("view-distance", 200)
    with pytest.raises(helper.HelperError):
        helper.ActionHelper._validate_property("online-mode", True)


def test_console_accepts_printable_unicode_but_rejects_ascii_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], int]] = []
    monkeypatch.setattr(helper, "_run", lambda argv, timeout: calls.append((argv, timeout)))

    helper._console("say [验收] 面板控制台测试")
    assert calls[0][0][-1] == "say [验收] 面板控制台测试\r"
    assert calls[0][1] == 10

    with pytest.raises(helper.HelperError):
        helper._console("say ok; reboot")

    helper._console("say " + ("好" * 200), allow_unicode=True)


def test_helper_does_not_claim_every_action_was_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(helper, "_console", lambda command, allow_unicode=False, paths=None: None)
    paths = helper.Paths(lock_file=tmp_path / "action.lock")

    result = helper.ActionHelper(paths).execute("send_announcement", {"message": "hello"})

    assert result == {
        "ok": True,
        "operation": "send_announcement",
        "result": {"sent": True},
    }


def test_successful_stop_clears_systemd_failed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[str] = []
    monkeypatch.setattr(helper, "_systemctl", lambda action, unit: actions.append(action))
    monkeypatch.setattr(helper, "_is_active", lambda unit="minecraft.service": False)

    result = helper.ActionHelper().stop_server({})

    assert result == {"active": False}
    assert actions == ["stop", "reset-failed"]


def test_created_backup_is_verified_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    (backup_root / "minecraft-20260713-cold.tar.gz").write_bytes(b"existing")
    archive = backup_root / "minecraft-20260714-cold.tar.gz"
    paths = helper.Paths(
        backup_root=backup_root,
        backup_command=tmp_path / "mc-backup",
        lock_file=tmp_path / "action.lock",
    )
    verified: list[Path] = []
    retention: list[dict[str, str] | None] = []

    def fake_run(
        argv: list[str],
        timeout: int,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        retention.append(env_overrides)
        archive.write_bytes(b"verified fixture")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_is_active", lambda unit="minecraft.service": True)
    monkeypatch.setattr(helper, "_wait_for_port", lambda port, timeout: True)
    monkeypatch.setattr(
        helper.ActionHelper,
        "_verify_archive",
        lambda self, path: verified.append(path),
    )

    result = helper.ActionHelper(paths).create_backup({})
    assert verified == [archive]
    assert retention == [{"KEEP_BACKUPS": "2"}]
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_backup_retention_override_rejects_unbounded_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("ran command"))

    with pytest.raises(helper.HelperError, match="retention override"):
        helper._run(["/bin/true"], 1, env_overrides={"PATH": "/not-allowed"})


def test_backup_schedule_recovers_both_dropins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer = tmp_path / "timer" / "mc-panel.conf"
    service = tmp_path / "service" / "mc-panel.conf"
    timer.parent.mkdir()
    service.parent.mkdir()
    timer.write_text("old timer\n", encoding="ascii")
    service.write_text("old service\n", encoding="ascii")
    recovery = tmp_path / "recovery"
    paths = helper.Paths(
        timer_dropin=timer,
        backup_service_dropin=service,
        recovery_root=recovery,
        lock_file=tmp_path / "action.lock",
    )

    monkeypatch.setattr(helper, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helper,
        "_run",
        lambda argv, timeout: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(
        helper.ActionHelper,
        "_atomic_root_file",
        staticmethod(lambda path, content: path.write_text(content, encoding="ascii")),
    )

    result = helper.ActionHelper(paths).set_backup_schedule({"hour": 4, "minute": 15, "keep": 9})
    recovery_path = Path(result["recovery_point"])
    assert (recovery_path / "timer-mc-panel.conf").read_text(encoding="ascii") == "old timer\n"
    assert (recovery_path / "service-mc-panel.conf").read_text(encoding="ascii") == "old service\n"


def test_backup_schedule_rolls_back_partial_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer = tmp_path / "timer" / "mc-panel.conf"
    service = tmp_path / "service" / "mc-panel.conf"
    timer.parent.mkdir()
    service.parent.mkdir()
    timer.write_text("old timer\n", encoding="utf-8")
    service.write_text("old service\n", encoding="utf-8")
    paths = helper.Paths(
        timer_dropin=timer,
        backup_service_dropin=service,
        recovery_root=tmp_path / "recovery",
        lock_file=tmp_path / "action.lock",
    )
    failed_once = False

    def atomic_write(path: Path, content: str) -> None:
        nonlocal failed_once
        if path == service and "KEEP_BACKUPS" in content and not failed_once:
            failed_once = True
            raise helper.HelperError("command_failed", "injected write failure")
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(helper.ActionHelper, "_atomic_root_file", staticmethod(atomic_write))
    monkeypatch.setattr(helper, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helper,
        "_run",
        lambda argv, timeout: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    with pytest.raises(helper.HelperError, match="injected write failure"):
        helper.ActionHelper(paths).set_backup_schedule({"hour": 5, "minute": 20, "keep": 8})

    assert timer.read_text(encoding="utf-8") == "old timer\n"
    assert service.read_text(encoding="utf-8") == "old service\n"


def test_privileged_jar_validation_rejects_expansion_before_testzip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jar = tmp_path / "mod.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("META-INF/mods.toml", "fixture")
    oversized = zipfile.ZipInfo("META-INF/mods.toml")
    oversized.file_size = 513 * 1024 * 1024
    testzip_called = False

    def forbidden_testzip(_archive: zipfile.ZipFile) -> str | None:
        nonlocal testzip_called
        testzip_called = True
        return None

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lambda _archive: [oversized])
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden_testzip)

    with pytest.raises(helper.HelperError) as error:
        helper.ActionHelper._validate_jar(jar)

    assert error.value.code == "validation_failed"
    assert testzip_called is False


def test_backup_attempts_to_restart_service_before_reporting_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    archive = backup_root / "minecraft-20260715-cold.tar.gz"
    paths = helper.Paths(
        backup_root=backup_root,
        backup_command=tmp_path / "mc-backup",
        lock_file=tmp_path / "action.lock",
    )
    actions: list[str] = []
    active = iter([False, True])

    def fake_run(
        argv: list[str],
        timeout: int,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        archive.write_bytes(b"verified fixture")
        Path(str(archive) + ".sha256").write_text("fixture\n", encoding="ascii")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_is_active", lambda unit="minecraft.service": next(active))
    monkeypatch.setattr(helper, "_wait_for_port", lambda port, timeout: True)
    monkeypatch.setattr(helper, "_systemctl", lambda action, unit: actions.append(action))
    monkeypatch.setattr(helper.ActionHelper, "_verify_archive", lambda self, path: None)

    result = helper.ActionHelper(paths).create_backup({})

    assert actions == ["start"]
    assert result["service_active"] is True


def test_backup_verification_failure_still_recovers_minecraft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    paths = helper.Paths(
        backup_root=backup_root,
        backup_command=tmp_path / "mc-backup",
        lock_file=tmp_path / "action.lock",
    )
    actions: list[str] = []
    active = iter([False, True])
    monkeypatch.setattr(
        helper,
        "_run",
        lambda argv, timeout, *, env_overrides=None: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(helper, "_is_active", lambda unit="minecraft.service": next(active))
    monkeypatch.setattr(helper, "_wait_for_port", lambda port, timeout: True)
    monkeypatch.setattr(helper, "_systemctl", lambda action, unit: actions.append(action))

    with pytest.raises(helper.HelperError) as error:
        helper.ActionHelper(paths).create_backup({})

    assert error.value.code == "verification_failed"
    assert actions == ["start"]


def test_service_state_timeout_is_a_structured_helper_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(["systemctl"], 10)),
    )

    with pytest.raises(helper.HelperError) as error:
        helper._is_active()

    assert error.value.code == "verification_failed"


def test_restore_rollback_failure_reports_recovery_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_root = tmp_path / "server"
    recovery = tmp_path / "recovery"
    failed_new = recovery / "failed-new-data"
    (server_root / "world").mkdir(parents=True)
    (server_root / "world" / "level.dat").write_text("new", encoding="ascii")
    (recovery / "world").mkdir(parents=True)
    (recovery / "world" / "level.dat").write_text("old", encoding="ascii")
    paths = helper.Paths(
        server_root=server_root,
        recovery_root=tmp_path / "recovery-root",
        lock_file=tmp_path / "action.lock",
    )

    def systemctl(action: str, unit: str) -> None:
        assert unit == "minecraft.service"
        if action == "start":
            raise helper.HelperError("command_failed", "injected start failure")

    monkeypatch.setattr(helper, "_systemctl", systemctl)
    monkeypatch.setattr(helper, "_is_active", lambda unit="minecraft.service": False)
    monkeypatch.setattr(helper, "_wait_for_port", lambda port, timeout: False)

    with pytest.raises(helper.HelperError) as error:
        helper.ActionHelper(paths)._rollback_restore(
            recovery,
            failed_new,
            moved_old=["world"],
            installed_new=["world"],
        )

    assert error.value.code == "rollback_failed"
    assert error.value.details["recovery_point"] == str(recovery)
    assert error.value.details["failed_new_data"] == str(failed_new)
    assert (server_root / "world" / "level.dat").read_text(encoding="ascii") == "old"
    assert (failed_new / "world" / "level.dat").read_text(encoding="ascii") == "new"
