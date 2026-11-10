"""Vacuum platform for Imou robot vacuums."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

try:  # Home Assistant 2026.3 and newer
    from homeassistant.components.vacuum import Segment
except ImportError:  # 2026.1: no native room cleaning
    Segment = None  # type: ignore[assignment,misc]

from .const import (
    CLEAN_CONTINUE,
    CLEAN_MODE_WHOLE_HOUSE,
    CLEAN_PAUSE,
    CLEAN_PLAN_GENERAL,
    CLEAN_START,
    CLEAN_STOP,
    DOMAIN,
    INPUT_MODE,
    INPUT_PARTITION_AREA,
    INPUT_PLAN,
    INPUT_STATUS,
    PROP_BACK_CHARGING,
    PROP_BACK_STATION,
    PROP_WIND_POWER,
    SERVICE_CLEAN_CONTROL,
    SERVICE_LOCATION,
)
from .coordinator import ImouConfigEntry, ImouCoordinator, default_room_name
from .entity import ImouEntity
from .model import level_key, to_int

PARALLEL_UPDATES = 1

# Room cleaning exists in Home Assistant 2026.3 and newer only.
CLEAN_AREA = getattr(VacuumEntityFeature, "CLEAN_AREA", None)

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
    14: VacuumActivity.RETURNING,
    15: VacuumActivity.DOCKED,
    16: VacuumActivity.DOCKED,
    17: VacuumActivity.CLEANING,
    18: VacuumActivity.PAUSED,
    19: VacuumActivity.PAUSED,
}
PAUSED_WORK_STATUSES = frozenset({1, 13, 18, 19})

# Fan speed presets are the suction levels of the things model (see
# ThingsModel.wind_levels); translated through
# entity.vacuum.robot.state_attributes.fan_speed.state.


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
        if (
            model.prop(PROP_BACK_CHARGING) is not None
            or model.prop(PROP_BACK_STATION) is not None
        ):
            features |= VacuumEntityFeature.RETURN_HOME
        if model.service(SERVICE_LOCATION) is not None:
            features |= VacuumEntityFeature.LOCATE
        if model.prop(PROP_WIND_POWER) is not None:
            features |= VacuumEntityFeature.FAN_SPEED
        if model.services:
            features |= VacuumEntityFeature.SEND_COMMAND
        if (
            CLEAN_AREA is not None
            and control is not None
            and control.input_ref(INPUT_MODE) is not None
            and control.input_ref(INPUT_PARTITION_AREA) is not None
        ):
            features |= CLEAN_AREA
        # the room ids for which a "segments changed" issue was last created
        self._issue_room_ids: frozenset[str] | None = None
        self._attr_supported_features = features
        self._fan_speeds = model.wind_levels()

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
        return list(self._fan_speeds.values())

    @property
    @override
    def fan_speed(self) -> str | None:
        """Return the current fan speed; levels we do not know are unknown_<n>."""
        reported = to_int(self.robot_values.get(PROP_WIND_POWER))
        commanded = self.coordinator.commanded[PROP_WIND_POWER]
        return level_key(self._fan_speeds, commanded.resolve(reported))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw work status and error code."""
        data = self.coordinator.data
        if data is None:
            return None
        return {"work_status": data.work_status, "error": data.error_code}

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Check for new rooms, then update the state."""
        self._async_check_segments_issue(self.coordinator.room_names())
        super()._handle_coordinator_update()

    def _last_seen_segments(self) -> list[Any] | None:
        """Return the segments the user last mapped to areas (None if never)."""
        try:
            return getattr(self, "last_seen_segments", None)
        except RuntimeError:  # not in the entity registry (yet)
            return None

    def _async_check_segments_issue(self, names: dict[int, str]) -> None:
        """Create the repair issue (once per set of ids) when a room is new."""
        if CLEAN_AREA is None or CLEAN_AREA not in self.supported_features:
            return
        last_seen = self._last_seen_segments()
        if last_seen is None:
            return
        current = frozenset(str(room_id) for room_id in names)
        if not current - {segment.id for segment in last_seen}:
            return
        if current != self._issue_room_ids:
            self._issue_room_ids = current
            self.async_create_segments_issue()

    @override
    async def async_get_segments(self) -> list[Any]:
        """Return the rooms of the schedules, plus the rooms the user mapped before.

        The rooms are read from the robot's schedules (the robot reports no rooms
        otherwise). Rooms that were mapped earlier stay, so that deleting a
        schedule does not make a room disappear.
        """
        await self.coordinator.async_refresh_schedules()  # on failure: the cached ones
        names = self.coordinator.room_names()
        self._async_check_segments_issue(names)
        segments = {str(room_id): name for room_id, name in names.items()}
        for seen in self._last_seen_segments() or []:
            # a name that is only the default does not replace the one seen before
            if seen.id not in segments or segments[seen.id] == default_room_name(
                to_int(seen.id) or 0
            ):
                segments[seen.id] = seen.name
        return [
            Segment(id=segment_id, name=segments[segment_id])
            for segment_id in sorted(
                segments, key=lambda sid: (to_int(sid) is None, to_int(sid) or 0, sid)
            )
        ]

    @override
    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean these rooms."""
        room_ids: list[int] = []
        for segment_id in segment_ids:
            try:
                room_ids.append(int(str(segment_id).strip()))
            except ValueError:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_room",
                    translation_placeholders={"room": str(segment_id)},
                ) from None
        if room_ids:
            await self.coordinator.async_clean_rooms(room_ids)

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
        model = self.coordinator.model
        identifier = (
            PROP_BACK_CHARGING
            if model.prop(PROP_BACK_CHARGING) is not None
            else PROP_BACK_STATION
        )
        await self.coordinator.async_set_properties({identifier: 1})

    @override
    async def async_locate(self, **kwargs: Any) -> None:
        """Make the robot announce itself."""
        await self.coordinator.async_control(SERVICE_LOCATION)

    @override
    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the suction level (through the WindWater service, see the coordinator)."""
        levels = {key: level for level, key in self._fan_speeds.items()}
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

    @override
    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Call a service of the things model with raw input values.

        ``command`` is a service identifier and ``params`` maps its input
        identifiers to values, which are passed on unchanged (no JSON encoding).
        """
        if (service := self.coordinator.model.service(command)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_command",
                translation_placeholders={"command": command},
            )
        if params is not None and not isinstance(params, dict):
            bad = "list"
        else:
            bad = ", ".join(
                str(name)
                for name in (params or {})
                if service.input_ref(str(name)) is None
            )
        if bad:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_command_params",
                translation_placeholders={"command": command, "params": bad},
            )
        await self.coordinator.async_control(command, params)

