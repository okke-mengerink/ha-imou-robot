"""Which parts of a robot model the integration can map.

Imou publishes no things models for its robot vacuums, so every model is mapped from
its own things model. ``support_report`` says which core functions the model has and
which enum labels are not known, so that the user can be told clearly. This module
has no Home Assistant dependency.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from .const import (
    ERROR_CODE,
    ERROR_LABEL_KEYS,
    INPUT_MODE,
    INPUT_PARTITION_AREA,
    INPUT_STATUS,
    PROP_ERROR_INFO,
    PROP_POWER,
    PROP_WIFI,
    PROP_WIND_POWER,
    PROP_WORK_STATUS,
    SERVICE_CLEAN_CONTROL,
    WIFI_LABEL_KEYS,
    WORK_STATUS_LABEL_KEYS,
)
from .model import ThingsModel

# Robot models (as the Imou app shows them) that were checked on a real robot, and
# the ones that were mapped from a user's diagnostics only.
VERIFIED_MODELS: frozenset[str] = frozenset({"L11-A"})
PARTLY_VERIFIED_MODELS: frozenset[str] = frozenset({"RV1 Pro Max"})

NEW_MODEL_URL = (
    "https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=new_model.yml"
)

# the identifiers (and the struct member) whose enum labels are checked
_LABEL_CHECKS: tuple[tuple[str, str | None, str, dict[str, str]], ...] = (
    (PROP_WORK_STATUS, None, PROP_WORK_STATUS, WORK_STATUS_LABEL_KEYS),
    (PROP_WIFI, None, PROP_WIFI, WIFI_LABEL_KEYS),
    (PROP_ERROR_INFO, ERROR_CODE, f"{PROP_ERROR_INFO}.{ERROR_CODE}", ERROR_LABEL_KEYS),
)


def _normalise_model(name: str) -> str:
    """Upper-case, without a leading "IMOU", spaces, hyphens and underscores."""
    text = name.strip().upper()
    text = text.removeprefix("IMOU")
    return re.sub(r"[\s_-]+", "", text)


def model_in(name: str | None, names: frozenset[str]) -> bool:
    """Return whether a robot's model name is exactly one of ``names``.

    Both sides are normalised (see ``_normalise_model``), and "RV" in front of the
    name is accepted, so "Imou RV-L11-A" and "L11-A" are the L11-A, but "L11-A Pro"
    is not.
    """
    if not name or not _normalise_model(name):
        return False
    given = _normalise_model(name)
    known = {_normalise_model(item) for item in names}
    return given in known or (given.startswith("RV") and given[2:] in known)


def support_report(model: ThingsModel, device_model: str | None = None) -> dict[str, Any]:
    """Describe how well this things model is understood.

    ``capabilities`` says which functions the model offers; ``missing_core`` lists
    the core ones it lacks (start_stop, return_to_dock, battery, status);
    ``unknown_enum_labels`` is ``{identifier: {value: label}}`` for labels that are
    not in the integration's tables.
    """
    control = model.service(SERVICE_CLEAN_CONTROL)
    has_status_input = control is not None and control.input_ref(INPUT_STATUS) is not None
    capabilities = {
        "start": has_status_input,
        "pause": has_status_input,
        "stop": has_status_input,
        "return_to_dock": model.return_home() is not None,
        "battery": model.prop(PROP_POWER) is not None,
        "status": model.prop(PROP_WORK_STATUS) is not None,
        "suction": model.prop(PROP_WIND_POWER) is not None,
        "room_cleaning": (
            control is not None
            and control.input_ref(INPUT_MODE) is not None
            and control.input_ref(INPUT_PARTITION_AREA) is not None
        ),
    }
    missing_core = [
        name
        for name, present in (
            ("start_stop", capabilities["start"] and capabilities["stop"]),
            ("return_to_dock", capabilities["return_to_dock"]),
            ("battery", capabilities["battery"]),
            ("status", capabilities["status"]),
        )
        if not present
    ]
    unknown: dict[str, dict[int, str]] = {}
    for identifier, child, name, label_keys in _LABEL_CHECKS:
        prop = model.member(identifier, child)
        if prop is None:
            continue
        found = {
            value: label
            for value, label in sorted(prop.enum_labels.items())
            if label.strip() not in label_keys
        }
        if found:
            unknown[name] = found
    return {
        "model": device_model,
        "verified_model": model_in(device_model, VERIFIED_MODELS),
        "partly_verified": model_in(device_model, PARTLY_VERIFIED_MODELS),
        "capabilities": capabilities,
        "missing_core": missing_core,
        "unknown_enum_labels": unknown,
    }


def needs_notice(report: dict[str, Any]) -> bool:
    """Return whether the user should be told that this model is not fully supported."""
    return bool(report["missing_core"] or report["unknown_enum_labels"])


def new_model_issue_url(device_model: str | None) -> str:
    """Return the GitHub "new model" issue form, prefilled with the robot model."""
    name = quote_plus((device_model or "").strip() or "unknown")
    return f"{NEW_MODEL_URL}&title=%5BModel%5D%3A+{name}&robot_model={name}"
