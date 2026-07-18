#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import os
import re
from pathlib import Path

IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "local-production",
    "node_modules",
    "release",
}
TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|password|token|secret)\s*=\s*(['\"])[^'\"\s]{16,}\1"
)
IPV4_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
PERSONAL_PATH_PATTERNS = (
    re.compile("/" + "Users/" + r"[^/\s]+/"),
    re.compile(r"/home/[A-Za-z0-9_.-]+/(?:minecraft|mc-server)/"),
)
HOST_MINECRAFT_PATH = "/opt/" + "minecraft/"


def _is_test_path(path: Path) -> bool:
    return "tests" in path.parts or ".test." in path.name


def _text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in IGNORED_DIRECTORIES)
        base = Path(directory)
        for filename in sorted(files):
            path = base / filename
            if path.is_symlink() or not path.is_file():
                continue
            content = _text(path)
            if content is None:
                continue
            relative = path.relative_to(root)
            test_path = _is_test_path(relative)
            for line_number, line in enumerate(content.splitlines(), start=1):
                allowed_fixture = "gitleaks:allow" in line
                if TOKEN_PATTERN.search(line) and not allowed_fixture:
                    findings.append((relative, line_number, "possible API token"))
                if not test_path and ASSIGNMENT_PATTERN.search(line):
                    findings.append((relative, line_number, "possible hard-coded credential"))
                if HOST_MINECRAFT_PATH in line or any(
                    pattern.search(line) for pattern in PERSONAL_PATH_PATTERNS
                ):
                    findings.append((relative, line_number, "host-specific absolute path"))
                for candidate in IPV4_PATTERN.findall(line):
                    try:
                        address = ipaddress.ip_address(candidate)
                    except ValueError:
                        continue
                    if address.is_global:
                        findings.append((relative, line_number, "public IPv4 address"))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Reject private data from a public source tree")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Public tree is missing: {root}")

    findings = scan(root)
    for path, line_number, reason in findings:
        print(f"{path}:{line_number}: {reason}")
    if findings:
        raise SystemExit("Public tree safety scan failed; suspected values were not printed.")
    print("Public tree safety scan passed.")


if __name__ == "__main__":
    main()
