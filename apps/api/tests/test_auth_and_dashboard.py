from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mc_panel_api.config import Settings
from mc_panel_api.main import create_app

CURRENT_PASSWORD = "Correct horse battery staple 2026"  # noqa: S105
NEW_PASSWORD = "New correct horse battery staple 2026"  # noqa: S105


def test_dashboard_requires_login(client: TestClient) -> None:
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_built_frontend_is_served_from_the_configured_path(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_missing_frontend_returns_explicit_not_built_status(
    settings: Settings, tmp_path: Path
) -> None:
    app = create_app(replace(settings, web_dist_path=tmp_path / "missing-web-dist"))

    with TestClient(app) as test_client:
        response = test_client.get("/")

    assert response.status_code == 200
    assert response.json()["data"] == {"name": "方块管家", "frontend": "not_built"}


def test_unknown_api_route_never_falls_back_to_the_spa(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "api_not_found"


def test_login_dashboard_and_logout(logged_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = logged_in
    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    data = dashboard.json()["data"]
    assert data["server"]["state"] == "running"
    assert data["players"] == {"online": 1, "maximum": 10, "players": ["Alex"]}
    assert data["metrics"]["tps"] == 20.0
    assert data["ai"]["enabled"] is False
    assert data["diagnostics"]["level"] == "good"
    assert len(data["diagnostics"]["checks"]) == 4

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert client.get("/api/v1/dashboard").status_code == 401


def test_player_activity_endpoint_is_authenticated_and_bounded(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, _ = logged_in

    response = client.get("/api/v1/server/player-activity?days=7")
    invalid = client.get("/api/v1/server/player-activity?days=31")

    assert response.status_code == 200
    assert response.json()["data"]["days"] == 7
    assert len(response.json()["data"]["daily"]) == 7
    assert response.json()["data"]["source"] == "persisted_minecraft_login_history"
    assert response.json()["data"]["history"]["persisted"] is True
    assert invalid.status_code == 422


def test_backup_runtime_status_endpoint_is_authenticated(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, _ = logged_in

    response = client.get("/api/v1/backup-status")

    assert response.status_code == 200
    assert response.json()["data"]["last_result"] == "success"
    assert response.json()["data"]["timer_active"] is True


def test_password_change_rotates_session_and_revokes_old_credentials(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    old_token = client.cookies.get("mc_panel_session")
    assert old_token

    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["changed"] is True
    assert data["sessions_revoked"] is True
    assert client.cookies.get("mc_panel_session") != old_token
    assert client.app.state.auth.resolve_session(old_token) is None
    assert client.app.state.auth.authenticate("admin", NEW_PASSWORD) is not None
    assert client.app.state.auth.authenticate("admin", CURRENT_PASSWORD) is None


def test_password_change_rejects_wrong_current_password(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": "wrong-current-password",
            "new_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "current_password_invalid"


def test_state_change_rejects_missing_csrf(logged_in: tuple[TestClient, dict[str, str]]) -> None:
    client, _ = logged_in
    response = client.post("/api/v1/server/restart")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_cross_origin_write_is_rejected(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/server/restart",
        headers={**headers, "Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_failed"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_console_websocket_closes_cleanly(
    logged_in: tuple[TestClient, dict[str, str]],
    settings: Settings,
) -> None:
    client, headers = logged_in
    ticket_response = client.post("/api/v1/auth/ws-ticket", headers=headers)
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["data"]["ticket"]
    with client.websocket_connect(
        f"/api/v1/ws/console?ticket={ticket}",
        headers={"Origin": settings.panel_public_origin},
    ) as websocket:
        payload = websocket.receive_json()
        assert payload["type"] == "logs"
        assert isinstance(payload["lines"], list)


def test_console_websocket_rejects_untrusted_public_origin(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    ticket_response = client.post("/api/v1/auth/ws-ticket", headers=headers)
    ticket = ticket_response.json()["data"]["ticket"]

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            f"/api/v1/ws/console?ticket={ticket}",
            headers={"Origin": "https://untrusted.example"},
        ),
    ):
        pass

    assert exc_info.value.code == 4403


def test_ai_disabled_does_not_break_traditional_panel(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    missing_csrf = client.post("/api/v1/ai/chat", json={"message": "服务器正常吗？"})
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"

    ai = client.post("/api/v1/ai/chat", headers=headers, json={"message": "服务器正常吗？"})
    assert ai.status_code == 503
    assert ai.json()["error"]["code"] == "ai_disabled"
    assert client.get("/api/v1/server/status").status_code == 200


def test_ai_provider_target_is_locked_to_the_server_environment(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.patch(
        "/api/v1/ai/settings",
        headers=headers,
        json={"api_base_url": "https://untrusted.example/v1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_configuration_locked"
