"""Rooms and schedules of the robot.

The robot does not report a map. Room ids, and the room TYPE labels that were chosen
in the Imou app, appear in the cleaning schedules (``clean_list``); a model with the
property ``sequence`` (the cleaning order) also reports its room ids there. Room ids
can be negative. RV1 models may also report rooms in the room configuration, the
cleaning plans and the cleaning order of a map. This module parses those and works
with room names.

This module is deliberately free of Home Assistant imports so that it can be
unit tested with plain Python.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping
from dataclasses import dataclass, field
import json
import logging
from typing import Any

from .model import PropertyDef, ci_get, to_bool, to_int

_LOGGER = logging.getLogger(__name__)

# the rooms of one struct item of a room source: (map id or None, room ids)
RoomEntry = tuple[int | None, tuple[int, ...]]


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
    start_raw: Any = None  # start_time as reported
    timer_id: int | None = None
    map_id: int | None = None
    room_names: dict[int, str] = field(default_factory=dict)  # exact names, by room id
    # The local time of day ("HH:MM") and the repeat days ("mon" ... "sun"). L11-A: only
    # for a repeating schedule, days counted 0 = Sunday; RV1: from start_time (local
    # seconds since midnight) and select_day (1 = Monday ... 7 = Sunday).
    time_of_day: str | None = None
    days: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScheduleList:
    """All schedules of a robot, with the robot's UTC offset."""

    tz_offset: int | None  # seconds
    schedules: list[Schedule] = field(default_factory=list)


_DAY_SECONDS = 86400
# period 0 = Sunday (verified on an L11-A); 1 = Monday ... 6 = Saturday is assumed
_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
# RV1 select_day: 1 = Monday ... 7 = Sunday (verified: 7 is Sunday in the app)
_STRUCT_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# start/end times outside 2000-01-01..2100-01-01 (e.g. milliseconds) are not usable
_MIN_TIMESTAMP = 946684800
_MAX_TIMESTAMP = 4102444800


def _time_of_day(seconds: int) -> str:
    """Format local seconds since midnight as ``HH:MM``."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


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




def _timer_tz(timer: Mapping[str, Any]) -> int | None:
    return to_int(ci_get(timer, "timeZoneSec"))


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
    period_raw = ci_get(raw, "period")
    period = list(period_raw) if isinstance(period_raw, list) else []
    start_time = to_int(ci_get(raw, "startTime"))
    # A repeating schedule's startTime is the LOCAL time of day in seconds (verified
    # on an L11-A: 50400 with period [0] is "Sun 14:00" in the app); a one-off
    # schedule's startTime is a unix timestamp.
    time_of_day = None
    seconds_of_day = start_time is not None and 0 <= start_time < _DAY_SECONDS
    days: list[str] = []
    if period:
        if seconds_of_day:
            time_of_day = _time_of_day(start_time)
        days = [
            _DAY_NAMES[day]
            for item in period
            if not isinstance(item, bool) and (day := to_int(item)) is not None and 0 <= day < 7
        ]
    return Schedule(
        enabled=True if enabled is None else enabled,
        active=ci_get(raw, "active"),
        start=None if period and seconds_of_day else _timestamp(ci_get(raw, "startTime")),
        end=_timestamp(ci_get(raw, "endTime")),
        period=period,
        room_ids=room_ids,
        tags=tags,
        aligned=aligned,
        time_of_day=time_of_day,
        days=days,
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
    period = _int_list(get("select_day"))
    # start_time is the LOCAL time of day in seconds (verified: 7200 is 02:00 and
    # 82800 is 23:00 in the app); select_day counts 1 = Monday ... 7 = Sunday
    start_time = to_int(get("start_time"))
    seconds_of_day = start_time is not None and 0 <= start_time < _DAY_SECONDS
    return Schedule(
        enabled=True if state is None else state,
        active=None,
        start=None,  # a time of day, not a moment (see time_of_day)
        end=None,
        period=period,
        room_ids=room_ids,
        rooms_unknown=bool(rooms_only) and not room_ids,
        tags=[],
        aligned=False,
        once=_label_is(members.get("cleaning_times"), get("cleaning_times"), ("仅一次", "重复"), 0),
        start_raw=get("start_time"),
        timer_id=None if timer_id is None else to_int(timer_id),
        map_id=None if map_id is None else to_int(map_id),
        room_names=room_names,
        time_of_day=_time_of_day(start_time) if seconds_of_day else None,
        days=[
            _STRUCT_DAY_NAMES[day - 1]
            for day in period
            if 1 <= day <= len(_STRUCT_DAY_NAMES)
        ],
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
    return parse_timer(entry)


def parse_timer(entry: Mapping[str, Any]) -> ScheduleList:
    """Parse a raw L11-A ``timer`` object (timeZone, timeZoneSec, value list)."""
    raw_items = ci_get(entry, "value")
    if not isinstance(raw_items, list):
        raw_items = []
    tz_offset = _timer_tz(entry)
    schedules = [
        schedule for raw in raw_items if (schedule := _parse_schedule(raw)) is not None
    ]
    return ScheduleList(tz_offset=tz_offset, schedules=schedules)


def sequence_room_ids(value: Any) -> list[int]:
    """Return the room ids of the ``sequence`` property (the cleaning order).

    The value is an int array (a JSON text or a comma-separated string is tolerated);
    ids can be negative. Entries that are not numbers are skipped, and an id that
    shows up twice is kept once. Zeros are ignored (a fixed-size array may be padded
    with them). A robot without rooms reports ``[]``.
    """
    return list(dict.fromkeys(room for room in _int_list(value) if room != 0))


# -- rooms from the properties of an RV1 model ----------------------------------
def _plain_int(value: Any) -> int | None:
    """Return an int for an int, an integral float or a numeric text; never for a bool."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _room_number(value: Any) -> int | None:
    """Return a room id (negative ids are valid), or ``None`` for 0 and non-numbers."""
    return _plain_int(value) or None


def valid_map_id(value: Any) -> int | None:
    """Return a usable map id, else ``None``.

    -1 (a whole-house schedule), 0, other negative numbers and non-numbers are not
    usable. The declared range of the model is not enforced (real ids are large).
    """
    number = _plain_int(value)
    return number if number is not None and number > 0 else None


def _loads(text: str) -> Any:
    """Parse JSON text; ``None`` when it is not JSON."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def list_room_ids(value: Any) -> list[int]:
    """Return the room ids of a list of rooms, in order and without duplicates.

    ``value`` is a list, or text: JSON (``"[-128,-127]"``, a list of objects with an
    ``id``) or comma-separated numbers. Only ints (and numeric text) count; zeros,
    bools and everything else are ignored. Never raises.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parsed = _loads(text)
        value = parsed if parsed is not None else text.strip("[]").split(",")
    if isinstance(value, (Mapping, int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        _LOGGER.debug("A room list has an unknown format: %s", type(value).__name__)
        return []
    rooms = [
        _room_number(ci_get(item, "id") if isinstance(item, Mapping) else item)
        for item in value
    ]
    return list(dict.fromkeys(room for room in rooms if room is not None))


def _member(item: Mapping[str, Any], refs: Mapping[str, str], identifier: str) -> Any:
    """Return a struct member by its ref (from the model), else by its identifier."""
    if (ref := refs.get(identifier)) is not None:
        for key, value in item.items():
            if str(key) == ref:
                return value
    return ci_get(item, identifier)


def _value_items(value: Any) -> list[Mapping[str, Any]]:
    """Return the struct items of a property value: a list, one struct or JSON text."""
    if isinstance(value, str):
        value = _loads(value)
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, (list, tuple)):
        if value is not None:
            _LOGGER.debug("A room property has an unknown format: %s", type(value).__name__)
        return []
    items = [item for item in value if isinstance(item, Mapping)]
    if len(items) != len(value):
        _LOGGER.debug("A room property has %d item(s) that are not structs", len(value) - len(items))
    return items


def room_entries(
    value: Any,
    refs: Mapping[str, str],
    room_member: str | None = None,
    list_member: str | None = None,
    map_member: str = "mapid",
) -> list[RoomEntry]:
    """Return the rooms of each struct item of a property value, with its map id.

    ``refs`` maps the lower-case member identifiers to their refs (from the things
    model, ``PropertyDef.member_refs``); an item may be keyed by ref or by identifier.
    ``room_member`` holds one room id, ``list_member`` a list of room ids (see
    ``list_room_ids``). The map id is ``None`` when the item has none or an unusable
    one (see ``valid_map_id``). Items without rooms are left out. Never raises.
    """
    entries: list[RoomEntry] = []
    for item in _value_items(value):
        map_id = valid_map_id(_member(item, refs, map_member))
        if list_member is not None:
            rooms = list_room_ids(_member(item, refs, list_member))
        elif room_member is not None and (room := _room_number(_member(item, refs, room_member))):
            rooms = [room]
        else:
            rooms = []
        if rooms:
            entries.append((map_id, tuple(rooms)))
    return entries


def map_ids(value: Any, refs: Mapping[str, str], member: str = "map_id") -> list[int]:
    """Return the usable map ids of ``map_name``, without duplicates."""
    found = (valid_map_id(_member(item, refs, member)) for item in _value_items(value))
    return list(dict.fromkeys(map_id for map_id in found if map_id is not None))


def current_map_id(
    schedule_map_ids: Iterable[Any], listed_map_ids: Iterable[Any]
) -> int | None:
    """Return the id of the map the robot uses now, if it can be told, else ``None``.

    (a) the one usable map id of the schedules (-1 and other unusable ids are
    ignored; several different ids tell nothing), unless ``map_name`` lists maps and
    that id is not among them (the two may be different id spaces), else (b) the map
    id when ``map_name`` lists exactly one map, else (c) ``None``. Ids are compared
    as ints, so ``"123"`` and ``123`` are the same map.
    """
    scheduled = {valid_map_id(item) for item in schedule_map_ids} - {None}
    listed = {valid_map_id(item) for item in listed_map_ids} - {None}
    if len(scheduled) == 1:
        only = next(iter(scheduled))
        if not listed or only in listed:
            return only
    return next(iter(listed)) if len(listed) == 1 else None


def rooms_on_map(entries: Iterable[RoomEntry], current_map: Any) -> list[int]:
    """Return the rooms (in order, without duplicates) of the entries on the current map.

    Entries without a map id are always kept. Room ids repeat per map, so rooms of
    several maps must not be mixed: when the current map is unknown (or no entry has
    its id: the ids of the schedules and of these properties may differ) and the
    entries carry two or more distinct map ids, the entries with a map id are dropped.
    With a single map id they are kept.
    """
    entries = list(entries)
    present = {map_id for map_id, _ in entries if map_id is not None}
    current = valid_map_id(current_map)
    if current is not None and current not in present:
        current = None
    rooms: dict[int, None] = {}
    for map_id, room_ids in entries:
        if map_id is None:
            keep = True
        elif current is not None:
            keep = map_id == current
        else:
            keep = len(present) < 2
        if keep:
            rooms.update(dict.fromkeys(room_ids))
    return list(rooms)


def active_map_id(
    sweeper_map: Any,
    sequence_entries: Iterable[RoomEntry],
    sequence: Iterable[int],
    listed_map_ids: Iterable[Any],
) -> int | None:
    """Return the id of the map the robot has active now, if it can be told, else ``None``.

    In this order: (a) the map id of ``sweeper_sequence`` when it is usable, (b) the
    one map whose cleaning order (``sequence_entries``, from ``swp_cleansequence``)
    equals the top-level ``sequence``, which is the order of the active map (the
    order must not be empty and exactly one map must match), (c) the one map of
    ``map_name``, else (d) ``None``.
    """
    if (sweeper := valid_map_id(sweeper_map)) is not None:
        return sweeper
    order = tuple(sequence)
    if order:
        matches = {
            map_id
            for map_id, rooms in sequence_entries
            if map_id is not None and rooms == order
        }
        if len(matches) == 1:
            return next(iter(matches))
    listed = {valid_map_id(item) for item in listed_map_ids} - {None}
    return next(iter(listed)) if len(listed) == 1 else None


def map_rooms(entries: Iterable[RoomEntry], map_id: int | None) -> list[int]:
    """Return the rooms (in order, without duplicates) of one map.

    Entries without a map id are kept (they cannot be told apart); entries of another
    map are not. Without a ``map_id`` only the entries without a map id are kept.
    """
    rooms: dict[int, None] = {}
    for entry_map, room_ids in entries:
        if entry_map is None or entry_map == map_id:
            rooms.update(dict.fromkeys(room_ids))
    return list(rooms)


def map_names(value: Any, refs: Mapping[str, str], id_member: str, name_member: str) -> dict[int, str]:
    """Return the name of every map of ``map_name`` that has a usable id and a name."""
    names: dict[int, str] = {}
    for item in _value_items(value):
        map_id = valid_map_id(_member(item, refs, id_member))
        name = _member(item, refs, name_member)
        if map_id is not None and isinstance(name, str) and name.strip():
            names.setdefault(map_id, name.strip())
    return names


def room_key(map_id: int | None, room_id: int) -> str:
    """Return the key of a room: ``<room>``, or ``<map>:<room>`` on a robot with several maps.

    It is used as the segment id, in the stored room names and in the area mapping.
    """
    return str(room_id) if map_id is None else f"{map_id}:{room_id}"


def parse_room_key(text: Any) -> tuple[int | None, int]:
    """Parse a key made by ``room_key`` into ``(map id or None, room id)``.

    Raises ``ValueError`` for anything else.
    """
    value = str(text).strip()
    map_text, separator, room_text = value.rpartition(":")
    map_id = None
    if separator:
        map_id = valid_map_id(map_text)
        if map_id is None:
            raise ValueError(f"not a room key: {value!r}")
    return map_id, int(room_text)


def room_overrides_for(stored: Any, map_id: int | None) -> dict[int, str]:
    """Return the room names of the user for one map (``None``: the plain room keys)."""
    overrides: dict[int, str] = {}
    for key, name in stored.items() if isinstance(stored, Mapping) else ():
        try:
            key_map, room_id = parse_room_key(key)
        except ValueError:
            continue
        if key_map == map_id and isinstance(name, str) and name:
            overrides[room_id] = name
    return overrides


def replace_room_names(
    stored: Any, map_id: int | None, names: Mapping[int, str]
) -> dict[str, str]:
    """Return the stored room names with those of one map replaced by ``names``.

    The names of other maps are kept, and so are the plain names (older ones, or of a
    robot whose map is unknown) while ``map_id`` is a map; names with an unusable key go.
    """
    result: dict[str, str] = {}
    for key, name in stored.items() if isinstance(stored, Mapping) else ():
        try:
            key_map, _ = parse_room_key(key)
        except ValueError:
            continue
        if key_map != map_id and isinstance(name, str):
            result[str(key)] = name
    result.update({room_key(map_id, room_id): name for room_id, name in names.items()})
    return result


def known_rooms(
    schedules: ScheduleList | None, sequence: Iterable[int] = ()
) -> dict[int, str | None]:
    """Return every room id of every schedule and of the cleaning order (ascending),
    with a name if known.

    The rooms of ``sequence`` (the ids of the ``sequence`` property, and of the other
    room properties of an RV1 model) have no name.

    The labels are room TYPES and rooms without a label are skipped, so a name is
    only taken from a schedule whose raw ids and labels have the same length and
    are all usable (``aligned``). Exact names (``room_names``, from the area_array
    of an RV1 Pro Max schedule) are always used. The first exact name wins.
    """
    rooms: dict[int, str | None] = dict.fromkeys(sequence)
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


def remembered_rooms(
    known: Mapping[int, str | None], overrides: Mapping[int, str]
) -> dict[int, str | None]:
    """Return the rooms of the schedules plus the rooms the user named (ascending).

    A room that only has a name of the user (no schedule lists it any more) has
    no label (None), so the user's name is used.
    """
    return {room_id: known.get(room_id) for room_id in sorted({*known, *overrides})}


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


def segment_sort_key(segment_id: str) -> tuple[bool, int, int, str]:
    """Sort plain segment ids by number, then those of a map by map and number, other ids last."""
    try:
        map_id, room_id = parse_room_key(segment_id)
    except ValueError:
        return (True, 0, 0, segment_id)
    return (False, map_id or 0, room_id, segment_id)


def room_area_labels(
    known: Mapping[int, str | None],
    overrides: Mapping[int, str],
    default_name: Callable[[int], str],
) -> dict[str, int]:
    """Return the form label of every room, mapped to its room id (ascending).

    The label is ``Room 3: Kantoor`` (the name of the user, else the label of the
    Imou app) or just ``Room 3`` when the room only has its default name. The room
    number is always in the label, so the labels are unique.
    """
    labels: dict[str, int] = {}
    for room_id in sorted(known):
        default = default_name(room_id)
        name = overrides.get(room_id) or known[room_id]
        labels[f"{default}: {name}" if name and name != default else default] = room_id
    return labels


def room_button_name(name: str, default: str) -> str:
    """Return the room name for "Clean <room>": a default name reads "room 3"."""
    return name.lower() if name == default else name


def areas_by_room(
    area_mapping: Any,
    room_ids: Iterable[int],
    valid_areas: Container[str] | None = None,
    map_id: int | None = None,
) -> dict[int, str]:
    """Return the area linked to each room, from a vacuum ``area_mapping``.

    ``area_mapping`` is ``{area_id: [segment_id, ...]}``. Rooms without an area, and
    areas that are not in ``valid_areas`` (when given), are left out. When a room is
    in several areas the first area (by id) wins. A segment id counts for the rooms
    of ``map_id`` only (``None``: plain room ids, see ``room_key``).
    """
    result: dict[int, str] = {}
    if not isinstance(area_mapping, Mapping):
        return result
    wanted = set(room_ids)
    for area_id in sorted(str(key) for key in area_mapping):
        if valid_areas is not None and area_id not in valid_areas:
            continue
        segments = area_mapping[area_id]
        for segment in segments if isinstance(segments, (list, tuple)) else ():
            try:
                segment_map, room_id = parse_room_key(segment)
            except ValueError:
                continue
            if segment_map == map_id and room_id in wanted and room_id not in result:
                result[room_id] = area_id
    return result


def new_area_mapping(
    old: Any,
    shown: Mapping[int, str | None],
    valid_areas: Container[str] | None = None,
    map_id: int | None = None,
) -> dict[str, list[str]]:
    """Return the vacuum ``area_mapping`` after the user chose areas for ``shown`` rooms.

    ``shown`` maps each room id that was in the form to its area id (None: no area).
    A room whose area equals the one the form showed (``areas_by_room`` of ``old``
    with ``valid_areas``) keeps all its links. The other shown rooms are removed from
    every area of ``old`` and added to their new area; segment ids that were not
    shown stay. Segment ids are strings, ascending and without duplicates;
    areas without segments are dropped. The segment ids are ``room_key(map_id, room)``.
    """
    default = areas_by_room(old, shown, valid_areas, map_id)
    shown = {room_id: area for room_id, area in shown.items() if area != default.get(room_id)}
    shown_ids = {room_key(map_id, room_id) for room_id in shown}
    result: dict[str, set[str]] = {}
    for area_id, segments in (old.items() if isinstance(old, Mapping) else ()):
        for segment in segments if isinstance(segments, (list, tuple)) else ():
            if str(segment) not in shown_ids:
                result.setdefault(str(area_id), set()).add(str(segment))
    for room_id, area_id in shown.items():
        if area_id:
            result.setdefault(area_id, set()).add(room_key(map_id, room_id))
    return {
        area_id: sorted(segments, key=segment_sort_key)
        for area_id, segments in result.items()
        if segments
    }


def new_last_seen_segments(old: Any, names: Mapping[int | str, str]) -> list[dict[str, Any]]:
    """Return the vacuum ``last_seen_segments`` with ``names`` (segment id -> name) applied.

    Entries of ``old`` keep their order and other keys; a listed room gets its new
    name. Rooms that are not in ``old`` are added at the end (ascending), with
    ``group`` None. Entries without an id are dropped.
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    wanted = {str(room_id): name for room_id, name in names.items()}
    for entry in old if isinstance(old, (list, tuple)) else ():
        if not isinstance(entry, Mapping) or "id" not in entry:
            continue
        segment_id = str(entry["id"])
        if segment_id in seen:
            continue
        seen.add(segment_id)
        item = {**entry, "id": segment_id}
        if segment_id in wanted:
            item["name"] = wanted[segment_id]
        result.append(item)
    for room_id in sorted(names, key=lambda key: segment_sort_key(str(key))):
        if str(room_id) not in seen:
            result.append({"id": str(room_id), "name": names[room_id], "group": None})
    return result


def partition_area(room_ids: Iterable[int]) -> str:
    """Return the ``partition_area`` input for room cleaning: a compact JSON list."""
    return json.dumps([int(room_id) for room_id in room_ids], separators=(",", ":"))
