import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
import zipfile
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .activity import REFRESH_SECONDS, PlayerActivityService
from .adapters import MockMinecraftAdapter, SystemdMinecraftAdapter
from .adapters.base import MinecraftAdapter
from .agent import AgentError, AgentService
from .auth import AuthenticatedUser, AuthService
from .config import Settings
from .database import Database
from .diagnostics import build_diagnostics
from .models import (
    AiSettingsPatch,
    AnnouncementParams,
    ApiError,
    ApiResponse,
    BackupScheduleParams,
    ChangePasswordRequest,
    ChatRequest,
    ConfirmationRequest,
    ConsoleCommandParams,
    EditPropertyParams,
    InstallModParams,
    LoginRequest,
    ModObjectParams,
    OperationRequest,
    PlayerParams,
    RestoreBackupParams,
    Role,
)
from .operations import OperationError, OperationService
from .redaction import redact
from .tools import ToolRegistry

COOKIE_NAME = "mc_panel_session"
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_" + uuid.uuid4().hex)


def _success(request: Request, data: Any, status_code: int = 200) -> JSONResponse:
    body = ApiResponse(ok=True, data=data, request_id=_request_id(request))
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


def _error_response(
    request: Request,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ApiResponse(
        ok=False,
        error=ApiError(code=code, message=message, details=redact(details or {})),
        request_id=_request_id(request),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    database.migrate()
    auth = AuthService(database, settings)
    adapter: MinecraftAdapter
    if settings.adapter == "systemd":
        adapter = SystemdMinecraftAdapter(settings)
    else:
        adapter = MockMinecraftAdapter(settings.mock_root)
    activity = PlayerActivityService(database, adapter)
    registry = ToolRegistry(adapter)
    operations = OperationService(database, registry, adapter, settings.server_display_name)
    agent = AgentService(settings, database, adapter, registry, activity)

    async def activity_collector() -> None:
        while True:
            try:
                await asyncio.to_thread(activity.refresh_if_stale, force=True)
            except Exception:
                logger.exception("background player activity collection failed")
            await asyncio.sleep(REFRESH_SECONDS)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        collector = asyncio.create_task(activity_collector())
        try:
            yield
        finally:
            collector.cancel()
            with suppress(asyncio.CancelledError):
                await collector

    app = FastAPI(
        title="方块管家 API",
        version="0.4.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.auth = auth
    app.state.adapter = adapter
    app.state.registry = registry
    app.state.operations = operations
    app.state.agent = agent
    app.state.activity = activity
    app.state.login_attempts = defaultdict(deque)

    @app.middleware("http")
    async def security_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = "req_" + uuid.uuid4().hex
        origin = request.headers.get("origin")
        expected_origin = str(request.base_url).rstrip("/")
        response: Response
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and origin.rstrip("/") != expected_origin
        ):
            response = _error_response(
                request,
                "origin_failed",
                "请求来源安全校验失败",
                403,
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self' ws: wss:; font-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(OperationError)
    async def operation_error_handler(request: Request, exc: OperationError) -> JSONResponse:
        status = 409 if exc.code not in {"confirmation_not_found"} else 404
        return _error_response(request, exc.code, exc.message, status, exc.details)

    @app.exception_handler(AgentError)
    async def agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
        status = (
            429
            if exc.code in {"rate_limited", "ai_budget_exceeded", "provider_rate_limited"}
            else 503
        )
        return _error_response(request, exc.code, exc.message, status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "location": [str(item) for item in error.get("loc", ())],
                "type": str(error.get("type", "validation_error")),
                "message": str(error.get("msg", "输入格式不正确"))[:300],
            }
            for error in exc.errors()
        ]
        return _error_response(
            request,
            "validation_failed",
            "提交的内容格式不正确",
            422,
            {"fields": fields},
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "request_failed"))
            message = str(exc.detail.get("message", "请求失败"))
            details: dict[str, Any] = dict(exc.detail.get("details", {}))
        else:
            code, message, details = "request_failed", str(exc.detail), {}
        return _error_response(request, code, message, exc.status_code, details)

    def current_user(request: Request) -> AuthenticatedUser:
        token = request.cookies.get(COOKIE_NAME, "")
        user = auth.resolve_session(token)
        if user is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "authentication_required", "message": "请先登录"},
            )
        return user

    def owner(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> AuthenticatedUser:
        if user.role is not Role.OWNER:
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "当前账号没有操作权限"},
            )
        return user

    def csrf_user(
        request: Request, user: Annotated[AuthenticatedUser, Depends(current_user)]
    ) -> AuthenticatedUser:
        if not auth.verify_csrf(user.session_token, request.headers.get("X-CSRF-Token", "")):
            raise HTTPException(
                status_code=403,
                detail={"code": "csrf_failed", "message": "安全校验失败，请刷新页面后重试"},
            )
        return user

    def csrf_owner(user: Annotated[AuthenticatedUser, Depends(csrf_user)]) -> AuthenticatedUser:
        if user.role is not Role.OWNER:
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "当前账号没有操作权限"},
            )
        return user

    User = Annotated[AuthenticatedUser, Depends(current_user)]
    CsrfUser = Annotated[AuthenticatedUser, Depends(csrf_user)]
    Owner = Annotated[AuthenticatedUser, Depends(csrf_owner)]

    def dashboard_snapshot() -> dict[str, Any]:
        status = adapter.get_status()
        metrics = adapter.get_metrics()
        players = adapter.list_players()
        backups = adapter.list_backups()
        schedule = adapter.get_backup_schedule()
        backup_status = adapter.get_backup_status()
        crashes = adapter.list_crash_reports()
        return {
            "server": status,
            "server_name": settings.server_display_name,
            "connection_address": settings.public_address or None,
            "metrics": metrics,
            "players": players,
            "ai": agent.status(),
            "diagnostics": build_diagnostics(
                status=status,
                metrics=metrics,
                players=players,
                backups=backups,
                schedule=schedule,
                backup_status=backup_status,
                crashes=crashes,
                local_timezone=settings.server_timezone,
            ),
            "quick_actions": [
                "start_server",
                "stop_server",
                "restart_server",
                "create_backup",
            ],
        }

    @app.get("/api/v1/health")
    def health(request: Request) -> JSONResponse:
        return _success(
            request,
            {
                "panel": "ok",
                "adapter": settings.adapter,
                "minecraft_independent": True,
                "production_writes_enabled": settings.production_writes_enabled,
            },
        )

    @app.post("/api/v1/auth/login")
    def login(payload: LoginRequest, request: Request) -> JSONResponse:
        client = request.client.host if request.client else "unknown"
        key = hashlib.sha256(f"{client}|{payload.username}".encode()).hexdigest()
        attempts: deque[float] = app.state.login_attempts[key]
        now = time.monotonic()
        while attempts and now - attempts[0] > 900:
            attempts.popleft()
        if len(attempts) >= 5:
            return _error_response(request, "rate_limited", "登录失败次数过多，请稍后再试", 429)
        user = auth.authenticate(payload.username, payload.password)
        if user is None:
            attempts.append(now)
            return _error_response(request, "invalid_credentials", "用户名或密码不正确", 401)
        attempts.clear()
        token = auth.create_session(int(user["id"]))
        response = _success(
            request,
            {
                "user": {"username": user["username"], "role": user["role"]},
                "csrf_token": auth.csrf_token(token),
            },
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=settings.session_secure,
            samesite="strict",
            max_age=settings.session_absolute_hours * 3600,
            path="/",
        )
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, user: CsrfUser) -> JSONResponse:
        auth.revoke_session(user.session_token)
        response = _success(request, {"logged_out": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.post("/api/v1/auth/change-password")
    def change_password(
        payload: ChangePasswordRequest, request: Request, user: CsrfUser
    ) -> JSONResponse:
        try:
            token = auth.change_password(user.id, payload.current_password, payload.new_password)
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={"code": "password_rejected", "message": str(exc)},
            ) from exc
        if token is None:
            raise HTTPException(
                403,
                detail={"code": "current_password_invalid", "message": "当前密码不正确"},
            )
        database.add_audit(
            event_id="audit_" + uuid.uuid4().hex,
            user_id=user.id,
            action="change_password",
            risk="medium",
            outcome="succeeded",
            request_id=_request_id(request),
            confirmation_id=None,
            params_summary={},
            result_summary={"sessions_revoked": True},
        )
        response = _success(
            request,
            {
                "changed": True,
                "csrf_token": auth.csrf_token(token),
                "sessions_revoked": True,
            },
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=settings.session_secure,
            samesite="strict",
            max_age=settings.session_absolute_hours * 3600,
            path="/",
        )
        return response

    @app.get("/api/v1/auth/me")
    def me(request: Request, user: User) -> JSONResponse:
        return _success(
            request,
            {
                "username": user.username,
                "role": user.role.value,
                "csrf_token": auth.csrf_token(user.session_token),
            },
        )

    @app.post("/api/v1/auth/ws-ticket")
    def ws_ticket(request: Request, user: CsrfUser) -> JSONResponse:
        return _success(request, {"ticket": auth.create_ws_ticket(user.id), "expires_in": 30})

    @app.get("/api/v1/dashboard")
    def dashboard(request: Request, _: User) -> JSONResponse:
        return _success(request, dashboard_snapshot())

    @app.get("/api/v1/diagnostics")
    def diagnostics(request: Request, _: User) -> JSONResponse:
        return _success(request, dashboard_snapshot()["diagnostics"])

    @app.get("/api/v1/server/status")
    def server_status(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.get_status())

    @app.get("/api/v1/server/metrics")
    def server_metrics(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.get_metrics())

    @app.get("/api/v1/server/players")
    def server_players(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_players())

    @app.get("/api/v1/server/player-activity")
    def player_activity(request: Request, _: User, days: int = 7) -> JSONResponse:
        if not 1 <= days <= 30:
            raise HTTPException(422, "活跃天数需要在 1 到 30 之间")
        return _success(request, activity.get_activity(days))

    @app.get("/api/v1/server/detection")
    def server_detection(request: Request, _: User) -> JSONResponse:
        status = adapter.get_status()
        return _success(
            request,
            {
                "service": status["service"],
                "minecraft_version": status["minecraft_version"],
                "loader": status["loader"],
                "loader_version": status["loader_version"],
                "java_version": status["java_version"],
                "identity_sources": status.get("identity_sources", {}),
                "fingerprint": adapter.fingerprint(),
                "structure_approved": status.get("structure_approved", settings.adapter == "mock"),
                "writes_enabled": status.get("writes_enabled", settings.production_writes_enabled),
                "connection_address": settings.public_address or None,
            },
        )

    @app.get("/api/v1/logs/recent")
    def recent_logs(
        request: Request,
        _: User,
        source: str = "latest",
        limit: int = 200,
        severity: str = "",
    ) -> JSONResponse:
        if source not in {"latest", "kubejs", "systemd"} or not 1 <= limit <= 500:
            raise HTTPException(422, "日志参数不正确")
        levels = [
            item for item in severity.split(",") if item in {"INFO", "WARN", "ERROR", "FATAL"}
        ]
        return _success(request, redact(adapter.read_logs(source, limit, levels)))

    @app.get("/api/v1/crash-reports")
    def crash_reports(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_crash_reports())

    @app.get("/api/v1/crash-reports/{report_id}")
    def crash_report(report_id: str, request: Request, _: User) -> JSONResponse:
        if not report_id.startswith("crash_"):
            raise HTTPException(422, "崩溃报告编号不正确")
        try:
            data = adapter.read_crash_report(report_id)
        except KeyError as exc:
            raise HTTPException(404, "没有找到该崩溃报告") from exc
        return _success(request, redact(data))

    @app.get("/api/v1/mods")
    def mods(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_mods())

    @app.post("/api/v1/mods/uploads")
    async def upload_mod(
        request: Request, user: Owner, file: Annotated[UploadFile, File()]
    ) -> JSONResponse:
        raw_filename = file.filename or ""
        filename = Path(raw_filename).name
        invalid_filename = (
            filename != raw_filename
            or any(separator in raw_filename for separator in ("/", "\\", "\x00"))
            or filename.startswith(".")
            or not filename.isprintable()
            or len(filename.encode("utf-8")) > 184
        )
        if invalid_filename or not filename.lower().endswith(".jar"):
            raise HTTPException(422, "只能上传单个 JAR 文件")
        quarantine = settings.database_path.parent / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = quarantine / ("incoming_" + secrets.token_hex(12))
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("xb") as handle:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "模组文件超过 128 MiB 限制")
                    digest.update(chunk)
                    handle.write(chunk)
            if not zipfile.is_zipfile(temporary):
                raise HTTPException(422, "JAR 文件结构无效")
            with zipfile.ZipFile(temporary) as archive:
                entries = archive.infolist()
                if len(entries) > 10_000:
                    raise HTTPException(422, "JAR 内部文件数量异常")
                if any(info.flag_bits & 1 for info in entries):
                    raise HTTPException(422, "不接受加密 JAR")
                if sum(info.file_size for info in entries) > 512 * 1024 * 1024:
                    raise HTTPException(422, "JAR 解压后体积异常")
                if archive.testzip() is not None:
                    raise HTTPException(422, "JAR 校验失败")
                names = [entry.filename for entry in entries]
                metadata = next(
                    (
                        item
                        for item in (
                            "META-INF/mods.toml",
                            "META-INF/neoforge.mods.toml",
                            "fabric.mod.json",
                            "quilt.mod.json",
                        )
                        if item in names
                    ),
                    None,
                )
                if metadata is None:
                    raise HTTPException(422, "没有识别到受支持的模组元数据")
            upload_id = "upload_" + digest.hexdigest()[:16]
            destination = quarantine / f"{upload_id}.jar"
            temporary.replace(destination)
            destination.chmod(0o600)
            sidecar = quarantine / f"{upload_id}.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "filename": filename,
                        "sha256": digest.hexdigest(),
                        "size_bytes": total,
                        "metadata": metadata,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            sidecar.chmod(0o600)
            database.add_audit(
                event_id="audit_" + uuid.uuid4().hex,
                user_id=user.id,
                action="upload_mod",
                risk="medium",
                outcome="quarantined",
                request_id=_request_id(request),
                confirmation_id=None,
                params_summary={"filename": filename, "size_bytes": total},
                result_summary={
                    "upload_id": upload_id,
                    "sha256": digest.hexdigest(),
                    "metadata": metadata,
                },
            )
            return _success(
                request,
                {
                    "upload_id": upload_id,
                    "filename": filename,
                    "size_bytes": total,
                    "sha256": digest.hexdigest(),
                    "metadata": metadata,
                    "state": "quarantined",
                },
                201,
            )
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/properties")
    def properties(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.get_properties())

    @app.get("/api/v1/whitelist")
    def whitelist(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_whitelist())

    @app.get("/api/v1/operators")
    def operators_list(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_operators())

    @app.get("/api/v1/backups")
    def backups(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.list_backups())

    @app.get("/api/v1/backup-schedule")
    def backup_schedule(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.get_backup_schedule())

    @app.get("/api/v1/backup-status")
    def backup_status(request: Request, _: User) -> JSONResponse:
        return _success(request, adapter.get_backup_status())

    @app.post("/api/v1/operations/preview")
    def preview(payload: OperationRequest, request: Request, _: User) -> JSONResponse:
        try:
            data = registry.preview(payload.action, payload.params)
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={
                    "code": "validation_failed",
                    "message": "操作参数不正确",
                    "details": {"reason": str(exc)[:1000]},
                },
            ) from exc
        return _success(request, data)

    @app.post("/api/v1/operations")
    def request_operation(payload: OperationRequest, request: Request, user: Owner) -> JSONResponse:
        try:
            data = operations.request(
                action=payload.action,
                params=payload.params,
                user_id=user.id,
                request_id=_request_id(request),
            )
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={
                    "code": "validation_failed",
                    "message": "操作参数不正确",
                    "details": {"reason": str(exc)[:1000]},
                },
            ) from exc
        return _success(
            request, data, 202 if data.get("status") == "confirmation_required" else 200
        )

    @app.post("/api/v1/confirmations/{confirmation_id}/confirm")
    def confirm_once(
        confirmation_id: str,
        payload: ConfirmationRequest,
        request: Request,
        user: Owner,
    ) -> JSONResponse:
        data = operations.confirm(
            confirmation_id=confirmation_id,
            user_id=user.id,
            request_id=_request_id(request),
            second=False,
            server_name=payload.server_name,
        )
        return _success(request, data)

    @app.post("/api/v1/confirmations/{confirmation_id}/confirm-again")
    def confirm_twice(
        confirmation_id: str,
        payload: ConfirmationRequest,
        request: Request,
        user: Owner,
    ) -> JSONResponse:
        data = operations.confirm(
            confirmation_id=confirmation_id,
            user_id=user.id,
            request_id=_request_id(request),
            second=True,
            server_name=payload.server_name,
        )
        return _success(request, data)

    def submit(
        action: str, params: dict[str, Any], request: Request, user: AuthenticatedUser
    ) -> JSONResponse:
        data = operations.request(
            action=action,
            params=params,
            user_id=user.id,
            request_id=_request_id(request),
        )
        return _success(
            request, data, 202 if data.get("status") == "confirmation_required" else 200
        )

    @app.post("/api/v1/server/start")
    def start_server(request: Request, user: Owner) -> JSONResponse:
        return submit("start_server", {}, request, user)

    @app.post("/api/v1/server/stop")
    def stop_server(request: Request, user: Owner) -> JSONResponse:
        return submit("stop_server", {}, request, user)

    @app.post("/api/v1/server/restart")
    def restart_server(request: Request, user: Owner) -> JSONResponse:
        return submit("restart_server", {}, request, user)

    @app.post("/api/v1/console/announcements")
    def announce(payload: AnnouncementParams, request: Request, user: Owner) -> JSONResponse:
        return submit("send_announcement", payload.model_dump(), request, user)

    @app.post("/api/v1/console/commands")
    def console_command(
        payload: ConsoleCommandParams, request: Request, user: Owner
    ) -> JSONResponse:
        return submit("send_console_command", payload.model_dump(), request, user)

    @app.post("/api/v1/backups")
    def create_backup(request: Request, user: Owner) -> JSONResponse:
        return submit("create_backup", {}, request, user)

    @app.post("/api/v1/backups/{backup_id}/restore")
    def restore_backup(backup_id: str, request: Request, user: Owner) -> JSONResponse:
        payload = RestoreBackupParams(backup_id=backup_id)
        return submit("restore_backup", payload.model_dump(), request, user)

    @app.patch("/api/v1/properties")
    def edit_property(payload: EditPropertyParams, request: Request, user: Owner) -> JSONResponse:
        return submit("edit_server_property", payload.model_dump(), request, user)

    @app.post("/api/v1/whitelist/{player}")
    def whitelist_add(player: str, request: Request, user: Owner) -> JSONResponse:
        payload = PlayerParams(player=player)
        return submit("add_whitelist_player", payload.model_dump(), request, user)

    @app.delete("/api/v1/whitelist/{player}")
    def whitelist_remove(player: str, request: Request, user: Owner) -> JSONResponse:
        payload = PlayerParams(player=player)
        return submit("remove_whitelist_player", payload.model_dump(), request, user)

    @app.post("/api/v1/operators/{player}")
    def operator_add(player: str, request: Request, user: Owner) -> JSONResponse:
        payload = PlayerParams(player=player)
        return submit("add_operator", payload.model_dump(), request, user)

    @app.delete("/api/v1/operators/{player}")
    def operator_remove(player: str, request: Request, user: Owner) -> JSONResponse:
        payload = PlayerParams(player=player)
        return submit("remove_operator", payload.model_dump(), request, user)

    @app.post("/api/v1/mods/{mod_id}/disable")
    def disable_mod(mod_id: str, request: Request, user: Owner) -> JSONResponse:
        payload = ModObjectParams(mod_id=mod_id)
        return submit("disable_mod", payload.model_dump(), request, user)

    @app.post("/api/v1/mods/{upload_id}/install")
    def install_mod(upload_id: str, request: Request, user: Owner) -> JSONResponse:
        payload = InstallModParams(upload_id=upload_id)
        return submit("install_mod", payload.model_dump(), request, user)

    @app.patch("/api/v1/backup-schedule")
    def set_backup_schedule(
        payload: BackupScheduleParams, request: Request, user: Owner
    ) -> JSONResponse:
        return submit("set_backup_schedule", payload.model_dump(), request, user)

    @app.get("/api/v1/audit-events")
    def audit_events(request: Request, user: User, limit: int = 100) -> JSONResponse:
        if user.role is not Role.OWNER:
            raise HTTPException(403, "只有管理员可以查看审计日志")
        limit = max(1, min(limit, 500))
        rows = database.fetch_all(
            """
            SELECT id, created_at, action, risk, outcome, request_id,
                   confirmation_id, params_summary, result_summary
            FROM audit_events ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row["params_summary"] = json.loads(row["params_summary"])
            row["result_summary"] = json.loads(row["result_summary"])
        return _success(request, redact(rows))

    @app.get("/api/v1/ai/settings")
    def ai_settings(request: Request, _: User) -> JSONResponse:
        return _success(request, agent.status())

    @app.patch("/api/v1/ai/settings")
    def update_ai_settings(payload: AiSettingsPatch, request: Request, user: Owner) -> JSONResponse:
        values = payload.model_dump(exclude_none=True)
        submitted_base_url = values.pop("api_base_url", None)
        displayed_base_url = agent.status()["api_base_url"]
        if submitted_base_url is not None and submitted_base_url != (displayed_base_url or ""):
            raise HTTPException(
                409,
                detail={
                    "code": "provider_configuration_locked",
                    "message": "API Base URL 只能在服务器受限环境文件中修改",
                },
            )
        updated = agent.update_settings(values)
        database.add_audit(
            event_id="audit_" + uuid.uuid4().hex,
            user_id=user.id,
            action="update_ai_settings",
            risk="medium",
            outcome="succeeded",
            request_id=_request_id(request),
            confirmation_id=None,
            params_summary=values,
            result_summary={"configured": updated["configured"], "enabled": updated["enabled"]},
        )
        return _success(request, updated)

    @app.get("/api/v1/ai/usage")
    def ai_usage(request: Request, _: User) -> JSONResponse:
        return _success(request, agent.usage())

    @app.post("/api/v1/ai/chat")
    def ai_chat(payload: ChatRequest, request: Request, _: CsrfUser) -> JSONResponse:
        return _success(request, agent.chat(payload.message))

    @app.websocket("/api/v1/ws/console")
    async def console_socket(websocket: WebSocket, ticket: str) -> None:
        allowed_origins = {
            f"http://127.0.0.1:{settings.port}",
            f"http://localhost:{settings.port}",
            f"https://127.0.0.1:{settings.port}",
            f"https://localhost:{settings.port}",
        }
        if not settings.is_production:
            allowed_origins.update({"http://127.0.0.1:5173", "http://localhost:5173"})
        if settings.panel_public_origin:
            allowed_origins.add(settings.panel_public_origin)
        origin = websocket.headers.get("origin", "")
        if origin not in allowed_origins or auth.consume_ws_ticket(ticket) is None:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        previous: list[str] = []
        try:
            while True:
                current = redact(adapter.read_logs(lines=120))["lines"]
                if current != previous:
                    await websocket.send_json({"type": "logs", "lines": current})
                    previous = current
                try:
                    event = await asyncio.wait_for(websocket.receive(), timeout=1)
                except TimeoutError:
                    continue
                if event["type"] == "websocket.disconnect":
                    return
                await websocket.close(code=1008)
                return
        except (WebSocketDisconnect, asyncio.CancelledError):
            return

    web_dist = settings.web_dist_path
    if web_dist.is_dir():
        assets = web_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/api/{path:path}", include_in_schema=False)
        def unknown_api(path: str) -> None:
            raise HTTPException(
                status_code=404,
                detail={"code": "api_not_found", "message": "API 地址不存在"},
            )

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = (web_dist / path).resolve()
            if candidate.is_file() and web_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def root(request: Request) -> JSONResponse:
            return _success(request, {"name": "方块管家", "frontend": "not_built"})

    return app


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.bind,
        port=settings.port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=10,
    )
