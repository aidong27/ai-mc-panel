from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from .adapters.base import MinecraftAdapter
from .models import (
    AnnouncementParams,
    BackupScheduleParams,
    ConsoleCommandParams,
    EditPropertyParams,
    EmptyParams,
    InstallModParams,
    ModObjectParams,
    OperationReview,
    PlayerParams,
    RestoreBackupParams,
    ReviewItem,
    RiskLevel,
)


class ToolExposure(StrEnum):
    READ_ONLY = "read_only"
    AI_PROPOSABLE = "ai_proposable"
    MANUAL_ONLY = "manual_only"


class ToolUnavailableError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    risk: RiskLevel
    params_model: type[BaseModel]
    exposure: ToolExposure
    title: str
    reason: str
    impact: str
    stops_server: bool
    creates_recovery_point: bool

    def __post_init__(self) -> None:
        if not isinstance(self.exposure, ToolExposure):
            raise ValueError("Tool exposure must be a declared ToolExposure value")


SPECS: dict[str, ToolSpec] = {
    "start_server": ToolSpec(
        "start_server",
        RiskLevel.MEDIUM,
        EmptyParams,
        ToolExposure.AI_PROPOSABLE,
        "启动服务器",
        "让已停止的 Minecraft 服务重新运行。",
        "会启动 Java 和模组加载流程。",
        False,
        False,
    ),
    "stop_server": ToolSpec(
        "stop_server",
        RiskLevel.MEDIUM,
        EmptyParams,
        ToolExposure.AI_PROPOSABLE,
        "停止服务器",
        "安全保存世界后停止 Minecraft。",
        "在线玩家会断开，直到再次启动。",
        True,
        False,
    ),
    "restart_server": ToolSpec(
        "restart_server",
        RiskLevel.MEDIUM,
        EmptyParams,
        ToolExposure.AI_PROPOSABLE,
        "重启服务器",
        "解决需要重新加载服务的配置或临时问题。",
        "在线玩家会短暂断开，通常需要数分钟恢复。",
        True,
        False,
    ),
    "send_announcement": ToolSpec(
        "send_announcement",
        RiskLevel.LOW,
        AnnouncementParams,
        ToolExposure.AI_PROPOSABLE,
        "发送游戏内通知",
        "提醒当前在线玩家。",
        "只发送一条纯文本消息，不修改世界。",
        False,
        False,
    ),
    "send_console_command": ToolSpec(
        "send_console_command",
        RiskLevel.MEDIUM,
        ConsoleCommandParams,
        ToolExposure.MANUAL_ONLY,
        "发送控制台命令",
        "由服务器管理员手动执行一条 Minecraft 命令。",
        "命令只进入 Minecraft 控制台，不进入系统 Shell；执行结果会记录。",
        False,
        False,
    ),
    "create_backup": ToolSpec(
        "create_backup",
        RiskLevel.LOW,
        EmptyParams,
        ToolExposure.AI_PROPOSABLE,
        "创建备份",
        "给世界和关键配置创建可校验的恢复点。",
        "当前冷备流程会广播后短暂停服约 1 到 2 分钟。",
        True,
        False,
    ),
    "verify_backup": ToolSpec(
        "verify_backup",
        RiskLevel.LOW,
        RestoreBackupParams,
        ToolExposure.AI_PROPOSABLE,
        "完整校验备份",
        "确认备份文件完整、结构安全，而且仍与校验文件一致。",
        "只读取备份并写入小型校验凭据，不停服、不修改世界。",
        False,
        False,
    ),
    "edit_server_property": ToolSpec(
        "edit_server_property",
        RiskLevel.MEDIUM,
        EditPropertyParams,
        ToolExposure.AI_PROPOSABLE,
        "修改服务器设置",
        "调整允许的日常游戏设置。",
        "会备份原配置；部分设置需要重启后生效。",
        False,
        True,
    ),
    "add_whitelist_player": ToolSpec(
        "add_whitelist_player",
        RiskLevel.MEDIUM,
        PlayerParams,
        ToolExposure.AI_PROPOSABLE,
        "加入白名单",
        "允许指定玩家进入启用白名单的服务器。",
        "离线模式下用户名不能作为强身份凭据。",
        False,
        False,
    ),
    "remove_whitelist_player": ToolSpec(
        "remove_whitelist_player",
        RiskLevel.MEDIUM,
        PlayerParams,
        ToolExposure.AI_PROPOSABLE,
        "移出白名单",
        "取消指定玩家的白名单资格。",
        "该玩家之后可能无法进入服务器。",
        False,
        False,
    ),
    "add_operator": ToolSpec(
        "add_operator",
        RiskLevel.MEDIUM,
        PlayerParams,
        ToolExposure.AI_PROPOSABLE,
        "授予管理员权限",
        "允许指定玩家使用高权限游戏命令。",
        "离线模式下存在用户名冒用风险。",
        False,
        False,
    ),
    "remove_operator": ToolSpec(
        "remove_operator",
        RiskLevel.MEDIUM,
        PlayerParams,
        ToolExposure.AI_PROPOSABLE,
        "移除管理员权限",
        "收回指定玩家的高权限游戏命令。",
        "不会删除玩家数据。",
        False,
        False,
    ),
    "install_mod": ToolSpec(
        "install_mod",
        RiskLevel.MEDIUM,
        InstallModParams,
        ToolExposure.AI_PROPOSABLE,
        "安装已检查的模组",
        "把隔离区中通过检查的 JAR 加入模组目录。",
        "会创建恢复点并需要重启；不自动下载或更新其他模组。",
        False,
        True,
    ),
    "disable_mod": ToolSpec(
        "disable_mod",
        RiskLevel.MEDIUM,
        ModObjectParams,
        ToolExposure.AI_PROPOSABLE,
        "停用模组",
        "把模组移动到保留目录而不是删除。",
        "可能影响存档内容，必须先备份并重启。",
        False,
        True,
    ),
    "set_backup_schedule": ToolSpec(
        "set_backup_schedule",
        RiskLevel.MEDIUM,
        BackupScheduleParams,
        ToolExposure.AI_PROPOSABLE,
        "修改自动备份时间",
        "调整每日冷备份的时间和保留数量。",
        "备份时间会出现短暂停服。",
        False,
        True,
    ),
    "restore_backup": ToolSpec(
        "restore_backup",
        RiskLevel.HIGH,
        RestoreBackupParams,
        ToolExposure.AI_PROPOSABLE,
        "恢复旧备份",
        "把世界和关键配置恢复到选定时间。",
        "会停服并替换当前数据；执行前再次备份，失败时自动回滚。",
        True,
        True,
    ),
}


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def operation_params_hash(action: str, params: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"action": action, "params": params}).encode()).hexdigest()


def _review_text(value: Any) -> str:
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    if value is None:
        return "未知"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    normalized = " ".join(text.split())
    return normalized[:500] or "空值"


def _format_size(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return "未知"
    return f"{value / 1024**3:.2f} GB"


def _verification_label(value: Any) -> str:
    return {
        "verified": "已完整校验",
        "checksum_present": "校验文件就绪",
        "invalid": "校验文件无效",
        "missing": "缺少校验文件",
    }.get(str(value), "状态未知")


class ToolRegistry:
    def __init__(
        self,
        adapter: MinecraftAdapter,
        *,
        manual_console_enabled: bool = False,
    ) -> None:
        self.adapter = adapter
        self.manual_console_enabled = manual_console_enabled

    def validate(self, action: str, params: dict[str, Any]) -> tuple[ToolSpec, dict[str, Any]]:
        spec = SPECS.get(action)
        if spec is None:
            raise ValueError("未知或未授权的操作")
        if spec.exposure is ToolExposure.MANUAL_ONLY and not self.manual_console_enabled:
            raise ToolUnavailableError("通用控制台命令已由服务器 root 配置关闭")
        try:
            validated = spec.params_model.model_validate(params)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise ValueError(json.dumps(errors, ensure_ascii=False)) from exc
        return spec, validated.model_dump()

    def preview(
        self,
        action: str,
        params: dict[str, Any],
        *,
        operation_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        spec, normalized = self.validate(action, params)
        return self.build_preview(
            spec,
            normalized,
            operation_id=operation_id,
            expires_at=expires_at,
        )

    def build_preview(
        self,
        spec: ToolSpec,
        normalized: dict[str, Any],
        *,
        operation_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        action = spec.name
        rollback = "使用自动恢复点回滚" if spec.creates_recovery_point else "无需数据回滚"
        review = OperationReview(
            operation_id=operation_id or "review_" + uuid.uuid4().hex,
            human_title=spec.title,
            risk=spec.risk,
            review_items=self._review_items(action, normalized),
            impact=spec.impact,
            stops_server=spec.stops_server,
            recovery_plan=rollback,
            assurance_expected=self._assurance_expected(action),
            params_hash=operation_params_hash(action, normalized),
            expires_at=expires_at,
        )
        return {
            "action": action,
            "params": normalized,
            "title": spec.title,
            "reason": spec.reason,
            "impact": spec.impact,
            "risk": spec.risk.value,
            "requires_confirmation": spec.risk is not RiskLevel.LOW,
            "requires_second_confirmation": spec.risk is RiskLevel.HIGH,
            "stops_server": spec.stops_server,
            "creates_recovery_point": spec.creates_recovery_point,
            "rollback": rollback,
            "review": review.model_dump(mode="json"),
        }

    def _review_items(self, action: str, params: dict[str, Any]) -> list[ReviewItem]:
        if action in {"start_server", "stop_server", "restart_server"}:
            service = _review_text(self.adapter.get_status().get("service"))
            return [ReviewItem(label="目标服务", value=service)]
        if action == "send_announcement":
            return [ReviewItem(label="公告", value=_review_text(params["message"]))]
        if action == "send_console_command":
            return [ReviewItem(label="命令", value=str(params["command"]))]
        if action == "create_backup":
            return [ReviewItem(label="备份内容", value="世界、模组和关键配置")]
        if action in {"verify_backup", "restore_backup"}:
            return self._backup_review_items(str(params["backup_id"]))
        if action == "edit_server_property":
            key = str(params["key"])
            current = self.adapter.get_properties()
            return [
                ReviewItem(label="设置", value=key),
                ReviewItem(label="当前值", value=_review_text(current.get(key))),
                ReviewItem(label="新值", value=_review_text(params["value"])),
            ]
        if action in {
            "add_whitelist_player",
            "remove_whitelist_player",
            "add_operator",
            "remove_operator",
        }:
            return [ReviewItem(label="玩家", value=str(params["player"]))]
        if action == "install_mod":
            upload_id = str(params["upload_id"])
            try:
                upload = self.adapter.get_mod_upload(upload_id)
            except KeyError as exc:
                raise ValueError("没有找到对应的隔离区模组") from exc
            return [
                ReviewItem(label="隔离文件 ID", value=upload_id),
                ReviewItem(label="模组文件", value=_review_text(upload.get("filename"))),
                ReviewItem(label="文件大小", value=_format_size(upload.get("size_bytes"))),
            ]
        if action == "disable_mod":
            mod_id = str(params["mod_id"])
            selected = next(
                (item for item in self.adapter.list_mods() if item.get("id") == mod_id),
                None,
            )
            if selected is None:
                raise ValueError("没有找到对应的模组")
            name = _review_text(selected.get("name"))
            filename = _review_text(selected.get("filename"))
            return [
                ReviewItem(label="模组 ID", value=mod_id),
                ReviewItem(label="模组", value=f"{name}（{filename}）"),
            ]
        if action == "set_backup_schedule":
            current = self.adapter.get_backup_schedule()
            return [
                ReviewItem(
                    label="当前计划",
                    value=(
                        f"每天 {int(current.get('hour', 0)):02d}:"
                        f"{int(current.get('minute', 0)):02d}，"
                        f"保留 {int(current.get('keep', 0))} 份"
                    ),
                ),
                ReviewItem(
                    label="新计划",
                    value=(
                        f"每天 {int(params['hour']):02d}:{int(params['minute']):02d}，"
                        f"保留 {int(params['keep'])} 份"
                    ),
                ),
            ]
        raise ValueError("操作缺少可审核的精确目标")

    def _backup_review_items(self, backup_id: str) -> list[ReviewItem]:
        selected = next(
            (item for item in self.adapter.list_backups() if item.get("id") == backup_id),
            None,
        )
        if selected is None:
            raise ValueError("没有找到对应的备份")
        return [
            ReviewItem(label="备份 ID", value=backup_id),
            ReviewItem(label="备份时间", value=_review_text(selected.get("created_at"))),
            ReviewItem(label="备份大小", value=_format_size(selected.get("size_bytes"))),
            ReviewItem(
                label="完整性",
                value=_verification_label(selected.get("verification_status")),
            ),
        ]

    @staticmethod
    def _assurance_expected(action: str) -> str:
        if action in {
            "send_announcement",
            "send_console_command",
            "add_whitelist_player",
            "remove_whitelist_player",
            "add_operator",
            "remove_operator",
        }:
            return "只确认命令已发送，不证明游戏内效果。"
        if action in {"start_server", "stop_server", "restart_server"}:
            return "执行后重新读取 systemd 服务状态。"
        if action == "edit_server_property":
            return "写入后重新读取受控配置值。"
        if action in {"create_backup", "verify_backup", "restore_backup"}:
            return "完成备份内容、文件身份和必要后置条件检查。"
        return "以后端受控组件返回的结构化结果为准。"

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        _, normalized = self.validate(action, params)
        return self.adapter.execute(action, normalized)
