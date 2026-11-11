"""Button platform for Imou robot vacuums."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SERVICE_DUST
from .coordinator import ImouConfigEntry, ImouCoordinator, default_room_name
from .entity import ImouEntity
from .rooms import room_button_name

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
    for coordinator in entry.runtime_data.coordinators.values():
        if coordinator.can_clean_rooms():
            _async_track_rooms(entry, coordinator, async_add_entities)


def _async_track_rooms(
    entry: ImouConfigEntry,
    coordinator: ImouCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a "Clean <room>" button per room now and for every room that shows up later."""
    known: set[int] = set()

    @callback
    def add_new() -> None:
        new = [
            ImouRoomButton(coordinator, room_id, name)
            for room_id, name in coordinator.room_names().items()
            if room_id not in known
        ]
        known.update(button.room_id for button in new)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


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


class ImouRoomButton(ImouEntity, ButtonEntity):
    """Cleans one room. It is unavailable once the room is no longer in the schedules."""

    _attr_translation_key = "clean_room"

    def __init__(self, coordinator: ImouCoordinator, room_id: int, name: str) -> None:
        """Initialize the button; it is named after the room (a default name in lower case)."""
        super().__init__(coordinator, f"clean_room_{room_id}")
        self.room_id = room_id
        self._attr_translation_placeholders = {
            "room": room_button_name(name, default_room_name(room_id))
        }

    @property
    @override
    def available(self) -> bool:
        """Return whether the robot is reachable and the room is still known."""
        return super().available and self.room_id in self.coordinator.room_names()

    @override
    async def async_press(self) -> None:
        """Clean this room."""
        await self.coordinator.async_clean_rooms([self.room_id])
