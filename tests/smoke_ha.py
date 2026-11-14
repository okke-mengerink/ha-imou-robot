"""Smoke test against real Home Assistant. No pytest needed.

Run from the repository root with, for example:

    uv run --python 3.14 --with "homeassistant==2026.10.2" python -I tests/smoke_ha.py

(`-I` ignores PYTHONPATH and the current directory, so the repo root is added to
`sys.path` below.) Three layers are checked:

1. every module imports, entities work on a fake coordinator fed with the
   anonymised L11-A values, translations/manifest are consistent;
2. the config-flow schema builds, validates and serialises;
3. the integration is set up in a bare Home Assistant core with a fake API
   client: config flow, entities, commands, polling interval, offline state,
   reauth, diagnostics and unload. No network is used.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import copy
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import traceback
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PKG = "custom_components.imou_vacuum"
PKG_DIR = ROOT / "custom_components" / "imou_vacuum"
MODEL_JSON = json.loads((ROOT / "tests/fixtures/l11a_model.json").read_text("utf-8"))
VALUES_JSON = json.loads((ROOT / "tests/fixtures/l11a_values.json").read_text("utf-8"))
RV1_MODEL_JSON = json.loads((ROOT / "tests/fixtures/rv1promax_model.json").read_text("utf-8"))
RV1_VALUES_JSON = json.loads((ROOT / "tests/fixtures/rv1promax_values.json").read_text("utf-8"))
RV1LPRO_MODEL_JSON = json.loads((ROOT / "tests/fixtures/rv1lpro_model.json").read_text("utf-8"))
RV1LPRO_VALUES_JSON = json.loads((ROOT / "tests/fixtures/rv1lpro_values.json").read_text("utf-8"))
RV1_CLEAN_LIST_JSON = json.loads((ROOT / "tests/fixtures/rv1promax_clean_list.json").read_text("utf-8"))
ICONS_JSON = json.loads((PKG_DIR / "icons.json").read_text("utf-8"))
STRINGS_JSON = json.loads((PKG_DIR / "strings.json").read_text("utf-8"))
EN_JSON = json.loads((PKG_DIR / "translations" / "en.json").read_text("utf-8"))

RESULTS: list[tuple[bool, str]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    """Record one check."""
    ok = bool(condition)
    RESULTS.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def mod(name: str) -> Any:
    return importlib.import_module(f"{PKG}.{name}")


def ha_clean_area() -> Any:
    """VacuumEntityFeature.CLEAN_AREA, or None on Home Assistant before 2026.3."""
    from homeassistant.components.vacuum import VacuumEntityFeature  # noqa: PLC0415

    return getattr(VacuumEntityFeature, "CLEAN_AREA", None)


# ---------------------------------------------------------------------------
# 1. imports
# ---------------------------------------------------------------------------
MODULES = [
    "api", "binary_sensor", "button", "commanded", "config_flow", "const", "coordinator",
    "diagnostics", "entity", "health", "model", "probe", "rooms", "select", "sensor", "switch", "vacuum",
]  # fmt: skip


def test_imports() -> None:
    import homeassistant.const as ha_const  # noqa: PLC0415

    print(f"Home Assistant {ha_const.__version__}, Python {sys.version.split()[0]}")
    importlib.import_module(PKG)
    for name in MODULES:
        try:
            mod(name)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            check(f"import {name}", False)
        else:
            check(f"import {name}", True)


# ---------------------------------------------------------------------------
# 2. entities on a fake coordinator
# ---------------------------------------------------------------------------
class FakeCoordinator:
    """Just enough of ImouCoordinator for entities; records commands."""

    def __init__(self, model_json: dict[str, Any], raw_values: dict[str, Any], online: bool = True, device_model: str = "L11-A") -> None:
        api, coordinator = mod("api"), mod("coordinator")
        self.model = mod("model").ThingsModel.from_api(model_json)
        self.device = api.ImouDevice(
            device_id="TESTDEVICE0001",
            product_id="PRODUCT_ID",
            name="RV-L11-A-TEST",
            model=device_model,
            status="online",
            version="0.4.50",
            brand="lechange",
        )
        self.poll_refs = self.model.poll_refs()
        self.data = coordinator.ImouRobotState(
            online=online, values=self.model.parse_values(raw_values)
        )
        self.reports = mod("model").ReportTracker()
        self.reports.update(online, self.data.values)  # the first poll
        self.last_update_success = True
        self.calls: list[tuple[Any, ...]] = []
        self.commanded: defaultdict[str, Any] = defaultdict(mod("commanded").CommandedValue)
        self.schedules: Any = None  # no schedules read yet
        self.sequence_rooms: Any = None
        self.rooms_cleaned: list[list[int]] = []
        self.listeners: list[Any] = []

    def can_switch_schedules(self) -> bool:
        return mod("coordinator").ImouCoordinator.can_switch_schedules(self)

    def can_clean_rooms(self) -> bool:
        return mod("coordinator").ImouCoordinator.can_clean_rooms(self)

    def room_names(self) -> dict[int, str]:
        return {}  # no schedules read yet, so no rooms

    def room_map(self) -> None:
        return None  # a robot with one map: plain room ids

    def is_multi_map(self) -> bool:
        return False

    async def async_check_rooms(self, map_ids: Any) -> None:
        self.checked_maps = list(map_ids)

    def set_values(self, **values: Any) -> None:
        self.data.values.update(values)

    def async_add_listener(self, update_callback: Any, *args: Any, **kwargs: Any) -> Any:
        self.listeners.append(update_callback)

        def remove() -> None:
            self.listeners.remove(update_callback)  # (a second call would fail, like in Home Assistant)

        return remove

    def first_online_poll(self, raw_values: dict[str, Any]) -> None:
        """The robot comes online: record the poll and call the listeners."""
        self.data = mod("coordinator").ImouRobotState(online=True, values=self.model.parse_values(raw_values))
        self.reports.update(True, self.data.values)
        for listener in list(self.listeners):
            listener()

    async def async_refresh(self) -> None:
        return None

    def async_update_listeners(self) -> None:
        return None

    async def async_set_properties(self, values: dict[str, Any]) -> None:
        self.calls.append(("set", dict(values)))

    async def async_set_level(self, identifier: str, level: int) -> None:
        self.calls.append(("level", identifier, level))

    async def async_control(self, service: str, inputs: dict[str, Any] | None = None) -> None:
        self.calls.append(("control", service, dict(inputs or {})))

    async def async_clean_rooms(self, room_ids: list[int]) -> None:
        self.rooms_cleaned.append(list(room_ids))


def make_entities(coordinator: Any) -> dict[str, list[Any]]:
    """Run every platform's async_setup_entry against the fake coordinator."""
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinators={"x": coordinator}), async_on_unload=lambda unsub: None)
    out: dict[str, list[Any]] = {}
    for platform in ("vacuum", "sensor", "binary_sensor", "switch", "select", "button"):
        collected: list[Any] = []

        def add(entities: Any, collected: list[Any] = collected, **_: Any) -> None:
            collected.extend(entities)

        asyncio.run(mod(platform).async_setup_entry(None, entry, add))
        out[platform] = collected
    return out


def no_rooms_model_for_buttons() -> dict[str, Any]:
    """The L11-A model without the partition_area input of clean_control."""
    model_json = copy.deepcopy(MODEL_JSON)
    for service in model_json["services"]:
        if service["identifier"] == "clean_control":
            service["inputData"] = [i for i in service["inputData"] if i["identifier"] != "partition_area"]
    return model_json


def by_key(entities: list[Any]) -> dict[str, Any]:
    return {e.unique_id.split("_", 1)[1]: e for e in entities}


def test_entities() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass  # noqa: PLC0415
    from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature  # noqa: PLC0415
    from homeassistant.const import EntityCategory  # noqa: PLC0415
    from homeassistant.exceptions import ServiceValidationError  # noqa: PLC0415

    const = mod("const")
    coordinator = FakeCoordinator(MODEL_JSON, VALUES_JSON["properties"])
    ents = make_entities(coordinator)
    total = sum(len(v) for v in ents.values())
    check("entity counts per platform (1/11/1/1/2/5)", [len(ents[p]) for p in ents] == [1, 11, 1, 1, 2, 5], str({p: len(v) for p, v in ents.items()}))
    expected_ids = sorted(f"TESTDEVICE0001_{key}" for key in ('battery', 'carpet_boost', 'cleaned_area', 'cleaning_time', 'empty_bin', 'error', 'filter', 'main_brush', 'mop_fitted', 'mop_water_level', 'schedules', 'sensors', 'side_brush', 'status', 'suction_level', 'vacuum', 'wifi_signal'))  # main (v0.4.x) behaviour
    check("L11-A has the unique ids of main plus the four reset buttons", sorted(e.unique_id for v in ents.values() for e in v) == sorted(expected_ids + [f"TESTDEVICE0001_reset_{p}" for p in ("side_brush", "main_brush", "filter", "sensors")]), str(sorted(e.unique_id for v in ents.values() for e in v)))
    check("21 entities with unique ids (the 17 of main plus four reset buttons)", total == 21 and len({e.unique_id for v in ents.values() for e in v}) == 21)

    # --- Clean <room> buttons ---------------------------------------------
    room_coordinator = FakeCoordinator(MODEL_JSON, VALUES_JSON["properties"])
    room_coordinator.room_names = lambda: {3: "Room 3", 4: "Kantoor"}  # type: ignore[method-assign]
    room_buttons = make_entities(room_coordinator)["button"]
    clean = {b.room_id: b for b in room_buttons if hasattr(b, "room_id")}
    check("room buttons: one per room, unique id <device id>_clean_room_<room id>, translation key clean_room, no entity category", sorted(clean) == [3, 4] and [clean[r].unique_id for r in (3, 4)] == ["TESTDEVICE0001_clean_room_3", "TESTDEVICE0001_clean_room_4"] and all(b.translation_key == "clean_room" and b.entity_category is None for b in clean.values()))
    check("room buttons: a default name reads 'room 3', a real name is kept", clean[3].translation_placeholders == {"room": "room 3"} and clean[4].translation_placeholders == {"room": "Kantoor"}, str([b.translation_placeholders for b in clean.values()]))
    asyncio.run(clean[4].async_press())
    check("room button press cleans only that room", room_coordinator.rooms_cleaned == [[4]], str(room_coordinator.rooms_cleaned))
    no_rooms_coordinator = FakeCoordinator(no_rooms_model_for_buttons(), VALUES_JSON["properties"])
    no_rooms_coordinator.room_names = room_coordinator.room_names  # type: ignore[method-assign]
    check("a model without room cleaning (no partition_area input) gets no room buttons", not [b for b in make_entities(no_rooms_coordinator)["button"] if hasattr(b, "room_id")])

    # --- vacuum -----------------------------------------------------------
    vac = ents["vacuum"][0]
    expected_features = (
        VacuumEntityFeature.START | VacuumEntityFeature.PAUSE | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME | VacuumEntityFeature.LOCATE | VacuumEntityFeature.STATE
        | VacuumEntityFeature.FAN_SPEED | VacuumEntityFeature.SEND_COMMAND
    )  # fmt: skip
    clean_area = ha_clean_area()
    if clean_area is not None:
        expected_features |= clean_area
    import homeassistant.const as ha_const  # noqa: PLC0415

    ha_version = tuple(int(x) for x in ha_const.__version__.split(".")[:2])
    check("vacuum features START|PAUSE|STOP|RETURN_HOME|LOCATE|STATE|FAN_SPEED|SEND_COMMAND (+ CLEAN_AREA where Home Assistant has it)", vac.supported_features == expected_features, str(vac.supported_features))
    check("Home Assistant has CLEAN_AREA exactly from 2026.3", (clean_area is not None) == (ha_version >= (2026, 3)), str(ha_version))
    unwanted = 0
    for name in ("CLEAN_SPOT", "MAP", "TURN_ON", "TURN_OFF", "STATUS"):
        unwanted |= int(getattr(VacuumEntityFeature, name, 0))
    check("vacuum has no CLEAN_SPOT/MAP features (the robot reports no map)", not (int(vac.supported_features) & unwanted))
    no_rooms_model = copy.deepcopy(MODEL_JSON)
    for service in no_rooms_model["services"]:
        if service["identifier"] == "clean_control":
            service["inputData"] = [i for i in service["inputData"] if i["identifier"] != "partition_area"]
    no_rooms_vac = make_entities(FakeCoordinator(no_rooms_model, VALUES_JSON["properties"]))["vacuum"][0]
    check("a model whose clean_control lacks partition_area gets no CLEAN_AREA", int(no_rooms_vac.supported_features) == int(expected_features) & ~int(clean_area or 0), str(no_rooms_vac.supported_features))
    check("vacuum has the translation key 'robot' (fan speed names)", vac.translation_key == "robot")
    battery = mod("sensor")._battery  # noqa: SLF001
    check("battery: absolute value clamped to 0..100 (-38 -> 38, 41 -> 41, 150 and -150 -> 100, garbage -> None)", [battery(v) for v in (-38, 41, 150, -150, 0, None, "x")] == [38, 41, 100, 100, 0, None, None], str([battery(v) for v in (-38, 41, 150, -150, 0, None, "x")]))
    check("vacuum activity DOCKED for work_status 3", vac.activity == VacuumActivity.DOCKED, str(vac.activity))
    check("vacuum state string is 'docked'", vac.state == "docked", str(vac.state))
    check("vacuum attributes work_status/error", vac.extra_state_attributes == {"work_status": 3, "error": 0}, str(vac.extra_state_attributes))
    check("vacuum has_entity_name and main-entity name None", vac.has_entity_name and vac.name is None)

    expected_activity = {
        0: VacuumActivity.IDLE, 1: VacuumActivity.PAUSED, 13: VacuumActivity.PAUSED,
        2: VacuumActivity.RETURNING, 3: VacuumActivity.DOCKED, 11: VacuumActivity.DOCKED,
        6: VacuumActivity.ERROR,
        14: VacuumActivity.RETURNING, 15: VacuumActivity.DOCKED, 16: VacuumActivity.DOCKED,
        18: VacuumActivity.PAUSED, 19: VacuumActivity.PAUSED,
        **{n: VacuumActivity.CLEANING for n in (4, 5, 7, 8, 9, 10, 12, 17)},
    }  # fmt: skip
    wrong = {}
    for status, activity in expected_activity.items():
        coordinator.set_values(work_status=status)
        if vac.activity != activity:
            wrong[status] = vac.activity
    check("work_status -> VacuumActivity table (0..19)", not wrong, str(wrong))
    coordinator.set_values(work_status=99)
    check("unknown work_status -> activity None (state unknown)", vac.activity is None and vac.state in (None, "unknown"))
    coordinator.set_values(work_status=3)

    # --- vacuum send_command (generic, model-validated) ---------------------
    async def send(command: str, params: Any = None) -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        await vac.async_send_command(command, params)
        return list(coordinator.calls)

    full = {"status": 1, "mode": 1, "plan": 0, "partition_area": "[2]"}
    check("send_command passes identifiers and a text value unchanged", asyncio.run(send("clean_control", dict(full))) == [("control", "clean_control", full)])
    as_list = {**full, "partition_area": [2]}
    check("send_command passes a list value unchanged (no JSON encoding)", asyncio.run(send("clean_control", dict(as_list))) == [("control", "clean_control", as_list)])
    check("send_command matches command and keys case-insensitively", asyncio.run(send("Clean_Control", {"Mode": 1})) == [("control", "Clean_Control", {"Mode": 1})])
    check("send_command without params is allowed", asyncio.run(send("location")) == [("control", "location", {})])

    async def bad_send(command: str, params: Any) -> tuple[str | None, dict[str, str] | None, list[Any]]:
        coordinator.calls.clear()
        try:
            await vac.async_send_command(command, params)
        except ServiceValidationError as err:
            return err.translation_key, err.translation_placeholders, list(coordinator.calls)
        return None, None, list(coordinator.calls)

    check("send_command unknown command -> unknown_command, nothing sent", asyncio.run(bad_send("nope", None)) == ("unknown_command", {"command": "nope"}, []))
    check("send_command unknown input key -> invalid_command_params, nothing sent", asyncio.run(bad_send("clean_control", {"mode": 1, "bogus": 2, "x": 3})) == ("invalid_command_params", {"command": "clean_control", "params": "bogus, x"}, []))
    check("send_command list as params -> invalid_command_params, nothing sent", asyncio.run(bad_send("clean_control", [1, 2])) == ("invalid_command_params", {"command": "clean_control", "params": "list"}, []))

    # --- vacuum commands at identifier level ------------------------------
    async def commands() -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        coordinator.set_values(work_status=3)
        await vac.async_start()
        coordinator.set_values(work_status=1)
        await vac.async_start()
        coordinator.set_values(work_status=13)
        await vac.async_start()
        coordinator.set_values(work_status=7)
        await vac.async_pause()
        await vac.async_stop()
        await vac.async_return_to_base()
        await vac.async_locate()
        coordinator.set_values(work_status=3)
        return list(coordinator.calls)

    calls = asyncio.run(commands())
    check(
        "vacuum commands call the right services",
        calls == [
            ("control", "clean_control", {"status": 1, "mode": 0, "plan": 0}),
            ("control", "clean_control", {"status": 2}),
            ("control", "clean_control", {"status": 2}),
            ("control", "clean_control", {"status": 0}),
            ("control", "clean_control", {"status": 3}),
            ("set", {"back_charging": 1}),
            ("control", "location", {}),
        ],
        str(calls),
    )  # fmt: skip

    # --- sensors ----------------------------------------------------------
    sensors = by_key(ents["sensor"])
    battery = sensors["battery"]
    check("battery device class/unit/state class", battery.device_class == SensorDeviceClass.BATTERY and battery.native_unit_of_measurement == "%" and str(battery.state_class) in ("measurement", "SensorStateClass.MEASUREMENT"))
    clamps = {}
    for raw, want in ((1, 1), (150, 100), (-20, 20), (-150, 100), (0, 0), (100, 100), ("55", 55), (None, None)):
        coordinator.set_values(power=raw)
        if battery.native_value != want:
            clamps[raw] = battery.native_value
    check("battery is the absolute value clamped to 0..100 (1,150,-20,-150,0,100,'55',None)", not clamps, str(clamps))
    coordinator.set_values(power=1)
    check("battery observed value is 1", battery.native_value == 1)

    status = sensors["status"]
    check("status sensor is an ENUM", status.device_class == SensorDeviceClass.ENUM)
    check("status options = all 20 translation keys in value order", status.options == [const.WORK_STATUS_KEYS[n] for n in range(20)], str(status.options))
    bad = {}
    for n in range(14):
        coordinator.set_values(work_status=n)
        if status.native_value != const.WORK_STATUS_KEYS[n]:
            bad[n] = status.native_value
    check("status enum maps every work_status", not bad, str(bad))
    coordinator.set_values(work_status=3)
    check("status 3 -> 'charging'", status.native_value == "charging")
    coordinator.set_values(work_status=99)
    check("status outside the model enum -> None (no crash)", status.native_value is None)
    coordinator.set_values(work_status=3)

    error = sensors["error"]
    check("error options 0..11 in value order", error.options == [const.ERROR_KEYS[n] for n in range(12)], str(error.options))
    cases = [
        (None, "none"),  # robot reports no error info
        ({"errcode": 0, "valid": True}, "none"),
        ({"errcode": 5, "valid": True}, "cliff"),
        ({"errcode": 5}, "cliff"),
        ({"errcode": 5, "valid": False}, "none"),  # flagged invalid: ignored
        ({"errcode": 11, "valid": True}, "main_brush"),
        ({"errcode": 42, "valid": True}, None),  # not in the model's enum
    ]
    bad = {}
    for info, want in cases:
        if info is None:
            coordinator.data.values.pop("errorinfo", None)
        else:
            coordinator.set_values(errorinfo=info)
        if error.native_value != want:
            bad[json.dumps(info)] = error.native_value
    check("error sensor values", not bad, str(bad))
    coordinator.data.values.pop("errorinfo", None)
    check("vacuum 'error' attribute follows errcode", ents["vacuum"][0].extra_state_attributes["error"] == 0)

    wifi = sensors["wifi_signal"]
    check("wifi enum 3 -> 'fair', diagnostic", wifi.native_value == "fair" and wifi.entity_category == EntityCategory.DIAGNOSTIC and wifi.options == ["abnormal", "no_network", "poor", "fair", "good"])
    check("cleaned area 0 m2 / cleaning time 0 min", sensors["cleaned_area"].native_value == 0 and sensors["cleaned_area"].native_unit_of_measurement == "m²" and sensors["cleaning_time"].native_value == 0 and sensors["cleaning_time"].native_unit_of_measurement == "min")
    consumables = [sensors[k] for k in ("side_brush", "main_brush", "filter", "sensors")]
    check("4 consumable sensors: hours, diagnostic, value 0", all(s.native_value == 0 and s.native_unit_of_measurement == "h" and s.entity_category == EntityCategory.DIAGNOSTIC for s in consumables))
    check(
        "consumable sensors: duration, measurement, display precision 1",
        all(s.device_class == SensorDeviceClass.DURATION and s.state_class == SensorStateClass.MEASUREMENT and s.suggested_display_precision == 1 for s in consumables),
    )  # fmt: skip
    coordinator.set_values(life={"brush_time": 12, "main_brush_time": 34, "rosebox_time": 56, "sensor_time": 78})
    check("consumables are minutes from the robot, native value in hours (12, 34, 56, 78 min)", [s.native_value for s in consumables] == [0.2, 0.5667, 0.9333, 1.3], str([s.native_value for s in consumables]))
    check("34 raw (the real run: 34 min) gives 0.5667 h native, 0.6 h displayed", sensors["main_brush"].native_value == 0.5667 and f"{sensors['main_brush'].native_value:.1f}" == "0.6")
    coordinator.set_values(life={"brush_time": "90", "main_brush_time": None, "rosebox_time": "x", "sensor_time": 600})
    check("raw values are coerced; unusable ones give no value", [s.native_value for s in consumables] == [1.5, None, None, 10.0], str([s.native_value for s in consumables]))
    coordinator.set_values(life={"brush_time": 0, "main_brush_time": 0, "rosebox_time": 0, "sensor_time": 0})

    # unknown enum values: unknown_<n> + model label
    raw_model = copy.deepcopy(MODEL_JSON)
    for prop in raw_model["properties"]:
        if prop["identifier"] == "work_status":
            prop["dataType"]["specs"]["list"].append({"value": "20", "desc": "测试"})
    coordinator2 = FakeCoordinator(raw_model, VALUES_JSON["properties"])
    status2 = by_key(make_entities(coordinator2)["sensor"])["status"]
    coordinator2.set_values(work_status=20)
    check("unknown model enum value -> option unknown_20", status2.options[-1] == "unknown_20" and status2.native_value == "unknown_20")
    check("unknown value exposes model_label attribute", status2.extra_state_attributes == {"raw_value": 20, "model_label": "测试"}, str(status2.extra_state_attributes))
    coordinator2.set_values(work_status=3)
    check("known value has no extra attributes", status2.extra_state_attributes is None)

    # --- binary sensor, switches, button ---------------------------
    mop = ents["binary_sensor"][0]
    check("mop fitted is off for 0", mop.is_on is False)
    coordinator.set_values(mopenable=1)
    check("mop fitted is on for 1", mop.is_on is True)
    coordinator.set_values(mopenable=0)

    switches = by_key(ents["switch"])
    check(
        "only the switch whose setting the robot reports is enabled by default (11900 yes)",
        {k: sw.entity_registry_enabled_default for k, sw in switches.items()} == {"carpet_boost": True},
        str({k: sw.entity_registry_enabled_default for k, sw in switches.items()}),
    )  # fmt: skip
    check(
        "switch attribute reported_by_device",
        {k: sw.extra_state_attributes for k, sw in switches.items()} == {"carpet_boost": {"reported_by_device": True}},
    )  # fmt: skip
    check("the switch is available and shows the reported value", all(sw.available for sw in switches.values()) and switches["carpet_boost"].is_on is True, str({k: sw.available for k, sw in switches.items()}))
    unreported = by_key(make_entities(FakeCoordinator(MODEL_JSON, {k: v for k, v in VALUES_JSON["properties"].items() if k != "11900"}))["switch"])
    check("a robot that does not report carpet boost: disabled by default, still available (unknown)", not unreported["carpet_boost"].entity_registry_enabled_default and unreported["carpet_boost"].available and unreported["carpet_boost"].is_on is None and unreported["carpet_boost"].extra_state_attributes == {"reported_by_device": False})
    # offline at setup: nothing is created (so nothing can be wrongly enabled) until the first online poll
    offline = FakeCoordinator(MODEL_JSON, {}, online=False)
    added: list[Any] = []
    unloads: list[Any] = []
    offline_entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinators={"x": offline}), async_on_unload=unloads.append)
    asyncio.run(mod("switch").async_setup_entry(None, offline_entry, lambda entities, **_: added.extend(entities)))
    check("offline at the first poll: no switches yet, waiting for the robot", not added and len(offline.listeners) == 1)
    offline.listeners[0]()
    check("a listener call while the robot is still offline adds nothing", not added and len(offline.listeners) == 1)
    offline.first_online_poll(VALUES_JSON["properties"])
    late = {e.unique_id.split("_", 1)[1]: e for e in added}
    check("the first online poll creates the switches once, and stops listening", sorted(late) == ["carpet_boost"] and not offline.listeners and len(added) == 1, str(sorted(late)))
    check("... enabled by default from that poll: the reported one", {k: sw.entity_registry_enabled_default for k, sw in late.items()} == {"carpet_boost": True})
    for unload in unloads:
        unload()  # the unload after the listener removed itself must not fail
    check("unloading after the first poll is safe", True)
    unloaded: list[Any] = []
    fresh_offline = FakeCoordinator(MODEL_JSON, {}, online=False)
    asyncio.run(mod("switch").async_setup_entry(None, SimpleNamespace(runtime_data=SimpleNamespace(coordinators={"x": fresh_offline}), async_on_unload=unloaded.append), lambda entities, **_: None))
    for unload in unloaded:
        unload()
    check("unloading before the robot came online removes the listener", not fresh_offline.listeners)
    check("switches are still created for every identifier in the model", set(switches) == {"carpet_boost"})

    async def switch_calls() -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        await switches["carpet_boost"].async_turn_off()
        return list(coordinator.calls)

    calls = asyncio.run(switch_calls())
    check("switches write 1/0 by identifier", calls == [("set", {"rug_press": 0})], str(calls))
    check("commanded value is shown until the cloud reports it", switches["carpet_boost"].is_on is False)

    # --- fan speed (vacuum) ---------------------------------------------------
    check("fan speed list is quiet/standard/max", vac.fan_speed_list == ["quiet", "standard", "max"], str(vac.fan_speed_list))
    bad = {}
    for raw, want in ((1, "quiet"), (2, "standard"), (3, "max"), (0, "unknown_0"), (4, "unknown_4"), (99, "unknown_99"), ("2", "standard"), (None, None)):
        if raw is None:
            coordinator.data.values.pop("windpower", None)
        else:
            coordinator.set_values(windpower=raw)
        if vac.fan_speed != want:
            bad[raw] = vac.fan_speed
    check("fan_speed maps WindPower 1/2/3 and shows other values as unknown_<n>", not bad, str(bad))
    coordinator.set_values(windpower=2)
    check("the observed WindPower 2 is 'standard'", vac.fan_speed == "standard")

    async def fan_calls() -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        await vac.async_set_fan_speed("quiet")
        await vac.async_set_fan_speed("max")
        return list(coordinator.calls)

    calls = asyncio.run(fan_calls())
    check("set_fan_speed writes the WindPower level", calls == [("level", "windpower", 1), ("level", "windpower", 3)], str(calls))
    check("a commanded fan speed is shown until the cloud reports it", vac.fan_speed == "max")

    async def bad_fan_speed() -> bool:
        try:
            await vac.async_set_fan_speed("turbo")
        except ServiceValidationError:
            return True
        return False

    check("an unknown fan speed is rejected", asyncio.run(bad_fan_speed()))
    coordinator.commanded.clear()

    # --- selects: suction and water level ------------------------------------------
    selects = by_key(ents["select"])
    suction, water = selects["suction_level"], selects["mop_water_level"]
    check("select options", suction.options == ["quiet", "standard", "max"] and water.options == ["low", "medium", "high"], f"{suction.options} {water.options}")
    check("observed levels 2/2 -> standard/medium", (suction.current_option, water.current_option) == ("standard", "medium"))
    bad = {}
    for raw, want in ((1, ("quiet", "low")), (3, ("max", "high")), (2, ("standard", "medium"))):
        coordinator.set_values(windpower=raw, water=raw)
        if (suction.current_option, water.current_option) != want:
            bad[raw] = (suction.current_option, water.current_option)
    check("selects map 1/2/3 to quiet/standard/max and low/medium/high", not bad, str(bad))
    bad = {}
    for raw in (0, 4, 99):
        coordinator.set_values(windpower=raw, water=raw)
        want = f"unknown_{raw}"
        if not (suction.current_option == want == water.current_option and suction.options == ["quiet", "standard", "max", want] and water.options == ["low", "medium", "high", want]):
            bad[raw] = (suction.current_option, suction.options)
    check("unknown raw values show as unknown_<n> (and are offered while current)", not bad, str(bad))
    coordinator.data.values.pop("windpower", None)
    coordinator.data.values.pop("water", None)
    check("no reported level -> unknown state, no extra option", suction.current_option is None and suction.options == ["quiet", "standard", "max"])
    coordinator.set_values(windpower=2, water=2)

    async def select_calls() -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        await suction.async_select_option("max")
        await water.async_select_option("low")
        return list(coordinator.calls)

    calls = asyncio.run(select_calls())
    check("selects write the level by identifier", calls == [("level", "windpower", 3), ("level", "water", 1)], str(calls))
    check("commanded levels are shown (suction shared with the vacuum fan speed)", (suction.current_option, water.current_option, vac.fan_speed) == ("max", "low", "max"))

    async def bad_select() -> bool:
        try:
            await suction.async_select_option("unknown_5")
        except ServiceValidationError:
            return True
        return False

    check("selecting an unknown level is rejected", asyncio.run(bad_select()))
    coordinator.commanded.clear()

    button = ents["button"][0]
    calls = []

    async def press() -> None:
        coordinator.calls.clear()
        await button.async_press()
        calls.extend(coordinator.calls)

    asyncio.run(press())
    check("empty bin button calls service 'dust'", calls == [("control", "dust", {})], str(calls))

    # --- device info / names -------------------------------------------------
    info = vac.device_info
    check("DeviceInfo identifiers/name/model/manufacturer/sw_version", info["identifiers"] == {("imou_vacuum", "TESTDEVICE0001")} and info["name"] == "RV-L11-A-TEST" and info["model"] == "L11-A" and info["manufacturer"] == "Imou" and info["sw_version"] == "0.4.50", str(info))
    check("every entity has has_entity_name and a translation_key (except the vacuum)", all(e.has_entity_name for v in ents.values() for e in v) and all(e.translation_key for p, v in ents.items() if p != "vacuum" for e in v))

    # --- availability --------------------------------------------------------
    coordinator.data.online = False
    check("entities unavailable when the robot is not online", all(not e.available for v in ents.values() for e in v))
    coordinator.data.online = True
    coordinator.last_update_success = False
    check("entities unavailable after a failed update", all(not e.available for v in ents.values() for e in v))
    coordinator.last_update_success = True
    check("entities available again, also the settings the robot never reported (the L11-A never reports auto-empty)", all(e.available for v in ents.values() for e in v))

    # --- only entities whose identifiers exist -----------------------------------
    slim = copy.deepcopy(MODEL_JSON)
    drop = {"rug_press", "life", "mopEnable", "WindPower", "wifi_rssi_sdj", "errorinfo"}
    slim["properties"] = [p for p in slim["properties"] if p["identifier"] not in drop]
    slim["services"] = [s for s in slim["services"] if s["identifier"] not in ("dust", "location")]
    slim_ents = make_entities(FakeCoordinator(slim, VALUES_JSON["properties"]))
    keys = {e.unique_id.split("_", 1)[1] for v in slim_ents.values() for e in v}
    check("entities only for identifiers in the model", not keys & {"carpet_boost", "side_brush", "main_brush", "filter", "sensors", "mop_fitted", "suction_level", "wifi_signal", "error", "empty_bin"} and {"vacuum", "battery", "status", "mop_water_level"} <= keys, str(sorted(keys)))
    slim_vac = slim_ents["vacuum"][0]
    check("vacuum without 'location' service has no LOCATE, without WindPower no FAN_SPEED", not slim_vac.supported_features & VacuumEntityFeature.LOCATE and not slim_vac.supported_features & VacuumEntityFeature.FAN_SPEED and bool(slim_vac.supported_features & VacuumEntityFeature.START))


def test_reset_buttons() -> None:
    from homeassistant.const import EntityCategory  # noqa: PLC0415

    def reset_buttons(model_json: dict[str, Any], device_model: str) -> tuple[Any, dict[str, Any]]:
        coordinator = FakeCoordinator(model_json, VALUES_JSON["properties"], device_model=device_model)
        return coordinator, {b.unique_id: b for b in make_entities(coordinator)["button"] if "_reset_" in b.unique_id}

    parts = ("side_brush", "main_brush", "filter", "sensors")
    ids = [f"TESTDEVICE0001_reset_{part}" for part in parts]
    coordinator, l11a = reset_buttons(MODEL_JSON, "L11-A")
    check("L11-A: four reset buttons, unique id <device id>_reset_<part>", sorted(l11a) == sorted(ids), str(sorted(l11a)))
    check("L11-A: reset buttons are enabled by default, configuration category, translation key reset_<part>", all(b.entity_registry_enabled_default and b.entity_category is EntityCategory.CONFIG and b.translation_key == f"reset_{p}" for p, b in zip(parts, (l11a[i] for i in ids))))
    asyncio.run(l11a["TESTDEVICE0001_reset_sensors"].async_press())
    check("pressing Reset sensors sends reset_parts with type 3", coordinator.calls == [("control", "reset_parts", {"type": 3})], str(coordinator.calls))
    coordinator.calls.clear()
    asyncio.run(l11a["TESTDEVICE0001_reset_side_brush"].async_press())
    check("pressing Reset side brush sends type 0", coordinator.calls[0] == ("control", "reset_parts", {"type": 0}), str(coordinator.calls))
    for json_model, name in ((RV1_MODEL_JSON, "RV1 Pro Max"), (RV1LPRO_MODEL_JSON, "RV1L Pro")):
        rv1_coordinator, rv1 = reset_buttons(json_model, name)
        check(f"{name}: four reset buttons, all disabled by default", sorted(rv1) == sorted(ids) and not any(b.entity_registry_enabled_default for b in rv1.values()), str({k: b.entity_registry_enabled_default for k, b in rv1.items()}))
        asyncio.run(rv1["TESTDEVICE0001_reset_sensors"].async_press())
        check(f"{name}: a reset button still sends type 3 (its enum value)", rv1_coordinator.calls[0] == ("control", "reset_parts", {"type": 3}), str(rv1_coordinator.calls))
    no_service = copy.deepcopy(MODEL_JSON)
    no_service["services"] = [s for s in no_service["services"] if s["identifier"] != "reset_parts"]
    check("a model without reset_parts gets no reset buttons", not reset_buttons(no_service, "L11-A")[1])
    unknown_labels = copy.deepcopy(MODEL_JSON)
    next(s for s in unknown_labels["services"] if s["identifier"] == "reset_parts")["inputData"][0]["dataType"]["specs"]["list"] = [{"value": "0", "desc": "Brush"}]
    check("reset_parts without a matching label gets no button (no numeric fallback)", not reset_buttons(unknown_labels, "L11-A")[1])
    check("reset button texts and icons exist", all(json.loads((ROOT / "custom_components/imou_vacuum" / f).read_text("utf-8"))["entity"]["button"][f"reset_{p}"] for f in ("strings.json", "icons.json") for p in parts))


def test_model_adaptive_entities() -> None:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass  # noqa: PLC0415
    from homeassistant.components.vacuum import VacuumActivity  # noqa: PLC0415

    # Charging binary sensor: only for a model with power_status
    rv1_values = {**RV1_VALUES_JSON["properties"], "112200": 1}
    rv1 = FakeCoordinator(RV1_MODEL_JSON, rv1_values)
    rv1_ents = make_entities(rv1)
    charging = by_key(rv1_ents["binary_sensor"]).get("charging")
    check("RV1 Pro Max: Charging binary sensor exists", charging is not None, str(list(by_key(rv1_ents["binary_sensor"]))))
    if charging is not None:
        check("Charging: device class battery_charging, translation key charging, on for power_status 1", charging.device_class == BinarySensorDeviceClass.BATTERY_CHARGING and charging.entity_description.translation_key == "charging" and charging.is_on is True, f"{charging.device_class} {charging.is_on}")
        rv1.set_values(power_status=0)
        check("Charging: off for power_status 0", charging.is_on is False)
        rv1.set_values(power_status=2)
        check("Charging: off for power_status 2 (abnormal)", charging.is_on is False)
        no_status = {k: v for k, v in rv1_values.items() if k != "112200"}
        rv1_bare = FakeCoordinator(RV1_MODEL_JSON, no_status)
        bare_charging = by_key(make_entities(rv1_bare)["binary_sensor"])["charging"]
        check("Charging without a power_status value: the battery sign decides (-38 is charging)", bare_charging.is_on is True, str(bare_charging.is_on))
        rv1_bare.set_values(power=38)
        check("Charging without a power_status value: a positive battery is unknown, not off", bare_charging.is_on is None)
        rv1_bare.set_values(power=None)
        check("Charging without power_status and battery: unknown", bare_charging.is_on is None)
        rv1_bare.set_values(power=-38, power_status=0)
        check("Charging: a power_status value wins over the battery sign", bare_charging.is_on is False)

    # dock sensors: created once the robot was seen online; disabled by default and unavailable
    # when it never reports station_status (RV1 Pro Max), enabled when it does
    dock_keys = ("dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag")

    def dock_entities(coordinator: Any) -> dict[str, Any]:
        out = make_entities(coordinator)
        return {k: e for k, e in {**by_key(out["binary_sensor"]), **by_key(out["sensor"])}.items() if k in dock_keys}

    silent = dock_entities(FakeCoordinator(RV1_MODEL_JSON, RV1_VALUES_JSON["properties"]))
    check("RV1 Pro Max (never reports station_status): the four dock entities exist but are disabled by default", sorted(silent) == sorted(dock_keys) and not any(e.entity_registry_enabled_default for e in silent.values()), str({k: e.entity_registry_enabled_default for k, e in silent.items()}))
    check("... and they stay available (state unknown), not unavailable", all(e.available for e in silent.values()))
    dock = {"121100": {"121101": True, "121102": 0, "121103": 0, "121104": 0}}
    loud = dock_entities(FakeCoordinator(RV1_MODEL_JSON, {**RV1_VALUES_JSON["properties"], **dock}))
    check("a robot that reports station_status (RV1L Pro): the dock entities are still disabled by default (no model is verified), and available", all(not e.entity_registry_enabled_default and e.available for e in loud.values()) and len(loud) == 4)
    waiting = FakeCoordinator(RV1_MODEL_JSON, {}, online=False)
    waiting_added: dict[str, list[Any]] = {"sensor": [], "binary_sensor": []}
    for platform, collected in waiting_added.items():
        waiting_entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinators={"x": waiting}), async_on_unload=lambda unsub: None)
        asyncio.run(mod(platform).async_setup_entry(None, waiting_entry, lambda entities, collected=collected, **_: collected.extend(entities)))
    waiting_keys = {e.unique_id.split("_", 1)[1] for v in waiting_added.values() for e in v}
    check("a robot that is offline at setup: no dock entities yet (the rest is there), two listeners wait", not waiting_keys & set(dock_keys) and "battery" in waiting_keys and len(waiting.listeners) == 2, str(waiting_keys))
    waiting.first_online_poll(RV1_VALUES_JSON["properties"])
    late_dock = {e.unique_id.split("_", 1)[1]: e for v in waiting_added.values() for e in v if e.unique_id.split("_", 1)[1] in dock_keys}
    check("... the first online poll adds them, disabled by default, and the listeners are gone", sorted(late_dock) == sorted(dock_keys) and not any(e.entity_registry_enabled_default for e in late_dock.values()) and not waiting.listeners, str(sorted(late_dock)))
    mop = by_key(make_entities(FakeCoordinator(RV1_MODEL_JSON, RV1_VALUES_JSON["properties"]))["binary_sensor"])["mop_fitted"]
    check("Mop fitted has the translation key mop_fitted (states Fitted / Not fitted in the translations)", mop.translation_key == "mop_fitted" and EN_JSON["entity"]["binary_sensor"]["mop_fitted"]["state"] == {"on": "Fitted", "off": "Not fitted"} and STRINGS_JSON == EN_JSON)
    l11a_ents = make_entities(FakeCoordinator(MODEL_JSON, VALUES_JSON["properties"]))
    check("L11-A: no Charging binary sensor", "charging" not in by_key(l11a_ents["binary_sensor"]), str(list(by_key(l11a_ents["binary_sensor"]))))

    # a model that numbers work_status differently (reversed) maps by label
    renumbered = copy.deepcopy(MODEL_JSON)
    for item in next(p for p in renumbered["properties"] if p["identifier"] == "work_status")["dataType"]["specs"]["list"]:
        item["value"] = str(13 - int(item["value"]))
    fake = FakeCoordinator(renumbered, {**VALUES_JSON["properties"], "12300": 10})  # 10 = charging here
    ents = make_entities(fake)
    vac = ents["vacuum"][0]
    status = by_key(ents["sensor"])["status"]
    check("renumbered work_status: value 10 is charging -> docked", vac.activity == VacuumActivity.DOCKED and status.native_value == "charging", f"{vac.activity} {status.native_value}")
    expected = {13: VacuumActivity.IDLE, 12: VacuumActivity.PAUSED, 0: VacuumActivity.PAUSED, 11: VacuumActivity.RETURNING, 6: VacuumActivity.CLEANING, 7: VacuumActivity.ERROR, 1: VacuumActivity.CLEANING}
    wrong = {}
    for value, activity in expected.items():
        fake.set_values(work_status=value)
        if vac.activity != activity:
            wrong[value] = vac.activity
    check("renumbered work_status: activity follows the labels", not wrong, str(wrong))
    check("renumbered work_status: status options start with the model's own order", status.options[0] == "return_paused" and status.options[13] == "standby" and len(status.options) == 20, str(status.options))
    fake.set_values(work_status=12)  # paused in this numbering
    asyncio.run(vac.async_start())
    check("renumbered work_status: start while paused continues", fake.calls[-1] == ("control", "clean_control", {"status": 2}), str(fake.calls[-1]))
    fake.set_values(work_status=10)
    asyncio.run(vac.async_start())
    check("renumbered work_status: start while charging starts a new run", fake.calls[-1][2].get("status") == 1, str(fake.calls[-1]))

    # an unknown label gives unknown_<n> with the raw value and the model label
    odd = copy.deepcopy(MODEL_JSON)
    next(p for p in odd["properties"] if p["identifier"] == "work_status")["dataType"]["specs"]["list"][5]["desc"] = "新状态"
    fake = FakeCoordinator(odd, {**VALUES_JSON["properties"], "12300": 5})
    ents = make_entities(fake)
    status = by_key(ents["sensor"])["status"]
    check("unknown label: unknown_5 with raw value and model label", status.native_value == "unknown_5" and status.extra_state_attributes == {"raw_value": 5, "model_label": "新状态"} and "unknown_5" in status.options and ents["vacuum"][0].activity is None, f"{status.native_value} {status.extra_state_attributes}")


def test_level_inputs() -> None:
    """The WindWater inputs: the new level plus the other one's current value."""
    coordinator_cls = mod("coordinator").ImouCoordinator
    const = mod("const")
    model = mod("model").ThingsModel.from_api(MODEL_JSON)
    commanded_cls = mod("commanded").CommandedValue

    def fake(values: dict[str, Any] | None = None, model_obj: Any = model, data: bool = True) -> Any:
        values = {"windpower": 2, "water": 2} if values is None else values
        return SimpleNamespace(model=model_obj, data=SimpleNamespace(values=dict(values)) if data else None, commanded=defaultdict(commanded_cls))

    def inputs(self_: Any, identifier: str, level: int) -> Any:
        return coordinator_cls._wind_water_inputs(self_, identifier, level)

    check("both inputs, cloud 2/2: wind 3", inputs(fake(), "windpower", 3) == {"wind": 3, "water": 2})
    check("both inputs, cloud 2/2: water 1", inputs(fake(), "water", 1) == {"water": 1, "wind": 2})
    check("other input follows the cloud (1/3)", inputs(fake({"windpower": 1, "water": 3}), "windpower", 2) == {"wind": 2, "water": 3} and inputs(fake({"windpower": 1, "water": 3}), "water", 2) == {"water": 2, "wind": 1})
    check("an unknown reported level of the other setting is passed on unchanged", inputs(fake({"windpower": 2, "water": 5}), "windpower", 3) == {"wind": 3, "water": 5})

    no_water = fake({"windpower": 2})
    no_water.commanded["water"].set(1)
    check("cloud has no value for the other: the commanded value", inputs(no_water, "windpower", 3) == {"wind": 3, "water": 1})
    check("neither cloud nor command knows it: default level 2", const.DEFAULT_LEVEL == 2 and inputs(fake({}), "windpower", 3) == {"wind": 3, "water": 2} and inputs(fake(data=False), "water", 1) == {"water": 1, "wind": 2})

    pending = fake({"windpower": 2, "water": 2})
    pending.commanded["windpower"].set(3)  # suction was just set to 3, the cloud still says 2
    check("pending command of the other setting is kept (no revert while the cloud lags)", inputs(pending, "water", 1) == {"water": 1, "wind": 3})
    pending.commanded["windpower"]._until = 0.0
    check("after the grace period the cloud value is used again", inputs(pending, "water", 1) == {"water": 1, "wind": 2})
    check("commanded values are only shown for 30 s", const.COMMAND_GRACE_SECONDS == 30)

    check("not a level property -> None", inputs(fake(), "clean_area", 1) is None)
    without_service = copy.deepcopy(MODEL_JSON)
    without_service["services"] = [x for x in without_service["services"] if x["identifier"] != "WindWater"]
    check("model without a WindWater service -> None (property write)", inputs(fake(model_obj=mod("model").ThingsModel.from_api(without_service)), "windpower", 3) is None)
    wind_only = copy.deepcopy(MODEL_JSON)
    for service in wind_only["services"]:
        if service["identifier"] == "WindWater":
            service["inputData"] = [x for x in service["inputData"] if x["identifier"] == "Wind"]
    check("service with only a Wind input -> only Wind is sent", inputs(fake(model_obj=mod("model").ThingsModel.from_api(wind_only)), "windpower", 3) == {"wind": 3} and inputs(fake(model_obj=mod("model").ThingsModel.from_api(wind_only)), "water", 1) is None)


# ---------------------------------------------------------------------------
# 3. translations, manifest, hacs.json
# ---------------------------------------------------------------------------
def keyset(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        out: set[str] = set()
        for key, item in value.items():
            out |= keyset(item, f"{prefix}/{key}")
        return out
    return {prefix}


def test_files() -> None:
    const = mod("const")
    strings = json.loads((PKG_DIR / "strings.json").read_text("utf-8"))
    en = json.loads((PKG_DIR / "translations/en.json").read_text("utf-8"))
    check("strings.json equals translations/en.json", strings == en)
    check("English only: no other translation file", sorted(p.name for p in (PKG_DIR / "translations").iterdir()) == ["en.json"])
    check("L2: switch.py does not import a private name from sensor.py", "from .sensor import" not in (PKG_DIR / "switch.py").read_text("utf-8") and "_local_start" not in (PKG_DIR / "sensor.py").read_text("utf-8"))

    descriptions: dict[str, list[Any]] = {
        "sensor": list(mod("sensor").SENSORS) + list(mod("sensor").ENUM_SENSORS),
        "switch": list(mod("switch").SWITCHES),
        "select": list(mod("select").SELECTS),
        "button": list(mod("button").BUTTONS),
        "binary_sensor": list(mod("binary_sensor").BINARY_SENSORS),
    }
    for lang, data in (("en", en),):
        missing = [f"{p}.{d.translation_key}" for p, ds in descriptions.items() for d in ds if not data["entity"].get(p, {}).get(d.translation_key, {}).get("name")]
        check(f"[{lang}] every entity translation_key has a name", not missing, str(missing))
        for platform, key, known in (
            ("sensor", "status", const.WORK_STATUS_KEYS), ("sensor", "error", const.ERROR_KEYS), ("sensor", "wifi_signal", const.WIFI_KEYS),
            ("select", "suction_level", {**const.LEVEL_KEYS["windpower"], **{i: k for i, k in enumerate(const.WIND_ENUM_LABEL_KEYS.values(), 100)}}), ("select", "mop_water_level", const.LEVEL_KEYS["water"]),
        ):  # fmt: skip
            states = data["entity"][platform][key]["state"]
            check(f"[{lang}] {platform}.{key} has a translated state for every known value", set(states) == set(known.values()) and all(states.values()), str(set(known.values()) ^ set(states)))
        fan_states = data["entity"]["vacuum"]["robot"]["state_attributes"]["fan_speed"]["state"]
        check(f"[{lang}] vacuum fan speed presets are translated", set(fan_states) == set(const.LEVEL_KEYS["windpower"].values()) | set(const.WIND_ENUM_LABEL_KEYS.values()) and all(fan_states.values()), str(fan_states))
        check(f"[{lang}] options flow texts: the menu, both room steps, field, error and abort", {"init", "menu", "room_areas", "rooms"} <= set(data["options"]["step"]) and set(data["options"]["step"]["menu"]["menu_options"]) == {"room_areas", "rooms"} and data["options"]["step"]["menu"]["menu_options"]["room_areas"] == "Link rooms to areas" and "{robot}" in data["options"]["step"]["room_areas"]["title"] and "Clean room" in data["options"]["step"]["room_areas"]["description"] and data["entity"]["button"]["clean_room"]["name"] == "Clean {room}" and data["options"]["step"]["rooms"]["data"]["room_names"] and data["options"]["step"]["init"]["data"]["robot"] and data["options"]["error"]["invalid_room_names"] and data["options"]["abort"]["no_rooms"] and "{robot}" in data["options"]["step"]["rooms"]["title"])
        check(f"[{lang}] schedules sensor name and invalid_room exception", data["entity"]["sensor"]["schedules"]["name"] and "{room}" in data["exceptions"]["invalid_room"]["message"])
        check(f"[{lang}] region selector options", set(data["selector"]["region"]["options"]) == set(const.REGION_HOSTS))
        check(f"[{lang}] config errors/abort/steps present", {"cannot_connect", "invalid_auth", "unknown"} <= set(data["config"]["error"]) and "already_configured" in data["config"]["abort"] and {"user", "reauth_confirm"} <= set(data["config"]["step"]))
        used = set()
        for fname in ("coordinator.py", "__init__.py", "vacuum.py", "select.py"):
            used |= set(re.findall(r'translation_domain=DOMAIN,\s*translation_key="([a-z_]+)"', (PKG_DIR / fname).read_text("utf-8")))
        check(f"[{lang}] every exception translation_key exists", used and used <= set(data["exceptions"]), str(used - set(data["exceptions"])))
        for key, item in data["exceptions"].items():
            placeholders = set(re.findall(r"\{(\w+)\}", item["message"]))
            check(f"[{lang}] exception '{key}' placeholders are known", placeholders <= {"error", "feature", "fan_speed", "option", "command", "params", "room"}, str(placeholders))
    check("sensor name Schedules", en["entity"]["sensor"]["schedules"]["name"] == "Schedules")
    check("English suction/water labels", en["entity"]["select"]["suction_level"]["state"] == {"off": "Off", "quiet": "Quiet", "standard": "Standard", "strong": "Strong", "max": "Max"} and en["entity"]["select"]["mop_water_level"]["state"] == {"low": "Low", "medium": "Medium", "high": "High"})
    check("no number entities are left in the translations", "number" not in en["entity"])
    check("water volume (mop_speed): named 'Water volume', a translated state for every level, the old mop_intensity key is gone", en["entity"]["select"]["water_volume"]["name"] == "Water volume" and en["entity"]["select"]["water_volume"]["state"] == {"off": "Off", "low": "Low", "medium": "Medium"} and set(en["entity"]["select"]["water_volume"]["state"]) == set(const.MOP_SPEED_KEYS.values()) == set(const.MOP_SPEED_LABEL_KEYS.values()) and "mop_intensity" not in en["entity"]["select"])
    check("mop fitted: translated states Fitted / Not fitted", en["entity"]["binary_sensor"]["mop_fitted"]["state"] == {"on": "Fitted", "off": "Not fitted"})

    icons = json.loads((PKG_DIR / "icons.json").read_text("utf-8"))
    icon_keys = {(p, k) for p, d in icons["entity"].items() for k in d}
    all_keys = {(p, k) for p, d in en["entity"].items() for k in d}
    check("icons.json only references translated entities", icon_keys <= all_keys, str(icon_keys - all_keys))

    manifest = json.loads((PKG_DIR / "manifest.json").read_text("utf-8"))
    keys = list(manifest)
    check("manifest keys: domain, name, then alphabetical (hassfest)", keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:]), str(keys))
    check(
        "manifest values",
        manifest["domain"] == "imou_vacuum" and manifest["name"] == "Imou Vacuum (Open Platform)" and manifest["version"] == "0.7.0b2"
        and manifest["config_flow"] is True and manifest["iot_class"] == "cloud_polling" and manifest["integration_type"] == "hub"
        and manifest["requirements"] == [] and manifest["codeowners"] == ["@okke-mengerink"]
        and manifest["documentation"] == "https://github.com/okke-mengerink/ha-imou-robot"
        and manifest["issue_tracker"] == "https://github.com/okke-mengerink/ha-imou-robot/issues",
    )  # fmt: skip
    # hassfest accepts a version that AwesomeVersion reads as one of these schemes (HA uses it too)
    from awesomeversion import AwesomeVersion, AwesomeVersionStrategy  # noqa: PLC0415

    version = AwesomeVersion(manifest["version"])
    check("manifest version is valid for hassfest (AwesomeVersion) and a pre-release of 0.7.0", version.strategy in (AwesomeVersionStrategy.SEMVER, AwesomeVersionStrategy.PEP440, AwesomeVersionStrategy.SIMPLEVER, AwesomeVersionStrategy.BUILDVER, AwesomeVersionStrategy.CALVER) and version.modifier == "b2" and AwesomeVersion("0.6.4") < version < AwesomeVersion("0.7.0"), f"{version.strategy} {version.modifier}")
    hacs = json.loads((ROOT / "hacs.json").read_text("utf-8"))
    check("hacs.json", hacs == {"name": "Imou Vacuum (Open Platform)", "homeassistant": "2026.1.0", "render_readme": True, "zip_release": True, "filename": "imou_vacuum.zip"}, str(hacs))
    # the release workflow zips exactly this folder; the archive name must match hacs.json
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    check("release workflow builds and uploads the zip named in hacs.json", f"-o {hacs['filename']}" in release_workflow and f"{hacs['filename']} --clobber" in release_workflow and "HEAD:custom_components/imou_vacuum" in release_workflow)
    check("brand icons present", (PKG_DIR / "brand/icon.png").is_file() and (PKG_DIR / "brand/icon@2x.png").is_file())

    # the domain was renamed in 0.2.0: manifest, folder and DOMAIN agree, and nothing of the old name is left
    check("domain: manifest, folder name and DOMAIN are all 'imou_vacuum'", manifest["domain"] == PKG_DIR.name == const.DOMAIN == "imou_vacuum", f"{manifest['domain']} {PKG_DIR.name} {const.DOMAIN}")
    check("display name 'Imou Vacuum (Open Platform)' in manifest, hacs.json and the translation", {manifest["name"], hacs["name"], en["title"]} == {"Imou Vacuum (Open Platform)"}, str({manifest["name"], hacs["name"], en["title"]}))
    old_domain, old_name = "imou" + "_robot", "Imou Robot" + " Vacuum"  # (built from parts so that this file does not match itself)
    leftovers = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts or path.suffix == ".png":
            continue
        if path.name in ("README.md", "CHANGELOG.md"):
            continue  # the migration notes mention the old names on purpose
        text = path.read_text("utf-8", errors="ignore")
        if old_domain in text or old_name in text:
            leftovers.append(str(path.relative_to(ROOT)))
    check("no trace of the old domain or name outside the README and CHANGELOG migration notes", not leftovers, str(leftovers))
    for doc in ("README.md", "CHANGELOG.md"):
        text = (ROOT / doc).read_text("utf-8")
        check(f"{doc} explains the rename and the migration", old_domain in text and "imou_vacuum" in text and "0.2.0" in text)


# ---------------------------------------------------------------------------
# 4. config-flow schema
# ---------------------------------------------------------------------------
def test_config_flow_schema() -> None:
    from homeassistant.helpers import config_validation as cv  # noqa: PLC0415

    flow = mod("config_flow")
    schema = flow.STEP_USER_SCHEMA
    keys = [str(k) for k in schema.schema]
    check("schema fields app_id, app_secret, region", keys == ["app_id", "app_secret", "region"], str(keys))
    result = schema({"app_id": "id", "app_secret": "secret"})
    check("region defaults to 'eu'", result["region"] == "eu", str(result))
    try:
        schema({"app_id": "id", "app_secret": "secret", "region": "mars"})
        check("unknown region is rejected", False)
    except Exception:  # noqa: BLE001
        check("unknown region is rejected", True)
    try:
        import probatio  # noqa: PLC0415

        fields = probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
    except ImportError:
        import voluptuous_serialize  # noqa: PLC0415

        fields = voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
    by_name = {f["name"]: f for f in fields}
    check("serialised form: secret is a password field", by_name["app_secret"]["selector"]["text"]["type"] == "password", str(by_name["app_secret"]))
    check("serialised form: region options + default", by_name["region"]["default"] == "eu" and by_name["region"]["selector"]["select"]["options"] == ["eu", "sg", "na"] and by_name["region"]["selector"]["select"]["translation_key"] == "region", str(by_name["region"]))
    const = mod("const")
    check("region hosts", const.REGION_HOSTS == {"eu": "openapi-fk.easy4ip.com", "sg": "openapi-sg.easy4ip.com", "na": "openapi-or.easy4ip.com"} and const.DEFAULT_REGION == "eu")
    check("reauth schema asks for the secret only", [str(k) for k in flow.STEP_REAUTH_SCHEMA.schema] == ["app_secret"])


# ---------------------------------------------------------------------------
# 5. full setup in a bare Home Assistant core with a fake API client
# ---------------------------------------------------------------------------
PROBE_MAP_URL = "https://oss.example.com/bucket/maps/abc/map001.bin?Signature=PROBESECRETSIG&Expires=99"
PROBE_AREA_URL = "https://oss.example.com/bucket/areas/area001.json?token=PROBESECRETTOK"
# clean_list response data in the format of the real robot (verified live)
SCHEDULES_VALUE = {
    "timeZone": 1,
    "timeZoneSec": 3600,
    "value": [
        {"period": [], "unlock": True, "active": True, "cleanAreas": [2], "startTime": 1794224460, "endTime": 0, "segmentTagIds": ["Kantoor"]},
        {"period": [0], "unlock": False, "active": True, "cleanAreas": [], "startTime": 50400, "endTime": 0, "segmentTagIds": []},
        {"period": [], "unlock": True, "active": True, "cleanAreas": [3, 4], "startTime": 1794400000, "endTime": 0, "segmentTagIds": ["Slaapkamer", "Slaapkamer"]},
    ],
}
PROBE_PROPS = {
    "1022": json.dumps({"mode": 1, "rooms": [{"id": 3, "roomName": "Kitchen", "order": 1}]}),
    "1023": json.dumps([{"areaId": 3, "areaName": "Kitchen", "points": [[1.5, 2.5]]}]),
    "1025": "plain text that is not json " + PROBE_MAP_URL,
    "40100": {"40102": "Kitchen Floor", "40103": PROBE_MAP_URL, "40104": PROBE_AREA_URL},
}


# refs of the room properties that are read once an hour with ``sequence`` (RV1 models): sequence,
# swp_allareaConfig, swp_areaConfig, swp_allCleanPlan, area_clean_plan, swp_cleanSequence,
# sweeper_sequence and map_name
ROOM_READ_REFS = {"120600", "147400", "147300", "145800", "121600", "146000", "160700", "122300"}


class FakeClient:
    """Stands in for ImouClient (no network)."""

    instance: FakeClient | None = None
    validate_error: Exception | None = None
    devices_override: list[Any] | None = None
    model_error: Exception | None = None
    robot_id = "TESTDEVICE0001"
    initial_status = "online"
    initial_fail_with: Exception | None = None  # the first polls fail

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        FakeClient.instance = self
        self.app_id, self.host = app_id, host
        self.status = FakeClient.initial_status
        self.props: dict[str, Any] = copy.deepcopy(VALUES_JSON["properties"])
        self.props.update(copy.deepcopy(PROBE_PROPS))  # never polled; only read by the diagnostics probe
        self.calls: list[tuple[Any, ...]] = []
        self.probe_service_calls: list[tuple[Any, ...]] = []
        self.room_read_refs: list[list[str]] = []  # the refs of every read of the room properties
        self.poll_count = 0
        self.fail_with: Exception | None = FakeClient.initial_fail_with  # makes polls and commands fail
        self.report_while_not_online = False  # a cloud that still returns the last values while asleep
        self.fail_commands_with: Exception | None = None  # makes only commands fail
        self.fail_set_with: Exception | None = None  # makes only property writes fail
        self.fail_control_with: Exception | None = None  # makes only service calls fail
        self.fail_schedules_with: Exception | None = None  # makes only clean_list fail
        self.fail_sequence_with: Exception | None = None  # makes only the sequence read fail
        self.fail_extras_with: Exception | None = None  # makes only the room reads with more than ``sequence`` fail
        self.sequence_reads = 0  # reads of the room properties (sequence, room sources, map_name)
        self.timer_value: Any = copy.deepcopy(SCHEDULES_VALUE)
        self.latency = False  # service calls yield to the event loop first (to interleave concurrent calls)
        self.ignore_sets = False  # simulate a cloud that accepts but does not apply writes
        self.ignore_level_control = False  # WindWater accepted (code 0) but not applied
        self.apply_level_property_writes = False  # the real L11-A ignores 1027/1028 writes

    async def async_validate(self) -> None:
        if FakeClient.validate_error is not None:
            raise FakeClient.validate_error

    async def async_list_devices(self) -> list[Any]:
        if FakeClient.devices_override is not None:
            return FakeClient.devices_override
        api = mod("api")
        base = {"status": "online", "version": "0.4.50", "brand": "lechange"}
        return [
            api.ImouDevice(device_id=FakeClient.robot_id, product_id="PRODUCT_ID", name="RV-L11-A-TEST", model="L11-A", **base),
            api.ImouDevice(device_id="CAMERA0001", product_id="", name="Camera", model="CAM", **base),
            api.ImouDevice(device_id="OTHERIOT0001", product_id="OTHER_PRODUCT", name="Other IoT", model="X1", **base),
        ]  # fmt: skip

    async def async_get_product_model(self, product_id: str) -> dict[str, Any]:
        if FakeClient.model_error is not None:
            raise FakeClient.model_error
        if product_id == "PRODUCT_ID":
            return copy.deepcopy(MODEL_JSON)
        return {"properties": [{"identifier": "light", "ref": "1", "dataType": {"type": "bool", "specs": {}}}], "services": [{"identifier": "reboot", "ref": "2", "inputData": []}]}

    async def async_get_properties(self, product_id: str, device_id: str, refs: Any) -> Any:
        api = mod("api")
        self.poll_count += 1
        room_read = set(refs) <= ROOM_READ_REFS  # (read together with the schedules; not the poll, not the probe)
        if not room_read:
            self.last_refs = list(refs)
        else:
            self.room_read_refs.append(list(refs))
            self.sequence_reads += 1
            if self.fail_sequence_with is not None:
                raise self.fail_sequence_with
            if self.fail_extras_with is not None and set(refs) - {"120600"}:
                raise self.fail_extras_with
        if self.fail_with is not None:
            raise self.fail_with
        values = {ref: self.props[ref] for ref in refs if ref in self.props} if self.status == "online" or self.report_while_not_online else {}
        return api.ImouProperties(status=self.status, values=values)

    async def async_set_properties(self, product_id: str, device_id: str, properties: dict[str, Any]) -> None:
        self.calls.append(("set", device_id, dict(properties)))
        if (failure := self.fail_set_with or self.fail_commands_with or self.fail_with) is not None:
            raise failure
        if not self.ignore_sets:
            for ref, value in properties.items():
                if ref in ("1027", "1028") and not self.apply_level_property_writes:
                    continue  # like the real L11-A: code 0, but the robot ignores the write
                self.props[ref] = value

    async def async_control(self, product_id: str, device_id: str, ref: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
        if ref == "2500":  # clean_list (read-only probe): kept out of self.calls
            self.probe_service_calls.append((ref, dict(content or {})))
            if self.latency:
                await asyncio.sleep(0)
            if (failure := self.fail_schedules_with or self.fail_with) is not None:
                raise failure
            return {"content": {"outputData": {"2501": copy.deepcopy(self.timer_value)}, "service": ref}}
        if self.latency:
            await asyncio.sleep(0)
        self.calls.append(("control", device_id, ref, dict(content or {})))
        if (failure := self.fail_control_with or self.fail_commands_with or self.fail_with) is not None:
            raise failure
        if ref == "3100" and not self.ignore_level_control:  # WindWater: Wind 3103 -> 1027, Water 3102 -> 1028
            if "3103" in content:
                self.props["1027"] = content["3103"]
            if "3102" in content:
                self.props["1028"] = content["3102"]
        return {"content": {"outputData": {"3104": 0} if ref == "3100" else {"2706": 0}, "service": ref}}


async def full_setup_checks(config_dir: str) -> None:
    from homeassistant import block_async_io, bootstrap, config_entries, loader  # noqa: PLC0415
    from homeassistant.core import HomeAssistant  # noqa: PLC0415
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
    from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: PLC0415

    const = mod("const")
    api = mod("api")
    package = importlib.import_module(PKG)
    flow_module = mod("config_flow")
    package.ImouClient = FakeClient  # used by async_setup_entry
    flow_module.ImouClient = FakeClient  # used by the config flow
    # the HTTP session is irrelevant for the fake client (and needs zeroconf/network)
    package.async_get_clientsession = lambda hass: None
    flow_module.async_get_clientsession = lambda hass: None

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root = logging.getLogger()
    capture = Capture(level=logging.DEBUG)
    root.addHandler(capture)
    root.setLevel(logging.WARNING)
    logging.getLogger(PKG).setLevel(logging.DEBUG)
    block_async_io.enable()  # warn about blocking calls made inside the event loop

    if True:  # keep the indentation of the block below
        hass = HomeAssistant(config_dir)
        loader.async_setup(hass)
        hass.config.skip_pip = True
        hass.config.language = "en"
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        check("custom integration is discovered", "imou_vacuum" in await loader.async_get_custom_components(hass))
        await bootstrap.async_load_base_functionality(hass)
        await hass.async_start()
        try:
            await _full_setup_body(hass, const, api, flow_module, config_entries, dr, er, HomeAssistantError)
        finally:
            await hass.async_stop()
            root.removeHandler(capture)
            for record in records:
                if os.environ.get("IMOU_TEST_VERBOSE") and record.exc_info and record.name.startswith(PKG):
                    print("--- logged exception:", record.getMessage())
                    traceback.print_exception(*record.exc_info)

    messages = [r.getMessage() for r in records]
    ours = [r for r in records if r.name.startswith(PKG)]
    check("integration logged something at debug level", bool(ours))
    secrets = ("test-secret", "new-secret", "test-app", "TESTDEVICE0001", '"2701"')
    leaked = [m for m in messages if any(sv in m for sv in secrets)]
    check("no secrets, App ID or device serial in any log line", not leaked, str(leaked[:3]))
    blocking = [m for m in messages if "blocking call" in m.lower() and "imou_vacuum" in m]
    check("no blocking calls inside the event loop from the integration", not blocking, str(blocking[:2]))
    errors = [r.getMessage() for r in records if r.levelno >= logging.ERROR and r.name.startswith(PKG)]
    allowed = [
        r"^Authentication failed while fetching imou_vacuum RV-L11-A-TEST data",
        r"^Error fetching imou_vacuum RV-L11-A-TEST data: Could not read the robot's state: TIMEOUT: getIotDeviceProperties$",
        r"^Unexpected error while validating Imou credentials$",
        r"^Error setting up entry t for imou_vacuum: No Imou robot vacuum was found",
    ]
    unexpected = [m for m in errors if not any(re.search(pattern, m) for pattern in allowed)]
    check("only the injected failures were logged as errors", not unexpected, str(unexpected))
    # (the 'no robots' setup error is logged by Home Assistant itself, under a different logger, in older versions)
    check("each injected failure was logged", all(any(re.search(pattern, m) for m in errors) for pattern in allowed[:3]), str(errors))


async def _full_setup_body(hass: Any, const: Any, api: Any, flow_module: Any, config_entries: Any, dr: Any, er: Any, HomeAssistantError: type[Exception]) -> None:  # noqa: N803, C901, PLR0915
    # an upgrade from 0.1.0/0.1.1: two switches already exist (enabled) in the entity registry
    pre_registry = er.async_get(hass)
    for key in ("carpet_boost",):
        pre_registry.async_get_or_create("switch", "imou_vacuum", f"TESTDEVICE0001_{key}", suggested_object_id=f"rv_l11_a_test_{key}")

    # ---- config flow: errors, success ------------------------------------
    result = await hass.config_entries.flow.async_init("imou_vacuum", context={"source": "user"})
    check("config flow shows the user form", result["type"] == "form" and result["step_id"] == "user", str(result["type"]))
    flow_id = result["flow_id"]
    user_input = {"app_id": "test-app", "app_secret": "test-secret", "region": "sg"}
    for error, expected in ((api.ImouConnectionError("TIMEOUT", "accessToken"), "cannot_connect"), (api.ImouAuthError("SN1004", "bad sign"), "invalid_auth"), (api.ImouApiError("X", "y"), "unknown"), (RuntimeError("boom"), "unknown")):
        FakeClient.validate_error = error
        result = await hass.config_entries.flow.async_configure(flow_id, user_input)
        check(f"config flow error {type(error).__name__} -> {expected}", result["type"] == "form" and result["errors"] == {"base": expected}, str(result))
    FakeClient.validate_error = None

    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    check("config flow creates the entry", result["type"] == "create_entry" and result["data"] == user_input and result["result"].unique_id == "test-app", str(result))
    check("the config entry is titled 'Imou Vacuum (Open Platform)'", result["title"] == "Imou Vacuum (Open Platform)", str(result.get("title")))
    entry = result["result"]
    await hass.async_block_till_done()
    check("entry is loaded", entry.state is config_entries.ConfigEntryState.LOADED, str(entry.state))
    check("client used the region host", FakeClient.instance.host == "openapi-sg.easy4ip.com")

    dup = await hass.config_entries.flow.async_init("imou_vacuum", context={"source": "user"})
    dup = await hass.config_entries.flow.async_configure(dup["flow_id"], user_input)
    check("same App ID aborts as already_configured", dup["type"] == "abort" and dup["reason"] == "already_configured", str(dup))

    client = FakeClient.instance
    runtime = entry.runtime_data
    check("only the robot got a coordinator (camera and non-robot IoT skipped)", list(runtime.coordinators) == ["TESTDEVICE0001"])
    coordinator = runtime.coordinators["TESTDEVICE0001"]
    check("polled refs are exactly the spec list", client.last_refs == ["12300", "11600", "1019", "1020", "1021", "1027", "1028", "1029", "11900", "12200", "40500", "11300"], str(client.last_refs))
    check("never polls map/trail/zone refs", not {"1022", "1023", "1025", "1026", "40100", "40400"} & set(client.last_refs))

    # ---- registries -------------------------------------------------------
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    device = devices[0] if len(devices) == 1 else None
    check("one device with name/model/manufacturer/sw_version", device is not None and (device.name, device.model, device.manufacturer, device.sw_version) == ("RV-L11-A-TEST", "L11-A", "Imou", "0.4.50") and ("imou_vacuum", "TESTDEVICE0001") in device.identifiers, str(device))
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    room_button_ids = {f"TESTDEVICE0001_clean_room_{room_id}" for room_id in (2, 3, 4)}
    check("a Clean button exists for every room of the schedules", {e.unique_id for e in entities} >= room_button_ids)
    check("L11-A entities: the 17 of main, four reset buttons and one Clean button per room (24), and no schedule switch", len(entities) == 24 and len({e.unique_id for e in entities} - room_button_ids) == 21 and not any("schedule_" in e.unique_id for e in entities) and not [e for e in entities if e.domain == "switch" and "schedule" in e.unique_id], str(sorted(e.unique_id for e in entities)))
    ids = {e.unique_id.split("_", 1)[1]: e.entity_id for e in entities}
    print("entity ids:", ", ".join(sorted(ids.values())))
    check("existing registry entries stay enabled (upgrade): switches are not disabled", all(e.disabled_by is None for e in entities if e.domain == "switch"), str({e.unique_id: e.disabled_by for e in entities if e.domain == "switch"}))
    check("the first refresh ran before the entities were created", coordinator.reports.seen_online and "rug_press" in coordinator.reports.reported, str(coordinator.reports.as_diagnostics()))

    def state(key: str) -> Any:
        return hass.states.get(ids[key])

    check("vacuum entity id is vacuum.rv_l11_a_test", ids["vacuum"] == "vacuum.rv_l11_a_test", ids["vacuum"])
    check("vacuum state is docked", state("vacuum").state == "docked", str(state("vacuum")))
    vac_attrs = state("vacuum").attributes
    check("vacuum attributes: work_status 3, error 0, supported_features", vac_attrs["work_status"] == 3 and vac_attrs["error"] == 0 and vac_attrs["supported_features"] == 13116 | int(ha_clean_area() or 0), str(dict(vac_attrs)))
    check("vacuum fan speed attributes", vac_attrs["fan_speed"] == "standard" and list(vac_attrs["fan_speed_list"]) == ["quiet", "standard", "max"], str(dict(vac_attrs)))
    expected_states = {
        "battery": "1", "status": "charging", "error": "none", "wifi_signal": "fair",
        "cleaned_area": "0", "cleaning_time": "0", "side_brush": "0.0", "main_brush": "0.0",
        "filter": "0.0", "sensors": "0.0", "mop_fitted": "off",
        "carpet_boost": "on",
        "suction_level": "standard", "mop_water_level": "medium", "schedules": "2",
    }  # fmt: skip
    got = {k: state(k).state for k in expected_states}
    check("initial states from the observed values", got == expected_states, str({k: (v, expected_states[k]) for k, v in got.items() if v != expected_states[k]}))
    check("switch attribute reported_by_device is shown for every switch", state("carpet_boost").attributes["reported_by_device"] is True)
    check("entity names are translated (en)", state("battery").name == "RV-L11-A-TEST Battery" and state("empty_bin").name == "RV-L11-A-TEST Empty bin" and state("carpet_boost").name == "RV-L11-A-TEST Carpet boost" and state("suction_level").name == "RV-L11-A-TEST Suction" and state("mop_water_level").name == "RV-L11-A-TEST Water level", f"{state('battery').name} | {state('suction_level').name}")
    check("status sensor lists options", state("status").attributes["options"][3] == "charging" and len(state("status").attributes["options"]) == 20)
    check("diagnostic entities are categorised", ent_reg.async_get(ids["wifi_signal"]).entity_category == "diagnostic" and ent_reg.async_get(ids["side_brush"]).entity_category == "diagnostic" and ent_reg.async_get(ids["battery"]).entity_category is None)

    check("idle polling interval is 300 s", coordinator.update_interval.total_seconds() == 300, str(coordinator.update_interval))

    # ---- consumables: the robot reports minutes, the sensors show hours -----------------------------
    from homeassistant.components.sensor import async_rounded_state  # noqa: PLC0415

    consumable_keys = ("side_brush", "main_brush", "filter", "sensors")
    client.props["12200"] = {"12202": 34, "12204": 34, "12206": 34, "12208": 34}  # after a real 34-minute run
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("34 raw (minutes) -> state 0.5667 h on all four consumable sensors", [state(k).state for k in consumable_keys] == ["0.5667"] * 4, str([state(k).state for k in consumable_keys]))
    check("... displayed as 0.6 h (display precision 1)", [async_rounded_state(hass, ids[k], state(k)) for k in consumable_keys] == ["0.6"] * 4, str([async_rounded_state(hass, ids[k], state(k)) for k in consumable_keys]))
    check("... unit h, device class duration, state class measurement", all(state(k).attributes["unit_of_measurement"] == "h" and state(k).attributes["device_class"] == "duration" and state(k).attributes["state_class"] == "measurement" for k in consumable_keys))
    client.props["12200"] = {"12202": 0, "12204": 0, "12206": 0, "12208": 0}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("a new robot (0 raw) shows 0.0 h", [state(k).state for k in consumable_keys] == ["0.0"] * 4)

    # ---- commands -> exact API calls (refs resolved from the model) ------------
    async def call(domain: str, service: str, entity: str, **data: Any) -> None:
        client.calls.clear()
        await hass.services.async_call(domain, service, {"entity_id": ids[entity], **data}, blocking=True)

    await call("vacuum", "start", "vacuum")
    check("start from docked -> clean_control {2701:1,2702:0,2703:0}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 1, "2702": 0, "2703": 0})], str(client.calls))
    await call("vacuum", "pause", "vacuum")
    check("pause -> {2701:0}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 0})], str(client.calls))
    await call("vacuum", "stop", "vacuum")
    check("stop -> {2701:3}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 3})], str(client.calls))
    await call("vacuum", "return_to_base", "vacuum")
    check("return_to_base -> setIotDeviceProperties {1019:1}", client.calls == [("set", "TESTDEVICE0001", {"1019": 1})], str(client.calls))
    await call("vacuum", "locate", "vacuum")
    check("locate -> control 20400 {}", client.calls == [("control", "TESTDEVICE0001", "20400", {})], str(client.calls))
    client.props["12300"] = 1
    await coordinator.async_refresh()
    check("paused robot -> vacuum state 'paused'", state("vacuum").state == "paused")
    await call("vacuum", "start", "vacuum")
    check("start while paused -> {2701:2}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 2})], str(client.calls))
    client.props["12300"] = 13
    await coordinator.async_refresh()
    await call("vacuum", "start", "vacuum")
    check("start while return-paused -> {2701:2}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 2})], str(client.calls))
    client.props["12300"] = 3
    await coordinator.async_refresh()
    client.props["12300"] = 1  # paused from the Imou app; the cached state still says 'charging'
    polls = client.poll_count
    await call("vacuum", "start", "vacuum")
    check("start looks at the robot first (one extra poll)", client.poll_count == polls + 1, f"{client.poll_count} vs {polls}")
    check("start with a stale cache but a paused robot resumes -> {2701:2}", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 2})], str(client.calls))
    client.props["12300"] = 3
    await coordinator.async_refresh()

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await call("switch", "turn_off", "carpet_boost")
    check("carpet boost off -> set {11900:0}", client.calls == [("set", "TESTDEVICE0001", {"11900": 0})], str(client.calls))
    await call("select", "select_option", "suction_level", option="max")
    check("suction 'max' -> WindWater {Wind:3, Water:2 from the cloud}, no property write", client.calls == [("control", "TESTDEVICE0001", "3100", {"3103": 3, "3102": 2})], str(client.calls))
    await call("select", "select_option", "mop_water_level", option="low")
    check("water 'low' -> WindWater {Water:1, Wind:3 = the pending command, the cloud has not caught up}", client.calls == [("control", "TESTDEVICE0001", "3100", {"3102": 1, "3103": 3})], str(client.calls))
    await call("vacuum", "set_fan_speed", "vacuum", fan_speed="quiet")
    check("fan speed 'quiet' -> WindWater {Wind:1, Water:1 = pending}", client.calls == [("control", "TESTDEVICE0001", "3100", {"3103": 1, "3102": 1})], str(client.calls))
    await call("button", "press", "empty_bin")
    check("empty bin -> control 2300 {}", client.calls == [("control", "TESTDEVICE0001", "2300", {})], str(client.calls))
    check("room buttons are named 'Clean <room>' (the room's name, with the id suffix of a duplicate name)", [state(f"clean_room_{r}").name for r in (2, 3, 4)] == ["RV-L11-A-TEST Clean Kantoor", "RV-L11-A-TEST Clean Slaapkamer (3)", "RV-L11-A-TEST Clean Slaapkamer (4)"], str([state(f"clean_room_{r}").name for r in (2, 3, 4)]))
    check("room buttons: unique id, translation key, no entity category, broom icon", all((ent_reg.async_get(ids[f"clean_room_{r}"]).unique_id, ent_reg.async_get(ids[f"clean_room_{r}"]).translation_key, ent_reg.async_get(ids[f"clean_room_{r}"]).entity_category) == (f"TESTDEVICE0001_clean_room_{r}", "clean_room", None) for r in (2, 3, 4)) and ICONS_JSON["entity"]["button"]["clean_room"]["default"] == "mdi:broom")
    reads_before = client.sequence_reads
    await call("button", "press", "clean_room_3")
    check("single map (L11-A): pressing a room button makes no extra room-property read", client.sequence_reads == reads_before, f"{reads_before} {client.sequence_reads}")
    check("pressing 'Clean Slaapkamer (3)' sends a clean of room 3 only (mode 1, partition_area [3])", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 1, "2702": 1, "2703": 0, "2704": "[3]"})], str(client.calls))
    check("... the press is recorded as the last command", coordinator.last_command is not None and coordinator.last_command["inputs"]["partition_area"] == {"type": "json", "value": [3]}, str(coordinator.last_command))
    await hass.async_block_till_done()
    check("the switch state follows the commanded value", state("carpet_boost").state == "off")
    check("select and fan speed share the commanded suction (last command wins)", (state("suction_level").state, state("vacuum").attributes["fan_speed"], state("mop_water_level").state) == ("quiet", "quiet", "low"), f"{state('suction_level').state} {state('vacuum').attributes['fan_speed']} {state('mop_water_level').state}")

    # ---- levels outside 1-3 (a robot with another scale) -------------------------------
    for identifier in ("windpower", "water"):
        coordinator.commanded[identifier]._until = 0.0  # the command window is over
    client.props["1027"], client.props["1028"] = 5, 0
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    got = (state("suction_level").state, state("mop_water_level").state, state("vacuum").attributes["fan_speed"])
    check("raw levels outside 1-3 show as unknown_<n>", got == ("unknown_5", "unknown_0", "unknown_5"), str(got))
    check("the current unknown level is offered as an option", state("suction_level").attributes["options"] == ["quiet", "standard", "max", "unknown_5"], str(state("suction_level").attributes["options"]))
    client.props["1027"], client.props["1028"] = 2, 2
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("back to standard / medium", (state("suction_level").state, state("mop_water_level").state, state("vacuum").attributes["fan_speed"]) == ("standard", "medium", "standard"))

    from homeassistant.exceptions import ServiceValidationError  # noqa: PLC0415

    for domain, service, entity, data in (
        ("select", "select_option", "suction_level", {"option": "turbo"}),
        ("select", "select_option", "suction_level", {"option": "unknown_5"}),
        ("select", "select_option", "mop_water_level", {"option": "unknown_0"}),
        ("vacuum", "set_fan_speed", "vacuum", {"fan_speed": "turbo"}),
        ("vacuum", "set_fan_speed", "vacuum", {"fan_speed": "unknown_5"}),
    ):  # fmt: skip
        try:
            await call(domain, service, entity, **data)
        except ServiceValidationError:
            check(f"invalid choice {data} is rejected without a write", client.calls == [], str(client.calls))
        else:
            check(f"invalid choice {data} is rejected without a write", False, "no error")

    # ---- levels go through the WindWater service (the L11-A ignores property writes) ----
    wind_water = ("control", "TESTDEVICE0001", "3100")
    coordinator.commanded.clear()
    client.props["1027"], client.props["1028"] = 2, 2
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await call("vacuum", "set_fan_speed", "vacuum", fan_speed="quiet")
    check("set_fan_speed -> iotDeviceControl 3100 with both inputs: Wind 1 and the cloud's Water 2", client.calls == [(*wind_water, {"3103": 1, "3102": 2})], str(client.calls))
    await coordinator.async_refresh()  # the cloud applied it and reports it now
    await hass.async_block_till_done()
    check("select and fan speed show the level the cloud reports", client.props["1027"] == 1 and state("suction_level").state == "quiet" and state("vacuum").attributes["fan_speed"] == "quiet")
    await call("select", "select_option", "mop_water_level", option="high")
    check("water 'high' -> {Water:3, Wind: the cloud's 1}", client.calls == [(*wind_water, {"3102": 3, "3103": 1})], str(client.calls))
    await call("select", "select_option", "suction_level", option="max")
    check("suction 'max' -> {Wind:3, Water: the pending command 3}", client.calls == [(*wind_water, {"3103": 3, "3102": 3})], str(client.calls))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("cloud reports the new levels", (state("suction_level").state, state("mop_water_level").state, state("vacuum").attributes["fan_speed"]) == ("max", "high", "max"), f"{state('suction_level').state} {state('mop_water_level').state}")

    # the real L11-A finding: a command that is accepted but ignored must become visible
    client.props["1027"], client.props["1028"] = 2, 2
    coordinator.commanded.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    client.ignore_level_control = True
    await call("select", "select_option", "suction_level", option="max")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("ignored command: the commanded level is shown during the grace period", state("suction_level").state == "max" and state("vacuum").attributes["fan_speed"] == "max")
    coordinator.commanded["windpower"]._until = 0.0  # 30 s are over
    await coordinator.async_refresh()  # unchanged data must still refresh the entities
    await hass.async_block_till_done()
    check("ignored command: after the grace period the reported level wins", state("suction_level").state == "standard" and state("vacuum").attributes["fan_speed"] == "standard", f"{state('suction_level').state} {state('vacuum').attributes['fan_speed']}")
    client.ignore_level_control = False

    # a model without a WindWater service gets property writes
    removed = coordinator.model._services.pop("windwater")
    client.apply_level_property_writes = True
    await call("select", "select_option", "suction_level", option="max")
    check("no WindWater service -> property write {1027:3}", client.calls == [("set", "TESTDEVICE0001", {"1027": 3})], str(client.calls))
    await call("vacuum", "set_fan_speed", "vacuum", fan_speed="quiet")
    check("no WindWater service -> fan speed writes the property {1027:1}", client.calls == [("set", "TESTDEVICE0001", {"1027": 1})], str(client.calls))
    await call("select", "select_option", "mop_water_level", option="low")
    check("no WindWater service -> water writes the property {1028:1}", client.calls == [("set", "TESTDEVICE0001", {"1028": 1})], str(client.calls))
    coordinator.model._services["windwater"] = removed
    client.apply_level_property_writes = False

    # failures are reported, there is no second route, and the state does not change
    for failure in (api.ImouApiError("OP2000", "service refused"), api.ImouConnectionError("TIMEOUT", "iotDeviceControl"), api.ImouDeviceOfflineError("DV1007", "offline")):
        coordinator.commanded.clear()
        client.props["1027"], client.props["1028"] = 2, 2
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        client.fail_control_with = failure
        try:
            await call("select", "select_option", "suction_level", option="max")
        except HomeAssistantError as err:
            check(f"{type(failure).__name__} on WindWater -> error with code and msg, no property write", str(failure) in str(err) and [c[0] for c in client.calls] == ["control"], f"{err} {client.calls}")
        else:
            check(f"{type(failure).__name__} on WindWater -> error", False, "no error")
        check(f"{type(failure).__name__}: a failed command does not change the state", state("suction_level").state == "standard")
    client.fail_control_with = None
    coordinator.commanded.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # ---- a lagging/rejecting cloud must not leave a wrong state behind --------------
    client.props["11900"] = 0
    await coordinator.async_refresh()  # the cache now says 'off'
    await hass.async_block_till_done()
    client.ignore_sets = True
    await call("switch", "turn_on", "carpet_boost")  # the cloud accepts but ignores the write: it keeps reporting 0
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("lagging cloud: commanded value is shown inside the window", state("carpet_boost").state == "on", state("carpet_boost").state)
    coordinator.commanded["rug_press"]._until = 0.0  # the command window is over
    await coordinator.async_refresh()  # unchanged data must still refresh the entity
    await hass.async_block_till_done()
    check("rejected command: after the window the cloud value wins again", state("carpet_boost").state == "off", state("carpet_boost").state)
    client.ignore_sets = False
    client.props["11900"] = 1
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # ---- refresh ~3 s after a command, then fast polling for a while ---------------
    await asyncio.sleep(0.2)  # let any earlier timers settle
    before = client.poll_count
    client.calls.clear()
    await hass.services.async_call("vacuum", "locate", {"entity_id": ids["vacuum"]}, blocking=True)
    await asyncio.sleep(1.5)
    check("no refresh before ~3 s", client.poll_count == before, f"{client.poll_count} vs {before}")
    await asyncio.sleep(2.5)
    check("one refresh about 3 s after a command", client.poll_count == before + 1, f"{client.poll_count} vs {before}")
    check("polling is 30 s for a while after a command (idle robot)", coordinator.update_interval.total_seconds() == 30, str(coordinator.update_interval))

    # ---- adaptive interval ---------------------------------------------------------
    coordinator._boost_until = 0.0
    results = {}
    for status in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13):
        client.props["12300"] = status
        await coordinator.async_refresh()
        results[status] = coordinator.update_interval.total_seconds()
    expected = {s: (30 if s in (2, 4, 5, 7, 8, 9, 10, 11, 12, 13) else 300) for s in range(14)}
    check("interval: 30 s for {2,4,5,7,8,9,10,11,12,13}, 300 s otherwise", results == expected, str(results))
    client.props["12300"] = 3
    await coordinator.async_refresh()

    # ---- transient cloud failures keep the last data ------------------------------------------------
    log_records: list[logging.LogRecord] = []

    class CaptureCoordinatorLog(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    coordinator_logger = logging.getLogger(f"{PKG}.coordinator")
    log_handler = CaptureCoordinatorLog(level=logging.DEBUG)
    coordinator_logger.addHandler(log_handler)

    def levels() -> list[str]:  # (Home Assistant's own DEBUG lines are not interesting here)
        return [r.levelname for r in log_records if r.levelno >= logging.INFO]

    async def poll(times: int = 1) -> list[tuple[str, float]]:
        seen = []
        for _ in range(times):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            seen.append((state("vacuum").state, coordinator.update_interval.total_seconds()))
        return seen

    def all_unavailable() -> bool:
        return all(hass.states.get(e).state == "unavailable" for e in ids.values())

    timeout = api.ImouConnectionError("TIMEOUT", "getIotDeviceProperties")
    coordinator._boost_until = 0.0
    client.props["12300"] = 3
    await poll()
    check("idle robot polls every 300 s", coordinator.update_interval.total_seconds() == 300)

    # a single failed poll
    log_records.clear()
    client.fail_with = timeout
    ((first, first_interval),) = await poll()
    check("a single failed poll: entities stay available and show the last data", first == "docked" and state("battery").state == "1" and coordinator.last_update_success and not all_unavailable())
    check("a single failed poll: the next poll comes in 60 s, not 300 s", first_interval == 60, str(first_interval))
    client.fail_with = None
    await poll()
    check("recovery: the failure count is reset and polling is idle again", coordinator.health.failures == 0 and coordinator.update_interval.total_seconds() == 300)
    check("logs for a short hiccup: one warning at the first failure, one info on recovery", levels() == ["WARNING", "INFO"], str([(r.levelname, r.getMessage()) for r in log_records if r.levelno >= logging.INFO]))
    check("the warning names the robot and the error code, nothing secret", "Could not read RV-L11-A-TEST (TIMEOUT: getIotDeviceProperties); keeping the last data" in [r.getMessage() for r in log_records if r.levelno == logging.WARNING], str([r.getMessage() for r in log_records]))

    # three failed polls in a row
    log_records.clear()
    client.fail_with = timeout
    seen = await poll(3)
    check("two failed polls stay available, the third makes the entities unavailable", [x for x, _ in seen] == ["docked", "docked", "unavailable"] and not coordinator.last_update_success and all_unavailable(), str(seen))
    check("retry after 60 s, 60 s, then backing off to 120 s", [i for _, i in seen] == [60, 60, 120], str(seen))
    seen = await poll(2)
    check("a longer outage keeps backing off: 240 s, then 300 s; still unavailable", [i for _, i in seen] == [240, 300] and all_unavailable(), str(seen))
    check("logs for an outage: one warning (first failure) and one error (unavailable), no spam", levels() == ["WARNING", "ERROR"], str([(r.levelname, r.getMessage()) for r in log_records if r.levelno >= logging.INFO]))
    client.fail_with = None
    await poll()
    check("recovery after an outage: available again, count reset", state("vacuum").state == "docked" and coordinator.health.failures == 0 and not all_unavailable())
    check("recovery after an outage logs one info line (Home Assistant's own)", levels() == ["WARNING", "ERROR", "INFO"], str(levels()))

    # recovery resets the count
    client.fail_with = timeout
    await poll(2)
    client.fail_with = None
    await poll()
    client.fail_with = timeout
    seen = await poll(2)
    check("recovery resets the count: 2 failures, a good poll, 2 failures never make it unavailable", all(x == "docked" for x, _ in seen) and coordinator.last_update_success)
    client.fail_with = None
    await poll()

    # an active robot is retried after 30 s: never later than a normal poll
    client.props["12300"] = 7
    await poll()
    client.fail_with = timeout
    ((active, active_interval),) = await poll()
    check("active robot: a failed poll is retried after 30 s", active == "cleaning" and active_interval == 30, f"{active} {active_interval}")
    client.fail_with = None
    client.props["12300"] = 3
    await poll()

    # a last good poll older than 15 minutes
    coordinator.health.last_success -= 16 * 60
    client.fail_with = timeout
    ((stale, _),) = await poll()
    check("last good data older than 15 minutes: unavailable at the first failure", stale == "unavailable", stale)
    client.fail_with = None
    await poll()
    check("available again after a good poll", state("vacuum").state == "docked")

    # a robot that the cloud calls offline counts the same way (status, or API error)
    for label, go_offline, go_online in (
        ("status 'offline'", lambda: setattr(client, "status", "offline"), lambda: setattr(client, "status", "online")),
        ("API error 'device offline'", lambda: setattr(client, "fail_with", api.ImouDeviceOfflineError("DV1007", "device offline")), lambda: setattr(client, "fail_with", None)),
    ):  # fmt: skip
        go_offline()
        seen = await poll(3)
        check(f"{label}: available for 2 polls, unavailable from the 3rd", [x for x, _ in seen] == ["docked", "docked", "unavailable"] and all_unavailable(), str(seen))
        go_online()
        await poll()
        check(f"{label}: back online, available again", state("vacuum").state == "docked" and state("battery").state == "1" and coordinator.health.failures == 0)

    # sleep / upgrading are not failures: the last data is shown
    for status in ("sleep", "upgrading", "something-new"):
        client.status = status
        seen = await poll(4)
        check(f"status '{status}': stays available with the last data", all(x == "docked" for x, _ in seen) and coordinator.health.failures == 0 and state("battery").state == "1" and state("suction_level").state == "standard", str(seen))
    # the error DV1030 (device asleep) is not offline: the last data stays, no failure counted
    client.fail_with = api.ImouDeviceOfflineError("DV1030", "device sleep")
    seen = await poll(4)
    check("API error DV1030 (asleep): stays available with the last data, not counted as a failure", all(x == "docked" for x, _ in seen) and coordinator.health.failures == 0 and state("battery").state == "1", f"{seen} {coordinator.health.failures}")
    client.fail_with = api.ImouDeviceOfflineError("DV1062", "no heartbeat")
    seen = await poll(3)
    check("API error DV1062 (no heartbeat) still counts as offline", [x for x, _ in seen] == ["docked", "docked", "unavailable"], str(seen))
    client.fail_with = None
    await poll()
    client.status = "sleep"
    client.report_while_not_online = True
    client.props["11600"] = 55
    await poll()
    check("status 'sleep' with values: the reported values are shown", state("battery").state == "55" and state("vacuum").state == "docked")
    client.report_while_not_online = False
    client.props["11600"] = 1
    client.status = "online"
    await poll()
    check("online again: fresh data", state("battery").state == "1")

    coordinator_logger.removeHandler(log_handler)
    secrets = ("test-app", "test-secret", "new-secret", "TESTDEVICE0001")
    check("no secrets in the coordinator log lines", not any(sv in r.getMessage() for r in log_records for sv in secrets))

    # ---- errors ----------------------------------------------------------------------------
    client.fail_commands_with = api.ImouApiError("OP1001", "param error")
    client.calls.clear()
    try:
        await hass.services.async_call("vacuum", "start", {"entity_id": ids["vacuum"]}, blocking=True)
    except HomeAssistantError as err:
        check("command failure -> HomeAssistantError with code and msg only", "OP1001: param error" in str(err), str(err))
    else:
        check("command failure -> HomeAssistantError", False)
    client.fail_commands_with = None
    await asyncio.sleep(0)

    # ---- last_command: recorded after a success and after an API error --------------------
    send_params = {"status": 1, "mode": 1, "plan": 0, "partition_area": "[2]"}
    client.calls.clear()
    await hass.services.async_call("vacuum", "send_command", {"entity_id": ids["vacuum"], "command": "clean_control", "params": dict(send_params)}, blocking=True)
    check("vacuum.send_command reaches the API with refs and the value unchanged", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 1, "2702": 1, "2703": 0, "2704": "[2]"})], str(client.calls))
    check("last_command recorded after success (inputs run through redact, summarized output)", coordinator.last_command == {"service": "clean_control", "inputs": {**send_params, "partition_area": {"type": "json", "value": [2]}}, "output": {"2706": 0}}, str(coordinator.last_command))
    client.fail_control_with = api.ImouApiError("OP1002", "rejected")
    try:
        await hass.services.async_call("vacuum", "send_command", {"entity_id": ids["vacuum"], "command": "clean_control", "params": {"mode": 1, "partition_area": "[2]"}}, blocking=True)
    except HomeAssistantError as err:
        check("send_command API error -> HomeAssistantError", "OP1002: rejected" in str(err), str(err))
    else:
        check("send_command API error -> HomeAssistantError", False)
    check("last_command recorded after an ImouError (code, redacted inputs)", coordinator.last_command == {"service": "clean_control", "inputs": {"mode": 1, "partition_area": {"type": "json", "value": [2]}}, "error": "OP1002"}, str(coordinator.last_command))
    client.fail_control_with = None
    await asyncio.sleep(0)

    # ---- reauth ---------------------------------------------------------------------------
    client.fail_with = api.ImouAuthError("TK1002", "token invalid")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler("imou_vacuum")
    check("auth failure at runtime starts a reauth flow", any(f["context"]["source"] == "reauth" for f in flows), str(flows))
    reauth = next(f for f in flows if f["context"]["source"] == "reauth")
    FakeClient.validate_error = api.ImouAuthError("SN1004", "bad sign")
    result = await hass.config_entries.flow.async_configure(reauth["flow_id"], {"app_secret": "wrong"})
    check("reauth: wrong secret -> invalid_auth", result["type"] == "form" and result["errors"] == {"base": "invalid_auth"}, str(result))
    FakeClient.validate_error = None
    client.fail_with = None
    result = await hass.config_entries.flow.async_configure(reauth["flow_id"], {"app_secret": "new-secret"})
    await hass.async_block_till_done()
    check("reauth: new secret stored, entry reloaded", result["type"] == "abort" and result["reason"] == "reauth_successful" and entry.data["app_secret"] == "new-secret" and entry.state is config_entries.ConfigEntryState.LOADED, f"{result.get('reason')} {entry.state}")
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    ids = {e.unique_id.split("_", 1)[1]: e.entity_id for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)}
    check("entities are back after reauth reload", len(ids) == 24 and state("vacuum").state == "docked")

    # ---- diagnostics ------------------------------------------------------------------------
    diagnostics = importlib.import_module(f"{PKG}.diagnostics")

    class FakeSession:
        """Stands in for the shared aiohttp session: no network."""

        requests: list[dict[str, Any]] = []
        fail: Exception | None = None

        def get(self, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
            FakeSession.requests.append({"url": url, "headers": dict(headers or {}), **kwargs})
            if FakeSession.fail is not None:
                raise FakeSession.fail

            class Response:
                status = 206
                headers = {"Content-Type": "application/octet-stream", "Content-Length": "64"}

                class content:  # noqa: N801
                    @staticmethod
                    async def read(size: int) -> bytes:
                        return b"\x89PNG" + b"\x00" * 200  # more than asked: the probe must cap it

                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *exc: Any) -> None:
                    return None

            return Response()

    real_session_getter = diagnostics.async_get_clientsession
    diagnostics.async_get_clientsession = lambda hass: FakeSession()
    check("L11-A: no room property call (no sequence, room sources or map_name in its model), so no extra call and no extra entity", client.room_read_refs == [] and coordinator._room_property_identifiers() == [] and coordinator.sequence_rooms is None and coordinator.source_entries is None and coordinator.listed_map_ids == [] and coordinator.active_map() is None and not coordinator.is_multi_map() and coordinator.room_map() is None)  # noqa: SLF001
    client.probe_service_calls.clear()  # (the schedules were read during setup)
    probe_polls_before, polled_refs_before = client.poll_count, getattr(client, "last_refs", None)
    calls_before, health_before = len(client.calls), coordinator.health.failures
    boost_before = coordinator._boost_until  # noqa: SLF001
    dump = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    blob = json.dumps(dump, ensure_ascii=False, default=str)
    check("diagnostics contain last_command (None before any service call)", dump["robots"][0]["last_command"] is None, str(dump["robots"][0]["last_command"]))
    check("diagnostics redact App ID/Secret", dump["entry"]["app_id"] == "**REDACTED**" and dump["entry"]["app_secret"] == "**REDACTED**")
    check("diagnostics contain no secrets, serial or name", not any(s in blob for s in ("test-app", "new-secret", "test-secret", "TESTDEVICE0001", "RV-L11-A-TEST")), "leak")
    check("diagnostics tell which settings the robot reports", dump["robots"][0]["reports"]["seen_online"] is True and "rug_press" in dump["robots"][0]["reports"]["reported_identifiers"], str(dump["robots"][0]["reports"]))
    check("diagnostics include the support report (L11-A: verified, nothing missing or unknown)", dump["robots"][0]["support_report"]["verified_model"] is True and dump["robots"][0]["support_report"]["missing_core"] == [] and dump["robots"][0]["support_report"]["unknown_enum_labels"] == {} and dump["robots"][0]["support_report"]["capabilities"]["room_cleaning"] is True, str(dump["robots"][0].get("support_report")))
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    check("L11-A: no model support repair issue", ir.async_get(hass).async_get_issue("imou_vacuum", "model_support_TESTDEVICE0001") is None)
    check("L11-A: no Charging entity (the model has no power_status)", "charging" not in ids)
    check("diagnostics include the things model and values", dump["robots"][0]["things_model"]["properties"] and dump["robots"][0]["values"]["work_status"] == 3 and dump["robots"][0]["resolved_model"]["services"]["clean_control"]["ref"] == "2700")

    # ---- diagnostics probe: read-only, summarized, redacted ---------------------------------
    probe = dump["robots"][0]["probe"]
    props_out = probe["properties"]
    probe_blob = json.dumps(probe, ensure_ascii=False)
    check("probe covers the four unknown properties of the L11-A (the room/map ones of the RV1 Pro Max are not in its model)", set(props_out) == {"clean_params", "areainfoarray", "mapconfig", "map_switch"}, str(props_out.keys()))
    check("probe keeps JSON structure", props_out["clean_params"]["type"] == "json" and props_out["clean_params"]["value"]["rooms"][0]["id"] == 3 and props_out["areainfoarray"]["value"][0]["points"] == [[1.5, 2.5]], str(props_out))
    check("probe summarizes plain text", props_out["mapconfig"]["type"] == "text" and props_out["mapconfig"]["length"] > 0, str(props_out["mapconfig"]))
    check("probe hides room and map names", "Kitchen" not in probe_blob and "Morning" not in probe_blob and props_out["map_switch"]["map_name"] == "**REDACTED**", probe_blob)
    check("probe has no query strings, secrets or tokens", not any(s in probe_blob for s in ("PROBESECRET", "Signature", "Expires", "?", "test-app", "test-secret", "new-secret", "TESTDEVICE0001")), probe_blob)
    check("probe summarizes the clean_list service", probe["services"]["clean_list"]["content"]["outputData"]["2501"]["value"][0]["cleanAreas"] == [2] and probe["services"]["clean_list"]["content"]["outputData"]["2501"]["timeZoneSec"] == 3600, str(probe["services"]))
    checks = probe["url_checks"]
    check("probe fetched the mocked URLs (map and area first, no network)", len(checks) == 2 and checks[0]["url"]["url"] == "https://oss.example.com/…/b22d88fd.bin" and checks[0]["status"] == 206 and checks[0]["first_bytes_hex"] == "89504e47" + "00" * 12 and checks[0]["content_type"] == "application/octet-stream" and checks[1]["url"]["has_query"] is True, str(checks))
    check("probe fetch is a ranged GET with a timeout, and never follows redirects", all(r["headers"] == {"Range": "bytes=0-63"} and r.get("allow_redirects") is False for r in FakeSession.requests) and len(FakeSession.requests) == 2, str(FakeSession.requests))
    # only https URLs on public hosts are fetched: the others are skipped without a request
    FakeSession.requests.clear()
    skipped_urls = {
        "http://oss.example.com/a/b.bin?sig=1": "not_https", "ftp://oss.example.com/b.bin": "not_https",
        "https://localhost/a.bin": "private_host", "https://LOCALHOST./a.bin": "private_host", "https://127.0.0.1/a.bin": "private_host",
        "https://10.1.2.3/a.bin": "private_host", "https://192.168.1.5:8443/a.bin": "private_host", "https://172.16.0.9/a.bin": "private_host",
        "https://169.254.169.254/latest/meta-data": "private_host", "https://[::1]/a.bin": "private_host", "https://[fe80::1]/a.bin": "private_host",
        "https://[fd00::1]/a.bin": "private_host", "https://[::ffff:127.0.0.1]/a.bin": "private_host", "https://0.0.0.0/a.bin": "private_host",
    }
    skip_results = {url: await diagnostics._async_check_url(hass, url) for url in skipped_urls}  # noqa: SLF001
    check("probe skips non-https and private/loopback/link-local hosts, with a reason and no request", FakeSession.requests == [] and all(r.get("skipped") == skipped_urls[u] and set(r) == {"url", "skipped"} for u, r in skip_results.items()), str(skip_results))
    check("... a skipped URL is still redacted", skip_results["http://oss.example.com/a/b.bin?sig=1"]["url"] == {"url": "http://oss.example.com/…/" + hashlib.sha256(b"b.bin").hexdigest()[:8] + ".bin", "has_query": True, "path_depth": 2} and "sig=1" not in json.dumps(list(skip_results.values())))
    public = await diagnostics._async_check_url(hass, "https://8.8.8.8/a.bin")  # noqa: SLF001
    check("... an https URL on a public IP or a name is fetched (no DNS lookup, no redirects)", len(FakeSession.requests) == 1 and public["status"] == 206 and FakeSession.requests[0]["allow_redirects"] is False, str(FakeSession.requests))
    FakeSession.requests.clear()
    check("probe made one property read and one service call, and no commands", client.poll_count == probe_polls_before + 1 and client.last_refs == ["1022", "1023", "1025", "40100"] and len(client.probe_service_calls) == 1 and client.probe_service_calls[0] == ("2500", {}) and len(client.calls) == calls_before, f"{client.poll_count} {client.last_refs} {client.probe_service_calls}")
    check("probe does not touch poll health, boost or refresh", coordinator.health.failures == health_before and coordinator._command_refresh_cancel is None and coordinator._boost_until == boost_before)  # noqa: SLF001
    client.poll_count, client.last_refs = probe_polls_before, polled_refs_before

    coordinator.last_command = {"service": "clean_control", "inputs": {"mode": 1}, "output": {"2706": 0}}
    dump_cmd = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    check("diagnostics contain the last_command", dump_cmd["robots"][0]["last_command"] == {"service": "clean_control", "inputs": {"mode": 1}, "output": {"2706": 0}}, str(dump_cmd["robots"][0]["last_command"]))
    client.poll_count, client.last_refs = probe_polls_before, polled_refs_before
    client.probe_service_calls.clear()

    # a failing URL fetch becomes an error entry for that URL only
    FakeSession.fail = TimeoutError()
    dump_url_fail = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    url_fail = dump_url_fail["robots"][0]["probe"]
    check("a failing URL fetch is reported per URL", [c.get("error") for c in url_fail["url_checks"]] == ["TimeoutError", "TimeoutError"] and url_fail["properties"]["clean_params"]["type"] == "json", str(url_fail["url_checks"]))
    FakeSession.fail = None

    # an Imou error in the probe never fails the diagnostics download
    client.fail_with = api.ImouApiError("OP1001", "probe boom")
    dump_fail = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    probe_fail = dump_fail["robots"][0]["probe"]
    check("an ImouError in the probe becomes a per-part error", probe_fail["properties"] == {"error": "OP1001"} and probe_fail["services"]["clean_list"] == {"error": "OP1001"} and probe_fail["url_checks"] == [], str(probe_fail))
    check("diagnostics still complete when the probe fails", dump_fail["robots"][0]["things_model"]["properties"] and dump_fail["robots"][0]["values"]["work_status"] == 3 and coordinator.health.failures == health_before)
    client.fail_with = None
    client.poll_count, client.last_refs = probe_polls_before, polled_refs_before
    client.probe_service_calls.clear()
    diagnostics.async_get_clientsession = real_session_getter

    # ---- schedules sensor, schedule fetching ---------------------------------------------------------
    import time  # noqa: PLC0415
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415

    sched = state("schedules")
    sched_entry = ent_reg.async_get(ids["schedules"])
    check("schedules sensor: 2 of the 3 schedules are enabled", sched.state == "2", str(sched.state))
    check("schedules sensor: translated name, translation key, not a diagnostic entity", sched.name == "RV-L11-A-TEST Schedules" and sched_entry.translation_key == "schedules" and sched_entry.entity_category is None, f"{sched.name} {sched_entry.entity_category}")
    schedule_attrs = sched.attributes["schedules"]
    check("schedules sensor: one attribute entry per schedule", len(schedule_attrs) == 3 and all(set(a) == {"enabled", "active", "start", "time", "days", "repeat", "rooms"} for a in schedule_attrs), str(schedule_attrs))
    check("schedules sensor: enabled, raw active, raw repeat", [(a["enabled"], a["active"], a["repeat"]) for a in schedule_attrs] == [(True, True, []), (False, True, [0]), (True, True, [])], str(schedule_attrs))
    check("schedules sensor: a one-off schedule's start is ISO 8601 with a time zone (the timestamp of the robot), a repeating one has no date", [a["start"] is None or (datetime.fromisoformat(a["start"]).tzinfo is not None and int(datetime.fromisoformat(a["start"]).timestamp())) for a in schedule_attrs] == [1794224460, True, 1794400000] and schedule_attrs[1]["start"] is None, str(schedule_attrs))
    check("schedules sensor: a repeating schedule (period [0], startTime 50400) shows time 14:00 and days ['sun'] (verified: the Imou app shows Sun 14:00)", schedule_attrs[1]["time"] == "14:00" and schedule_attrs[1]["days"] == ["sun"], str(schedule_attrs[1]))
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    check("schedules sensor: a one-off schedule has a time (local, from its timestamp) and no days", [(a["time"], a["days"]) for a in (schedule_attrs[0], schedule_attrs[2])] == [(datetime.fromtimestamp(t, ZoneInfo(hass.config.time_zone)).strftime("%H:%M"), []) for t in (1794224460, 1794400000)], str(schedule_attrs))
    check("schedules sensor: rooms by name, 'whole_house' without rooms, equal names get the id", [a["rooms"] for a in schedule_attrs] == [["Kantoor"], "whole_house", ["Slaapkamer (3)", "Slaapkamer (4)"]], str(schedule_attrs))
    check("the schedules sensor has the calendar icon", ICONS_JSON["entity"]["sensor"]["schedules"]["default"] == "mdi:calendar-clock")
    check("room names: labels where aligned, equal names get the id", coordinator.room_names() == {2: "Kantoor", 3: "Slaapkamer (3)", 4: "Slaapkamer (4)"}, str(coordinator.room_names()))

    client.probe_service_calls.clear()
    for _ in range(3):
        await coordinator.async_refresh()
    check("the schedules are not read again within the hour (polls do not fetch them)", client.probe_service_calls == [], str(client.probe_service_calls))
    check("the next read is due in about an hour", 3500 < coordinator._schedules_due - time.monotonic() <= 3600, str(coordinator._schedules_due - time.monotonic()))  # noqa: SLF001
    coordinator._schedules_due = time.monotonic() - 1  # an hour later  # noqa: SLF001
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    check("an hour later one poll reads the schedules again (clean_list 2500, no inputs)", client.probe_service_calls == [("2500", {})], str(client.probe_service_calls))
    check("reading the schedules is no command: no pending refresh", coordinator._command_refresh_cancel is None)  # noqa: SLF001

    # a failing schedule fetch never fails or slows the property poll
    client.probe_service_calls.clear()
    client.fail_schedules_with = api.ImouConnectionError("TIMEOUT", "iotDeviceControl")
    coordinator._schedules_due = 0.0  # noqa: SLF001
    polls_before = client.poll_count
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("a failing schedule fetch: the poll still succeeds, health is clean, entities stay available", coordinator.last_update_success and coordinator.health.failures == 0 and client.poll_count == polls_before + 1 and state("vacuum").state == "docked" and state("schedules").state == "2", f"{coordinator.last_update_success} {coordinator.health.failures}")
    check("... the previous schedules are kept", coordinator.schedules is not None and len(coordinator.schedules.schedules) == 3)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    check("... and the failure is not retried on every poll (about every 15 minutes)", len(client.probe_service_calls) == 1 and 800 < coordinator._schedules_due - time.monotonic() <= 900, f"{len(client.probe_service_calls)} {coordinator._schedules_due - time.monotonic()}")  # noqa: SLF001
    coordinator._schedules_fetched_at = None  # the 60 second cache would skip the fetch  # noqa: SLF001
    check("... an on-demand refresh reports the failure and keeps the schedules", await coordinator.async_refresh_schedules() is False and coordinator.schedules is not None and len(coordinator.schedules.schedules) == 3)
    client.fail_schedules_with = None
    client.fail_with = api.ImouConnectionError("TIMEOUT", "getIotDeviceProperties")
    coordinator._schedules_due = 0.0  # noqa: SLF001
    client.probe_service_calls.clear()
    await coordinator.async_refresh()  # a failed property poll: the schedules are not even tried
    client.fail_with = None
    check("a failed property poll does not try the schedules", client.probe_service_calls == [] and coordinator.health.failures == 1)
    await coordinator.async_refresh()
    check("... the next good poll reads them and the poll health recovers", len(client.probe_service_calls) == 1 and coordinator.health.failures == 0)
    client.probe_service_calls.clear()
    client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": "garbage"}
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    check("an unusable schedules response keeps the previous schedules", await coordinator.async_refresh_schedules() is False and len(coordinator.schedules.schedules) == 3)
    client.timer_value = copy.deepcopy(SCHEDULES_VALUE)

    # the schedules sensor never fails on a start time that cannot be converted (rooms.py already drops implausible ones)
    rooms_module = importlib.import_module(f"{PKG}.rooms")
    real_schedules = coordinator.schedules
    odd_starts = [1794224460000, -62135596801, 10**20, 253402300800, None, 1794224460]
    coordinator.schedules = rooms_module.ScheduleList(tz_offset=0, schedules=[rooms_module.Schedule(enabled=True, active=True, start=value, end=None) for value in odd_starts])
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    odd_attrs = state("schedules").attributes["schedules"]
    check("schedules sensor: a start that cannot be converted is None, the others stay", state("schedules").state == "6" and [a["start"] is None for a in odd_attrs] == [True] * 5 + [False], str([a["start"] for a in odd_attrs]))
    check("schedules sensor: a valid start is still ISO 8601", int(datetime.fromisoformat(odd_attrs[5]["start"]).timestamp()) == 1794224460, str(odd_attrs[5]))
    coordinator.schedules = real_schedules
    coordinator.async_update_listeners()

    # on-demand refreshes: cached for 60 seconds
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    client.probe_service_calls.clear()
    results = [await coordinator.async_refresh_schedules(), await coordinator.async_refresh_schedules()]
    check("async_refresh_schedules: the second call within a minute is served from the cache", results == [True, True] and client.probe_service_calls == [("2500", {})], f"{results} {client.probe_service_calls}")
    coordinator._schedules_fetched_at = time.monotonic() - const.SCHEDULE_MIN_REFRESH_SECONDS - 1  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    check("... after a minute it reads again", len(client.probe_service_calls) == 2, str(client.probe_service_calls))
    check("the cache is a constant of 60 seconds", const.SCHEDULE_MIN_REFRESH_SECONDS == 60)

    client.calls.clear()
    client.probe_service_calls.clear()

    # ---- room cleaning (Home Assistant 2026.3 and newer) ----------------------------------------------------
    from homeassistant.components import vacuum as vacuum_component  # noqa: PLC0415

    if ha_clean_area() is None:
        check("without CLEAN_AREA (Home Assistant 2026.1): the feature and the segment API are absent, the rest is unchanged", not hasattr(vacuum_component.StateVacuumEntity, "async_get_segments") and not (state("vacuum").attributes["supported_features"] & 16384) and state("vacuum").attributes["supported_features"] == 13116)
    else:
        vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
        vac_reg = ent_reg.async_get(ids["vacuum"])
        issue_registry = ir.async_get(hass)
        issue_id = f"segments_changed_{vac_reg.id}"
        created: list[Any] = []
        original_create = vac_entity.async_create_segments_issue
        vac_entity.async_create_segments_issue = lambda: (created.append(1), original_create())[1]

        def seg(segments: list[Any]) -> list[tuple[str, str]]:
            return [(s.id, s.name) for s in segments]

        async def fresh_segments() -> Any:
            coordinator._schedules_fetched_at = None  # the 60 second cache would hide the change  # noqa: SLF001
            return await vac_entity.async_get_segments()

        client.probe_service_calls.clear()
        segments = await fresh_segments()
        check("segments: the rooms of the schedules, sorted by id, ids are strings", seg(segments) == [("2", "Kantoor"), ("3", "Slaapkamer (3)"), ("4", "Slaapkamer (4)")], str(seg(segments)))
        check("segments: the schedules are refreshed on demand", client.probe_service_calls == [("2500", {})], str(client.probe_service_calls))
        check("segments: no issue while the user has not mapped any areas", created == [] and issue_registry.async_get_issue("vacuum", issue_id) is None)

        mapped = [{"id": "2", "name": "Kantoor"}, {"id": "3", "name": "Slaapkamer (3)"}, {"id": "4", "name": "Slaapkamer (4)"}]
        ent_reg.async_update_entity_options(ids["vacuum"], "vacuum", {"last_seen_segments": mapped})
        await hass.async_block_till_done()
        await fresh_segments()
        check("segments: no issue when every room was mapped", created == [] and issue_registry.async_get_issue("vacuum", issue_id) is None)

        client.timer_value["value"].append({"unlock": True, "cleanAreas": [5], "segmentTagIds": ["Wc"], "startTime": 1794500000})
        segments = await fresh_segments()
        check("segments: a new room appears", seg(segments) == [("2", "Kantoor"), ("3", "Slaapkamer (3)"), ("4", "Slaapkamer (4)"), ("5", "Wc")], str(seg(segments)))
        check("segments: a room the user has not mapped creates the repair issue", created == [1] and issue_registry.async_get_issue("vacuum", issue_id) is not None, str(created))
        await fresh_segments()
        coordinator._schedules_fetched_at = None  # noqa: SLF001
        await coordinator.async_refresh_schedules()
        await hass.async_block_till_done()
        check("segments: the issue is created once per set of ids", created == [1], str(created))

        client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": [SCHEDULES_VALUE["value"][0]]}  # the other schedules were deleted
        segments = await fresh_segments()
        check("segments: rooms of deleted schedules stay (from last_seen_segments) with their last name", seg(segments) == [("2", "Kantoor"), ("3", "Slaapkamer (3)"), ("4", "Slaapkamer (4)")], str(seg(segments)))
        client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": [SCHEDULES_VALUE["value"][0], {"unlock": True, "cleanAreas": [3], "segmentTagIds": ["Kast"], "startTime": 1}]}
        segments = await fresh_segments()
        check("segments: a newer name replaces the last-seen name", seg(segments) == [("2", "Kantoor"), ("3", "Kast"), ("4", "Slaapkamer (4)")], str(seg(segments)))
        client.fail_schedules_with = api.ImouApiError("OP1001", "boom")
        segments = await fresh_segments()
        check("segments: when the refresh fails the cached schedules are used", seg(segments) == [("2", "Kantoor"), ("3", "Kast"), ("4", "Slaapkamer (4)")], str(seg(segments)))
        client.fail_schedules_with = None

        # two segment reads within a minute: one clean_list call (the second is served from the cache)
        client.probe_service_calls.clear()
        await fresh_segments()
        await vac_entity.async_get_segments()
        check("segments: two reads within a minute call clean_list once", client.probe_service_calls == [("2500", {})], str(client.probe_service_calls))
        client.calls.clear()
        await vac_entity.async_clean_segments(["3", "4"])
        want = ("control", "TESTDEVICE0001", "2700", {"2701": 1, "2702": 1, "2703": 0, "2704": "[3,4]"})
        check("clean_segments(['3','4']) sends exactly status 1, mode 1, plan 0, partition_area '[3,4]'", client.calls == [want], str(client.calls))
        check("clean_segments is recorded as last_command", coordinator.last_command["service"] == "clean_control" and coordinator.last_command["inputs"] == {"status": 1, "mode": 1, "plan": 0, "partition_area": {"type": "json", "value": [3, 4]}}, str(coordinator.last_command))
        client.calls.clear()
        await vac_entity.async_clean_segments(["2"])
        check("clean_segments(['2']) -> '[2]'", client.calls == [("control", "TESTDEVICE0001", "2700", {"2701": 1, "2702": 1, "2703": 0, "2704": "[2]"})], str(client.calls))
        client.calls.clear()
        await vac_entity.async_clean_segments([])
        check("clean_segments([]) is a no-op", client.calls == [])
        try:
            await vac_entity.async_clean_segments(["3", "kitchen"])
        except ServiceValidationError as err:
            check("an invalid room id raises invalid_room with the id, and nothing is sent", err.translation_key == "invalid_room" and err.translation_placeholders == {"room": "kitchen"} and client.calls == [], f"{err.translation_key} {err.translation_placeholders} {client.calls}")
        else:
            check("an invalid room id raises invalid_room", False, "no error")
        client.fail_control_with = api.ImouApiError("OP2000", "service refused")
        try:
            await vac_entity.async_clean_segments(["3"])
        except HomeAssistantError as err:
            check("a refused room clean is reported with the cloud's code", "OP2000" in str(err))
        else:
            check("a refused room clean is reported", False, "no error")
        client.fail_control_with = None
        vac_entity.async_create_segments_issue = original_create
        ent_reg.async_update_entity_options(ids["vacuum"], "vacuum", None)
        ir.async_delete_issue(hass, "vacuum", issue_id)
        client.timer_value = copy.deepcopy(SCHEDULES_VALUE)
        await coordinator.async_refresh_schedules()
        await hass.async_block_till_done()

    # ---- room buttons follow the rooms of the schedules ----------------------------------------------
    def button_state(room_id: int) -> Any:
        entity_id = ent_reg.async_get_entity_id("button", "imou_vacuum", f"TESTDEVICE0001_clean_room_{room_id}")
        return hass.states.get(entity_id) if entity_id else None

    client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": [{"unlock": True, "cleanAreas": [2, 7], "startTime": 5}]}
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    await hass.async_block_till_done()
    check("room buttons: a room that shows up later gets a button (default name reads 'room 7')", button_state(7) is not None and button_state(7).name == "RV-L11-A-TEST Clean room 7", str(button_state(7)))
    check("room buttons: a room that is no longer in the schedules is unavailable, the others are not", [button_state(r).state for r in (2, 3, 4, 7)] == ["unknown", "unavailable", "unavailable", "unknown"], str([button_state(r).state for r in (2, 3, 4, 7)]))
    client.timer_value = copy.deepcopy(SCHEDULES_VALUE)
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    await hass.async_block_till_done()
    check("room buttons: a room that comes back is available again (no second entity)", [button_state(r).state for r in (2, 3, 4)] == ["unknown"] * 3 and {e.unique_id for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if "_clean_room_" in e.unique_id} >= {f"TESTDEVICE0001_clean_room_{r}" for r in (2, 3, 4, 7)}, str(sorted(e.unique_id for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if "_clean_room_" in e.unique_id)))

    # ---- options flow: menu, room names, areas ------------------------------------------------------
    def suggested(result: Any) -> Any:
        key = next(k for k in result["data_schema"].schema if str(k) == "room_names")
        return key.description["suggested_value"]

    async def options_step(step: str) -> Any:
        """Start the options flow (one robot) and choose a step in the menu."""
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "menu", result
        return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})

    coordinator._schedules_fetched_at = None  # the 60 second cache would hide the change  # noqa: SLF001
    client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": [{"unlock": True, "cleanAreas": [], "startTime": 5}]}
    probe_calls_menu = len(client.probe_service_calls)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    check("options flow: one robot goes straight to the menu (Link rooms to areas, Rename rooms)", result["type"] == "menu" and result["step_id"] == "menu" and result["menu_options"] == ["room_areas", "rooms"] and result["description_placeholders"] == {"robot": "RV-L11-A-TEST"}, str(result))
    check("options flow: the menu does not read the schedules yet", len(client.probe_service_calls) == probe_calls_menu, str(client.probe_service_calls))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    check("options flow: a robot without known rooms aborts with no_rooms (rooms step)", result["type"] == "abort" and result["reason"] == "no_rooms", str(result))
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    result = await options_step("room_areas")
    check("options flow: a robot without known rooms aborts with no_rooms (room_areas step)", result["type"] == "abort" and result["reason"] == "no_rooms", str(result))
    client.timer_value = copy.deepcopy(SCHEDULES_VALUE)
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    probe_calls_before = len(client.probe_service_calls)
    result = await options_step("rooms")
    flow_id = result["flow_id"]
    check("options flow: the rooms step (Rename rooms, advanced) still shows its form", result["type"] == "form" and result["step_id"] == "rooms" and result["description_placeholders"] == {"robot": "RV-L11-A-TEST"}, str(result))
    check("options flow: reads the schedules once", len(client.probe_service_calls) == probe_calls_before + 1, str(client.probe_service_calls))
    check("options flow: the field is a multiline text", [str(k) for k in result["data_schema"].schema] == ["room_names"] and next(iter(result["data_schema"].schema.values())).config["multiline"] is True)
    check("options flow: prefilled with the labels (and defaults), one 'id = name' per line, no id suffix", suggested(result) == "2 = Kantoor\n3 = Slaapkamer\n4 = Slaapkamer", repr(suggested(result)))
    result = await hass.config_entries.options.async_configure(flow_id, {"room_names": "2 = Werkkamer\nthis is wrong"})
    check("options flow: invalid text -> invalid_room_names, the text is kept", result["type"] == "form" and result["errors"] == {"base": "invalid_room_names"} and suggested(result) == "2 = Werkkamer\nthis is wrong", str(result))
    check("options flow: nothing was saved yet", dict(entry.options) == {})
    result = await hass.config_entries.options.async_configure(flow_id, {"room_names": "2 = Werkkamer\n\n3 = Blauwe kamer\n4 = Slaapkamer\n"})
    check("options flow: saving creates the entry options per robot", result["type"] == "create_entry" and result["data"] == {"room_names": {"TESTDEVICE0001": {"2": "Werkkamer", "3": "Blauwe kamer"}}} and dict(entry.options) == result["data"], str(result))
    check("options flow: a name equal to the automatic label (4 = Slaapkamer) is not stored", "4" not in entry.options["room_names"]["TESTDEVICE0001"])
    await hass.async_block_till_done()
    check("options flow: the entry reloads and is loaded again", entry.state is config_entries.ConfigEntryState.LOADED, str(entry.state))
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("saved names are used (they win over the labels)", coordinator.room_names() == {2: "Werkkamer", 3: "Blauwe kamer", 4: "Slaapkamer"}, str(coordinator.room_names()))
    check("the schedules sensor shows the new names", [a["rooms"] for a in state("schedules").attributes["schedules"]] == [["Werkkamer"], "whole_house", ["Blauwe kamer", "Slaapkamer"]], str(state("schedules").attributes["schedules"]))
    if ha_clean_area() is not None:
        vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
        segments = await vac_entity.async_get_segments()
        check("the segments use the saved names", seg(segments) == [("2", "Werkkamer"), ("3", "Blauwe kamer"), ("4", "Slaapkamer")], str(seg(segments)))
    result = await options_step("rooms")
    check("options flow: the saved names are prefilled the next time", suggested(result) == "2 = Werkkamer\n3 = Blauwe kamer\n4 = Slaapkamer", repr(suggested(result)))
    hass.config_entries.options.async_abort(result["flow_id"])

    # two robots: the first step picks the robot, saving merges with the other robot's names
    second = copy.copy(coordinator)
    second.device = api.ImouDevice(device_id="SECONDROBOT0002", product_id="PRODUCT_ID", name="Second robot", model="L11-A", status="online", version="0.4.50", brand="lechange")
    entry.runtime_data.coordinators["SECONDROBOT0002"] = second
    result = await hass.config_entries.options.async_init(entry.entry_id)
    options_list = next(iter(result["data_schema"].schema.values())).config["options"]
    check("options flow: with two robots the first step is a robot select (by name)", result["type"] == "form" and result["step_id"] == "init" and [o["label"] for o in options_list] == ["RV-L11-A-TEST", "Second robot"], str(result))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"robot": "SECONDROBOT0002"})
    check("options flow: the menu is for the chosen robot", result["type"] == "menu" and result["step_id"] == "menu" and result["description_placeholders"] == {"robot": "Second robot"}, str(result))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    check("options flow: the rooms form is for the chosen robot", result["type"] == "form" and result["step_id"] == "rooms" and result["description_placeholders"] == {"robot": "Second robot"}, str(result))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_names": "2 = Keuken"})
    check("options flow: the other robot's names are kept", result["type"] == "create_entry" and result["data"] == {"room_names": {"TESTDEVICE0001": {"2": "Werkkamer", "3": "Blauwe kamer"}, "SECONDROBOT0002": {"2": "Keuken"}}}, str(result))
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("entry is loaded again with its one real robot", entry.state is config_entries.ConfigEntryState.LOADED and list(entry.runtime_data.coordinators) == ["TESTDEVICE0001"], str(entry.state))
    check("the first robot keeps its own names", coordinator.room_names()[2] == "Werkkamer")
    result = await options_step("rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_names": ""})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("an empty text clears the names of that robot (labels and defaults again)", result["type"] == "create_entry" and coordinator.room_names() == {2: "Kantoor", 3: "Slaapkamer (3)", 4: "Slaapkamer (4)"}, str(coordinator.room_names()))

    # only real user overrides are stored: not the default name, not the current automatic name
    result = await options_step("rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_names": "2 = Room 2\n3 = Slaapkamer\n4 = Eigen kamer"})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("options flow: default ('Room 2') and automatic ('Slaapkamer') names are not stored, a real override is", result["type"] == "create_entry" and result["data"]["room_names"]["TESTDEVICE0001"] == {"4": "Eigen kamer"}, str(result))
    check("... the displayed names are still right", coordinator.room_names() == {2: "Kantoor", 3: "Slaapkamer", 4: "Eigen kamer"}, str(coordinator.room_names()))
    result = await options_step("rooms")
    check("... and they are prefilled again", suggested(result) == "2 = Kantoor\n3 = Slaapkamer\n4 = Eigen kamer", repr(suggested(result)))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_names": "2 = Werkkamer"})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("options flow: removing a line removes that override", result["data"]["room_names"]["TESTDEVICE0001"] == {"2": "Werkkamer"} and coordinator.room_names()[4] == "Slaapkamer (4)", str(result))

    # ---- options flow: link rooms to areas ---------------------------------------------------------
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    area_reg = ar.async_get(hass)
    kitchen, living, bath = (area_reg.async_create(name) for name in ("Kitchen", "Living room", "Bathroom"))

    def vacuum_options() -> dict[str, Any]:
        return json.loads(json.dumps(dict(ent_reg.async_get(ids["vacuum"]).options.get("vacuum", {}))))

    seeded = {
        "area_mapping": {kitchen.id: ["3"], "deleted_area": ["4"], living.id: ["9"]},
        "last_seen_segments": [{"id": "9", "name": "Nine", "group": None}, {"id": "3", "name": "Old three", "group": None}],
        "other_key": {"keep": "me"},
    }
    ent_reg.async_update_entity_options(ids["vacuum"], "vacuum", seeded)
    options_before = json.loads(json.dumps(dict(entry.options)))
    result = await options_step("room_areas")
    labels = [str(k) for k in result["data_schema"].schema]
    check("room areas: one optional field per room, labelled 'Room <id>: <name>' (the user's name, else the app's label)", result["type"] == "form" and result["step_id"] == "room_areas" and labels == ["Room 2: Werkkamer", "Room 3: Slaapkamer", "Room 4: Slaapkamer"] and all(type(k).__name__ == "Optional" for k in result["data_schema"].schema) and result["description_placeholders"] == {"robot": "RV-L11-A-TEST"}, str(result))
    check("room areas: every field is an area selector", all(type(v).__name__ == "AreaSelector" for v in result["data_schema"].schema.values()), str(list(result["data_schema"].schema.values())))
    suggestions = {str(k): k.description.get("suggested_value") if k.description else None for k in result["data_schema"].schema}
    check("room areas: the current links of the vacuum are suggested (a deleted area is not)", suggestions == {"Room 2: Werkkamer": None, "Room 3: Slaapkamer": kitchen.id, "Room 4: Slaapkamer": None}, str(suggestions))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"Room 2: Werkkamer": living.id, "Room 4: Slaapkamer": bath.id})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("room areas: the rooms take the names of their areas, a room without an area keeps its name (none stored)", result["type"] == "create_entry" and result["data"]["room_names"]["TESTDEVICE0001"] == {"2": "Living room", "4": "Bathroom"} and dict(entry.options) == result["data"], str(result))
    check("room areas: the names of the other robot are kept", result["data"]["room_names"]["SECONDROBOT0002"] == {"2": "Keuken"}, str(result))
    check("room areas: the entry reloads and shows the new names", entry.state is config_entries.ConfigEntryState.LOADED and coordinator.room_names() == {2: "Living room", 3: "Slaapkamer", 4: "Bathroom"}, str(coordinator.room_names()))
    check("room areas: the Clean buttons are named after the areas", [state(f"clean_room_{r}").name for r in (2, 3, 4)] == ["RV-L11-A-TEST Clean Living room", "RV-L11-A-TEST Clean Slaapkamer", "RV-L11-A-TEST Clean Bathroom"], str([state(f"clean_room_{r}").name for r in (2, 3, 4)]))
    if ha_clean_area() is not None:
        check(
            "room areas (CLEAN_AREA): area_mapping = old minus the shown rooms plus the new links, ids as ascending strings; other rooms and other keys stay",
            vacuum_options()["area_mapping"] == {living.id: ["2", "9"], bath.id: ["4"]} and vacuum_options()["other_key"] == {"keep": "me"},
            str(vacuum_options()),
        )
        check(
            "room areas (CLEAN_AREA): last_seen_segments keeps its entries, renames the shown rooms and adds the new ones (ascending)",
            vacuum_options()["last_seen_segments"] == [{"id": "9", "name": "Nine", "group": None}, {"id": "3", "name": "Slaapkamer", "group": None}, {"id": "2", "name": "Living room", "group": None}, {"id": "4", "name": "Bathroom", "group": None}],
            str(vacuum_options()["last_seen_segments"]),
        )
        vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
        segments = await vac_entity.async_get_segments()
        check("room areas (CLEAN_AREA): the segments carry the names stored in last_seen_segments", seg(segments) == [("2", "Living room"), ("3", "Slaapkamer"), ("4", "Bathroom"), ("9", "Nine")], str(seg(segments)))
        check("room areas (CLEAN_AREA): no 'new rooms' repair issue after linking (every room is in last_seen_segments)", ir.async_get(hass).async_get_issue("vacuum", f"segments_changed_{ent_reg.async_get(ids['vacuum']).id}") is None)
    else:
        check("room areas (no CLEAN_AREA, Home Assistant 2026.1): the vacuum registry options are not touched", vacuum_options() == seeded, str(vacuum_options()))

    # the labels follow the new names; the links are the defaults; clearing every room removes the links but keeps the names
    result = await options_step("room_areas")
    suggestions = {str(k): k.description.get("suggested_value") if k.description else None for k in result["data_schema"].schema}
    if ha_clean_area() is not None:
        check("room areas: the second time the saved links are suggested, under the new labels", suggestions == {"Room 2: Living room": living.id, "Room 3: Slaapkamer": None, "Room 4: Bathroom": bath.id}, str(suggestions))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("room areas: submitting no area keeps the stored names", result["type"] == "create_entry" and result["data"]["room_names"]["TESTDEVICE0001"] == {"2": "Living room", "4": "Bathroom"} and coordinator.room_names() == {2: "Living room", 3: "Slaapkamer", 4: "Bathroom"}, str(result))
    if ha_clean_area() is not None:
        check("room areas (CLEAN_AREA): clearing the rooms removes them from the mapping; the room that was not shown stays", vacuum_options()["area_mapping"] == {living.id: ["9"]} and vacuum_options()["other_key"] == {"keep": "me"}, str(vacuum_options()))
    # an unknown (deleted) area counts as no area; a real area again; one area for two rooms
    result = await options_step("room_areas")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"Room 2: Living room": "no_such_area", "Room 3: Slaapkamer": kitchen.id, "Room 4: Bathroom": kitchen.id})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("room areas: an unknown area is ignored (that room keeps its name), two rooms may share an area", result["type"] == "create_entry" and result["data"]["room_names"]["TESTDEVICE0001"] == {"2": "Living room", "3": "Kitchen", "4": "Kitchen"} and coordinator.room_names() == {2: "Living room", 3: "Kitchen (3)", 4: "Kitchen (4)"}, f"{result} {coordinator.room_names()}")
    if ha_clean_area() is not None:
        check("room areas (CLEAN_AREA): a shared area maps both rooms; the names with the ' (id)' suffix of the new duplicate are stored as last seen", vacuum_options()["area_mapping"] == {living.id: ["9"], kitchen.id: ["3", "4"]} and [(e["id"], e["name"]) for e in vacuum_options()["last_seen_segments"]] == [("9", "Nine"), ("3", "Kitchen (3)"), ("2", "Living room"), ("4", "Kitchen (4)")], str(vacuum_options()))
        segments = await hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"]).async_get_segments()
        check("room areas (CLEAN_AREA): ... and these are the names the segments show", seg(segments) == [("2", "Living room"), ("3", "Kitchen (3)"), ("4", "Kitchen (4)"), ("9", "Nine")], str(seg(segments)))
    # a robot whose vacuum is not in the entity registry: names are still saved, nothing else is written
    ent_reg.async_update_entity_options(ids["vacuum"], "vacuum", None)
    ent_reg.async_remove(ids["vacuum"])
    result = await options_step("room_areas")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"Room 2: Living room": bath.id})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("room areas: without a vacuum in the entity registry the names are saved and nothing fails", result["type"] == "create_entry" and result["data"]["room_names"]["TESTDEVICE0001"]["2"] == "Bathroom", str(result))
    ids = {e.unique_id.split("_", 1)[1]: e.entity_id for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)}  # the vacuum entity is created again by the reload
    check("... and the reload creates the vacuum entity again", "vacuum" in ids and entry.state is config_entries.ConfigEntryState.LOADED, str(sorted(ids)))
    ent_reg.async_update_entity_options(ids["vacuum"], "vacuum", None)
    options_updated = {**options_before}
    hass.config_entries.async_update_entry(entry, options=options_updated)  # back to the options of before this block
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("room areas: back to the options from before (names as the earlier tests expect)", coordinator.room_names()[2] == "Werkkamer", str(coordinator.room_names()))

    # ---- rooms with a name of the user stay known when no schedule lists them -----------------------
    hass.config_entries.async_update_entry(entry, options={"room_names": {"TESTDEVICE0001": {"1": "Een", "2": "Twee", "3": "Drie"}}})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    client.timer_value = {"timeZone": 1, "timeZoneSec": 3600, "value": [{"unlock": True, "cleanAreas": [2], "startTime": 5, "segmentTagIds": ["Kantoor"]}]}
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    await hass.async_block_till_done()
    check("remembered rooms: names for rooms 1-3, the schedule lists only room 2 -> room_names has 1, 2 and 3", coordinator.room_names() == {1: "Een", 2: "Twee", 3: "Drie"} and coordinator.known_room_tags() == {1: None, 2: "Kantoor", 3: None}, str(coordinator.room_names()))
    check("remembered rooms: the Clean buttons of rooms 1, 2 and 3 are available", [button_state(r).state for r in (1, 2, 3)] == ["unknown"] * 3 and button_state(1).name == "RV-L11-A-TEST Clean Een", str([button_state(r) for r in (1, 2, 3)]))
    result = await options_step("rooms")
    check("remembered rooms: the rooms step lists rooms 1, 2 and 3", suggested(result) == "1 = Een\n2 = Twee\n3 = Drie", repr(suggested(result)))
    hass.config_entries.options.async_abort(result["flow_id"])
    result = await options_step("room_areas")
    check("remembered rooms: the areas step lists rooms 1, 2 and 3", [str(k) for k in result["data_schema"].schema] == ["Room 1: Een", "Room 2: Twee", "Room 3: Drie"], str([str(k) for k in result["data_schema"].schema]))
    hass.config_entries.options.async_abort(result["flow_id"])
    result = await options_step("rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_names": "2 = Twee"})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("remembered rooms: removing a line forgets the room", 1 not in coordinator.room_names() and coordinator.room_names()[2] == "Twee" and button_state(1).state == "unavailable", f"{coordinator.room_names()} {button_state(1).state}")
    hass.config_entries.async_update_entry(entry, options={"room_names": {"TESTDEVICE0001": {"2": "Werkkamer"}}})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    client.timer_value = copy.deepcopy(SCHEDULES_VALUE)
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    await hass.async_block_till_done()

    # the update listener reloads on an options change only, not on a data-only update (reauth reloads by itself)
    reloads: list[str] = []
    real_reload = hass.config_entries.async_reload

    async def counting_reload(entry_id: str, *args: Any, **kwargs: Any) -> Any:
        reloads.append(entry_id)
        return await real_reload(entry_id, *args, **kwargs)

    hass.config_entries.async_reload = counting_reload
    try:
        init_module = importlib.import_module(PKG)
        await init_module._async_options_updated(hass, entry)  # noqa: SLF001
        check("update listener: unchanged options -> no reload", reloads == [], str(reloads))
        hass.config_entries.async_update_entry(entry, data={**entry.data, "note": "data only"})
        await hass.async_block_till_done()
        check("update listener: a data-only update does not reload", reloads == [] and entry.state is config_entries.ConfigEntryState.LOADED, str(reloads))
        hass.config_entries.async_update_entry(entry, options={**entry.options, "room_names": {"TESTDEVICE0001": {"2": "Zed"}}})
        await hass.async_block_till_done()
        check("update listener: an options change reloads once", reloads == [entry.entry_id] and entry.state is config_entries.ConfigEntryState.LOADED, str(reloads))
        hass.config_entries.async_update_entry(entry, data={**entry.data, "note": "again"})
        await hass.async_block_till_done()
        check("update listener: after the reload the new options are the baseline (data-only still no reload)", reloads == [entry.entry_id], str(reloads))
    finally:
        hass.config_entries.async_reload = real_reload
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0001"]
    client = FakeClient.instance
    check("... and the changed option is in use", coordinator.room_names()[2] == "Zed", str(coordinator.room_names()))

    # ---- setup failure modes ----------------------------------------------------------------
    async def add_entry(app_id: str) -> Any:
        extra = config_entries.ConfigEntry(
            data={"app_id": app_id, "app_secret": "s", "region": "eu"}, discovery_keys={},
            domain="imou_vacuum", entry_id=app_id, minor_version=1, options={}, source="user",
            subentries_data=None, title="t", unique_id=app_id, version=1,
        )  # fmt: skip
        await hass.config_entries.async_add(extra)
        await hass.async_block_till_done()
        return extra

    FakeClient.devices_override = []
    empty = await add_entry("no-robots")
    check("account without robots -> setup error, no retry loop (saves quota)", empty.state is config_entries.ConfigEntryState.SETUP_ERROR and "reload" in (empty.reason or ""), f"{empty.state} {empty.reason}")
    await hass.config_entries.async_remove(empty.entry_id)
    FakeClient.devices_override = None
    FakeClient.model_error = api.ImouApiError("OP9999", "temporary problem")
    flaky = await add_entry("model-error")
    check("model lookup failed and no robot found -> retry later", flaky.state is config_entries.ConfigEntryState.SETUP_RETRY, str(flaky.state))
    await hass.config_entries.async_remove(flaky.entry_id)
    FakeClient.model_error = None
    FakeClient.devices_override = None

    FakeClient.initial_fail_with = api.ImouConnectionError("TIMEOUT", "getIotDeviceProperties")
    failing = await add_entry("first-poll-fails")
    check("first poll fails (no data to keep): setup is retried later", failing.state is config_entries.ConfigEntryState.SETUP_RETRY, str(failing.state))
    await hass.config_entries.async_remove(failing.entry_id)
    FakeClient.initial_fail_with = None

    # ---- fresh installs: which switches are enabled by default ---------------------------------------
    switch_keys = ("carpet_boost",)
    for label, app_id, robot_id, flags, expected_disabled in (
        ("the real L11-A", "fresh-l11a", "FRESHROBOT0001", {}, set()),
        ("a robot that is offline at the first poll", "fresh-offline", "FRESHROBOT0003", {"initial_status": "offline"}, set()),
        ("a robot that is asleep at the first poll", "fresh-sleep", "FRESHROBOT0004", {"initial_status": "sleep"}, set()),
    ):  # fmt: skip
        FakeClient.robot_id = robot_id
        for name, value in flags.items():
            setattr(FakeClient, name, value)
        fresh = await add_entry(app_id)
        regs = {e.unique_id.removeprefix(f"{robot_id}_"): e for e in er.async_entries_for_config_entry(ent_reg, fresh.entry_id)}
        if "initial_status" in flags:
            # no first online poll yet: the switches do not exist (so nothing is wrongly enabled), the rest does
            check(f"fresh install, {label}: no switch yet, the other 20 entities are there", not set(switch_keys) & set(regs) and len(regs) == 20 and fresh.state is config_entries.ConfigEntryState.LOADED, f"{sorted(regs)} {fresh.state}")
            fresh_client = FakeClient.instance
            fresh_client.status = "online"
            await fresh.runtime_data.coordinators[robot_id].async_refresh()
            await hass.async_block_till_done()
            regs = {e.unique_id.removeprefix(f"{robot_id}_"): e for e in er.async_entries_for_config_entry(ent_reg, fresh.entry_id)}
            check(f"fresh install, {label}: the first online poll adds the switch", set(switch_keys) <= set(regs), str(sorted(regs)))
        disabled = {k for k in switch_keys if regs[k].disabled_by is not None}
        check(f"fresh install, {label}: disabled by default = {sorted(expected_disabled)}", disabled == expected_disabled and fresh.state is config_entries.ConfigEntryState.LOADED, f"{disabled} {fresh.state}")
        check(f"fresh install, {label}: all 24 entities are created", len(regs) == 24, str(len(regs)))
        check(f"fresh install, {label}: the reported switch is on and says so", hass.states.get(regs["carpet_boost"].entity_id).state == "on" and hass.states.get(regs["carpet_boost"].entity_id).attributes["reported_by_device"] is True)
        await hass.config_entries.async_remove(fresh.entry_id)
        FakeClient.robot_id, FakeClient.initial_status = "TESTDEVICE0001", "online"

    # ---- unload ---------------------------------------------------------------------------------
    await hass.services.async_call("vacuum", "locate", {"entity_id": ids["vacuum"]}, blocking=True)
    check("a command refresh is pending before unload", coordinator._command_refresh_cancel is not None)
    check("unload succeeds", await hass.config_entries.async_unload(entry.entry_id) and entry.state is config_entries.ConfigEntryState.NOT_LOADED, str(entry.state))
    check("unload cancels the pending command refresh", coordinator._command_refresh_cancel is None)
    coordinator._async_after_command()
    check("a late command does not re-arm the timer after unload", coordinator._command_refresh_cancel is None)
    polls = client.poll_count
    await asyncio.sleep(3.5)
    check("no polling after unload", client.poll_count == polls)
    check("entities are unavailable after unload", hass.states.get(ids["vacuum"]).state == "unavailable")


def test_full_setup() -> None:
    config_dir = tempfile.mkdtemp(prefix="imou-smoke-")
    try:
        asyncio.run(full_setup_checks(config_dir))
    finally:
        # block_async_io (enabled for the run) also flags the main thread after the
        # loop has closed, so remove the temp dir from a worker thread.
        worker = threading.Thread(target=shutil.rmtree, args=(config_dir,), kwargs={"ignore_errors": True})
        worker.start()
        worker.join()


# ---------------------------------------------------------------------------
# 6. a second robot: the Imou RV1 Pro Max (issue #1), full setup with its fixtures
# ---------------------------------------------------------------------------
class RV1Client(FakeClient):
    """Fake client for the RV1 Pro Max (no network)."""

    report_station = True

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        self.props = copy.deepcopy(RV1_VALUES_JSON["properties"])
        # room/map properties: never polled, only read by the diagnostics probe
        self.props.update({"126200": [3, 4], "122300": ["Map one"], "120600": [], "121600": []})
        if RV1Client.report_station:  # (the real RV1 Pro Max never reports it; see rv1_dock_checks)
            self.props["121100"] = {"121101": True, "121102": 0, "121103": 0, "121104": 0}
        # clean_list: the real response of the user's robot (one whole-house schedule)
        self.clean_list_output: Any = copy.deepcopy(RV1_CLEAN_LIST_JSON["content"]["outputData"])

    async def async_list_devices(self) -> list[Any]:
        api = mod("api")
        return [api.ImouDevice(device_id="TESTDEVICE0002", product_id="PRODUCT_ID_RV1", name="RV1-PRO-MAX-TEST", model="RV1 Pro Max", status="online", version="1.0.0", brand="lechange")]

    async def async_get_product_model(self, product_id: str) -> dict[str, Any]:
        return copy.deepcopy(RV1_MODEL_JSON)

    async def async_set_properties(self, product_id: str, device_id: str, properties: dict[str, Any]) -> None:
        self.calls.append(("set", device_id, dict(properties)))
        if self.fail_set_with is not None:
            raise self.fail_set_with
        self.props.update(properties)

    async def async_control(self, product_id: str, device_id: str, ref: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
        if ref == "201600":  # clean_list (read-only): kept out of self.calls
            self.probe_service_calls.append((ref, dict(content or {})))
            if self.fail_schedules_with is not None:
                raise self.fail_schedules_with
            return {"content": {"outputData": copy.deepcopy(self.clean_list_output), "service": ref}}
        self.calls.append(("control", device_id, ref, dict(content or {})))
        return {"content": {"outputData": {"200921": 0}, "service": ref}}


async def rv1_setup_checks(config_dir: str) -> None:
    from homeassistant import bootstrap, config_entries, loader  # noqa: PLC0415
    from homeassistant.core import HomeAssistant  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    const = mod("const")
    package = importlib.import_module(PKG)
    package.ImouClient = RV1Client
    package.async_get_clientsession = lambda hass: None
    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config.language = "en"
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    await bootstrap.async_load_base_functionality(hass)
    await hass.async_start()
    try:
        # dock sensors are disabled by default (0.6.3); these exist enabled, like those of an earlier install
        for platform, key in (("binary_sensor", "dock_connected"), ("sensor", "clean_water_tank"), ("sensor", "dirty_water_tank"), ("sensor", "dust_bag")):
            er.async_get(hass).async_get_or_create(platform, "imou_vacuum", f"TESTDEVICE0002_{key}", suggested_object_id=f"rv1_pro_max_test_{key}")
        entry = config_entries.ConfigEntry(
            data={"app_id": "rv1-app", "app_secret": "s", "region": "eu"}, discovery_keys={},
            domain="imou_vacuum", entry_id="rv1-app", minor_version=1, options={}, source="user",
            subentries_data=None, title="t", unique_id="rv1-app", version=1,
        )  # fmt: skip
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        check("RV1 Pro Max: entry is loaded", entry.state is config_entries.ConfigEntryState.LOADED, str(entry.state))
        client = FakeClient.instance
        coordinator = entry.runtime_data.coordinators["TESTDEVICE0002"]
        ent_registry = er.async_get(hass)
        ids = {e.unique_id.split("_", 1)[1]: e.entity_id for e in er.async_entries_for_config_entry(ent_registry, entry.entry_id)}

        def state(key: str) -> Any:
            return hass.states.get(ids[key])

        async def call(domain: str, service: str, entity: str, **data: Any) -> None:
            client.calls.clear()
            await hass.services.async_call(domain, service, {"entity_id": ids[entity], **data}, blocking=True)

        from homeassistant.components import vacuum as vacuum_component  # noqa: PLC0415
        from homeassistant.components.vacuum import VacuumEntityFeature  # noqa: PLC0415

        check("RV1 Pro Max: polled refs include windpower 118900 and not back_charging/water", "118900" in client.last_refs and "1019" not in client.last_refs and "1028" not in client.last_refs, str(client.last_refs))
        check("RV1 Pro Max: battery -38 shows 38 %", state("battery").state == "38", str(state("battery")))
        check("RV1 Pro Max: power_status 112200 is polled", "112200" in client.last_refs, str(client.last_refs))
        check("RV1 Pro Max: Charging binary sensor exists; no power_status reported yet, so the battery sign decides (-38 is charging)", "charging" in ids and state("charging").state == "on", str(state("charging")))
        client.props["112200"] = 1
        await coordinator.async_refresh()
        check("RV1 Pro Max: Charging is on for power_status 1 (battery_charging)", state("charging").state == "on" and state("charging").attributes["device_class"] == "battery_charging" and state("charging").name == "RV1-PRO-MAX-TEST Charging", f"{state('charging').state} {dict(state('charging').attributes)}")
        client.props["112200"] = 0
        await coordinator.async_refresh()
        check("RV1 Pro Max: Charging is off for power_status 0", state("charging").state == "off", str(state("charging").state))
        del client.props["112200"]
        client.props["11600"] = 38
        await coordinator.async_refresh()
        check("RV1 Pro Max: no power_status and a positive battery: Charging is unknown", state("charging").state == "unknown", str(state("charging").state))
        client.props["11600"] = -38
        await coordinator.async_refresh()
        check("RV1 Pro Max: no power_status and a negative battery: Charging is on again", state("charging").state == "on", str(state("charging").state))
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        check("RV1 Pro Max: no model support repair issue", ir.async_get(hass).async_get_issue("imou_vacuum", "model_support_TESTDEVICE0002") is None)
        features = int(state("vacuum").attributes["supported_features"])
        check("RV1 Pro Max: RETURN_HOME is supported (back_station)", bool(features & int(VacuumEntityFeature.RETURN_HOME)), str(features))
        await call("vacuum", "return_to_base", "vacuum")
        check("RV1 Pro Max: return_to_base writes back_station {119300: 1}", client.calls == [("set", "TESTDEVICE0002", {"119300": 1})], str(client.calls))
        check("RV1 Pro Max: fan_speed_list is quiet/standard/strong/max (no off), current 2 -> standard", list(state("vacuum").attributes["fan_speed_list"]) == ["quiet", "standard", "strong", "max"] and state("vacuum").attributes["fan_speed"] == "standard", str(dict(state("vacuum").attributes)))
        check("RV1 Pro Max: suction select has the same options", state("suction_level").attributes["options"] == ["quiet", "standard", "strong", "max"] and state("suction_level").state == "standard", str(dict(state("suction_level").attributes)))
        client.props["118900"] = 0
        await coordinator.async_refresh()
        check("RV1 Pro Max: a robot that reports suction 0 shows 'off' (vacuum and select), which is still not offered", state("vacuum").attributes["fan_speed"] == "off" and state("suction_level").state == "off" and "off" not in state("vacuum").attributes["fan_speed_list"] and state("suction_level").attributes["options"] == ["quiet", "standard", "strong", "max", "off"], str(dict(state("suction_level").attributes)))
        for domain, service, entity, field in (("vacuum", "set_fan_speed", "vacuum", "fan_speed"), ("select", "select_option", "suction_level", "option")):
            try:
                await call(domain, service, entity, **{field: "off"})
            except Exception as err:  # noqa: BLE001
                check(f"RV1 Pro Max: setting suction 'off' from {domain} is rejected and nothing is written", client.calls == [], f"{type(err).__name__} {client.calls}")
            else:
                check(f"RV1 Pro Max: setting suction 'off' from {domain} is rejected and nothing is written", False)
        client.props["118900"] = 2
        await coordinator.async_refresh()
        check("RV1 Pro Max: back to standard, the select offers the four levels again", state("suction_level").state == "standard" and state("suction_level").attributes["options"] == ["quiet", "standard", "strong", "max"], str(dict(state("suction_level").attributes)))
        check("RV1 Pro Max: no water level select", "mop_water_level" not in ids)
        await call("vacuum", "set_fan_speed", "vacuum", fan_speed="max")
        check("RV1 Pro Max: fan speed max writes WindPower 4 as a property write (no WindWater)", client.calls == [("set", "TESTDEVICE0002", {"118900": 4})], str(client.calls))
        await call("select", "select_option", "suction_level", option="strong")
        check("RV1 Pro Max: suction select strong writes WindPower 3", client.calls == [("set", "TESTDEVICE0002", {"118900": 3})], str(client.calls))
        client.props["12300"] = 15
        await coordinator.async_refresh()
        check("RV1 Pro Max: work_status 15 -> docked and washing_mop", state("vacuum").state == "docked" and state("status").state == "washing_mop", f"{state('vacuum').state} {state('status').state}")
        client.props["12300"] = 18
        await coordinator.async_refresh()
        await call("vacuum", "start", "vacuum")
        check("RV1 Pro Max: start while mapping is paused -> continue {200901: 2}", state("status").state == "mapping_paused" and client.calls == [("control", "TESTDEVICE0002", "200900", {"200901": 2})], str(client.calls))
        client.props["12300"] = 3
        await coordinator.async_refresh()

        if ha_clean_area() is not None:
            vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
            client.calls.clear()
            await vac_entity.async_clean_segments(["3", "4"])
            check("RV1 Pro Max: clean_segments sends partition_area [3, 4] as a list and no plan input", client.calls == [("control", "TESTDEVICE0002", "200900", {"200901": 1, "200902": 1, "200903": [3, 4]})], str(client.calls))
        else:
            client.calls.clear()
            await coordinator.async_clean_rooms([3, 4])
            check("RV1 Pro Max: clean_rooms sends partition_area [3, 4] as a list and no plan input", client.calls == [("control", "TESTDEVICE0002", "200900", {"200901": 1, "200902": 1, "200903": [3, 4]})], str(client.calls))

        # diagnostics probe: only declared identifiers, one property call
        diagnostics = importlib.import_module(f"{PKG}.diagnostics")
        polls_before = client.poll_count
        dump = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
        report = dump["robots"][0]["support_report"]
        check("RV1 Pro Max: diagnostics support_report (partly verified, nothing missing or unknown)", report["partly_verified"] is True and report["verified_model"] is False and report["missing_core"] == [] and report["unknown_enum_labels"] == {}, str(report))
        probe_props = dump["robots"][0]["probe"]["properties"]
        check("RV1 Pro Max: probe reads exactly the declared room/map properties", set(probe_props) == {"partition_area", "map_name", "sequence", "area_clean_plan", "swp_allcleanplan", "swp_cleansequence"}, str(set(probe_props)))
        check("RV1 Pro Max: probe is one property call with the 6 refs, no large ones", client.poll_count == polls_before + 1 and sorted(client.last_refs) == ["120600", "121600", "122300", "126200", "145800", "146000"], f"{client.poll_count - polls_before} {client.last_refs}")
        check("RV1 Pro Max: probe never reads curpath_data, virtual_area or the other swp_* properties", not {"121300", "121400", "146100", "145700"} & set(client.last_refs))

        # --- 0.5.0: mop intensity, dock status, schedules ---------------------
        for key in ("mop_intensity", "dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag", "schedules"):
            check(f"RV1 Pro Max: entity {key} exists", key in ids, str(sorted(ids)))
        await coordinator.async_refresh()
        check("RV1 Pro Max: no schedule switches", not any(k.startswith("schedule_") for k in ids), str(sorted(ids)))
        check("RV1 Pro Max: polled refs include mop_speed 119100 and station_status 121100", {"119100", "121100"} <= set(client.last_refs), str(client.last_refs))
        check("RV1 Pro Max: water volume select is named 'Water volume', options off/low/medium, unknown while not reported", state("mop_intensity").name == "RV1-PRO-MAX-TEST Water volume" and state("mop_intensity").attributes["options"] == ["off", "low", "medium"] and state("mop_intensity").state == "unknown", str(dict(state("mop_intensity").attributes)))
        check("RV1 Pro Max: water volume keeps its unique id (key mop_intensity) and uses the translation key water_volume", ent_registry.async_get(ids["mop_intensity"]).unique_id == "TESTDEVICE0002_mop_intensity" and ent_registry.async_get(ids["mop_intensity"]).translation_key == "water_volume")
        for raw, key in ((0, "off"), (1, "low"), (2, "medium")):
            client.props["119100"] = raw
            await coordinator.async_refresh()
            check(f"RV1 Pro Max: mop_speed {raw} -> water volume {key}", state("mop_intensity").state == key, state("mop_intensity").state)
        await call("select", "select_option", "mop_intensity", option="medium")
        check("RV1 Pro Max: water volume medium is a property write {119100: 2}", client.calls == [("set", "TESTDEVICE0002", {"119100": 2})], str(client.calls))
        check("RV1 Pro Max: the commanded value shows at once", state("mop_intensity").state == "medium", state("mop_intensity").state)
        await call("select", "select_option", "mop_intensity", option="off")
        check("RV1 Pro Max: water volume off writes {119100: 0}", client.calls == [("set", "TESTDEVICE0002", {"119100": 0})], str(client.calls))
        for bad in ("turbo", "high", "gentle"):
            try:
                await call("select", "select_option", "mop_intensity", option=bad)
            except Exception as err:  # noqa: BLE001
                check(f"RV1 Pro Max: water volume option '{bad}' is rejected and nothing is written (the app's High has no value in the model)", client.calls == [], f"{type(err).__name__} {client.calls}")
            else:
                check(f"RV1 Pro Max: water volume option '{bad}' is rejected and nothing is written", False)
        client.props["121100"] = {"121101": True, "121102": 0, "121103": 0, "121104": 0}
        await coordinator.async_refresh()
        check("RV1 Pro Max: dock connected is on (connectivity)", state("dock_connected").state == "on" and state("dock_connected").attributes["device_class"] == "connectivity" and state("dock_connected").name == "RV1-PRO-MAX-TEST Dock connected", f"{state('dock_connected').state} {dict(state('dock_connected').attributes)}")
        check("RV1 Pro Max: tanks and bag are normal", (state("clean_water_tank").state, state("dirty_water_tank").state, state("dust_bag").state) == ("normal", "normal", "normal"), str([state(k).state for k in ("clean_water_tank", "dirty_water_tank", "dust_bag")]))
        client.props["121100"] = {"121101": False, "121102": 2, "121103": 2, "121104": 1}
        await coordinator.async_refresh()
        check("RV1 Pro Max: dock disconnected, water low, dirty tank full, no dust bag", (state("dock_connected").state, state("clean_water_tank").state, state("dirty_water_tank").state, state("dust_bag").state) == ("off", "water_low", "full", "not_installed"), str([state(k).state for k in ("dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag")]))
        check("RV1 Pro Max: station enum sensor options", state("clean_water_tank").attributes["options"] == ["normal", "not_installed", "water_low"] and state("dirty_water_tank").attributes["options"] == ["normal", "not_installed", "full"] and state("dust_bag").attributes["options"] == ["normal", "not_installed", "full"], str(dict(state("dust_bag").attributes)))
        client.props["121100"] = {"121101": True, "121102": 9, "121103": 0, "121104": 0}
        await coordinator.async_refresh()
        check("RV1 Pro Max: a value the model does not declare shows as unknown with the raw value (existing enum sensor behaviour)", state("clean_water_tank").state == "unknown" and state("clean_water_tank").attributes["raw_value"] == 9, str(dict(state("clean_water_tank").attributes)))
        del client.props["121100"]
        await coordinator.async_refresh()
        check("RV1 Pro Max: station_status not reported -> unknown", state("dock_connected").state == "unknown" and state("dust_bag").state == "unknown", f"{state('dock_connected').state} {state('dust_bag').state}")

        schedules = state("schedules")
        check("RV1 Pro Max: Schedules sensor counts the one enabled schedule", schedules.state == "1", str(schedules))
        check(
            "RV1 Pro Max: schedule attributes: local time 23:00, the days as names (7 = Sunday), whole house, start null, raw start kept",
            schedules.attributes["schedules"] == [{"enabled": True, "active": None, "start": None, "time": "23:00", "days": ["sun", "mon", "tue", "wed", "thu", "fri", "sat"], "repeat": [7, 1, 2, 3, 4, 5, 6], "rooms": "whole_house", "start_raw": 82800, "once": False}],
            str(schedules.attributes["schedules"]),
        )
        check("RV1 Pro Max: a whole-house schedule gives no rooms", coordinator.room_names() == {}, str(coordinator.room_names()))
        room_item = {"201662": 8, "201661": -1, "201668": "[]", "201667": 0, "201669": 3600, "201664": 0, "201663": 1, "201665": 1, "201666": [{"id": 3, "name": "Kitchen"}, {"id": 5}]}
        client.clean_list_output = {"201621": [room_item]}
        coordinator._schedules_fetched_at = None
        check("RV1 Pro Max: schedules are refreshed", await coordinator.async_refresh_schedules() is True)
        check("RV1 Pro Max: room names from the room schedule (exact name, else default)", coordinator.room_names() == {3: "Kitchen", 5: "Room 5"}, str(coordinator.room_names()))
        check(
            "RV1 Pro Max: room schedule attributes (once, 01:00, rooms by name)",
            state("schedules").attributes["schedules"] == [{"enabled": True, "active": None, "start": None, "time": "01:00", "days": [], "repeat": [], "rooms": ["Kitchen", "Room 5"], "start_raw": 3600, "once": True}],
            str(state("schedules").attributes["schedules"]),
        )
        if ha_clean_area() is not None:
            vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
            segments = await vac_entity.async_get_segments()
            check("RV1 Pro Max: segments come from the room schedule", [(seg.id, seg.name) for seg in segments] == [("3", "Kitchen"), ("5", "Room 5")], str(segments))
        unknown_item = {**room_item, "201666": None}
        client.clean_list_output = {"201621": [unknown_item]}
        coordinator._schedules_fetched_at = None
        check("RV1 Pro Max: schedules are refreshed (rooms unknown)", await coordinator.async_refresh_schedules() is True)
        check(
            "RV1 Pro Max: a rooms schedule without usable area_array shows 'unknown', not 'whole_house'",
            [a["rooms"] for a in state("schedules").attributes["schedules"]] == ["unknown"],
            str(state("schedules").attributes["schedules"]),
        )

        # --- 0.6.1: rooms from the cleaning order (sequence), with negative ids ----------------
        # verified on an RV1 Pro Max: schedule rooms [-128, -127], sequence [-128, -126, -127, -124]
        client.clean_list_output = {"201621": [{**room_item, "201666": [{"id": -128}, {"id": -127}]}]}
        client.props["120600"] = [-128, -126, -127, -124]
        coordinator._schedules_fetched_at = None
        polls_before = client.poll_count
        check("RV1 Pro Max: schedules and cleaning order are read in one refresh (one service call, one property call)", await coordinator.async_refresh_schedules() is True and client.poll_count == polls_before + 1)
        check("RV1 Pro Max: the cleaning order is kept", coordinator.sequence_rooms == [-128, -126, -127, -124], str(coordinator.sequence_rooms))
        check("RV1 Pro Max: rooms are the union of the schedule and the cleaning order; rooms only in the order have default names", coordinator.room_names() == {-128: "Room -128", -127: "Room -127", -126: "Room -126", -124: "Room -124"}, str(coordinator.room_names()))
        await hass.async_block_till_done()
        reads = client.poll_count
        check("RV1 Pro Max: a second read within a minute is served from the cache", await coordinator.async_refresh_schedules() is True and client.poll_count == reads)
        registry = er.async_get(hass)
        reset_entries = [registry.async_get(registry.async_get_entity_id("button", "imou_vacuum", f"TESTDEVICE0002_reset_{part}")) for part in ("side_brush", "main_brush", "filter", "sensors")]
        check("RV1 Pro Max: the four reset buttons are in the registry, disabled by the integration, configuration category", all(e.disabled_by is er.RegistryEntryDisabler.INTEGRATION and e.entity_category == "config" for e in reset_entries), str([(e.disabled_by, e.entity_category) for e in reset_entries]))
        room_buttons = {rid: registry.async_get_entity_id("button", "imou_vacuum", f"TESTDEVICE0002_clean_room_{rid}") for rid in (-128, -127, -126, -124)}
        check("RV1 Pro Max: a Clean button exists for each room, unique id <device id>_clean_room_<negative id>", all(room_buttons.values()), str(room_buttons))
        check("RV1 Pro Max: the button of room -127 reads 'Clean room -127'", hass.states.get(room_buttons[-127]).name == "RV1-PRO-MAX-TEST Clean room -127", hass.states.get(room_buttons[-127]).name)
        client.calls.clear()
        await hass.services.async_call("button", "press", {"entity_id": room_buttons[-127]}, blocking=True)
        check("RV1 Pro Max: pressing the button of room -127 cleans only that room (partition_area [-127] as a list)", client.calls == [("control", "TESTDEVICE0002", "200900", {"200901": 1, "200902": 1, "200903": [-127]})], str(client.calls))
        if ha_clean_area() is not None:
            vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(ids["vacuum"])
            segments = await vac_entity.async_get_segments()
            wanted = {("-128", "Room -128"), ("-127", "Room -127"), ("-126", "Room -126"), ("-124", "Room -124")}
            check("RV1 Pro Max: segments include the rooms of the cleaning order, ids as text of the negative number, ascending", wanted <= {(seg.id, seg.name) for seg in segments} and [s.id for s in segments if s.id.startswith("-")] == ["-128", "-127", "-126", "-124"], str([(seg.id, seg.name) for seg in segments]))
            client.calls.clear()
            await vac_entity.async_clean_segments(["-128", "-124"])
            check("RV1 Pro Max: clean_segments with negative segment ids", client.calls == [("control", "TESTDEVICE0002", "200900", {"200901": 1, "200902": 1, "200903": [-128, -124]})], str(client.calls))
        client.props["120600"] = []
        client.clean_list_output = {"201621": []}
        coordinator._schedules_fetched_at = None
        check("RV1 Pro Max: a robot without rooms reports [] and has no rooms", await coordinator.async_refresh_schedules() is True and coordinator.sequence_rooms == [] and coordinator.room_names() == {}, str(coordinator.room_names()))
        await hass.async_block_till_done()
        check("RV1 Pro Max: the buttons of rooms that are gone are unavailable", hass.states.get(room_buttons[-127]).state == "unavailable")

        # --- review fixes: zero padding, quota, cache -------------------------------------------
        import time  # noqa: PLC0415

        api_mod = mod("api")
        client.props["120600"] = [-128, 0, -127, 0, 0]
        coordinator._schedules_fetched_at = None
        await coordinator.async_refresh_schedules()
        check("RV1 Pro Max: zeros of a padded sequence are ignored", coordinator.sequence_rooms == [-128, -127], str(coordinator.sequence_rooms))
        del client.props["120600"]
        coordinator._schedules_fetched_at = None
        coordinator.sequence_rooms = None
        check("RV1 Pro Max: a response without sequence is 'read, empty', not a failure", await coordinator.async_refresh_schedules() is True and coordinator.sequence_rooms == [] and not coordinator._failed_parts and 3500 < coordinator._schedules_due - time.monotonic() <= 3600, f"{coordinator.sequence_rooms} {coordinator._failed_parts}")
        # a failing sequence read: only it is retried, clean_list is not called again
        client.props["120600"] = [-128]
        client.fail_sequence_with = api_mod.ImouConnectionError("TIMEOUT", "getIotDeviceProperties")
        coordinator._schedules_due = 0.0
        client.probe_service_calls.clear()
        client.sequence_reads = 0
        await coordinator.async_refresh()
        check("RV1 Pro Max: sequence fails: clean_list was read once (the room properties twice: combined, then sequence alone), retry in about 15 minutes", len(client.probe_service_calls) == 1 and client.sequence_reads == 2 and coordinator._failed_parts == {"room_properties"} and 800 < coordinator._schedules_due - time.monotonic() <= 900, f"{client.probe_service_calls} {client.sequence_reads} {coordinator._failed_parts}")
        client.fail_sequence_with = None
        coordinator._schedules_due = 0.0
        client.probe_service_calls.clear()
        await coordinator.async_refresh()
        check("RV1 Pro Max: the retry reads only sequence (no clean_list call), then waits an hour", client.probe_service_calls == [] and client.sequence_reads == 3 and coordinator.sequence_rooms == [-128] and not coordinator._failed_parts and 3500 < coordinator._schedules_due - time.monotonic() <= 3600, f"{client.probe_service_calls} {client.sequence_reads} {coordinator.sequence_rooms}")
        # the reverse: clean_list fails, sequence is not read again by the retry
        client.fail_schedules_with = api_mod.ImouConnectionError("TIMEOUT", "iotDeviceControl")
        coordinator._schedules_due = 0.0
        client.probe_service_calls.clear()
        client.sequence_reads = 0
        await coordinator.async_refresh()
        check("RV1 Pro Max: clean_list fails: sequence read once, retry soon", len(client.probe_service_calls) == 1 and client.sequence_reads == 1 and coordinator._failed_parts == {"clean_list"}, f"{client.probe_service_calls} {client.sequence_reads} {coordinator._failed_parts}")
        coordinator._schedules_due = 0.0
        await coordinator.async_refresh()
        check("RV1 Pro Max: the retry reads only clean_list (sequence is not read again)", len(client.probe_service_calls) == 2 and client.sequence_reads == 1 and coordinator._failed_parts == {"clean_list"}, f"{client.probe_service_calls} {client.sequence_reads}")
        # a partial read (sequence ok, schedules failed, none known) is not cached as fresh
        coordinator.schedules = None
        coordinator._schedules_fetched_at = None
        client.probe_service_calls.clear()
        first = await coordinator.async_refresh_schedules()
        second = await coordinator.async_refresh_schedules()
        check("RV1 Pro Max: a partial read with schedules still unknown is not cached for 60 s (clean_list is tried again)", first is True and second is True and len(client.probe_service_calls) == 2 and coordinator.schedules is None, f"{first} {second} {client.probe_service_calls}")
        client.fail_schedules_with = None
        client.fail_with = None
    finally:
        await hass.async_stop()


def test_rv1_setup() -> None:
    config_dir = tempfile.mkdtemp(prefix="imou-rv1-")
    try:
        asyncio.run(rv1_setup_checks(config_dir))
    finally:
        worker = threading.Thread(target=shutil.rmtree, args=(config_dir,), kwargs={"ignore_errors": True})
        worker.start()
        worker.join()


# ---------------------------------------------------------------------------
# 6b. RV1L Pro (productId AC8ZW8Y9) and the optional entities of a robot that never reports them
# ---------------------------------------------------------------------------
class RV1LProClient(RV1Client):
    """Fake client for the RV1L Pro: its own model (swp_backStation, no back_station) and values."""

    initial_props: dict[str, Any] | None = None
    drop_station = False

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        self.props = copy.deepcopy(RV1LPRO_VALUES_JSON["properties"])
        self.props.update({"126200": [], "122300": [{"122302": "Map", "122301": 1}], "120600": [], "121600": []})
        self.clean_list_output = {"201621": []}  # no schedules (the user's real response)
        if RV1LProClient.drop_station:  # like an RV1 Pro Max: the dock is never reported
            del self.props["121100"]

    async def async_list_devices(self) -> list[Any]:
        api = mod("api")
        return [api.ImouDevice(device_id="TESTDEVICE0004", product_id="PRODUCT_ID_RV1LPRO", name="RV1L-PRO-TEST", model="RV1L Pro", status="online", version="1.0.0", brand="lechange")]

    async def async_get_product_model(self, product_id: str) -> dict[str, Any]:
        return copy.deepcopy(RV1LPRO_MODEL_JSON)


async def with_fresh_hass(config_dir: str, client_cls: Any, body: Any, before: Any = None) -> None:
    """Start a bare Home Assistant with ``client_cls``, add one entry and run ``body``."""
    from homeassistant import bootstrap, config_entries, loader  # noqa: PLC0415
    from homeassistant.core import HomeAssistant  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    package = importlib.import_module(PKG)
    package.ImouClient = client_cls
    package.async_get_clientsession = lambda hass: None
    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config.language = "en"
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    await bootstrap.async_load_base_functionality(hass)
    await hass.async_start()
    try:
        if before is not None:
            before(er.async_get(hass))
        entry = config_entries.ConfigEntry(
            data={"app_id": "fresh-app", "app_secret": "s", "region": "eu"}, discovery_keys={},
            domain="imou_vacuum", entry_id="fresh-app", minor_version=1, options={}, source="user",
            subentries_data=None, title="t", unique_id="fresh-app", version=1,
        )  # fmt: skip
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        await body(hass, entry)
    finally:
        await hass.async_stop()


def run_fresh(client_cls: Any, body: Any, before: Any = None) -> None:
    config_dir = tempfile.mkdtemp(prefix="imou-fresh-")
    try:
        asyncio.run(with_fresh_hass(config_dir, client_cls, body, before))
    finally:
        worker = threading.Thread(target=shutil.rmtree, args=(config_dir,), kwargs={"ignore_errors": True})
        worker.start()
        worker.join()


async def rv1lpro_checks(hass: Any, entry: Any) -> None:
    from homeassistant.components.vacuum import VacuumEntityFeature  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er, issue_registry as ir  # noqa: PLC0415

    check("RV1L Pro: entry is loaded", entry.state.value == "loaded", str(entry.state))
    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    regs = {e.unique_id.split("_", 1)[1]: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    def state(key: str) -> Any:
        return hass.states.get(regs[key].entity_id)

    # 1. return to dock through swp_backStation
    features = int(state("vacuum").attributes["supported_features"])
    check("RV1L Pro: RETURN_HOME is supported through swp_backStation", bool(features & int(VacuumEntityFeature.RETURN_HOME)), str(features))
    check("RV1L Pro: no model support repair issue (return to dock is no longer missing)", ir.async_get(hass).async_get_issue("imou_vacuum", "model_support_TESTDEVICE0004") is None and not [i for d, i in ir.async_get(hass).issues if d == "imou_vacuum"], str(list(ir.async_get(hass).issues)))
    client.calls.clear()
    await hass.services.async_call("vacuum", "return_to_base", {"entity_id": regs["vacuum"].entity_id}, blocking=True)
    check("RV1L Pro: return_to_base writes swp_backStation {151300: 1} (start return)", client.calls == [("set", "TESTDEVICE0004", {"151300": 1})], str(client.calls))
    # 2. optional entities, decided by the first (online) poll
    check("RV1L Pro: Carpet boost is reported, so enabled and on", regs["carpet_boost"].disabled_by is None and state("carpet_boost").state == "on")
    check("RV1L Pro: the dock sensors exist but are disabled by default (it reports all zeros, no model is verified)", all(regs[k].disabled_by is er.RegistryEntryDisabler.INTEGRATION for k in ("dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag")), str({k: regs[k].disabled_by for k in ("dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag")}))
    # 4. suction and 5. water volume
    check("RV1L Pro: suction reports 4 (max); the offered levels are quiet/standard/strong/max", state("vacuum").attributes["fan_speed"] == "max" and list(state("vacuum").attributes["fan_speed_list"]) == ["quiet", "standard", "strong", "max"] and state("suction_level").attributes["options"] == ["quiet", "standard", "strong", "max"])
    check("RV1L Pro: water volume 2 is medium", state("mop_intensity").state == "medium" and state("mop_intensity").attributes["options"] == ["off", "low", "medium"], str(state("mop_intensity")))
    # 8. charging: power_status 1; and the fallback to the battery sign
    check("RV1L Pro: charging is on (power_status 1, battery -67 shows 67 %)", state("charging").state == "on" and state("battery").state == "67", f"{state('charging').state} {state('battery').state}")
    # 7. mop fitted (0 = off = 'Not fitted')
    check("RV1L Pro: mop fitted is off (shown as 'Not fitted')", state("mop_fitted").state == "off")
    # 3. the rooms of a robot without rooms: none
    check("RV1L Pro: no rooms (sequence [] and no schedules)", coordinator.sequence_rooms == [] and coordinator.room_names() == {}, str(coordinator.sequence_rooms))
    check("RV1L Pro: Schedules sensor counts 0 schedules", state("schedules").state == "0")
    # a write answered with DV1026 makes the switch unavailable and gives a translated error
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    client.fail_set_with = mod("api").ImouApiError("OP1001", "param error")
    try:
        await hass.services.async_call("switch", "turn_on", {"entity_id": regs["carpet_boost"].entity_id}, blocking=True)
    except HomeAssistantError:
        pass
    client.fail_set_with = None
    await hass.async_block_till_done()
    check("RV1L Pro: another error code does not make a switch unavailable", state("carpet_boost").state != "unavailable", state("carpet_boost").state)
    client.fail_set_with = mod("api").ImouApiError("DV1026", "device does not support this function")
    try:
        await hass.services.async_call("switch", "turn_on", {"entity_id": regs["carpet_boost"].entity_id}, blocking=True)
    except HomeAssistantError as err:
        check("RV1L Pro: DV1026 gives the translated error 'setting_not_supported'", getattr(err, "translation_key", None) == "setting_not_supported" and "does not support that setting" in str(err), f"{type(err).__name__} {err}")
    else:
        check("RV1L Pro: DV1026 gives the translated error 'setting_not_supported'", False)
    client.fail_set_with = None
    await hass.async_block_till_done()
    check("RV1L Pro: ... and the switch is unavailable", state("carpet_boost").state == "unavailable", state("carpet_boost").state)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("RV1L Pro: ... also after a poll, until the integration reloads", state("carpet_boost").state == "unavailable")


async def rv1lpro_late_checks(hass: Any, entry: Any) -> None:
    """The robot is offline at setup: the optional entities appear with the first online poll."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)

    def keys() -> dict[str, Any]:
        return {e.unique_id.split("_", 1)[1]: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    optional = {"carpet_boost", "dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag"}
    check("offline at setup: the optional switch and dock sensors do not exist yet", not optional & set(keys()) and "battery" in keys(), str(sorted(keys())))
    client.status = "online"
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    regs = keys()
    check("the first online poll creates them", optional <= set(regs), str(sorted(optional - set(regs))))
    check("... decided on that poll: the carpet boost it reports is enabled, the dock sensors are disabled by default", regs["carpet_boost"].disabled_by is None and all(regs[k].disabled_by is er.RegistryEntryDisabler.INTEGRATION for k in optional - {"carpet_boost"}), str({k: regs[k].disabled_by for k in optional}))
    count = len(regs)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("a later poll does not create them again", len(keys()) == count)


async def rv1_silent_checks(hass: Any, entry: Any) -> None:
    """An RV1 Pro Max never reports station_status; enabled entities of old installs stay available."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    regs = {e.unique_id.split("_", 1)[1]: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    dock = ("dock_connected", "clean_water_tank", "dirty_water_tank", "dust_bag")
    check("the robot never reports station_status: the four dock entities are disabled by default", all(regs[k].disabled_by is er.RegistryEntryDisabler.INTEGRATION for k in dock if k not in ("dust_bag", "clean_water_tank")), str({k: regs[k].disabled_by for k in dock}))
    # the entities that existed (enabled) before this version are not touched by the new default and stay available
    for key in ("dust_bag", "clean_water_tank"):
        check(f"an enabled {key} entity of the robot that never reports it is unknown, not unavailable", hass.states.get(regs[key].entity_id).state == "unknown", str(hass.states.get(regs[key].entity_id)))
    check("... the Carpet boost it does report is available", hass.states.get(regs["carpet_boost"].entity_id).state == "on")
    client.props["121100"] = {"121101": 1, "121102": 0, "121103": 0, "121104": 2}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    check("when the robot starts to report them, the entities show the values", (hass.states.get(regs["dust_bag"].entity_id).state, hass.states.get(regs["clean_water_tank"].entity_id).state) == ("full", "normal"), str([hass.states.get(regs[k].entity_id).state for k in ("dust_bag", "clean_water_tank")]))


# --- 0.7.0: rooms without a schedule, from the room properties of an RV1 model --------------------
ROOM_MAP = 1001
OTHER_MAP = 1002


class RV1LProRoomsClient(RV1LProClient):
    """An RV1L Pro without a schedule and with an empty ``sequence``, but with the room
    configuration (swp_allareaConfig): 7 rooms on its single map and 1 room of another map."""

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        rooms = [{"147401": ROOM_MAP, "147402": room, "147403": 0} for room in (-128, -127, -126, -125, -124, -123, -122)]
        rooms.append({"147401": OTHER_MAP, "147402": -50, "147403": 1})  # another map: filtered out
        self.props.update({"147400": rooms, "122300": [{"122302": "Map A", "122301": ROOM_MAP}]})


async def rv1lpro_rooms_checks(hass: Any, entry: Any) -> None:
    from homeassistant.components import vacuum as vacuum_component  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    regs = {e.unique_id.split("_", 1)[1]: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    wanted = (-128, -127, -126, -125, -124, -123, -122)
    check("RV1L Pro rooms: no schedule and an empty sequence", coordinator.schedules is not None and coordinator.schedules.schedules == [] and coordinator.sequence_rooms == [], str(coordinator.sequence_rooms))
    check("RV1L Pro rooms: the room properties were read in one call with the refs of the model (sequence 120600 ... map_name 122300)", len(client.room_read_refs) == 1 and sorted(client.room_read_refs[0]) == ["120600", "121600", "122300", "145800", "146000", "147300", "147400", "160700"], str(client.room_read_refs))
    check("RV1L Pro rooms: the current map is the one map of map_name", coordinator.current_map() == ROOM_MAP, str(coordinator.current_map()))
    check("RV1L Pro rooms: 7 rooms with default names; the room of the other map is filtered out", coordinator.room_names() == {room: f"Room {room}" for room in wanted}, str(coordinator.room_names()))
    clean_buttons = {key for key in regs if key.startswith("clean_room_")}
    check("RV1L Pro rooms: 7 Clean buttons", clean_buttons == {f"clean_room_{room}" for room in wanted}, str(sorted(clean_buttons)))
    check("RV1L Pro rooms: every Clean button is available", all(hass.states.get(regs[key].entity_id).state != "unavailable" for key in clean_buttons))
    if ha_clean_area() is not None:
        vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(regs["vacuum"].entity_id)
        segments = await vac_entity.async_get_segments()
        check("RV1L Pro rooms: 7 segments", sorted(int(seg.id) for seg in segments) == sorted(wanted), str([(seg.id, seg.name) for seg in segments]))
    client.calls.clear()
    await hass.services.async_call("button", "press", {"entity_id": regs["clean_room_-127"].entity_id}, blocking=True)
    check("RV1L Pro rooms: pressing a Clean button cleans only that room (partition_area as a list)", client.calls == [("control", "TESTDEVICE0004", "200900", {"200901": 1, "200902": 1, "200903": [-127]})], str(client.calls))
    # several maps and no known current map: the rooms with a map id are dropped (room ids repeat per map)
    client.props["122300"] = [{"122302": "A", "122301": ROOM_MAP}, {"122302": "B", "122301": OTHER_MAP}]
    client.props["147400"].append({"147402": -9, "147403": 0})  # an item without a map id is kept
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    check("RV1L Pro rooms: two maps and no schedule: the rooms with a map id are dropped, the one without stays", await coordinator.async_refresh_schedules() is True and coordinator.current_map() is None and coordinator.room_names() == {-9: "Room -9"}, f"{coordinator.current_map()} {coordinator.room_names()}")
    # the current map is known (the one listed map) but no room carries its id: the filter is ignored, then the same rule
    client.props["122300"] = [{"122302": "C", "122301": 5555}]
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    check("RV1L Pro rooms: a known map that no room carries: same result", await coordinator.async_refresh_schedules() is True and coordinator.current_map() == 5555 and coordinator.room_names() == {-9: "Room -9"}, f"{coordinator.current_map()} {coordinator.room_names()}")
    client.props["147400"].pop()
    client.props["122300"] = [{"122302": "A", "122301": ROOM_MAP}]
    # a model with map_name but without sequence and room sources makes no call at all
    real_model = coordinator.model
    coordinator.model = mod("model").ThingsModel.from_api({"properties": [p for p in copy.deepcopy(RV1LPRO_MODEL_JSON["properties"]) if p["identifier"] == "map_name"], "services": []})
    reads_before, polls_before = len(client.room_read_refs), client.poll_count
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    check("RV1L Pro rooms: a model with only map_name makes no property call", coordinator._room_property_identifiers() == [] and client.poll_count == polls_before and len(client.room_read_refs) == reads_before, f"{coordinator._room_property_identifiers()} {client.poll_count - polls_before}")  # noqa: SLF001
    coordinator.model = real_model
    # a response without these properties behaves like a robot without them
    for ref in ("147400", "122300"):
        del client.props[ref]
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    check("RV1L Pro rooms: a response without the properties is 'read, empty': no rooms, no failure", await coordinator.async_refresh_schedules() is True and coordinator.room_names() == {} and coordinator.source_entries == [] and not coordinator._failed_parts, f"{coordinator.room_names()} {coordinator._failed_parts}")  # noqa: SLF001


async def rv1lpro_fallback_checks(hass: Any, entry: Any) -> None:
    """The room properties share one call with ``sequence``; when that call fails, ``sequence`` alone is read."""
    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    api = mod("api")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture(level=logging.INFO)
    logging.getLogger(PKG).addHandler(capture)
    try:
        check("fallback: the first combined read worked", len(client.room_read_refs[0]) == 8 and coordinator.sequence_rooms == [] and len(coordinator.room_names()) == 7, str(client.room_read_refs))
        client.props["120600"] = [-128, -5]
        client.fail_extras_with = api.ImouApiError("OP1001", "param error")

        async def refresh() -> bool:
            client.room_read_refs.clear()
            coordinator._schedules_fetched_at = None  # noqa: SLF001
            return await coordinator.async_refresh_schedules()

        for attempt in (1, 2, 3):
            ok = await refresh()
            check(f"fallback: failed combined read {attempt}: retried with only sequence, which keeps the rooms", ok is True and [len(r) for r in client.room_read_refs] == [8, 1] and client.room_read_refs[1] == ["120600"] and coordinator.sequence_rooms == [-128, -5] and not coordinator._failed_parts, f"{ok} {client.room_read_refs} {coordinator.sequence_rooms}")  # noqa: SLF001
            check(f"fallback: ... the rooms of the earlier combined read stay (attempt {attempt})", {-128, -5} <= set(coordinator.room_names()) and len(coordinator.room_names()) == 8, str(coordinator.room_names()))
        infos = [r.getMessage() for r in records if r.levelno == logging.INFO and "room properties" in r.getMessage()]
        check("fallback: after 3 failures one info line and the extra refs are no longer requested", coordinator._extras_disabled and len(infos) == 1, f"{coordinator._extras_disabled} {infos}")  # noqa: SLF001
        ok = await refresh()
        check("fallback: the next read asks for sequence only (one call)", ok is True and client.room_read_refs == [["120600"]], str(client.room_read_refs))
        ok = await refresh()
        check("fallback: ... and says nothing more", len([r for r in records if r.levelno == logging.INFO and "room properties" in r.getMessage()]) == 1)
        # a failure of sequence alone too is a plain failed read, not blamed on the extra refs
        client.fail_extras_with = None
        coordinator._extras_disabled, coordinator._extra_failures = False, 0  # noqa: SLF001  (a restart)
        client.fail_sequence_with = api.ImouApiError("OP1001", "param error")
        for _ in range(4):
            await refresh()
        check("fallback: when even sequence fails, nothing is disabled and the part is retried", not coordinator._extras_disabled and "room_properties" in coordinator._failed_parts, f"{coordinator._extras_disabled} {coordinator._failed_parts}")  # noqa: SLF001
        client.fail_sequence_with = None
        check("fallback: a restart (fresh state) asks for everything again and a good read resets the count", await refresh() is True and len(client.room_read_refs[0]) == 8 and coordinator._extra_failures == 0, str(client.room_read_refs))  # noqa: SLF001
    finally:
        logging.getLogger(PKG).removeHandler(capture)


def test_rv1lpro_room_call_fallback() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1LProRoomsClient, rv1lpro_fallback_checks)


def test_rv1lpro_rooms_without_a_schedule() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1LProRoomsClient, rv1lpro_rooms_checks)


def test_rv1lpro_setup() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1LProClient, rv1lpro_checks)


# --- 0.7.0b2: rooms per active map -----------------------------------------------------------------
MAP_A, MAP_B = 1001, 1002
ORDER_A = [-127, -128, -126, -125]
ORDER_B = [-127, -126, -128]


class RV1TwoMapClient(RV1LProClient):
    """An RV1 robot with two maps. Map A is active (its cleaning order is the top-level ``sequence``).
    Room ids repeat on both maps. The schedules hold a room of map A and a schedule of map B."""

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        area_config = [{"147401": MAP_A, "147402": room, "147403": 0} for room in ORDER_A]
        area_config += [{"147401": MAP_B, "147402": room, "147403": 0} for room in (*ORDER_B, -129)]
        self.props.update(
            {
                "122300": [{"122302": "Map A", "122301": MAP_A}, {"122302": "Map B", "122301": MAP_B}],
                "120600": list(ORDER_A),
                "146000": [{"146001": MAP_A, "146002": json.dumps(ORDER_A)}, {"146001": MAP_B, "146002": json.dumps(ORDER_B)}],
                "147400": area_config,
            }
        )

        def schedule(map_id: int, timer: int, rooms: list[Any]) -> dict[str, Any]:
            return {"201661": map_id, "201662": timer, "201663": 1, "201665": 1, "201666": rooms, "201667": 0, "201668": [1], "201669": 3600}

        self.clean_list_output = {
            "201621": [
                schedule(MAP_A, 1, [-127, -128]),
                schedule(MAP_B, 2, [{"id": -130, "name": "Attic"}, {"id": -127, "name": "Study"}]),  # rooms of map B
            ]
        }


async def rv1_two_map_checks(hass: Any, entry: Any) -> None:
    from homeassistant.components import button as button_component, vacuum as vacuum_component  # noqa: PLC0415
    from homeassistant.exceptions import ServiceValidationError  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    device = "TESTDEVICE0004"

    def clean_ids() -> set[str]:
        return {e.unique_id.split("_", 1)[1] for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.unique_id.split("_", 1)[1].startswith("clean_room_")}

    def entity_id(key: str) -> str:
        return registry.async_get_entity_id("button", "imou_vacuum", f"{device}_{key}")

    async def refresh() -> None:
        coordinator._schedules_fetched_at = None  # noqa: SLF001
        await coordinator.async_refresh_schedules()
        await hass.async_block_till_done()

    # 1. the active map: no sweeper_sequence, so the map whose cleaning order equals the top-level sequence
    check("two maps: the robot has several maps", coordinator.is_multi_map() and coordinator.listed_map_ids == [MAP_A, MAP_B], str(coordinator.listed_map_ids))
    check("two maps: the active map is the one whose cleaning order equals sequence (b)", coordinator.active_map() == MAP_A and coordinator.room_map() == MAP_A, str(coordinator.active_map()))
    # 2. only the rooms of the active map: not -129 of map B, not the schedule of map B
    check("two maps: map A gives the rooms of map A only (a room of map B and its schedule are not added)", coordinator.room_names() == {-128: "Room -128", -127: "Room -127", -126: "Room -126", -125: "Room -125"}, str(coordinator.room_names()))
    check("two maps: the names of a schedule of map B do not appear (Study, Attic)", "Study" not in coordinator.room_names().values() and -130 not in coordinator.room_names())
    check("two maps: segment ids carry the map", set(coordinator.room_segments()) == {f"{MAP_A}:{room}" for room in ORDER_A}, str(coordinator.room_segments()))
    # 3. buttons: unique ids with map and room, none for the rooms of map B
    check("two maps: the Clean buttons are keyed by map and room", clean_ids() == {f"clean_room_{MAP_A}_{room}" for room in ORDER_A}, str(sorted(clean_ids())))
    button = entity_id(f"clean_room_{MAP_A}_-127")
    check("two maps: a button is named after the room and its map and is available", hass.states.get(button).state != "unavailable" and hass.states.get(button).attributes["friendly_name"].endswith("Clean room -127 (Map A)"), str(hass.states.get(button)))
    # 4. cleaning
    client.calls.clear()
    reads = client.sequence_reads
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    check("two maps: pressing a button forces one fresh read of the room properties first", client.sequence_reads == reads + 1, f"{reads} {client.sequence_reads}")
    check("two maps: pressing a button of the active map cleans that room", client.calls == [("control", device, "200900", {"200901": 1, "200902": 1, "200903": [-127]})], str(client.calls))
    vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(registry.async_get_entity_id("vacuum", "imou_vacuum", f"{device}_vacuum"))
    client.calls.clear()
    await vac_entity.async_clean_segments([f"{MAP_A}:-126"])
    check("two maps: a segment of the active map is cleaned", client.calls == [("control", device, "200900", {"200901": 1, "200902": 1, "200903": [-126]})], str(client.calls))
    client.calls.clear()
    try:
        await vac_entity.async_clean_segments([f"{MAP_A}:-126", f"{MAP_B}:-127"])
    except ServiceValidationError as err:
        check("two maps: a room of the other map raises the translated error", err.translation_key == "room_on_other_map" and "switch the map in the Imou app first" in str(err), f"{err.translation_key} {err}")
    else:
        check("two maps: a room of the other map raises the translated error", False)
    check("two maps: ... and nothing was sent to the robot", client.calls == [], str(client.calls))
    # a plain id (no map) on a multi-map robot whose active map is known is refused
    for plain in ("-127", "5"):
        try:
            await vac_entity.async_clean_segments([plain])
        except ServiceValidationError as err:
            check(f"two maps: a plain segment id {plain!r} is refused with the re-link message", err.translation_key == "room_without_map" and "Link rooms to areas" in str(err), f"{err.translation_key} {err}")
        else:
            check(f"two maps: a plain segment id {plain!r} is refused with the re-link message", False)
    check("two maps: ... and nothing was sent to the robot (plain ids)", client.calls == [], str(client.calls))
    # the check bypasses the cache: the map switched in the app is noticed at once
    client.props["160700"] = {"160701": MAP_B, "160702": json.dumps(ORDER_B)}
    client.props["120600"] = list(ORDER_B)
    client.calls.clear()
    reads = client.sequence_reads
    check("two maps: (cache is fresh) the coordinator still thinks map A is active", coordinator.active_map() == MAP_A)
    try:
        await vac_entity.async_clean_segments([f"{MAP_A}:-126"])
    except ServiceValidationError as err:
        check("two maps: a map switched since the last read is caught by the fresh read", err.translation_key == "room_on_other_map" and client.sequence_reads == reads + 1 and client.calls == [], f"{err.translation_key} {client.sequence_reads - reads} {client.calls}")
    else:
        check("two maps: a map switched since the last read is caught by the fresh read", False)
    check("two maps: ... and the coordinator now knows map B", coordinator.active_map() == MAP_B)
    client.props["160700"] = {"160701": MAP_A, "160702": json.dumps(ORDER_A)}
    client.props["120600"] = list(ORDER_A)
    await refresh()
    check("two maps: back on map A", coordinator.active_map() == MAP_A)
    # a failed fresh read or an unknown active map refuses the clean, with its own translated error
    client.fail_sequence_with = mod("api").ImouApiError("OP1001", "boom")
    client.calls.clear()
    try:
        await vac_entity.async_clean_segments([f"{MAP_A}:-126"])
    except ServiceValidationError as err:
        check("two maps: a failed fresh read refuses the clean (active_map_unconfirmed, translated)", err.translation_key == "active_map_unconfirmed" and "Could not confirm the robot's active map" in str(err) and client.calls == [], f"{err.translation_key} {err} {client.calls}")
    else:
        check("two maps: a failed fresh read refuses the clean (active_map_unconfirmed, translated)", False)
    try:
        await hass.data[button_component.DATA_COMPONENT].get_entity(button).async_press()
    except ServiceValidationError as err:
        check("two maps: ... a button press too", err.translation_key == "active_map_unconfirmed" and client.calls == [], f"{err.translation_key} {client.calls}")
    else:
        check("two maps: ... a button press too", False)
    client.fail_sequence_with = None
    # a fresh read with only ``sequence`` (the extra refs failed) leaves the active map unknown
    client.fail_extras_with = mod("api").ImouApiError("OP1001", "extras")
    try:
        await vac_entity.async_clean_segments([f"{MAP_A}:-126"])
    except ServiceValidationError as err:
        check("two maps: a sequence-only fallback read makes the active map unknown and refuses the clean", err.translation_key == "active_map_unconfirmed" and coordinator.sweeper_map_id is None and coordinator.sequence_entries == [] and coordinator.active_map() is None, f"{err.translation_key} {coordinator.active_map()}")
    else:
        check("two maps: a sequence-only fallback read makes the active map unknown and refuses the clean", False)
    check("two maps: ... and the buttons are not created or reused under another map meanwhile", not any(key.startswith("clean_room_-") for key in clean_ids()), str(sorted(clean_ids())))
    client.fail_extras_with = None
    coordinator._extra_failures = 0  # noqa: SLF001
    coordinator._extras_disabled = False  # noqa: SLF001
    await refresh()
    check("two maps: the next full read finds map A again", coordinator.active_map() == MAP_A, str(coordinator.active_map()))
    for bad in ("x", f"{MAP_A}:", "0:5"):
        try:
            await vac_entity.async_clean_segments([bad])
        except ServiceValidationError as err:
            check(f"two maps: an invalid segment id {bad!r} is refused", err.translation_key == "invalid_room", err.translation_key)
        else:
            check(f"two maps: an invalid segment id {bad!r} is refused", False)
    if ha_clean_area() is not None:
        segments = await vac_entity.async_get_segments()
        check("two maps: the segments are the rooms of map A with map-qualified ids", sorted(seg.id for seg in segments) == sorted(f"{MAP_A}:{room}" for room in ORDER_A), str([(seg.id, seg.name) for seg in segments]))
    # 5. names are kept per map: the same room id on both maps has two names
    options = {"room_names": {device: {f"{MAP_A}:-127": "Kitchen", f"{MAP_B}:-127": "Study", "-127": "Plain", f"{MAP_B}:-130": "Attic"}}}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators[device]  # the entry reloaded: a new coordinator, client and entities
    client = FakeClient.instance
    vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(registry.async_get_entity_id("vacuum", "imou_vacuum", f"{device}_vacuum"))
    check("two maps: the name of -127 on map A is Kitchen; the name on map B and the plain name do not leak", coordinator.room_names()[-127] == "Kitchen" and "Study" not in coordinator.room_names().values() and "Plain" not in coordinator.room_names().values(), str(coordinator.room_names()))
    check("two maps: a named room of map B is not remembered on map A", -130 not in coordinator.room_names())
    check("two maps: the buttons of map A after the reload: Kitchen is named after its map", hass.states.get(entity_id(f"clean_room_{MAP_A}_-127")).attributes["friendly_name"].endswith("Clean Kitchen (Map A)"))
    # the schedules sensor does not name the rooms of the schedule of map B after map A
    schedules_state = hass.states.get(registry.async_get_entity_id("sensor", "imou_vacuum", f"{device}_schedules"))
    rooms_attr = [item["rooms"] for item in schedules_state.attributes["schedules"]]
    check("two maps: the schedules sensor names the rooms of map A, and shows default names for map B", rooms_attr == [["Kitchen", "Room -128"], ["Room -130", "Room -127"]], str(rooms_attr))
    # the options flow shows the rooms of the active map, labelled with the map; saving keeps the names of the other map
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "room_areas"})
    check("two maps: the area form labels the rooms of the active map with the map name", [str(key) for key in form["data_schema"].schema] == ["Room -128 (Map A)", "Room -127 (Map A): Kitchen", "Room -126 (Map A)", "Room -125 (Map A)"] and form["description_placeholders"]["robot"] == "RV1L-PRO-TEST (Map A)", f"{[str(key) for key in form['data_schema'].schema]} {form['description_placeholders']}")
    hass.config_entries.options.async_abort(flow["flow_id"])
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "rooms"})
    suggested = next(iter(form["data_schema"].schema)).description["suggested_value"]
    check("two maps: the rename form lists the rooms of map A only", suggested == "-128 = Room -128\n-127 = Kitchen\n-126 = Room -126\n-125 = Room -125", repr(suggested))
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"room_names": "-127 = Kitchen\n-126 = Hall\n-125 = Room -125"})
    await hass.async_block_till_done()
    check("two maps: saving the names of map A keeps those of map B and the plain names", done["data"]["room_names"][device] == {"-127": "Plain", f"{MAP_B}:-127": "Study", f"{MAP_B}:-130": "Attic", f"{MAP_A}:-127": "Kitchen", f"{MAP_A}:-126": "Hall"}, str(done["data"]["room_names"]))
    coordinator = entry.runtime_data.coordinators[device]
    client = FakeClient.instance
    vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(registry.async_get_entity_id("vacuum", "imou_vacuum", f"{device}_vacuum"))
    # the options forms refuse a submit after the map changed while they were open
    async def switch_map(map_id: int, order: list[int]) -> None:
        client.props["160700"] = {"160701": map_id, "160702": json.dumps(order)}
        client.props["120600"] = list(order)
        await refresh()

    for step, user_input in (("room_areas", {}), ("rooms", {"room_names": "-127 = Hall"})):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": step})
        await switch_map(MAP_B, ORDER_B)
        before_options = dict(entry.options)
        done = await hass.config_entries.options.async_configure(flow["flow_id"], user_input)
        check(f"two maps: the {step} form aborts with map_changed when the map changed while it was open", done["type"] == "abort" and done["reason"] == "map_changed" and dict(entry.options) == before_options, str(done))
        await switch_map(MAP_A, ORDER_A)
    # a submit while the map is unchanged still works (the map of the form is the one that is used)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "rooms"})
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"room_names": "-127 = Kitchen\n-126 = Hall\n-125 = Room -125"})
    await hass.async_block_till_done()
    check("two maps: a rename with an unchanged map is stored under that map", done["type"] == "create_entry" and done["data"]["room_names"][device][f"{MAP_A}:-126"] == "Hall", str(done))
    coordinator = entry.runtime_data.coordinators[device]
    client = FakeClient.instance
    vac_entity = hass.data[vacuum_component.DATA_COMPONENT].get_entity(registry.async_get_entity_id("vacuum", "imou_vacuum", f"{device}_vacuum"))
    # the "new rooms" repair issue only compares with the rooms mapped on the active map
    if ha_clean_area() is not None:
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        vacuum_id = registry.async_get_entity_id("vacuum", "imou_vacuum", f"{device}_vacuum")
        issue_id = f"segments_changed_{registry.async_get(vacuum_id).id}"
        issues = ir.async_get(hass)
        mapped = [{"id": f"{MAP_A}:{r}", "name": f"A{r}", "group": None} for r in ORDER_A]
        mapped += [{"id": f"{MAP_B}:{r}", "name": f"B{r}", "group": None} for r in (-130, -129, -128, -127, -126)]
        registry.async_update_entity_options(vacuum_id, "vacuum", {"last_seen_segments": mapped})
        await hass.async_block_till_done()
        await refresh()
        check("two maps: no repair issue when every room of the active map was mapped", issues.async_get_issue("vacuum", issue_id) is None)
        await switch_map(MAP_B, ORDER_B)
        check("two maps: switching to another fully mapped map raises no repair issue", issues.async_get_issue("vacuum", issue_id) is None)
        await switch_map(MAP_A, ORDER_A)
        registry.async_update_entity_options(vacuum_id, "vacuum", {"last_seen_segments": [m for m in mapped if m["id"] != f"{MAP_B}:-129"]})
        await hass.async_block_till_done()
        await refresh()
        check("two maps: no repair issue on map A while only map B misses a mapped room", issues.async_get_issue("vacuum", issue_id) is None)
        await switch_map(MAP_B, ORDER_B)
        check("two maps: a room of the active map that was not mapped raises the issue", issues.async_get_issue("vacuum", issue_id) is not None)
        ir.async_delete_issue(hass, "vacuum", issue_id)
        registry.async_update_entity_options(vacuum_id, "vacuum", {"last_seen_segments": []})
        await switch_map(MAP_A, ORDER_A)
    # 6. switch to map B (sweeper_sequence names the active map)
    client.props["160700"] = {"160701": MAP_B, "160702": json.dumps(ORDER_B)}
    client.props["120600"] = list(ORDER_B)
    await refresh()
    check("two maps: after the switch the active map is B (sweeper_sequence, (a))", coordinator.active_map() == MAP_B and coordinator.room_map() == MAP_B, str(coordinator.active_map()))
    check("two maps: map B gives its own rooms (the schedule of map B and -129 included, none of map A's -125)", coordinator.room_names() == {-130: "Attic", -129: "Room -129", -128: "Room -128", -127: "Study", -126: "Room -126"}, str(coordinator.room_names()))
    check("two maps: new buttons for map B exist; those of map A are unavailable", f"clean_room_{MAP_B}_-127" in clean_ids() and hass.states.get(entity_id(f"clean_room_{MAP_A}_-127")).state == "unavailable" and hass.states.get(entity_id(f"clean_room_{MAP_B}_-127")).state != "unavailable", str(sorted(clean_ids())))
    check("two maps: the buttons of map A are not asked for again and the plain keys are not created", not any(key.startswith("clean_room_-") for key in clean_ids()))
    client.calls.clear()
    try:
        await vac_entity.async_clean_segments([f"{MAP_A}:-127"])
    except ServiceValidationError as err:
        check("two maps: after the switch a room of map A raises", err.translation_key == "room_on_other_map", err.translation_key)
    else:
        check("two maps: after the switch a room of map A raises", False)
    try:
        button_entity = hass.data[button_component.DATA_COMPONENT].get_entity(entity_id(f"clean_room_{MAP_A}_-127"))
        await button_entity.async_press()
    except ServiceValidationError as err:
        check("two maps: pressing a button of the other map raises too", err.translation_key == "room_on_other_map" and client.calls == [], f"{err.translation_key} {client.calls}")
    else:
        check("two maps: pressing a button of the other map raises too", False)
    # 7. the active map is unknown: only the rooms of sequence (plain ids), schedules need a usable map
    del client.props["160700"]
    client.props["120600"] = [-5, -6]
    await refresh()
    check("two maps: no active map: the rooms of sequence, plus a room with a plain (older) name; no room of the schedules or sources", coordinator.active_map() is None and coordinator.room_map() is None and coordinator.room_names() == {-127: "Plain", -6: "Room -6", -5: "Room -5"}, f"{coordinator.active_map()} {coordinator.room_names()}")
    check("two maps: ... no plain Clean buttons are created while the map is unknown", not {f"clean_room_{room}" for room in (-5, -6)} & clean_ids(), str(sorted(clean_ids())))
    check("two maps: ... the names of either map do not apply (only the plain name)", not {"Kitchen", "Study", "Attic"} & set(coordinator.room_names().values()))
    client.calls.clear()
    try:
        await vac_entity.async_clean_segments(["-5"])
    except ServiceValidationError as err:
        check("two maps: ... a plain segment is refused while the map is unknown (active_map_unconfirmed)", err.translation_key == "active_map_unconfirmed" and client.calls == [], f"{err.translation_key} {client.calls}")
    else:
        check("two maps: ... a plain segment is refused while the map is unknown (active_map_unconfirmed)", False)
    # the schedules sensor and the diagnostics know the active map
    from homeassistant.components.diagnostics import async_redact_data  # noqa: F401, PLC0415

    diagnostics = await importlib.import_module(f"{PKG}.diagnostics").async_get_config_entry_diagnostics(hass, entry)
    check("two maps: the diagnostics show the active map", diagnostics["robots"][0]["active_map"] is None and diagnostics["robots"][0]["multi_map"] is True, str({k: diagnostics["robots"][0][k] for k in ("active_map", "multi_map")}))
    client.props["160700"] = {"160701": MAP_A, "160702": "[]"}
    await refresh()
    diagnostics = await importlib.import_module(f"{PKG}.diagnostics").async_get_config_entry_diagnostics(hass, entry)
    check("two maps: ... and the active map once it is known", diagnostics["robots"][0]["active_map"] == MAP_A, str(diagnostics["robots"][0]["active_map"]))


class RV1SingleMapClient(RV1LProClient):
    """The RV1L Pro of a real beta report (one map, ids neutralised): ``sequence``, ``swp_cleansequence`` and
    ``sweeper_sequence`` hold the same 9 rooms and ``swp_allareaConfig`` 5 of them."""

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        order = [-128, -126, -125, -124, -123, -127, -121, -120, -119]
        self.props.update(
            {
                "122300": [{"122302": "Map A", "122301": MAP_A}],
                "120600": list(order),
                "146000": [{"146001": MAP_A, "146002": json.dumps(order)}],
                "160700": {"160701": MAP_A, "160702": json.dumps(order)},
                "147400": [{"147401": MAP_A, "147402": room, "147403": 0} for room in order[:5]],
            }
        )


async def rv1_single_map_checks(hass: Any, entry: Any) -> None:
    from homeassistant.components import vacuum as vacuum_component  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    client = FakeClient.instance
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    wanted = [-128, -126, -125, -124, -123, -127, -121, -120, -119]
    keys = {e.unique_id.split("_", 1)[1] for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    check("single map: the active map is known, but the robot has one map", coordinator.active_map() == MAP_A and not coordinator.is_multi_map() and coordinator.room_map() is None, f"{coordinator.active_map()} {coordinator.is_multi_map()}")
    check("single map: the 9 rooms appear with plain ids and plain names", coordinator.room_names() == {room: f"Room {room}" for room in sorted(wanted)} and set(coordinator.room_segments()) == {str(room) for room in wanted}, str(coordinator.room_names()))
    check("single map: Clean buttons keep the plain unique ids", {k for k in keys if k.startswith("clean_room_")} == {f"clean_room_{room}" for room in wanted}, str(sorted(keys)))
    reads = client.sequence_reads
    client.calls.clear()
    await hass.services.async_call("button", "press", {"entity_id": registry.async_get_entity_id("button", "imou_vacuum", "TESTDEVICE0004_clean_room_-127")}, blocking=True)
    vac_single = hass.data[vacuum_component.DATA_COMPONENT].get_entity(registry.async_get_entity_id("vacuum", "imou_vacuum", "TESTDEVICE0004_vacuum"))
    await vac_single.async_clean_segments(["-126"])
    check("single map: a button and a plain segment id clean without any extra read of the room properties", client.sequence_reads == reads and [c[3]["200903"] for c in client.calls] == [[-127], [-126]], f"{client.sequence_reads - reads} {client.calls}")
    check("single map: the button is named without a map", hass.states.get(registry.async_get_entity_id("button", "imou_vacuum", "TESTDEVICE0004_clean_room_-127")).attributes["friendly_name"].endswith("Clean room -127"))
    # a robot that reports no rooms (empty sequence and lists, no room configuration): nothing breaks
    client.props["120600"] = []
    client.props["146000"] = [{"146001": MAP_A, "146002": "[]"}]
    client.props["160700"] = {"160701": MAP_A, "160702": "[]"}
    del client.props["147400"]
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    check("single map: a robot with an empty cleaning order has no rooms and no failure", await coordinator.async_refresh_schedules() is True and coordinator.room_names() == {} and coordinator.active_map() == MAP_A, str(coordinator.room_names()))
    # the stored names are plain ones; a qualified name of an earlier two-map state is not used
    hass.config_entries.async_update_entry(entry, options={"room_names": {"TESTDEVICE0004": {"-127": "Plain", f"{MAP_A}:-127": "Qualified"}}})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    check("single map: only the plain room names apply", coordinator.room_overrides() == {-127: "Plain"}, str(coordinator.room_overrides()))


def test_rv1_two_maps() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1TwoMapClient, rv1_two_map_checks)


class RV1TwoMapUnknownClient(RV1TwoMapClient):
    """Two maps, but nothing tells which one is active: the order of ``sequence`` matches neither map."""

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        super().__init__(session, app_id, app_secret, host)
        self.props["120600"] = [-5, -6]


async def rv1_two_map_unknown_checks(hass: Any, entry: Any) -> None:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    coordinator = entry.runtime_data.coordinators["TESTDEVICE0004"]
    registry = er.async_get(hass)
    keys = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    check("two maps, active map unknown at setup: multi-map and no active map", coordinator.is_multi_map() and coordinator.active_map() is None, str(coordinator.active_map()))
    check("two maps, active map unknown at setup: rooms are known (plain ids) but no Clean button is created", bool(coordinator.room_names()) and not any("_clean_room_" in key for key in keys), str(sorted(keys)))
    # once the map is known the buttons appear, qualified with the map
    FakeClient.instance.props["160700"] = {"160701": MAP_A, "160702": json.dumps(ORDER_A)}
    FakeClient.instance.props["120600"] = list(ORDER_A)
    coordinator._schedules_fetched_at = None  # noqa: SLF001
    await coordinator.async_refresh_schedules()
    await hass.async_block_till_done()
    keys = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    check("two maps: the Clean buttons appear, with the map, once the active map is known", {f"TESTDEVICE0004_clean_room_{MAP_A}_{room}" for room in ORDER_A} <= keys and not any(k.startswith("TESTDEVICE0004_clean_room_-") for k in keys), str(sorted(keys)))


def test_rv1_two_maps_unknown_at_setup() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1TwoMapUnknownClient, rv1_two_map_unknown_checks)


def test_rv1_single_map_with_room_properties() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", False
    run_fresh(RV1SingleMapClient, rv1_single_map_checks)


def test_optional_entities_of_a_late_robot() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "offline", False
    try:
        run_fresh(RV1LProClient, rv1lpro_late_checks)
    finally:
        FakeClient.initial_status = "online"


def test_optional_entities_of_a_silent_robot() -> None:
    FakeClient.initial_status, RV1LProClient.drop_station = "online", True

    def before(registry: Any) -> None:
        # entities of an earlier version: enabled in the registry
        for platform, key in (("sensor", "dust_bag"), ("sensor", "clean_water_tank")):
            registry.async_get_or_create(platform, "imou_vacuum", f"TESTDEVICE0004_{key}", suggested_object_id=f"rv1l_pro_test_{key}")

    try:
        run_fresh(RV1LProClient, rv1_silent_checks, before)
    finally:
        RV1LProClient.drop_station = False


# ---------------------------------------------------------------------------
# 7. a model that is not fully supported: repair issue, log line, removal
# ---------------------------------------------------------------------------
class UnknownModelClient(FakeClient):
    """A robot of an unknown model; without a dock function and with an unknown label."""

    drop_dock = True
    empty = False

    async def async_list_devices(self) -> list[Any]:
        api = mod("api")
        if UnknownModelClient.empty:
            return []
        return [api.ImouDevice(device_id="TESTDEVICE0003", product_id="PRODUCT_ID_X", name="Mystery robot", model="Zed 9 & Co", status="online", version="1.0.0", brand="lechange")]

    async def async_get_product_model(self, product_id: str) -> dict[str, Any]:
        data = copy.deepcopy(MODEL_JSON)
        if UnknownModelClient.drop_dock:
            data["properties"] = [p for p in data["properties"] if p["identifier"] != "back_charging"]
            next(p for p in data["properties"] if p["identifier"] == "wifi_rssi_sdj")["dataType"]["specs"]["list"][3]["desc"] = "新等级"
        return data


async def unknown_model_checks(config_dir: str) -> None:
    from homeassistant import bootstrap, config_entries, loader  # noqa: PLC0415
    from homeassistant.core import HomeAssistant  # noqa: PLC0415
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    package = importlib.import_module(PKG)
    package.ImouClient = UnknownModelClient
    package.async_get_clientsession = lambda hass: None
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture(level=logging.INFO)
    logging.getLogger(PKG).addHandler(capture)
    UnknownModelClient.drop_dock = True
    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config.language = "en"
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    await bootstrap.async_load_base_functionality(hass)
    await hass.async_start()
    try:
        entry = config_entries.ConfigEntry(
            data={"app_id": "unknown-app", "app_secret": "s", "region": "eu"}, discovery_keys={},
            domain="imou_vacuum", entry_id="unknown-app", minor_version=1, options={}, source="user",
            subentries_data=None, title="t", unique_id="unknown-app", version=1,
        )  # fmt: skip
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        check("unknown model: entry is loaded", entry.state is config_entries.ConfigEntryState.LOADED, str(entry.state))
        registry = ir.async_get(hass)
        issue_id = "model_support_unknown-app_" + hashlib.sha256(b"TESTDEVICE0003").hexdigest()[:12]
        issue = registry.async_get_issue("imou_vacuum", issue_id)
        check("unknown model: a repair issue is created", issue is not None)
        own_issues = [i for (d, i) in registry.issues if d == "imou_vacuum"]
        check("unknown model: the device id is in no issue id or placeholder", all("TESTDEVICE0003" not in i for i in own_issues) and (issue is None or all("TESTDEVICE0003" not in str(v) for v in issue.translation_placeholders.values())) and own_issues == [issue_id], str(own_issues))
        if issue is not None:
            check("unknown model: the issue holds the entry id", dict(issue.data or {}) == {"entry_id": "unknown-app"}, str(issue.data))
            expected_url = "https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=new_model.yml&title=%5BModel%5D%3A+Zed+9+%26+Co&robot_model=Zed+9+%26+Co"
            check("unknown model: issue translation key, severity, not fixable", issue.translation_key == "model_not_fully_supported" and issue.severity == ir.IssueSeverity.WARNING and issue.is_fixable is False, f"{issue.translation_key} {issue.severity} {issue.is_fixable}")
            check("unknown model: placeholders name the model, the missing function and the unknown value", dict(issue.translation_placeholders) == {"model": "Zed 9 & Co", "missing": "return_to_dock", "unknown": "wifi_rssi_sdj"}, str(issue.translation_placeholders))
            check("unknown model: learn_more_url is the prefilled GitHub issue form", issue.learn_more_url == expected_url, str(issue.learn_more_url))
        infos = [r.getMessage() for r in records if r.levelno == logging.INFO and "is not verified yet" in r.getMessage()]
        check("unknown model: exactly one 'not verified yet' info line, naming the model", len(infos) == 1 and infos[0].startswith("Zed 9 & Co is not verified yet; please share"), str(infos))
        coordinator = entry.runtime_data.coordinators["TESTDEVICE0003"]
        vacuum_ents = [e for e in er_entries(hass, entry) if e.domain == "vacuum"]
        check("unknown model: the robot still works (vacuum entity exists)", len(vacuum_ents) == 1 and coordinator.model.prop("back_charging") is None)
        diagnostics = importlib.import_module(f"{PKG}.diagnostics")
        report = (await diagnostics.async_get_config_entry_diagnostics(hass, entry))["robots"][0]["support_report"]
        check("unknown model: diagnostics list what is missing and unknown", report["missing_core"] == ["return_to_dock"] and report["unknown_enum_labels"] == {"wifi_rssi_sdj": {3: "新等级"}} and report["verified_model"] is False, str(report))

        # a later setup that finds nothing missing removes the issue again
        UnknownModelClient.drop_dock = False
        records.clear()
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check("unknown model: the issue is deleted when nothing is missing any more", registry.async_get_issue("imou_vacuum", issue_id) is None)
        infos = [r.getMessage() for r in records if r.levelno == logging.INFO and "is not verified yet" in r.getMessage()]
        check("unknown model: still not verified -> one info line per setup", len(infos) == 1, str(infos))

        # a robot that is no longer found removes its issue
        UnknownModelClient.drop_dock = True
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check("unknown model: the issue is back after a reload", registry.async_get_issue("imou_vacuum", issue_id) is not None)
        UnknownModelClient.empty = True
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check("unknown model: the issue is deleted when the robot is no longer found", registry.async_get_issue("imou_vacuum", issue_id) is None and entry.state is config_entries.ConfigEntryState.SETUP_ERROR, str(entry.state))
        UnknownModelClient.empty = False
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check("unknown model: the issue is back once the robot is found again", registry.async_get_issue("imou_vacuum", issue_id) is not None)

        # removing the config entry removes its issue (and leaves other domains' issues alone)
        ir.async_create_issue(hass, "other_domain", issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="x", data={"entry_id": "unknown-app"})
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        check("unknown model: removing the config entry deletes its repair issue", registry.async_get_issue("imou_vacuum", issue_id) is None and not [1 for (d, _) in registry.issues if d == "imou_vacuum"])
        check("unknown model: an issue of another domain is left alone", registry.async_get_issue("other_domain", issue_id) is not None)
    finally:
        logging.getLogger(PKG).removeHandler(capture)
        await hass.async_stop()


def er_entries(hass: Any, entry: Any) -> list[Any]:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def test_unknown_model() -> None:
    config_dir = tempfile.mkdtemp(prefix="imou-unknown-")
    try:
        asyncio.run(unknown_model_checks(config_dir))
    finally:
        worker = threading.Thread(target=shutil.rmtree, args=(config_dir,), kwargs={"ignore_errors": True})
        worker.start()
        worker.join()


def test_issue_translations() -> None:
    strings, english = STRINGS_JSON, EN_JSON
    check("no schedule switch text, icon or schedule write error is left in the translations", "schedule" not in strings["entity"]["switch"] and "schedule" not in ICONS_JSON["entity"]["switch"] and not [k for k in strings["exceptions"] if k.startswith("schedule_")])
    issue = strings["issues"]["model_not_fully_supported"]
    check("issue text is in strings.json and translations/en.json, identical", strings == english and strings["entity"]["binary_sensor"]["charging"] == {"name": "Charging"})
    check("issue text does not overclaim", "Other functions may work, but the missing or unknown parts may not show or react correctly." in issue["description"] and "Everything else works" not in issue["description"])
    check("issue title and description use the three placeholders", "{model}" in issue["title"] and all(p in issue["description"] for p in ("{model}", "{missing}", "{unknown}")) and "diagnostics" in issue["description"].lower() and "GitHub" in issue["description"], str(issue))


def main() -> int:
    for func in (test_imports, test_entities, test_model_adaptive_entities, test_level_inputs, test_reset_buttons, test_files, test_config_flow_schema, test_full_setup, test_rv1_setup, test_rv1lpro_setup, test_rv1lpro_rooms_without_a_schedule, test_rv1lpro_room_call_fallback, test_rv1_two_maps, test_rv1_two_maps_unknown_at_setup, test_rv1_single_map_with_room_properties, test_optional_entities_of_a_late_robot, test_optional_entities_of_a_silent_robot, test_unknown_model, test_issue_translations):
        print(f"\n=== {func.__name__} ===")
        try:
            func()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            check(f"{func.__name__} ran without an exception", False)
    failed = [name for ok, name in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name in failed:
        print("FAILED:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
