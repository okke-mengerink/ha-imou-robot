"""Constants for the Imou Vacuum (Open Platform) integration.

This module is deliberately free of Home Assistant imports so that the pure
parts of the integration (API client, things-model parsing) can be unit tested
without Home Assistant installed.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "imou_vacuum"
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
INPUT_PARTITION_AREA: Final = "partition_area"  # clean_control: JSON text list of room ids
OUTPUT_TIMER: Final = "timer"  # clean_list: the schedules

PROP_WORK_STATUS: Final = "work_status"
PROP_POWER: Final = "power"
PROP_POWER_STATUS: Final = "power_status"  # RV1 Pro Max: 0 normal, 1 charging, 2 abnormal
PROP_BACK_CHARGING: Final = "back_charging"
PROP_BACK_STATION: Final = "back_station"  # RV1 Pro Max: return to base station
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

# The L11-A reports the consumable counters in MINUTES used, although its things
# model declares the unit "h" (after a 34 minute cleaning run all four read 34).
# Raw units per hour, per member identifier of the "life" struct; the sensors show
# hours (raw / this value). Another model that really reports hours would use 1.
LIFE_RAW_PER_HOUR: Final[dict[str, int]] = {
    LIFE_SIDE_BRUSH: 60,
    LIFE_MAIN_BRUSH: 60,
    LIFE_FILTER: 60,
    LIFE_SENSORS: 60,
}

ERROR_CODE: Final = "errcode"
ERROR_VALID: Final = "valid"

# Identifiers that are polled, in this order. Everything else (map, trail,
# zone and timer data) is large and is never polled.
POLL_IDENTIFIERS: Final[tuple[str, ...]] = (
    PROP_WORK_STATUS,
    PROP_POWER,
    PROP_POWER_STATUS,
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
        "partition_area",
        "map_name",
        "sequence",
        "area_clean_plan",
        "curpath_data",
        "virtual_area",
    }
)

# Read-only probe (diagnostics only): properties whose real format is not known
# yet (room cleaning, map, schedules). They are never polled and never written.
PROP_CLEAN_PARAMS: Final = "clean_params"
PROP_AREA_INFO: Final = "areainfoarray"
PROP_MAP_CONFIG: Final = "mapconfig"
PROP_MAP_SWITCH: Final = "map_switch"
PROP_PARTITION_AREA: Final = "partition_area"  # RV1 Pro Max (array)
PROP_SEQUENCE: Final = "sequence"  # RV1 Pro Max (array)
PROP_AREA_CLEAN_PLAN: Final = "area_clean_plan"  # RV1 Pro Max (array)
MAP_NAME: Final = "map_name"
MAP_URL: Final = "map_url"
AREA_URL: Final = "area_url"
SERVICE_CLEAN_LIST: Final = "clean_list"
PROBE_IDENTIFIERS: Final[tuple[str, ...]] = (
    PROP_CLEAN_PARAMS,
    PROP_AREA_INFO,
    PROP_MAP_CONFIG,
    PROP_MAP_SWITCH,
    PROP_PARTITION_AREA,
    MAP_NAME,
    PROP_SEQUENCE,
    PROP_AREA_CLEAN_PLAN,
)

# clean_control "status" input values
CLEAN_PAUSE: Final = 0
CLEAN_START: Final = 1
CLEAN_CONTINUE: Final = 2
CLEAN_STOP: Final = 3
# clean_control "mode" / "plan" used when starting: whole house, general plan
CLEAN_MODE_WHOLE_HOUSE: Final = 0
CLEAN_MODE_ROOMS: Final = 1
CLEAN_PLAN_GENERAL: Final = 0

# Rooms and schedules. The robot reports no rooms or map; room ids and the room
# type labels only appear in the schedules (clean_list). They are refreshed this
# often (seconds); a failed fetch is retried sooner.
CONF_ROOM_NAMES: Final = "room_names"  # config entry option: {device id: {room id: name}}
SCHEDULE_REFRESH_SECONDS: Final = 3600
SCHEDULE_RETRY_SECONDS: Final = 900
SCHEDULE_MIN_REFRESH_SECONDS: Final = 60  # on-demand reads are cached this long

# ---------------------------------------------------------------------------
# Documented fallback: the refs of the tested Imou RV-L11-A. They are used ONLY
# when a things model declares one of these identifiers without a usable ref.
# They never cause an entity to be created; the model decides that.
# ---------------------------------------------------------------------------
FALLBACK_PROPERTY_REFS: Final[dict[str, str]] = {
    PROP_WORK_STATUS: "12300",
    PROP_POWER: "11600",
    PROP_POWER_STATUS: "112200",
    PROP_BACK_CHARGING: "1019",
    PROP_BACK_STATION: "119300",
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
    SERVICE_CLEAN_LIST: "2500",
}
FALLBACK_INPUT_REFS: Final[dict[str, dict[str, str]]] = {
    SERVICE_CLEAN_CONTROL: {
        INPUT_STATUS: "2701",
        INPUT_MODE: "2702",
        INPUT_PLAN: "2703",
        INPUT_PARTITION_AREA: "2704",
    },
    SERVICE_WIND_WATER: {
        INPUT_WATER: "3102",
        INPUT_WIND: "3103",
    },
}
FALLBACK_OUTPUT_REFS: Final[dict[str, dict[str, str]]] = {
    SERVICE_CLEAN_LIST: {OUTPUT_TIMER: "2501"},
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
    14: "returning_to_wash",
    15: "washing_mop",
    16: "drying",
    17: "mapping",
    18: "mapping_paused",
    19: "return_to_wash_paused",
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
# Suction levels of a model whose WindPower is an enum (RV1 Pro Max: 0 off, 1 small,
# 2 medium, 3 large, 4 max): the model's labels (Chinese) -> translation keys.
# Unknown labels become "unknown_<n>".
WIND_ENUM_LABEL_KEYS: Final[dict[str, str]] = {
    "关闭": "off",
    "小": "quiet",
    "中": "standard",
    "大": "strong",
    "最大": "max",
}
# Level sent for the other setting when neither the cloud nor a command knows it.
DEFAULT_LEVEL: Final = 2

# ---------------------------------------------------------------------------
# Label based mapping. Imou publishes no things models, but every model lists its
# enum values with a (Chinese) label. When a model has labels, the label decides
# the key, so a model that numbers its values differently still maps correctly.
# A label that is not listed here becomes "unknown_<n>". A model without labels
# uses the numeric tables above.
# ---------------------------------------------------------------------------
WORK_STATUS_LABEL_KEYS: Final[dict[str, str]] = {
    "待机中": "standby",
    "暂停中": "paused",
    "回充中": "returning",
    "充电中": "charging",
    "定位中": "relocating",
    "遥控中": "remote_control",
    "异常中": "error",
    "选区清扫中": "room_clean",
    "划区清扫中": "zone_clean",
    "定点清扫中": "spot_clean",
    "指哪到哪": "go_to",
    "集尘中": "emptying_bin",
    "全屋清扫中": "whole_house_clean",
    "回充暂停中": "return_paused",
    "回洗拖布中": "returning_to_wash",
    "拖布清洗中": "washing_mop",
    "烘干中": "drying",
    "建图中": "mapping",
    "建图暂停中": "mapping_paused",
    "回洗暂停中": "return_to_wash_paused",
}
WIFI_LABEL_KEYS: Final[dict[str, str]] = {
    "异常": "abnormal",
    "无网络": "no_network",
    "较差": "poor",
    "一般": "fair",
    "较好": "good",
}
ERROR_LABEL_KEYS: Final[dict[str, str]] = {
    "无异常": "none",
    "主轮等卡住": "wheel_stuck",
    "风机异常": "fan",
    "未检测水箱": "no_water_tank",
    "水量不足": "water_low",
    "跌落": "cliff",
    "雷达上盖卡住": "lidar_cover_stuck",
    "设备离地": "lifted",
    "设备在禁区上": "in_no_go_zone",
    "电池异常": "battery",
    "雷达异常": "lidar",
    "主刷等异常": "main_brush",
}

# work_status keys (not numbers, see above) during which the robot is active
# (fast polling) or paused (start continues instead of starting).
ACTIVE_STATUS_KEYS: Final = frozenset(
    {
        "returning",
        "relocating",
        "remote_control",
        "room_clean",
        "zone_clean",
        "spot_clean",
        "go_to",
        "emptying_bin",
        "whole_house_clean",
        "return_paused",
        "returning_to_wash",
        "mapping",
    }
)
PAUSED_STATUS_KEYS: Final = frozenset(
    {"paused", "return_paused", "mapping_paused", "return_to_wash_paused"}
)
# The same set by number, for the numeric tables (L11-A and RV1 Pro Max).
ACTIVE_WORK_STATUSES: Final = frozenset(
    number for number, key in WORK_STATUS_KEYS.items() if key in ACTIVE_STATUS_KEYS
)

# power_status: the label (and, without labels, the value) that means "charging".
CHARGING_LABEL: Final = "充电中"
CHARGING_VALUE: Final = 1
