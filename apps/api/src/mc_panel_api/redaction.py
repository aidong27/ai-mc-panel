from __future__ import annotations

import ipaddress
import re
from typing import Any

PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|cookie|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
IPV6_CANDIDATE = re.compile(r"(?<![A-Fa-f0-9:])[A-Fa-f0-9:]{2,39}(?![A-Fa-f0-9:])")


def _redact_ipv6(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return "[REDACTED]"


def redact_text(value: str, limit: int = 24_000) -> str:
    text = value[:limit]
    for pattern in PATTERNS:
        if pattern.groups:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return IPV6_CANDIDATE.sub(_redact_ipv6, text)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value[:500]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:200]:
            if key.lower() in {"api_key", "password", "cookie", "authorization", "token"}:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    return value
