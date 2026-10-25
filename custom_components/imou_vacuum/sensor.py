"""Sensor platform for Imou robot vacuums."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    ERROR_CODE,
    ERROR_KEYS,
    LIFE_FILTER,
    LIFE_MAIN_BRUSH,
    LIFE_SENSORS,
    LIFE_SIDE_BRUSH,
    PROP_CLEAN_AREA,
    PROP_CLEAN_TIME,
    PROP_ERROR_INFO,
    PROP_LIFE,
    PROP_POWER,
    PROP_WIFI,
    PROP_WORK_STATUS,
    WIFI_KEYS,
    WORK_STATUS_KEYS,
)
from .coordinator import ImouConfigEntry, ImouCoordinator, ImouRobotState
from .entity import ImouEntity
from .model import PropertyDef, to_int

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def _battery(value: Any) -> int | None:
    """Clamp the battery level to 0-100 (the model allows -100..100)."""
    number = to_int(value)
    return None if number is None else max(0, min(100, number))


@dataclass(frozen=True, kw_only=True)
class ImouSensorDescription(SensorEntityDescription):
    """Numeric sensor backed by one property (or struct member)."""

    identifier: str
    child: str | None = None
    value_fn: Callable[[Any], StateType] = to_int


@dataclass(frozen=True, kw_only=True)
class ImouEnumSensorDescription(SensorEntityDescription):
    """Enum sensor; known values are translated, others become unknown_<n>."""

    identifier: str
    child: str | None = None
    known: Mapping[int, str]
    raw_fn: Callable[[ImouRobotState], int | None]
    # values that are always offered, even if the model does not list them
    base_values: tuple[int, ...] = ()


def _consumable(key: str, child: str) -> ImouSensorDescription:
    return ImouSensorDescription(
        key=key,
        translation_key=key,
        identifier=PROP_LIFE,
        child=child,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    )


SENSORS: tuple[ImouSensorDescription, ...] = (
    ImouSensorDescription(
        key="battery",
        translation_key="battery",
        identifier=PROP_POWER,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery,
    ),
    ImouSensorDescription(
        key="cleaned_area",
        translation_key="cleaned_area",
        identifier=PROP_CLEAN_AREA,
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
    ),
    ImouSensorDescription(
        key="cleaning_time",
        translation_key="cleaning_time",
        identifier=PROP_CLEAN_TIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    _consumable("side_brush", LIFE_SIDE_BRUSH),
    _consumable("main_brush", LIFE_MAIN_BRUSH),
    _consumable("filter", LIFE_FILTER),
    _consumable("sensors", LIFE_SENSORS),
)

ENUM_SENSORS: tuple[ImouEnumSensorDescription, ...] = (
    ImouEnumSensorDescription(
        key="status",
        translation_key="status",
        identifier=PROP_WORK_STATUS,
        known=WORK_STATUS_KEYS,
        raw_fn=lambda state: state.work_status,
    ),
    ImouEnumSensorDescription(
        key="error",
        translation_key="error",
        identifier=PROP_ERROR_INFO,
        child=ERROR_CODE,
        known=ERROR_KEYS,
        raw_fn=lambda state: state.error_code,
        base_values=(0,),
    ),
    ImouEnumSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        identifier=PROP_WIFI,
        known=WIFI_KEYS,
        raw_fn=lambda state: to_int(state.values.get(PROP_WIFI)),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for every identifier the robot's model actually has."""
    entities: list[SensorEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        model = coordinator.model
        entities.extend(
            ImouSensor(coordinator, description)
            for description in SENSORS
            if model.member(description.identifier, description.child) is not None
        )
        for enum_description in ENUM_SENSORS:
            definition = model.member(enum_description.identifier, enum_description.child)
            if definition is not None:
                entities.append(
                    ImouEnumSensor(coordinator, enum_description, definition)
                )
    async_add_entities(entities)


class ImouSensor(ImouEntity, SensorEntity):
    """A numeric sensor."""

    entity_description: ImouSensorDescription

    def __init__(
        self, coordinator: ImouCoordinator, description: ImouSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> StateType:
        """Return the sensor value."""
        description = self.entity_description
        raw = self.robot_values.get(description.identifier)
        if description.child is not None:
            raw = raw.get(description.child) if isinstance(raw, Mapping) else None
        return description.value_fn(raw)


class ImouEnumSensor(ImouEntity, SensorEntity):
    """An enum sensor with options taken from the robot's things model."""

    entity_description: ImouEnumSensorDescription
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        coordinator: ImouCoordinator,
        description: ImouEnumSensorDescription,
        definition: PropertyDef,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._model_labels = definition.enum_labels
        # Offer every value we can translate plus every value the model declares
        # (those we cannot translate become unknown_<n>).
        values = sorted(
            {*description.known, *definition.enum_labels, *description.base_values}
        )
        self._keys = {
            value: description.known.get(value, f"unknown_{value}") for value in values
        }
        self._attr_options = list(self._keys.values())

    def _raw(self) -> int | None:
        data = self.coordinator.data
        return self.entity_description.raw_fn(data) if data is not None else None

    @property
    @override
    def native_value(self) -> str | None:
        """Return the option key for the current raw value."""
        raw = self._raw()
        if raw is None:
            return None
        if (key := self._keys.get(raw)) is None:
            _LOGGER.debug(
                "%s: value %s is not in the model's enum", self.entity_description.key, raw
            )
        return key

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """For values we cannot translate, expose the raw value and model label."""
        raw = self._raw()
        if raw is None or raw in self.entity_description.known:
            return None
        attributes: dict[str, Any] = {"raw_value": raw}
        if label := self._model_labels.get(raw):
            attributes["model_label"] = label
        return attributes
