"""Binary sensor platform for Imou robot vacuums."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PROP_MOP_ENABLE, PROP_POWER_STATUS
from .coordinator import ImouConfigEntry, ImouCoordinator
from .entity import ImouEntity
from .model import ThingsModel, to_bool

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ImouBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor backed by one boolean property."""

    identifier: str
    # (model, raw value) -> state; a plain boolean unless the model says otherwise
    is_on_fn: Callable[[ThingsModel, Any], bool | None] = lambda _model, raw: to_bool(raw)


BINARY_SENSORS: tuple[ImouBinarySensorDescription, ...] = (
    ImouBinarySensorDescription(
        key="mop_fitted",
        translation_key="mop_fitted",
        identifier=PROP_MOP_ENABLE,
    ),
    ImouBinarySensorDescription(
        key="charging",
        translation_key="charging",
        identifier=PROP_POWER_STATUS,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda model, raw: model.is_charging(raw),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create binary sensors for every identifier the robot's model has."""
    async_add_entities(
        ImouBinarySensor(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in BINARY_SENSORS
        if coordinator.model.prop(description.identifier) is not None
    )


class ImouBinarySensor(ImouEntity, BinarySensorEntity):
    """A binary sensor."""

    entity_description: ImouBinarySensorDescription

    def __init__(
        self, coordinator: ImouCoordinator, description: ImouBinarySensorDescription
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the property is true."""
        description = self.entity_description
        return description.is_on_fn(
            self.coordinator.model, self.robot_values.get(description.identifier)
        )
