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


if __name__ == "__main__":
    sys.exit(run_all(globals()))
