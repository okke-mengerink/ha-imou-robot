"""Constants for the Imou Robot Vacuum integration.

This module is deliberately free of Home Assistant imports so that the pure
parts of the integration (API client, things-model parsing) can be unit tested
without Home Assistant installed.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "imou_robot"
MANUFACTURER: Final = "Imou"

# Config entry data
CONF_APP_ID: Final = "app_id"
CONF_APP_SECRET: Final = "app_secret"
CONF_REGION: Final = "region"

# Imou Open Platform data centres
REGION_EU: Final = "eu"
REGION_SG: Final = "sg"
REGION_NA: Final = "na"
DEFAULT_REGION: Final = REGION_EU
REGION_HOSTS: Final[dict[str, str]] = {
    REGION_EU: "openapi-fk.easy4ip.com",
    REGION_SG: "openapi-sg.easy4ip.com",
    REGION_NA: "openapi-or.easy4ip.com",
}

# Polling. One API call per robot per poll; the free tier is 30,000 calls per
# month per App ID (about 12k/month with this plan).
SCAN_INTERVAL_IDLE: Final = 300  # seconds, docked / idle
SCAN_INTERVAL_ACTIVE: Final = 30  # seconds, robot is doing something
COMMAND_REFRESH_DELAY: Final = 3  # seconds, one refresh after any command
COMMAND_BOOST_SECONDS: Final = 120  # keep fast polling this long after a command
# A failed poll keeps the last good data. Entities become unavailable only after
# this many failed polls in a row, or when the last good poll is older than
# MAX_STALE_SECONDS, whichever comes first. A failure is retried after
# RETRY_INTERVAL seconds, not after the idle interval.
FAILURES_BEFORE_UNAVAILABLE: Final = 3
MAX_STALE_SECONDS: Final = 15 * 60
RETRY_INTERVAL: Final = 60
# A commanded value may be shown for at most this long while the cloud reports a
# different value for the same setting; after that the cloud value wins, so a
# command the robot ignores becomes visible.
COMMAND_GRACE_SECONDS: Final = 30

# work_status values during which the robot is considered active.
ACTIVE_WORK_STATUSES: Final = frozenset({2, 4, 5, 7, 8, 9, 10, 11, 12, 13})

# ---------------------------------------------------------------------------
# Things-model identifiers (matched case-insensitively, stored lower-case).
# Refs are always resolved at runtime from the device's things model.
# ---------------------------------------------------------------------------
SERVICE_CLEAN_CONTROL: Final = "clean_control"
SERVICE_LOCATION: Final = "location"
SERVICE_DUST: Final = "dust"
SERVICE_WIND_WATER: Final = "windwater"  # sets suction and water together (the way to set levels)

INPUT_STATUS: Final = "status"
INPUT_MODE: Final = "mode"
INPUT_PLAN: Final = "plan"
INPUT_WIND: Final = "wind"
INPUT_WATER: Final = "water"

PROP_WORK_STATUS: Final = "work_status"
PROP_POWER: Final = "power"
PROP_BACK_CHARGING: Final = "back_charging"
PROP_CLEAN_AREA: Final = "clean_area"
PROP_CLEAN_TIME: Final = "clean_time"
PROP_WIND_POWER: Final = "windpower"
PROP_WATER: Final = "water"
PROP_MOP_ENABLE: Final = "mopenable"
PROP_KEEP_CLEAN: Final = "keep_clean"
PROP_AUTO_DUST: Final = "auto_dust"
PROP_RUG_PRESS: Final = "rug_press"
PROP_LIFE: Final = "life"
PROP_ERROR_INFO: Final = "errorinfo"
PROP_WIFI: Final = "wifi_rssi_sdj"

LIFE_SIDE_BRUSH: Final = "brush_time"
LIFE_MAIN_BRUSH: Final = "main_brush_time"
LIFE_FILTER: Final = "rosebox_time"
LIFE_SENSORS: Final = "sensor_time"

ERROR_CODE: Final = "errcode"
ERROR_VALID: Final = "valid"

# Identifiers that are polled, in this order. Everything else (map, trail,
# zone and timer data) is large and is never polled.
POLL_IDENTIFIERS: Final[tuple[str, ...]] = (
    PROP_WORK_STATUS,
    PROP_POWER,
    PROP_BACK_CHARGING,
    PROP_CLEAN_AREA,
    PROP_CLEAN_TIME,
    PROP_WIND_POWER,
    PROP_WATER,
    PROP_MOP_ENABLE,
    PROP_KEEP_CLEAN,
    PROP_AUTO_DUST,
    PROP_RUG_PRESS,
    PROP_LIFE,
    PROP_ERROR_INFO,
    PROP_WIFI,
)

# Documented guard: these must never end up in the poll list.
NEVER_POLL_IDENTIFIERS: Final = frozenset(
    {
        "clean_params",
        "areainfoarray",
        "mapconfig",
        "trailpath",
        "map_switch",
        "specify_clean",
    }
)

# clean_control "status" input values
CLEAN_PAUSE: Final = 0
CLEAN_START: Final = 1
CLEAN_CONTINUE: Final = 2
CLEAN_STOP: Final = 3
# clean_control "mode" / "plan" used when starting: whole house, general plan
CLEAN_MODE_WHOLE_HOUSE: Final = 0
CLEAN_PLAN_GENERAL: Final = 0

# ---------------------------------------------------------------------------
# Documented fallback: the refs of the tested Imou RV-L11-A. They are used ONLY
# when a things model declares one of these identifiers without a usable ref.
# They never cause an entity to be created; the model decides that.
# ---------------------------------------------------------------------------
FALLBACK_PROPERTY_REFS: Final[dict[str, str]] = {
    PROP_WORK_STATUS: "12300",
    PROP_POWER: "11600",
    PROP_BACK_CHARGING: "1019",
    PROP_CLEAN_AREA: "1020",
    PROP_CLEAN_TIME: "1021",
    PROP_WIND_POWER: "1027",
    PROP_WATER: "1028",
    PROP_MOP_ENABLE: "1029",
    PROP_KEEP_CLEAN: "11700",
    PROP_AUTO_DUST: "11800",
    PROP_RUG_PRESS: "11900",
    PROP_LIFE: "12200",
    PROP_ERROR_INFO: "40500",
    PROP_WIFI: "11300",
    # struct members
    LIFE_SIDE_BRUSH: "12202",
    LIFE_MAIN_BRUSH: "12204",
    LIFE_FILTER: "12206",
    LIFE_SENSORS: "12208",
    ERROR_CODE: "40501",
    ERROR_VALID: "40502",
}
FALLBACK_SERVICE_REFS: Final[dict[str, str]] = {
    SERVICE_CLEAN_CONTROL: "2700",
    SERVICE_LOCATION: "20400",
    SERVICE_DUST: "2300",
    SERVICE_WIND_WATER: "3100",
}
FALLBACK_INPUT_REFS: Final[dict[str, dict[str, str]]] = {
    SERVICE_CLEAN_CONTROL: {
        INPUT_STATUS: "2701",
        INPUT_MODE: "2702",
        INPUT_PLAN: "2703",
    },
    SERVICE_WIND_WATER: {
        INPUT_WATER: "3102",
        INPUT_WIND: "3103",
    },
}

# ---------------------------------------------------------------------------
# Known enum values -> translation keys (see strings.json / translations).
# Values that are not listed here show up as "unknown_<n>".
# ---------------------------------------------------------------------------
WORK_STATUS_KEYS: Final[dict[int, str]] = {
    0: "standby",
    1: "paused",
    2: "returning",
    3: "charging",
    4: "relocating",
    5: "remote_control",
    6: "error",
    7: "room_clean",
    8: "zone_clean",
    9: "spot_clean",
    10: "go_to",
    11: "emptying_bin",
    12: "whole_house_clean",
    13: "return_paused",
}
ERROR_KEYS: Final[dict[int, str]] = {
    0: "none",
    1: "wheel_stuck",
    2: "fan",
    3: "no_water_tank",
    4: "water_low",
    5: "cliff",
    6: "lidar_cover_stuck",
    7: "lifted",
    8: "in_no_go_zone",
    9: "battery",
    10: "lidar",
    11: "main_brush",
}
WIFI_KEYS: Final[dict[int, str]] = {
    0: "abnormal",
    1: "no_network",
    2: "poor",
    3: "fair",
    4: "good",
}

# ---------------------------------------------------------------------------
# Suction and water are levels, not percentages. The Imou app shows 3 levels for
# the L11-A (the things model declares a misleading range of 0-100):
#   WindPower 1/2/3 = quiet / standard / max (moon, standard, fan icons)
#   Water     1/2/3 = low / medium / high
# Any other raw value (0, 4, 99, ...) is shown as "unknown_<n>". Other models may
# use other scales; add them here per identifier.
# On the L11-A the levels must be set with the WindWater service (it always gets
# both levels): a plain setIotDeviceProperties write is accepted (code 0) but
# ignored by the robot. Only a model without that service gets property writes.
# ---------------------------------------------------------------------------
LEVEL_KEYS: Final[dict[str, dict[int, str]]] = {
    PROP_WIND_POWER: {1: "quiet", 2: "standard", 3: "max"},
    PROP_WATER: {1: "low", 2: "medium", 3: "high"},
}
# Level sent for the other setting when neither the cloud nor a command knows it.
DEFAULT_LEVEL: Final = 2
