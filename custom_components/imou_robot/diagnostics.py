"""Diagnostics for the Imou Robot Vacuum integration.

The download contains the product-level things model and the current values so
that other robot models can be mapped. App ID/Secret are redacted and neither
the device serial, name nor any token is included.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_APP_ID, CONF_APP_SECRET
from .coordinator import ImouConfigEntry

TO_REDACT = {CONF_APP_ID, CONF_APP_SECRET}


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
                "poll_refs": coordinator.poll_refs,
                "update_interval": coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None,
                "online": data.online if data is not None else None,
                "values": data.values if data is not None else None,
                "resolved_model": coordinator.model.as_diagnostics(),
                "things_model": coordinator.model.raw,
            }
        )
    return {"entry": async_redact_data(dict(entry.data), TO_REDACT), "robots": robots}
