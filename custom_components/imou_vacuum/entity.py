"""Base entity for the Imou Vacuum (Open Platform) integration."""

from __future__ import annotations

from typing import Any, override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ImouCoordinator


class ImouEntity(CoordinatorEntity[ImouCoordinator]):
    """Base class: one device per robot, unavailable while it is not online."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ImouCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
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
