from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


def _as_datetime(value: Any) -> datetime | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _ratio(used: Any, total: Any) -> float | None:
    if isinstance(used, bool) or isinstance(total, bool):
        return None
    try:
        used_value = max(0.0, float(used))
        total_value = float(total)
    except (TypeError, ValueError):
        return None
    return used_value / total_value if total_value > 0 else None


def build_diagnostics(
    *,
    status: dict[str, Any],
    metrics: dict[str, Any],
    players: dict[str, Any],
    backups: list[dict[str, Any]],
    schedule: dict[str, Any],
    crashes: list[dict[str, Any]],
    backup_status: dict[str, Any] | None = None,
    local_timezone: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    checks: list[dict[str, Any]] = []

    running = bool(status.get("healthy"))
    checks.append(
        {
            "id": "server",
            "tone": "good" if running else "critical",
            "title": "Minecraft 正在运行" if running else "Minecraft 当前未正常运行",
            "detail": (
                f"{status.get('loader', '未知加载器')} "
                f"{status.get('minecraft_version', '未知版本')}"
                if running
                else "启动服务器前建议先查看最近日志。"
            ),
            "target": "server",
        }
    )

    disk_ratio = _ratio(metrics.get("disk_used_bytes"), metrics.get("disk_total_bytes"))
    if disk_ratio is None:
        disk_tone, disk_title, disk_detail = (
            "neutral",
            "磁盘状态暂时无法确认",
            "面板没有用推测值代替真实数据。",
        )
    else:
        free_percent = max(0, round((1 - disk_ratio) * 100))
        disk_tone = (
            "critical" if disk_ratio >= 0.92 else "warning" if disk_ratio >= 0.85 else "good"
        )
        disk_title = "磁盘空间充足" if disk_tone == "good" else "磁盘空间需要关注"
        disk_detail = f"约剩余 {free_percent}% 空间。"
    checks.append(
        {
            "id": "disk",
            "tone": disk_tone,
            "title": disk_title,
            "detail": disk_detail,
            "target": "server",
        }
    )

    latest_backup = backups[0] if backups else None
    backup_age_hours: float | None = None
    if latest_backup is None:
        backup_tone = "critical"
        backup_title = "还没有可用备份"
        backup_detail = "在修改模组或配置前，请先创建备份。"
    else:
        created = _as_datetime(latest_backup.get("created_at"))
        if created is not None:
            backup_age_hours = max(0.0, (current - created).total_seconds() / 3600)
        verification = str(latest_backup.get("verification_status") or "missing")
        too_old = backup_age_hours is None or backup_age_hours > 48
        unusable_checksum = verification in {"missing", "invalid"}
        backup_tone = "warning" if too_old or unusable_checksum else "good"
        if backup_age_hours is None:
            backup_title = "备份时间无法确认"
        elif backup_age_hours < 1:
            backup_title = "最近 1 小时内已备份"
        elif backup_age_hours <= 48:
            backup_title = f"最近备份在 {round(backup_age_hours)} 小时前"
        else:
            backup_title = "备份已超过 48 小时"
        if verification == "verified":
            backup_detail = "备份已完成内容与结构校验；恢复时仍会再次确认。"
        elif verification == "checksum_present":
            backup_detail = "校验文件已就绪，恢复时仍会再做完整校验。"
        elif verification == "invalid":
            backup_detail = "校验文件格式无效，不建议使用该备份恢复。"
        else:
            backup_detail = "没有找到校验文件，不建议使用该备份恢复。"
    runtime = backup_status or {}
    last_result = runtime.get("last_result")
    if last_result == "failed":
        backup_tone = "critical"
        backup_title = "最近一次自动备份失败"
        exit_code = runtime.get("last_exit_code")
        backup_detail = (
            f"备份任务退出码为 {exit_code}，请先查看最近日志。"
            if isinstance(exit_code, int)
            else "备份任务未成功完成，请先查看最近日志。"
        )
    elif runtime.get("timer_active") is False or runtime.get("timer_enabled") is False:
        backup_tone = "warning"
        backup_title = "自动备份计划未运行"
        backup_detail = "现有备份仍保留，但下一次不会自动执行。"
    elif last_result == "never":
        backup_tone = "warning"
        backup_title = "自动备份尚未执行"
        backup_detail = "备份计划已配置，但还没有可确认的执行结果。"
    elif last_result == "running":
        backup_tone = "good"
        backup_title = "正在创建一致性备份"
        backup_detail = "冷备份完成后 Minecraft 会自动恢复运行。"
    elif last_result == "success":
        backup_detail = f"{backup_detail} 上次自动备份执行成功。"
    checks.append(
        {
            "id": "backup",
            "tone": backup_tone,
            "title": backup_title,
            "detail": backup_detail,
            "target": "backups",
        }
    )

    player_data_stale = bool(players.get("stale"))
    checks.append(
        {
            "id": "players",
            "tone": "neutral" if player_data_stale else "good",
            "title": "在线玩家数据待确认" if player_data_stale else "玩家状态可正常读取",
            "detail": (
                "日志覆盖不完整，面板不会把未知误报为零人。"
                if player_data_stale
                else f"当前 {players.get('online', 0)} 人在线。"
            ),
            "target": "players",
        }
    )

    signals: list[dict[str, Any]] = []
    identity_values = {
        str(status.get("minecraft_version", "")).lower(),
        str(status.get("loader", "")).lower(),
        str(status.get("java_version", "")).lower(),
    }
    if identity_values.intersection({"", "unknown", "未知"}):
        signals.append(
            {
                "id": "identity_unknown",
                "tone": "warning",
                "title": "服务器版本信息尚未完全识别",
                "target": "server",
            }
        )
    if crashes:
        created = _as_datetime(crashes[0].get("created_at"))
        if created is not None and (current - created).total_seconds() <= 24 * 3600:
            signals.append(
                {
                    "id": "recent_crash",
                    "tone": "warning",
                    "title": "24 小时内发现崩溃报告",
                    "target": "server",
                }
            )
    if status.get("structure_approved") is False or status.get("writes_enabled") is False:
        signals.append(
            {
                "id": "write_guard",
                "tone": "warning",
                "title": "管理写入已被安全锁定",
                "target": "audit",
            }
        )

    tones = [str(item["tone"]) for item in [*checks, *signals]]
    level = "critical" if "critical" in tones else "attention" if "warning" in tones else "good"
    headline = {
        "critical": "有问题需要尽快处理",
        "attention": "服务器可用，有一些事值得看看",
        "good": "今天不需要额外处理",
    }[level]

    return {
        "level": level,
        "headline": headline,
        "updated_at": current.isoformat(),
        "checks": checks,
        "signals": signals,
        "backup": {
            "latest": latest_backup,
            "age_hours": round(backup_age_hours, 1) if backup_age_hours is not None else None,
            "schedule": schedule,
            "count": len(backups),
            "status": backup_status,
        },
        "local_time": current.astimezone(ZoneInfo(local_timezone)).isoformat(),
    }
