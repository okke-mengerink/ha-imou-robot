"""Plain-Python tests: schedules, room names and the room-clean input.

Run: python3 -I tests/test_rooms.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import fixture, load, run_all  # noqa: E402

const = load("const")
model = load("model")
rooms = load("rooms")


def response(value: object, key: str = "2501", extra: dict | None = None) -> dict:
    """A clean_list response with one timer output."""
    entry = {"timeZone": 1, "timeZoneSec": 3600, "value": value, **(extra or {})}
    return {"content": {"outputData": {key: entry}, "service": "2500"}}


# the real sample of the RV-L11-A: 7 room ids, only 6 labels (so not aligned)
REAL = response(
    [
        {
            "period": [],
            "unlock": False,
            "active": True,
            "cleanAreas": [2, 8, 4, 5, 6, 1, 3],
            "startTime": 1794224460,
            "endTime": 0,
            "segmentTagIds": ["Kantoor", "Slaapkamer", "Kast", "Wc", "Badkamer", "Slaapkamer"],
        }
    ]
)
SINGLE = response(
    [{"period": [], "unlock": True, "active": True, "cleanAreas": [2], "startTime": 1, "endTime": 0, "segmentTagIds": ["Kantoor"]}]
)


def test_constants() -> None:
    assert const.INPUT_PARTITION_AREA == "partition_area"
    assert const.OUTPUT_TIMER == "timer"
    assert const.CLEAN_MODE_ROOMS == 1
    assert const.SCHEDULE_REFRESH_SECONDS == 3600
    assert const.CONF_ROOM_NAMES == "room_names"
    assert const.FALLBACK_SERVICE_REFS[const.SERVICE_CLEAN_LIST] == "2500"
    assert const.FALLBACK_INPUT_REFS[const.SERVICE_CLEAN_CONTROL][const.INPUT_PARTITION_AREA] == "2704"
    assert const.FALLBACK_OUTPUT_REFS[const.SERVICE_CLEAN_LIST][const.OUTPUT_TIMER] == "2501"


def test_model_parses_service_outputs() -> None:
    things = model.ThingsModel.from_api(fixture("l11a_model.json"))
    clean_list = things.service("clean_list")
    assert clean_list is not None and clean_list.ref == "2500"
    assert clean_list.outputs == {"timer": "2501"} and clean_list.output_ref("TIMER") == "2501"
    control = things.service("clean_control")
    assert control.output_ref("code") == "2706" and control.input_ref("partition_area") == "2704"
    # a model that leaves out the refs falls back to the documented ones
    bare = model.ThingsModel.from_api(
        {"services": [{"identifier": "clean_list", "outputData": [{"identifier": "timer"}], "inputData": []}]}
    )
    assert bare.service("clean_list").ref == "2500" and bare.service("clean_list").output_ref("timer") == "2501"


def test_parse_real_schedule() -> None:
    result = rooms.parse_schedules(REAL, "2501")
    assert result.tz_offset == 3600 and len(result.schedules) == 1
    schedule = result.schedules[0]
    assert schedule.enabled is False and schedule.active is True
    assert schedule.start == 1794224460 and schedule.end is None and schedule.period == []  # endTime 0 = no end
    assert schedule.aligned is False
    assert schedule.room_ids == [2, 8, 4, 5, 6, 1, 3] and len(schedule.tags) == 6
    # 7 ids, 6 labels: not aligned, so no names, but every id is known (ascending)
    assert rooms.known_rooms(result) == {1: None, 2: None, 3: None, 4: None, 5: None, 6: None, 8: None}
    assert list(rooms.known_rooms(result)) == [1, 2, 3, 4, 5, 6, 8]


def test_output_lookup() -> None:
    assert rooms.parse_schedules(SINGLE) is not None  # no ref: the first output with a value list
    assert rooms.parse_schedules(SINGLE, "2501") is not None
    assert rooms.parse_schedules(SINGLE, "9999") is not None  # unknown ref: falls back to the first
    two = {"content": {"outputData": {"1": {"x": 1}, "2": {"value": []}, "3": {"value": [{"cleanAreas": [1]}]}}}}
    assert rooms.parse_schedules(two, "3").schedules[0].room_ids == [1]
    assert rooms.parse_schedules(two).schedules == []  # the first with a value list is "2"
    for bad in (None, [], "x", {}, {"content": None}, {"content": {"outputData": []}}, {"content": {"outputData": {"1": {"value": "x"}}}}):
        assert rooms.parse_schedules(bad) is None, bad


def test_single_room_schedule_is_named() -> None:
    result = rooms.parse_schedules(SINGLE)
    assert result.schedules[0].enabled is True
    assert rooms.known_rooms(result) == {2: "Kantoor"}


def test_equal_length_multi_room() -> None:
    value = [{"unlock": True, "cleanAreas": [4, 3], "segmentTagIds": ["Wc", "Badkamer"]}]
    assert rooms.known_rooms(rooms.parse_schedules(response(value))) == {3: "Badkamer", 4: "Wc"}


def test_first_exact_name_wins_and_ids_are_merged() -> None:
    value = [
        {"cleanAreas": [1, 2, 3], "segmentTagIds": ["A", "B"]},  # not aligned
        {"cleanAreas": [2], "segmentTagIds": ["First"]},
        {"cleanAreas": [2, 5], "segmentTagIds": ["Second", "Five"]},
    ]
    assert rooms.known_rooms(rooms.parse_schedules(response(value))) == {1: None, 2: "First", 3: None, 5: "Five"}


def test_alignment_needs_equal_raw_lengths_and_usable_entries() -> None:
    def known(areas: object, tags: object) -> dict:
        return rooms.known_rooms(rooms.parse_schedules(response([{"cleanAreas": areas, "segmentTagIds": tags}])))

    # the tag list has an unusable entry and a different length: no names
    assert known([7, 8], ["A", None, "B"]) == {7: None, 8: None}
    # an unusable id: the usable ids stay known, but their labels are not used
    assert known([3, None, 4], ["A", "B"]) == {3: None, 4: None}
    # equal raw length, but an unusable id or tag: dropping would shift the pairs
    assert known([3, None, 4], ["A", "B", "C"]) == {3: None, 4: None}
    assert known([3, 4], ["A", None]) == {3: None, 4: None}
    assert known([3, 4], ["A", ""]) == {3: None, 4: None}
    assert known([3, 4], ["A", "  "]) == {3: None, 4: None}
    assert known([3, True], ["A", "B"]) == {3: None}
    assert known([3, 4], "AB") == {3: None, 4: None}
    # a clean equal-length case still pairs
    assert known([4, 3], [" Wc ", "Badkamer"]) == {3: "Badkamer", 4: "Wc"}
    schedules = rooms.parse_schedules(response([{"cleanAreas": [3, None, 4], "segmentTagIds": ["A", "B"]}]))
    assert schedules.schedules[0].room_ids == [3, 4] and schedules.schedules[0].aligned is False
    # an unaligned schedule does not block a later aligned one from naming a room
    value = [{"cleanAreas": [1, 2], "segmentTagIds": ["X"]}, {"cleanAreas": [2], "segmentTagIds": ["Y"]}]
    assert rooms.known_rooms(rooms.parse_schedules(response(value))) == {1: None, 2: "Y"}


def test_implausible_timestamps_are_none() -> None:
    def times(start: object, end: object = 0) -> tuple:
        schedule = rooms.parse_schedules(response([{"cleanAreas": [1], "startTime": start, "endTime": end}])).schedules[0]
        return schedule.start, schedule.end

    assert times(1794224460, 1794228060) == (1794224460, 1794228060)
    assert times(1794224460000) == (None, None)  # milliseconds
    assert times(-5, -1) == (None, None)
    assert times(946684799, 4102444801) == (None, None)
    assert times(946684800, 4102444800) == (946684800, 4102444800)
    assert times("1794224460") == (1794224460, None)
    assert times(None, "bad") == (None, None)


def test_whole_house_and_missing() -> None:
    result = rooms.parse_schedules(response([{"unlock": True, "cleanAreas": [], "startTime": 5}]))
    assert result.schedules[0].room_ids == [] and result.schedules[0].tags == []
    assert rooms.known_rooms(result) == {}
    assert rooms.known_rooms(None) == {}
    assert rooms.parse_schedules(response([])).schedules == []


def test_odd_types() -> None:
    value = [
        "junk",
        None,
        7,
        {
            "unlock": "0",
            "active": "yes?",
            "cleanAreas": [1, "2", 3.0, None, "x", True, [4]],
            "segmentTagIds": "not a list",
            "startTime": "1794224460",
            "endTime": "bad",
            "period": "weekly",
        },
        {"cleanAreas": "all"},
    ]
    result = rooms.parse_schedules(response(value, extra={"timeZoneSec": "7200"}))
    assert result.tz_offset == 7200 and len(result.schedules) == 2
    first, second = result.schedules
    assert first.enabled is False and first.active == "yes?"
    assert first.room_ids == [1, 2, 3] and first.tags == [] and first.period == []
    assert first.start == 1794224460 and first.end is None
    # nothing says whether it is on: it is treated as enabled
    assert second.enabled is True and second.room_ids == [] and second.start is None
    assert rooms.parse_schedules(response([], extra={"timeZoneSec": None})).tz_offset is None


def test_repeat_period_is_kept_raw() -> None:
    value = [{"unlock": True, "period": [1, 3, 5], "cleanAreas": [1]}]
    assert rooms.parse_schedules(response(value)).schedules[0].period == [1, 3, 5]


def test_room_names_round_trip() -> None:
    text = "3 = Blauwe kamer\n\n  1=Kantoor  \n2 = A = B\n"
    parsed = rooms.parse_room_names(text)
    assert parsed == {3: "Blauwe kamer", 1: "Kantoor", 2: "A = B"}
    formatted = rooms.format_room_names(parsed)
    assert formatted == "1 = Kantoor\n2 = A = B\n3 = Blauwe kamer"
    assert rooms.parse_room_names(formatted) == parsed
    assert rooms.parse_room_names("") == {} and rooms.parse_room_names("\n \n") == {}
    assert rooms.format_room_names({}) == ""


def test_room_names_errors() -> None:
    cases = {"x": 1, "1 = a\nno separator": 2, "1 = a\n\n= name": 3, "1 =   ": 1, "a = b": 1, "1.5 = a": 1}
    for text, line in cases.items():
        try:
            rooms.parse_room_names(text)
        except ValueError as err:
            assert f"line {line}" in str(err), (text, str(err))
        else:
            raise AssertionError(f"no error for {text!r}")


def test_segment_names() -> None:
    default = lambda room_id: f"Room {room_id}"  # noqa: E731
    names = rooms.segment_names([1, 2, 3], {1: "Kantoor", 2: None, 3: "Kast"}, {3: "Mijn kast"}, default)
    assert names == {1: "Kantoor", 2: "Room 2", 3: "Mijn kast"}
    # the same name twice: both get the id
    dup = rooms.segment_names([1, 2, 3], {1: "Slaapkamer", 2: "Slaapkamer", 3: "Wc"}, {}, default)
    assert dup == {1: "Slaapkamer (1)", 2: "Slaapkamer (2)", 3: "Wc"}
    # an override that collides with a label
    clash = rooms.segment_names([1, 2], {1: "Wc", 2: None}, {2: "Wc"}, default)
    assert clash == {1: "Wc (1)", 2: "Wc (2)"}
    assert rooms.segment_names([], {}, {}, default) == {}


def test_partition_area() -> None:
    assert rooms.partition_area([3, 4]) == "[3,4]"
    assert rooms.partition_area([2]) == "[2]"
    assert rooms.partition_area([]) == "[]"
    assert rooms.partition_area((8, 1)) == "[8,1]"
    assert json.loads(rooms.partition_area([5, 6])) == [5, 6]


# -- RV1 Pro Max: clean_list is an array of structs --------------------------
def rv1_members() -> dict:
    things = model.ThingsModel.from_api(fixture("rv1promax_model.json"))
    return things.service("clean_list").output_members


def rv1_response(item: dict) -> dict:
    return {"content": {"outputData": {"201621": [item]}, "service": "201600"}}


def rv1_item(**changes: object) -> dict:
    item = {"201662": 7, "201661": -1, "201668": "[7,1,2,3,4,5,6]", "201667": 1, "201669": 82800, "201664": 0, "201663": 1, "201665": 0}
    return {**item, **{k: v for k, v in changes.items()}}


def test_model_keeps_array_struct_members() -> None:
    members = rv1_members()
    assert {k: v.ref for k, v in members.items()} == {
        "map_id": "201661", "timerid": "201662", "state": "201663", "control": "201664",
        "clean_type": "201665", "area_array": "201666", "cleaning_times": "201667",
        "select_day": "201668", "start_time": "201669",
    }
    assert members["state"].enum_labels[1] == "开启"
    # the L11-A has no array-of-struct output
    l11a = model.ThingsModel.from_api(fixture("l11a_model.json"))
    assert l11a.service("clean_list").output_members == {}


def test_rv1_fixture_schedule() -> None:
    result = rooms.parse_schedules(fixture("rv1promax_clean_list.json"), None, rv1_members())
    assert result is not None and len(result.schedules) == 1
    schedule = result.schedules[0]
    assert schedule.enabled is True and schedule.once is False
    assert schedule.room_ids == [] and schedule.tags == [] and schedule.aligned is False
    assert schedule.period == [7, 1, 2, 3, 4, 5, 6]
    assert schedule.start_raw == 82800 and schedule.start is None
    assert schedule.timer_id == 1794324046 and schedule.map_id == -1
    assert rooms.known_rooms(result) == {}
    # without the members the format is unknown
    assert rooms.parse_schedules(fixture("rv1promax_clean_list.json")) is None


def test_rv1_room_schedules() -> None:
    members = rv1_members()
    for area in ("[3,5]", [3, 5], "3,5", "3, 5", [{"id": 3}, {"id": 5}], '[{"id":3},{"id":5}]'):
        result = rooms.parse_schedules(rv1_response(rv1_item(**{"201665": 1, "201666": area})), None, members)
        assert result.schedules[0].room_ids == [3, 5], area
        assert rooms.known_rooms(result) == {3: None, 5: None}, area
    named = rooms.parse_schedules(
        rv1_response(rv1_item(**{"201665": 1, "201666": [{"id": 3, "name": "Kitchen"}, {"id": 5}]})), None, members
    )
    assert named.schedules[0].room_ids == [3, 5]
    assert rooms.known_rooms(named) == {3: "Kitchen", 5: None}
    # a whole-house schedule ignores area_array
    whole = rooms.parse_schedules(rv1_response(rv1_item(**{"201666": "[3,5]"})), None, members)
    assert whole.schedules[0].room_ids == []
    # unusable area_array
    bad = rooms.parse_schedules(rv1_response(rv1_item(**{"201665": 1, "201666": "x"})), None, members)
    assert bad.schedules[0].room_ids == []


def test_rv1_state_once_and_days() -> None:
    members = rv1_members()

    def parse(**changes: object):
        return rooms.parse_schedules(rv1_response(rv1_item(**changes)), None, members).schedules[0]

    assert parse(**{"201663": 2}).enabled is False
    assert parse(**{"201663": 0}).enabled is False
    once = parse(**{"201667": 0, "201668": "[]"})
    assert once.once is True and once.period == []
    assert parse(**{"201668": [1, 3]}).period == [1, 3]
    # without labels in the model: state 1 is enabled, clean_type 1 rooms, cleaning_times 0 once
    bare = {k: v.__class__(identifier=v.identifier, ref=v.ref, data_type=v.data_type) for k, v in members.items()}
    item = rv1_item(**{"201663": 1, "201665": 1, "201666": "[2]", "201667": 0})
    schedule = rooms.parse_schedules(rv1_response(item), None, bare).schedules[0]
    assert schedule.enabled and schedule.room_ids == [2] and schedule.once is True
    assert rooms.parse_schedules(rv1_response(rv1_item(**{"201663": 2})), None, bare).schedules[0].enabled is False


def test_l11a_parsing_unchanged_with_members() -> None:
    # passing (empty) members changes nothing for the L11-A format
    assert rooms.parse_schedules(REAL, "2501", {}) == rooms.parse_schedules(REAL, "2501")
    schedule = rooms.parse_schedules(REAL, "2501").schedules[0]
    assert (schedule.once, schedule.start_raw, schedule.timer_id, schedule.map_id, schedule.room_names) == (None, None, None, None, {})


def test_english_labels_decide_by_number() -> None:
    members = rv1_members()

    def english(member: object, labels: dict[int, str]) -> object:
        return member.__class__(identifier=member.identifier, ref=member.ref, data_type=member.data_type, enum_labels=labels)

    eng = dict(members)
    eng["state"] = english(members["state"], {1: "On", 2: "Off"})
    eng["clean_type"] = english(members["clean_type"], {0: "Whole house", 1: "Rooms"})
    eng["cleaning_times"] = english(members["cleaning_times"], {0: "Once", 1: "Repeat"})
    item = rv1_item(**{"201663": 1, "201665": 1, "201666": "[2]", "201667": 0})
    schedule = rooms.parse_schedules(rv1_response(item), None, eng).schedules[0]
    assert schedule.enabled is True and schedule.room_ids == [2] and schedule.once is True
    off = rv1_item(**{"201663": 2, "201665": 0, "201666": "[2]", "201667": 1})
    schedule = rooms.parse_schedules(rv1_response(off), None, eng).schedules[0]
    assert schedule.enabled is False and schedule.room_ids == [] and schedule.once is False
    # known labels still decide by label, not number
    assert rooms.parse_schedules(rv1_response(rv1_item(**{"201663": 2})), None, members).schedules[0].enabled is False


def test_struct_format_is_tried_before_l11a() -> None:
    members = rv1_members()
    wrapped = {"content": {"outputData": {"201621": {"value": [rv1_item()]}}, "service": "201600"}}
    result = rooms.parse_schedules(wrapped, "201621", members)
    schedule = result.schedules[0]
    assert len(result.schedules) == 1 and schedule.timer_id == 7 and schedule.once is False
    assert schedule.start_raw == 82800 and schedule.enabled is True and schedule.period == [7, 1, 2, 3, 4, 5, 6]
    # the L11-A format still works when members are given
    assert rooms.parse_schedules(REAL, "2501", members) == rooms.parse_schedules(REAL, "2501")


def test_struct_item_without_identity_is_skipped() -> None:
    members = rv1_members()
    for junk in ({}, {"foo": 1}, {"201661": -1, "201665": 1}):
        result = rooms.parse_schedules(rv1_response(junk), None, members)
        assert result is not None and result.schedules == [], junk
    result = rooms.parse_schedules(rv1_response({}), None, members)
    assert [s for s in result.schedules if s.enabled] == []
    mixed = {"content": {"outputData": {"201621": [{}, rv1_item()]}}}
    assert len(rooms.parse_schedules(mixed, None, members).schedules) == 1


def test_rooms_schedule_without_usable_rooms_is_unknown() -> None:
    members = rv1_members()

    def parse(**changes: object):
        return rooms.parse_schedules(rv1_response(rv1_item(**changes)), None, members).schedules[0]

    for area in ("[]", "", None, "x", "not a list", []):
        schedule = parse(**{"201665": 1, "201666": area})
        assert schedule.room_ids == [] and schedule.rooms_unknown is True, area
    missing = rv1_item(**{"201665": 1})
    schedule = rooms.parse_schedules(rv1_response(missing), None, members).schedules[0]
    assert schedule.rooms_unknown is True and schedule.room_ids == []
    # whole house is not unknown; with usable rooms nothing is unknown
    assert parse(**{"201665": 0}).rooms_unknown is False
    assert parse(**{"201665": 1, "201666": "[3]"}).rooms_unknown is False
    # clean_type missing but area_array parses: those rooms are used
    item = {k: v for k, v in rv1_item(**{"201666": "[3,5]"}).items() if k != "201665"}
    schedule = rooms.parse_schedules(rv1_response(item), None, members).schedules[0]
    assert schedule.room_ids == [3, 5] and schedule.rooms_unknown is False
    # the L11-A is not affected
    assert rooms.parse_schedules(REAL, "2501").schedules[0].rooms_unknown is False


def test_struct_items_use_the_struct_output() -> None:
    things = model.ThingsModel.from_api(fixture("rv1promax_model.json"))
    service = things.service("clean_list")
    assert service.output_members_ref == "201621"
    assert model.ThingsModel.from_api(fixture("l11a_model.json")).service("clean_list").output_members_ref is None
    members = service.output_members
    other = [{"201662": 99, "201663": 1}]
    both = {"content": {"outputData": {"200001": other, "201621": [rv1_item()]}}}
    result = rooms.parse_schedules(both, None, members, service.output_members_ref)
    assert [s.timer_id for s in result.schedules] == [7]
    both = {"content": {"outputData": {"200001": {"value": other}, "201621": {"value": [rv1_item()]}}}}
    result = rooms.parse_schedules(both, None, members, service.output_members_ref)
    assert [s.timer_id for s in result.schedules] == [7]
    # without a ref: the first list (as before)
    assert [s.timer_id for s in rooms.parse_schedules(both, None, members).schedules] == [99]


if __name__ == "__main__":
    sys.exit(run_all(globals()))
