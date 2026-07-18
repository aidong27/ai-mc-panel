from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .adapters.base import AdapterOperationError, MinecraftAdapter
from .database import Database, utc_now
from .models import OperationReview, OperationState, RiskLevel
from .redaction import redact, redact_text
from .tools import ToolRegistry, operation_params_hash


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _canonical(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    secret_names = {"password", "token", "api_key", "cookie", "authorization"}
    return {
        key: "[REDACTED]" if key.lower() in secret_names else value for key, value in params.items()
    }


class OperationService:
    def __init__(
        self,
        database: Database,
        registry: ToolRegistry,
        adapter: MinecraftAdapter,
        server_name: str,
    ) -> None:
        self.database = database
        self.registry = registry
        self.adapter = adapter
        self.server_name = server_name

    def request(
        self,
        *,
        action: str,
        params: dict[str, Any],
        user_id: int,
        request_id: str,
    ) -> dict[str, Any]:
        spec, normalized = self.registry.validate(action, params)
        risk = spec.risk
        if risk is RiskLevel.LOW:
            return self._execute(
                action=action,
                params=normalized,
                user_id=user_id,
                request_id=request_id,
                confirmation_id=None,
                risk=risk,
            )

        confirmation_id = "confirm_" + uuid.uuid4().hex
        expires = datetime.now(UTC) + timedelta(minutes=5)
        preview = self.registry.build_preview(
            spec,
            normalized,
            operation_id=confirmation_id,
            expires_at=expires.isoformat(),
        )
        frozen = {"params": normalized, "review": preview["review"]}
        canonical = _canonical(frozen)
        self.database.execute(
            """
            INSERT INTO confirmations(
                id, user_id, action, params_json, params_hash, risk, state,
                fingerprint, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                confirmation_id,
                user_id,
                action,
                canonical,
                hashlib.sha256(canonical.encode()).hexdigest(),
                risk.value,
                OperationState.PENDING.value,
                self.adapter.fingerprint(),
                utc_now(),
                expires.isoformat(),
            ),
        )
        self._audit(
            user_id=user_id,
            action=action,
            risk=risk,
            outcome="confirmation_required",
            request_id=request_id,
            confirmation_id=confirmation_id,
            params=normalized,
            result={"expires_at": expires.isoformat()},
        )
        return {**preview, "status": "confirmation_required", "confirmation_id": confirmation_id}

    def confirm(
        self,
        *,
        confirmation_id: str,
        user_id: int,
        request_id: str,
        second: bool,
        server_name: str | None,
    ) -> dict[str, Any]:
        record = self.database.fetch_one(
            "SELECT * FROM confirmations WHERE id = ? AND user_id = ?",
            (confirmation_id, user_id),
        )
        if record is None:
            raise OperationError("confirmation_not_found", "确认请求不存在")
        if datetime.now(UTC) >= datetime.fromisoformat(str(record["expires_at"])):
            raise OperationError("confirmation_expired", "确认已过期，请重新发起操作")
        if record["fingerprint"] != self.adapter.fingerprint():
            raise OperationError(
                "server_structure_changed", "服务器结构已变化，写操作已锁定，请重新审计"
            )

        risk = RiskLevel(str(record["risk"]))
        state = OperationState(str(record["state"]))
        canonical = str(record["params_json"])
        if not hashlib.sha256(canonical.encode()).hexdigest() == str(record["params_hash"]):
            raise OperationError("confirmation_invalid", "确认参数校验失败，请重新发起操作")
        try:
            frozen: Any = json.loads(canonical)
            if not isinstance(frozen, dict) or set(frozen) != {"params", "review"}:
                raise ValueError("invalid frozen confirmation")
            frozen_params = frozen["params"]
            if not isinstance(frozen_params, dict):
                raise ValueError("invalid frozen params")
            frozen_review = frozen["review"]
            if not isinstance(frozen_review, dict):
                raise ValueError("invalid frozen review")
            review = OperationReview.model_validate_json(_canonical(frozen_review))
            spec, params = self.registry.validate(str(record["action"]), frozen_params)
        except (TypeError, ValueError) as exc:
            raise OperationError(
                "confirmation_invalid", "确认内容校验失败，请重新发起操作"
            ) from exc
        if (
            params != frozen_params
            or spec.risk is not risk
            or review.operation_id != confirmation_id
            or review.risk is not risk
            or review.params_hash != operation_params_hash(str(record["action"]), params)
            or review.expires_at != str(record["expires_at"])
        ):
            raise OperationError("confirmation_invalid", "确认内容已变化，请重新发起操作")
        now = utc_now()
        if risk is RiskLevel.HIGH:
            if state is OperationState.PENDING and not second:
                updated = self.database.execute(
                    """
                    UPDATE confirmations
                    SET state = ?, first_confirmed_at = ?
                    WHERE id = ? AND user_id = ? AND state = ? AND expires_at > ?
                    """,
                    (
                        OperationState.FIRST_CONFIRMED.value,
                        now,
                        confirmation_id,
                        user_id,
                        OperationState.PENDING.value,
                        now,
                    ),
                )
                if updated != 1:
                    raise OperationError("confirmation_invalid", "当前确认状态不允许执行")
                return {
                    "status": "second_confirmation_required",
                    "confirmation_id": confirmation_id,
                    "prompt": f"请输入服务器名称 {self.server_name} 完成第二次确认",
                    "review": review.model_dump(mode="json"),
                }
            if state is not OperationState.FIRST_CONFIRMED or not second:
                raise OperationError("confirmation_required", "需要完成两次确认")
            if server_name != self.server_name:
                raise OperationError("confirmation_phrase_mismatch", "服务器名称不匹配")
        elif second or state is not OperationState.PENDING:
            raise OperationError("confirmation_invalid", "当前确认状态不允许执行")

        expected_state = (
            OperationState.FIRST_CONFIRMED if risk is RiskLevel.HIGH else OperationState.PENDING
        )
        updated = self.database.execute(
            """
            UPDATE confirmations SET state = ?
            WHERE id = ? AND user_id = ? AND state = ? AND expires_at > ?
            """,
            (
                OperationState.EXECUTING.value,
                confirmation_id,
                user_id,
                expected_state.value,
                now,
            ),
        )
        if updated != 1:
            raise OperationError("confirmation_invalid", "当前确认状态不允许执行")
        return self._execute(
            action=str(record["action"]),
            params=params,
            user_id=user_id,
            request_id=request_id,
            confirmation_id=confirmation_id,
            risk=risk,
        )

    def _execute(
        self,
        *,
        action: str,
        params: dict[str, Any],
        user_id: int,
        request_id: str,
        confirmation_id: str | None,
        risk: RiskLevel,
    ) -> dict[str, Any]:
        operation_id = "op_" + uuid.uuid4().hex
        self.database.execute(
            """
            INSERT INTO operations(id, confirmation_id, user_id, action, state, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                confirmation_id,
                user_id,
                action,
                OperationState.EXECUTING.value,
                utc_now(),
            ),
        )
        try:
            result = self.registry.execute(action, params)
            outcome = OperationState.SUCCEEDED
        except AdapterOperationError as exc:
            safe_details = redact(exc.details)
            result = {
                "error": exc.code,
                "message": exc.message[:500],
                "details": safe_details if isinstance(safe_details, dict) else {},
            }
            outcome = OperationState.FAILED
        except Exception as exc:
            result = {
                "error": type(exc).__name__,
                "message": redact_text(str(exc), limit=500),
            }
            outcome = OperationState.FAILED
        self.database.execute(
            "UPDATE operations SET state = ?, finished_at = ?, result_json = ? WHERE id = ?",
            (outcome.value, utc_now(), _canonical(result), operation_id),
        )
        if confirmation_id:
            self.database.execute(
                "UPDATE confirmations SET state = ?, result_json = ? WHERE id = ?",
                (outcome.value, _canonical(result), confirmation_id),
            )
        self._audit(
            user_id=user_id,
            action=action,
            risk=risk,
            outcome=outcome.value,
            request_id=request_id,
            confirmation_id=confirmation_id,
            params=params,
            result=result,
        )
        if outcome is OperationState.FAILED:
            raise OperationError("operation_failed", "操作失败，未报告为成功", result)
        return {"status": outcome.value, "operation_id": operation_id, "result": result}

    def _audit(
        self,
        *,
        user_id: int,
        action: str,
        risk: RiskLevel,
        outcome: str,
        request_id: str,
        confirmation_id: str | None,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        safe_params = redact(_safe_params(params))
        safe_result = redact({key: value for key, value in result.items() if key != "excerpt"})
        self.database.add_audit(
            event_id="audit_" + uuid.uuid4().hex,
            user_id=user_id,
            action=action,
            risk=risk.value,
            outcome=outcome,
            request_id=request_id,
            confirmation_id=confirmation_id,
            params_summary=safe_params if isinstance(safe_params, dict) else {},
            result_summary=safe_result if isinstance(safe_result, dict) else {},
        )
