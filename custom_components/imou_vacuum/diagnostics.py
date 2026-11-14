"""Diagnostics for the Imou Vacuum (Open Platform) integration.

The download contains the product-level things model and the current values so
that other robot models can be mapped. App ID/Secret are redacted and neither
the device serial, name nor any token is included.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ImouError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    PROBE_IDENTIFIERS,
    PROP_MAP_SWITCH,
    SERVICE_CLEAN_LIST,
)
from .coordinator import ImouConfigEntry, ImouCoordinator
from .probe import find_urls, redact_url, summarize, summarize_members
from .support import support_report

TO_REDACT = {CONF_APP_ID, CONF_APP_SECRET}

MAX_PROBE_URLS = 4
URL_TIMEOUT = 10  # seconds
URL_READ_BYTES = 64
URL_HEX_BYTES = 16


def _error(err: Exception) -> dict[str, str]:
    """Describe a failed probe part; only a code or a type, never a body."""
    return {"error": err.code if isinstance(err, ImouError) else type(err).__name__}


async def _guard(call: Awaitable[Any]) -> Any:
    """Await one probe part; a failure becomes ``{"error": ...}`` for that part."""
    try:
        return await call
    except Exception as err:  # noqa: BLE001 - the probe must never fail diagnostics
        return _error(err)


def _summarize_property(identifier: str, value: Any) -> Any:
    if value is None:
        return None
    if identifier == PROP_MAP_SWITCH and isinstance(value, dict):
        return summarize_members(value)
    return summarize(value)


def _is_private_host(host: str) -> bool:
    """Return whether a host is localhost or a private/loopback/link-local IP literal.

    Only IP literals are looked at; there is no DNS lookup.
    """
    host = host.strip().rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global


async def _async_check_url(hass: HomeAssistant, url: str) -> dict[str, Any]:
    """Fetch the first bytes of a URL; the result never contains the URL itself.

    Only https URLs on public hosts are fetched, and redirects are not followed.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "https":
        return {"url": redact_url(url), "skipped": "not_https"}
    if _is_private_host(parts.hostname or ""):
        return {"url": redact_url(url), "skipped": "private_host"}
    try:
        async with asyncio.timeout(URL_TIMEOUT):
            session = async_get_clientsession(hass)
            async with session.get(
                url, headers={"Range": "bytes=0-63"}, allow_redirects=False
            ) as response:
                head = await response.content.read(URL_READ_BYTES)
                return {
                    "url": redact_url(url),
                    "status": response.status,
                    "content_type": response.headers.get("Content-Type"),
                    "content_length": response.headers.get("Content-Length"),
                    "first_bytes_hex": head[:URL_HEX_BYTES].hex(),
                }
    except Exception as err:  # noqa: BLE001 - the probe must never fail diagnostics
        return {"url": redact_url(url), "error": type(err).__name__}


async def _async_probe(hass: HomeAssistant, coordinator: ImouCoordinator) -> dict[str, Any]:
    """Read-only probe of the rooms, map and schedule data (see probe.py)."""
    probe: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    try:
        raw = await coordinator.async_read_identifiers(PROBE_IDENTIFIERS)
    except Exception as err:  # noqa: BLE001 - the probe must never fail diagnostics
        probe["properties"] = _error(err)
    else:
        probe["properties"] = {
            identifier: _summarize_property(identifier, raw.get(identifier))
            for identifier in PROBE_IDENTIFIERS
            if coordinator.model.prop(identifier) is not None
        }
    # the map and area URLs first: they are the ones that matter most
    ordered = [raw.get(PROP_MAP_SWITCH)] + [
        raw.get(i) for i in PROBE_IDENTIFIERS if i != PROP_MAP_SWITCH
    ]
    urls = find_urls(ordered, MAX_PROBE_URLS)
    probe["url_checks"] = [await _async_check_url(hass, url) for url in urls]
    if coordinator.model.has_service(SERVICE_CLEAN_LIST):
        response = await _guard(coordinator.async_query_service(SERVICE_CLEAN_LIST))
        probe["services"] = {SERVICE_CLEAN_LIST: summarize(response)}
    return probe


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ImouConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    robots: list[dict[str, Any]] = []
    for coordinator in entry.runtime_data.coordinators.values():
        data = coordinator.data
        device = coordinator.device
        robots.append(
            {
                "device": {
                    "model": device.model,
                    "product_id": device.product_id,
                    "brand": device.brand,
                    "version": device.version,
                    "status": device.status,
                },
                "active_map": coordinator.active_map(),
                "multi_map": coordinator.is_multi_map(),
                "poll_refs": coordinator.poll_refs,
                "reports": coordinator.reports.as_diagnostics(),
                "failed_polls_in_a_row": coordinator.health.failures,
                "update_interval": coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None,
                "online": data.online if data is not None else None,
                "values": data.values if data is not None else None,
                "support_report": support_report(coordinator.model, device.model),
                "resolved_model": coordinator.model.as_diagnostics(),
                "things_model": coordinator.model.raw,
                "probe": await _guard(_async_probe(hass, coordinator)),
                "last_command": coordinator.last_command,
            }
        )
    return {"entry": async_redact_data(dict(entry.data), TO_REDACT), "robots": robots}
