from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Role(StrEnum):
    OWNER = "owner"
    VIEWER = "viewer"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperationState(StrEnum):
    PENDING = "pending"
    FIRST_CONFIRMED = "first_confirmed"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewItem(StrictModel):
    label: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=500)


class OperationReview(StrictModel):
    operation_id: str = Field(
        min_length=10,
        max_length=80,
        pattern=r"^(?:confirm|review)_[a-f0-9]+$",
    )
    human_title: str = Field(min_length=1, max_length=120)
    risk: RiskLevel
    review_items: list[ReviewItem] = Field(min_length=1, max_length=12)
    impact: str = Field(min_length=1, max_length=500)
    stops_server: bool
    recovery_plan: str = Field(min_length=1, max_length=500)
    assurance_expected: str = Field(min_length=1, max_length=500)
    params_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: str | None = Field(default=None, max_length=80)


class LoginRequest(StrictModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用户名只能包含字母、数字、下划线和连字符")
        return normalized


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=10, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class OperationRequest(StrictModel):
    action: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    params: dict[str, Any] = Field(default_factory=dict)


class ConfirmationRequest(StrictModel):
    server_name: str | None = Field(default=None, max_length=80)


class AnnouncementParams(StrictModel):
    message: str = Field(min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def safe_message(cls, value: str) -> str:
        value = value.strip()
        if not value or value.startswith("/") or any(ord(char) < 32 for char in value):
            raise ValueError("公告不能包含命令、换行或控制字符")
        return value


class EmptyParams(StrictModel):
    pass


class EditPropertyParams(StrictModel):
    key: Literal[
        "difficulty",
        "gamemode",
        "max-players",
        "pvp",
        "view-distance",
        "simulation-distance",
        "white-list",
        "motd",
    ]
    value: str | int | bool

    @model_validator(mode="after")
    def validate_property_value(self) -> EditPropertyParams:
        value = self.value
        if self.key in {"pvp", "white-list"}:
            if not isinstance(value, bool):
                raise ValueError("该设置必须使用开关")
            return self
        if self.key == "max-players":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                raise ValueError("最大玩家数需要在 1 到 100 之间")
            return self
        if self.key in {"view-distance", "simulation-distance"}:
            if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 32:
                raise ValueError("距离需要在 2 到 32 之间")
            return self
        if self.key == "difficulty":
            if value not in {"peaceful", "easy", "normal", "hard"}:
                raise ValueError("游戏难度不正确")
            return self
        if self.key == "gamemode":
            if value not in {"survival", "creative", "adventure", "spectator"}:
                raise ValueError("默认游戏模式不正确")
            return self
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 120
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("服务器列表名称格式不正确")
        return self


class PlayerParams(StrictModel):
    player: str = Field(min_length=3, max_length=16, pattern=r"^[A-Za-z0-9_]+$")


class ConsoleCommandParams(StrictModel):
    command: str = Field(min_length=1, max_length=200)

    @field_validator("command")
    @classmethod
    def safe_command(cls, value: str) -> str:
        normalized = value.strip().lstrip("/")
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("控制台命令不能包含换行或控制字符")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. :@#~[]{}")
        if any(
            (ord(char) < 128 and char not in allowed)
            or (ord(char) >= 128 and not char.isprintable())
            for char in normalized
        ):
            raise ValueError("控制台命令包含不允许的字符")
        return normalized


class RestoreBackupParams(StrictModel):
    backup_id: str = Field(pattern=r"^backup_[a-f0-9]{12}$")


class ModObjectParams(StrictModel):
    mod_id: str = Field(pattern=r"^mod_[a-zA-Z0-9_-]{3,80}$")


class InstallModParams(StrictModel):
    upload_id: str = Field(pattern=r"^upload_[a-f0-9]{16}$")


class BackupScheduleParams(StrictModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    keep: int = Field(ge=3, le=30)


class LogQuery(StrictModel):
    source: Literal["latest", "kubejs", "systemd"] = "latest"
    lines: int = Field(default=200, ge=1, le=500)
    severity: list[Literal["INFO", "WARN", "ERROR", "FATAL"]] = Field(default_factory=list)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)


class AiSettingsPatch(StrictModel):
    enabled: bool | None = None
    api_base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=64, le=4096)
    timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    requests_per_minute: int | None = Field(default=None, ge=1, le=30)
    requests_per_day: int | None = Field(default=None, ge=1, le=1000)
    tokens_per_day: int | None = Field(default=None, ge=1000, le=10_000_000)

    @field_validator("api_base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        normalized = value.rstrip("/")
        if not normalized.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("API Base URL 必须使用 HTTPS；本机测试可以使用 localhost")
        return normalized


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: ApiError | None = None
    request_id: str
