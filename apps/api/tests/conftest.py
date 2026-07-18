from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mc_panel_api.auth import AuthService
from mc_panel_api.config import Settings
from mc_panel_api.database import Database
from mc_panel_api.main import create_app

PASSWORD = "Correct horse battery staple 2026"  # noqa: S105 - isolated test credential


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    fixture_root = Path(__file__).parents[3] / "tests" / "fixtures" / "mock-server"
    monkeypatch.setenv("MC_PANEL_ENV", "test")
    monkeypatch.setenv("MC_PANEL_SECRET_KEY", "test-secret-key-that-is-not-for-production")
    monkeypatch.setenv("MC_PANEL_DATABASE_PATH", str(tmp_path / "panel.db"))
    monkeypatch.setenv("MC_PANEL_MOCK_ROOT", str(fixture_root))
    monkeypatch.setenv("MC_PANEL_ADAPTER", "mock")
    monkeypatch.setenv("MC_PANEL_SERVER_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("MC_PANEL_SERVER_NAME", "星光好友服")
    monkeypatch.setenv("MC_PANEL_AI_ENABLED", "false")
    monkeypatch.setenv("MC_PANEL_PUBLIC_ORIGIN", "https://panel.example.test")
    return Settings.from_env()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    database = Database(settings.database_path)
    database.migrate()
    AuthService(database, settings).create_user("admin", PASSWORD)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client: TestClient) -> tuple[TestClient, dict[str, str]]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200
    csrf = response.json()["data"]["csrf_token"]
    return client, {"X-CSRF-Token": csrf}
