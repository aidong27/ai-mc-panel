from __future__ import annotations

import json
from dataclasses import dataclass
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
    PlayerParams,
    RestoreBackupParams,
    RiskLevel,
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    risk: RiskLevel
    params_model: type[BaseModel]
    title: str
    reason: str
    impact: str
    stops_server: bool
    creates_recovery_point: bool


SPECS: dict[str, ToolSpec] = {
    "start_server": ToolSpec(
        "start_server",
        RiskLevel.MEDIUM,
        EmptyParams,
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
        "恢复旧备份",
        "把世界和关键配置恢复到选定时间。",
        "会停服并替换当前数据；执行前再次备份，失败时自动回滚。",
        True,
        True,
    ),
}


class ToolRegistry:
    def __init__(self, adapter: MinecraftAdapter) -> None:
        self.adapter = adapter

    def validate(self, action: str, params: dict[str, Any]) -> tuple[ToolSpec, dict[str, Any]]:
        spec = SPECS.get(action)
        if spec is None:
            raise ValueError("未知或未授权的操作")
        try:
            validated = spec.params_model.model_validate(params)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise ValueError(json.dumps(errors, ensure_ascii=False)) from exc
        return spec, validated.model_dump()

    def preview(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        spec, normalized = self.validate(action, params)
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
            "rollback": "使用自动恢复点回滚" if spec.creates_recovery_point else "无需数据回滚",
        }

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        _, normalized = self.validate(action, params)
        return self.adapter.execute(action, normalized)
