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

from .model import ci_get, to_bool, to_int


@dataclass(frozen=True, slots=True)
class Schedule:
    """One cleaning schedule of the robot."""

    enabled: bool  # the schedule's on/off switch ("unlock")
    active: Any  # raw "active" flag; its meaning is unknown
    start: int | None  # unix timestamp in seconds
    end: int | None
    period: list[Any] = field(default_factory=list)  # raw; [] is a one-off schedule
    room_ids: list[int] = field(default_factory=list)  # [] means the whole house
    tags: list[str] = field(default_factory=list)  # room TYPE labels from the app
    # True only when the raw ids and labels have the same length and every entry of
    # both is usable; only then do the labels belong to the ids, in order
    aligned: bool = False


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


def parse_schedules(response: Any, output_ref: str | None = None) -> ScheduleList | None:
    """Parse the response data of the ``clean_list`` service.

    ``output_ref`` is the ref of the ``timer`` output from the things model; without
    it (or when it is not in the response) the first output with a ``value`` list is
    used. Returns ``None`` when the response holds no schedules list. Entries that
    are not usable are skipped.
    """
    entry = _timer_entry(response, output_ref)
    if entry is None:
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
    are all usable (``aligned``). The first exact name wins.
    """
    rooms: dict[int, str | None] = {}
    names: dict[int, str] = {}
    for schedule in schedules.schedules if schedules is not None else []:
        for index, room_id in enumerate(schedule.room_ids):
            rooms[room_id] = None
            if schedule.aligned and room_id not in names:
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
