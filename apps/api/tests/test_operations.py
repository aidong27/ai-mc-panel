from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient

from mc_panel_api.adapters.base import AdapterOperationError
from mc_panel_api.operations import OperationError


def test_low_risk_backup_executes_and_is_audited(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    before = client.get("/api/v1/backups").json()["data"]
    response = client.post("/api/v1/backups", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"
    after = client.get("/api/v1/backups").json()["data"]
    assert len(after) == len(before) + 1
    audit = client.get("/api/v1/audit-events").json()["data"]
    assert audit[0]["action"] == "create_backup"
    assert audit[0]["outcome"] == "succeeded"


def test_low_risk_backup_verification_executes_without_confirmation(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    before = client.get("/api/v1/backups").json()["data"]
    backup_id = before[0]["id"]
    assert before[0]["verification_status"] == "checksum_present"

    response = client.post(f"/api/v1/backups/{backup_id}/verify", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"
    after = client.get("/api/v1/backups").json()["data"]
    assert after[0]["verified"] is True
    assert after[0]["verification_status"] == "verified"
    audit = client.get("/api/v1/audit-events").json()["data"]
    assert audit[0]["action"] == "verify_backup"
    assert audit[0]["risk"] == "low"


def test_restart_needs_confirmation_and_only_runs_after_confirm(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    requested = client.post("/api/v1/server/restart", headers=headers)
    assert requested.status_code == 202
    data = requested.json()["data"]
    assert data["status"] == "confirmation_required"
    confirmation_id = data["confirmation_id"]

    audit_before = client.get("/api/v1/audit-events").json()["data"]
    assert audit_before[0]["outcome"] == "confirmation_required"

    confirmed = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm",
        headers=headers,
        json={},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "succeeded"
    logs = client.get("/api/v1/logs/recent?limit=20").json()["data"]["lines"]
    assert any("safe restart" in line for line in logs)


def test_restore_requires_two_distinct_confirmations(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    backup_id = client.get("/api/v1/backups").json()["data"][0]["id"]
    requested = client.post(f"/api/v1/backups/{backup_id}/restore", headers=headers)
    confirmation_id = requested.json()["data"]["confirmation_id"]

    first = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm", headers=headers, json={}
    )
    assert first.json()["data"]["status"] == "second_confirmation_required"

    wrong = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm-again",
        headers=headers,
        json={"server_name": "wrong"},
    )
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "confirmation_phrase_mismatch"

    final = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm-again",
        headers=headers,
        json={"server_name": "星光好友服"},
    )
    assert final.status_code == 200
    assert final.json()["data"]["status"] == "succeeded"


def test_console_injection_is_rejected(logged_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/console/commands",
        headers=headers,
        json={"command": "say ok; reboot"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_unicode_console_command_uses_the_confirmation_flow(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    requested = client.post(
        "/api/v1/operations",
        headers=headers,
        json={
            "action": "send_console_command",
            "params": {"command": "say [验收] 面板控制台测试"},
        },
    )
    assert requested.status_code == 202
    confirmation_id = requested.json()["data"]["confirmation_id"]

    confirmed = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm",
        headers=headers,
        json={},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "succeeded"


def test_generic_tool_validation_error_is_structured_json(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/operations",
        headers=headers,
        json={
            "action": "send_console_command",
            "params": {"command": "say ok; reboot"},
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "validation_failed"


def test_extra_tool_parameters_are_rejected(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/operations",
        headers=headers,
        json={"action": "restart_server", "params": {"command": "anything"}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pvp", "true"),
        ("max-players", 0),
        ("view-distance", 33),
        ("difficulty", "nightmare"),
        ("gamemode", "builder"),
        ("motd", "line one\nline two"),
    ],
)
def test_invalid_property_value_is_rejected_before_confirmation(
    logged_in: tuple[TestClient, dict[str, str]], key: str, value: object
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/operations",
        headers=headers,
        json={"action": "edit_server_property", "params": {"key": key, "value": value}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_confirmation_is_claimed_atomically_across_concurrent_requests(
    logged_in: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = logged_in
    requested = client.post("/api/v1/server/restart", headers=headers)
    confirmation_id = requested.json()["data"]["confirmation_id"]
    service = client.app.state.operations
    adapter = client.app.state.adapter
    registry = client.app.state.registry
    fingerprint = adapter.fingerprint()
    barrier = Barrier(2)
    calls = 0
    calls_lock = Lock()
    fingerprint_calls = 0
    fingerprint_lock = Lock()
    original_execute = registry.execute

    def synchronized_fingerprint() -> str:
        nonlocal fingerprint_calls
        with fingerprint_lock:
            fingerprint_calls += 1
            should_wait = fingerprint_calls <= 2
        if should_wait:
            barrier.wait(timeout=3)
        return fingerprint

    def counted_execute(action: str, params: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        with calls_lock:
            calls += 1
        return original_execute(action, params)

    monkeypatch.setattr(adapter, "fingerprint", synchronized_fingerprint)
    monkeypatch.setattr(registry, "execute", counted_execute)

    def confirm() -> str:
        try:
            result = service.confirm(
                confirmation_id=confirmation_id,
                user_id=1,
                request_id="req_concurrent",
                second=False,
                server_name=None,
            )
            return str(result["status"])
        except OperationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: confirm(), range(2)))

    assert sorted(outcomes) == ["confirmation_invalid", "succeeded"]
    assert calls == 1


def test_helper_failure_details_are_preserved_and_redacted(
    logged_in: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = logged_in

    def fail(_action: str, _params: dict[str, object]) -> dict[str, object]:
        raise AdapterOperationError(
            "rollback_failed",
            "restore rollback failed",
            {
                "recovery_point": "/srv/minecraft/panel-recovery/restore-test",
                "stderr": "password=do-not-store-this",
            },
        )

    monkeypatch.setattr(client.app.state.adapter, "execute", fail)
    response = client.post("/api/v1/backups", headers=headers)

    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["error"] == "rollback_failed"
    assert details["details"]["recovery_point"].endswith("restore-test")
    assert "do-not-store-this" not in details["details"]["stderr"]


def test_unexpected_operation_errors_are_redacted_before_database_storage(
    logged_in: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = logged_in

    def fail(_action: str, _params: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("password=database-secret-do-not-store")

    monkeypatch.setattr(client.app.state.adapter, "execute", fail)
    response = client.post("/api/v1/backups", headers=headers)
    audit = client.app.state.database.fetch_one(
        "SELECT result_summary FROM audit_events WHERE action = ? ORDER BY created_at DESC LIMIT 1",
        ("create_backup",),
    )

    assert response.status_code == 409
    assert audit is not None
    assert "database-secret-do-not-store" not in str(audit["result_summary"])
    assert "[REDACTED]" in str(audit["result_summary"])


def test_backup_schedule_endpoint_returns_the_saved_schedule(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    requested = client.patch(
        "/api/v1/backup-schedule",
        headers=headers,
        json={"hour": 4, "minute": 15, "keep": 9},
    )
    confirmation_id = requested.json()["data"]["confirmation_id"]
    confirmed = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm", headers=headers, json={}
    )

    assert confirmed.status_code == 200
    assert client.get("/api/v1/backup-schedule").json()["data"] == {
        "hour": 4,
        "minute": 15,
        "keep": 9,
        "kind": "cold",
    }


def test_confirmation_rejects_changed_bound_parameters(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    requested = client.post("/api/v1/server/restart", headers=headers)
    confirmation_id = requested.json()["data"]["confirmation_id"]
    client.app.state.database.execute(
        "UPDATE confirmations SET params_json = ? WHERE id = ?",
        ('{"unexpected":true}', confirmation_id),
    )

    response = client.post(
        f"/api/v1/confirmations/{confirmation_id}/confirm", headers=headers, json={}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "confirmation_invalid"
