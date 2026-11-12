"""Parsing of the Imou things model (properties and services) and its values.

Refs are resolved at runtime by *identifier*. Identifiers and dictionary keys are
matched case-insensitively because the API mixes ``ref``/``Ref``,
``identifier``/``Identifier`` and so on. This module has no Home Assistant
dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from typing import Any

from .const import (
    CHARGING_LABEL,
    CHARGING_VALUE,
    ERROR_CODE,
    ERROR_KEYS,
    ERROR_LABEL_KEYS,
    FALLBACK_INPUT_REFS,
    FALLBACK_OUTPUT_REFS,
    FALLBACK_PROPERTY_REFS,
    FALLBACK_SERVICE_REFS,
    LEVEL_KEYS,
    MOP_SPEED_KEYS,
    MOP_SPEED_LABEL_KEYS,
    NEVER_POLL_IDENTIFIERS,
    POLL_IDENTIFIERS,
    PROP_ERROR_INFO,
    PROP_MOP_SPEED,
    PROP_POWER_STATUS,
    PROP_WIFI,
    PROP_WIND_POWER,
    PROP_WORK_STATUS,
    RETURN_HOME_IDENTIFIERS,
    RETURN_START_LABEL,
    RETURN_START_VALUE,
    ROOM_SOURCES,
    WIFI_KEYS,
    WIFI_LABEL_KEYS,
    WIND_ENUM_LABEL_KEYS,
    WIND_UNOFFERED_KEYS,
    WORK_STATUS_KEYS,
    WORK_STATUS_LABEL_KEYS,
)


def ci_get(mapping: Any, key: str, default: Any = None) -> Any:
    """Case-insensitive lookup in a mapping; non-mappings return ``default``."""
    if not isinstance(mapping, Mapping):
        return default
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if isinstance(candidate, str) and candidate.lower() == lowered:
            return value
    return default


def to_int(value: Any) -> int | None:
    """Coerce a raw API value to an int, or ``None`` when that is not possible."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return None
            return int(round(number)) if math.isfinite(number) else None
    return None


def to_bool(value: Any) -> bool | None:
    """Coerce a raw API value (``1``/``0``/``true``/...) to a bool or ``None``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "on", "yes"):
            return True
        if text in ("0", "false", "off", "no"):
            return False
    return None


def level_key(levels: Mapping[int, str], raw: int | None) -> str | None:
    """Translation key of a raw level; unmapped values become ``unknown_<n>``."""
    if raw is None:
        return None
    return levels.get(raw, f"unknown_{raw}")


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_ref(value: Any) -> str:
    """Return a ref as a stripped string ("" when missing)."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


# the identifiers of the room properties: a struct of these is kept in any shape
_ROOM_SOURCE_IDENTIFIERS = frozenset(source[0] for source in ROOM_SOURCES)


@dataclass(frozen=True, slots=True)
class PropertyDef:
    """One property (or struct member) of the things model."""

    identifier: str  # lower-case
    ref: str
    data_type: str  # lower-case: int, bool, enum, text, struct, ...
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    # enum value -> raw label from the model (usually Chinese)
    enum_labels: Mapping[int, str] = field(default_factory=dict)
    # struct members by lower-case identifier
    children: Mapping[str, PropertyDef] = field(default_factory=dict)
    # members (by lower-case identifier) of the item struct of an array of structs
    item_members: Mapping[str, PropertyDef] = field(default_factory=dict)

    @property
    def member_refs(self) -> dict[str, str]:
        """Return the refs of the struct members (or of the array's item struct
        members), by lower-case identifier."""
        members = self.children or self.item_members
        return {name: member.ref for name, member in members.items()}


@dataclass(frozen=True, slots=True)
class ServiceDef:
    """One service of the things model."""

    identifier: str  # lower-case
    ref: str
    inputs: Mapping[str, str] = field(default_factory=dict)  # identifier -> ref
    outputs: Mapping[str, str] = field(default_factory=dict)  # identifier -> ref
    input_types: Mapping[str, str] = field(default_factory=dict)  # identifier -> lower-case type
    # members (by lower-case identifier, with ref and enum labels) of the item struct
    # of an output that is an array of structs (RV1 Pro Max clean_list); else empty
    output_members: Mapping[str, PropertyDef] = field(default_factory=dict)
    # ref of that array-of-struct output (None when there is none)
    output_members_ref: str | None = None

    def input_ref(self, identifier: str) -> str | None:
        """Return the ref of an input parameter, or ``None`` if it does not exist."""
        return self.inputs.get(identifier.lower())

    def output_ref(self, identifier: str) -> str | None:
        """Return the ref of an output, or ``None`` if it does not exist."""
        return self.outputs.get(identifier.lower())


def _parse_property(raw: Any) -> PropertyDef | None:
    identifier = ci_get(raw, "identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    identifier = identifier.strip().lower()

    ref = _clean_ref(ci_get(raw, "ref")) or FALLBACK_PROPERTY_REFS.get(identifier, "")
    if not ref:
        return None

    data_type = ci_get(raw, "dataType")
    type_name = str(ci_get(data_type, "type") or "").lower()
    specs = ci_get(data_type, "specs")

    children: dict[str, PropertyDef] = {}
    item_members: dict[str, PropertyDef] = {}
    enum_labels: dict[int, str] = {}
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    if type_name == "struct" and isinstance(specs, list):
        for member in specs:
            if (child := _parse_property(member)) is not None:
                children.setdefault(child.identifier, child)
    elif type_name == "array" and isinstance(specs, Mapping):
        element = ci_get(specs, "item")
        members = ci_get(element, "specs")
        if str(ci_get(element, "type") or "").lower() == "struct" and isinstance(members, list):
            for member in members:
                if (child := _parse_property(member)) is not None:
                    item_members.setdefault(child.identifier, child)
    elif isinstance(specs, Mapping):
        for item in ci_get(specs, "list") or []:
            number = to_int(ci_get(item, "value"))
            if number is not None:
                enum_labels[number] = str(ci_get(item, "desc") or "")
        unit = ci_get(specs, "unit") or None
        bounds = ci_get(specs, "range")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
            minimum, maximum = _to_float(bounds[0]), _to_float(bounds[1])
        step = _to_float(ci_get(specs, "step"))

    return PropertyDef(
        identifier=identifier,
        ref=ref,
        data_type=type_name,
        unit=unit,
        minimum=minimum,
        maximum=maximum,
        step=step,
        enum_labels=enum_labels,
        children=children,
        item_members=item_members,
    )


def _parse_service(raw: Any) -> ServiceDef | None:
    identifier = ci_get(raw, "identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    identifier = identifier.strip().lower()

    ref = _clean_ref(ci_get(raw, "ref")) or FALLBACK_SERVICE_REFS.get(identifier, "")
    if not ref:
        return None

    inputs = _parse_parameters(
        ci_get(raw, "inputData"), FALLBACK_INPUT_REFS.get(identifier, {})
    )
    outputs = _parse_parameters(
        ci_get(raw, "outputData"), FALLBACK_OUTPUT_REFS.get(identifier, {})
    )
    input_types: dict[str, str] = {}
    for item in ci_get(raw, "inputData") or []:
        name = ci_get(item, "identifier")
        if isinstance(name, str) and name.strip().lower() in inputs:
            type_name = str(ci_get(ci_get(item, "dataType"), "type") or "").lower()
            input_types.setdefault(name.strip().lower(), type_name)
    members_ref, members = _parse_output_members(ci_get(raw, "outputData"))
    return ServiceDef(
        identifier=identifier,
        ref=ref,
        inputs=inputs,
        outputs=outputs,
        input_types=input_types,
        output_members=members,
        output_members_ref=members_ref,
    )


def _parse_output_members(items: Any) -> tuple[str | None, dict[str, PropertyDef]]:
    """Return the ref and item struct members of the first output that is an array
    of structs."""
    for item in items or []:
        data_type = ci_get(item, "dataType")
        if str(ci_get(data_type, "type") or "").lower() != "array":
            continue
        element = ci_get(ci_get(data_type, "specs"), "item")
        members = ci_get(element, "specs")
        if str(ci_get(element, "type") or "").lower() != "struct" or not isinstance(members, list):
            continue
        parsed: dict[str, PropertyDef] = {}
        for member in members:
            if (prop := _parse_property(member)) is not None:
                parsed.setdefault(prop.identifier, prop)
        if parsed:
            return _clean_ref(ci_get(item, "ref")) or None, parsed
    return None, {}


def _unique_level_keys(
    labels: Mapping[int, str], label_keys: Mapping[str, str]
) -> dict[int, str]:
    """Raw value -> key in value order; a key that an earlier value already has
    (same label) is not offered twice: the later value gets ``unknown_<n>``."""
    keys: dict[int, str] = {}
    used: set[str] = set()
    for value, label in sorted(labels.items()):
        key = label_keys.get(label.strip(), f"unknown_{value}")
        if key in used:
            key = f"unknown_{value}"
        used.add(key)
        keys[value] = key
    return keys


def _parse_parameters(items: Any, fallbacks: Mapping[str, str]) -> dict[str, str]:
    """Return ``{identifier: ref}`` of the inputs or outputs of a service."""
    parameters: dict[str, str] = {}
    for item in items or []:
        name = ci_get(item, "identifier")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip().lower()
        if ref := _clean_ref(ci_get(item, "ref")) or fallbacks.get(name, ""):
            parameters.setdefault(name, ref)
    return parameters


class ReportTracker:
    """Which settings the robot actually reports.

    The shared things model lists settings that a firmware may not implement: the
    L11-A never reports auto-empty or resume-after-charging, before or after a
    write. A setting that is missing from the first online poll is therefore
    treated as "not reported by the device".
    """

    def __init__(self) -> None:
        """Start without any evidence."""
        self.seen_online = False
        self.reported: set[str] = set()

    def update(self, online: bool, values: Mapping[str, Any]) -> None:
        """Record a poll; only polls of an online robot count as evidence."""
        if online:
            self.seen_online = True
            self.reported.update(values)

    def is_reported(self, identifier: str) -> bool:
        """Return whether the robot has reported this setting."""
        return identifier in self.reported

    def enabled_by_default(self, identifier: str) -> bool:
        """Return whether an entity for this setting is enabled by default.

        It is, unless the robot was online and left the setting out of its poll.
        Without evidence (the robot was never online yet) nothing is disabled.
        """
        return not self.seen_online or identifier in self.reported

    def as_diagnostics(self) -> dict[str, Any]:
        """Return the evidence, for the diagnostics download."""
        return {"seen_online": self.seen_online, "reported_identifiers": sorted(self.reported)}


class ThingsModel:
    """The things model of one product, resolved by identifier."""

    def __init__(
        self,
        properties: Mapping[str, PropertyDef],
        services: Mapping[str, ServiceDef],
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        self._properties = dict(properties)
        self._services = dict(services)
        self.raw: Mapping[str, Any] = raw or {}

    @classmethod
    def from_api(cls, data: Any) -> ThingsModel:
        """Build a model from the ``getProductModel`` response data."""
        properties: dict[str, PropertyDef] = {}
        for item in ci_get(data, "properties") or []:
            if (prop := _parse_property(item)) is not None:
                properties.setdefault(prop.identifier, prop)
        services: dict[str, ServiceDef] = {}
        for item in ci_get(data, "services") or []:
            if (service := _parse_service(item)) is not None:
                services.setdefault(service.identifier, service)
        return cls(properties, services, data if isinstance(data, Mapping) else None)

    # -- lookups -----------------------------------------------------------
    def prop(self, identifier: str) -> PropertyDef | None:
        """Return a property by identifier (case-insensitive)."""
        return self._properties.get(identifier.lower())

    def member(self, identifier: str, child: str | None = None) -> PropertyDef | None:
        """Return a property, or one member of a struct property."""
        prop = self.prop(identifier)
        if prop is None or child is None:
            return prop
        return prop.children.get(child.lower())

    def service(self, identifier: str) -> ServiceDef | None:
        """Return a service by identifier (case-insensitive)."""
        return self._services.get(identifier.lower())

    @property
    def services(self) -> Mapping[str, ServiceDef]:
        """Return all services by (lower-case) identifier."""
        return self._services

    def wind_levels(self) -> dict[int, str]:
        """Return the suction levels (raw value -> translation key) of this model.

        A WindPower enum with labels (RV1 Pro Max) gives its levels in value order;
        otherwise the fixed L11-A levels apply.
        """
        prop = self.prop(PROP_WIND_POWER)
        if prop is not None and prop.data_type == "enum" and prop.enum_labels:
            return _unique_level_keys(prop.enum_labels, WIND_ENUM_LABEL_KEYS)
        return dict(LEVEL_KEYS[PROP_WIND_POWER])

    def wind_choices(self) -> dict[int, str]:
        """Return the suction levels that are offered for selection.

        These are the levels of ``wind_levels`` without the "off" level: the app
        does not offer it either. A robot that reports it still shows as "off".
        """
        return {
            value: key
            for value, key in self.wind_levels().items()
            if key not in WIND_UNOFFERED_KEYS
        }

    def mop_levels(self) -> dict[int, str]:
        """Return the mop intensity levels (raw value -> translation key).

        The model's labels decide (unknown labels give ``unknown_<n>``); a model
        without labels uses the number table.
        """
        prop = self.prop(PROP_MOP_SPEED)
        if prop is not None and prop.enum_labels:
            return _unique_level_keys(prop.enum_labels, MOP_SPEED_LABEL_KEYS)
        return dict(MOP_SPEED_KEYS)

    def enum_keys(
        self,
        identifier: str,
        child: str | None,
        label_keys: Mapping[str, str],
        numeric: Mapping[int, str],
    ) -> dict[int, str]:
        """Return an enum's raw value -> translation key, in value order.

        With labels in the model, the label decides: a label that is not in
        ``label_keys`` gives ``unknown_<n>``. When none of the labels is known
        (another language), or the model has no labels, the whole number table
        applies. Values the model does not declare get their number-table key only
        when every declared, recognised label agrees with the number table (same
        key for the same value) and no declared value uses that key; otherwise they
        become ``unknown_<n>``.
        """
        prop = self.member(identifier, child)
        if prop is None or not prop.enum_labels:
            return dict(numeric)
        keys = {
            value: label_keys.get(label.strip(), f"unknown_{value}")
            for value, label in prop.enum_labels.items()
        }
        recognised = {
            value: key
            for value, label in prop.enum_labels.items()
            if (key := label_keys.get(label.strip())) is not None
        }
        if not recognised:
            return dict(numeric)
        agree = all(numeric.get(value) == key for value, key in recognised.items())
        used = set(keys.values())
        for value, key in numeric.items():
            if value in keys:
                continue
            keys[value] = key if agree and key not in used else f"unknown_{value}"
        return dict(sorted(keys.items()))

    def work_status_keys(self) -> dict[int, str]:
        """Return work_status value -> key (see ``enum_keys``)."""
        return self.enum_keys(PROP_WORK_STATUS, None, WORK_STATUS_LABEL_KEYS, WORK_STATUS_KEYS)

    def error_keys(self) -> dict[int, str]:
        """Return errorinfo.errcode value -> key (see ``enum_keys``)."""
        return self.enum_keys(PROP_ERROR_INFO, ERROR_CODE, ERROR_LABEL_KEYS, ERROR_KEYS)

    def wifi_keys(self) -> dict[int, str]:
        """Return wifi_rssi_sdj value -> key (see ``enum_keys``)."""
        return self.enum_keys(PROP_WIFI, None, WIFI_LABEL_KEYS, WIFI_KEYS)

    def is_charging(self, raw: Any, power: Any = None) -> bool | None:
        """Return whether a ``power_status`` value means "charging" (None: unknown).

        With labels in the model the label (充电中) decides, otherwise (no labels, or
        none of them is the charging label) the value 1. Without any value, the
        sign of the battery ``power`` decides: negative means charging (RV1 models),
        anything else is unknown, not "not charging".
        """
        value = to_int(raw)
        if value is None:
            battery = to_int(power)
            # a negative battery means charging; anything else proves nothing
            return True if battery is not None and battery < 0 else None
        prop = self.prop(PROP_POWER_STATUS)
        if prop is not None and CHARGING_LABEL in {
            label.strip() for label in prop.enum_labels.values()
        }:
            label = prop.enum_labels.get(value)
            return None if label is None else label.strip() == CHARGING_LABEL
        return value == CHARGING_VALUE

    def return_home(self) -> tuple[str, int] | None:
        """Return the property and value that send the robot to its dock.

        The first property of ``RETURN_HOME_IDENTIFIERS`` that the model has; the
        value is the one labelled "start return" when the property is an enum with
        that label (RV1L Pro ``swp_backstation``), else 1. ``None`` without any.
        """
        for identifier in RETURN_HOME_IDENTIFIERS:
            if (prop := self.prop(identifier)) is None:
                continue
            for value, label in sorted(prop.enum_labels.items()):
                if label.strip() == RETURN_START_LABEL:
                    return identifier, value
            return identifier, RETURN_START_VALUE
        return None

    def has_service(self, identifier: str) -> bool:
        """Return whether the model has a service with this identifier."""
        return identifier.lower() in self._services

    # -- polling -----------------------------------------------------------
    def poll_refs(self) -> list[str]:
        """Refs to poll, in a stable order; large/map data is never included."""
        refs: list[str] = []
        for identifier in POLL_IDENTIFIERS:
            if identifier in NEVER_POLL_IDENTIFIERS:
                continue
            prop = self._properties.get(identifier)
            if prop is not None and prop.ref not in refs:
                refs.append(prop.ref)
        return refs

    def parse_values(self, raw: Any) -> dict[str, Any]:
        """Map a ``{ref: value}`` response to ``{identifier: value}``.

        Struct values become ``{member identifier: value}``. Properties that the
        robot did not report are simply absent.
        """
        if not isinstance(raw, Mapping):
            return {}
        by_ref = {str(key): value for key, value in raw.items()}
        values: dict[str, Any] = {}
        for identifier, prop in self._properties.items():
            if prop.ref not in by_ref:
                continue
            value = by_ref[prop.ref]
            if prop.children:
                keep_raw = identifier in _ROOM_SOURCE_IDENTIFIERS
                if not isinstance(value, Mapping):
                    # a room source struct may come as a list or as JSON text: the room
                    # parser (``rooms.room_entries``) reads those shapes
                    if not keep_raw:
                        continue
                else:
                    members = {str(key): item for key, item in value.items()}
                    mapped = {
                        name: members[child.ref]
                        for name, child in prop.children.items()
                        if child.ref in members
                    }
                    if not mapped and keep_raw:
                        pass  # keyed by identifier (or unknown): keep it as it came
                    else:
                        value = mapped
            values[identifier] = value
        return values

    # -- diagnostics -------------------------------------------------------
    def as_diagnostics(self) -> dict[str, Any]:
        """Return a compact, product-level summary of the resolved model."""

        def describe(prop: PropertyDef) -> dict[str, Any]:
            info: dict[str, Any] = {"ref": prop.ref, "type": prop.data_type}
            if prop.enum_labels:
                info["enum"] = {str(k): v for k, v in sorted(prop.enum_labels.items())}
            if prop.minimum is not None or prop.maximum is not None:
                info["range"] = [prop.minimum, prop.maximum, prop.step]
            if prop.children:
                info["members"] = {k: describe(v) for k, v in prop.children.items()}
            return info

        return {
            "properties": {k: describe(v) for k, v in self._properties.items()},
            "services": {
                k: {"ref": v.ref, "inputs": dict(v.inputs)}
                for k, v in self._services.items()
            },
        }
