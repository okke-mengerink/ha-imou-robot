"""Plain-Python tests for the API client (fake session, no network).

Run: python3 -I tests/test_api.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load, run_all  # noqa: E402

api = load("api")
aiohttp = sys.modules["aiohttp"]  # real aiohttp, or the stub registered by load()

APP_ID = "test-app-id-123"
APP_SECRET = "test-secret-value-456"
DEVICE_ID = "TESTDEVICE0001"


class FakeResponse:
    """Minimal aiohttp response."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: Any = None) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeSession:
    """Records requests and answers them with ``handler(method, body)``."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        method = url.rsplit("/", 1)[-1]
        self.requests.append((method, json or {}))
        status, payload = self.handler(method, json or {})
        return FakeResponse(status, payload)

    def methods(self) -> list[str]:
        return [method for method, _ in self.requests]


def ok(data: Any = None) -> tuple[int, dict[str, Any]]:
    return 200, {"result": {"code": "0", "msg": "ok", "data": data or {}}}


def err(code: str, msg: str) -> tuple[int, dict[str, Any]]:
    return 200, {"result": {"code": code, "msg": msg}}


def make_client(handler: Any) -> tuple[Any, FakeSession]:
    session = FakeSession(handler)
    return api.ImouClient(session, APP_ID, APP_SECRET, "example.invalid"), session


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_token_is_cached() -> None:
    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok-1", "expireTime": 259200})
        return ok({"properties": {"1": 1}, "status": "online"})

    client, session = make_client(handler)

    async def go() -> None:
        await client.async_get_properties("p", DEVICE_ID, ["1"])
        await client.async_get_properties("p", DEVICE_ID, ["1"])

    run(go())
    assert session.methods() == ["accessToken", "getIotDeviceProperties", "getIotDeviceProperties"]
    assert session.requests[1][1]["params"]["token"] == "tok-1"


def test_token_is_refreshed_before_expiry() -> None:
    counter = {"n": 0}

    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            counter["n"] += 1
            return ok({"accessToken": f"tok-{counter['n']}", "expireTime": 259200})
        return ok({"properties": {}, "status": "online"})

    client, session = make_client(handler)

    async def go() -> None:
        await client.async_get_properties("p", DEVICE_ID, ["1"])
        # pretend the token is within the 60 s margin of its expiry
        client._token_valid_until = time.monotonic() - 1
        await client.async_get_properties("p", DEVICE_ID, ["1"])

    run(go())
    assert session.methods().count("accessToken") == 2
    assert session.requests[-1][1]["params"]["token"] == "tok-2"


def test_expire_time_duration_and_epoch() -> None:
    assert abs(api._remaining_seconds(259200) - 259200) < 1
    assert abs(api._remaining_seconds(time.time() + 1000) - 1000) < 5
    assert api._remaining_seconds(None) == api.DEFAULT_TOKEN_LIFETIME
    assert api._remaining_seconds("garbage") == api.DEFAULT_TOKEN_LIFETIME


def test_token_error_refreshes_once_and_retries() -> None:
    state = {"tokens": 0}

    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            state["tokens"] += 1
            return ok({"accessToken": f"tok-{state['tokens']}", "expireTime": 259200})
        if body["params"]["token"] == "tok-1":
            return err("TK1002", "Token has expired")
        return ok({"properties": {"7": 3}, "status": "online"})

    client, session = make_client(handler)
    result = run(client.async_get_properties("p", DEVICE_ID, ["7"]))
    assert result.values == {"7": 3}
    assert session.methods() == [
        "accessToken",
        "getIotDeviceProperties",
        "accessToken",
        "getIotDeviceProperties",
    ]


def test_second_token_error_after_one_refresh_is_not_an_auth_error() -> None:
    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok", "expireTime": 259200})
        return err("TK1002", "Token expired or does not exist, please get token again.")

    client, session = make_client(handler)
    try:
        run(client.async_get_properties("p", DEVICE_ID, ["7"]))
    except api.ImouApiError as exc:
        # a token we just got was refused too: not a credentials problem -> no reauth
        assert not isinstance(exc, api.ImouAuthError)
        assert exc.code == "TK1002"
    else:
        raise AssertionError("expected ImouApiError")
    assert session.methods().count("accessToken") == 2  # first + exactly one refresh
    assert session.methods().count("getIotDeviceProperties") == 2


def test_token_error_classifier_has_no_false_positives() -> None:
    token_error = api._looks_like_token_error
    assert token_error("TK1002", "Token expired or does not exist, please get token again.")
    assert token_error("TK1005", "Token illegal")
    assert not token_error("TK9999", "something else")
    assert not token_error("DV1001", "device token not exist")
    assert not token_error("DV1001", "device does not exist")
    assert not token_error("OP1000", "Server error, please retry")


def test_access_token_failures_runtime_versus_validation() -> None:
    cases = [
        ("OP1000", "Server error, please retry", False, True),  # transient: no reauth at runtime
        ("SN1004", "Invalid AppID. ", True, True),  # verified live
        ("XX0001", "wrong secret or bad signature", True, True),
        ("OP1013", "Request too frequent, please retry later", False, False),  # rate limit
    ]
    for code, msg, runtime_is_auth, strict_is_auth in cases:
        client, _ = make_client(lambda m, b, code=code, msg=msg: err(code, msg))
        try:
            run(client.async_list_devices())
        except api.ImouError as exc:
            assert isinstance(exc, api.ImouAuthError) is runtime_is_auth, (code, "runtime")
        else:
            raise AssertionError("expected an error")
        try:
            run(client.async_validate())
        except api.ImouError as exc:
            assert isinstance(exc, api.ImouAuthError) is strict_is_auth, (code, "validate")
        else:
            raise AssertionError("expected an error")


def test_transport_failures_are_typed_and_leak_nothing() -> None:
    class Boom(aiohttp.ClientError):
        pass

    def raiser(exc: BaseException) -> Any:
        def handler(method: str, body: dict[str, Any]) -> tuple[int, Any]:
            raise exc

        return handler

    cases = [
        (raiser(TimeoutError()), api.ImouConnectionError, "TIMEOUT"),
        (raiser(Boom("https://secret.example/openapi?key=SECRET-KEY")), api.ImouConnectionError, "CONNECTION"),
        (lambda m, b: (200, ValueError("not json")), api.ImouApiError, "INVALID_RESPONSE"),
        (lambda m, b: (200, ["not", "a", "dict"]), api.ImouApiError, "INVALID_RESPONSE"),
        (lambda m, b: (200, {"no": "result"}), api.ImouApiError, "INVALID_RESPONSE"),
        (lambda m, b: (502, {}), api.ImouConnectionError, "HTTP502"),
    ]
    for handler, expected_type, expected_code in cases:
        client, _ = make_client(handler)
        try:
            run(client.async_validate())
        except api.ImouError as exc:
            assert type(exc) in (expected_type, api.ImouAuthError) or isinstance(exc, expected_type), (expected_code, type(exc))
            assert exc.code == expected_code, (expected_code, exc.code)
            assert "SECRET-KEY" not in str(exc) and "secret.example" not in str(exc)
            assert exc.__cause__ is None and (exc.__suppress_context__ or exc.__context__ is None)
        else:
            raise AssertionError(f"expected an error for {expected_code}")


def test_token_expiry_margin_is_60_seconds() -> None:
    client, _ = make_client(lambda m, b: ok({"accessToken": "tok", "expireTime": 259200}))
    run(client.async_validate())
    left = client._token_valid_until - time.monotonic()
    assert abs(left - (259200 - 60)) < 5, left


def test_other_error_is_typed_and_carries_only_code_and_msg() -> None:
    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok", "expireTime": 259200})
        return err("DV1001", "device does not exist")

    client, _ = make_client(handler)
    try:
        run(client.async_get_product_model("p"))
    except api.ImouApiError as exc:
        assert (exc.code, exc.msg) == ("DV1001", "device does not exist")
        assert str(exc) == "DV1001: device does not exist"
        assert not isinstance(exc, api.ImouAuthError)
    else:
        raise AssertionError("expected ImouApiError")


def test_offline_error_is_typed() -> None:
    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok", "expireTime": 259200})
        return err("XX9999", "The device is offline")

    client, _ = make_client(handler)
    try:
        run(client.async_get_properties("p", DEVICE_ID, ["1"]))
    except api.ImouDeviceOfflineError:
        pass
    else:
        raise AssertionError("expected ImouDeviceOfflineError")


def test_invalid_credentials_give_auth_error() -> None:
    client, _ = make_client(lambda m, b: err("SN1004", "appId or secret is wrong"))
    try:
        run(client.async_validate())
    except api.ImouAuthError as exc:
        assert exc.code == "SN1004"
    else:
        raise AssertionError("expected ImouAuthError")


def test_http_error_is_a_connection_error() -> None:
    client, _ = make_client(lambda m, b: (503, {}))
    try:
        run(client.async_validate())
    except api.ImouConnectionError as exc:
        assert exc.code == "HTTP503"
    else:
        raise AssertionError("expected ImouConnectionError")


def test_devices_are_paged_and_sanitised() -> None:
    sensitive = {
        "devicePassword": "SECRET-PW",
        "deviceUsername": "SECRET-USER",
        "salt": "SECRET-SALT",
        "playToken": "SECRET-PLAY",
        "playTokenV2": "SECRET-PLAY2",
        "devicePasswordV2": "SECRET-PW2",
        "upgradeInfo": {"packageUrl": "https://secret.example/fw.bin"},
    }

    def device(index: int) -> dict[str, Any]:
        return {
            "deviceId": f"DEV{index:04d}",
            "productId": "prod",
            "deviceName": f"Robot {index}",
            "deviceModel": "L11-A",
            "deviceStatus": "online",
            "deviceVersion": "1.2.3",
            "brand": "lechange",
            **sensitive,
        }

    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok", "expireTime": 259200})
        page = body["params"]["page"]
        assert body["params"]["source"] == "bindAndShare"
        assert body["params"]["pageSize"] == 50
        count = 50 if page == 1 else 10
        return ok({"deviceList": [device(page * 100 + i) for i in range(count)]})

    client, session = make_client(handler)
    devices = run(client.async_list_devices())
    assert len(devices) == 60
    assert [r[1]["params"]["page"] for r in session.requests if r[0] == "listDeviceDetailsByPage"] == [1, 2]
    blob = repr(devices)
    for value in ("SECRET-PW", "SECRET-USER", "SECRET-SALT", "SECRET-PLAY", "secret.example"):
        assert value not in blob, value
    assert devices[0].model == "L11-A" and devices[0].version == "1.2.3"


def test_nothing_secret_is_logged() -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("imou_pure.api")
    handler_obj = Capture(level=logging.DEBUG)
    old_level = logger.level
    logger.addHandler(handler_obj)
    logger.setLevel(logging.DEBUG)

    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "SECRET-ACCESS-TOKEN", "expireTime": 259200})
        if body["params"].get("token") == "SECRET-ACCESS-TOKEN" and method == "iotDeviceControl":
            return err("TK1002", "token expired")
        return ok({"properties": {"1": 1}, "status": "online"})

    try:
        client, _ = make_client(handler)

        async def go() -> None:
            await client.async_get_properties("prod", DEVICE_ID, ["1"])
            try:
                await client.async_control("prod", DEVICE_ID, "2700", {"2701": 1})
            except api.ImouError:
                pass

        run(go())
    finally:
        logger.removeHandler(handler_obj)
        logger.setLevel(old_level)

    assert records, "expected some debug logging"
    joined = "\n".join(records)
    for value in (APP_SECRET, APP_ID, "SECRET-ACCESS-TOKEN", DEVICE_ID, "2701", "sign"):
        assert value not in joined, value


def test_command_payloads() -> None:
    def handler(method: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "accessToken":
            return ok({"accessToken": "tok", "expireTime": 259200})
        return ok({"content": {"outputData": {"2706": 0}, "service": "2700"}})

    client, session = make_client(handler)

    async def go() -> None:
        await client.async_control("prod", DEVICE_ID, "2700", {"2701": 2})
        await client.async_control("prod", DEVICE_ID, "20400")
        await client.async_set_properties("prod", DEVICE_ID, {"1019": 1})

    run(go())
    control, locate, setter = (r for r in session.requests if r[0] != "accessToken")
    assert control[0] == "iotDeviceControl"
    assert control[1]["params"] == {
        "token": "tok",
        "productId": "prod",
        "deviceId": DEVICE_ID,
        "ref": "2700",
        "content": {"2701": 2},
    }
    assert locate[1]["params"]["content"] == {}
    assert setter[0] == "setIotDeviceProperties"
    assert setter[1]["params"]["properties"] == {"1019": 1}


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
