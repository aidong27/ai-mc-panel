from __future__ import annotations

from pathlib import Path

from mc_panel_api.detection import detect_server_identity


def test_detects_forge_identity_from_library_layout(tmp_path: Path) -> None:
    forge = tmp_path / "libraries/net/minecraftforge/forge/1.20.1-47.4.0"
    forge.mkdir(parents=True)

    identity = detect_server_identity(server_root=tmp_path, pid=0, java_version_hint="17")

    assert identity.minecraft_version == "1.20.1"
    assert identity.loader == "Forge"
    assert identity.loader_version == "47.4.0"
    assert identity.java_version == "17"
    assert identity.sources["loader"] == "filesystem"


def test_latest_log_identity_takes_precedence_without_exposing_log_content(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "latest.log").write_text(
        "[main/INFO] Loading Minecraft 1.21.1 with Fabric Loader 0.16.9\n"
        "Player [/203.0.113.4:25565] logged in\n",
        encoding="utf-8",
    )

    identity = detect_server_identity(server_root=tmp_path, pid=0)

    assert identity.minecraft_version == "1.21.1"
    assert identity.loader == "Fabric"
    assert identity.loader_version == "0.16.9"
    assert "203.0.113.4" not in repr(identity)


def test_explicit_identity_hints_are_reported_as_configuration(tmp_path: Path) -> None:
    identity = detect_server_identity(
        server_root=tmp_path,
        pid=0,
        minecraft_version_hint="1.20.6",
        loader_hint="NeoForge",
        loader_version_hint="20.6.120",
        java_version_hint="21",
    )

    assert identity.minecraft_version == "1.20.6"
    assert identity.loader == "NeoForge"
    assert identity.loader_version == "20.6.120"
    assert identity.java_version == "21"
    assert set(identity.sources.values()) == {"configuration"}
