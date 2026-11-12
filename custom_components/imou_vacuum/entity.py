"""Base entity for the Imou Vacuum (Open Platform) integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, override

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import ImouConfigEntry, ImouCoordinator


def local_start(timestamp: int | None) -> str | None:
    """Return a schedule's start as local ISO 8601; never raises."""
    if timestamp is None:
        return None
    try:
        return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def local_time(timestamp: int | None) -> str | None:
    """Return the local time of day (HH:MM) of a timestamp; never raises."""
    if timestamp is None:
        return None
    try:
        return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).strftime("%H:%M")
    except (ValueError, OverflowError, OSError):
        return None


@callback
def async_add_after_first_poll(
    entry: ImouConfigEntry,
    coordinator: ImouCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[], Iterable[Entity]],
) -> None:
    """Add the optional entities of ``build`` once the robot's first online poll is in.

    Whether an optional setting or sensor is enabled by default depends on what the
    robot reports (``ReportTracker.enabled_by_default``), so they are created after
    that poll: now when the robot has been seen online, else from a coordinator
    listener as soon as it is.
    """
    if coordinator.reports.seen_online:
        async_add_entities(build())
        return
    unsubscribe: CALLBACK_TYPE | None = None

    @callback
    def stop() -> None:
        nonlocal unsubscribe
        if unsubscribe is not None:
            unsubscribe()
            unsubscribe = None

    @callback
    def add_when_online() -> None:
        if coordinator.reports.seen_online:
            stop()
            async_add_entities(build())

    unsubscribe = coordinator.async_add_listener(add_when_online)
    entry.async_on_unload(stop)


class ImouEntity(CoordinatorEntity[ImouCoordinator]):
    """Base class: one device per robot, unavailable while it is not online.

    An optional entity names the identifier whose reports it depends on: it is
    disabled by default when the robot was online and did not report it. It stays
    available (a robot may accept a setting without ever reporting it, or report a
    property only in some states).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ImouCoordinator,
        key: str,
        optional_identifier: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._optional_identifier = optional_identifier
        if optional_identifier is not None:
            # (existing registry entries are not changed)
            self._attr_entity_registry_enabled_default = (
                coordinator.reports.enabled_by_default(optional_identifier)
            )
        device = coordinator.device
        self._attr_unique_id = f"{device.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.name,
            model=device.model or None,
            manufacturer=MANUFACTURER,
            sw_version=device.version or None,
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the robot is reachable and the last update worked."""
        data = self.coordinator.data
        return super().available and data is not None and data.online

    @property
    def robot_values(self) -> dict[str, Any]:
        """Return the latest values keyed by things-model identifier."""
        data = self.coordinator.data
        return data.values if data is not None else {}
