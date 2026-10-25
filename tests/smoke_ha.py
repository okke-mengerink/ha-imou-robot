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

RESULTS: list[tuple[bool, str]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    """Record one check."""
    ok = bool(condition)
    RESULTS.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def mod(name: str) -> Any:
    return importlib.import_module(f"{PKG}.{name}")


# ---------------------------------------------------------------------------
# 1. imports
# ---------------------------------------------------------------------------
MODULES = [
    "api", "binary_sensor", "button", "commanded", "config_flow", "const", "coordinator",
    "diagnostics", "entity", "health", "model", "select", "sensor", "switch", "vacuum",
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

    def __init__(self, model_json: dict[str, Any], raw_values: dict[str, Any], online: bool = True) -> None:
        api, coordinator = mod("api"), mod("coordinator")
        self.model = mod("model").ThingsModel.from_api(model_json)
        self.device = api.ImouDevice(
            device_id="TESTDEVICE0001",
            product_id="PRODUCT_ID",
            name="RV-L11-A-TEST",
            model="L11-A",
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

    def set_values(self, **values: Any) -> None:
        self.data.values.update(values)

    def async_add_listener(self, *args: Any, **kwargs: Any) -> Any:
        return lambda: None

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


def make_entities(coordinator: Any) -> dict[str, list[Any]]:
    """Run every platform's async_setup_entry against the fake coordinator."""
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinators={"x": coordinator}))
    out: dict[str, list[Any]] = {}
    for platform in ("vacuum", "sensor", "binary_sensor", "switch", "select", "button"):
        collected: list[Any] = []

        def add(entities: Any, collected: list[Any] = collected, **_: Any) -> None:
            collected.extend(entities)

        asyncio.run(mod(platform).async_setup_entry(None, entry, add))
        out[platform] = collected
    return out


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
    check("entity counts per platform (1/10/1/3/2/1)", [len(ents[p]) for p in ents] == [1, 10, 1, 3, 2, 1], str({p: len(v) for p, v in ents.items()}))
    check("18 entities with unique ids", total == 18 and len({e.unique_id for v in ents.values() for e in v}) == 18)

    # --- vacuum -----------------------------------------------------------
    vac = ents["vacuum"][0]
    expected_features = (
        VacuumEntityFeature.START | VacuumEntityFeature.PAUSE | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME | VacuumEntityFeature.LOCATE | VacuumEntityFeature.STATE
        | VacuumEntityFeature.FAN_SPEED
    )  # fmt: skip
    check("vacuum features START|PAUSE|STOP|RETURN_HOME|LOCATE|STATE|FAN_SPEED", vac.supported_features == expected_features, str(vac.supported_features))
    unwanted = 0
    for name in ("SEND_COMMAND", "CLEAN_SPOT", "CLEAN_AREA", "MAP", "TURN_ON", "TURN_OFF", "STATUS"):
        unwanted |= int(getattr(VacuumEntityFeature, name, 0))  # CLEAN_AREA only exists in newer HA
    check("vacuum has no SEND_COMMAND/CLEAN_*/MAP features", not (int(vac.supported_features) & unwanted))
    check("vacuum has the translation key 'robot' (fan speed names)", vac.translation_key == "robot")
    check("vacuum activity DOCKED for work_status 3", vac.activity == VacuumActivity.DOCKED, str(vac.activity))
    check("vacuum state string is 'docked'", vac.state == "docked", str(vac.state))
    check("vacuum attributes work_status/error", vac.extra_state_attributes == {"work_status": 3, "error": 0}, str(vac.extra_state_attributes))
    check("vacuum has_entity_name and main-entity name None", vac.has_entity_name and vac.name is None)

    expected_activity = {
        0: VacuumActivity.IDLE, 1: VacuumActivity.PAUSED, 13: VacuumActivity.PAUSED,
        2: VacuumActivity.RETURNING, 3: VacuumActivity.DOCKED, 11: VacuumActivity.DOCKED,
        6: VacuumActivity.ERROR,
        **{n: VacuumActivity.CLEANING for n in (4, 5, 7, 8, 9, 10, 12)},
    }  # fmt: skip
    wrong = {}
    for status, activity in expected_activity.items():
        coordinator.set_values(work_status=status)
        if vac.activity != activity:
            wrong[status] = vac.activity
    check("work_status -> VacuumActivity table (0..13)", not wrong, str(wrong))
    coordinator.set_values(work_status=99)
    check("unknown work_status -> activity None (state unknown)", vac.activity is None and vac.state in (None, "unknown"))
    coordinator.set_values(work_status=3)

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
    for raw, want in ((1, 1), (150, 100), (-20, 0), (0, 0), (100, 100), ("55", 55), (None, None)):
        coordinator.set_values(power=raw)
        if battery.native_value != want:
            clamps[raw] = battery.native_value
    check("battery clamps to 0..100 (1,150,-20,0,100,'55',None)", not clamps, str(clamps))
    coordinator.set_values(power=1)
    check("battery observed value is 1", battery.native_value == 1)

    status = sensors["status"]
    check("status sensor is an ENUM", status.device_class == SensorDeviceClass.ENUM)
    check("status options = all 14 translation keys in value order", status.options == [const.WORK_STATUS_KEYS[n] for n in range(14)], str(status.options))
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
            prop["dataType"]["specs"]["list"].append({"value": "14", "desc": "测试"})
    coordinator2 = FakeCoordinator(raw_model, VALUES_JSON["properties"])
    status2 = by_key(make_entities(coordinator2)["sensor"])["status"]
    coordinator2.set_values(work_status=14)
    check("unknown model enum value -> option unknown_14", status2.options[-1] == "unknown_14" and status2.native_value == "unknown_14")
    check("unknown value exposes model_label attribute", status2.extra_state_attributes == {"raw_value": 14, "model_label": "测试"}, str(status2.extra_state_attributes))
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
        "only the switch whose setting the robot reports is enabled by default (11900 yes, 11700/11800 no)",
        {k: sw.entity_registry_enabled_default for k, sw in switches.items()} == {"auto_empty": False, "carpet_boost": True, "resume_after_charging": False},
        str({k: sw.entity_registry_enabled_default for k, sw in switches.items()}),
    )  # fmt: skip
    check(
        "switch attribute reported_by_device",
        {k: sw.extra_state_attributes for k, sw in switches.items()} == {"auto_empty": {"reported_by_device": False}, "carpet_boost": {"reported_by_device": True}, "resume_after_charging": {"reported_by_device": False}},
    )  # fmt: skip
    all_reported = by_key(make_entities(FakeCoordinator(MODEL_JSON, {**VALUES_JSON["properties"], "11700": 0, "11800": 1}))["switch"])
    check("a robot that reports all three: all enabled by default, all reported", all(sw.entity_registry_enabled_default and sw.extra_state_attributes["reported_by_device"] for sw in all_reported.values()))
    no_evidence = by_key(make_entities(FakeCoordinator(MODEL_JSON, {}, online=False))["switch"])
    check("no evidence (offline at the first poll): nothing is disabled", all(sw.entity_registry_enabled_default and not sw.extra_state_attributes["reported_by_device"] for sw in no_evidence.values()))
    check("switches are still created for every identifier in the model", set(switches) == {"auto_empty", "carpet_boost", "resume_after_charging"})
    check("switch states: auto_empty/resume unknown, carpet_boost on", (switches["auto_empty"].is_on, switches["resume_after_charging"].is_on, switches["carpet_boost"].is_on) == (None, None, True))

    async def switch_calls() -> list[tuple[Any, ...]]:
        coordinator.calls.clear()
        await switches["auto_empty"].async_turn_on()
        await switches["carpet_boost"].async_turn_off()
        await switches["resume_after_charging"].async_turn_on()
        return list(coordinator.calls)

    calls = asyncio.run(switch_calls())
    check("switches write 1/0 by identifier", calls == [("set", {"auto_dust": 1}), ("set", {"rug_press": 0}), ("set", {"keep_clean": 1})], str(calls))
    check("commanded value is shown until the cloud reports it", switches["auto_empty"].is_on is True and switches["carpet_boost"].is_on is False and switches["resume_after_charging"].is_on is True)

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
    check("entities available again", all(e.available for v in ents.values() for e in v))

    # --- only entities whose identifiers exist -----------------------------------
    slim = copy.deepcopy(MODEL_JSON)
    drop = {"rug_press", "life", "mopEnable", "WindPower", "wifi_rssi_sdj", "errorinfo"}
    slim["properties"] = [p for p in slim["properties"] if p["identifier"] not in drop]
    slim["services"] = [s for s in slim["services"] if s["identifier"] not in ("dust", "location")]
    slim_ents = make_entities(FakeCoordinator(slim, VALUES_JSON["properties"]))
    keys = {e.unique_id.split("_", 1)[1] for v in slim_ents.values() for e in v}
    check("entities only for identifiers in the model", not keys & {"carpet_boost", "side_brush", "main_brush", "filter", "sensors", "mop_fitted", "suction_level", "wifi_signal", "error", "empty_bin"} and {"vacuum", "battery", "status", "mop_water_level", "auto_empty", "resume_after_charging"} <= keys, str(sorted(keys)))
    slim_vac = slim_ents["vacuum"][0]
    check("vacuum without 'location' service has no LOCATE, without WindPower no FAN_SPEED", not slim_vac.supported_features & VacuumEntityFeature.LOCATE and not slim_vac.supported_features & VacuumEntityFeature.FAN_SPEED and bool(slim_vac.supported_features & VacuumEntityFeature.START))


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
    nl = json.loads((PKG_DIR / "translations/nl.json").read_text("utf-8"))
    check("strings.json equals translations/en.json", strings == en)
    check("nl.json has exactly the same keys as en.json", keyset(en) == keyset(nl), str(keyset(en) ^ keyset(nl)))

    descriptions: dict[str, list[Any]] = {
        "sensor": list(mod("sensor").SENSORS) + list(mod("sensor").ENUM_SENSORS),
        "switch": list(mod("switch").SWITCHES),
        "select": list(mod("select").SELECTS),
        "button": list(mod("button").BUTTONS),
        "binary_sensor": list(mod("binary_sensor").BINARY_SENSORS),
    }
    for lang, data in (("en", en), ("nl", nl)):
        missing = [f"{p}.{d.translation_key}" for p, ds in descriptions.items() for d in ds if not data["entity"].get(p, {}).get(d.translation_key, {}).get("name")]
        check(f"[{lang}] every entity translation_key has a name", not missing, str(missing))
        for platform, key, known in (
            ("sensor", "status", const.WORK_STATUS_KEYS), ("sensor", "error", const.ERROR_KEYS), ("sensor", "wifi_signal", const.WIFI_KEYS),
            ("select", "suction_level", const.LEVEL_KEYS["windpower"]), ("select", "mop_water_level", const.LEVEL_KEYS["water"]),
        ):  # fmt: skip
            states = data["entity"][platform][key]["state"]
            check(f"[{lang}] {platform}.{key} has a translated state for every known value", set(states) == set(known.values()) and all(states.values()), str(set(known.values()) ^ set(states)))
        fan_states = data["entity"]["vacuum"]["robot"]["state_attributes"]["fan_speed"]["state"]
        check(f"[{lang}] vacuum fan speed presets are translated", set(fan_states) == set(const.LEVEL_KEYS["windpower"].values()) and all(fan_states.values()), str(fan_states))
        check(f"[{lang}] region selector options", set(data["selector"]["region"]["options"]) == set(const.REGION_HOSTS))
        check(f"[{lang}] config errors/abort/steps present", {"cannot_connect", "invalid_auth", "unknown"} <= set(data["config"]["error"]) and "already_configured" in data["config"]["abort"] and {"user", "reauth_confirm"} <= set(data["config"]["step"]))
        used = set()
        for fname in ("coordinator.py", "__init__.py", "vacuum.py", "select.py"):
            used |= set(re.findall(r'translation_domain=DOMAIN,\s*translation_key="([a-z_]+)"', (PKG_DIR / fname).read_text("utf-8")))
        check(f"[{lang}] every exception translation_key exists", used and used <= set(data["exceptions"]), str(used - set(data["exceptions"])))
        for key, item in data["exceptions"].items():
            placeholders = set(re.findall(r"\{(\w+)\}", item["message"]))
            check(f"[{lang}] exception '{key}' placeholders are known", placeholders <= {"error", "feature", "fan_speed", "option"}, str(placeholders))
    check("no Dutch leaked into en.json / English into nl names (spot check)", en["entity"]["sensor"]["battery"]["name"] == "Battery" and nl["entity"]["sensor"]["battery"]["name"] == "Batterij")
    check("Dutch suction labels stil / standaard / maximaal", nl["entity"]["select"]["suction_level"]["state"] == {"quiet": "Stil", "standard": "Standaard", "max": "Maximaal"} and nl["entity"]["vacuum"]["robot"]["state_attributes"]["fan_speed"]["state"] == {"quiet": "Stil", "standard": "Standaard", "max": "Maximaal"})
    check("Dutch water labels laag / gemiddeld / hoog", nl["entity"]["select"]["mop_water_level"]["state"] == {"low": "Laag", "medium": "Gemiddeld", "high": "Hoog"})
    check("English suction/water labels", en["entity"]["select"]["suction_level"]["state"] == {"quiet": "Quiet", "standard": "Standard", "max": "Max"} and en["entity"]["select"]["mop_water_level"]["state"] == {"low": "Low", "medium": "Medium", "high": "High"})
    check("no number entities are left in the translations", "number" not in en["entity"] and "number" not in nl["entity"])

    icons = json.loads((PKG_DIR / "icons.json").read_text("utf-8"))
    icon_keys = {(p, k) for p, d in icons["entity"].items() for k in d}
    all_keys = {(p, k) for p, d in en["entity"].items() for k in d}
    check("icons.json only references translated entities", icon_keys <= all_keys, str(icon_keys - all_keys))

    manifest = json.loads((PKG_DIR / "manifest.json").read_text("utf-8"))
    keys = list(manifest)
    check("manifest keys: domain, name, then alphabetical (hassfest)", keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:]), str(keys))
    check(
        "manifest values",
        manifest["domain"] == "imou_vacuum" and manifest["name"] == "Imou Vacuum (Open Platform)" and manifest["version"] == "0.2.0"
        and manifest["config_flow"] is True and manifest["iot_class"] == "cloud_polling" and manifest["integration_type"] == "hub"
        and manifest["requirements"] == [] and manifest["codeowners"] == ["@okke-mengerink"]
        and manifest["documentation"] == "https://github.com/okke-mengerink/ha-imou-robot"
        and manifest["issue_tracker"] == "https://github.com/okke-mengerink/ha-imou-robot/issues",
    )  # fmt: skip
    hacs = json.loads((ROOT / "hacs.json").read_text("utf-8"))
    check("hacs.json", hacs == {"name": "Imou Vacuum (Open Platform)", "homeassistant": "2026.1.0", "render_readme": True}, str(hacs))
    check("brand icons present", (PKG_DIR / "brand/icon.png").is_file() and (PKG_DIR / "brand/icon@2x.png").is_file())

    # the domain was renamed in 0.2.0: manifest, folder and DOMAIN agree, and nothing of the old name is left
    check("domain: manifest, folder name and DOMAIN are all 'imou_vacuum'", manifest["domain"] == PKG_DIR.name == const.DOMAIN == "imou_vacuum", f"{manifest['domain']} {PKG_DIR.name} {const.DOMAIN}")
    check("display name 'Imou Vacuum (Open Platform)' in manifest, hacs.json and both translations", {manifest["name"], hacs["name"], en["title"], nl["title"]} == {"Imou Vacuum (Open Platform)"}, str({manifest["name"], hacs["name"], en["title"], nl["title"]}))
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
class FakeClient:
    """Stands in for ImouClient (no network)."""

    instance: FakeClient | None = None
    validate_error: Exception | None = None
    devices_override: list[Any] | None = None
    model_error: Exception | None = None
    robot_id = "TESTDEVICE0001"
    initial_status = "online"
    report_optional = False  # a firmware that also reports 11700/11800
    initial_fail_with: Exception | None = None  # the first polls fail

    def __init__(self, session: Any, app_id: str, app_secret: str, host: str) -> None:
        FakeClient.instance = self
        self.app_id, self.host = app_id, host
        self.status = FakeClient.initial_status
        self.props: dict[str, Any] = copy.deepcopy(VALUES_JSON["properties"])
        if FakeClient.report_optional:
            self.props.update({"11700": 0, "11800": 0})
        self.calls: list[tuple[Any, ...]] = []
        self.poll_count = 0
        self.fail_with: Exception | None = FakeClient.initial_fail_with  # makes polls and commands fail
        self.report_while_not_online = False  # a cloud that still returns the last values while asleep
        self.fail_commands_with: Exception | None = None  # makes only commands fail
        self.fail_set_with: Exception | None = None  # makes only property writes fail
        self.fail_control_with: Exception | None = None  # makes only service calls fail
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
        self.last_refs = list(refs)
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
    for key in ("auto_empty", "resume_after_charging"):
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
    check("polled refs are exactly the spec list", client.last_refs == ["12300", "11600", "1019", "1020", "1021", "1027", "1028", "1029", "11700", "11800", "11900", "12200", "40500", "11300"], str(client.last_refs))
    check("never polls map/trail/zone refs", not {"1022", "1023", "1025", "1026", "40100", "40400"} & set(client.last_refs))

    # ---- registries -------------------------------------------------------
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    device = devices[0] if len(devices) == 1 else None
    check("one device with name/model/manufacturer/sw_version", device is not None and (device.name, device.model, device.manufacturer, device.sw_version) == ("RV-L11-A-TEST", "L11-A", "Imou", "0.4.50") and ("imou_vacuum", "TESTDEVICE0001") in device.identifiers, str(device))
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    check("18 entities registered", len(entities) == 18, str(len(entities)))
    ids = {e.unique_id.split("_", 1)[1]: e.entity_id for e in entities}
    print("entity ids:", ", ".join(sorted(ids.values())))
    check("existing registry entries stay enabled (upgrade): switches are not disabled", all(e.disabled_by is None for e in entities if e.domain == "switch"), str({e.unique_id: e.disabled_by for e in entities if e.domain == "switch"}))
    check("the first refresh ran before the entities were created", coordinator.reports.seen_online and "rug_press" in coordinator.reports.reported and not {"auto_dust", "keep_clean"} & coordinator.reports.reported, str(coordinator.reports.as_diagnostics()))

    def state(key: str) -> Any:
        return hass.states.get(ids[key])

    check("vacuum entity id is vacuum.rv_l11_a_test", ids["vacuum"] == "vacuum.rv_l11_a_test", ids["vacuum"])
    check("vacuum state is docked", state("vacuum").state == "docked", str(state("vacuum")))
    vac_attrs = state("vacuum").attributes
    check("vacuum attributes: work_status 3, error 0, supported_features", vac_attrs["work_status"] == 3 and vac_attrs["error"] == 0 and vac_attrs["supported_features"] == 12860, str(dict(vac_attrs)))
    check("vacuum fan speed attributes", vac_attrs["fan_speed"] == "standard" and list(vac_attrs["fan_speed_list"]) == ["quiet", "standard", "max"], str(dict(vac_attrs)))
    expected_states = {
        "battery": "1", "status": "charging", "error": "none", "wifi_signal": "fair",
        "cleaned_area": "0", "cleaning_time": "0", "side_brush": "0.0", "main_brush": "0.0",
        "filter": "0.0", "sensors": "0.0", "mop_fitted": "off", "auto_empty": "unknown",
        "carpet_boost": "on", "resume_after_charging": "unknown",
        "suction_level": "standard", "mop_water_level": "medium",
    }  # fmt: skip
    got = {k: state(k).state for k in expected_states}
    check("initial states from the observed values", got == expected_states, str({k: (v, expected_states[k]) for k, v in got.items() if v != expected_states[k]}))
    check("switch attribute reported_by_device is shown", (state("carpet_boost").attributes["reported_by_device"], state("auto_empty").attributes["reported_by_device"], state("resume_after_charging").attributes["reported_by_device"]) == (True, False, False))
    check("entity names are translated (en)", state("battery").name == "RV-L11-A-TEST Battery" and state("empty_bin").name == "RV-L11-A-TEST Empty bin" and state("carpet_boost").name == "RV-L11-A-TEST Carpet boost" and state("suction_level").name == "RV-L11-A-TEST Suction" and state("mop_water_level").name == "RV-L11-A-TEST Water level", f"{state('battery').name} | {state('suction_level').name}")
    check("status sensor lists options", state("status").attributes["options"][3] == "charging" and len(state("status").attributes["options"]) == 14)
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

    await call("switch", "turn_on", "auto_empty")
    check("auto-empty on -> set {11800:1}", client.calls == [("set", "TESTDEVICE0001", {"11800": 1})], str(client.calls))
    await call("switch", "turn_off", "carpet_boost")
    check("carpet boost off -> set {11900:0}", client.calls == [("set", "TESTDEVICE0001", {"11900": 0})], str(client.calls))
    await call("switch", "turn_on", "resume_after_charging")
    check("resume after charging on -> set {11700:1}", client.calls == [("set", "TESTDEVICE0001", {"11700": 1})], str(client.calls))
    await call("select", "select_option", "suction_level", option="max")
    check("suction 'max' -> WindWater {Wind:3, Water:2 from the cloud}, no property write", client.calls == [("control", "TESTDEVICE0001", "3100", {"3103": 3, "3102": 2})], str(client.calls))
    await call("select", "select_option", "mop_water_level", option="low")
    check("water 'low' -> WindWater {Water:1, Wind:3 = the pending command, the cloud has not caught up}", client.calls == [("control", "TESTDEVICE0001", "3100", {"3102": 1, "3103": 3})], str(client.calls))
    await call("vacuum", "set_fan_speed", "vacuum", fan_speed="quiet")
    check("fan speed 'quiet' -> WindWater {Wind:1, Water:1 = pending}", client.calls == [("control", "TESTDEVICE0001", "3100", {"3103": 1, "3102": 1})], str(client.calls))
    await call("button", "press", "empty_bin")
    check("empty bin -> control 2300 {}", client.calls == [("control", "TESTDEVICE0001", "2300", {})], str(client.calls))
    await hass.async_block_till_done()
    check("switch states follow the commanded values", (state("auto_empty").state, state("carpet_boost").state, state("resume_after_charging").state) == ("on", "off", "on"))
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
    check("entities are back after reauth reload", len(ids) == 18 and state("vacuum").state == "docked")

    # ---- diagnostics ------------------------------------------------------------------------
    diagnostics = importlib.import_module(f"{PKG}.diagnostics")
    dump = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    blob = json.dumps(dump, ensure_ascii=False, default=str)
    check("diagnostics redact App ID/Secret", dump["entry"]["app_id"] == "**REDACTED**" and dump["entry"]["app_secret"] == "**REDACTED**")
    check("diagnostics contain no secrets, serial or name", not any(s in blob for s in ("test-app", "new-secret", "test-secret", "TESTDEVICE0001", "RV-L11-A-TEST")), "leak")
    check("diagnostics tell which settings the robot reports", dump["robots"][0]["reports"]["seen_online"] is True and "rug_press" in dump["robots"][0]["reports"]["reported_identifiers"] and "auto_dust" not in dump["robots"][0]["reports"]["reported_identifiers"], str(dump["robots"][0]["reports"]))
    check("diagnostics include the things model and values", dump["robots"][0]["things_model"]["properties"] and dump["robots"][0]["values"]["work_status"] == 3 and dump["robots"][0]["resolved_model"]["services"]["clean_control"]["ref"] == "2700")

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
    switch_keys = ("auto_empty", "carpet_boost", "resume_after_charging")
    for label, app_id, robot_id, flags, expected_disabled in (
        ("the real L11-A (11700 and 11800 never reported)", "fresh-l11a", "FRESHROBOT0001", {}, {"auto_empty", "resume_after_charging"}),
        ("a firmware that reports all three", "fresh-all", "FRESHROBOT0002", {"report_optional": True}, set()),
        ("a robot that is offline at the first poll (no evidence)", "fresh-offline", "FRESHROBOT0003", {"initial_status": "offline"}, set()),
        ("a robot that is asleep at the first poll (no evidence)", "fresh-sleep", "FRESHROBOT0004", {"initial_status": "sleep"}, set()),
    ):  # fmt: skip
        FakeClient.robot_id = robot_id
        for name, value in flags.items():
            setattr(FakeClient, name, value)
        fresh = await add_entry(app_id)
        regs = {e.unique_id.removeprefix(f"{robot_id}_"): e for e in er.async_entries_for_config_entry(ent_reg, fresh.entry_id)}
        disabled = {k for k in switch_keys if regs[k].disabled_by is not None}
        check(f"fresh install, {label}: disabled by default = {sorted(expected_disabled)}", disabled == expected_disabled and fresh.state is config_entries.ConfigEntryState.LOADED, f"{disabled} {fresh.state}")
        check(f"fresh install, {label}: all 18 entities are created (disabled ones in the registry only)", len(regs) == 18, str(len(regs)))
        if expected_disabled:
            check(f"fresh install, {label}: disabled_by is the integration, disabled switches have no state", all(regs[k].disabled_by is er.RegistryEntryDisabler.INTEGRATION and hass.states.get(regs[k].entity_id) is None for k in expected_disabled))
            check(f"fresh install, {label}: the reported switch is on and says so", hass.states.get(regs["carpet_boost"].entity_id).state == "on" and hass.states.get(regs["carpet_boost"].entity_id).attributes["reported_by_device"] is True)
        await hass.config_entries.async_remove(fresh.entry_id)
        FakeClient.robot_id, FakeClient.initial_status, FakeClient.report_optional = "TESTDEVICE0001", "online", False

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


def main() -> int:
    for func in (test_imports, test_entities, test_level_inputs, test_files, test_config_flow_schema, test_full_setup):
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
