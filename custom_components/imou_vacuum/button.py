"""Button platform for Imou robot vacuums."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SERVICE_DUST
from .coordinator import ImouConfigEntry, ImouCoordinator
from .entity import ImouEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ImouButtonDescription(ButtonEntityDescription):
    """Button that calls one service without inputs."""

    service: str


BUTTONS: tuple[ImouButtonDescription, ...] = (
    ImouButtonDescription(
        key="empty_bin",
        translation_key="empty_bin",
        service=SERVICE_DUST,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create buttons for every service the robot's model has."""
    async_add_entities(
        ImouButton(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in BUTTONS
        if coordinator.model.has_service(description.service)
    )


class ImouButton(ImouEntity, ButtonEntity):
    """A button that triggers a service."""

    entity_description: ImouButtonDescription

    def __init__(
        self, coordinator: ImouCoordinator, description: ImouButtonDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @override
    async def async_press(self) -> None:
        """Call the service."""
        await self.coordinator.async_control(self.entity_description.service)
