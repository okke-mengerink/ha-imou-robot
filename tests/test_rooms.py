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


def test_repeating_schedule_start_is_local_time_of_day() -> None:
    # verified live on an RV-L11-A: period [0], startTime 50400 and timeZoneSec 3600
    # is "Sun 14:00" in the Imou app
    value = [{"unlock": True, "period": [0], "cleanAreas": [1], "startTime": 50400, "endTime": 0}]
    schedule = rooms.parse_schedules(response(value)).schedules[0]
    assert schedule.time_of_day == "14:00" and schedule.days == ["sun"]
    assert schedule.start is None and schedule.period == [0]
    # more days, in the order of the robot; unusable entries are skipped
    value = [{"period": [1, 3, 6, 7, -1, True, "x", "2"], "cleanAreas": [], "startTime": 27000}]
    schedule = rooms.parse_schedules(response(value)).schedules[0]
    assert schedule.time_of_day == "07:30" and schedule.days == ["mon", "wed", "sat", "tue"]
    # a startTime that is not a time of day gives no time of day
    for start in (None, "bad", -1, 86400):
        value = [{"period": [0], "cleanAreas": [], "startTime": start}]
        schedule = rooms.parse_schedules(response(value)).schedules[0]
        assert schedule.time_of_day is None and schedule.days == ["sun"] and schedule.start is None
    # a repeating schedule with an epoch startTime keeps it as start; the days come from period
    value = [{"period": [0, 2], "cleanAreas": [], "startTime": 1794224460}]
    schedule = rooms.parse_schedules(response(value)).schedules[0]
    assert schedule.start == 1794224460 and schedule.time_of_day is None, schedule
    assert schedule.days == ["sun", "tue"] and schedule.period == [0, 2]
    assert rooms.parse_schedules(response([{"period": [0], "startTime": 0}])).schedules[0].time_of_day == "00:00"
    assert rooms.parse_schedules(response([{"period": [0], "startTime": 86399}])).schedules[0].time_of_day == "23:59"


def test_one_off_schedule_keeps_epoch_start() -> None:
    # verified live: 1794224460 with timeZoneSec 3600 is shown as 12:41
    schedule = rooms.parse_schedules(REAL, "2501").schedules[0]
    assert schedule.start == 1794224460 and schedule.time_of_day is None and schedule.days == []
    assert (schedule.start + 3600) % 86400 == 12 * 3600 + 41 * 60


def test_rv1_schedule_time_and_days_match_the_l11a_format() -> None:
    # verified in the app: start_time is LOCAL seconds since midnight (7200 is 02:00,
    # 82800 is 23:00); select_day is 1 = Monday ... 7 = Sunday
    result = rooms.parse_schedules(rv1_response(rv1_item()), None, rv1_members())
    schedule = result.schedules[0]
    assert schedule.start is None and schedule.start_raw == 82800
    assert schedule.time_of_day == "23:00"
    assert schedule.days == ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    assert schedule.period == [7, 1, 2, 3, 4, 5, 6]
    two = rooms.parse_schedules(rv1_response(rv1_item(**{"201669": 7200, "201668": "[1,3]"})), None, rv1_members())
    assert two.schedules[0].time_of_day == "02:00" and two.schedules[0].days == ["mon", "wed"]
    # the same attribute values as an L11-A schedule of that time and days: strings "HH:MM", "mon"...
    l11a = rooms.parse_schedules(response([{"period": [1, 3], "cleanAreas": [], "startTime": 7200}])).schedules[0]
    assert (l11a.time_of_day, l11a.days) == (two.schedules[0].time_of_day, two.schedules[0].days)
    # a list instead of text, out-of-range days are skipped, 0 is not a day
    odd = rooms.parse_schedules(rv1_response(rv1_item(**{"201668": [0, 8, 2, True, "x", 5], "201669": 86399})), None, rv1_members())
    assert odd.schedules[0].days == ["tue", "fri"] and odd.schedules[0].time_of_day == "23:59"
    # no days (a one-off schedule): a time, no days
    once = rooms.parse_schedules(rv1_response(rv1_item(**{"201668": "[]", "201669": 0})), None, rv1_members())
    assert once.schedules[0].time_of_day == "00:00" and once.schedules[0].days == []
    # a start_time that is no time of day gives no time
    for start in (None, "bad", -1, 86400, 1794224460):
        item = rv1_item(**{"201669": start})
        if start is None:
            del item["201669"]
        schedule = rooms.parse_schedules(rv1_response(item), None, rv1_members()).schedules[0]
        assert schedule.time_of_day is None, start


def test_sequence_room_ids() -> None:
    assert rooms.sequence_room_ids([-128, -126, -127, -124]) == [-128, -126, -127, -124]
    assert rooms.sequence_room_ids([]) == [] and rooms.sequence_room_ids(None) == []
    assert rooms.sequence_room_ids("[-128,-127]") == [-128, -127]
    assert rooms.sequence_room_ids("1, -2") == [1, -2]
    assert rooms.sequence_room_ids([1, "x", None, True, 2.0, "3", 1]) == [1, 2, 3]
    assert rooms.sequence_room_ids({"a": 1}) == []
    assert rooms.sequence_room_ids([-128, 0, -127, 0, 0]) == [-128, -127]  # zero padding
    assert rooms.sequence_room_ids([0, 0]) == [] and rooms.sequence_room_ids("0,0,3") == [3]


def test_known_rooms_include_the_cleaning_order() -> None:
    # verified on an RV1 Pro Max: the schedule has [-128, -127], the sequence the 4 rooms
    schedule = rooms.parse_schedules(rv1_response(rv1_item(**{"201665": 1, "201666": [{"id": -128, "name": "Kitchen"}, {"id": -127}]})), None, rv1_members())
    assert rooms.known_rooms(schedule) == {-128: "Kitchen", -127: None}
    both = rooms.known_rooms(schedule, [-128, -126, -127, -124])
    assert both == {-128: "Kitchen", -127: None, -126: None, -124: None}
    assert list(both) == [-128, -127, -126, -124]  # ascending
    # without schedules the sequence alone gives the rooms (no names)
    assert rooms.known_rooms(None, [-127, -128]) == {-128: None, -127: None}
    assert rooms.known_rooms(rooms.ScheduleList(None, []), []) == {}
    assert rooms.known_rooms(None) == {}
    # the named rooms are added on top
    assert rooms.remembered_rooms(rooms.known_rooms(None, [-127]), {-5: "Mine", -127: "Hall"}) == {-127: None, -5: None}


def test_negative_room_ids_everywhere() -> None:
    assert _default(-127) == "Room -127"
    known = {-128: None, -127: "Kantoor", 3: None}
    names = rooms.segment_names(known, known, {-128: "Mine"}, _default)
    assert names == {-128: "Mine", -127: "Kantoor", 3: "Room 3"}
    same = rooms.segment_names({-1: None, 1: None}, {-1: "Wc", 1: "Wc"}, {}, _default)
    assert same == {-1: "Wc (-1)", 1: "Wc (1)"}
    assert rooms.room_button_name("Room -127", "Room -127") == "room -127"
    labels = rooms.room_area_labels({-127: None, -126: "Kantoor"}, {}, _default)
    assert list(labels.items()) == [("Room -127", -127), ("Room -126: Kantoor", -126)]
    assert rooms.parse_room_names("-127 = Hall\n3 = Office") == {-127: "Hall", 3: "Office"}
    assert rooms.format_room_names({3: "Office", -127: "Hall"}) == "-127 = Hall\n3 = Office"
    assert rooms.partition_area([-128, -127]) == "[-128,-127]"
    assert rooms.areas_by_room({"hall": ["-127", "3"]}, [-127, 3]) == {-127: "hall", 3: "hall"}
    new = rooms.new_area_mapping({}, {-127: "hall", -128: "hall"})
    assert new == {"hall": ["-128", "-127"]}
    last = rooms.new_last_seen_segments([], {-127: "Hall"})
    assert last == [{"id": "-127", "name": "Hall", "group": None}]


def _default(room_id: int) -> str:
    return f"Room {room_id}"


def test_room_area_labels() -> None:
    known = {2: "Kantoor", 3: None, 4: "Slaapkamer", 10: "Room 10"}
    labels = rooms.room_area_labels(known, {4: "Logeerkamer"}, _default)
    assert list(labels.items()) == [("Room 2: Kantoor", 2), ("Room 3", 3), ("Room 4: Logeerkamer", 4), ("Room 10", 10)], labels
    # the same label name for two rooms still gives two labels (the number is in it)
    both = rooms.room_area_labels({1: "Wc", 2: "Wc"}, {}, _default)
    assert list(both) == ["Room 1: Wc", "Room 2: Wc"]
    assert rooms.room_area_labels({}, {}, _default) == {}


def test_remembered_rooms() -> None:
    assert rooms.remembered_rooms({4: "Wc"}, {1: "A", 2: "B", 4: "Mine"}) == {1: None, 2: None, 4: "Wc"}
    assert rooms.remembered_rooms({}, {}) == {} and rooms.remembered_rooms({3: None, 1: "K"}, {}) == {1: "K", 3: None}


def test_room_button_name() -> None:
    assert rooms.room_button_name("Room 3", "Room 3") == "room 3"
    assert rooms.room_button_name("Kantoor", "Room 3") == "Kantoor"


def test_areas_by_room() -> None:
    mapping = {"kitchen": ["2", "3"], "office": ["3", "10"], "gone": ["4"], "bad": "x"}
    assert rooms.areas_by_room(mapping, [2, 3, 4, 5]) == {2: "kitchen", 3: "kitchen", 4: "gone"}
    assert rooms.areas_by_room(mapping, [2, 3, 10], {"office", "kitchen"}) == {2: "kitchen", 3: "kitchen", 10: "office"}
    assert rooms.areas_by_room(None, [2]) == {} and rooms.areas_by_room({"a": None}, [2]) == {}


def test_new_area_mapping() -> None:
    old = {"kitchen": ["2", "3"], "office": ["10", "4"], "attic": ["7"]}
    # room 2 moves to office, 3 loses its area, 4 gets the kitchen; 7 and 10 are not shown and stay
    new = rooms.new_area_mapping(old, {2: "office", 3: None, 4: "kitchen"})
    assert new == {"kitchen": ["4"], "office": ["2", "10"], "attic": ["7"]}, new
    assert list(new["office"]) == ["2", "10"]  # numeric ascending, not "10" < "2"
    # an area without segments is dropped; the input is not changed
    assert rooms.new_area_mapping({"a": ["1"]}, {1: None}) == {}
    assert old == {"kitchen": ["2", "3"], "office": ["10", "4"], "attic": ["7"]}
    # no duplicates, integers in the old data become strings
    assert rooms.new_area_mapping({"a": [1, "1", 5]}, {2: "a", 5: None}) == {"a": ["1", "2"]}
    assert rooms.new_area_mapping(None, {2: "a"}) == {"a": ["2"]} and rooms.new_area_mapping("x", {}) == {}


def test_new_area_mapping_keeps_extra_links_when_unchanged() -> None:
    old = {"kitchen": ["2", "3"], "office": ["3"], "attic": ["7"]}
    # room 3 is in kitchen and office; the form shows kitchen (first by id is "kitchen")
    assert rooms.areas_by_room(old, [3]) == {3: "kitchen"}
    assert rooms.new_area_mapping(old, {3: "kitchen"}) == {"kitchen": ["2", "3"], "office": ["3"], "attic": ["7"]}
    # a link to an area that does not exist stays when the shown area is unchanged
    assert rooms.new_area_mapping(old, {3: "office"}, {"office"}) == old
    # changed: removed from every area, added only to the new one
    assert rooms.new_area_mapping(old, {3: "attic"}) == {"kitchen": ["2"], "attic": ["3", "7"]}
    # cleared: removed from every area
    assert rooms.new_area_mapping(old, {3: None}) == {"kitchen": ["2"], "attic": ["7"]}
    assert old == {"kitchen": ["2", "3"], "office": ["3"], "attic": ["7"]}


def test_new_last_seen_segments() -> None:
    old = [{"id": "2", "name": "Old", "group": None}, {"id": "9", "name": "Gone", "group": "g"}, "junk", {"name": "no id"}]
    new = rooms.new_last_seen_segments(old, {2: "Kantoor", 4: "Wc", 3: "Kast"})
    assert new == [
        {"id": "2", "name": "Kantoor", "group": None},
        {"id": "9", "name": "Gone", "group": "g"},
        {"id": "3", "name": "Kast", "group": None},
        {"id": "4", "name": "Wc", "group": None},
    ], new
    assert rooms.new_last_seen_segments(None, {1: "A"}) == [{"id": "1", "name": "A", "group": None}]
    assert old[0]["name"] == "Old"  # not changed in place
    # extra keys of an entry stay, a repeated id is dropped
    assert rooms.new_last_seen_segments([{"id": "1", "name": "x", "extra": 1}, {"id": 1, "name": "y"}], {}) == [{"id": "1", "name": "x", "extra": 1}]


# -- rooms from the properties of an RV1 model (synthetic data built from the definitions) --
AREA_REFS = {"mapid": "147401", "id": "147402", "clean_direction": "147403"}
SEQ_REFS = {"mapid": "146001", "list": "146002"}
MAP_REFS = {"map_id": "122301", "name": "122302"}


def test_list_room_ids() -> None:
    assert rooms.list_room_ids("[-128,-127]") == [-128, -127]
    assert rooms.list_room_ids(" [ -128, -127 , 3 ] ") == [-128, -127, 3]
    assert rooms.list_room_ids("-128,-127") == [-128, -127]  # comma-separated
    assert rooms.list_room_ids("[-128,-127") == [-128, -127]  # broken JSON, comma-separated
    assert rooms.list_room_ids('[{"id":-128,"name":"a"},{"id":5},{"x":1},"y"]') == [-128, 5]  # objects
    assert rooms.list_room_ids("[3,3,0,-3,true,false,null,1.5,\"7\",\"x\",[1]]") == [3, -3, 7]  # ints only; no 0, no bools
    assert rooms.list_room_ids([3, "4", 0, True, 2.0, None, {"id": 9}]) == [3, 4, 2, 9]
    assert rooms.list_room_ids("5") == [5] and rooms.list_room_ids("0") == []
    for odd in ("", "  ", "[]", "{}", "null", "abc", "[[1,2]]", None, True, 0, object(), b"1", {"id": "x"}):
        assert rooms.list_room_ids(odd) == [], odd


def test_valid_map_id() -> None:
    assert rooms.valid_map_id(201661) == 201661 and rooms.valid_map_id("1001") == 1001
    for bad in (None, -1, 0, -100, True, False, "x", "", 1.5, [], {}):
        assert rooms.valid_map_id(bad) is None, bad


def test_room_entries_by_ref_and_by_identifier() -> None:
    by_ref = [{"147401": 1001, "147402": -128, "147403": 0}, {"147401": 1001, "147402": -127}]
    assert rooms.room_entries(by_ref, AREA_REFS, "id") == [(1001, (-128,)), (1001, (-127,))]
    by_name = [{"mapId": 7, "id": -3}, {"MAPID": 7, "ID": 4}, {"mapid": 7, "id": "5"}]
    assert rooms.room_entries(by_name, AREA_REFS, "id") == [(7, (-3,)), (7, (4,)), (7, (5,))]
    assert rooms.room_entries(by_name, {}, "id") == [(7, (-3,)), (7, (4,)), (7, (5,))]  # no refs known
    # one struct (not a list), and JSON text of the items
    assert rooms.room_entries({"mapid": 7, "id": 2}, AREA_REFS, "id") == [(7, (2,))]
    assert rooms.room_entries('[{"147401": 7, "147402": 2}]', AREA_REFS, "id") == [(7, (2,))]
    # an item without map id, with an unusable map id (-1, 0, text), and without a usable room
    odd = [{"147402": 1}, {"147401": -1, "147402": 2}, {"147401": 0, "147402": 3}, {"147401": "x", "147402": 4},
           {"147401": 7, "147402": 0}, {"147401": 7, "147402": True}, {"147401": 7}, {"147401": 7, "147402": "x"}, "junk", None, 5]
    assert rooms.room_entries(odd, AREA_REFS, "id") == [(None, (1,)), (None, (2,)), (None, (3,)), (None, (4,))]
    for value in (None, [], {}, "", "junk", "[1", 5, True, object(), [[1]], {"a": {"b": 1}}):
        assert rooms.room_entries(value, AREA_REFS, "id") == [], value


def test_room_entries_of_the_cleaning_order() -> None:
    value = [
        {"146001": 1001, "146002": "[-128,-126,-127]"},
        {"146001": 99, "146002": '[{"id":-5},{"id":0}]'},
        {"146001": 1001, "146002": "-124,-128,0"},
        {"146001": 1001, "146002": "[]"},
        {"146001": 1001},
    ]
    assert rooms.room_entries(value, SEQ_REFS, list_member="list") == [
        (1001, (-128, -126, -127)), (99, (-5,)), (1001, (-124, -128)),
    ]
    # sweeper_sequence is a struct; the model has parsed it by identifier
    assert rooms.room_entries({"mapid": 5, "list": "[1,2]"}, {"mapid": "160701", "list": "160702"}, list_member="list") == [(5, (1, 2))]
    assert rooms.room_entries({"160701": 5, "160702": "[1,2]"}, {"mapid": "160701", "list": "160702"}, list_member="list") == [(5, (1, 2))]


def test_map_ids() -> None:
    assert rooms.map_ids([{"122302": "Map A", "122301": 1001}], MAP_REFS) == [1001]
    assert rooms.map_ids([{"map_id": 1}, {"map_id": 2}, {"map_id": 2}, {"map_id": -1}, {"name": "x"}], MAP_REFS) == [1, 2]
    assert rooms.map_ids(None, MAP_REFS) == [] and rooms.map_ids([], MAP_REFS) == [] and rooms.map_ids("junk", MAP_REFS) == []


def test_current_map_id() -> None:
    # (a) the one usable map id of the schedules; -1 (whole house) and junk are ignored
    assert rooms.current_map_id([201661, -1, None, 201661], []) == 201661
    assert rooms.current_map_id([-1, None], [5]) == 5  # (b) no usable schedule map: map_name has one map
    assert rooms.current_map_id([201661], [201661, 6]) == 201661  # (a) wins over (b)
    assert rooms.current_map_id([201661], []) == 201661  # no map_name: the schedule is all there is
    assert rooms.current_map_id([], [5, 5]) == 5
    assert rooms.current_map_id([-1, 0, "x", True], [5, 6]) is None  # (c) no filter
    assert rooms.current_map_id([], []) is None
    assert rooms.current_map_id([1, 2], []) is None  # several maps in the schedules tell nothing
    assert rooms.current_map_id([1, 2], [3]) == 3


def test_current_map_id_string_and_unlisted_schedule_map() -> None:
    # ids are compared as ints
    assert rooms.current_map_id(["1001"], [1001, 1002]) == 1001
    assert rooms.current_map_id([1001], ["1001"]) == 1001
    assert rooms.current_map_id([], ["1001", 1001]) == 1001
    # the schedule's map id is not among the listed maps: the one listed map wins
    assert rooms.current_map_id([5], [1001]) == 1001
    # ... and with several listed maps the unlisted schedule id tells nothing
    assert rooms.current_map_id([5], [1001, 1002]) is None


def test_rooms_on_map() -> None:
    entries = [(1, (-128, -127)), (2, (-5,)), (None, (9,)), (1, (-127, 4)), (2, (4,))]
    assert rooms.rooms_on_map(entries, 1) == [-128, -127, 9, 4]  # other maps are dropped, no map id is kept
    assert rooms.rooms_on_map(entries, 2) == [-5, 9, 4]
    assert rooms.rooms_on_map(entries, "2") == [-5, 9, 4]  # ids are compared as ints
    assert rooms.rooms_on_map([], 1) == []
    assert rooms.rooms_on_map([], None) == []


def test_rooms_on_map_several_maps_without_a_known_map() -> None:
    entries = [(1, (-128, -127)), (2, (-5,)), (None, (9,)), (1, (-127, 4)), (2, (4,))]
    # unknown current map and two distinct map ids: the entries with a map id are dropped
    assert rooms.rooms_on_map(entries, None) == [9]
    # a known map that no entry carries: the filter is ignored, then the same rule
    assert rooms.rooms_on_map(entries, 3) == [9]
    assert rooms.rooms_on_map(entries, 3) == rooms.rooms_on_map(entries, None)
    # a single map (or none): nothing is dropped
    assert rooms.rooms_on_map([(1, (-128,)), (1, (-127,)), (None, (9,))], None) == [-128, -127, 9]
    assert rooms.rooms_on_map([(1, (-128,)), (1, (-127,))], 7) == [-128, -127]  # unknown id, one map
    assert rooms.rooms_on_map([(None, (-128,)), (None, (-127,))], None) == [-128, -127]


def test_rooms_from_the_definitions_end_to_end() -> None:
    """Seven rooms on the single map of ``map_name`` plus one room of another map."""
    lpro = model.ThingsModel.from_api(fixture("rv1lpro_model.json"))
    prop = lpro.prop("swp_allareaconfig")
    values = lpro.parse_values(
        {
            "147400": [{"147401": 11, "147402": room, "147403": 0} for room in (-128, -127, -126, -125, -124, -123, -122)]
            + [{"147401": 22, "147402": -50, "147403": 1}],
            "122300": [{"122301": 11, "122302": "Map"}],
        }
    )
    entries = rooms.room_entries(values["swp_allareaconfig"], prop.member_refs, "id")
    listed = rooms.map_ids(values["map_name"], lpro.prop("map_name").member_refs, "map_id")
    current = rooms.current_map_id([], listed)
    assert current == 11
    assert rooms.rooms_on_map(entries, current) == [-128, -127, -126, -125, -124, -123, -122]
    names = rooms.segment_names(*(rooms.known_rooms(None, rooms.rooms_on_map(entries, current)),) * 2, {}, lambda i: f"Room {i}")
    assert names[-128] == "Room -128" and len(names) == 7


def test_sweeper_sequence_shapes_reach_the_room_parser() -> None:
    """The path of the coordinator: parse_values, then room_entries with the model's refs."""
    lpro = model.ThingsModel.from_api(fixture("rv1lpro_model.json"))
    prop = lpro.prop("sweeper_sequence")
    assert prop.children, "sweeper_sequence is a struct"
    refs = prop.member_refs

    def entries(raw: object) -> list:
        values = lpro.parse_values({prop.ref: raw})
        assert "sweeper_sequence" in values, raw
        return rooms.room_entries(values["sweeper_sequence"], refs, None, "list", const.MEMBER_MAP_ID)

    expected = [(5, (1, 2))]
    assert entries({"160701": 5, "160702": "[1,2]"}) == expected  # keyed by ref
    assert entries({"mapid": 5, "list": "[1,2]"}) == expected  # keyed by identifier
    assert entries({"mapId": 5, "List": [1, 2]}) == expected  # identifier in another case
    assert entries([{"160701": 5, "160702": "[1,2]"}]) == expected  # a list of structs
    assert entries('{"160701": 5, "160702": "[1,2]"}') == expected  # JSON text
    assert entries('[{"160701": 5, "160702": [1, 2]}]') == expected  # JSON text of a list
    for odd in (None, 5, "junk", []):
        assert entries(odd) == [], odd


def test_sources_of_the_models() -> None:
    """Every source of const.ROOM_SOURCES has the members it names in both RV1 models."""
    for name in ("rv1lpro_model.json", "rv1promax_model.json"):
        found = model.ThingsModel.from_api(fixture(name))
        for identifier, room_member, list_member in const.ROOM_SOURCES:
            prop = found.prop(identifier)
            if prop is None:
                continue
            refs = prop.member_refs
            assert const.MEMBER_MAP_ID in refs and (room_member or list_member) in refs, (name, identifier)
    assert model.ThingsModel.from_api(fixture("l11a_model.json")).prop("swp_allareaconfig") is None


if __name__ == "__main__":
    sys.exit(run_all(globals()))
