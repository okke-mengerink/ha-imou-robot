"""Plain-Python tests: label based mapping and the support report.

Run: python3 -I tests/test_support.py
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import fixture, load, run_all  # noqa: E402

const = load("const")
model_mod = load("model")
support = load("support")


def build(name: str, edit: Any = None) -> Any:
    data = copy.deepcopy(fixture(name))
    if edit is not None:
        edit(data)
    return model_mod.ThingsModel.from_api(data)


def l11a() -> Any:
    return build("l11a_model.json")


def rv1() -> Any:
    return build("rv1promax_model.json")


def prop_of(data: dict[str, Any], identifier: str) -> dict[str, Any]:
    return next(p for p in data["properties"] if p["identifier"] == identifier)


def test_labels_give_the_same_keys_as_the_numeric_tables() -> None:
    for model in (l11a(), rv1()):
        assert model.work_status_keys() == const.WORK_STATUS_KEYS
        assert model.error_keys() == const.ERROR_KEYS or model.prop("errorinfo") is None
        assert model.wifi_keys() == const.WIFI_KEYS
    assert l11a().error_keys() == const.ERROR_KEYS
    # without errorinfo (RV1 Pro Max) the numeric table applies
    assert rv1().error_keys() == const.ERROR_KEYS
    # every label of both fixtures is in the label maps
    for model in (l11a(), rv1()):
        for identifier, child, labels in (
            ("work_status", None, const.WORK_STATUS_LABEL_KEYS),
            ("wifi_rssi_sdj", None, const.WIFI_LABEL_KEYS),
            ("errorinfo", "errcode", const.ERROR_LABEL_KEYS),
        ):
            prop = model.member(identifier, child)
            if prop is not None:
                assert {label.strip() for label in prop.enum_labels.values()} <= set(labels)


def test_label_maps_are_consistent_with_the_numeric_tables() -> None:
    assert set(const.WORK_STATUS_LABEL_KEYS.values()) == set(const.WORK_STATUS_KEYS.values())
    assert set(const.WIFI_LABEL_KEYS.values()) == set(const.WIFI_KEYS.values())
    assert set(const.ERROR_LABEL_KEYS.values()) == set(const.ERROR_KEYS.values())
    assert const.ACTIVE_STATUS_KEYS <= set(const.WORK_STATUS_KEYS.values())
    assert const.PAUSED_STATUS_KEYS <= set(const.WORK_STATUS_KEYS.values())


def test_renumbered_work_status_maps_by_label() -> None:
    def reverse(data: dict[str, Any]) -> None:
        items = prop_of(data, "work_status")["dataType"]["specs"]["list"]
        for item in items:
            item["value"] = str(13 - int(item["value"]))

    model = build("l11a_model.json", reverse)
    keys = model.work_status_keys()
    assert keys[13] == "standby" and keys[10] == "charging" and keys[0] == "return_paused"
    assert keys[11] == "returning" and keys[6] == "room_clean"
    # the declared labels disagree with the number table, so undeclared values are unknown
    assert keys[14] == "unknown_14" and keys[19] == "unknown_19"
    assert list(keys) == sorted(keys)
    assert not any(key.startswith("unknown_") for key in [keys[n] for n in range(14)])
    # the old numbering does not leak through
    assert keys[0] != "standby" and keys[3] != "charging"


def test_unknown_label_becomes_unknown_n_and_is_reported() -> None:
    def edit(data: dict[str, Any]) -> None:
        prop_of(data, "work_status")["dataType"]["specs"]["list"].append(
            {"value": "20", "desc": "新状态"}
        )
        prop_of(data, "wifi_rssi_sdj")["dataType"]["specs"]["list"][2]["desc"] = "未知等级"
        errcode = prop_of(data, "errorinfo")["dataType"]["specs"][0]
        errcode["dataType"]["specs"]["list"][0]["desc"] = "新错误"  # value 1

    model = build("l11a_model.json", edit)
    assert model.work_status_keys()[20] == "unknown_20"
    assert model.wifi_keys()[2] == "unknown_2"
    assert model.error_keys()[1] == "unknown_1"
    report = support.support_report(model)
    assert report["unknown_enum_labels"] == {
        "work_status": {20: "新状态"},
        "wifi_rssi_sdj": {2: "未知等级"},
        "errorinfo.errcode": {1: "新错误"},
    }
    assert report["missing_core"] == []
    assert support.needs_notice(report)


def test_l11a_undeclared_values_come_from_the_numeric_table() -> None:
    keys = l11a().work_status_keys()
    assert sorted(keys) == list(range(20)) and keys == const.WORK_STATUS_KEYS


def test_labels_in_another_language_fall_back_to_the_numeric_tables() -> None:
    english = ["Standby", "Paused", "Returning", "Charging", "Relocating", "Remote", "Error",
               "Room clean", "Zone clean", "Spot clean", "Go to", "Emptying", "Whole house", "Return paused"]

    def edit(data: dict[str, Any]) -> None:
        for item, label in zip(prop_of(data, "work_status")["dataType"]["specs"]["list"], english):
            item["desc"] = label
        for item in prop_of(data, "wifi_rssi_sdj")["dataType"]["specs"]["list"]:
            item["desc"] = "Level " + item["value"]

    model = build("l11a_model.json", edit)
    assert model.work_status_keys() == const.WORK_STATUS_KEYS
    assert model.wifi_keys() == const.WIFI_KEYS
    report = support.support_report(model, "L11-A")
    assert report["unknown_enum_labels"]["work_status"][0] == "Standby"
    assert len(report["unknown_enum_labels"]["work_status"]) == 14
    assert support.needs_notice(report)

    def charging(data: dict[str, Any]) -> None:
        for item in prop_of(data, "power_status")["dataType"]["specs"]["list"]:
            item["desc"] = {"0": "Normal", "1": "Charging", "2": "Fault"}[item["value"]]

    other = build("rv1promax_model.json", charging)
    assert other.is_charging(1) is True and other.is_charging(0) is False and other.is_charging(2) is False


def test_undeclared_values_are_filled_only_when_the_declared_labels_agree() -> None:
    def edit(data: dict[str, Any]) -> None:
        items = prop_of(data, "work_status")["dataType"]["specs"]["list"]
        items[:] = [i for i in items if i["value"] in ("0", "1", "2")]
        items[2]["value"] = "3"  # returning declared as 3: the table says 3 is charging

    keys = build("l11a_model.json", edit).work_status_keys()
    assert keys[0] == "standby" and keys[1] == "paused" and keys[3] == "returning"
    assert keys[7] == "unknown_7" and keys[7] != "room_clean"
    assert keys[2] == "unknown_2" and keys[19] == "unknown_19"

    def agree(data: dict[str, Any]) -> None:
        items = prop_of(data, "work_status")["dataType"]["specs"]["list"]
        items[:] = [i for i in items if i["value"] in ("0", "1", "3")]

    keys = build("l11a_model.json", agree).work_status_keys()
    assert keys[7] == "room_clean" and keys[2] == "returning" and keys[19] == "return_to_wash_paused"


def test_model_without_labels_uses_the_numeric_tables() -> None:
    def edit(data: dict[str, Any]) -> None:
        for identifier in ("work_status", "wifi_rssi_sdj"):
            prop_of(data, identifier)["dataType"]["specs"] = {}

    model = build("l11a_model.json", edit)
    assert model.work_status_keys() == const.WORK_STATUS_KEYS
    assert model.wifi_keys() == const.WIFI_KEYS
    assert support.support_report(model)["unknown_enum_labels"] == {}


def test_support_report_of_the_known_models() -> None:
    for name, device_model, verified, partly in (
        ("l11a_model.json", "L11-A", True, False),
        ("rv1promax_model.json", "RV1 Pro Max", False, True),
    ):
        report = support.support_report(build(name), device_model)
        assert report["missing_core"] == [], (name, report)
        assert report["unknown_enum_labels"] == {}, (name, report)
        assert not support.needs_notice(report)
        assert (report["verified_model"], report["partly_verified"]) == (verified, partly)
        assert all(report["capabilities"][k] for k in ("start", "pause", "stop", "return_to_dock", "battery", "status", "suction", "room_cleaning"))
    assert support.support_report(l11a(), "Imou RV-L11-A")["verified_model"]
    assert not support.support_report(l11a(), "L11-AB")["verified_model"]
    for name in ("L11-A", "RV-L11-A", "rv l11-a", "Imou L11_A", "IMOU RV-L11-A", " l11a "):
        assert support.model_in(name, support.VERIFIED_MODELS), name
    for name in ("L11-A Pro", "L11-AB", "XL11-A", "RV-L11-A Pro", "Imou", "", None):
        assert not support.model_in(name, support.VERIFIED_MODELS), name
    assert support.model_in("RV1 Pro Max", support.PARTLY_VERIFIED_MODELS)
    assert support.model_in("Imou RV1-Pro-Max", support.PARTLY_VERIFIED_MODELS)
    assert not support.model_in("RV1 Pro Max Plus", support.PARTLY_VERIFIED_MODELS)
    assert not support.support_report(l11a(), "L11-A Pro")["verified_model"]
    assert support.support_report(rv1(), "RV1 Pro Max")["partly_verified"]
    assert not support.support_report(l11a())["verified_model"]
    assert support.VERIFIED_MODELS == {"L11-A"} and support.PARTLY_VERIFIED_MODELS == {"RV1 Pro Max"}


def test_missing_core_functions_are_listed() -> None:
    def edit(data: dict[str, Any]) -> None:
        data["properties"] = [
            p for p in data["properties"] if p["identifier"] not in ("back_charging", "back_station", "power")
        ]
        for service in data["services"]:
            if service["identifier"] == "clean_control":
                service["inputData"] = [i for i in service["inputData"] if i["identifier"] != "status"]

    report = support.support_report(build("l11a_model.json", edit), "X1")
    assert report["missing_core"] == ["start_stop", "return_to_dock", "battery"]
    assert not report["capabilities"]["start"] and not report["capabilities"]["return_to_dock"]
    assert report["capabilities"]["status"] and report["capabilities"]["suction"]
    assert not report["verified_model"] and support.needs_notice(report)


def test_rv1lpro_return_to_dock_counts_swp_backstation() -> None:
    model = build("rv1lpro_model.json")
    report = support.support_report(model, "RV1L Pro")
    assert report["capabilities"]["return_to_dock"] is True
    assert report["missing_core"] == [] and report["unknown_enum_labels"] == {}
    assert not support.needs_notice(report)
    # without any of the three properties the function is missing again
    def edit(data: dict[str, Any]) -> None:
        data["properties"] = [p for p in data["properties"] if p["identifier"] != "swp_backStation"]

    gone = support.support_report(build("rv1lpro_model.json", edit), "RV1L Pro")
    assert gone["missing_core"] == ["return_to_dock"] and support.needs_notice(gone)


def test_is_charging() -> None:
    model = rv1()
    assert model.prop("power_status") is not None and l11a().prop("power_status") is None
    assert model.is_charging(1) is True and model.is_charging(0) is False and model.is_charging(2) is False
    assert model.is_charging(None) is None and model.is_charging(7) is None

    def edit(data: dict[str, Any]) -> None:
        prop_of(data, "power_status")["dataType"]["specs"] = {}

    assert build("rv1promax_model.json", edit).is_charging(1) is True
    assert build("rv1promax_model.json", edit).is_charging(0) is False

    def renumber(data: dict[str, Any]) -> None:
        for item in prop_of(data, "power_status")["dataType"]["specs"]["list"]:
            item["value"] = str({"0": 2, "1": 0, "2": 1}[item["value"]])

    renumbered = build("rv1promax_model.json", renumber)
    assert renumbered.is_charging(0) is True and renumbered.is_charging(1) is False


def test_power_status_is_polled_only_when_present() -> None:
    assert "112200" in rv1().poll_refs() and "112200" not in l11a().poll_refs()


def test_new_model_issue_url() -> None:
    url = support.new_model_issue_url("RV1 Pro Max")
    assert url == (
        "https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=new_model.yml"
        "&title=%5BModel%5D%3A+RV1+Pro+Max&robot_model=RV1+Pro+Max"
    )
    query = parse_qs(urlsplit(url).query)
    assert query["title"] == ["[Model]: RV1 Pro Max"] and query["robot_model"] == ["RV1 Pro Max"]
    assert "robot_model=unknown" in support.new_model_issue_url(None)
    assert "%26%3D" in support.new_model_issue_url("A&=")


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
