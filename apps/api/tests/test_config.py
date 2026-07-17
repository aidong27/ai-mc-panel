from pathlib import Path

import pytest

from mc_panel_api.config import Settings


def _production_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MC_PANEL_ENV", "production")
    monkeypatch.setenv("MC_PANEL_SECRET_KEY", "test-secret-key-that-is-not-for-production")
    monkeypatch.setenv("MC_PANEL_DATABASE_PATH", str(tmp_path / "panel.db"))
    monkeypatch.setenv("MC_PANEL_ADAPTER", "systemd")


def test_production_public_origin_requires_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MC_PANEL_PUBLIC_ORIGIN", "http://panel.example.test")

    with pytest.raises(ValueError, match="must use HTTPS"):
        Settings.from_env()


def test_public_origin_rejects_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MC_PANEL_PUBLIC_ORIGIN", "https://panel.example.test/admin")

    with pytest.raises(ValueError, match="without credentials or a path"):
        Settings.from_env()


def test_production_runtime_paths_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MC_PANEL_SERVER_ROOT", "relative/server")

    with pytest.raises(ValueError, match="must be absolute"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MC_PANEL_DATABASE_PATH", "relative/panel.db", "must be absolute"),
        ("MC_PANEL_WEB_DIST_PATH", "relative/dist", "must be absolute"),
        ("MC_PANEL_HELPER_PATH", "relative/helper", "must be absolute"),
    ],
)
def test_all_production_process_paths_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_unknown_environment_cannot_fall_back_to_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MC_PANEL_ENV", "prodution")

    with pytest.raises(ValueError, match="development, test, or production"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MC_PANEL_BIND", "0.0.0.0", "loopback-only"),  # noqa: S104 - rejection fixture
        ("MC_PANEL_SESSION_SECURE", "false", "must be true"),
        ("MC_PANEL_ADAPTER", "mock", "must be systemd"),
        ("MC_PANEL_APPROVED_FINGERPRINT", "not-a-sha256", "SHA-256"),
    ],
)
def test_production_rejects_security_downgrades(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MC_PANEL_SERVER_SERVICE", "minecraft;reboot.service", "valid systemd"),
        ("MC_PANEL_BACKUP_ARCHIVE_GLOB", "../*.tar.gz", "filename-only glob"),
        ("MC_PANEL_SERVER_TIMEZONE", "Not/A-Timezone", "IANA timezone"),
    ],
)
def test_runtime_profile_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()
