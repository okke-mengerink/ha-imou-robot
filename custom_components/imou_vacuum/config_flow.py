"""Config flow for the Imou Vacuum (Open Platform) integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    AreaSelector,
    SelectSelector,
    SelectOptionDict,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ImouAuthError, ImouClient, ImouConnectionError, ImouError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_REGION,
    CONF_ROOM_NAMES,
    DEFAULT_REGION,
    DOMAIN,
    REGION_HOSTS,
)
from .coordinator import ImouCoordinator, default_room_name
from .rooms import (
    areas_by_room,
    format_room_names,
    new_area_mapping,
    new_last_seen_segments,
    parse_room_names,
    replace_room_names,
    room_area_labels,
    room_key,
    room_overrides_for,
    segment_names,
)
from .vacuum import CLEAN_AREA

_LOGGER = logging.getLogger(__name__)

REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(REGION_HOSTS),
        translation_key=CONF_REGION,
        mode=SelectSelectorMode.DROPDOWN,
    )
)
SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APP_ID): TextSelector(),
        vol.Required(CONF_APP_SECRET): SECRET_SELECTOR,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): REGION_SELECTOR,
    }
)
CONF_ROBOT = "robot"
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_APP_SECRET): SECRET_SELECTOR})


async def _async_validate(
    hass: HomeAssistant, app_id: str, app_secret: str, region: str
) -> None:
    """Validate the credentials by requesting an access token."""
    client = ImouClient(
        async_get_clientsession(hass), app_id, app_secret, REGION_HOSTS[region]
    )
    await client.async_validate()


def _error_for(err: Exception) -> str:
    """Map a validation failure to a translated error key."""
    if isinstance(err, ImouConnectionError):
        return "cannot_connect"
    if isinstance(err, ImouAuthError):
        return "invalid_auth"
    return "unknown"


class ImouRobotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Imou Vacuum (Open Platform)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ImouOptionsFlow:
        """Return the options flow (room names)."""
        return ImouOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the App ID, App Secret and region."""
        errors: dict[str, str] = {}
        if user_input is not None:
            app_id = user_input[CONF_APP_ID].strip()
            app_secret = user_input[CONF_APP_SECRET].strip()
            region = user_input[CONF_REGION]
            await self.async_set_unique_id(app_id)
            self._abort_if_unique_id_configured()
            try:
                await _async_validate(self.hass, app_id, app_secret, region)
            except ImouError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating Imou credentials")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Imou Vacuum (Open Platform)",
                    data={
                        CONF_APP_ID: app_id,
                        CONF_APP_SECRET: app_secret,
                        CONF_REGION: region,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                {k: v for k, v in (user_input or {}).items() if k != CONF_APP_SECRET},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when Imou rejects the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new App Secret."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            app_secret = user_input[CONF_APP_SECRET].strip()
            try:
                await _async_validate(
                    self.hass,
                    entry.data[CONF_APP_ID],
                    app_secret,
                    entry.data.get(CONF_REGION, DEFAULT_REGION),
                )
            except ImouError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating Imou credentials")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_APP_SECRET: app_secret}
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )


class ImouOptionsFlow(OptionsFlow):
    """Let the user name the rooms of a robot and link them to areas."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._device_id: str | None = None
        # form label -> room id of the "link rooms to areas" form
        self._area_labels: dict[str, int] = {}
        # the map of the rooms in the form that was shown (``None``: plain room ids);
        # a submit is only valid while the robot's room map is still this one
        self._form_map: int | None = None

    @property
    def _coordinators(self) -> dict[str, ImouCoordinator]:
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        return runtime_data.coordinators if runtime_data is not None else {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the robot (only asked when there is more than one)."""
        coordinators = self._coordinators
        if not coordinators:
            return self.async_abort(reason="no_rooms")
        if user_input is not None and user_input[CONF_ROBOT] in coordinators:
            self._device_id = user_input[CONF_ROBOT]
            return await self.async_step_menu()
        if len(coordinators) == 1:
            self._device_id = next(iter(coordinators))
            return await self.async_step_menu()
        schema = vol.Schema(
            {
                vol.Required(CONF_ROBOT): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=device_id, label=item.device.name)
                            for device_id, item in coordinators.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between linking rooms to areas and the advanced names text."""
        coordinator = self._coordinators.get(self._device_id or "")
        if coordinator is None:
            return self.async_abort(reason="no_rooms")
        return self.async_show_menu(
            step_id="menu",
            menu_options=["room_areas", "rooms"],
            description_placeholders={"robot": coordinator.device.name},
        )

    @staticmethod
    def _robot_label(coordinator: ImouCoordinator) -> str:
        """Return the robot's name, with the name of its active map on a robot with several maps."""
        map_id = coordinator.room_map()
        name = coordinator.device.name
        return name if map_id is None else f"{name} ({coordinator.map_label(map_id)})"

    def _vacuum_entity_id(self, coordinator: ImouCoordinator) -> str | None:
        """Return the entity id of the robot's vacuum, if it is in the entity registry."""
        return er.async_get(self.hass).async_get_entity_id(
            "vacuum", DOMAIN, f"{coordinator.device.device_id}_vacuum"
        )

    async def async_step_room_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Link every room to an area: the room takes the area's name."""
        coordinator = self._coordinators.get(self._device_id or "")
        if coordinator is None:
            return self.async_abort(reason="no_rooms")
        if user_input is None:
            await coordinator.async_refresh_schedules()  # pick up new schedules
        elif coordinator.room_map() != self._form_map:
            return self.async_abort(reason="map_changed")
        known = coordinator.known_room_tags()
        if not known:
            return self.async_abort(reason="no_rooms")

        device_id = coordinator.device.device_id
        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        vacuum_id = self._vacuum_entity_id(coordinator)
        vacuum_entry = entity_registry.async_get(vacuum_id) if vacuum_id else None
        vacuum_options = dict(vacuum_entry.options.get("vacuum", {})) if vacuum_entry else {}

        map_id = self._form_map if user_input is not None else coordinator.room_map()
        if user_input is None:
            self._form_map = map_id  # with several maps: the rooms of the active map
            labels = room_area_labels(known, coordinator.room_overrides(), coordinator.room_label)
            self._area_labels = labels
            schema = vol.Schema({vol.Optional(label): AreaSelector() for label in labels})
            current = areas_by_room(
                vacuum_options.get("area_mapping"),
                known,
                {area.id for area in area_registry.async_list_areas()},
                map_id,
            )
            return self.async_show_form(
                step_id="room_areas",
                data_schema=self.add_suggested_values_to_schema(
                    schema,
                    {
                        label: current[room_id]
                        for label, room_id in labels.items()
                        if room_id in current
                    },
                ),
                description_placeholders={"robot": self._robot_label(coordinator)},
            )

        # the area of every room that was in the form; an unknown area counts as none
        chosen: dict[int, str | None] = {}
        area_names: dict[int, str] = {}
        for label, room_id in self._area_labels.items():
            if room_id not in known:
                continue
            area_id = user_input.get(label)
            area = area_registry.async_get_area(area_id) if area_id else None
            chosen[room_id] = area.id if area is not None else None
            if area is not None:
                area_names[room_id] = area.name

        # a. the room takes the name of its area; rooms without an area keep their name
        stored = dict(self.config_entry.options.get(CONF_ROOM_NAMES, {}))
        device_names = dict(stored.get(device_id, {}))
        device_names.update({room_key(map_id, room_id): name for room_id, name in area_names.items()})
        stored[device_id] = device_names

        # b. the link to the area (Home Assistant 2026.3 and newer, for "Clean area")
        if CLEAN_AREA is not None and vacuum_id is not None:
            overrides = room_overrides_for(device_names, map_id)
            # the names the coordinator will show once these options are saved
            new_names = segment_names(known, known, overrides, default_room_name)
            entity_registry.async_update_entity_options(
                vacuum_id,
                "vacuum",
                {
                    **vacuum_options,
                    "area_mapping": new_area_mapping(
                        vacuum_options.get("area_mapping"),
                        chosen,
                        {area.id for area in area_registry.async_list_areas()},
                        map_id,
                    ),
                    "last_seen_segments": new_last_seen_segments(
                        vacuum_options.get("last_seen_segments"),
                        {room_key(map_id, room_id): new_names[room_id] for room_id in chosen},
                    ),
                },
            )

        # c. save the names; the update listener reloads the entry when they changed
        return self.async_create_entry(
            data={**self.config_entry.options, CONF_ROOM_NAMES: stored}
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the room names of the chosen robot, one ``id = name`` per line."""
        coordinator = self._coordinators.get(self._device_id or "")
        if coordinator is None:
            return self.async_abort(reason="no_rooms")
        if user_input is None:
            await coordinator.async_refresh_schedules()  # pick up new schedules
            self._form_map = coordinator.room_map()
        elif coordinator.room_map() != self._form_map:
            return self.async_abort(reason="map_changed")
        known = coordinator.known_room_tags()
        if not known:
            return self.async_abort(reason="no_rooms")

        errors: dict[str, str] = {}
        if user_input is not None:
            text = user_input.get(CONF_ROOM_NAMES, "")
            try:
                names = parse_room_names(text)
            except ValueError:
                errors["base"] = "invalid_room_names"
            else:
                # only real user overrides are stored: a name equal to the default
                # or to the current automatic name may change later
                automatic = {
                    room_id: tag or default_room_name(room_id)
                    for room_id, tag in known.items()
                }
                stored = dict(self.config_entry.options.get(CONF_ROOM_NAMES, {}))
                # the names of the active map are replaced, those of other maps are kept
                stored[coordinator.device.device_id] = replace_room_names(
                    stored.get(coordinator.device.device_id, {}),
                    self._form_map,
                    {
                        room_id: name
                        for room_id, name in names.items()
                        if name not in (default_room_name(room_id), automatic.get(room_id))
                    },
                )
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_ROOM_NAMES: stored}
                )

        if user_input is not None:
            suggested = user_input.get(CONF_ROOM_NAMES, "")
        else:
            overrides = coordinator.room_overrides()
            suggested = format_room_names(
                {
                    room_id: overrides.get(room_id) or tag or default_room_name(room_id)
                    for room_id, tag in known.items()
                }
            )
        schema = vol.Schema(
            {vol.Optional(CONF_ROOM_NAMES): TextSelector(TextSelectorConfig(multiline=True))}
        )
        return self.async_show_form(
            step_id="rooms",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_ROOM_NAMES: suggested}
            ),
            description_placeholders={"robot": self._robot_label(coordinator)},
            errors=errors,
        )
