from datetime import UTC, datetime, timedelta

from mc_panel_api.diagnostics import build_diagnostics


def _snapshot(now: datetime) -> dict[str, object]:
    return build_diagnostics(
        status={
            "healthy": True,
            "loader": "Forge",
            "minecraft_version": "1.20.1",
            "java_version": "17",
            "structure_approved": True,
            "writes_enabled": True,
        },
        metrics={
            "disk_used_bytes": 20,
            "disk_total_bytes": 100,
            "memory_used_bytes": 4,
            "memory_total_bytes": 16,
        },
        players={"online": 1, "stale": False},
        backups=[
            {
                "id": "backup_fixture",
                "created_at": (now - timedelta(hours=6)).isoformat(),
                "verification_status": "checksum_present",
            }
        ],
        schedule={"hour": 3, "minute": 30, "keep": 7, "kind": "cold"},
        crashes=[],
        now=now,
    )


def test_healthy_diagnostics_explain_backup_without_overclaiming_verification() -> None:
    result = _snapshot(datetime(2026, 7, 16, 8, tzinfo=UTC))

    assert result["level"] == "good"
    assert result["headline"] == "今天不需要额外处理"
    backup = next(item for item in result["checks"] if item["id"] == "backup")
    assert backup["tone"] == "good"
    assert "恢复时仍会再做完整校验" in backup["detail"]


def test_diagnostics_raise_attention_for_low_disk_and_old_backup() -> None:
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    result = build_diagnostics(
        status={
            "healthy": True,
            "loader": "Forge",
            "minecraft_version": "1.20.1",
            "java_version": "17",
        },
        metrics={"disk_used_bytes": 94, "disk_total_bytes": 100},
        players={"online": 0, "stale": True},
        backups=[
            {
                "created_at": (now - timedelta(days=3)).isoformat(),
                "verification_status": "missing",
            }
        ],
        schedule={"hour": 3, "minute": 30, "keep": 7},
        crashes=[],
        now=now,
    )

    assert result["level"] == "critical"
    tones = {item["id"]: item["tone"] for item in result["checks"]}
    assert tones["disk"] == "critical"
    assert tones["backup"] == "warning"
    assert tones["players"] == "neutral"


def test_failed_automatic_backup_is_a_critical_dashboard_check() -> None:
    now = datetime(2026, 7, 17, 8, tzinfo=UTC)
    result = build_diagnostics(
        status={
            "healthy": True,
            "loader": "Forge",
            "minecraft_version": "1.20.1",
            "java_version": "17",
        },
        metrics={"disk_used_bytes": 20, "disk_total_bytes": 100},
        players={"online": 0, "stale": False},
        backups=[
            {
                "created_at": (now - timedelta(hours=24)).isoformat(),
                "verification_status": "checksum_present",
            }
        ],
        schedule={"hour": 3, "minute": 30, "keep": 7},
        crashes=[],
        backup_status={
            "timer_active": True,
            "last_result": "failed",
            "last_exit_code": 1,
        },
        now=now,
    )

    backup = next(item for item in result["checks"] if item["id"] == "backup")
    assert result["level"] == "critical"
    assert backup["tone"] == "critical"
    assert "自动备份失败" in backup["title"]
    assert "退出码为 1" in backup["detail"]


def test_unknown_runtime_identity_is_visible_without_guessing() -> None:
    now = datetime(2026, 7, 17, 8, tzinfo=UTC)
    result = build_diagnostics(
        status={
            "healthy": True,
            "loader": "Unknown",
            "minecraft_version": "unknown",
            "java_version": "unknown",
        },
        metrics={"disk_used_bytes": 20, "disk_total_bytes": 100},
        players={"online": 0, "stale": False},
        backups=[
            {
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "verification_status": "verified",
            }
        ],
        schedule={"hour": 3, "minute": 30, "keep": 7},
        crashes=[],
        now=now,
    )

    signal = next(item for item in result["signals"] if item["id"] == "identity_unknown")
    assert result["level"] == "attention"
    assert signal["tone"] == "warning"
    assert "尚未完全识别" in signal["title"]
