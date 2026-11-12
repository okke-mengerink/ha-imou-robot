"""Plain-Python tests: redaction and summaries of the read-only probe.

Run: python3 -I tests/test_probe.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load, run_all  # noqa: E402

const = load("const")
probe = load("probe")

SECRET_URL = "https://bucket.example.com/a/b/c/map123.bin?Signature=ABCSECRET&Expires=1"


def test_constants() -> None:
    assert const.PROBE_IDENTIFIERS == (
        "clean_params", "areainfoarray", "mapconfig", "map_switch",
        "partition_area", "map_name", "sequence", "area_clean_plan",
        "swp_allareaconfig", "swp_areaconfig", "swp_allcleanplan",
        "swp_cleansequence", "sweeper_sequence",
    )
    assert not {"curpath_data", "virtual_area"} & set(const.PROBE_IDENTIFIERS)
    # only the room sources are probed, no other (large) swp_* property
    assert {i for i in const.PROBE_IDENTIFIERS if i.startswith("swp_")} == {
        "swp_allareaconfig", "swp_areaconfig", "swp_allcleanplan", "swp_cleansequence",
    }
    assert not set(const.PROBE_IDENTIFIERS) & set(const.POLL_IDENTIFIERS)
    assert set(const.PROBE_IDENTIFIERS) <= const.NEVER_POLL_IDENTIFIERS
    assert const.SERVICE_CLEAN_LIST == "clean_list"


def test_redact_url() -> None:
    info = probe.redact_url(SECRET_URL)
    assert info == {"url": "https://bucket.example.com/…/a8aed401.bin", "has_query": True, "path_depth": 4}
    blob = json.dumps(info)
    assert "ABCSECRET" not in blob and "Expires" not in blob and "/a/b/c" not in blob
    assert probe.redact_url("http://host.example/") == {"url": "http://host.example/", "has_query": False, "path_depth": 0}
    assert probe.redact_url("https://user:pw@h.example:8443/x?y=1")["url"] == "https://h.example/…/2d711642"
    # the last segment is hashed (8 hex of sha256) so a serial or id cannot leak
    serial = probe.redact_url("https://h.example/maps/SERIAL123ABC.png?x=1")["url"]
    assert serial == "https://h.example/…/" + hashlib.sha256(b"SERIAL123ABC.png").hexdigest()[:8] + ".png"
    assert "SERIAL" not in serial
    assert probe.redact_url("https://h.example/a/SERIAL123")["url"] == (
        "https://h.example/…/" + hashlib.sha256(b"SERIAL123").hexdigest()[:8]
    )


def test_summarize_json_text() -> None:
    result = probe.summarize('{"rooms": [{"id": 1, "type": 3}]}')
    assert result == {"type": "json", "value": {"rooms": [{"id": 1, "type": 3}]}}
    assert probe.summarize("[1, 2]") == {"type": "json", "value": [1, 2]}
    text = probe.summarize("hello world")
    assert text == {"type": "text", "length": 11, "preview": "hello world"}
    # a JSON scalar or broken JSON is text
    assert probe.summarize("42")["type"] == "text"
    assert probe.summarize('{"a": ')["type"] == "text"
    long = probe.summarize("x" * 500)
    assert long["length"] == 500 and len(long["preview"]) == 120


def test_nested_redaction() -> None:
    raw = {
        "mapName": "Home",
        "rooms": [{"id": 1, "roomName": "Kitchen", "x": 1.5, "type": 3}],
        "owner": {"nickname": "Okke", "userId": "u1", "Phone": "0612345678", "email": "a@b.c"},
        "token": "TOK", "Sign": "SIG", "secret": "S", "password": "P", "sn": "SN123",
        "serialNumber": "SER", "deviceId": "DEV", "account": "ACC",
        "snapshot": "keep", "sync": 1,
        "map": SECRET_URL,
        "flag": True, "none": None, "n": 7,
    }
    out = probe.summarize(json.dumps(raw))
    assert out["type"] == "json"
    value = out["value"]
    assert value["mapName"] == "**REDACTED**"
    assert value["rooms"][0] == {"id": 1, "roomName": "**REDACTED**", "x": 1.5, "type": 3}
    assert all(v == "**REDACTED**" for v in value["owner"].values())
    for key in ("token", "Sign", "secret", "password", "sn", "serialNumber", "deviceId", "account"):
        assert value[key] == "**REDACTED**", key
    assert value["snapshot"] == "keep" and value["sync"] == 1  # "sn" only matches exactly
    assert value["map"] == {"url": "https://bucket.example.com/…/a8aed401.bin", "has_query": True, "path_depth": 4}
    assert value["flag"] is True and value["none"] is None and value["n"] == 7
    blob = json.dumps(out)
    for leak in ("Kitchen", "Okke", "0612345678", "TOK", "ABCSECRET", "SN123", "u1"):
        assert leak not in blob, leak


def test_more_sensitive_keys() -> None:
    hidden = {
        "ip": "1.2.3.4", "IP": "1.2.3.4", "mac": "aa:bb", "Ssid": "Home", "bssid": "aa",
        "accessKey": "A", "secretKey": "S", "apiKey": "K", "authorization": "x", "Auth": "y",
        "address": "street", "ipAddress": "1.2.3.4", "password": "p", "accessToken": "t", "sn": "SN",
    }
    out = probe.redact(hidden)
    assert all(value == "**REDACTED**" for value in out.values()), out
    harmless = {"description": "d", "keepClean": 1, "key": 2, "tip": 3, "mapKey": 4, "ssidCount": 5, "snapshot": 6, "mode": 7}
    assert probe.redact(harmless) == harmless
    assert probe.summarize_members({"ssid": "Home", "n": 1}) == {"ssid": "**REDACTED**", "n": 1}


def test_nested_json_string_and_embedded_urls() -> None:
    inner = json.dumps({"roomName": "Bedroom", "u": SECRET_URL})
    out = probe.summarize({"data": inner, "note": f"see {SECRET_URL} now"})
    blob = json.dumps(out)
    assert "Bedroom" not in blob and "ABCSECRET" not in blob and "Signature" not in blob
    assert out["note"] == "see https://bucket.example.com/…/a8aed401.bin now"


def test_text_preview_redacts_urls() -> None:
    out = probe.summarize(f"prefix {SECRET_URL} suffix")
    assert out["type"] == "text" and out["length"] == len(f"prefix {SECRET_URL} suffix")
    assert "ABCSECRET" not in out["preview"] and "?" not in out["preview"]
    assert out["preview"] == "prefix https://bucket.example.com/…/a8aed401.bin suffix"
    # a long URL that would be cut inside its query is still redacted first
    cut = probe.summarize("y" * 100 + " " + SECRET_URL)
    assert "Signature" not in cut["preview"] and "ABCSECRET" not in cut["preview"]


def test_lists_dicts_none() -> None:
    assert probe.summarize(None) is None
    assert probe.summarize(5) == 5 and probe.summarize(1.5) == 1.5 and probe.summarize(False) is False
    assert probe.summarize([1, {"name": "x", "k": "v"}]) == [1, {"name": "**REDACTED**", "k": "v"}]
    assert probe.summarize({}) == {} and probe.summarize([]) == []
    members = probe.summarize_members({"map_name": "Home", "map_url": SECRET_URL, "n": 3})
    assert members["map_name"] == "**REDACTED**" and members["n"] == 3
    assert members["map_url"]["type"] == "text" and "ABCSECRET" not in json.dumps(members)
    json.dumps(probe.summarize({1: object()}), default=None)  # always JSON-safe


def test_find_urls() -> None:
    raw = [
        {"map_url": "https://a.example/x?q=1", "area_url": "https://b.example/y"},
        json.dumps({"deep": ["https://c.example/z", "https://a.example/x?q=1", "not a url"]}),
        "text https://d.example/1 and https://e.example/2",
        None, 5,
    ]
    assert probe.find_urls(raw, 4) == [
        "https://a.example/x?q=1", "https://b.example/y", "https://c.example/z", "https://d.example/1",
    ]
    assert probe.find_urls(raw, 2) == ["https://a.example/x?q=1", "https://b.example/y"]
    assert probe.find_urls([], 4) == []


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
