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
    FALLBACK_INPUT_REFS,
    FALLBACK_PROPERTY_REFS,
    FALLBACK_SERVICE_REFS,
    NEVER_POLL_IDENTIFIERS,
    POLL_IDENTIFIERS,
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


@dataclass(frozen=True, slots=True)
class ServiceDef:
    """One service of the things model."""

    identifier: str  # lower-case
    ref: str
    inputs: Mapping[str, str] = field(default_factory=dict)  # identifier -> ref

    def input_ref(self, identifier: str) -> str | None:
        """Return the ref of an input parameter, or ``None`` if it does not exist."""
        return self.inputs.get(identifier.lower())


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
    enum_labels: dict[int, str] = {}
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    if type_name == "struct" and isinstance(specs, list):
        for member in specs:
            if (child := _parse_property(member)) is not None:
                children.setdefault(child.identifier, child)
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
    )


def _parse_service(raw: Any) -> ServiceDef | None:
    identifier = ci_get(raw, "identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    identifier = identifier.strip().lower()

    ref = _clean_ref(ci_get(raw, "ref")) or FALLBACK_SERVICE_REFS.get(identifier, "")
    if not ref:
        return None

    inputs: dict[str, str] = {}
    fallbacks = FALLBACK_INPUT_REFS.get(identifier, {})
    for item in ci_get(raw, "inputData") or []:
        name = ci_get(item, "identifier")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip().lower()
        if input_ref := _clean_ref(ci_get(item, "ref")) or fallbacks.get(name, ""):
            inputs.setdefault(name, input_ref)
    return ServiceDef(identifier=identifier, ref=ref, inputs=inputs)


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
                if not isinstance(value, Mapping):
                    continue
                members = {str(key): item for key, item in value.items()}
                value = {
                    name: members[child.ref]
                    for name, child in prop.children.items()
                    if child.ref in members
                }
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
