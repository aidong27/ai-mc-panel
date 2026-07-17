from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_readonly_installer_waits_for_health_and_exits_after_rollback() -> None:
    script = (ROOT / "deploy/scripts/install-readonly.sh").read_text(encoding="utf-8")

    assert "for _ in {1..30}" in script
    assert "Panel health check did not become ready within 30 seconds." in script
    assert "local failure_status=$?" in script
    assert 'exit "$failure_status"' in script


def test_systemd_documentation_uses_a_file_url() -> None:
    unit = (ROOT / "deploy/systemd/mc-panel.service").read_text(encoding="utf-8")

    assert "Documentation=file:/opt/mc-panel/current/DEPLOYMENT.md" in unit
    assert "/opt/" + "minecraft/" not in unit
    assert "runtime-paths.conf" in unit


def test_production_change_scripts_wait_for_panel_health() -> None:
    for relative in ("deploy/scripts/enable-writes.sh", "deploy/scripts/update.sh"):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert "for _ in {1..30}" in script
        assert "Panel health check did not become ready within 30 seconds." in script


def test_maintenance_scripts_use_configured_port_and_minecraft_service() -> None:
    for relative in (
        "deploy/scripts/configure-ai.sh",
        "deploy/scripts/enable-writes.sh",
        "deploy/scripts/update.sh",
        "deploy/scripts/rollback.sh",
        "deploy/scripts/verify.sh",
    ):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert "PANEL_PORT=$(" in script
        assert "127.0.0.1:$PANEL_PORT" in script

    preflight = (ROOT / "deploy/scripts/preflight-readonly.sh").read_text(encoding="utf-8")
    assert "PANEL_PORT=${MC_PANEL_PORT:-18080}" in preflight
    assert "listener_panel" in preflight
    assert "listener_18080" not in preflight

    uninstall = (ROOT / "deploy/scripts/uninstall-preserve-data.sh").read_text(encoding="utf-8")
    assert 'json.load(open(sys.argv[1], encoding="utf-8"))["server_service"]' in uninstall
    assert 'systemctl is-active --quiet "$SERVER_SERVICE"' in uninstall
    assert "systemctl is-active --quiet minecraft.service" not in uninstall


def test_account_scripts_load_quoted_root_owned_environment() -> None:
    for relative in (
        "deploy/scripts/create-admin.sh",
        "deploy/scripts/reset-password.sh",
        "deploy/scripts/fingerprint.sh",
        "deploy/scripts/enable-writes.sh",
        "deploy/scripts/update.sh",
        "deploy/scripts/rollback.sh",
        "deploy/scripts/disable-writes.sh",
    ):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert "root:mc-panel:640" in script
    for relative in ("deploy/scripts/create-admin.sh", "deploy/scripts/reset-password.sh"):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert 'source "$ENV_FILE"' in script
        assert "extract_env" not in script


def test_root_helper_profile_is_always_required_to_be_mode_0600() -> None:
    for relative in (
        "deploy/scripts/enable-writes.sh",
        "deploy/scripts/update.sh",
        "deploy/scripts/rollback.sh",
        "deploy/scripts/uninstall-preserve-data.sh",
    ):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert "root:root:600" in script


def test_runtime_profile_renderer_is_private_and_refuses_overwrite(tmp_path: Path) -> None:
    panel_env = tmp_path / "panel.env"
    helper_config = tmp_path / "helper.json"
    script = ROOT / "deploy/scripts/render-runtime-profile.py"
    environment = {
        "PATH": os.environ["PATH"],
        "MC_PANEL_ENV": "production",
        "MC_PANEL_SERVER_ROOT": "/srv/minecraft/example",
        "MC_PANEL_SERVER_STARTUP_PATH": "/srv/minecraft/example/start.sh",
        "MC_PANEL_BACKUP_ROOT": "/srv/minecraft/backups",
        "MC_PANEL_BACKUP_COMMAND": "/usr/local/bin/minecraft-backup",
        "MC_PANEL_RECOVERY_ROOT": "/srv/minecraft/recovery",
    }

    result = subprocess.run(  # noqa: S603 - fixed interpreter and local test script
        [sys.executable, str(script), str(panel_env), str(helper_config)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert panel_env.stat().st_mode & 0o777 == 0o600
    assert helper_config.stat().st_mode & 0o777 == 0o600
    panel_content = panel_env.read_text(encoding="utf-8")
    secret_line = next(
        line for line in panel_content.splitlines() if line.startswith("MC_PANEL_SECRET_KEY=")
    )
    generated_secret = secret_line.partition("=")[2].strip('"')
    assert len(generated_secret) >= 48
    assert generated_secret not in result.stdout
    helper = json.loads(helper_config.read_text(encoding="utf-8"))
    assert helper["server_root"] == "/srv/minecraft/example"
    assert helper["server_service"] == "minecraft.service"

    before = panel_env.read_bytes()
    refused = subprocess.run(  # noqa: S603 - fixed interpreter and local test script
        [sys.executable, str(script), str(panel_env), str(helper_config)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert refused.returncode != 0
    assert "Refusing to overwrite" in refused.stderr
    assert panel_env.read_bytes() == before


def test_release_builder_excludes_private_runtime_artifacts_and_scans_stage() -> None:
    script = (ROOT / "deploy/scripts/build-release.sh").read_text(encoding="utf-8")
    scanner = (ROOT / "deploy/scripts/check-public-tree.py").read_text(encoding="utf-8")

    for excluded in (
        "--exclude '.env*'",
        "--exclude 'local-production/'",
        "--exclude 'AUDIT-*.md'",
        "--exclude 'PRODUCTION_CHANGE_PLAN.md'",
        "--exclude 'PRODUCT-REVIEW-*.md'",
        "--exclude '*.command'",
    ):
        assert excluded in script
    assert 'check-public-tree.py" "$STAGE"' in script
    assert "possible API token" in scanner
    assert "possible hard-coded credential" in scanner
    assert "host-specific absolute path" in scanner
    assert "public IPv4 address" in scanner


def test_public_tree_scanner_accepts_documentation_fixtures(tmp_path: Path) -> None:
    fixture = tmp_path / "safe.txt"
    fixture.write_text(
        "server=/srv/minecraft/example\n"
        "host=203.0.113.9\n"
        "sk-example-secret-123456789 # gitleaks:allow\n",
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603 - fixed interpreter and local scanner
        [sys.executable, str(ROOT / "deploy/scripts/check-public-tree.py"), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Public tree safety scan passed." in result.stdout


def test_public_tree_scanner_rejects_without_echoing_suspected_value(tmp_path: Path) -> None:
    synthetic_token = "sk-public-scan-" + "1234567890abcdef"
    fixture = tmp_path / "unsafe.txt"
    fixture.write_text(
        f"api_key={synthetic_token}\n"
        "resolver=8." + "8.8.8\n"
        "root=/" + "Users/example/minecraft/server\n",
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603 - fixed interpreter and local scanner
        [sys.executable, str(ROOT / "deploy/scripts/check-public-tree.py"), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "possible API token" in result.stdout
    assert "public IPv4 address" in result.stdout
    assert "host-specific absolute path" in result.stdout
    assert synthetic_token not in result.stdout
    assert synthetic_token not in result.stderr


def test_all_deployment_shell_scripts_are_executable() -> None:
    scripts = sorted((ROOT / "deploy").rglob("*.sh"))
    assert scripts
    for script in scripts:
        assert os.access(script, os.X_OK), f"deployment script is not executable: {script}"


def test_all_deployment_shell_scripts_parse() -> None:
    scripts = sorted((ROOT / "deploy").rglob("*.sh"))
    assert scripts
    for script in scripts:
        subprocess.run(  # noqa: S603 - fixed shell and repository-owned scripts
            ["/bin/bash", "-n", str(script)], check=True
        )


def test_certificate_install_validates_key_pair_and_rolls_back_on_reload_failure() -> None:
    script = (ROOT / "deploy/caddy/install-certificate.sh").read_text(encoding="utf-8")

    assert "openssl x509" in script
    assert "openssl pkey" in script
    assert "restore_previous" in script
    assert "rollback_needed=true" in script
