"""Async client for the Imou Open Platform (generic things-model calls).

Security notes:
* The app secret, access token and request bodies are never logged. Only the
  method name and the result ``code``/``msg`` are logged (at debug level).
* Device dictionaries from ``listDeviceDetailsByPage`` are reduced to a few safe
  fields; credentials such as ``devicePassword``, ``deviceUsername``, ``salt``,
  ``playToken`` and ``upgradeInfo.packageUrl`` are never kept.
* Errors only carry the Imou result ``code`` and ``msg``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
import logging
import time
from typing import Any
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_VERSION = "1.0"
REQUEST_TIMEOUT = 20  # seconds
TOKEN_EXPIRY_MARGIN = 60  # refresh the token this many seconds before it expires
DEFAULT_TOKEN_LIFETIME = 3600  # used when the response has no usable expireTime
DEVICE_PAGE_SIZE = 50
MAX_DEVICE_PAGES = 20  # safety guard against endless paging


class ImouError(Exception):
    """Base error. Carries only the Imou result code and message."""

    def __init__(self, code: str, msg: str) -> None:
        """Initialize the error."""
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class ImouConnectionError(ImouError):
    """The Imou cloud could not be reached (timeout, DNS, HTTP error)."""


class ImouApiError(ImouError):
    """The Imou cloud answered with a non-zero result code."""


class ImouAuthError(ImouApiError):
    """The App ID/App Secret or the access token was rejected."""


class ImouTokenError(ImouAuthError):
    """The access token is invalid or expired (the client refreshes once)."""


class ImouDeviceOfflineError(ImouApiError):
    """The device is offline."""


def sign(app_secret: str, timestamp: int, nonce: str) -> str:
    """Return the request signature for the Imou Open Platform."""
    source = f"time:{timestamp},nonce:{nonce},appSecret:{app_secret}"
    key = hashlib.sha256(app_secret.encode()).hexdigest().lower()
    digest = hmac.new(key.encode(), source.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_request_body(
    app_id: str,
    app_secret: str,
    params: Mapping[str, Any],
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a signed request body (fresh time, nonce and id unless given)."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    nonce = nonce or str(uuid.uuid4())
    return {
        "system": {
            "ver": API_VERSION,
            "appId": app_id,
            "sign": sign(app_secret, timestamp, nonce),
            "time": timestamp,
            "nonce": nonce,
        },
        "id": request_id or str(uuid.uuid4()),
        "params": dict(params),
    }


@dataclass(frozen=True, slots=True)
class ImouDevice:
    """The safe subset of a device from ``listDeviceDetailsByPage``."""

    device_id: str
    product_id: str
    name: str
    model: str
    status: str
    version: str
    brand: str

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> ImouDevice | None:
        """Pick the safe fields; everything else is dropped on purpose."""
        device_id = raw.get("deviceId")
        if not isinstance(device_id, str) or not device_id:
            return None

        def text(key: str) -> str:
            value = raw.get(key)
            return value if isinstance(value, str) else ""

        model = text("deviceModel")
        return cls(
            device_id=device_id,
            product_id=text("productId"),
            name=text("deviceName") or model or "Imou robot",
            model=model,
            status=text("deviceStatus"),
            version=text("deviceVersion"),
            brand=text("brand"),
        )


@dataclass(frozen=True, slots=True)
class ImouProperties:
    """Result of ``getIotDeviceProperties``."""

    status: str | None  # "online" / "offline" as reported by the cloud
    values: dict[str, Any]  # {ref: value}


# Verified against the live API: a bad/expired token gives TK1002
# "Token expired or does not exist, please get token again."
_TOKEN_ERROR_CODES = frozenset({"TK1002"})


def _looks_like_token_error(code: str, msg: str) -> bool:
    """Heuristic: does this error code/message mean an invalid/expired token?"""
    code = code.upper()
    if code in _TOKEN_ERROR_CODES:
        return True
    return code.startswith("TK") and "token" in msg.lower()


def _looks_like_auth_failure(code: str, msg: str) -> bool:
    """Heuristic: does an accessToken error mean the App ID/Secret/signature is wrong?

    Verified live: a wrong App ID gives SN1004 "Invalid AppID.".
    """
    text = msg.lower()
    return code.upper().startswith("SN") or any(
        word in text for word in ("appid", "app id", "secret", "sign", "credential")
    )


def _looks_like_rate_limit(msg: str) -> bool:
    text = msg.lower()
    return any(
        word in text for word in ("frequen", "limit", "quota", "exceed", "too many", "busy")
    )


def _looks_like_offline(code: str, msg: str) -> bool:
    return code.upper() == "DV1007" or "offline" in msg.lower()


def _remaining_seconds(expire_time: Any) -> float:
    """Seconds until expiry; ``expireTime`` may be a duration or an epoch time."""
    try:
        expire = float(expire_time)
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_LIFETIME
    if expire <= 0:
        return DEFAULT_TOKEN_LIFETIME
    if expire > 1_000_000_000:  # absolute unix time in seconds
        return expire - time.time()
    return expire


class ImouClient:
    """Client for the Imou Open Platform, shared by all robots of an account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        app_id: str,
        app_secret: str,
        host: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._app_id = app_id
        self._app_secret = app_secret
        self._host = host
        self._token: str | None = None
        self._token_valid_until = 0.0  # monotonic, already includes the margin
        self._token_lock = asyncio.Lock()

    # -- transport -----------------------------------------------------------
    async def _post(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Send one signed request; return ``result.data`` or raise a typed error."""
        body = build_request_body(self._app_id, self._app_secret, params)
        url = f"https://{self._host}/openapi/{method}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.post(url, json=body) as response:
                    if response.status != 200:
                        raise ImouConnectionError(f"HTTP{response.status}", method)
                    payload = await response.json(content_type=None)
        except TimeoutError:
            raise ImouConnectionError("TIMEOUT", method) from None
        except aiohttp.ClientError as err:
            raise ImouConnectionError(
                "CONNECTION", f"{method}: {type(err).__name__}"
            ) from None
        except ValueError:
            raise ImouApiError("INVALID_RESPONSE", method) from None

        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ImouApiError("INVALID_RESPONSE", method)

        code = str(result.get("code", ""))
        msg = str(result.get("msg", ""))
        _LOGGER.debug("Imou %s: code=%s msg=%s", method, code, msg)
        if code != "0":
            if _looks_like_token_error(code, msg):
                raise ImouTokenError(code, msg)
            if _looks_like_offline(code, msg):
                raise ImouDeviceOfflineError(code, msg)
            raise ImouApiError(code, msg)
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    async def _async_fetch_token(self, *, strict: bool = False) -> str:
        """Fetch and cache a new access token (caller holds the token lock).

        accessToken takes no parameters, so when ``strict`` (config flow) every
        rejection is reported as an authentication error. At runtime only errors
        that look like bad credentials are, so that a transient server error does
        not start a re-authentication.
        """
        try:
            data = await self._post("accessToken", {})
        except ImouAuthError:
            raise
        except ImouApiError as err:
            if _looks_like_rate_limit(err.msg) or err.code == "INVALID_RESPONSE":
                raise
            if strict or _looks_like_auth_failure(err.code, err.msg):
                raise ImouAuthError(err.code, err.msg) from None
            raise

        token = data.get("accessToken")
        if not isinstance(token, str) or not token:
            raise ImouApiError("INVALID_RESPONSE", "accessToken")
        remaining = _remaining_seconds(data.get("expireTime"))
        self._token = token
        self._token_valid_until = time.monotonic() + max(
            remaining - TOKEN_EXPIRY_MARGIN, 0
        )
        return token

    async def _async_get_token(self, *, invalid: str | None = None) -> str:
        """Return the cached token, fetching a new one when needed."""
        async with self._token_lock:
            if (
                self._token is not None
                and self._token != invalid
                and time.monotonic() < self._token_valid_until
            ):
                return self._token
            return await self._async_fetch_token()

    async def _call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Call an authenticated method; on a token error refresh once and retry."""
        token = await self._async_get_token()
        try:
            return await self._post(method, {"token": token, **params})
        except ImouTokenError:
            _LOGGER.debug("Imou access token rejected for %s, refreshing once", method)
        token = await self._async_get_token(invalid=token)
        try:
            return await self._post(method, {"token": token, **params})
        except ImouTokenError as err:
            # A token we just obtained was refused too: this is not a token
            # problem, so do not report it as an authentication failure.
            raise ImouApiError(err.code, err.msg) from None

    # -- public API ----------------------------------------------------------
    async def async_validate(self) -> None:
        """Check the credentials by requesting a fresh access token."""
        async with self._token_lock:
            await self._async_fetch_token(strict=True)

    async def async_list_devices(self) -> list[ImouDevice]:
        """Return all devices of the account (bound and shared), sanitised."""
        devices: list[ImouDevice] = []
        for page in range(1, MAX_DEVICE_PAGES + 1):
            data = await self._call(
                "listDeviceDetailsByPage",
                {"page": page, "pageSize": DEVICE_PAGE_SIZE, "source": "bindAndShare"},
            )
            items = data.get("deviceList")
            items = items if isinstance(items, list) else []
            for item in items:
                if isinstance(item, dict) and (device := ImouDevice.from_api(item)):
                    devices.append(device)
            if len(items) < DEVICE_PAGE_SIZE:
                break
        return devices

    async def async_get_product_model(self, product_id: str) -> dict[str, Any]:
        """Return the things model (properties, services, events) of a product."""
        return await self._call("getProductModel", {"productId": product_id})

    async def async_get_properties(
        self, product_id: str, device_id: str, refs: Iterable[str]
    ) -> ImouProperties:
        """Read the given property refs of a device."""
        data = await self._call(
            "getIotDeviceProperties",
            {"productId": product_id, "deviceId": device_id, "properties": list(refs)},
        )
        values = data.get("properties")
        status = data.get("status")
        return ImouProperties(
            status=status if isinstance(status, str) else None,
            values=values if isinstance(values, dict) else {},
        )

    async def async_set_properties(
        self, product_id: str, device_id: str, properties: Mapping[str, Any]
    ) -> None:
        """Write properties ({ref: value}) of a device."""
        await self._call(
            "setIotDeviceProperties",
            {
                "productId": product_id,
                "deviceId": device_id,
                "properties": dict(properties),
            },
        )

    async def async_control(
        self,
        product_id: str,
        device_id: str,
        ref: str,
        content: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a service of a device; ``content`` maps input refs to values."""
        return await self._call(
            "iotDeviceControl",
            {
                "productId": product_id,
                "deviceId": device_id,
                "ref": ref,
                "content": dict(content or {}),
            },
        )
