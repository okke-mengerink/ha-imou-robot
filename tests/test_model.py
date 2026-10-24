"""Plain-Python tests: things-model identifier resolution on the L11-A fixture.

Run: python3 -I tests/test_model.py
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import fixture, load, run_all  # noqa: E402

const = load("const")
model_mod = load("model")

EXPECTED_PROP_REFS = {
    "work_status": "12300",
    "power": "11600",
    "back_charging": "1019",
    "clean_area": "1020",
    "clean_time": "1021",
    "windpower": "1027",
    "water": "1028",
    "mopenable": "1029",
    "keep_clean": "11700",
    "auto_dust": "11800",
    "rug_press": "11900",
    "life": "12200",
    "errorinfo": "40500",
    "wifi_rssi_sdj": "11300",
}
EXPECTED_POLL_REFS = [
    "12300", "11600", "1019", "1020", "1021", "1027", "1028",
    "1029", "11700", "11800", "11900", "12200", "40500", "11300",
]  # fmt: skip
DENIED_REFS = {"1022", "1023", "1025", "1026", "40100", "40400"}


def l11a() -> Any:
    return model_mod.ThingsModel.from_api(fixture("l11a_model.json"))


def shout(value: Any) -> Any:
    """Make keys 'Capitalised' and identifiers UPPER-case, like a mixed-case API."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            new_key = key[:1].upper() + key[1:]
            if key == "identifier" and isinstance(item, str):
                item = item.upper()
            out[new_key] = shout(item)
        return out
    if isinstance(value, list):
        return [shout(item) for item in value]
    return value


def test_properties_resolve_by_identifier() -> None:
    model = l11a()
    for identifier, ref in EXPECTED_PROP_REFS.items():
        prop = model.prop(identifier)
        assert prop is not None, identifier
        assert prop.ref == ref, (identifier, prop.ref)


def test_struct_members_resolve() -> None:
    model = l11a()
    life = model.prop("life")
    assert {k: v.ref for k, v in life.children.items()} == {
        "brush_time": "12202",
        "main_brush_time": "12204",
        "rosebox_time": "12206",
        "sensor_time": "12208",
    }
    assert model.member("errorinfo", "errcode").ref == "40501"
    assert model.member("errorinfo", "valid").ref == "40502"
    assert model.member("errorinfo", "nope") is None


def test_services_and_inputs_resolve() -> None:
    model = l11a()
    control = model.service("clean_control")
    assert control.ref == "2700"
    assert control.input_ref("status") == "2701"
    assert control.input_ref("mode") == "2702"
    assert control.input_ref("plan") == "2703"
    assert control.input_ref("does_not_exist") is None
    assert model.service("location").ref == "20400"
    assert model.service("dust").ref == "2300"
    assert model.has_service("clean_control")
    assert not model.has_service("no_such_service")


def test_identifiers_match_case_insensitively() -> None:
    model = l11a()
    assert model.prop("WORK_STATUS").ref == "12300"
    assert model.prop("WindPower").ref == "1027"
    assert model.prop("mopEnable").ref == "1029"
    assert model.service("Clean_Control").ref == "2700"
    assert model.member("LIFE", "Brush_Time").ref == "12202"


def test_mixed_case_api_keys_and_identifiers() -> None:
    model = model_mod.ThingsModel.from_api(shout(fixture("l11a_model.json")))
    assert model.prop("work_status").ref == "12300"
    assert model.prop("work_status").enum_labels[3]  # label present
    assert model.member("life", "main_brush_time").ref == "12204"
    assert model.service("clean_control").input_ref("status") == "2701"
    assert model.service("windwater").ref == "3100"
    assert model.service("windwater").input_ref("wind") == "3103"
    assert model.service("windwater").input_ref("water") == "3102"
    assert model.poll_refs() == EXPECTED_POLL_REFS


def test_poll_refs_exact_and_never_heavy_data() -> None:
    model = l11a()
    assert model.poll_refs() == EXPECTED_POLL_REFS
    assert not DENIED_REFS & set(model.poll_refs())
    # even if a model had them, the heavy identifiers are not in the poll list
    assert not const.NEVER_POLL_IDENTIFIERS & set(const.POLL_IDENTIFIERS)


def test_only_identifiers_in_the_model_are_polled() -> None:
    raw = copy.deepcopy(fixture("l11a_model.json"))
    raw["properties"] = [p for p in raw["properties"] if p["identifier"] not in ("rug_press", "life")]
    model = model_mod.ThingsModel.from_api(raw)
    refs = model.poll_refs()
    assert "11900" not in refs and "12200" not in refs
    assert model.prop("rug_press") is None
    assert model.member("life", "brush_time") is None


def test_robot_detection() -> None:
    assert l11a().has_service("clean_control")
    raw = copy.deepcopy(fixture("l11a_model.json"))
    raw["services"] = [s for s in raw["services"] if s["identifier"] != "clean_control"]
    assert not model_mod.ThingsModel.from_api(raw).has_service("clean_control")
    assert not model_mod.ThingsModel.from_api({}).has_service("clean_control")
    assert not model_mod.ThingsModel.from_api(None).has_service("clean_control")


def test_enum_labels_come_from_the_model() -> None:
    model = l11a()
    assert sorted(model.prop("work_status").enum_labels) == list(range(14))
    assert sorted(model.member("errorinfo", "errcode").enum_labels) == list(range(12))
    assert sorted(model.prop("wifi_rssi_sdj").enum_labels) == [0, 1, 2, 3, 4]
    # every known value has a translation key, nothing is left unmapped
    assert set(model.prop("work_status").enum_labels) == set(const.WORK_STATUS_KEYS)
    assert set(model.member("errorinfo", "errcode").enum_labels) == set(const.ERROR_KEYS)
    assert set(model.prop("wifi_rssi_sdj").enum_labels) == set(const.WIFI_KEYS)


def test_numeric_ranges_are_parsed_from_the_model() -> None:
    model = l11a()
    wind = model.prop("windpower")
    assert (wind.minimum, wind.maximum, wind.step) == (0.0, 100.0, 1.0)
    power = model.prop("power")
    assert (power.minimum, power.maximum) == (-100.0, 100.0)


def test_documented_fallback_refs_only_fill_missing_refs() -> None:
    raw = copy.deepcopy(fixture("l11a_model.json"))
    for prop in raw["properties"]:
        if prop["identifier"] == "work_status":
            prop["ref"] = ""  # declared, but without a usable ref
    for service in raw["services"]:
        if service["identifier"] == "clean_control":
            service["ref"] = None
            for item in service["inputData"]:
                if item["identifier"] == "status":
                    item["ref"] = ""
    model = model_mod.ThingsModel.from_api(raw)
    assert model.prop("work_status").ref == "12300"  # fallback
    assert model.service("clean_control").ref == "2700"
    assert model.service("clean_control").input_ref("status") == "2701"
    # an unknown identifier without a ref is dropped, never invented
    raw["properties"].append({"identifier": "brand_new", "ref": "", "dataType": {"type": "int", "specs": {}}})
    assert model_mod.ThingsModel.from_api(raw).prop("brand_new") is None


def test_wind_water_fallback_service_resolves() -> None:
    service = l11a().service("WindWater")
    assert service is not None and service.ref == "3100"
    assert service.input_ref("wind") == "3103"
    assert service.input_ref("water") == "3102"


def test_wind_water_documented_fallback_refs() -> None:
    raw = copy.deepcopy(fixture("l11a_model.json"))
    for service in raw["services"]:
        if service["identifier"] == "WindWater":
            service["ref"] = ""
            for item in service["inputData"]:
                item["ref"] = None
    service = model_mod.ThingsModel.from_api(raw).service("windwater")
    assert (service.ref, service.input_ref("wind"), service.input_ref("water")) == ("3100", "3103", "3102")


def test_level_maps_and_unknown_values() -> None:
    assert const.LEVEL_KEYS == {
        "windpower": {1: "quiet", 2: "standard", 3: "max"},
        "water": {1: "low", 2: "medium", 3: "high"},
    }
    level_key = model_mod.level_key
    for identifier in ("windpower", "water"):
        levels = const.LEVEL_KEYS[identifier]
        assert [level_key(levels, n) for n in (1, 2, 3)] == list(levels.values())
        # anything that is not a known level shows up as unknown_<n>
        assert [level_key(levels, n) for n in (0, 4, 99, -1)] == ["unknown_0", "unknown_4", "unknown_99", "unknown_-1"]
        assert level_key(levels, None) is None
    # the level properties exist in the L11-A model, which misleadingly declares 0-100
    model = l11a()
    assert model.prop("windpower").maximum == 100.0 and model.prop("water").maximum == 100.0
    assert all(identifier in const.POLL_IDENTIFIERS for identifier in const.LEVEL_KEYS)
    assert const.DEFAULT_LEVEL == 2 and const.DEFAULT_LEVEL in const.LEVEL_KEYS["windpower"] and const.DEFAULT_LEVEL in const.LEVEL_KEYS["water"]


def test_report_tracker_without_evidence_disables_nothing() -> None:
    tracker = model_mod.ReportTracker()
    assert tracker.enabled_by_default("auto_dust") and not tracker.is_reported("auto_dust")
    tracker.update(False, {})  # an offline poll is no evidence either
    tracker.update(False, {"auto_dust": 1})  # and its values are ignored
    assert tracker.enabled_by_default("auto_dust") and not tracker.is_reported("auto_dust")
    assert not tracker.seen_online


def test_report_tracker_flags_settings_missing_from_an_online_poll() -> None:
    tracker = model_mod.ReportTracker()
    tracker.update(True, {"rug_press": 1})
    assert tracker.seen_online
    assert tracker.enabled_by_default("rug_press") and tracker.is_reported("rug_press")
    assert not tracker.enabled_by_default("auto_dust") and not tracker.is_reported("auto_dust")
    tracker.update(True, {"auto_dust": 0})  # a later report counts as reported
    assert tracker.is_reported("auto_dust") and tracker.enabled_by_default("auto_dust")
    assert tracker.as_diagnostics() == {"seen_online": True, "reported_identifiers": ["auto_dust", "rug_press"]}


def test_l11a_fixture_reports_carpet_boost_but_not_auto_dust_or_keep_clean() -> None:
    model = l11a()
    tracker = model_mod.ReportTracker()
    tracker.update(True, model.parse_values(fixture("l11a_values.json")["properties"]))
    assert tracker.enabled_by_default("rug_press")  # 11900 is reported
    assert not tracker.enabled_by_default("auto_dust")  # 11800 never is
    assert not tracker.enabled_by_default("keep_clean")  # 11700 never is
    # all three switches exist in the model, so they are created either way
    assert all(model.prop(identifier) for identifier in ("rug_press", "auto_dust", "keep_clean"))


def test_parse_values_on_observed_data() -> None:
    model = l11a()
    values = model.parse_values(fixture("l11a_values.json")["properties"])
    assert values["work_status"] == 3
    assert values["power"] == 1
    assert values["mopenable"] == 0
    assert values["life"] == {
        "brush_time": 0,
        "main_brush_time": 0,
        "rosebox_time": 0,
        "sensor_time": 0,
    }
    # not reported by the robot -> absent
    assert "errorinfo" not in values and "auto_dust" not in values
    assert model.parse_values(None) == {}
    # int keys and struct values keyed by ints also work
    assert model.parse_values({12300: 7, 40500: {40501: 5, 40502: True}}) == {
        "work_status": 7,
        "errorinfo": {"errcode": 5, "valid": True},
    }


def test_coercions() -> None:
    to_int, to_bool = model_mod.to_int, model_mod.to_bool
    assert to_int(True) == 1 and to_int("55") == 55 and to_int("7.6") == 8 and to_int(2.0) == 2
    assert to_int(None) is None and to_int("x") is None and to_int(float("nan")) is None
    assert to_bool(1) is True and to_bool(0) is False and to_bool("true") is True
    assert to_bool("0") is False and to_bool(None) is None and to_bool("maybe") is None


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
