"""Switch platform for Imou robot vacuums."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PROP_AUTO_DUST, PROP_KEEP_CLEAN, PROP_RUG_PRESS
from .coordinator import ImouConfigEntry, ImouCoordinator
from .entity import ImouEntity
from .model import to_bool

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ImouSwitchDescription(SwitchEntityDescription):
    """Switch backed by one boolean property."""

    identifier: str


SWITCHES: tuple[ImouSwitchDescription, ...] = (
    ImouSwitchDescription(
        key="auto_empty",
        translation_key="auto_empty",
        identifier=PROP_AUTO_DUST,
    ),
    ImouSwitchDescription(
        key="carpet_boost",
        translation_key="carpet_boost",
        identifier=PROP_RUG_PRESS,
    ),
    ImouSwitchDescription(
        key="resume_after_charging",
        translation_key="resume_after_charging",
        identifier=PROP_KEEP_CLEAN,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create switches for every identifier the robot's model has."""
    async_add_entities(
        ImouSwitch(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in SWITCHES
        if coordinator.model.prop(description.identifier) is not None
    )


class ImouSwitch(ImouEntity, SwitchEntity):
    """A setting that is written with ``setIotDeviceProperties``."""

    entity_description: ImouSwitchDescription

    def __init__(
        self, coordinator: ImouCoordinator, description: ImouSwitchDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the setting is on."""
        identifier = self.entity_description.identifier
        return self.coordinator.commanded[identifier].resolve(
            to_bool(self.robot_values.get(identifier))
        )

    async def _async_set(self, value: bool) -> None:
        await self.coordinator.async_set_properties(
            {self.entity_description.identifier: int(value)}
        )
        self.coordinator.commanded[self.entity_description.identifier].set(value)
        # every entity showing this setting (e.g. select and fan speed) updates now
        self.coordinator.async_update_listeners()

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self._async_set(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self._async_set(False)
