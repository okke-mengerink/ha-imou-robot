"""Select platform for Imou robot vacuums: suction and water levels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, LEVEL_KEYS, PROP_WATER, PROP_WIND_POWER
from .coordinator import ImouConfigEntry, ImouCoordinator
from .entity import ImouEntity
from .model import level_key, to_int

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ImouSelectDescription(SelectEntityDescription):
    """Select backed by one level property; the levels are in const.LEVEL_KEYS."""

    identifier: str


SELECTS: tuple[ImouSelectDescription, ...] = (
    ImouSelectDescription(
        key="suction_level",
        translation_key="suction_level",
        identifier=PROP_WIND_POWER,
    ),
    ImouSelectDescription(
        key="mop_water_level",
        translation_key="mop_water_level",
        identifier=PROP_WATER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create selects for every level property the robot's model has."""
    async_add_entities(
        ImouLevelSelect(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in SELECTS
        if description.identifier in LEVEL_KEYS
        and coordinator.model.prop(description.identifier) is not None
    )


class ImouLevelSelect(ImouEntity, SelectEntity):
    """A 1/2/3 level setting, set through the WindWater service.

    The L11-A uses 3 levels although its things model declares 0-100, and it
    ignores plain property writes for them. A raw value that is not a known level
    is shown as ``unknown_<n>``.
    """

    entity_description: ImouSelectDescription

    def __init__(
        self, coordinator: ImouCoordinator, description: ImouSelectDescription
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._levels = LEVEL_KEYS[description.identifier]

    def _raw(self) -> int | None:
        identifier = self.entity_description.identifier
        reported = to_int(self.robot_values.get(identifier))
        return self.coordinator.commanded[identifier].resolve(reported)

    @property
    @override
    def options(self) -> list[str]:
        """Return the known levels, plus the current value if it is not one."""
        options = list(self._levels.values())
        raw = self._raw()
        if raw is not None and raw not in self._levels:
            options.append(f"unknown_{raw}")
        return options

    @property
    @override
    def current_option(self) -> str | None:
        """Return the current level."""
        return level_key(self._levels, self._raw())

    @override
    async def async_select_option(self, option: str) -> None:
        """Set a level."""
        levels = {key: level for level, key in self._levels.items()}
        if (level := levels.get(option)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_option",
                translation_placeholders={"option": option},
            )
        await self.coordinator.async_set_level(self.entity_description.identifier, level)
        self.coordinator.commanded[self.entity_description.identifier].set(level)
        # every entity showing this setting (e.g. select and fan speed) updates now
        self.coordinator.async_update_listeners()
