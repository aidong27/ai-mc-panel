from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

MAX_IDENTITY_LOG_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ServerIdentity:
    minecraft_version: str
    loader: str
    loader_version: str
    java_version: str
    sources: dict[str, str]


def _safe_tail(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - MAX_IDENTITY_LOG_BYTES))
            return handle.read(MAX_IDENTITY_LOG_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _last_match(pattern: str, text: str, *, flags: int = 0) -> tuple[str, ...] | None:
    matches = re.findall(pattern, text, flags)
    if not matches:
        return None
    match = matches[-1]
    return (match,) if isinstance(match, str) else tuple(match)


def _directory_names(path: Path) -> list[str]:
    try:
        return sorted(
            (
                child.name
                for child in path.iterdir()
                if child.is_dir() and not child.is_symlink() and child.name.isprintable()
            ),
            reverse=True,
        )
    except OSError:
        return []


def _filesystem_identity(server_root: Path) -> tuple[str, str, str, dict[str, str]]:
    forge = _directory_names(server_root / "libraries/net/minecraftforge/forge")
    if forge:
        minecraft, separator, loader_version = forge[0].partition("-")
        return (
            minecraft if separator else "unknown",
            "Forge",
            loader_version if separator else forge[0],
            {"loader": "filesystem", "minecraft_version": "filesystem"},
        )

    neoforge = _directory_names(server_root / "libraries/net/neoforged/neoforge")
    if neoforge:
        return "unknown", "NeoForge", neoforge[0], {"loader": "filesystem"}

    try:
        files = {
            path.name for path in server_root.iterdir() if path.is_file() and not path.is_symlink()
        }
    except OSError:
        files = set()
    if "fabric-server-launch.jar" in files:
        return "unknown", "Fabric", "unknown", {"loader": "filesystem"}
    if "quilt-server-launch.jar" in files:
        return "unknown", "Quilt", "unknown", {"loader": "filesystem"}

    minecraft_servers = _directory_names(server_root / "libraries/net/minecraft/server")
    if minecraft_servers:
        return (
            minecraft_servers[0],
            "Vanilla",
            "n/a",
            {
                "loader": "filesystem",
                "minecraft_version": "filesystem",
            },
        )
    return "unknown", "Unknown", "unknown", {}


def _log_identity(text: str) -> tuple[str, str, str, dict[str, str]] | None:
    fabric = _last_match(
        r"Loading Minecraft ([0-9A-Za-z.+_-]+) with Fabric Loader ([0-9A-Za-z.+_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if fabric:
        return (
            fabric[0],
            "Fabric",
            fabric[1],
            {
                "minecraft_version": "latest.log",
                "loader": "latest.log",
            },
        )

    quilt = _last_match(
        r"Loading Minecraft ([0-9A-Za-z.+_-]+) with Quilt Loader ([0-9A-Za-z.+_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if quilt:
        return (
            quilt[0],
            "Quilt",
            quilt[1],
            {
                "minecraft_version": "latest.log",
                "loader": "latest.log",
            },
        )

    forge = _last_match(
        r"Forge Mod Loader version ([0-9A-Za-z.+_-]+) for Minecraft ([0-9A-Za-z.+_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if forge:
        return (
            forge[1],
            "Forge",
            forge[0],
            {
                "minecraft_version": "latest.log",
                "loader": "latest.log",
            },
        )

    neoforge = _last_match(
        r"NeoForge(?: Mod Loader)?(?: version)?[ /]([0-9A-Za-z.+_-]+).*?"
        r"Minecraft[ /]([0-9A-Za-z.+_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if neoforge:
        return (
            neoforge[1],
            "NeoForge",
            neoforge[0],
            {
                "minecraft_version": "latest.log",
                "loader": "latest.log",
            },
        )

    minecraft = _last_match(
        r"(?:Starting minecraft server version|Minecraft Version:)\s*([0-9A-Za-z.+_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if minecraft:
        return (
            minecraft[0],
            "Vanilla",
            "n/a",
            {
                "minecraft_version": "latest.log",
                "loader": "latest.log",
            },
        )
    return None


def _java_major(pid: int) -> str:
    if pid <= 0 or not psutil.pid_exists(pid):
        return "unknown"
    try:
        executable = Path(psutil.Process(pid).exe())
        if not executable.is_absolute() or not executable.name.lower().startswith("java"):
            return "unknown"
        result = subprocess.run(  # noqa: S603
            [str(executable), "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, psutil.Error, subprocess.TimeoutExpired):
        return "unknown"
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(r'(?:version\s+")?(\d+)(?:[._][0-9]+)*', output)
    return match.group(1) if match else "unknown"


def detect_server_identity(
    *,
    server_root: Path,
    pid: int,
    minecraft_version_hint: str = "",
    loader_hint: str = "",
    loader_version_hint: str = "",
    java_version_hint: str = "",
) -> ServerIdentity:
    filesystem = _filesystem_identity(server_root)
    from_log = _log_identity(_safe_tail(server_root / "logs/latest.log"))
    minecraft, loader, loader_version, sources = from_log or filesystem

    if minecraft_version_hint:
        minecraft = minecraft_version_hint
        sources["minecraft_version"] = "configuration"
    if loader_hint:
        loader = loader_hint
        sources["loader"] = "configuration"
    if loader_version_hint:
        loader_version = loader_version_hint
        sources["loader_version"] = "configuration"
    elif loader_version != "unknown":
        sources.setdefault("loader_version", sources.get("loader", "filesystem"))

    java_version = java_version_hint or _java_major(pid)
    sources["java_version"] = (
        "configuration"
        if java_version_hint
        else ("process" if java_version != "unknown" else "unavailable")
    )
    return ServerIdentity(
        minecraft_version=minecraft,
        loader=loader,
        loader_version=loader_version,
        java_version=java_version,
        sources=sources,
    )
