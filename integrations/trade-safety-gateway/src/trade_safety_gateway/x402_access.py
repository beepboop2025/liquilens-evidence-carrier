"""Strict x402 v2 access entitlement for the paid Trade Safety route.

This module deliberately keeps payment identity separate from the protected
Trade Safety response.  A successful x402 settlement grants access to the exact
response bytes; it is not evidence that a trade is safe and must never be merged
into, or treated as authority for, a Trade Safety Receipt.

The intended route integration is small::

    challenge = gate.challenge(raw_body, resource=request_url)
    access = await gate.authorize(
        raw_body,
        resource=request_url,
        payment_signature=payment_signature,
    )
    if isinstance(access, CompletedAccess):
        return the cached exact bytes and ``access.response_headers``

    # Run the existing read-only check.  Call abort() if it fails before a
    # response is produced; no payment has been committed at this point.
    completed = await gate.settle(
        access,
        exact_response_bytes,
        status_code=200,
        content_type="application/json",
    )

``authorize`` performs local binding checks and a read-only facilitator verify.
``settle`` atomically journals the exact response before beginning settlement.
Once settlement may have started, every uncertain outcome fails closed and is
never retried automatically.  A settled replay returns the journaled exact bytes.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from .http_safety import cookie_free_jar

X402_VERSION = 2
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"
LIQUILENS_EXTENSION = "liquilens"
BODY_CANONICALIZATION = "python-json-sort-keys-utf8-no-nan-v1"

DEFAULT_MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_MAX_PAYMENT_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_FACILITATOR_RESPONSE_BYTES = 64 * 1024
DEFAULT_MAX_CACHED_RESPONSE_BYTES = 512 * 1024
DEFAULT_MAX_JOURNAL_ENTRIES = 10_000
DEFAULT_MAX_TERMINAL_ENTRIES = 1_000_000
DEFAULT_PROCESSING_LEASE_SECONDS = 300

_CAIP2_RE = re.compile(r"^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$")
_ATOMIC_AMOUNT_RE = re.compile(r"^[1-9][0-9]{0,77}$")
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_EVM_TRANSACTION_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SOLANA_VALUE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,128}$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_PRINTABLE_ASCII_RE = re.compile(r"^[\x21-\x7e]+$")
_EXTENSION_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")

_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "version",
        "method",
        "resource",
        "bodySha256",
        "offerSha256",
        "resourceInfoSha256",
        "requiredExtensionsSha256",
        "canonicalization",
        "quoteBinding",
    ],
    "properties": {
        "version": {"const": 1},
        "method": {"const": "POST"},
        "resource": {"type": "string", "format": "uri"},
        "bodySha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "offerSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "resourceInfoSha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "requiredExtensionsSha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "canonicalization": {"const": BODY_CANONICALIZATION},
        "quoteBinding": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}


class X402AccessError(RuntimeError):
    """Typed fail-closed error for route-level status mapping.

    ``http_status`` is a conservative suggested status.  A 402 means the client
    may present a new payment; 502 means the fixed facilitator failed before
    settlement; and 503 means settlement is potentially irreversible and needs
    reconciliation rather than an automatic retry.
    """

    def __init__(self, code: str, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class FacilitatorUnavailable(X402AccessError):
    """The fixed facilitator failed before an irreversible settle boundary."""

    def __init__(self, message: str = "fixed facilitator is unavailable") -> None:
        super().__init__("facilitator_unavailable", message, http_status=502)


class SettlementUncertain(X402AccessError):
    """Settlement may have started; reconciliation is required before retry."""

    def __init__(self, message: str = "settlement outcome is uncertain") -> None:
        super().__init__("settlement_uncertain", message, http_status=503)


class PaymentSettlementFailed(X402AccessError):
    """The facilitator returned a valid, terminal failed SettleResponse."""

    def __init__(self, *, payment_response_header: str, error_reason: str) -> None:
        super().__init__(
            "payment_settlement_failed",
            "payment settlement failed terminally",
            http_status=402,
        )
        self.payment_response_header = payment_response_header
        self.error_reason = error_reason


class SettledResponseRetired(X402AccessError):
    """Payment is terminally settled, but its exact response cache was retired."""

    def __init__(self) -> None:
        super().__init__(
            "settled_response_retired",
            "payment is already settled and cannot be charged again",
            http_status=409,
        )


class PaymentAuthorizationRetired(X402AccessError):
    """An operator closed an uncertain attempt; this authorization is terminal."""

    def __init__(self) -> None:
        super().__init__(
            "payment_authorization_retired",
            "payment authorization is terminal and cannot be reused",
            http_status=409,
        )


def _error(code: str, message: str, *, status: int = 402) -> X402AccessError:
    return X402AccessError(code, message, http_status=status)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _strict_json_object(raw: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise _error(
            "malformed_json", f"{name} must be one strict JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise _error("malformed_json", f"{name} must be one strict JSON object")
    return value


def _canonical_json(value: Any, name: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise _error("invalid_json_value", f"{name} is not bounded JSON") from exc


def _snapshot_bytes(value: bytes | bytearray | memoryview, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(value)


def canonical_body_sha256(
    body: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> str:
    """Return SHA-256 of one canonical strict-JSON request object.

    Caller-owned mutable buffers are copied before parsing, so later mutation
    cannot change the body that was payment-bound.
    """

    snapshot = _snapshot_bytes(body, "body")
    if len(snapshot) > max_bytes:
        raise _error("body_too_large", "request body exceeds the x402 byte budget")
    parsed = _strict_json_object(snapshot, "request body")
    return hashlib.sha256(_canonical_json(parsed, "request body")).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validated_https_url(value: str, name: str, *, resource: bool) -> str:
    if type(value) is not str or not value or len(value) > 2048:
        raise ValueError(f"{name} must be a bounded HTTPS URL")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ValueError(f"{name} must contain only visible ASCII")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (resource and not parsed.path.startswith("/"))
        or (not resource and parsed.path and not parsed.path.startswith("/"))
    ):
        raise ValueError(f"{name} must be an absolute HTTPS URL without userinfo/query")
    if resource and parsed.path.endswith("/"):
        raise ValueError(f"{name} must identify one exact resource, not a path prefix")
    normalized_path = parsed.path if resource else parsed.path.rstrip("/")
    if normalized_path in {"", "/"} and not resource:
        normalized_path = ""
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _bounded_ascii(value: str, name: str, *, maximum: int = 256) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or _PRINTABLE_ASCII_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{name} must be non-empty bounded visible ASCII")
    return value


def _bounded_printable_ascii(value: str, name: str, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value.strip() != value
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        raise ValueError(f"{name} must be bounded printable ASCII")
    return value


def _validate_positive_int(value: int, name: str, *, maximum: int) -> None:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")


def _validate_positive_float(value: float, name: str, *, maximum: float) -> None:
    if type(value) not in {float, int} or isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number")
    if not 0 < float(value) <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")


def _config_json_object(
    value: Mapping[str, Any], name: str, *, max_bytes: int = 32 * 1024
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    try:
        raw = _canonical_json(dict(value), name)
        if len(raw) > max_bytes:
            raise ValueError(f"{name} exceeds its JSON byte budget")
        return _strict_json_object(raw, name)
    except X402AccessError as exc:
        raise ValueError(f"{name} must be a strict JSON object") from exc


def _validate_resource_info_extra(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _config_json_object(value, "resource_info_extra", max_bytes=16 * 1024)
    allowed = {"description", "mimeType", "serviceName", "tags", "iconUrl"}
    if not set(result) <= allowed:
        raise ValueError(
            "resource_info_extra contains an unsupported ResourceInfo field"
        )
    description = result.get("description")
    if description is not None and (
        type(description) is not str
        or not description
        or len(description.encode("utf-8")) > 1024
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in description
        )
    ):
        raise ValueError("ResourceInfo description must be bounded text")
    mime_type = result.get("mimeType")
    if mime_type is not None:
        _bounded_ascii(mime_type, "ResourceInfo mimeType", maximum=128)
    service_name = result.get("serviceName")
    if service_name is not None:
        _bounded_printable_ascii(service_name, "ResourceInfo serviceName", maximum=32)
    tags = result.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not 1 <= len(tags) <= 5:
            raise ValueError("ResourceInfo tags must contain 1 to 5 unique strings")
        for tag in tags:
            _bounded_printable_ascii(tag, "ResourceInfo tag", maximum=32)
        if len(set(tags)) != len(tags):
            raise ValueError("ResourceInfo tags must contain 1 to 5 unique strings")
    icon_url = result.get("iconUrl")
    if icon_url is not None:
        if (
            type(icon_url) is not str
            or not icon_url
            or len(icon_url) > 2048
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E for character in icon_url
            )
        ):
            raise ValueError("ResourceInfo iconUrl must be a bounded HTTP(S) URL")
        try:
            parsed = urlsplit(icon_url)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(
                "ResourceInfo iconUrl must be a valid HTTP(S) URL"
            ) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("ResourceInfo iconUrl must be an absolute HTTP(S) URL")
    return result


def _validate_required_extensions(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _config_json_object(value, "required_extensions")
    if LIQUILENS_EXTENSION in result:
        raise ValueError("required_extensions cannot replace the LiquiLens binding")
    for name, extension in result.items():
        if _EXTENSION_NAME_RE.fullmatch(name) is None:
            raise ValueError("required extension name is invalid")
        if not isinstance(extension, dict) or set(extension) != {"info", "schema"}:
            raise ValueError(
                "required extension must contain exact info and schema fields"
            )
        if not isinstance(extension["info"], dict):
            raise ValueError("required extension info must be an object")
        if not isinstance(extension["schema"], dict):
            raise ValueError("required extension schema must be an object")
    return result


@dataclass(frozen=True, slots=True)
class X402Config:
    """One immutable exact-payment offer and its fixed facilitator boundary.

    ``quote_binding_key`` is required and must be a dedicated random secret.  It
    authenticates the body/resource/offer quote only; reusing a Trade Safety
    receipt HMAC or any broker/order-authority key would collapse trust domains.
    """

    resource_url: str
    facilitator_url: str
    network: str
    amount: str
    asset: str
    pay_to: str
    quote_binding_key: bytes = field(repr=False)
    scheme: str = "exact"
    max_timeout_seconds: int = 60
    offer_extra: Mapping[str, Any] = field(default_factory=dict)
    resource_info_extra: Mapping[str, Any] = field(default_factory=dict)
    required_extensions: Mapping[str, Any] = field(default_factory=dict)
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_payment_header_bytes: int = DEFAULT_MAX_PAYMENT_HEADER_BYTES
    max_facilitator_response_bytes: int = DEFAULT_MAX_FACILITATOR_RESPONSE_BYTES
    max_cached_response_bytes: int = DEFAULT_MAX_CACHED_RESPONSE_BYTES
    facilitator_timeout_seconds: float = 5.0
    facilitator_connect_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_url",
            _validated_https_url(self.resource_url, "resource_url", resource=True),
        )
        object.__setattr__(
            self,
            "facilitator_url",
            _validated_https_url(
                self.facilitator_url, "facilitator_url", resource=False
            ),
        )
        if type(self.scheme) is not str or self.scheme != "exact":
            raise ValueError("only the x402 exact scheme is supported")
        if type(self.network) is not str or _CAIP2_RE.fullmatch(self.network) is None:
            raise ValueError("network must be one exact CAIP-2 identifier")
        if (
            type(self.amount) is not str
            or _ATOMIC_AMOUNT_RE.fullmatch(self.amount) is None
        ):
            raise ValueError("amount must be a canonical positive atomic-unit integer")
        _bounded_ascii(self.asset, "asset")
        _bounded_ascii(self.pay_to, "pay_to")
        if self.network.startswith("eip155:") and (
            _EVM_ADDRESS_RE.fullmatch(self.asset) is None
            or _EVM_ADDRESS_RE.fullmatch(self.pay_to) is None
        ):
            raise ValueError("EVM asset and pay_to must be 20-byte hex addresses")
        if self.network.startswith("solana:") and (
            _SOLANA_VALUE_RE.fullmatch(self.asset) is None
            or len(self.asset) > 44
            or _SOLANA_VALUE_RE.fullmatch(self.pay_to) is None
            or len(self.pay_to) > 44
        ):
            raise ValueError("Solana asset and pay_to must be base58 public keys")
        if not isinstance(self.quote_binding_key, (bytes, bytearray, memoryview)):
            raise ValueError("quote_binding_key must be bytes-like")
        quote_binding_key = bytes(self.quote_binding_key)
        if not 32 <= len(quote_binding_key) <= 128:
            raise ValueError("quote_binding_key must contain 32 to 128 bytes")
        object.__setattr__(self, "quote_binding_key", quote_binding_key)
        _validate_positive_int(
            self.max_timeout_seconds, "max_timeout_seconds", maximum=3600
        )
        for name in (
            "max_request_bytes",
            "max_payment_header_bytes",
            "max_facilitator_response_bytes",
            "max_cached_response_bytes",
        ):
            _validate_positive_int(getattr(self, name), name, maximum=16 * 1024 * 1024)
        _validate_positive_float(
            self.facilitator_timeout_seconds,
            "facilitator_timeout_seconds",
            maximum=60.0,
        )
        _validate_positive_float(
            self.facilitator_connect_timeout_seconds,
            "facilitator_connect_timeout_seconds",
            maximum=float(self.facilitator_timeout_seconds),
        )
        extra_snapshot = _config_json_object(self.offer_extra, "offer_extra")
        payment_flow = extra_snapshot.get("paymentFlow", "authorization")
        if payment_flow != "authorization":
            raise ValueError(
                "only the verify-resource-settle authorization flow is supported"
            )
        extra_snapshot["paymentFlow"] = "authorization"
        transfer_method = extra_snapshot.get("assetTransferMethod")
        if transfer_method is not None:
            _bounded_ascii(
                transfer_method, "offer_extra.assetTransferMethod", maximum=64
            )
            if not self.network.startswith("eip155:") or transfer_method != "eip3009":
                raise ValueError(
                    "only the EVM eip3009 asset transfer method is implemented"
                )
        object.__setattr__(self, "offer_extra", _freeze_json(extra_snapshot))
        resource_info = _validate_resource_info_extra(self.resource_info_extra)
        object.__setattr__(self, "resource_info_extra", _freeze_json(resource_info))
        extensions = _validate_required_extensions(self.required_extensions)
        object.__setattr__(self, "required_extensions", _freeze_json(extensions))

    def payment_requirements(self) -> dict[str, Any]:
        """Return a fresh exact offer safe for serialization or facilitator calls."""

        return {
            "scheme": self.scheme,
            "network": self.network,
            "amount": self.amount,
            "asset": self.asset,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "extra": _thaw_json(self.offer_extra),
        }

    def resource_info(self) -> dict[str, Any]:
        """Return the exact advertised ResourceInfo, including bounded metadata."""

        return {"url": self.resource_url, **_thaw_json(self.resource_info_extra)}


def encode_payment_required(payment_required: Mapping[str, Any]) -> str:
    """Encode a v2 PaymentRequired object for ``PAYMENT-REQUIRED``."""

    value = dict(payment_required)
    if (
        type(value.get("x402Version")) is not int
        or value["x402Version"] != X402_VERSION
    ):
        raise ValueError("PAYMENT-REQUIRED must use x402 v2")
    return base64.b64encode(_canonical_json(value, "PAYMENT-REQUIRED")).decode("ascii")


def encode_payment_response(payment_response: Mapping[str, Any]) -> str:
    """Encode a validated SettlementResponse for ``PAYMENT-RESPONSE``."""

    value = dict(payment_response)
    if type(value.get("success")) is not bool:
        raise ValueError("PAYMENT-RESPONSE requires a settlement outcome")
    return base64.b64encode(_canonical_json(value, "PAYMENT-RESPONSE")).decode("ascii")


def decode_payment_signature(
    value: str,
    *,
    max_header_bytes: int = DEFAULT_MAX_PAYMENT_HEADER_BYTES,
) -> dict[str, Any]:
    """Strictly decode one canonical-base64 v2 ``PAYMENT-SIGNATURE`` value."""

    if type(value) is not str or not value:
        raise _error("payment_signature_required", "PAYMENT-SIGNATURE is required")
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE is not ASCII",
            status=400,
        ) from exc
    if len(encoded) > max_header_bytes:
        raise _error(
            "payment_signature_too_large",
            "PAYMENT-SIGNATURE exceeds the fixed byte budget",
            status=413,
        )
    if (
        len(encoded) % 4 != 0
        or _BASE64_RE.fullmatch(value) is None
        or any(character.isspace() for character in value)
    ):
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE is not canonical base64",
            status=400,
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE is not canonical base64",
            status=400,
        ) from exc
    if base64.b64encode(raw) != encoded:
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE is not canonical base64",
            status=400,
        )
    if len(raw) > max_header_bytes:
        raise _error(
            "payment_signature_too_large",
            "decoded PAYMENT-SIGNATURE exceeds the fixed byte budget",
            status=413,
        )
    try:
        return _strict_json_object(raw, "PAYMENT-SIGNATURE")
    except X402AccessError as exc:
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE must encode one strict JSON object",
            status=400,
        ) from exc


def extract_payment_signature(
    raw_headers: Iterable[tuple[bytes, bytes]],
    *,
    max_header_bytes: int = DEFAULT_MAX_PAYMENT_HEADER_BYTES,
) -> str | None:
    """Extract exactly zero or one PAYMENT-SIGNATURE from ASGI raw headers."""

    values: list[bytes] = []
    for name, value in raw_headers:
        if name.lower() == PAYMENT_SIGNATURE_HEADER.lower().encode("ascii"):
            values.append(value)
    if not values:
        return None
    if len(values) != 1:
        raise _error(
            "duplicate_payment_signature",
            "exactly one PAYMENT-SIGNATURE is allowed",
            status=400,
        )
    if len(values[0]) > max_header_bytes:
        raise _error(
            "payment_signature_too_large",
            "PAYMENT-SIGNATURE exceeds the fixed byte budget",
            status=413,
        )
    try:
        return values[0].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(
            "malformed_payment_signature",
            "PAYMENT-SIGNATURE is not ASCII",
            status=400,
        ) from exc


@dataclass(frozen=True, slots=True)
class PaymentChallenge:
    """Deterministic 402 challenge bound to one canonical body and resource."""

    resource: str
    body_sha256: str
    payment_required_bytes: bytes = field(repr=False)
    header_value: str = field(repr=False)

    @property
    def payment_required(self) -> dict[str, Any]:
        return _strict_json_object(self.payment_required_bytes, "PAYMENT-REQUIRED")

    @property
    def response_headers(self) -> dict[str, str]:
        return {PAYMENT_REQUIRED_HEADER: self.header_value}


@dataclass(frozen=True, slots=True)
class PaymentIdentity:
    """Settlement identity only; never a Trade Safety Receipt or trade authority."""

    x402_version: int
    scheme: str
    network: str
    amount: str
    asset: str
    pay_to: str
    payer: str
    transaction: str
    resource: str
    body_sha256: str
    payment_id: str
    payment_payload_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "x402Version": self.x402_version,
            "scheme": self.scheme,
            "network": self.network,
            "amount": self.amount,
            "asset": self.asset,
            "payTo": self.pay_to,
            "payer": self.payer,
            "transaction": self.transaction,
            "resource": self.resource,
            "bodySha256": self.body_sha256,
            "paymentId": self.payment_id,
            "paymentPayloadSha256": self.payment_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class PreparedAccess:
    """Verified access awaiting protected work and settlement."""

    payment_id: str
    payment_payload_sha256: str
    payer: str
    resource: str
    body_sha256: str
    _payment_payload_bytes: bytes = field(repr=False)
    _requirements_bytes: bytes = field(repr=False)
    _owner_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompletedAccess:
    """Settled entitlement plus the exact independently produced response bytes."""

    response_body: bytes
    status_code: int
    content_type: str
    payment_response_header: str = field(repr=False)
    payment_identity: PaymentIdentity
    cached: bool = False

    @property
    def response_headers(self) -> dict[str, str]:
        return {PAYMENT_RESPONSE_HEADER: self.payment_response_header}


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    """Redacted operator view of one uncertain settlement.

    The raw PaymentPayload, signature, payer, payee, amount, cached safety bytes,
    and facilitator response are intentionally excluded.  ``transaction`` is
    exposed only when a bounded facilitator result supplied a network transaction
    identifier, such as the standard ``settlement_pending`` response.
    """

    payment_id: str
    state: str
    body_sha256: str
    resource: str
    settlement_started_at_ns: int | None
    result_observed: bool
    result_code: str | None
    network: str | None
    transaction: str | None


@dataclass(frozen=True, slots=True)
class JournalCounts:
    """Redacted capacity counters for operations and admission monitoring."""

    total: int
    processing: int
    settling: int
    cached_settled: int
    tombstones: int


@dataclass(frozen=True, slots=True)
class _ReconciliationMaterial:
    payment_id: str
    body_sha256: str
    resource: str
    payer: str
    payment_payload: bytes
    payment_requirements: bytes
    response_body: bytes
    response_sha256: str
    status_code: int
    content_type: str
    settlement_result: bytes | None


@dataclass(frozen=True, slots=True)
class _StableAuthorization:
    payment_id: str
    payment_payload_sha256: str
    payment_payload_bytes: bytes
    payment_requirements_bytes: bytes


class Facilitator(Protocol):
    """Minimal injectable x402 facilitator interface used by the access gate."""

    async def verify(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    async def settle(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    async def aclose(self) -> None: ...


class HttpxFacilitator:
    """HTTPS-only fixed facilitator with bounded, uncompressed JSON responses."""

    def __init__(
        self,
        config: X402Config,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._verify_url = f"{config.facilitator_url}/verify"
        self._settle_url = f"{config.facilitator_url}/settle"
        timeout = httpx.Timeout(
            config.facilitator_timeout_seconds,
            connect=config.facilitator_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            cookies=cookie_free_jar(),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
                "User-Agent": "liquilens-trade-safety-gateway/x402-v2",
            },
        )

    async def _post(
        self,
        url: str,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if url not in {self._verify_url, self._settle_url}:
            raise FacilitatorUnavailable("facilitator destination is not fixed")
        envelope = {
            "x402Version": X402_VERSION,
            "paymentPayload": dict(payment_payload),
            "paymentRequirements": dict(payment_requirements),
        }
        content = _canonical_json(envelope, "facilitator request")
        request = self._client.build_request("POST", url, content=content)
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(self._config.facilitator_timeout_seconds):
                response = await self._client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                )
                if response.status_code != 200:
                    raise FacilitatorUnavailable("facilitator did not return HTTP 200")
                content_types = response.headers.get_list("content-type")
                if len(content_types) != 1:
                    raise FacilitatorUnavailable(
                        "facilitator returned an ambiguous Content-Type"
                    )
                media_type = content_types[0].split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    raise FacilitatorUnavailable("facilitator did not return JSON")
                encodings = response.headers.get_list("content-encoding")
                if len(encodings) > 1 or (
                    encodings and encodings[0].strip().lower() not in {"", "identity"}
                ):
                    raise FacilitatorUnavailable(
                        "compressed facilitator responses are forbidden"
                    )
                lengths = response.headers.get_list("content-length")
                if len(lengths) > 1:
                    raise FacilitatorUnavailable(
                        "facilitator returned ambiguous Content-Length"
                    )
                if lengths:
                    try:
                        declared = int(lengths[0])
                    except ValueError as exc:
                        raise FacilitatorUnavailable(
                            "facilitator Content-Length is invalid"
                        ) from exc
                    if (
                        declared < 0
                        or declared > self._config.max_facilitator_response_bytes
                    ):
                        raise FacilitatorUnavailable(
                            "facilitator response exceeds the byte budget"
                        )
                chunks: list[bytes] = []
                total = 0
                if response.is_stream_consumed:
                    chunks.append(response.content)
                    total = len(response.content)
                    if total > self._config.max_facilitator_response_bytes:
                        raise FacilitatorUnavailable(
                            "facilitator response exceeds the byte budget"
                        )
                else:
                    async for chunk in response.aiter_raw():
                        total += len(chunk)
                        if total > self._config.max_facilitator_response_bytes:
                            raise FacilitatorUnavailable(
                                "facilitator response exceeds the byte budget"
                            )
                        chunks.append(chunk)
                try:
                    return _strict_json_object(b"".join(chunks), "facilitator response")
                except X402AccessError as exc:
                    raise FacilitatorUnavailable(
                        "facilitator response was not strict JSON"
                    ) from exc
        except FacilitatorUnavailable:
            raise
        except (TimeoutError, httpx.HTTPError, OSError) as exc:
            raise FacilitatorUnavailable() from exc
        finally:
            if response is not None:
                await response.aclose()

    async def verify(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self._post(
            self._verify_url,
            payment_payload=payment_payload,
            payment_requirements=payment_requirements,
        )

    async def settle(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self._post(
            self._settle_url,
            payment_payload=payment_payload,
            payment_requirements=payment_requirements,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class SQLiteSettlementJournal:
    """Durable anti-resettlement journal with a bounded hot-response cache.

    ``max_entries`` bounds active rows plus exact response caches.  When capacity
    is needed, a settled response is retired to a small permanent tombstone; its
    payment identity is never deleted or made payable again.
    ``max_terminal_entries`` reserves capacity across active rows, cached
    settlements, and tombstones, refusing new payment identities at capacity
    because exact anti-replay identity cannot be safely discarded.

    ``processing`` leases use persisted wall time solely to reclaim work known to
    precede settlement.  Owner-token compare-and-swap prevents a clock jump from
    allowing two settlers.  ``settling`` never has a lease and is operator-only.
    """

    _TABLE = "x402_access_journal_v1"

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int = DEFAULT_MAX_JOURNAL_ENTRIES,
        max_terminal_entries: int = DEFAULT_MAX_TERMINAL_ENTRIES,
        processing_lease_seconds: int = DEFAULT_PROCESSING_LEASE_SECONDS,
        max_cached_response_bytes: int = DEFAULT_MAX_CACHED_RESPONSE_BYTES,
        max_metadata_bytes: int = DEFAULT_MAX_FACILITATOR_RESPONSE_BYTES,
        clock_ns: Callable[[], int] | None = None,
    ) -> None:
        _validate_positive_int(max_entries, "max_entries", maximum=1_000_000)
        _validate_positive_int(
            max_terminal_entries, "max_terminal_entries", maximum=10_000_000
        )
        _validate_positive_int(
            processing_lease_seconds,
            "processing_lease_seconds",
            maximum=86_400,
        )
        _validate_positive_int(
            max_cached_response_bytes,
            "max_cached_response_bytes",
            maximum=16 * 1024 * 1024,
        )
        _validate_positive_int(
            max_metadata_bytes, "max_metadata_bytes", maximum=1024 * 1024
        )
        path_text = str(path)
        if not path_text or "\x00" in path_text:
            raise ValueError("journal path must be non-empty")
        if clock_ns is not None and not callable(clock_ns):
            raise ValueError("clock_ns must be callable")
        self._max_entries = max_entries
        self._max_terminal_entries = max_terminal_entries
        self._processing_lease_ns = processing_lease_seconds * 1_000_000_000
        self._max_cached_response_bytes = max_cached_response_bytes
        self._max_metadata_bytes = max_metadata_bytes
        self._clock_ns = clock_ns or time.time_ns
        self._lock = threading.RLock()
        self._path = None if path_text == ":memory:" else Path(path_text)
        if self._path is not None:
            self._prepare_private_file(self._path)
            self._harden_sqlite_files()
        self._connection = sqlite3.connect(
            path_text,
            timeout=1.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 1000")
        secure_delete = self._connection.execute(
            "PRAGMA secure_delete = ON"
        ).fetchone()[0]
        if secure_delete != 1:
            self._connection.close()
            raise ValueError("journal storage must support SQLite secure deletion")
        if path_text != ":memory:":
            journal_mode = self._connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()[0]
            if type(journal_mode) is not str or journal_mode.lower() != "wal":
                self._connection.close()
                raise ValueError("journal storage must support SQLite WAL mode")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._TABLE} (
                payment_key TEXT PRIMARY KEY,
                body_sha256 TEXT NOT NULL,
                resource TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('processing', 'settling', 'settled')
                ),
                owner_token TEXT,
                payer TEXT NOT NULL,
                payment_payload BLOB,
                payment_payload_sha256 TEXT,
                payment_requirements BLOB,
                payment_requirements_sha256 TEXT,
                lease_expires_at_ns INTEGER,
                response_body BLOB,
                response_sha256 TEXT,
                status_code INTEGER,
                content_type TEXT,
                settlement_started_at_ns INTEGER,
                settlement_result BLOB,
                settlement_result_sha256 TEXT,
                settlement_result_at_ns INTEGER,
                payment_response TEXT,
                identity_json BLOB,
                terminal_reason TEXT,
                updated_at_ns INTEGER NOT NULL
            ) WITHOUT ROWID
            """
        )
        migrations = {
            "payment_payload": "BLOB",
            "payment_payload_sha256": "TEXT",
            "payment_requirements": "BLOB",
            "payment_requirements_sha256": "TEXT",
            "lease_expires_at_ns": "INTEGER",
            "response_sha256": "TEXT",
            "settlement_started_at_ns": "INTEGER",
            "settlement_result": "BLOB",
            "settlement_result_sha256": "TEXT",
            "settlement_result_at_ns": "INTEGER",
            "terminal_reason": "TEXT",
        }
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({self._TABLE})")
        }
        for name, declaration in migrations.items():
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE {self._TABLE} ADD COLUMN {name} {declaration}"
                )
        incompatible_rows = int(
            self._connection.execute(
                f"""
                SELECT COUNT(*) FROM {self._TABLE}
                WHERE payment_payload_sha256 IS NULL
                   OR (
                       state = 'processing'
                       AND (
                           payment_payload IS NULL
                           OR payment_requirements IS NULL
                           OR payment_requirements_sha256 IS NULL
                           OR lease_expires_at_ns IS NULL
                       )
                   )
                   OR (
                       state = 'settling'
                       AND (
                           payment_payload IS NULL
                           OR payment_requirements IS NULL
                           OR payment_requirements_sha256 IS NULL
                           OR response_body IS NULL
                           OR response_sha256 IS NULL
                           OR settlement_started_at_ns IS NULL
                       )
                   )
                   OR (
                       state = 'settled'
                       AND (
                           terminal_reason IS NULL
                           OR terminal_reason NOT IN (
                               'settled',
                               'settled_response_retired',
                               'payment_authorization_retired',
                               'payment_settlement_failed'
                           )
                           OR (
                               terminal_reason = 'settled'
                               AND (
                                   response_body IS NULL
                                   OR response_sha256 IS NULL
                                   OR payment_response IS NULL
                                   OR identity_json IS NULL
                               )
                           )
                       )
                   )
                """
            ).fetchone()[0]
        )
        if incompatible_rows:
            self._harden_sqlite_files()
            self._connection.close()
            raise _error(
                "journal_migration_required",
                "existing x402 journal requires an explicit operator migration",
                status=503,
            )
        self._connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS x402_access_state_updated_v1
            ON {self._TABLE} (state, updated_at_ns)
            """
        )
        self._harden_sqlite_files()

    @staticmethod
    def _prepare_private_file(path: Path) -> None:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ValueError("journal path must be a regular writable file") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("journal path must be a regular writable file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _harden_sqlite_files(self) -> None:
        if self._path is None:
            return
        for candidate in (
            self._path,
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            try:
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(candidate, flags)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise _error(
                    "journal_permissions_invalid",
                    "journal storage permissions could not be secured",
                    status=503,
                ) from exc
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise _error(
                        "journal_permissions_invalid",
                        "journal storage is not a regular file",
                        status=503,
                    )
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)

    def _commit(self) -> None:
        self._connection.commit()
        self._harden_sqlite_files()

    def _now_ns(self) -> int:
        value = self._clock_ns()
        if type(value) is not int or value < 0 or value > 9_223_372_036_854_775_807:
            raise _error(
                "journal_clock_invalid", "journal clock is invalid", status=503
            )
        return value

    def _transaction(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _row(self, payment_key: str) -> sqlite3.Row | None:
        return self._connection.execute(
            f"SELECT * FROM {self._TABLE} WHERE payment_key = ?",
            (payment_key,),
        ).fetchone()

    @staticmethod
    def _assert_binding(
        row: sqlite3.Row,
        *,
        body_sha256: str,
        resource: str,
        payment_payload_sha256: str,
    ) -> None:
        if row["body_sha256"] != body_sha256 or row["resource"] != resource:
            raise _error(
                "journal_binding_mismatch",
                "journal entry does not match the request body and resource",
                status=503,
            )
        stored_sha = row["payment_payload_sha256"]
        if type(stored_sha) is not str and isinstance(row["payment_payload"], bytes):
            stored_sha = hashlib.sha256(row["payment_payload"]).hexdigest()
        if type(stored_sha) is not str or not hmac.compare_digest(
            stored_sha, payment_payload_sha256
        ):
            raise _error(
                "payment_payload_mismatch",
                "payment payload differs from the journaled authorization",
                status=400,
            )

    @staticmethod
    def _terminal_reason(row: sqlite3.Row) -> str:
        reason = row["terminal_reason"]
        if reason is None and row["response_body"] is not None:
            return "settled"
        if reason in {
            "settled",
            "settled_response_retired",
            "payment_authorization_retired",
            "payment_settlement_failed",
        }:
            return reason
        raise _error("journal_corrupt", "terminal journal state is invalid", status=503)

    @classmethod
    def _completed_from_row(cls, row: sqlite3.Row) -> CompletedAccess:
        reason = cls._terminal_reason(row)
        if reason == "settled_response_retired":
            raise SettledResponseRetired()
        if reason == "payment_authorization_retired":
            raise PaymentAuthorizationRetired()
        if reason == "payment_settlement_failed":
            result = row["settlement_result"]
            digest = row["settlement_result_sha256"]
            payment_response = row["payment_response"]
            if (
                not isinstance(result, bytes)
                or type(digest) is not str
                or not hmac.compare_digest(hashlib.sha256(result).hexdigest(), digest)
                or type(payment_response) is not str
                or len(payment_response) > 16 * 1024 * 1024
                or not hmac.compare_digest(
                    base64.b64encode(result).decode("ascii"), payment_response
                )
            ):
                raise _error(
                    "journal_corrupt",
                    "failed settlement journal row is invalid",
                    status=503,
                )
            failure = _strict_json_object(result, "journaled failed settlement")
            error_reason = failure.get("errorReason")
            if failure.get("success") is not False or type(error_reason) is not str:
                raise _error(
                    "journal_corrupt",
                    "failed settlement journal outcome is invalid",
                    status=503,
                )
            raise PaymentSettlementFailed(
                payment_response_header=payment_response,
                error_reason=error_reason,
            )
        response_body = row["response_body"]
        response_sha256 = row["response_sha256"]
        status_code = row["status_code"]
        content_type = row["content_type"]
        payment_response = row["payment_response"]
        identity_json = row["identity_json"]
        if not isinstance(response_body, bytes) or not isinstance(identity_json, bytes):
            raise _error(
                "journal_corrupt", "settled journal row is incomplete", status=503
            )
        if (
            type(response_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", response_sha256) is None
            or not hmac.compare_digest(
                hashlib.sha256(response_body).hexdigest(), response_sha256
            )
        ):
            raise _error(
                "journal_corrupt",
                "cached response digest does not match its exact bytes",
                status=503,
            )
        if (
            type(status_code) is not int
            or not 200 <= status_code < 300
            or type(content_type) is not str
            or not content_type
            or len(content_type) > 128
            or type(payment_response) is not str
            or len(payment_response) > 16 * 1024 * 1024
        ):
            raise _error(
                "journal_corrupt", "settled journal metadata is invalid", status=503
            )
        identity = _payment_identity_from_dict(
            _strict_json_object(identity_json, "journal payment identity")
        )
        if type(row["payer"]) is not str or not _same_payer(
            row["payer"], identity.payer, identity.network
        ):
            raise _error(
                "journal_corrupt",
                "cached payer differs from the verified journal payer",
                status=503,
            )
        return CompletedAccess(
            response_body=response_body,
            status_code=status_code,
            content_type=content_type,
            payment_response_header=payment_response,
            payment_identity=identity,
            cached=True,
        )

    def inspect(
        self,
        payment_key: str,
        *,
        body_sha256: str,
        resource: str,
        payment_payload_sha256: str,
    ) -> CompletedAccess | None:
        """Return terminal cache/state before current quote validation.

        An expired ``processing`` lease returns ``None`` so a read-only verify can
        run before atomic reclamation.  ``settling`` never becomes reclaimable.
        """

        with self._lock:
            row = self._row(payment_key)
            if row is None:
                return None
            self._assert_binding(
                row,
                body_sha256=body_sha256,
                resource=resource,
                payment_payload_sha256=payment_payload_sha256,
            )
            state = row["state"]
            if state == "settled":
                return self._completed_from_row(row)
            if state == "settling":
                raise SettlementUncertain()
            if state == "processing":
                expiry = row["lease_expires_at_ns"]
                if type(expiry) is not int or self._now_ns() >= expiry:
                    return None
                raise _error(
                    "payment_processing",
                    "this payment is already processing",
                    status=409,
                )
            raise _error("journal_corrupt", "journal state is invalid", status=503)

    def _retire_cached_rows(self, count: int, now_ns: int) -> None:
        if count <= 0:
            return
        self._connection.execute(
            f"""
            UPDATE {self._TABLE}
            SET terminal_reason = 'settled_response_retired',
                payer = '', payment_payload = NULL,
                payment_requirements = NULL,
                payment_requirements_sha256 = NULL,
                response_body = NULL, response_sha256 = NULL,
                status_code = NULL, content_type = NULL,
                settlement_result = NULL,
                settlement_result_sha256 = NULL,
                settlement_result_at_ns = NULL,
                payment_response = NULL, identity_json = NULL,
                updated_at_ns = ?
            WHERE payment_key IN (
                SELECT payment_key FROM {self._TABLE}
                WHERE state = 'settled'
                  AND COALESCE(terminal_reason, 'settled') = 'settled'
                  AND response_body IS NOT NULL
                ORDER BY updated_at_ns ASC
                LIMIT ?
            )
            """,
            (now_ns, count),
        )

    def retire_terminal_responses(
        self,
        *,
        older_than_seconds: int,
        limit: int = 100,
    ) -> int:
        """Offline-retire aged response material while retaining anti-replay IDs."""

        _validate_positive_int(
            older_than_seconds,
            "older_than_seconds",
            maximum=3_153_600_000,
        )
        _validate_positive_int(limit, "limit", maximum=100)
        now_ns = self._now_ns()
        age_ns = older_than_seconds * 1_000_000_000
        cutoff_ns = max(0, now_ns - age_ns)
        with self._lock:
            try:
                self._transaction()
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET terminal_reason = CASE
                            WHEN terminal_reason = 'payment_settlement_failed'
                            THEN 'payment_authorization_retired'
                            ELSE 'settled_response_retired'
                        END,
                        payer = '', payment_payload = NULL,
                        payment_requirements = NULL,
                        payment_requirements_sha256 = NULL,
                        response_body = NULL, response_sha256 = NULL,
                        status_code = NULL, content_type = NULL,
                        settlement_result = NULL,
                        settlement_result_sha256 = NULL,
                        settlement_result_at_ns = NULL,
                        payment_response = NULL, identity_json = NULL,
                        updated_at_ns = ?
                    WHERE payment_key IN (
                        SELECT payment_key FROM {self._TABLE}
                        WHERE state = 'settled'
                          AND COALESCE(terminal_reason, 'settled') IN (
                              'settled', 'payment_settlement_failed'
                          )
                          AND updated_at_ns <= ?
                          AND (
                              response_body IS NOT NULL
                              OR settlement_result IS NOT NULL
                              OR payment_response IS NOT NULL
                          )
                        ORDER BY updated_at_ns ASC, payment_key ASC
                        LIMIT ?
                    )
                    """,
                    (now_ns, cutoff_ns, limit),
                ).rowcount
                self._commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            if self._path is not None:
                checkpoint = self._connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if checkpoint is None or checkpoint[0] != 0:
                    raise _error(
                        "retention_checkpoint_incomplete",
                        "retired material could not be removed from the active WAL",
                        status=503,
                    )
                self._harden_sqlite_files()
            return changed

    def _hot_count(self) -> int:
        return int(
            self._connection.execute(
                f"""
                SELECT COUNT(*) FROM {self._TABLE}
                WHERE state IN ('processing', 'settling')
                   OR (
                       state = 'settled'
                       AND COALESCE(terminal_reason, 'settled') = 'settled'
                       AND response_body IS NOT NULL
                   )
                """
            ).fetchone()[0]
        )

    def claim(
        self,
        payment_key: str,
        *,
        body_sha256: str,
        resource: str,
        payer: str,
        payment_payload: bytes,
        payment_payload_sha256: str,
        payment_requirements: bytes,
    ) -> str | CompletedAccess:
        """Atomically claim/reclaim verified pre-settlement work."""

        if (
            len(payment_payload) > self._max_metadata_bytes
            or len(payment_requirements) > self._max_metadata_bytes
            or not hmac.compare_digest(
                hashlib.sha256(payment_payload).hexdigest(), payment_payload_sha256
            )
        ):
            raise _error(
                "journal_metadata_invalid",
                "payment metadata exceeds its journal boundary",
                status=503,
            )
        owner_token = secrets.token_hex(32)
        now_ns = self._now_ns()
        if now_ns > 9_223_372_036_854_775_807 - self._processing_lease_ns:
            raise _error(
                "journal_clock_invalid",
                "journal clock cannot represent the processing lease",
                status=503,
            )
        lease_expires_at_ns = now_ns + self._processing_lease_ns
        with self._lock:
            try:
                self._transaction()
                row = self._row(payment_key)
                if row is not None:
                    self._assert_binding(
                        row,
                        body_sha256=body_sha256,
                        resource=resource,
                        payment_payload_sha256=payment_payload_sha256,
                    )
                    state = row["state"]
                    if state == "settled":
                        completed = self._completed_from_row(row)
                        self._commit()
                        return completed
                    if state == "settling":
                        raise SettlementUncertain()
                    expiry = row["lease_expires_at_ns"]
                    if type(expiry) is int and now_ns < expiry:
                        raise _error(
                            "payment_processing",
                            "this payment is already processing",
                            status=409,
                        )
                    changed = self._connection.execute(
                        f"""
                        UPDATE {self._TABLE}
                        SET owner_token = ?, payer = ?, payment_payload = ?,
                            payment_payload_sha256 = ?, payment_requirements = ?,
                            payment_requirements_sha256 = ?,
                            lease_expires_at_ns = ?, updated_at_ns = ?
                        WHERE payment_key = ? AND state = 'processing'
                        """,
                        (
                            owner_token,
                            payer,
                            payment_payload,
                            payment_payload_sha256,
                            payment_requirements,
                            hashlib.sha256(payment_requirements).hexdigest(),
                            lease_expires_at_ns,
                            now_ns,
                            payment_key,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise _error(
                            "payment_processing",
                            "payment processing ownership changed",
                            status=409,
                        )
                    self._commit()
                    return owner_token

                self._connection.execute(
                    f"""
                    DELETE FROM {self._TABLE}
                    WHERE state = 'processing'
                      AND (
                          lease_expires_at_ns IS NULL
                          OR lease_expires_at_ns <= ?
                      )
                    """,
                    (now_ns,),
                )
                identity_count = int(
                    self._connection.execute(
                        f"SELECT COUNT(*) FROM {self._TABLE}"
                    ).fetchone()[0]
                )
                if identity_count >= self._max_terminal_entries:
                    raise _error(
                        "journal_terminal_capacity",
                        "payment identity capacity requires operator archival",
                        status=503,
                    )
                hot_count = self._hot_count()
                if hot_count >= self._max_entries:
                    self._retire_cached_rows(hot_count - self._max_entries + 1, now_ns)
                    hot_count = self._hot_count()
                if hot_count >= self._max_entries:
                    raise _error(
                        "journal_full",
                        "journal is full of active or uncertain payments",
                        status=503,
                    )
                self._connection.execute(
                    f"""
                    INSERT INTO {self._TABLE} (
                        payment_key, body_sha256, resource, state, owner_token,
                        payer, payment_payload, payment_payload_sha256,
                        payment_requirements, payment_requirements_sha256,
                        lease_expires_at_ns, updated_at_ns
                    ) VALUES (?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payment_key,
                        body_sha256,
                        resource,
                        owner_token,
                        payer,
                        payment_payload,
                        payment_payload_sha256,
                        payment_requirements,
                        hashlib.sha256(payment_requirements).hexdigest(),
                        lease_expires_at_ns,
                        now_ns,
                    ),
                )
                self._commit()
                return owner_token
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def begin_settlement(
        self,
        prepared: PreparedAccess,
        *,
        response_body: bytes,
        status_code: int,
        content_type: str,
    ) -> CompletedAccess | None:
        """Persist output and atomically enter the never-auto-retried state."""

        if len(response_body) > self._max_cached_response_bytes:
            raise _error(
                "response_too_large",
                "protected response exceeds the journal byte budget",
                status=502,
            )
        now_ns = self._now_ns()
        with self._lock:
            try:
                self._transaction()
                row = self._row(prepared.payment_id)
                if row is None:
                    raise _error(
                        "journal_missing", "payment claim is missing", status=503
                    )
                self._assert_binding(
                    row,
                    body_sha256=prepared.body_sha256,
                    resource=prepared.resource,
                    payment_payload_sha256=prepared.payment_payload_sha256,
                )
                if row["state"] == "settled":
                    completed = self._completed_from_row(row)
                    self._commit()
                    return completed
                if row["state"] == "settling":
                    raise SettlementUncertain()
                if row["owner_token"] != prepared._owner_token:
                    raise _error(
                        "payment_owner_mismatch",
                        "payment claim is owned by another request",
                        status=409,
                    )
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET state = 'settling', lease_expires_at_ns = NULL,
                        response_body = ?, response_sha256 = ?, status_code = ?,
                        content_type = ?, settlement_started_at_ns = ?,
                        settlement_result = NULL,
                        settlement_result_sha256 = NULL,
                        settlement_result_at_ns = NULL, updated_at_ns = ?
                    WHERE payment_key = ? AND state = 'processing'
                          AND owner_token = ?
                    """,
                    (
                        response_body,
                        hashlib.sha256(response_body).hexdigest(),
                        status_code,
                        content_type,
                        now_ns,
                        now_ns,
                        prepared.payment_id,
                        prepared._owner_token,
                    ),
                ).rowcount
                if changed != 1:
                    raise SettlementUncertain()
                self._commit()
                return None
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def record_settlement_result(
        self,
        prepared: PreparedAccess,
        result: Mapping[str, Any],
    ) -> None:
        """Persist a bounded facilitator result before interpreting it."""

        try:
            result_bytes = _canonical_json(dict(result), "settlement result")
        except Exception as exc:
            raise SettlementUncertain(
                "settlement result could not be journaled safely"
            ) from exc
        if len(result_bytes) > self._max_metadata_bytes:
            raise SettlementUncertain("settlement result exceeds journal budget")
        now_ns = self._now_ns()
        with self._lock:
            try:
                self._transaction()
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET settlement_result = ?, settlement_result_sha256 = ?,
                        settlement_result_at_ns = ?, updated_at_ns = ?
                    WHERE payment_key = ? AND state = 'settling'
                          AND owner_token = ?
                    """,
                    (
                        result_bytes,
                        hashlib.sha256(result_bytes).hexdigest(),
                        now_ns,
                        now_ns,
                        prepared.payment_id,
                        prepared._owner_token,
                    ),
                ).rowcount
                if changed != 1:
                    raise SettlementUncertain(
                        "settlement result journal ownership is uncertain"
                    )
                self._commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def complete_failed_settlement(
        self,
        prepared: PreparedAccess,
        *,
        payment_response: str,
        error_reason: str,
    ) -> None:
        """Persist one terminal failed settlement without making it repayable."""

        now_ns = self._now_ns()
        with self._lock:
            try:
                self._transaction()
                row = self._row(prepared.payment_id)
                if (
                    row is None
                    or row["state"] != "settling"
                    or row["owner_token"] != prepared._owner_token
                ):
                    raise SettlementUncertain(
                        "failed settlement journal ownership is uncertain"
                    )
                result = row["settlement_result"]
                digest = row["settlement_result_sha256"]
                if (
                    not isinstance(result, bytes)
                    or type(digest) is not str
                    or not hmac.compare_digest(
                        hashlib.sha256(result).hexdigest(), digest
                    )
                    or not hmac.compare_digest(
                        base64.b64encode(result).decode("ascii"), payment_response
                    )
                ):
                    raise SettlementUncertain(
                        "failed settlement result is not durably bound"
                    )
                parsed = _strict_json_object(result, "failed settlement result")
                if (
                    parsed.get("success") is not False
                    or parsed.get("errorReason") != error_reason
                ):
                    raise SettlementUncertain(
                        "failed settlement terminal outcome changed"
                    )
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET state = 'settled', owner_token = NULL,
                        terminal_reason = 'payment_settlement_failed', payer = '',
                        payment_payload = NULL, payment_requirements = NULL,
                        payment_requirements_sha256 = NULL,
                        lease_expires_at_ns = NULL, response_body = NULL,
                        response_sha256 = NULL, status_code = NULL,
                        content_type = NULL, payment_response = ?,
                        identity_json = NULL, updated_at_ns = ?
                    WHERE payment_key = ? AND state = 'settling'
                          AND owner_token = ?
                    """,
                    (
                        payment_response,
                        now_ns,
                        prepared.payment_id,
                        prepared._owner_token,
                    ),
                ).rowcount
                if changed != 1:
                    raise SettlementUncertain(
                        "failed settlement completion is uncertain"
                    )
                self._commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    @staticmethod
    def _result_summary(
        row: sqlite3.Row,
    ) -> tuple[bool, str | None, str | None, str | None]:
        raw = row["settlement_result"]
        digest = row["settlement_result_sha256"]
        if not isinstance(raw, bytes):
            return False, None, None, None
        if type(digest) is not str or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), digest
        ):
            return True, "invalid_result", None, None
        try:
            result = _strict_json_object(raw, "journal settlement result")
        except X402AccessError:
            return True, "invalid_result", None, None
        success = result.get("success")
        reason = result.get("errorReason")
        if success is True:
            result_code = "success"
        elif success is False and reason == "settlement_pending":
            result_code = "settlement_pending"
        elif success is False:
            result_code = "unsuccessful"
        else:
            result_code = "invalid_result"
        network = result.get("network")
        if type(network) is not str or _CAIP2_RE.fullmatch(network) is None:
            network = None
        transaction = result.get("transaction")
        try:
            if network is None or network.split(":", 1)[0] not in {
                "eip155",
                "solana",
            }:
                raise ValueError("unsupported redacted transaction namespace")
            transaction = _validate_transaction(transaction, network)
        except ValueError:
            transaction = None
        return True, result_code, network, transaction

    @classmethod
    def _reconciliation_record(cls, row: sqlite3.Row) -> ReconciliationRecord:
        observed, result_code, network, transaction = cls._result_summary(row)
        started = row["settlement_started_at_ns"]
        return ReconciliationRecord(
            payment_id=row["payment_key"],
            state="settlement_uncertain",
            body_sha256=row["body_sha256"],
            resource=row["resource"],
            settlement_started_at_ns=started if type(started) is int else None,
            result_observed=observed,
            result_code=result_code,
            network=network,
            transaction=transaction,
        )

    def reconciliation_records(
        self, *, limit: int = 100
    ) -> tuple[ReconciliationRecord, ...]:
        """List bounded redacted uncertain records; never return replay material."""

        _validate_positive_int(limit, "limit", maximum=100)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT payment_key, body_sha256, resource,
                       settlement_started_at_ns, settlement_result,
                       settlement_result_sha256, updated_at_ns
                FROM {self._TABLE}
                WHERE state = 'settling'
                ORDER BY updated_at_ns ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(self._reconciliation_record(row) for row in rows)

    def counts(self) -> JournalCounts:
        """Return non-sensitive row counts for capacity monitoring."""

        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN state = 'processing' THEN 1 ELSE 0 END)
                           AS processing,
                       SUM(CASE WHEN state = 'settling' THEN 1 ELSE 0 END)
                           AS settling,
                       SUM(CASE WHEN state = 'settled'
                                 AND COALESCE(terminal_reason, 'settled') = 'settled'
                                THEN 1 ELSE 0 END) AS cached_settled,
                       SUM(CASE WHEN state = 'settled'
                                 AND terminal_reason IN (
                                     'settled_response_retired',
                                     'payment_authorization_retired',
                                     'payment_settlement_failed'
                                 ) THEN 1 ELSE 0 END) AS tombstones
                FROM {self._TABLE}
                """
            ).fetchone()
            return JournalCounts(
                total=int(row["total"]),
                processing=int(row["processing"] or 0),
                settling=int(row["settling"] or 0),
                cached_settled=int(row["cached_settled"] or 0),
                tombstones=int(row["tombstones"] or 0),
            )

    def _reconciliation_material(self, payment_id: str) -> _ReconciliationMaterial:
        """Load private material for gate validation, never for logs or responses."""

        with self._lock:
            row = self._row(payment_id)
            if row is None or row["state"] != "settling":
                raise _error(
                    "reconciliation_not_found",
                    "uncertain settlement was not found",
                    status=404,
                )
            fields = {
                "payment_payload": row["payment_payload"],
                "payment_requirements": row["payment_requirements"],
                "response_body": row["response_body"],
            }
            digests = {
                "payment_payload": row["payment_payload_sha256"],
                "payment_requirements": row["payment_requirements_sha256"],
                "response_body": row["response_sha256"],
            }
            if any(not isinstance(value, bytes) for value in fields.values()):
                raise _error(
                    "reconciliation_material_incomplete",
                    "uncertain settlement lacks bounded reconciliation material",
                    status=503,
                )
            for name, value in fields.items():
                digest = digests[name]
                if type(digest) is not str or not hmac.compare_digest(
                    hashlib.sha256(value).hexdigest(), digest
                ):
                    raise _error(
                        "journal_corrupt",
                        "reconciliation material digest mismatch",
                        status=503,
                    )
            settlement_result = row["settlement_result"]
            if settlement_result is not None:
                result_digest = row["settlement_result_sha256"]
                if (
                    not isinstance(settlement_result, bytes)
                    or type(result_digest) is not str
                    or not hmac.compare_digest(
                        hashlib.sha256(settlement_result).hexdigest(), result_digest
                    )
                ):
                    raise _error(
                        "journal_corrupt",
                        "settlement result digest mismatch",
                        status=503,
                    )
            status_code = row["status_code"]
            content_type = row["content_type"]
            if (
                type(status_code) is not int
                or not 200 <= status_code < 300
                or type(content_type) is not str
                or not content_type
                or type(row["payer"]) is not str
            ):
                raise _error(
                    "journal_corrupt",
                    "reconciliation metadata is invalid",
                    status=503,
                )
            return _ReconciliationMaterial(
                payment_id=payment_id,
                body_sha256=row["body_sha256"],
                resource=row["resource"],
                payer=row["payer"],
                payment_payload=fields["payment_payload"],
                payment_requirements=fields["payment_requirements"],
                response_body=fields["response_body"],
                response_sha256=digests["response_body"],
                status_code=status_code,
                content_type=content_type,
                settlement_result=settlement_result,
            )

    def complete(
        self,
        prepared: PreparedAccess,
        *,
        payment_response: str,
        identity: PaymentIdentity,
    ) -> None:
        """Atomically make an exact response replayable after valid settlement."""

        self._complete(
            prepared.payment_id,
            owner_token=prepared._owner_token,
            payment_response=payment_response,
            identity=identity,
            reconciliation_result=None,
        )

    def complete_reconciled(
        self,
        payment_id: str,
        *,
        settlement_result: Mapping[str, Any],
        payment_response: str,
        identity: PaymentIdentity,
    ) -> None:
        """Atomically record and complete an operator-confirmed settlement."""

        try:
            result_bytes = _canonical_json(
                dict(settlement_result), "reconciliation result"
            )
        except Exception as exc:
            raise SettlementUncertain(
                "reconciliation result could not be journaled safely"
            ) from exc
        if len(result_bytes) > self._max_metadata_bytes:
            raise SettlementUncertain("reconciliation result exceeds journal budget")

        self._complete(
            payment_id,
            owner_token=None,
            payment_response=payment_response,
            identity=identity,
            reconciliation_result=result_bytes,
        )

    @staticmethod
    def _validate_completion_row(
        row: sqlite3.Row,
        *,
        payment_id: str,
        payment_response: str,
        identity: PaymentIdentity,
    ) -> None:
        try:
            result_bytes = row["settlement_result"]
            result_digest = row["settlement_result_sha256"]
            requirements_bytes = row["payment_requirements"]
            requirements_digest = row["payment_requirements_sha256"]
            response_body = row["response_body"]
            response_digest = row["response_sha256"]
            if (
                not isinstance(result_bytes, bytes)
                or type(result_digest) is not str
                or not hmac.compare_digest(
                    hashlib.sha256(result_bytes).hexdigest(), result_digest
                )
                or not isinstance(requirements_bytes, bytes)
                or type(requirements_digest) is not str
                or not hmac.compare_digest(
                    hashlib.sha256(requirements_bytes).hexdigest(),
                    requirements_digest,
                )
                or not isinstance(response_body, bytes)
                or type(response_digest) is not str
                or not hmac.compare_digest(
                    hashlib.sha256(response_body).hexdigest(), response_digest
                )
            ):
                raise ValueError("completion material digest mismatch")
            expected_header = base64.b64encode(result_bytes).decode("ascii")
            if not hmac.compare_digest(payment_response, expected_header):
                raise ValueError("PAYMENT-RESPONSE differs from journaled settlement")
            requirements = _strict_json_object(
                requirements_bytes, "journaled PaymentRequirements"
            )
            _validate_payment_requirements_shape(requirements)
            result = _strict_json_object(result_bytes, "journaled settlement result")
            _response, payer, transaction = _validated_settlement_terms(
                result,
                network=requirements["network"],
                amount=requirements["amount"],
                verified_payer=row["payer"],
            )
            expected_identity = (
                identity.x402_version == X402_VERSION
                and identity.scheme == requirements["scheme"]
                and identity.network == requirements["network"]
                and identity.amount == requirements["amount"]
                and identity.asset == requirements["asset"]
                and identity.pay_to == requirements["payTo"]
                and _same_payer(identity.payer, payer, identity.network)
                and secrets.compare_digest(identity.transaction, transaction)
                and identity.resource == row["resource"]
                and identity.body_sha256 == row["body_sha256"]
                and identity.payment_id == payment_id
                and type(row["payment_payload_sha256"]) is str
                and hmac.compare_digest(
                    identity.payment_payload_sha256,
                    row["payment_payload_sha256"],
                )
            )
            if not expected_identity:
                raise ValueError("payment identity differs from journaled settlement")
        except SettlementUncertain:
            raise
        except Exception as exc:
            raise SettlementUncertain(
                "settlement completion material is invalid"
            ) from exc

    def _complete(
        self,
        payment_id: str,
        *,
        owner_token: str | None,
        payment_response: str,
        identity: PaymentIdentity,
        reconciliation_result: bytes | None,
    ) -> None:
        identity_json = _canonical_json(identity.as_dict(), "payment identity")
        now_ns = self._now_ns()
        owner_clause = " AND owner_token = ?" if owner_token is not None else ""
        parameters: tuple[Any, ...] = (
            payment_response,
            identity_json,
            now_ns,
            payment_id,
        )
        if owner_token is not None:
            parameters = (*parameters, owner_token)
        with self._lock:
            try:
                self._transaction()
                row = self._row(payment_id)
                if row is None or row["state"] != "settling":
                    raise SettlementUncertain(
                        "settlement succeeded but journal state is uncertain"
                    )
                if owner_token is not None and row["owner_token"] != owner_token:
                    raise SettlementUncertain(
                        "settlement succeeded but journal ownership changed"
                    )
                if reconciliation_result is not None:
                    changed = self._connection.execute(
                        f"""
                        UPDATE {self._TABLE}
                        SET settlement_result = ?, settlement_result_sha256 = ?,
                            settlement_result_at_ns = ?, updated_at_ns = ?
                        WHERE payment_key = ? AND state = 'settling'
                        """,
                        (
                            reconciliation_result,
                            hashlib.sha256(reconciliation_result).hexdigest(),
                            now_ns,
                            now_ns,
                            payment_id,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise SettlementUncertain(
                            "reconciliation state changed before completion"
                        )
                    row = self._row(payment_id)
                    if row is None:
                        raise SettlementUncertain(
                            "reconciliation state disappeared before completion"
                        )
                self._validate_completion_row(
                    row,
                    payment_id=payment_id,
                    payment_response=payment_response,
                    identity=identity,
                )
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET state = 'settled', owner_token = NULL,
                        terminal_reason = 'settled', payment_response = ?,
                        identity_json = ?, updated_at_ns = ?
                    WHERE payment_key = ? AND state = 'settling'{owner_clause}
                    """,
                    parameters,
                ).rowcount
                if changed != 1:
                    raise SettlementUncertain(
                        "settlement succeeded but journal completion is uncertain"
                    )
                self._commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def retire_unsettled(self, payment_id: str) -> None:
        """Operator-close an attempt known not settled; authorization stays dead."""

        now_ns = self._now_ns()
        with self._lock:
            try:
                self._transaction()
                row = self._row(payment_id)
                if row is None or row["state"] != "settling":
                    raise _error(
                        "reconciliation_not_found",
                        "uncertain settlement was not found",
                        status=404,
                    )
                result_bytes = row["settlement_result"]
                if result_bytes is not None:
                    result_digest = row["settlement_result_sha256"]
                    if (
                        not isinstance(result_bytes, bytes)
                        or type(result_digest) is not str
                        or not hmac.compare_digest(
                            hashlib.sha256(result_bytes).hexdigest(), result_digest
                        )
                    ):
                        raise _error(
                            "journal_corrupt",
                            "journaled settlement result digest is invalid",
                            status=503,
                        )
                    try:
                        result = _strict_json_object(
                            result_bytes, "journaled settlement result"
                        )
                    except X402AccessError as exc:
                        raise _error(
                            "journal_corrupt",
                            "journaled settlement result is invalid",
                            status=503,
                        ) from exc
                    if type(result.get("success")) is not bool:
                        raise SettlementUncertain(
                            "journaled settlement result has invalid success state"
                        )
                    if result["success"] is True:
                        requirements_bytes = row["payment_requirements"]
                        requirements_digest = row["payment_requirements_sha256"]
                        if (
                            not isinstance(requirements_bytes, bytes)
                            or type(requirements_digest) is not str
                            or not hmac.compare_digest(
                                hashlib.sha256(requirements_bytes).hexdigest(),
                                requirements_digest,
                            )
                        ):
                            raise _error(
                                "journal_corrupt",
                                "journaled payment requirements are invalid",
                                status=503,
                            )
                        requirements = _strict_json_object(
                            requirements_bytes,
                            "journaled PaymentRequirements",
                        )
                        _validate_payment_requirements_shape(requirements)
                        _validated_settlement_terms(
                            result,
                            network=requirements["network"],
                            amount=requirements["amount"],
                            verified_payer=row["payer"],
                        )
                        raise _error(
                            "reconciliation_settlement_observed",
                            "a successful settlement is already journaled",
                            status=409,
                        )
                changed = self._connection.execute(
                    f"""
                    UPDATE {self._TABLE}
                    SET state = 'settled', owner_token = NULL,
                        terminal_reason = 'payment_authorization_retired',
                        payer = '', payment_payload = NULL,
                        payment_requirements = NULL,
                        payment_requirements_sha256 = NULL,
                        response_body = NULL, response_sha256 = NULL,
                        status_code = NULL, content_type = NULL,
                        settlement_result = NULL,
                        settlement_result_sha256 = NULL,
                        settlement_result_at_ns = NULL,
                        payment_response = NULL, identity_json = NULL,
                        updated_at_ns = ?
                    WHERE payment_key = ? AND state = 'settling'
                    """,
                    (now_ns, payment_id),
                ).rowcount
                if changed != 1:
                    raise _error(
                        "reconciliation_not_found",
                        "uncertain settlement was not found",
                        status=404,
                    )
                self._commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def abort(self, prepared: PreparedAccess) -> bool:
        """Release only a pre-settlement claim; settling rows are never removed."""

        with self._lock:
            try:
                self._transaction()
                changed = self._connection.execute(
                    f"""
                    DELETE FROM {self._TABLE}
                    WHERE payment_key = ? AND state = 'processing'
                          AND owner_token = ?
                    """,
                    (prepared.payment_id, prepared._owner_token),
                ).rowcount
                self._commit()
                return changed == 1
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _payment_identity_from_dict(value: Mapping[str, Any]) -> PaymentIdentity:
    expected = {
        "x402Version",
        "scheme",
        "network",
        "amount",
        "asset",
        "payTo",
        "payer",
        "transaction",
        "resource",
        "bodySha256",
        "paymentId",
        "paymentPayloadSha256",
    }
    if (
        set(value) != expected
        or type(value.get("x402Version")) is not int
        or value["x402Version"] != X402_VERSION
    ):
        raise _error("journal_corrupt", "payment identity shape is invalid", status=503)
    strings = {key: value[key] for key in expected - {"x402Version"}}
    if any(type(item) is not str or not item for item in strings.values()):
        raise _error(
            "journal_corrupt", "payment identity fields are invalid", status=503
        )
    return PaymentIdentity(
        x402_version=X402_VERSION,
        scheme=strings["scheme"],
        network=strings["network"],
        amount=strings["amount"],
        asset=strings["asset"],
        pay_to=strings["payTo"],
        payer=strings["payer"],
        transaction=strings["transaction"],
        resource=strings["resource"],
        body_sha256=strings["bodySha256"],
        payment_id=strings["paymentId"],
        payment_payload_sha256=strings["paymentPayloadSha256"],
    )


def _validate_resource(config: X402Config, resource: str) -> None:
    if type(resource) is not str or resource != config.resource_url:
        raise _error(
            "resource_mismatch", "request does not match the configured paid resource"
        )


def _binding_extension(
    config: X402Config, resource: str, body_sha256: str
) -> dict[str, Any]:
    resource_info = config.resource_info()
    required_extensions = _thaw_json(config.required_extensions)
    offer_sha256 = hashlib.sha256(
        _canonical_json(config.payment_requirements(), "payment requirements")
    ).hexdigest()
    unsigned_info = {
        "version": 1,
        "method": "POST",
        "resource": resource,
        "bodySha256": body_sha256,
        "offerSha256": offer_sha256,
        "resourceInfoSha256": hashlib.sha256(
            _canonical_json(resource_info, "ResourceInfo")
        ).hexdigest(),
        "requiredExtensionsSha256": hashlib.sha256(
            _canonical_json(required_extensions, "required extensions")
        ).hexdigest(),
        "canonicalization": BODY_CANONICALIZATION,
    }
    quote_binding = hmac.new(
        config.quote_binding_key,
        _canonical_json(unsigned_info, "LiquiLens quote binding"),
        hashlib.sha256,
    ).hexdigest()
    return {
        **required_extensions,
        LIQUILENS_EXTENSION: {
            "info": {**unsigned_info, "quoteBinding": quote_binding},
            "schema": _BINDING_SCHEMA,
        },
    }


def _contains_unmodified_json(actual: Any, required: Any) -> bool:
    if isinstance(required, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_unmodified_json(actual[key], value)
            for key, value in required.items()
        )
    return _canonical_json(actual, "extension value") == _canonical_json(
        required, "required extension value"
    )


def _validate_extension_echo(actual: Any, required: dict[str, Any]) -> None:
    if not isinstance(actual, dict):
        raise _error(
            "binding_extension_mismatch", "payment extensions must be an object"
        )
    if set(actual) != set(required):
        raise _error(
            "binding_extension_mismatch",
            "payment extensions must preserve the advertised extension set",
        )
    for name, expected in required.items():
        received = actual.get(name)
        if (
            not isinstance(received, dict)
            or set(received) != {"info", "schema"}
            or not isinstance(received.get("info"), dict)
            or not isinstance(received.get("schema"), dict)
            or _canonical_json(received["schema"], "extension schema")
            != _canonical_json(expected["schema"], "required extension schema")
            or not _contains_unmodified_json(received["info"], expected["info"])
        ):
            raise _error(
                "binding_extension_mismatch",
                f"required extension {name!r} was deleted or overwritten",
            )


def _validate_payment_requirements_shape(value: Any) -> bytes:
    expected = {
        "scheme",
        "network",
        "amount",
        "asset",
        "payTo",
        "maxTimeoutSeconds",
        "extra",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise _error(
            "accepted_offer_mismatch",
            "accepted offer does not have the strict x402 v2 exact shape",
        )
    if value.get("scheme") != "exact":
        raise _error("accepted_offer_mismatch", "only the exact scheme is accepted")
    network = value.get("network")
    if type(network) is not str or _CAIP2_RE.fullmatch(network) is None:
        raise _error("invalid_network", "accepted network is not CAIP-2")
    amount = value.get("amount")
    if type(amount) is not str or _ATOMIC_AMOUNT_RE.fullmatch(amount) is None:
        raise _error(
            "accepted_offer_mismatch",
            "accepted amount is not a canonical positive atomic-unit integer",
        )
    asset = value.get("asset")
    pay_to = value.get("payTo")
    try:
        _bounded_ascii(asset, "accepted asset")
        _bounded_ascii(pay_to, "accepted payTo")
    except ValueError as exc:
        raise _error(
            "accepted_offer_mismatch", "accepted asset or payTo is invalid"
        ) from exc
    if network.startswith("eip155:") and (
        _EVM_ADDRESS_RE.fullmatch(asset) is None
        or _EVM_ADDRESS_RE.fullmatch(pay_to) is None
    ):
        raise _error(
            "accepted_offer_mismatch",
            "accepted EVM asset and payTo must be 20-byte hex addresses",
        )
    if network.startswith("solana:") and (
        _SOLANA_VALUE_RE.fullmatch(asset) is None
        or len(asset) > 44
        or _SOLANA_VALUE_RE.fullmatch(pay_to) is None
        or len(pay_to) > 44
    ):
        raise _error(
            "accepted_offer_mismatch",
            "accepted Solana asset and payTo must be public keys",
        )
    timeout = value.get("maxTimeoutSeconds")
    if type(timeout) is not int or not 0 < timeout <= 3600:
        raise _error(
            "accepted_offer_mismatch", "accepted timeout is outside its boundary"
        )
    extra = value.get("extra")
    try:
        extra_snapshot = _config_json_object(extra, "accepted offer extra")
    except ValueError as exc:
        raise _error(
            "accepted_offer_mismatch", "accepted offer extra is invalid"
        ) from exc
    if extra_snapshot.get("paymentFlow") != "authorization":
        raise _error(
            "accepted_offer_mismatch",
            "accepted offer must use the authorization payment flow",
        )
    return _canonical_json(value, "accepted offer")


def _validate_resource_info_shape(value: Any, *, resource: str) -> bytes:
    if not isinstance(value, dict) or value.get("url") != resource:
        raise _error("resource_mismatch", "PaymentPayload.resource URL is not exact")
    if set(value) - {
        "url",
        "description",
        "mimeType",
        "serviceName",
        "tags",
        "iconUrl",
    }:
        raise _error(
            "resource_mismatch", "PaymentPayload.resource contains unsupported fields"
        )
    try:
        _validate_resource_info_extra(
            {key: item for key, item in value.items() if key != "url"}
        )
    except ValueError as exc:
        raise _error(
            "resource_mismatch", "PaymentPayload.resource metadata is invalid"
        ) from exc
    return _canonical_json(value, "payment resource")


def _stable_payment_authorization(
    value: dict[str, Any], *, resource: str, body_sha256: str
) -> _StableAuthorization:
    """Strictly derive replay identity without trusting the current quote config.

    The full canonical PaymentPayload digest is checked by the journal in
    addition to the stable authorization id.  Consequently an old, exact paid
    payload can survive quote rotation, while a changed extension, resource, or
    accepted offer cannot retrieve its cached response.
    """

    expected_keys = {"x402Version", "resource", "accepted", "payload", "extensions"}
    if set(value) != expected_keys:
        raise _error(
            "invalid_payment_payload_shape",
            "PAYMENT-SIGNATURE must contain the strict x402 v2 fields",
        )
    if type(value.get("x402Version")) is not int or value["x402Version"] != 2:
        raise _error("invalid_x402_version", "only x402 v2 is accepted")
    _validate_resource_info_shape(value.get("resource"), resource=resource)
    requirements_bytes = _validate_payment_requirements_shape(value.get("accepted"))
    scheme_payload = value.get("payload")
    if not isinstance(scheme_payload, dict) or not scheme_payload:
        raise _error(
            "invalid_payment_payload", "scheme payload must be a non-empty object"
        )
    _canonical_json(scheme_payload, "scheme payload")
    extensions = value.get("extensions")
    if not isinstance(extensions, dict) or LIQUILENS_EXTENSION not in extensions:
        raise _error("binding_extension_mismatch", "LiquiLens quote binding is missing")
    for name, extension in extensions.items():
        if (
            type(name) is not str
            or _EXTENSION_NAME_RE.fullmatch(name) is None
            or not isinstance(extension, dict)
            or set(extension) != {"info", "schema"}
            or not isinstance(extension.get("info"), dict)
            or not isinstance(extension.get("schema"), dict)
        ):
            raise _error(
                "binding_extension_mismatch", "payment extension shape is invalid"
            )
    liquilens = extensions[LIQUILENS_EXTENSION]
    info = liquilens["info"]
    required_info = {
        "version",
        "method",
        "resource",
        "bodySha256",
        "offerSha256",
        "resourceInfoSha256",
        "requiredExtensionsSha256",
        "canonicalization",
        "quoteBinding",
    }
    if not required_info <= set(info):
        raise _error(
            "binding_extension_mismatch", "LiquiLens quote binding is incomplete"
        )
    if (
        info.get("version") != 1
        or info.get("method") != "POST"
        or info.get("resource") != resource
        or info.get("bodySha256") != body_sha256
        or info.get("canonicalization") != BODY_CANONICALIZATION
        or _canonical_json(liquilens["schema"], "LiquiLens binding schema")
        != _canonical_json(_BINDING_SCHEMA, "required LiquiLens binding schema")
    ):
        raise _error(
            "binding_extension_mismatch",
            "LiquiLens body, resource, or schema binding is invalid",
        )
    for name in (
        "offerSha256",
        "resourceInfoSha256",
        "requiredExtensionsSha256",
        "quoteBinding",
    ):
        item = info.get(name)
        if type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None:
            raise _error(
                "binding_extension_mismatch",
                "LiquiLens quote binding contains an invalid digest",
            )
    payload_bytes = _canonical_json(value, "payment payload")
    identity_bytes = _canonical_json(
        {
            "x402Version": X402_VERSION,
            "accepted": value["accepted"],
            "payload": scheme_payload,
        },
        "payment authorization identity",
    )
    return _StableAuthorization(
        payment_id=hashlib.sha256(identity_bytes).hexdigest(),
        payment_payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        payment_payload_bytes=payload_bytes,
        payment_requirements_bytes=requirements_bytes,
    )


def _invalid_payment_error(error: X402AccessError) -> X402AccessError:
    """Map local PaymentPayload validation to the HTTP invalid-payment class."""

    if error.http_status != 402:
        return error
    return X402AccessError(error.code, str(error), http_status=400)


def _validate_payment_payload(
    value: dict[str, Any],
    *,
    config: X402Config,
    resource: str,
    body_sha256: str,
) -> tuple[bytes, bytes]:
    expected_keys = {"x402Version", "resource", "accepted", "payload", "extensions"}
    if set(value) != expected_keys:
        raise _error(
            "invalid_payment_payload_shape",
            "PAYMENT-SIGNATURE must contain the strict x402 v2 fields",
        )
    if type(value.get("x402Version")) is not int or value["x402Version"] != 2:
        raise _error("invalid_x402_version", "only x402 v2 is accepted")
    expected_resource = config.resource_info()
    if _canonical_json(value.get("resource"), "payment resource") != _canonical_json(
        expected_resource, "configured resource"
    ):
        raise _error(
            "resource_mismatch", "PaymentPayload.resource is not the exact resource"
        )
    accepted = value.get("accepted")
    if not isinstance(accepted, dict):
        raise _error("accepted_offer_mismatch", "accepted offer must be an object")
    network = accepted.get("network")
    if type(network) is not str or _CAIP2_RE.fullmatch(network) is None:
        raise _error("invalid_network", "accepted network is not CAIP-2")
    expected_requirements = config.payment_requirements()
    requirements_bytes = _canonical_json(expected_requirements, "payment requirements")
    if _canonical_json(accepted, "accepted offer") != requirements_bytes:
        raise _error(
            "accepted_offer_mismatch",
            "PaymentPayload.accepted does not exactly equal the configured offer",
        )
    scheme_payload = value.get("payload")
    if not isinstance(scheme_payload, dict) or not scheme_payload:
        raise _error(
            "invalid_payment_payload", "scheme payload must be a non-empty object"
        )
    _canonical_json(scheme_payload, "scheme payload")
    expected_extensions = _binding_extension(config, resource, body_sha256)
    extensions = value.get("extensions")
    received_binding: Any = None
    if isinstance(extensions, dict):
        liquilens = extensions.get(LIQUILENS_EXTENSION)
        if isinstance(liquilens, dict):
            info = liquilens.get("info")
            if isinstance(info, dict):
                received_binding = info.get("quoteBinding")
    expected_binding = expected_extensions[LIQUILENS_EXTENSION]["info"]["quoteBinding"]
    if (
        type(received_binding) is not str
        or len(received_binding) != 64
        or not hmac.compare_digest(received_binding, expected_binding)
    ):
        raise _error(
            "binding_extension_mismatch",
            "LiquiLens quote binding is missing or invalid",
        )
    _validate_extension_echo(extensions, expected_extensions)
    return _canonical_json(value, "payment payload"), requirements_bytes


def _mapping_snapshot(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FacilitatorUnavailable(f"{name} was not a JSON object")
    try:
        raw = _canonical_json(dict(value), name)
        return _strict_json_object(raw, name)
    except X402AccessError as exc:
        raise FacilitatorUnavailable(f"{name} was not strict JSON") from exc


def _validate_payer(value: Any, network: str) -> str:
    if type(value) is not str:
        raise ValueError("payer is missing")
    namespace = network.split(":", 1)[0]
    if namespace == "eip155":
        if _EVM_ADDRESS_RE.fullmatch(value) is None:
            raise ValueError("payer is not an EVM address")
    elif namespace == "solana":
        if _SOLANA_VALUE_RE.fullmatch(value) is None or len(value) > 44:
            raise ValueError("payer is not a Solana address")
    elif (
        len(value) > 256
        or _PRINTABLE_ASCII_RE.fullmatch(value) is None
        or value.strip() != value
    ):
        raise ValueError("payer is not a bounded network identifier")
    return value


def _same_payer(left: str, right: str, network: str) -> bool:
    if network.startswith("eip155:"):
        return secrets.compare_digest(left.lower(), right.lower())
    return secrets.compare_digest(left, right)


def _validate_transaction(value: Any, network: str) -> str:
    if type(value) is not str:
        raise ValueError("transaction is missing")
    namespace = network.split(":", 1)[0]
    if namespace == "eip155":
        if _EVM_TRANSACTION_RE.fullmatch(value) is None:
            raise ValueError("transaction is not an EVM hash")
    elif namespace == "solana":
        if _SOLANA_VALUE_RE.fullmatch(value) is None or len(value) < 64:
            raise ValueError("transaction is not a Solana signature")
    elif (
        len(value) > 256
        or _PRINTABLE_ASCII_RE.fullmatch(value) is None
        or value.strip() != value
    ):
        raise ValueError("transaction is not a bounded network identifier")
    return value


def _verified_payer(
    value: Mapping[str, Any],
    network: str,
    *,
    payment_payload: Mapping[str, Any],
) -> str:
    response = _mapping_snapshot(value, "facilitator verify response")
    allowed = {"isValid", "invalidReason", "payer", "extensions", "extra"}
    if not set(response) <= allowed or type(response.get("isValid")) is not bool:
        raise FacilitatorUnavailable("facilitator verify response has invalid shape")
    if response["isValid"] is not True:
        reason = response.get("invalidReason")
        if type(reason) is not str or not reason:
            reason = "payment verification failed"
        raise _error("payment_verification_failed", reason)
    if "invalidReason" in response:
        raise FacilitatorUnavailable(
            "successful facilitator verify response contains invalidReason"
        )
    for optional in ("extensions", "extra"):
        if optional in response and not isinstance(response[optional], dict):
            raise FacilitatorUnavailable(
                f"facilitator verify {optional} must be an object"
            )
    derived_payer: str | None = None
    if network.startswith("eip155:"):
        scheme_payload = payment_payload.get("payload")
        authorization = (
            scheme_payload.get("authorization")
            if isinstance(scheme_payload, dict)
            else None
        )
        try:
            derived_payer = _validate_payer(
                authorization.get("from") if isinstance(authorization, dict) else None,
                network,
            )
        except ValueError as exc:
            raise FacilitatorUnavailable(
                "verified exact EVM authorization payer is invalid"
            ) from exc
    if "payer" not in response:
        if derived_payer is None:
            raise FacilitatorUnavailable(
                "facilitator verify payer is required for this network profile"
            )
        return derived_payer
    try:
        payer = _validate_payer(response["payer"], network)
    except ValueError as exc:
        raise FacilitatorUnavailable("facilitator verify payer is invalid") from exc
    if derived_payer is not None and not _same_payer(payer, derived_payer, network):
        raise FacilitatorUnavailable(
            "facilitator verify payer differs from the authorization"
        )
    return payer


def _validated_settlement(
    value: Mapping[str, Any],
    *,
    config: X402Config,
    verified_payer: str,
) -> tuple[dict[str, Any], str, str]:
    return _validated_settlement_terms(
        value,
        network=config.network,
        amount=config.amount,
        verified_payer=verified_payer,
    )


def _validated_failed_settlement(
    value: Mapping[str, Any],
    *,
    network: str,
    amount: str,
    verified_payer: str,
) -> tuple[dict[str, Any], str]:
    """Validate a failed settlement and distinguish terminal from pending."""

    try:
        response = _mapping_snapshot(value, "facilitator settle response")
    except X402AccessError as exc:
        raise SettlementUncertain(
            "facilitator settle response was not a strict JSON object"
        ) from exc
    allowed = {
        "success",
        "errorReason",
        "payer",
        "transaction",
        "network",
        "amount",
        "extensions",
    }
    if not set(response) <= allowed or response.get("success") is not False:
        raise SettlementUncertain("failed facilitator settlement has invalid shape")
    error_reason = response.get("errorReason")
    if (
        type(error_reason) is not str
        or not error_reason
        or len(error_reason) > 256
        or _PRINTABLE_ASCII_RE.fullmatch(error_reason) is None
        or error_reason.strip() != error_reason
    ):
        raise SettlementUncertain("failed settlement reason is invalid")
    received_network = response.get("network")
    if (
        type(received_network) is not str
        or _CAIP2_RE.fullmatch(received_network) is None
        or received_network != network
    ):
        raise SettlementUncertain("failed settlement network does not match the offer")
    if "amount" in response:
        received_amount = response["amount"]
        if (
            type(received_amount) is not str
            or _ATOMIC_AMOUNT_RE.fullmatch(received_amount) is None
            or received_amount != amount
        ):
            raise SettlementUncertain(
                "failed settlement amount does not match the exact offer"
            )
    transaction = response.get("transaction")
    if type(transaction) is not str:
        raise SettlementUncertain("failed settlement transaction is missing")
    if transaction:
        try:
            _validate_transaction(transaction, received_network)
        except ValueError as exc:
            raise SettlementUncertain(
                "failed settlement transaction has invalid shape"
            ) from exc
        raise SettlementUncertain(
            "failed settlement unexpectedly included a transaction"
        )
    if error_reason == "settlement_pending":
        raise SettlementUncertain(
            "facilitator reported a non-terminal pending settlement"
        )
    if "payer" in response:
        try:
            payer = _validate_payer(response["payer"], received_network)
        except ValueError as exc:
            raise SettlementUncertain("failed settlement payer is invalid") from exc
        if not _same_payer(payer, verified_payer, received_network):
            raise SettlementUncertain(
                "failed settlement payer differs from verified payer"
            )
    if "extensions" in response and not isinstance(response["extensions"], dict):
        raise SettlementUncertain("failed settlement extensions must be an object")
    return response, error_reason


def _validated_settlement_terms(
    value: Mapping[str, Any],
    *,
    network: str,
    amount: str,
    verified_payer: str,
) -> tuple[dict[str, Any], str, str]:
    try:
        response = _mapping_snapshot(value, "facilitator settle response")
    except X402AccessError as exc:
        raise SettlementUncertain(
            "facilitator settle response was not a strict JSON object"
        ) from exc
    allowed = {
        "success",
        "errorReason",
        "payer",
        "transaction",
        "network",
        "amount",
        "extensions",
    }
    if not set(response) <= allowed or type(response.get("success")) is not bool:
        raise SettlementUncertain("facilitator settle response has invalid shape")
    if response["success"] is not True:
        raise SettlementUncertain("facilitator reported unsuccessful settlement")
    if "errorReason" in response:
        raise SettlementUncertain(
            "successful facilitator settlement contains errorReason"
        )
    received_network = response.get("network")
    if (
        type(received_network) is not str
        or _CAIP2_RE.fullmatch(received_network) is None
        or received_network != network
    ):
        raise SettlementUncertain("settlement network does not match the offer")
    if "amount" in response:
        received_amount = response["amount"]
        if (
            type(received_amount) is not str
            or _ATOMIC_AMOUNT_RE.fullmatch(received_amount) is None
            or received_amount != amount
        ):
            raise SettlementUncertain(
                "settlement amount does not match the exact offer"
            )
    try:
        payer = _validate_payer(
            response.get("payer", verified_payer),
            received_network,
        )
        transaction = _validate_transaction(
            response.get("transaction"), received_network
        )
    except ValueError as exc:
        raise SettlementUncertain("settlement identity has invalid shape") from exc
    if not _same_payer(payer, verified_payer, received_network):
        raise SettlementUncertain("settlement payer differs from verified payer")
    if "extensions" in response and not isinstance(response["extensions"], dict):
        raise SettlementUncertain("settlement extensions must be an object")
    return response, payer, transaction


def _validate_cached_access(
    completed: CompletedAccess,
    *,
    accepted: Mapping[str, Any],
    payment_payload_sha256: str,
    max_payment_header_bytes: int,
    payment_id: str,
    body_sha256: str,
    resource: str,
) -> CompletedAccess:
    accepted_bytes = _validate_payment_requirements_shape(dict(accepted))
    accepted_snapshot = _strict_json_object(accepted_bytes, "accepted offer")
    identity = completed.payment_identity
    expected = (
        identity.x402_version == X402_VERSION
        and identity.scheme == accepted_snapshot["scheme"]
        and identity.network == accepted_snapshot["network"]
        and identity.amount == accepted_snapshot["amount"]
        and identity.asset == accepted_snapshot["asset"]
        and identity.pay_to == accepted_snapshot["payTo"]
        and identity.payment_id == payment_id
        and identity.body_sha256 == body_sha256
        and identity.resource == resource
        and re.fullmatch(r"[0-9a-f]{64}", identity.payment_payload_sha256) is not None
        and hmac.compare_digest(identity.payment_payload_sha256, payment_payload_sha256)
    )
    if not expected:
        raise _error(
            "journal_corrupt",
            "cached payment identity does not match configured entitlement",
            status=503,
        )
    try:
        payment_response = decode_payment_signature(
            completed.payment_response_header,
            max_header_bytes=max_payment_header_bytes,
        )
        _response, payer, transaction = _validated_settlement_terms(
            payment_response,
            network=identity.network,
            amount=identity.amount,
            verified_payer=identity.payer,
        )
    except Exception as exc:
        raise _error(
            "journal_corrupt",
            "cached PAYMENT-RESPONSE is invalid",
            status=503,
        ) from exc
    if not _same_payer(
        payer, identity.payer, identity.network
    ) or not secrets.compare_digest(transaction, identity.transaction):
        raise _error(
            "journal_corrupt",
            "cached settlement identity does not match PAYMENT-RESPONSE",
            status=503,
        )
    return completed


class X402AccessGate:
    """Two-phase, exact-request x402 v2 access gate.

    ``challenge`` and ``authorize`` must receive the exact configured resource URL
    and the same request bytes.  ``authorize`` returns either a new
    :class:`PreparedAccess` or a replay-safe :class:`CompletedAccess`.  After the
    protected handler succeeds, pass its exact bytes to ``settle``.  The returned
    :class:`PaymentIdentity` is billing provenance only and remains separate from
    the handler's Trade Safety Receipt.
    """

    def __init__(
        self,
        config: X402Config,
        *,
        facilitator: Facilitator,
        journal: SQLiteSettlementJournal,
        maintenance: bool = False,
    ) -> None:
        if type(maintenance) is not bool:
            raise TypeError("maintenance must be a boolean")
        self.config = config
        self._facilitator = facilitator
        self._journal = journal
        self._maintenance = maintenance

    def challenge(
        self,
        body: bytes | bytearray | memoryview,
        *,
        resource: str,
        error: str = "PAYMENT-SIGNATURE header is required",
    ) -> PaymentChallenge:
        """Build a deterministic PAYMENT-REQUIRED header for one exact request."""

        _validate_resource(self.config, resource)
        body_sha256 = canonical_body_sha256(
            body, max_bytes=self.config.max_request_bytes
        )
        if type(error) is not str or not error or len(error) > 256:
            raise ValueError("challenge error must be a bounded string")
        payment_required = {
            "x402Version": X402_VERSION,
            "error": error,
            "resource": self.config.resource_info(),
            "accepts": [self.config.payment_requirements()],
            "extensions": _binding_extension(self.config, resource, body_sha256),
        }
        payment_required_bytes = _canonical_json(payment_required, "PAYMENT-REQUIRED")
        header_value = encode_payment_required(payment_required)
        if len(header_value.encode("ascii")) > self.config.max_payment_header_bytes:
            raise ValueError("configured PAYMENT-REQUIRED exceeds the header budget")
        return PaymentChallenge(
            resource=resource,
            body_sha256=body_sha256,
            payment_required_bytes=payment_required_bytes,
            header_value=header_value,
        )

    async def authorize(
        self,
        body: bytes | bytearray | memoryview,
        *,
        resource: str,
        payment_signature: str | None,
    ) -> PreparedAccess | CompletedAccess:
        """Validate bindings, verify read-only, and atomically claim entitlement."""

        _validate_resource(self.config, resource)
        body_snapshot = _snapshot_bytes(body, "body")
        body_sha256 = canonical_body_sha256(
            body_snapshot, max_bytes=self.config.max_request_bytes
        )
        if payment_signature is None:
            raise _error(
                "payment_signature_required", "PAYMENT-SIGNATURE header is required"
            )
        payload = decode_payment_signature(
            payment_signature,
            max_header_bytes=self.config.max_payment_header_bytes,
        )
        try:
            stable = _stable_payment_authorization(
                payload,
                resource=resource,
                body_sha256=body_sha256,
            )
        except X402AccessError as exc:
            raise _invalid_payment_error(exc) from exc
        cached = self._journal.inspect(
            stable.payment_id,
            body_sha256=body_sha256,
            resource=resource,
            payment_payload_sha256=stable.payment_payload_sha256,
        )
        if cached is not None:
            return _validate_cached_access(
                cached,
                accepted=payload["accepted"],
                payment_payload_sha256=stable.payment_payload_sha256,
                max_payment_header_bytes=self.config.max_payment_header_bytes,
                payment_id=stable.payment_id,
                body_sha256=body_sha256,
                resource=resource,
            )
        try:
            payload_bytes, requirements_bytes = _validate_payment_payload(
                payload,
                config=self.config,
                resource=resource,
                body_sha256=body_sha256,
            )
        except X402AccessError as exc:
            raise _invalid_payment_error(exc) from exc
        if (
            payload_bytes != stable.payment_payload_bytes
            or requirements_bytes != stable.payment_requirements_bytes
        ):
            raise _error(
                "payment_snapshot_mismatch",
                "payment authorization changed during validation",
                status=503,
            )
        try:
            verify_response = await self._facilitator.verify(
                payment_payload=_strict_json_object(
                    payload_bytes, "snapshotted PaymentPayload"
                ),
                payment_requirements=_strict_json_object(
                    requirements_bytes, "snapshotted PaymentRequirements"
                ),
            )
        except X402AccessError:
            raise
        except Exception as exc:
            raise FacilitatorUnavailable() from exc
        payer = _verified_payer(
            verify_response,
            self.config.network,
            payment_payload=payload,
        )
        claim = self._journal.claim(
            stable.payment_id,
            body_sha256=body_sha256,
            resource=resource,
            payer=payer,
            payment_payload=payload_bytes,
            payment_payload_sha256=stable.payment_payload_sha256,
            payment_requirements=requirements_bytes,
        )
        if isinstance(claim, CompletedAccess):
            return _validate_cached_access(
                claim,
                accepted=payload["accepted"],
                payment_payload_sha256=stable.payment_payload_sha256,
                max_payment_header_bytes=self.config.max_payment_header_bytes,
                payment_id=stable.payment_id,
                body_sha256=body_sha256,
                resource=resource,
            )
        return PreparedAccess(
            payment_id=stable.payment_id,
            payment_payload_sha256=stable.payment_payload_sha256,
            payer=payer,
            resource=resource,
            body_sha256=body_sha256,
            _payment_payload_bytes=payload_bytes,
            _requirements_bytes=requirements_bytes,
            _owner_token=claim,
        )

    async def settle(
        self,
        prepared: PreparedAccess,
        response_body: bytes | bytearray | memoryview,
        *,
        status_code: int = 200,
        content_type: str = "application/json",
    ) -> CompletedAccess:
        """Settle once, then expose the exact protected bytes with x402 headers.

        Any exception after the journal enters ``settling`` leaves a sticky
        uncertain state.  Calling this method again cannot blindly resettle it.
        """

        if not isinstance(prepared, PreparedAccess):
            raise TypeError("prepared must be PreparedAccess")
        response_snapshot = _snapshot_bytes(response_body, "response_body")
        if len(response_snapshot) > self.config.max_cached_response_bytes:
            raise _error(
                "response_too_large",
                "protected response exceeds the configured byte budget",
                status=502,
            )
        if type(status_code) is not int or not 200 <= status_code < 300:
            raise _error(
                "protected_response_failed",
                "only a successful protected response may be settled",
                status=502,
            )
        if (
            type(content_type) is not str
            or not content_type
            or len(content_type) > 128
            or any(
                ord(character) < 0x20 or ord(character) > 0x7E
                for character in content_type
            )
        ):
            raise ValueError("content_type must be bounded visible ASCII")
        cached = self._journal.begin_settlement(
            prepared,
            response_body=response_snapshot,
            status_code=status_code,
            content_type=content_type,
        )
        if cached is not None:
            prepared_payload = _strict_json_object(
                prepared._payment_payload_bytes, "snapshotted PaymentPayload"
            )
            return _validate_cached_access(
                cached,
                accepted=prepared_payload["accepted"],
                payment_payload_sha256=prepared.payment_payload_sha256,
                max_payment_header_bytes=self.config.max_payment_header_bytes,
                payment_id=prepared.payment_id,
                body_sha256=prepared.body_sha256,
                resource=prepared.resource,
            )
        try:
            settle_response = await self._facilitator.settle(
                payment_payload=_strict_json_object(
                    prepared._payment_payload_bytes, "snapshotted PaymentPayload"
                ),
                payment_requirements=_strict_json_object(
                    prepared._requirements_bytes, "snapshotted PaymentRequirements"
                ),
            )
        except SettlementUncertain:
            raise
        except Exception as exc:
            raise SettlementUncertain() from exc
        try:
            settle_snapshot = _mapping_snapshot(
                settle_response, "facilitator settle response"
            )
            self._journal.record_settlement_result(prepared, settle_snapshot)
            if settle_snapshot.get("success") is False:
                failed, error_reason = _validated_failed_settlement(
                    settle_snapshot,
                    network=self.config.network,
                    amount=self.config.amount,
                    verified_payer=prepared.payer,
                )
                payment_response = encode_payment_response(failed)
                if (
                    len(payment_response.encode("ascii"))
                    > self.config.max_payment_header_bytes
                ):
                    raise SettlementUncertain(
                        "failed PAYMENT-RESPONSE exceeds the configured header budget"
                    )
                self._journal.complete_failed_settlement(
                    prepared,
                    payment_response=payment_response,
                    error_reason=error_reason,
                )
                raise PaymentSettlementFailed(
                    payment_response_header=payment_response,
                    error_reason=error_reason,
                )
            response, payer, transaction = _validated_settlement(
                settle_snapshot,
                config=self.config,
                verified_payer=prepared.payer,
            )
            payment_response = encode_payment_response(response)
            if (
                len(payment_response.encode("ascii"))
                > self.config.max_payment_header_bytes
            ):
                raise SettlementUncertain(
                    "PAYMENT-RESPONSE exceeds the configured header budget"
                )
            identity = PaymentIdentity(
                x402_version=X402_VERSION,
                scheme=self.config.scheme,
                network=self.config.network,
                amount=self.config.amount,
                asset=self.config.asset,
                pay_to=self.config.pay_to,
                payer=payer,
                transaction=transaction,
                resource=prepared.resource,
                body_sha256=prepared.body_sha256,
                payment_id=prepared.payment_id,
                payment_payload_sha256=prepared.payment_payload_sha256,
            )
            self._journal.complete(
                prepared,
                payment_response=payment_response,
                identity=identity,
            )
        except (PaymentSettlementFailed, SettlementUncertain):
            raise
        except Exception as exc:
            raise SettlementUncertain(
                "settlement succeeded but response release is uncertain"
            ) from exc
        return CompletedAccess(
            response_body=response_snapshot,
            status_code=status_code,
            content_type=content_type,
            payment_response_header=payment_response,
            payment_identity=identity,
        )

    def reconciliation_records(
        self, *, limit: int = 100
    ) -> tuple[ReconciliationRecord, ...]:
        """Return redacted uncertain attempts for an authenticated operator.

        Raw signatures, PaymentPayloads, payer/payee/amount, facilitator payloads,
        and protected response bytes never cross this API.
        """

        return self._journal.reconciliation_records(limit=limit)

    def journal_counts(self) -> JournalCounts:
        """Return redacted capacity counters without payment or safety data."""

        return self._journal.counts()

    def reconcile_settled(
        self,
        payment_id: str,
        settlement_response: Mapping[str, Any] | None = None,
    ) -> None:
        """Finalize one uncertain attempt from independently confirmed success.

        This never calls the facilitator and validates the supplied response
        against the original journaled offer and verified payer.  The response
        is recorded before the exact cached entitlement becomes replayable.
        """

        if not self._maintenance:
            raise _error(
                "reconciliation_runtime_active",
                "reconciliation is unavailable on a serving gateway",
                status=409,
            )

        if (
            type(payment_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payment_id) is None
        ):
            raise _error(
                "reconciliation_payment_id_invalid",
                "payment id must be one lowercase SHA-256 digest",
                status=400,
            )
        material = self._journal._reconciliation_material(payment_id)
        payload = _strict_json_object(
            material.payment_payload, "journaled PaymentPayload"
        )
        stable = _stable_payment_authorization(
            payload,
            resource=material.resource,
            body_sha256=material.body_sha256,
        )
        if stable.payment_id != payment_id or not hmac.compare_digest(
            stable.payment_payload_sha256,
            hashlib.sha256(material.payment_payload).hexdigest(),
        ):
            raise _error(
                "journal_corrupt",
                "journaled payment identity is inconsistent",
                status=503,
            )
        requirements = _strict_json_object(
            material.payment_requirements, "journaled PaymentRequirements"
        )
        requirements_bytes = _validate_payment_requirements_shape(requirements)
        if requirements_bytes != stable.payment_requirements_bytes:
            raise _error(
                "journal_corrupt",
                "journaled payment requirements differ from the PaymentPayload",
                status=503,
            )
        if settlement_response is None:
            if material.settlement_result is None:
                raise _error(
                    "reconciliation_result_required",
                    "an independently confirmed settlement result is required",
                    status=409,
                )
            recorded_response = _strict_json_object(
                material.settlement_result, "journaled settlement result"
            )
            if recorded_response.get("success") is not True:
                raise _error(
                    "reconciliation_result_required",
                    "an independently confirmed successful result is required",
                    status=409,
                )
            settlement_response = recorded_response
        response, payer, transaction = _validated_settlement_terms(
            settlement_response,
            network=requirements["network"],
            amount=requirements["amount"],
            verified_payer=material.payer,
        )
        payment_response = encode_payment_response(response)
        if len(payment_response.encode("ascii")) > self.config.max_payment_header_bytes:
            raise SettlementUncertain(
                "reconciled PAYMENT-RESPONSE exceeds the configured header budget"
            )
        identity = PaymentIdentity(
            x402_version=X402_VERSION,
            scheme=requirements["scheme"],
            network=requirements["network"],
            amount=requirements["amount"],
            asset=requirements["asset"],
            pay_to=requirements["payTo"],
            payer=payer,
            transaction=transaction,
            resource=material.resource,
            body_sha256=material.body_sha256,
            payment_id=payment_id,
            payment_payload_sha256=stable.payment_payload_sha256,
        )
        self._journal.complete_reconciled(
            payment_id,
            settlement_result=response,
            payment_response=payment_response,
            identity=identity,
        )

    def retire_unsettled(self, payment_id: str) -> None:
        """Close a confirmed-not-settled attempt without making it payable again."""

        if not self._maintenance:
            raise _error(
                "reconciliation_runtime_active",
                "reconciliation is unavailable on a serving gateway",
                status=409,
            )

        if (
            type(payment_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payment_id) is None
        ):
            raise _error(
                "reconciliation_payment_id_invalid",
                "payment id must be one lowercase SHA-256 digest",
                status=400,
            )
        self._journal.retire_unsettled(payment_id)

    def retire_terminal_responses(
        self,
        *,
        older_than_seconds: int,
        limit: int = 100,
    ) -> int:
        """Offline-retire aged response material through permanent tombstones."""

        if not self._maintenance:
            raise _error(
                "reconciliation_runtime_active",
                "retention is unavailable on a serving gateway",
                status=409,
            )
        return self._journal.retire_terminal_responses(
            older_than_seconds=older_than_seconds,
            limit=limit,
        )

    def abort(self, prepared: PreparedAccess) -> bool:
        """Release a claim only while settlement is known not to have started."""

        if not isinstance(prepared, PreparedAccess):
            raise TypeError("prepared must be PreparedAccess")
        return self._journal.abort(prepared)

    async def aclose(self) -> None:
        """Close the facilitator; the journal is closed separately by its owner."""

        await self._facilitator.aclose()


__all__ = [
    "BODY_CANONICALIZATION",
    "LIQUILENS_EXTENSION",
    "PAYMENT_REQUIRED_HEADER",
    "PAYMENT_RESPONSE_HEADER",
    "PAYMENT_SIGNATURE_HEADER",
    "CompletedAccess",
    "Facilitator",
    "FacilitatorUnavailable",
    "HttpxFacilitator",
    "JournalCounts",
    "PaymentAuthorizationRetired",
    "PaymentChallenge",
    "PaymentIdentity",
    "PaymentSettlementFailed",
    "PreparedAccess",
    "ReconciliationRecord",
    "SQLiteSettlementJournal",
    "SettledResponseRetired",
    "SettlementUncertain",
    "X402AccessError",
    "X402AccessGate",
    "X402Config",
    "canonical_body_sha256",
    "decode_payment_signature",
    "encode_payment_required",
    "encode_payment_response",
    "extract_payment_signature",
]
