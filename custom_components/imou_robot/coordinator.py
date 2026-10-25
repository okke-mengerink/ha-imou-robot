"""Data update coordinator for an Imou robot vacuum."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Mapping
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
    COMMAND_BOOST_SECONDS,
    COMMAND_REFRESH_DELAY,
    DEFAULT_LEVEL,
    DOMAIN,
    ERROR_CODE,
    ERROR_VALID,
    INPUT_WATER,
    INPUT_WIND,
    PROP_ERROR_INFO,
    PROP_WATER,
    PROP_WIND_POWER,
    PROP_WORK_STATUS,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    SERVICE_WIND_WATER,
)
from .commanded import CommandedValue
from .health import PollHealth
from .model import ReportTracker, ThingsModel, to_bool, to_int

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


type ImouConfigEntry = ConfigEntry[ImouRuntimeData]


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
            self.client.async_control(
                self.device.product_id, self.device.device_id, definition.ref, content
            )
        )
        _LOGGER.debug("Imou %s output: %s", service, _output_data(output))

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
