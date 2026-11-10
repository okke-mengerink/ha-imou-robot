"""The Imou Vacuum (Open Platform) integration.

Controls Imou robot vacuums through the Imou Open Platform cloud API. Property
and service refs are resolved at runtime, by identifier, from each device's
things model.
"""

from __future__ import annotations

import copy
import hashlib
import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import issue_registry as ir
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
from .support import (
    needs_notice,
    new_model_issue_url,
    support_report,
)

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


def _support_issue_id(entry_id: str, device_id: str) -> str:
    """Return the repair issue id; the device id (a serial) is only hashed into it."""
    digest = hashlib.sha256(device_id.encode()).hexdigest()[:12]
    return f"model_support_{entry_id}_{digest}"


@callback
def _async_delete_support_issues(
    hass: HomeAssistant, entry_id: str, keep: frozenset[str] = frozenset()
) -> None:
    """Delete the model-support repair issues of this config entry, except ``keep``."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain != DOMAIN or issue_id in keep:
            continue
        issue = registry.async_get_issue(domain, issue_id)
        if issue is not None and (issue.data or {}).get("entry_id") == entry_id:
            ir.async_delete_issue(hass, domain, issue_id)


@callback
def _async_report_support(
    hass: HomeAssistant, entry: ImouConfigEntry, device: ImouDevice, model: ThingsModel
) -> None:
    """Tell the user when this robot model is not (fully) supported.

    One log line for a model that is not verified yet, and a repair issue (deleted
    again when nothing is missing any more) when a core function is missing or a
    value is unknown.
    """
    report = support_report(model, device.model)
    name = device.model or "This robot"
    if not report["verified_model"]:
        _LOGGER.info(
            "%s is not verified yet; please share the diagnostics download so it can "
            "be mapped (see %s)",
            name,
            new_model_issue_url(device.model),
        )
    issue_id = _support_issue_id(entry.entry_id, device.device_id)
    if not needs_notice(report):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="model_not_fully_supported",
        translation_placeholders={
            "model": name,
            "missing": ", ".join(report["missing_core"]) or "none",
            "unknown": ", ".join(report["unknown_enum_labels"]) or "none",
        },
        learn_more_url=new_model_issue_url(device.model),
        data={"entry_id": entry.entry_id},
    )


async def async_setup_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Set up Imou Vacuum (Open Platform) from a config entry."""
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
        _async_delete_support_issues(hass, entry.entry_id)
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="no_robots_found"
        )

    # a robot that is no longer found must not leave its repair issue behind
    _async_delete_support_issues(
        hass,
        entry.entry_id,
        frozenset(_support_issue_id(entry.entry_id, d.device_id) for d, _ in robots),
    )
    coordinators: dict[str, ImouCoordinator] = {}
    for device, model in robots:
        coordinator = ImouCoordinator(hass, entry, client, device, model)
        await coordinator.async_config_entry_first_refresh()
        coordinators[device.device_id] = coordinator
        _async_report_support(hass, entry, device, model)

    entry.runtime_data = ImouRuntimeData(
        client=client, coordinators=coordinators, options=copy.deepcopy(dict(entry.options))
    )
    # new room names (options) are picked up by reloading
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ImouConfigEntry) -> None:
    """Reload the entry when its options (the room names) change.

    The listener also runs for data-only updates (reauth), which reload on their own.
    """
    if dict(entry.options) == entry.runtime_data.options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> None:
    """Delete the repair issues of a removed config entry."""
    _async_delete_support_issues(hass, entry.entry_id)
