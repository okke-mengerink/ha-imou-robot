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
    CLEAN_BOX_KEYS,
    CLEAN_BOX_LABEL_KEYS,
    DUST_BAG_KEYS,
    DUST_BAG_LABEL_KEYS,
    ERROR_CODE,
    LIFE_FILTER,
    LIFE_MAIN_BRUSH,
    LIFE_RAW_PER_HOUR,
    LIFE_SENSORS,
    LIFE_SIDE_BRUSH,
    PROP_CLEAN_AREA,
    PROP_CLEAN_TIME,
    PROP_ERROR_INFO,
    PROP_LIFE,
    PROP_POWER,
    PROP_STATION_STATUS,
    PROP_WIFI,
    PROP_WORK_STATUS,
    SERVICE_CLEAN_LIST,
    STATION_CLEAN_BOX,
    STATION_DUST_BAG,
    STATION_WASTE_BOX,
    WASTE_BOX_KEYS,
    WASTE_BOX_LABEL_KEYS,
)
from .coordinator import (
    ImouConfigEntry,
    ImouCoordinator,
    ImouRobotState,
    default_room_name,
)
from .entity import ImouEntity, async_add_after_first_poll, local_start, local_time
from .model import ThingsModel, to_int
from .rooms import Schedule

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def _battery(value: Any) -> int | None:
    """Return the battery level as 0-100 (the model allows -100..100).

    The RV1 Pro Max reports a negative value while charging (-38 for 38 %), so the
    absolute value is used.
    """
    number = to_int(value)
    return None if number is None else min(100, abs(number))


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
    # the model's raw value -> key (label based, see ThingsModel.enum_keys)
    keys_fn: Callable[[ThingsModel], Mapping[int, str]]
    raw_fn: Callable[[ImouRobotState], int | None]
    # a dock sensor: created after the first online poll, disabled by default when
    # the robot does not report it (see ``ImouEntity``)
    optional: bool = False


def _hours(child: str) -> Callable[[Any], float | None]:
    """Convert a consumable counter to hours.

    The L11-A reports minutes used although its things model says "h", see
    ``LIFE_RAW_PER_HOUR``. Rounded to 4 decimals (0.36 s) to keep the state short.
    """
    per_hour = LIFE_RAW_PER_HOUR[child]

    def convert(value: Any) -> float | None:
        raw = to_int(value)
        return None if raw is None else round(raw / per_hour, 4)

    return convert


def _consumable(key: str, child: str) -> ImouSensorDescription:
    return ImouSensorDescription(
        key=key,
        translation_key=key,
        identifier=PROP_LIFE,
        child=child,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_hours(child),
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
        keys_fn=ThingsModel.work_status_keys,
        raw_fn=lambda state: state.work_status,
    ),
    ImouEnumSensorDescription(
        key="error",
        translation_key="error",
        identifier=PROP_ERROR_INFO,
        child=ERROR_CODE,
        keys_fn=ThingsModel.error_keys,
        raw_fn=lambda state: state.error_code,
    ),
    ImouEnumSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        identifier=PROP_WIFI,
        keys_fn=ThingsModel.wifi_keys,
        raw_fn=lambda state: to_int(state.values.get(PROP_WIFI)),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _station_value(child: str) -> Callable[[ImouRobotState], int | None]:
    """Return a reader for one member of the ``station_status`` struct."""

    def read(state: ImouRobotState) -> int | None:
        status = state.values.get(PROP_STATION_STATUS)
        return to_int(status.get(child)) if isinstance(status, Mapping) else None

    return read


def _station_sensor(
    key: str,
    child: str,
    label_keys: Mapping[str, str],
    numeric: Mapping[int, str],
) -> ImouEnumSensorDescription:
    return ImouEnumSensorDescription(
        key=key,
        translation_key=key,
        identifier=PROP_STATION_STATUS,
        child=child,
        keys_fn=lambda model: model.enum_keys(
            PROP_STATION_STATUS, child, label_keys, numeric
        ),
        raw_fn=_station_value(child),
        optional=True,
    )


ENUM_SENSORS += (
    _station_sensor("clean_water_tank", STATION_CLEAN_BOX, CLEAN_BOX_LABEL_KEYS, CLEAN_BOX_KEYS),
    _station_sensor("dirty_water_tank", STATION_WASTE_BOX, WASTE_BOX_LABEL_KEYS, WASTE_BOX_KEYS),
    _station_sensor("dust_bag", STATION_DUST_BAG, DUST_BAG_LABEL_KEYS, DUST_BAG_KEYS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for every identifier the robot's model actually has.

    The dock sensors are created after the robot's first online poll.
    """
    entities: list[SensorEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        model = coordinator.model
        optional: list[ImouEnumSensorDescription] = []
        entities.extend(
            ImouSensor(coordinator, description)
            for description in SENSORS
            if model.member(description.identifier, description.child) is not None
        )
        for enum_description in ENUM_SENSORS:
            definition = model.member(enum_description.identifier, enum_description.child)
            if definition is None:
                continue
            if enum_description.optional:
                optional.append(enum_description)
            else:
                entities.append(ImouEnumSensor(coordinator, enum_description))
        if model.has_service(SERVICE_CLEAN_LIST):
            entities.append(ImouSchedulesSensor(coordinator))
        if optional:
            async_add_after_first_poll(
                entry,
                coordinator,
                async_add_entities,
                lambda coordinator=coordinator, optional=optional: [
                    ImouEnumSensor(coordinator, description) for description in optional
                ],
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
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            description.key,
            description.identifier if description.optional else None,
        )
        self.entity_description = description
        definition = coordinator.model.member(description.identifier, description.child)
        self._model_labels = definition.enum_labels if definition is not None else {}
        # Offer every value of the model's enum (by label; labels we cannot
        # translate become unknown_<n>).
        self._keys = dict(description.keys_fn(coordinator.model))
        self._attr_options = list(dict.fromkeys(self._keys.values()))

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
        if raw is None:
            return None
        key = self._keys.get(raw)
        if key is not None and not key.startswith("unknown_"):
            return None
        attributes: dict[str, Any] = {"raw_value": raw}
        if label := self._model_labels.get(raw):
            attributes["model_label"] = label
        return attributes


class ImouSchedulesSensor(ImouEntity, SensorEntity):
    """The robot's cleaning schedules: the number of enabled ones, and all of them."""

    _attr_translation_key = "schedules"

    def __init__(self, coordinator: ImouCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "schedules")

    @property
    @override
    def native_value(self) -> int | None:
        """Return the number of enabled schedules (None while they are unknown)."""
        schedules = self.coordinator.schedules
        if schedules is None:
            return None
        return sum(1 for schedule in schedules.schedules if schedule.enabled)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return every schedule; ``repeat`` and ``active`` are the raw values."""
        schedules = self.coordinator.schedules
        if schedules is None:
            return None
        names = self.coordinator.room_names()
        return {"schedules": [_schedule_attributes(s, names) for s in schedules.schedules]}


def _schedule_attributes(schedule: Schedule, names: Mapping[int, str]) -> dict[str, Any]:
    """Return one schedule for the attribute; ``start_raw`` and ``once`` only exist
    for a robot that reports them (RV1 Pro Max), whose start time is not converted."""
    attributes: dict[str, Any] = {
        "enabled": schedule.enabled,
        "active": schedule.active,
        "start": local_start(schedule.start),
        "time": schedule.time_of_day or local_time(schedule.start),
        "days": schedule.days,
        "repeat": schedule.period,
        "rooms": (
            [names.get(room_id, default_room_name(room_id)) for room_id in schedule.room_ids]
            if schedule.room_ids
            else "unknown"
            if schedule.rooms_unknown
            else "whole_house"
        ),
    }
    if schedule.start_raw is not None or schedule.once is not None:
        attributes["start_raw"] = schedule.start_raw
        attributes["once"] = schedule.once
    return attributes
