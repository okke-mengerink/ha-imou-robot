"""The Imou Robot Vacuum integration.

Controls Imou robot vacuums through the Imou Open Platform cloud API. Property
and service refs are resolved at runtime, by identifier, from each device's
things model.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ImouApiError,
    ImouAuthError,
    ImouClient,
    ImouConnectionError,
    ImouDevice,
    ImouError,
)
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
    REGION_HOSTS,
    SERVICE_CLEAN_CONTROL,
)
from .coordinator import ImouConfigEntry, ImouCoordinator, ImouRuntimeData
from .model import ThingsModel

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]


async def _async_discover_robots(
    client: ImouClient,
) -> tuple[list[tuple[ImouDevice, ThingsModel]], list[str]]:
    """Return the robot vacuums with their things model, and model-lookup errors.

    A device is a robot when it has a productId (an IoT device) and its things
    model has the service ``clean_control``. A product whose model cannot be
    fetched is skipped (its error is returned) so that it cannot block the others.
    """
    models: dict[str, ThingsModel | None] = {}
    robots: list[tuple[ImouDevice, ThingsModel]] = []
    errors: list[str] = []
    for device in await client.async_list_devices():
        if not device.product_id:
            continue
        if device.product_id not in models:
            try:
                models[device.product_id] = ThingsModel.from_api(
                    await client.async_get_product_model(device.product_id)
                )
            except (ImouConnectionError, ImouAuthError):
                raise
            except ImouApiError as err:
                _LOGGER.warning(
                    "Skipping device %s: its things model is unavailable (%s)",
                    device.name,
                    err,
                )
                errors.append(str(err))
                models[device.product_id] = None
        model = models[device.product_id]
        if model is not None and model.has_service(SERVICE_CLEAN_CONTROL):
            robots.append((device, model))
    return robots, errors


async def async_setup_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Set up Imou Robot Vacuum from a config entry."""
    client = ImouClient(
        async_get_clientsession(hass),
        entry.data[CONF_APP_ID],
        entry.data[CONF_APP_SECRET],
        REGION_HOSTS[entry.data.get(CONF_REGION, DEFAULT_REGION)],
    )

    try:
        robots, model_errors = await _async_discover_robots(client)
    except ImouAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except ImouError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    if not robots:
        if model_errors:
            # The robot's own model may be the one that failed: try again later.
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": model_errors[-1]},
            )
        # Retrying would only burn API quota; the user has to add the robot first.
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="no_robots_found"
        )

    coordinators: dict[str, ImouCoordinator] = {}
    for device, model in robots:
        coordinator = ImouCoordinator(hass, entry, client, device, model)
        await coordinator.async_config_entry_first_refresh()
        coordinators[device.device_id] = coordinator

    entry.runtime_data = ImouRuntimeData(client=client, coordinators=coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
