from __future__ import annotations

import copy
from datetime import UTC, datetime
from importlib.metadata import entry_points, version

from fastapi import FastAPI
from fastapi.testclient import TestClient
from liquilens_evidence.evidence_carrier import issue_evidence_carrier
from openbb_core.app.router import Router

from openbb_liquilens_evidence.router import (
    EvidenceCarrierRequest,
    router,
    verify,
)


def _carrier(*, expires_at: str = "2026-08-24T13:00:00Z") -> dict:
    return issue_evidence_carrier(
        producer={
            "name": "seiche",
            "version": "0.11.0",
            "endpoint": "https://api.seiche.info/v2/world-markets",
        },
        subject={
            "kind": "financial_instrument",
            "name": "US Treasury 10Y",
            "identifiers": {"ticker": "T", "currency": "USD"},
        },
        claim={
            "kind": "funding_context",
            "summary": "Observed USD funding context",
            "status": "observed",
        },
        clocks={
            "event_time": "2026-08-24T09:00:00Z",
            "knowledge_time": "2026-08-24T09:02:00Z",
            "as_of": "2026-08-24T09:05:00Z",
            "expires_at": expires_at,
        },
        sources=[
            {
                "source_id": "nyfed:sofr:2026-08-24",
                "publisher": "Federal Reserve Bank of New York",
                "title": "Secured Overnight Financing Rate",
                "url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
                "retrieved_at": "2026-08-24T09:01:00Z",
                "content_sha256": "a" * 64,
            }
        ],
        rights={
            "status": "allowed",
            "permissions": ["ingest", "derive", "display", "redistribute"],
            "license": "official-publication-terms-reviewed",
            "license_url": "https://www.newyorkfed.org/markets/data-policy",
            "attribution": "Federal Reserve Bank of New York",
            "jurisdictions": ["global"],
        },
        payload={"metric": "sofr", "value": "5.31", "unit": "percent"},
        extensions={},
    )


def _result_payload(result) -> dict:
    payload = result.model_dump(mode="json", exclude_none=False)
    return payload["results"]


def test_installed_entry_point_is_a_router_and_not_a_provider() -> None:
    matches = [
        item
        for item in entry_points(group="openbb_core_extension")
        if item.name == "liquilens"
    ]
    assert len(matches) == 1
    assert matches[0].load() is router
    assert isinstance(router, Router)
    assert not list(entry_points(group="openbb_provider_extension"))
    assert version("openbb-core") == "1.6.13"


def test_direct_python_router_call_verifies_without_disclosing_payload() -> None:
    carrier = _carrier()
    result = __import__("asyncio").run(
        verify(
            EvidenceCarrierRequest(
                carrier=carrier,
                evaluated_at="2026-08-24T12:00:00Z",
            )
        )
    )
    payload = _result_payload(result)
    assert payload == {
        "ok": True,
        "carrier_id": carrier["carrier_id"],
        "record_hash": carrier["record_hash"],
        "export_disposition": "full",
        "reason_codes": [],
        "policy_version": "liquilens-evidence-export-strict-v1",
        "evaluated_at": "2026-08-24T12:00:00Z",
        "error": None,
        "payload_disclosed": False,
        "data_provider": False,
        "network_access": False,
        "telemetry": False,
        "authority": {
            "financial_authority": "none",
            "can_execute": False,
            "can_recommend": False,
            "is_credit_rating": False,
        },
    }
    assert "payload" not in payload


def test_tampering_and_invalid_time_fail_closed_without_echoing_identity() -> None:
    carrier = _carrier()
    tampered = copy.deepcopy(carrier)
    tampered["payload"]["value"] = "99.99"
    result = __import__("asyncio").run(
        verify(
            EvidenceCarrierRequest(
                carrier=tampered,
                evaluated_at="2026-08-24T12:00:00Z",
            )
        )
    )
    payload = _result_payload(result)
    assert payload["ok"] is False
    assert payload["export_disposition"] == "reject"
    assert payload["reason_codes"] == ["invalid_carrier"]
    assert payload["carrier_id"] is None
    assert payload["record_hash"] is None
    assert "record_hash" in payload["error"]

    result = __import__("asyncio").run(
        verify(EvidenceCarrierRequest(carrier=carrier, evaluated_at="2026-08-24"))
    )
    payload = _result_payload(result)
    assert payload["ok"] is False
    assert payload["reason_codes"] == ["invalid_evaluation_time"]
    assert payload["evaluated_at"] is None


def test_expired_carrier_retains_metadata_only_disposition() -> None:
    result = __import__("asyncio").run(
        verify(
            EvidenceCarrierRequest(
                carrier=_carrier(expires_at="2026-08-24T10:00:00Z"),
                evaluated_at="2026-08-24T12:00:00Z",
            )
        )
    )
    payload = _result_payload(result)
    assert payload["ok"] is True
    assert payload["export_disposition"] == "metadata_only"
    assert payload["reason_codes"] == ["evidence_expired"]
    assert payload["payload_disclosed"] is False


def test_fastapi_route_is_post_only_and_body_is_strict() -> None:
    app = FastAPI()
    app.include_router(router._api_router, prefix="/liquilens")
    client = TestClient(app)
    carrier = _carrier()

    response = client.post(
        "/liquilens/verify",
        json={
            "carrier": carrier,
            "evaluated_at": "2026-08-24T12:00:00Z",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "provider" not in body
    assert body["results"]["ok"] is True
    assert body["results"]["data_provider"] is False

    assert client.get("/liquilens/verify").status_code == 405
    response = client.post(
        "/liquilens/verify",
        json={
            "carrier": carrier,
            "evaluated_at": "2026-08-24T12:00:00Z",
            "unexpected": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()["results"]
    assert payload["ok"] is False
    assert payload["reason_codes"] == ["invalid_request"]
    assert payload["error"] == (
        "request must contain only carrier and optional evaluated_at"
    )
    assert "carrier" not in payload


def test_verification_does_not_open_a_network_connection(monkeypatch) -> None:
    import socket

    def blocked(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    result = __import__("asyncio").run(
        verify(
            EvidenceCarrierRequest(
                carrier=_carrier(),
                evaluated_at=datetime(2026, 8, 24, 12, tzinfo=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            )
        )
    )
    assert _result_payload(result)["ok"] is True
