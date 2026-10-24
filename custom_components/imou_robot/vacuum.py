"""Vacuum platform for Imou robot vacuums."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLEAN_CONTINUE,
    CLEAN_MODE_WHOLE_HOUSE,
    CLEAN_PAUSE,
    CLEAN_PLAN_GENERAL,
    CLEAN_START,
    CLEAN_STOP,
    DOMAIN,
    INPUT_MODE,
    INPUT_PLAN,
    INPUT_STATUS,
    LEVEL_KEYS,
    PROP_BACK_CHARGING,
    PROP_WIND_POWER,
    SERVICE_CLEAN_CONTROL,
    SERVICE_LOCATION,
)
from .coordinator import ImouConfigEntry, ImouCoordinator
from .entity import ImouEntity
from .model import level_key, to_int

PARALLEL_UPDATES = 1

# work_status -> activity. Unknown values give an unknown state.
WORK_STATUS_ACTIVITY: dict[int, VacuumActivity] = {
    0: VacuumActivity.IDLE,
    1: VacuumActivity.PAUSED,
    13: VacuumActivity.PAUSED,
    2: VacuumActivity.RETURNING,
    3: VacuumActivity.DOCKED,
    11: VacuumActivity.DOCKED,
    6: VacuumActivity.ERROR,
    4: VacuumActivity.CLEANING,
    5: VacuumActivity.CLEANING,
    7: VacuumActivity.CLEANING,
    8: VacuumActivity.CLEANING,
    9: VacuumActivity.CLEANING,
    10: VacuumActivity.CLEANING,
    12: VacuumActivity.CLEANING,
}
PAUSED_WORK_STATUSES = frozenset({1, 13})

# Fan speed presets are the suction levels (WindPower 1/2/3); translated through
# entity.vacuum.robot.state_attributes.fan_speed.state.
FAN_SPEEDS = LEVEL_KEYS[PROP_WIND_POWER]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one vacuum per robot."""
    async_add_entities(
        ImouVacuum(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.model.has_service(SERVICE_CLEAN_CONTROL)
    )


class ImouVacuum(ImouEntity, StateVacuumEntity):
    """The robot vacuum itself."""

    _attr_name = None
    _attr_translation_key = "robot"

    def __init__(self, coordinator: ImouCoordinator) -> None:
        """Initialize the vacuum; features depend on the things model."""
        super().__init__(coordinator, "vacuum")
        model = coordinator.model
        features = VacuumEntityFeature.STATE
        control = model.service(SERVICE_CLEAN_CONTROL)
        if control is not None and control.input_ref(INPUT_STATUS) is not None:
            features |= (
                VacuumEntityFeature.START
                | VacuumEntityFeature.PAUSE
                | VacuumEntityFeature.STOP
            )
        if model.prop(PROP_BACK_CHARGING) is not None:
            features |= VacuumEntityFeature.RETURN_HOME
        if model.service(SERVICE_LOCATION) is not None:
            features |= VacuumEntityFeature.LOCATE
        if model.prop(PROP_WIND_POWER) is not None:
            features |= VacuumEntityFeature.FAN_SPEED
        self._attr_supported_features = features

    @property
    @override
    def activity(self) -> VacuumActivity | None:
        """Return the current activity."""
        data = self.coordinator.data
        if data is None or data.work_status is None:
            return None
        return WORK_STATUS_ACTIVITY.get(data.work_status)

    @property
    @override
    def fan_speed_list(self) -> list[str]:
        """Return the fan speed presets (the suction levels)."""
        return list(FAN_SPEEDS.values())

    @property
    @override
    def fan_speed(self) -> str | None:
        """Return the current fan speed; levels we do not know are unknown_<n>."""
        reported = to_int(self.robot_values.get(PROP_WIND_POWER))
        commanded = self.coordinator.commanded[PROP_WIND_POWER]
        return level_key(FAN_SPEEDS, commanded.resolve(reported))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw work status and error code."""
        data = self.coordinator.data
        if data is None:
            return None
        return {"work_status": data.work_status, "error": data.error_code}

    @override
    async def async_start(self) -> None:
        """Start cleaning, or resume when paused."""
        # The cached state can be minutes old while the robot is paused or docked,
        # so look at the robot first (one extra API call).
        await self.coordinator.async_refresh()
        data = self.coordinator.data
        if data is not None and data.work_status in PAUSED_WORK_STATUSES:
            inputs = {INPUT_STATUS: CLEAN_CONTINUE}
        else:
            inputs = {
                INPUT_STATUS: CLEAN_START,
                INPUT_MODE: CLEAN_MODE_WHOLE_HOUSE,
                INPUT_PLAN: CLEAN_PLAN_GENERAL,
            }
        await self.coordinator.async_control(SERVICE_CLEAN_CONTROL, inputs)

    @override
    async def async_pause(self) -> None:
        """Pause cleaning."""
        await self.coordinator.async_control(
            SERVICE_CLEAN_CONTROL, {INPUT_STATUS: CLEAN_PAUSE}
        )

    @override
    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        await self.coordinator.async_control(
            SERVICE_CLEAN_CONTROL, {INPUT_STATUS: CLEAN_STOP}
        )

    @override
    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send the robot back to its dock."""
        await self.coordinator.async_set_properties({PROP_BACK_CHARGING: 1})

    @override
    async def async_locate(self, **kwargs: Any) -> None:
        """Make the robot announce itself."""
        await self.coordinator.async_control(SERVICE_LOCATION)

    @override
    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the suction level; the property is written, WindWater is only a fallback."""
        levels = {key: level for level, key in FAN_SPEEDS.items()}
        if (level := levels.get(fan_speed)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_speed",
                translation_placeholders={"fan_speed": fan_speed},
            )
        await self.coordinator.async_set_level(PROP_WIND_POWER, level)
        self.coordinator.commanded[PROP_WIND_POWER].set(level)
        # every entity showing this setting (e.g. select and fan speed) updates now
        self.coordinator.async_update_listeners()
