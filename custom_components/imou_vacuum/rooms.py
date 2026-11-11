"""Rooms and schedules of the robot.

The robot does not report its rooms or a map. Room ids, and the room TYPE labels
that were chosen in the Imou app, only appear in the cleaning schedules
(``clean_list``). This module parses those schedules and works with room names.

This module is deliberately free of Home Assistant imports so that it can be
unit tested with plain Python.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any

from .model import PropertyDef, ci_get, to_bool, to_int


@dataclass(frozen=True, slots=True)
class Schedule:
    """One cleaning schedule of the robot."""

    enabled: bool  # the schedule's on/off switch ("unlock")
    active: Any  # raw "active" flag; its meaning is unknown
    start: int | None  # unix timestamp in seconds
    end: int | None
    period: list[Any] = field(default_factory=list)  # raw; [] is a one-off schedule
    room_ids: list[int] = field(default_factory=list)  # [] means the whole house
    # Only for the array-of-struct format: a rooms schedule whose rooms are unknown
    # (empty or unusable area_array); it is not a whole-house schedule.
    rooms_unknown: bool = False
    tags: list[str] = field(default_factory=list)  # room TYPE labels from the app
    # True only when the raw ids and labels have the same length and every entry of
    # both is usable; only then do the labels belong to the ids, in order
    aligned: bool = False
    # Only for the array-of-struct format (RV1 Pro Max); None/{} otherwise.
    once: bool | None = None  # True: runs one time, False: repeats
    start_raw: Any = None  # start_time as reported; its meaning/timezone is unknown
    timer_id: int | None = None
    map_id: int | None = None
    room_names: dict[int, str] = field(default_factory=dict)  # exact names, by room id


@dataclass(frozen=True, slots=True)
class ScheduleList:
    """All schedules of a robot, with the robot's UTC offset."""

    tz_offset: int | None  # seconds
    schedules: list[Schedule] = field(default_factory=list)


# start/end times outside 2000-01-01..2100-01-01 (e.g. milliseconds) are not usable
_MIN_TIMESTAMP = 946684800
_MAX_TIMESTAMP = 4102444800


def _timestamp(value: Any) -> int | None:
    """Return a plausible unix timestamp in seconds, else ``None``."""
    number = to_int(value)
    if number is None or not _MIN_TIMESTAMP <= number <= _MAX_TIMESTAMP:
        return None
    return number


def _timer_entry(response: Any, output_ref: str | None) -> Mapping[str, Any] | None:
    """Find the output of ``clean_list`` that holds the schedules."""
    content = ci_get(response, "content")
    outputs = ci_get(content, "outputData")
    if not isinstance(outputs, Mapping):
        return None
    if output_ref is not None:
        entry = outputs.get(str(output_ref))
        if isinstance(entry, Mapping) and isinstance(ci_get(entry, "value"), list):
            return entry
    for entry in outputs.values():
        if isinstance(entry, Mapping) and isinstance(ci_get(entry, "value"), list):
            return entry
    return None


def _parse_schedule(raw: Any) -> Schedule | None:
    if not isinstance(raw, Mapping):
        return None
    enabled = to_bool(ci_get(raw, "unlock"))
    areas = ci_get(raw, "cleanAreas")
    room_ids: list[int] = []
    if isinstance(areas, list):
        for item in areas:
            number = None if isinstance(item, bool) else to_int(item)
            if number is not None:
                room_ids.append(number)
    tags_raw = ci_get(raw, "segmentTagIds")
    tags = (
        [item.strip() for item in tags_raw if isinstance(item, str)]
        if isinstance(tags_raw, list)
        else []
    )
    aligned = (
        isinstance(areas, list)
        and isinstance(tags_raw, list)
        and len(areas) == len(tags_raw) > 0
        and len(room_ids) == len(areas)
        and len(tags) == len(tags_raw)
        and all(tags)
    )
    period = ci_get(raw, "period")
    return Schedule(
        enabled=True if enabled is None else enabled,
        active=ci_get(raw, "active"),
        start=_timestamp(ci_get(raw, "startTime")),
        end=_timestamp(ci_get(raw, "endTime")),
        period=list(period) if isinstance(period, list) else [],
        room_ids=room_ids,
        tags=tags,
        aligned=aligned,
    )


def _label_is(
    member: PropertyDef | None, raw: Any, labels: tuple[str, str], value: int
) -> bool | None:
    """Whether an enum member has ``labels[0]``; ``labels`` are the two expected
    labels (this one and its counterpart).

    Without labels, or when none of the expected labels is among the member's labels
    (another language), the numeric ``value`` decides, like ``ThingsModel.enum_keys``.
    ``None`` when the raw value is missing.
    """
    number = to_int(raw)
    if number is None:
        return None
    if member is not None and member.enum_labels:
        known = {label.strip() for label in member.enum_labels.values()}
        if known & set(labels):
            return member.enum_labels.get(number, "").strip() == labels[0]
    return number == value


def _int_list(value: Any) -> list[int]:
    """A list of ints from JSON text, a list or a comma-separated string."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text)
        except ValueError:
            value = text.split(",") if text else []
    if not isinstance(value, list):
        return []
    numbers = [None if isinstance(item, bool) else to_int(item) for item in value]
    return [number for number in numbers if number is not None]


def _area_array(value: Any) -> tuple[list[int], dict[int, str]]:
    """Room ids and exact names from ``area_array`` (several formats are tolerated)."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text)
        except ValueError:
            return _int_list(text), {}
    if not isinstance(value, list):
        return [], {}
    ids: list[int] = []
    names: dict[int, str] = {}
    for item in value:
        if isinstance(item, Mapping):
            room_id = ci_get(item, "id")
            number = None if isinstance(room_id, bool) else to_int(room_id)
            if number is None:
                continue
            ids.append(number)
            name = ci_get(item, "name")
            if isinstance(name, str) and name.strip():
                names[number] = name.strip()
        elif not isinstance(item, bool) and (number := to_int(item)) is not None:
            ids.append(number)
    return ids, names


def _parse_struct_schedule(raw: Any, members: Mapping[str, PropertyDef]) -> Schedule | None:
    """Parse one item of an array-of-struct ``clean_list`` output (RV1 Pro Max)."""
    if not isinstance(raw, Mapping):
        return None
    by_ref = {str(key): value for key, value in raw.items()}

    def get(identifier: str) -> Any:
        member = members.get(identifier)
        if member is not None and member.ref in by_ref:
            return by_ref[member.ref]
        return ci_get(raw, identifier)

    if get("timerid") is None and get("start_time") is None and get("state") is None:
        return None  # not a schedule
    state = _label_is(members.get("state"), get("state"), ("开启", "关闭"), 1)
    rooms_only = _label_is(members.get("clean_type"), get("clean_type"), ("选区清扫", "全屋清扫"), 1)
    room_ids: list[int] = []
    room_names: dict[int, str] = {}
    if rooms_only is not False:  # rooms, or an unknown type: usable room ids still count
        room_ids, room_names = _area_array(get("area_array"))
    map_id = get("map_id")
    timer_id = get("timerid")
    return Schedule(
        enabled=True if state is None else state,
        active=None,
        start=None,  # the meaning of start_time is unknown, so it is not converted
        end=None,
        period=_int_list(get("select_day")),
        room_ids=room_ids,
        rooms_unknown=bool(rooms_only) and not room_ids,
        tags=[],
        aligned=False,
        once=_label_is(members.get("cleaning_times"), get("cleaning_times"), ("仅一次", "重复"), 0),
        start_raw=get("start_time"),
        timer_id=None if timer_id is None else to_int(timer_id),
        map_id=None if map_id is None else to_int(map_id),
        room_names=room_names,
    )


def _entry_items(entry: Any) -> list[Any] | None:
    if isinstance(entry, list):
        return entry
    if isinstance(entry, Mapping) and isinstance(ci_get(entry, "value"), list):
        return ci_get(entry, "value")
    return None


def _struct_items(response: Any, members_ref: str | None = None) -> list[Any] | None:
    """Find the list of schedule items in the outputs (a list or a ``value`` list).

    The output with ref ``members_ref`` (the array-of-struct output) is preferred;
    otherwise the first output that holds a list.
    """
    outputs = ci_get(ci_get(response, "content"), "outputData")
    if not isinstance(outputs, Mapping):
        return None
    if members_ref is not None and (items := _entry_items(outputs.get(str(members_ref)))) is not None:
        return items
    for entry in outputs.values():
        if (items := _entry_items(entry)) is not None:
            return items
    return None


def parse_schedules(
    response: Any,
    output_ref: str | None = None,
    members: Mapping[str, PropertyDef] | None = None,
    members_ref: str | None = None,
) -> ScheduleList | None:
    """Parse the response data of the ``clean_list`` service.

    ``output_ref`` is the ref of the ``timer`` output from the things model; without
    it (or when it is not in the response) the first output with a ``value`` list is
    used. Returns ``None`` when the response holds no schedules list. Entries that
    are not usable are skipped.

    ``members`` is the item struct of a model whose output is an array of structs
    (RV1 Pro Max, ``ServiceDef.output_members``); it is only used when the response
    is tried first; the L11-A format above is the fallback when it yields nothing.
    ``members_ref`` is the ref of that array-of-struct output.
    """
    if members:
        items = _struct_items(response, members_ref)
        if items is not None:
            schedules = [
                schedule
                for raw in items
                if (schedule := _parse_struct_schedule(raw, members)) is not None
            ]
            if schedules:
                return ScheduleList(tz_offset=None, schedules=schedules)
    entry = _timer_entry(response, output_ref)
    if entry is None:
        if members and items is not None:  # a list without usable schedules
            return ScheduleList(tz_offset=None, schedules=[])
        return None
    schedules = [
        schedule
        for raw in ci_get(entry, "value")
        if (schedule := _parse_schedule(raw)) is not None
    ]
    return ScheduleList(tz_offset=to_int(ci_get(entry, "timeZoneSec")), schedules=schedules)


def known_rooms(schedules: ScheduleList | None) -> dict[int, str | None]:
    """Return every room id of every schedule (ascending), with a name if known.

    The labels are room TYPES and rooms without a label are skipped, so a name is
    only taken from a schedule whose raw ids and labels have the same length and
    are all usable (``aligned``). Exact names (``room_names``, from the area_array
    of an RV1 Pro Max schedule) are always used. The first exact name wins.
    """
    rooms: dict[int, str | None] = {}
    names: dict[int, str] = {}
    for schedule in schedules.schedules if schedules is not None else []:
        for index, room_id in enumerate(schedule.room_ids):
            rooms[room_id] = None
            if room_id in names:
                continue
            if room_id in schedule.room_names:
                names[room_id] = schedule.room_names[room_id]
            elif schedule.aligned:
                names[room_id] = schedule.tags[index]
    return {room_id: names.get(room_id) for room_id in sorted(rooms)}


def parse_room_names(text: str) -> dict[int, str]:
    """Parse lines like ``3 = Blue room``; blank lines are ignored.

    A line that is not ``<number> = <name>`` raises ``ValueError`` with its number.
    """
    rooms: dict[int, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        left, separator, right = line.partition("=")
        try:
            room_id = int(left.strip())
        except ValueError:
            room_id = None
        name = right.strip()
        if not separator or room_id is None or not name:
            raise ValueError(f"line {number}: expected '<room id> = <name>'")
        rooms[room_id] = name
    return rooms


def format_room_names(rooms: Mapping[int, str]) -> str:
    """Format room names as one ``id = name`` line per room, ascending by id."""
    return "\n".join(f"{room_id} = {rooms[room_id]}" for room_id in sorted(rooms))


def segment_names(
    room_ids: Iterable[int],
    tags: Mapping[int, str | None],
    overrides: Mapping[int, str],
    default_name: Callable[[int], str],
) -> dict[int, str]:
    """Name every room: the user's name, else the app's label, else a default.

    Rooms that end up with the same name get `` (<id>)`` appended to tell them apart.
    """
    names = {
        room_id: overrides.get(room_id) or tags.get(room_id) or default_name(room_id)
        for room_id in room_ids
    }
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    return {
        room_id: f"{name} ({room_id})" if counts[name] > 1 else name
        for room_id, name in names.items()
    }


def partition_area(room_ids: Iterable[int]) -> str:
    """Return the ``partition_area`` input for room cleaning: a compact JSON list."""
    return json.dumps([int(room_id) for room_id in room_ids], separators=(",", ":"))
