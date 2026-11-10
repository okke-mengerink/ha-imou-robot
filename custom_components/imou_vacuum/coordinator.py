"""Data update coordinator for an Imou robot vacuum."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ImouAuthError,
    ImouClient,
    ImouDevice,
    ImouDeviceOfflineError,
    ImouError,
)
from .const import (
    ACTIVE_WORK_STATUSES,
    CLEAN_MODE_ROOMS,
    CLEAN_PLAN_GENERAL,
    CLEAN_START,
    COMMAND_BOOST_SECONDS,
    COMMAND_REFRESH_DELAY,
    CONF_ROOM_NAMES,
    DEFAULT_LEVEL,
    DOMAIN,
    ERROR_CODE,
    ERROR_VALID,
    INPUT_MODE,
    INPUT_PARTITION_AREA,
    INPUT_PLAN,
    INPUT_STATUS,
    INPUT_WATER,
    INPUT_WIND,
    OUTPUT_TIMER,
    PROP_ERROR_INFO,
    PROP_WATER,
    PROP_WIND_POWER,
    PROP_WORK_STATUS,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    SCHEDULE_MIN_REFRESH_SECONDS,
    SCHEDULE_REFRESH_SECONDS,
    SCHEDULE_RETRY_SECONDS,
    SERVICE_CLEAN_CONTROL,
    SERVICE_CLEAN_LIST,
    SERVICE_WIND_WATER,
)
from .commanded import CommandedValue
from .health import PollHealth
from .model import ReportTracker, ThingsModel, to_bool, to_int
from .probe import redact, summarize
from .rooms import (
    ScheduleList,
    known_rooms,
    parse_schedules,
    partition_area,
    segment_names,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ImouRobotState:
    """Latest values of one robot, keyed by things-model identifier."""

    online: bool
    values: dict[str, Any]

    @property
    def work_status(self) -> int | None:
        """Return the raw work status, if known."""
        return to_int(self.values.get(PROP_WORK_STATUS))

    @property
    def error_code(self) -> int:
        """Return the active error code (0 = no error).

        Error info that the robot flags as not valid is ignored; a robot that
        reports no error info at all has no error.
        """
        info = self.values.get(PROP_ERROR_INFO)
        if not isinstance(info, Mapping):
            return 0
        if to_bool(info.get(ERROR_VALID)) is False:
            return 0
        return to_int(info.get(ERROR_CODE)) or 0


@dataclass
class ImouRuntimeData:
    """Runtime data stored in ``entry.runtime_data``."""

    client: ImouClient
    coordinators: dict[str, ImouCoordinator]  # keyed by device id
    options: dict[str, Any]  # snapshot of entry.options taken at setup


type ImouConfigEntry = ConfigEntry[ImouRuntimeData]


def default_room_name(room_id: int) -> str:
    """Return the name of a room that has no label and no name of the user."""
    return f"Room {room_id}"


def _unsupported(feature: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="unsupported",
        translation_placeholders={"feature": feature},
    )


def _command_failed(err: ImouError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
    )


class ImouCoordinator(DataUpdateCoordinator[ImouRobotState]):
    """Poll one robot and run its commands."""

    config_entry: ImouConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ImouConfigEntry,
        client: ImouClient,
        device: ImouDevice,
        model: ThingsModel,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.name}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_IDLE),
        )
        self.client = client
        self.device = device
        self.model = model
        self.poll_refs = model.poll_refs()
        # which settings the robot reports; filled by the first refresh, which
        # runs before any entity is created
        self.reports = ReportTracker()
        # consecutive failed polls: one hiccup must not make the entities unavailable
        self.health = PollHealth()
        # last value commanded per identifier, shared by all entities of the setting
        self.commanded: defaultdict[str, CommandedValue[Any]] = defaultdict(
            CommandedValue
        )
        # the last service call (redacted inputs, summarized output or error code),
        # for diagnostics
        self.last_command: dict[str, Any] | None = None
        # the robot's cleaning schedules (the only place with room ids), or None
        # while unknown; refreshed about once an hour
        self.schedules: ScheduleList | None = None
        self._schedules_due = 0.0  # time.monotonic() of the next fetch
        self._schedules_fetched_at: float | None = None  # monotonic, last success
        self._boost_until = 0.0
        self._command_refresh_cancel: CALLBACK_TYPE | None = None

    # -- polling -------------------------------------------------------------
    async def _async_update_data(self) -> ImouRobotState:
        """Fetch the latest values and pick the next polling interval.

        A failed poll, or a robot that the cloud calls "offline", keeps the last
        good data (the entities stay available) until it has happened several
        times in a row or the last good data is too old. Any other status, such as
        "sleep" or "upgrading", is not a failure: the last data is shown.
        """
        try:
            result = await self.client.async_get_properties(
                self.device.product_id, self.device.device_id, self.poll_refs
            )
        except ImouDeviceOfflineError:
            return self._poll_failed("the robot is offline")
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except ImouError as err:
            return self._poll_failed(str(err), err)

        # A missing status is treated as online.
        status = (result.status or "online").lower()
        if status == "offline":
            return self._poll_failed("the robot is offline")

        values = self.model.parse_values(result.values)
        if status != "online" and not values and self.data is not None:
            values = dict(self.data.values)  # asleep or upgrading: show the last data
        if status == "online":
            self.reports.update(True, values)  # only an online robot is evidence

        state = ImouRobotState(online=True, values=values)
        failed = self.health.success()
        if failed and self.last_update_success:
            # (after unavailable entities Home Assistant logs the recovery itself)
            _LOGGER.info(
                "Reading %s works again after %d failed poll(s)",
                self.device.name,
                failed,
            )
        self._update_polling_interval(state)
        if status == "online" and time.monotonic() >= self._schedules_due:
            await self._async_fetch_schedules()
        return state

    def _poll_failed(self, reason: str, err: ImouError | None = None) -> ImouRobotState:
        """Count a failed poll and decide what the entities see.

        ``err`` is the API error of a failed call; it is None when the cloud said
        the robot is offline, which becomes an offline state instead of an error.
        """
        keep = self.health.failure()
        self.update_interval = timedelta(
            seconds=self.health.retry_interval(self._interval_seconds())
        )
        if keep and self.data is not None:
            if self.health.failures == 1:  # one warning, not one per failed poll
                _LOGGER.warning(
                    "Could not read %s (%s); keeping the last data",
                    self.device.name,
                    reason,
                )
            return self.data
        if err is not None:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return ImouRobotState(
            online=False, values=self.data.values if self.data is not None else {}
        )

    def _interval_seconds(self) -> float:
        if self.update_interval is None:
            return float(SCAN_INTERVAL_IDLE)
        return self.update_interval.total_seconds()

    def _update_polling_interval(self, state: ImouRobotState) -> None:
        """30 s while the robot is active (or just got a command), else 300 s."""
        active = state.online and state.work_status in ACTIVE_WORK_STATUSES
        boosted = time.monotonic() < self._boost_until
        self.update_interval = timedelta(
            seconds=SCAN_INTERVAL_ACTIVE if active or boosted else SCAN_INTERVAL_IDLE
        )

    # -- commands ------------------------------------------------------------
    async def async_set_properties(self, values: Mapping[str, Any]) -> None:
        """Write properties given as ``{identifier: value}``."""
        payload: dict[str, Any] = {}
        for identifier, value in values.items():
            if (prop := self.model.prop(identifier)) is None:
                raise _unsupported(identifier)
            payload[prop.ref] = value
        await self._async_command(
            self.client.async_set_properties(
                self.device.product_id, self.device.device_id, payload
            )
        )

    async def async_control(
        self, service: str, inputs: Mapping[str, Any] | None = None
    ) -> None:
        """Call a service; ``inputs`` maps input identifiers to values."""
        if (definition := self.model.service(service)) is None:
            raise _unsupported(service)
        content = {
            ref: value
            for name, value in (inputs or {}).items()
            if (ref := definition.input_ref(name)) is not None
        }
        output = await self._async_command(
            self._async_record_control(service, inputs or {}, definition.ref, content)
        )
        self.last_command = {
            "service": service,
            "inputs": redact(dict(inputs or {})),
            "output": summarize(_output_data(output)),
        }
        _LOGGER.debug("Imou %s output: %s", service, _output_data(output))

    async def _async_record_control(
        self, service: str, inputs: Mapping[str, Any], ref: str, content: dict[str, Any]
    ) -> Any:
        """Call the service; an API error is recorded in ``last_command`` and re-raised."""
        try:
            return await self.client.async_control(
                self.device.product_id, self.device.device_id, ref, content
            )
        except ImouError as err:
            self.last_command = {
                "service": service,
                "inputs": redact(dict(inputs)),
                "error": err.code,
            }
            raise

    # -- read-only probe (diagnostics) ----------------------------------------
    async def async_read_identifiers(self, identifiers: Iterable[str]) -> dict[str, Any]:
        """Read properties once, by identifier, without touching polling or health.

        Unknown identifiers are skipped. ``ImouError`` propagates.
        """
        refs: list[str] = []
        for identifier in identifiers:
            if (prop := self.model.prop(identifier)) is not None:
                refs.append(prop.ref)
        if not refs:
            return {}
        result = await self.client.async_get_properties(
            self.device.product_id, self.device.device_id, refs
        )
        return self.model.parse_values(result.values)

    async def async_query_service(self, service: str) -> Any:
        """Call an input-less service and return the raw response data.

        Does not schedule a refresh or boost polling. ``ImouError`` propagates.
        """
        if (definition := self.model.service(service)) is None:
            raise _unsupported(service)
        return await self.client.async_control(
            self.device.product_id, self.device.device_id, definition.ref, {}
        )

    # -- rooms and schedules -------------------------------------------------
    async def _async_fetch_schedules(self) -> bool:
        """Read the schedules; on any failure keep the previous ones.

        Never raises ``ImouError``, so it cannot make a poll fail. Returns whether
        new schedules were read.
        """
        if (service := self.model.service(SERVICE_CLEAN_LIST)) is None:
            return False
        try:
            response = await self.async_query_service(SERVICE_CLEAN_LIST)
        except ImouError as err:
            _LOGGER.debug("Could not read the schedules of %s: %s", self.device.name, err)
            self._schedules_due = time.monotonic() + SCHEDULE_RETRY_SECONDS
            return False
        parsed = parse_schedules(response, service.output_ref(OUTPUT_TIMER))
        if parsed is None:
            _LOGGER.debug("The schedules of %s have an unknown format", self.device.name)
            self._schedules_due = time.monotonic() + SCHEDULE_RETRY_SECONDS
            return False
        self.schedules = parsed
        self._schedules_fetched_at = time.monotonic()
        self._schedules_due = time.monotonic() + SCHEDULE_REFRESH_SECONDS
        return True

    async def async_refresh_schedules(self) -> bool:
        """Read the schedules now and update the entities; False if that failed.

        Schedules read less than ``SCHEDULE_MIN_REFRESH_SECONDS`` ago are reused.
        """
        fetched_at = self._schedules_fetched_at
        if (
            self.schedules is not None
            and fetched_at is not None
            and time.monotonic() - fetched_at < SCHEDULE_MIN_REFRESH_SECONDS
        ):
            return True
        if not await self._async_fetch_schedules():
            return False
        self.async_update_listeners()
        return True

    def room_overrides(self) -> dict[int, str]:
        """Return the room names the user chose for this robot, by room id."""
        options = self.config_entry.options.get(CONF_ROOM_NAMES, {})
        stored = options.get(self.device.device_id, {}) if isinstance(options, Mapping) else {}
        overrides: dict[int, str] = {}
        for key, name in (stored.items() if isinstance(stored, Mapping) else ()):
            if (room_id := to_int(key)) is not None and isinstance(name, str) and name:
                overrides[room_id] = name
        return overrides

    def room_names(self) -> dict[int, str]:
        """Return the name of every room of the schedules, by room id.

        The user's name, else the room type label from the Imou app, else
        ``Room <id>``; rooms with the same name get their id appended.
        """
        known = known_rooms(self.schedules)
        return segment_names(known, known, self.room_overrides(), default_room_name)

    async def async_clean_rooms(self, room_ids: list[int]) -> None:
        """Clean these rooms (ids as found in the schedules)."""
        await self.async_control(
            SERVICE_CLEAN_CONTROL,
            {
                INPUT_STATUS: CLEAN_START,
                INPUT_MODE: CLEAN_MODE_ROOMS,
                INPUT_PLAN: CLEAN_PLAN_GENERAL,
                INPUT_PARTITION_AREA: partition_area(room_ids),
            },
        )

    async def async_set_level(self, identifier: str, level: int) -> None:
        """Set suction (WindPower) or water (Water) to a level.

        The WindWater service is the way to do it: on the L11-A a plain
        property write is accepted (code 0) but ignored by the robot. Only a
        model without that service gets a property write.
        """
        if (inputs := self._wind_water_inputs(identifier, level)) is not None:
            await self.async_control(SERVICE_WIND_WATER, inputs)
        else:
            await self.async_set_properties({identifier: level})

    def _wind_water_inputs(self, identifier: str, level: int) -> dict[str, int] | None:
        """Inputs for the WindWater service, or None if the model has no such service.

        Both levels are always sent: the new one, and the current value of the
        other one. That is a command that is still pending (the cloud can lag a
        few seconds), else the latest value reported by the cloud, else
        ``DEFAULT_LEVEL``.
        """
        service = self.model.service(SERVICE_WIND_WATER)
        pair = {PROP_WIND_POWER: INPUT_WIND, PROP_WATER: INPUT_WATER}
        changed = pair.get(identifier)
        if service is None or changed is None or service.input_ref(changed) is None:
            return None
        values = self.data.values if self.data is not None else {}
        inputs = {changed: level}
        for prop, name in pair.items():
            if name != changed and service.input_ref(name) is not None:
                current = self.commanded[prop].resolve(to_int(values.get(prop)))
                inputs[name] = DEFAULT_LEVEL if current is None else current
        return inputs

    async def _async_command(self, call: Awaitable[Any]) -> Any:
        """Run a command; map API errors and schedule a quick refresh."""
        try:
            return await call
        except ImouError as err:
            raise _command_failed(err) from err
        finally:
            self._async_after_command()

    @callback
    def _async_after_command(self) -> None:
        """Refresh once after about 3 s and poll fast for a while."""
        if self._shutdown_requested:
            return
        self._boost_until = time.monotonic() + COMMAND_BOOST_SECONDS
        if self._command_refresh_cancel is not None:
            self._command_refresh_cancel()
        self._command_refresh_cancel = async_call_later(
            self.hass, COMMAND_REFRESH_DELAY, self._async_command_refresh
        )

    async def _async_command_refresh(self, _now: datetime) -> None:
        self._command_refresh_cancel = None
        if not self.hass.is_stopping:
            await self.async_refresh()

    async def async_shutdown(self) -> None:
        """Cancel the pending command refresh and shut the coordinator down."""
        if self._command_refresh_cancel is not None:
            self._command_refresh_cancel()
            self._command_refresh_cancel = None
        await super().async_shutdown()


def _output_data(result: Any) -> Any:
    """Return the service output of an ``iotDeviceControl`` response (for logs)."""
    content = result.get("content") if isinstance(result, Mapping) else None
    return content.get("outputData") if isinstance(content, Mapping) else None
