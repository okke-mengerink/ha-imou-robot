"""Redaction and summaries for the read-only diagnostics probe.

The probe reads values whose format is not documented (rooms, map, schedules).
Diagnostics files are shared publicly, so the output keeps the STRUCTURE of the
data (keys, ids, numbers, coordinates, enums) but hides personal data: names,
account details, tokens and the full path and query of URLs.

This module is deliberately free of Home Assistant imports so that it can be
unit tested with plain Python.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Final
from urllib.parse import urlsplit

REDACTED: Final = "**REDACTED**"
PREVIEW_LENGTH: Final = 120

# Key names (lower-case, substring match) whose value is hidden. A bare "ip" or
# "key" is left out on purpose: it would hide harmless keys like "description".
_SENSITIVE_PARTS: Final = (
    "name", "nick", "user", "account", "phone", "mail", "token", "sign",
    "secret", "password", "serial", "deviceid", "accesskey", "secretkey",
    "apikey", "auth", "address",
)  # fmt: skip
# Key names (lower-case) that are hidden only when they match exactly.
_SENSITIVE_EXACT: Final = frozenset({"sn", "ip", "mac", "ssid", "bssid"})
_URL_RE: Final = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)


def is_sensitive_key(key: Any) -> bool:
    """Return whether the value under this key must be hidden."""
    name = str(key).lower()
    return name in _SENSITIVE_EXACT or any(part in name for part in _SENSITIVE_PARTS)


def _is_url(text: str) -> bool:
    return _URL_RE.fullmatch(text.strip()) is not None


def redact_url(url: str) -> dict[str, Any]:
    """Describe a URL without its query string or full path."""
    parts = urlsplit(url.strip())
    segments = [segment for segment in parts.path.split("/") if segment]
    last = segments[-1] if segments else ""
    if last:
        # a hash, so that no serial or id in the file name leaks
        digest = hashlib.sha256(last.encode()).hexdigest()[:8]
        last = digest + PurePosixPath(last).suffix
    shown = f"{parts.scheme}://{parts.hostname or ''}/" + (f"…/{last}" if last else "")
    return {
        "url": shown,
        "has_query": bool(parts.query),
        "path_depth": len(segments),
    }


def _parse_json_container(text: str) -> Any | None:
    """Return the parsed value if ``text`` is a JSON object or array."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _redact_urls_in_text(text: str) -> str:
    return _URL_RE.sub(lambda match: redact_url(match.group(0))["url"], text)


def _redact_str(text: str) -> Any:
    if _is_url(text):
        return redact_url(text)
    if (parsed := _parse_json_container(text)) is not None:
        return {"type": "json", "value": redact(parsed)}
    return _redact_urls_in_text(text)


def redact(value: Any) -> Any:
    """Return a recursively redacted copy of ``value`` (structure is kept)."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return {"type": type(value).__name__}


def summarize(value: Any) -> Any:
    """Return a JSON-safe summary of a raw value that hides personal data."""
    if isinstance(value, str):
        if (parsed := _parse_json_container(value)) is not None:
            return {"type": "json", "value": redact(parsed)}
        return {
            "type": "text",
            "length": len(value),
            "preview": _redact_urls_in_text(value)[:PREVIEW_LENGTH],
        }
    return redact(value)


def summarize_members(members: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize each member of a struct value; sensitive members are hidden."""
    return {
        str(key): REDACTED if is_sensitive_key(key) else summarize(item)
        for key, item in members.items()
    }


def find_urls(values: Iterable[Any], limit: int) -> list[str]:
    """Return up to ``limit`` distinct http(s) URLs found in raw values, in order."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            if (parsed := _parse_json_container(value)) is not None:
                walk(parsed)
            else:
                for match in _URL_RE.findall(value):
                    if match not in found and len(found) < limit:
                        found.append(match)

    for value in values:
        walk(value)
    return found
