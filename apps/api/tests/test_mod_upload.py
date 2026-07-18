from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mc_panel_api.adapters.systemd import SystemdMinecraftAdapter
from mc_panel_api.config import Settings

UPLOAD_ID = "upload_0123456789abcdef"


def _jar() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("META-INF/mods.toml", 'modLoader="javafml"\nloaderVersion="[47,)"')
        archive.writestr("example.txt", "fixture")
    return stream.getvalue()


def _stored_upload(settings: Settings) -> tuple[Path, Path, dict[str, object]]:
    quarantine = settings.database_path.parent / "quarantine"
    quarantine.mkdir(mode=0o700)
    quarantine.chmod(0o700)
    content = _jar()
    jar = quarantine / f"{UPLOAD_ID}.jar"
    jar.write_bytes(content)
    jar.chmod(0o600)
    metadata: dict[str, object] = {
        "filename": "safe-example.jar",
        "sha256": "a" * 64,
        "size_bytes": len(content),
        "metadata": "META-INF/mods.toml",
    }
    sidecar = quarantine / f"{UPLOAD_ID}.json"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    sidecar.chmod(0o600)
    return jar, sidecar, metadata


def test_systemd_adapter_reads_exact_quarantined_mod_metadata(settings: Settings) -> None:
    _, _, metadata = _stored_upload(settings)

    result = SystemdMinecraftAdapter(settings).get_mod_upload(UPLOAD_ID)

    assert result == {
        "id": UPLOAD_ID,
        "filename": metadata["filename"],
        "size_bytes": metadata["size_bytes"],
        "metadata": metadata["metadata"],
    }


def test_systemd_adapter_rejects_symlinked_upload_metadata(settings: Settings) -> None:
    _, sidecar, metadata = _stored_upload(settings)
    external = settings.database_path.parent / "external-upload.json"
    external.write_text(json.dumps(metadata), encoding="utf-8")
    sidecar.unlink()
    sidecar.symlink_to(external)

    with pytest.raises(KeyError, match="mod upload not found"):
        SystemdMinecraftAdapter(settings).get_mod_upload(UPLOAD_ID)


def test_systemd_adapter_rejects_duplicate_upload_metadata_keys(settings: Settings) -> None:
    _, sidecar, metadata = _stored_upload(settings)
    sidecar.write_text(
        "{" + f'"filename":"first.jar","filename":"second.jar",'
        f'"sha256":"{metadata["sha256"]}",'
        f'"size_bytes":{metadata["size_bytes"]},'
        '"metadata":"META-INF/mods.toml"}',
        encoding="utf-8",
    )

    with pytest.raises(KeyError, match="mod upload not found"):
        SystemdMinecraftAdapter(settings).get_mod_upload(UPLOAD_ID)


def test_mod_upload_goes_to_quarantine(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": ("safe-example.jar", _jar(), "application/java-archive")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["upload_id"].startswith("upload_")
    assert data["state"] == "quarantined"
    assert data["metadata"] == "META-INF/mods.toml"


def test_uppercase_jar_extension_remains_installable(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": ("safe-example.JAR", _jar(), "application/java-archive")},
    )

    assert response.status_code == 201
    upload_id = response.json()["data"]["upload_id"]
    requested = client.post(
        "/api/v1/operations",
        headers=headers,
        json={"action": "install_mod", "params": {"upload_id": upload_id}},
    )
    assert requested.status_code == 202


@pytest.mark.parametrize("filename", [".hidden.jar", "模" * 61 + ".jar"])
def test_upload_rejects_names_the_privileged_installer_cannot_accept(
    logged_in: tuple[TestClient, dict[str, str]], filename: str
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": (filename, _jar(), "application/java-archive")},
    )

    assert response.status_code == 422


def test_non_jar_upload_is_rejected(logged_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": ("not-a-mod.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 422


def test_windows_style_traversal_filename_is_rejected(
    logged_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = logged_in
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": ("..\\escaped.jar", _jar(), "application/java-archive")},
    )
    assert response.status_code == 422


def test_declared_zip_bomb_is_rejected_before_integrity_decompression(
    logged_in: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = logged_in
    payload = _jar()
    oversized = zipfile.ZipInfo("META-INF/mods.toml")
    oversized.file_size = 513 * 1024 * 1024
    testzip_called = False

    def forbidden_testzip(_archive: zipfile.ZipFile) -> str | None:
        nonlocal testzip_called
        testzip_called = True
        return None

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lambda _archive: [oversized])
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden_testzip)
    response = client.post(
        "/api/v1/mods/uploads",
        headers=headers,
        files={"file": ("oversized.jar", payload, "application/java-archive")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "JAR 解压后体积异常"
    assert testzip_called is False
