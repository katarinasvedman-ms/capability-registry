import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend import apic_catalog
from backend.capabilities import Catalog, Search

RESOURCE = (
    "/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/frontdoor"
    "/providers/Microsoft.ApiCenter/services/frontdoor-api-gw-kapeltol-apic"
)


def seed_rows():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "backend" / "catalog.json").read_text()
    )


def transport(monkeypatch, handler):
    class Credential:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_token(self, scope):
            assert scope == "https://management.azure.com/.default"
            return SimpleNamespace(token="scoped-reader-token")

    client = httpx.Client

    def create(**kwargs):
        assert kwargs["follow_redirects"] is False
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("APIC_RESOURCE_ID", RESOURCE)
    monkeypatch.setattr(apic_catalog, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(apic_catalog.httpx, "Client", create)


def test_apic_reads_owned_metadata_across_pages_and_honors_retirement(monkeypatch):
    calls = []
    row = seed_rows()[6]

    def handler(request):
        assert request.headers["authorization"] == "Bearer scoped-reader-token"
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "value": [{"properties": {"description": "Unrelated API"}}],
                    "nextLink": str(request.url) + "&skipToken=next",
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "properties": {
                            "description": apic_catalog.OWNER,
                            "lifecycleStage": "retired",
                            "customProperties": {apic_catalog.METADATA: json.dumps([row])},
                        }
                    }
                ]
            },
        )

    transport(monkeypatch, handler)
    result = apic_catalog.read_capabilities()
    assert len(calls) == 2
    assert result == [{**row, "status": "inactive"}]


def test_apic_never_follows_continuation_to_another_destination(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200, json={"value": [], "nextLink": "https://untrusted.example/collect-token"}
        )

    transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="unexpected inventory continuation"):
        apic_catalog.read_capabilities()
    assert len(calls) == 1


def test_apic_transport_failure_is_explicit(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(503))
    with pytest.raises(HTTPException) as error:
        apic_catalog.read_capabilities()
    assert error.value.status_code == 503
    assert "no local catalog fallback" in error.value.detail


def test_apic_catalog_filters_before_exposure_and_refreshes_execution_lifecycle(monkeypatch):
    rows = seed_rows()
    monkeypatch.setenv("CATALOG_PROVIDER", "apic")
    monkeypatch.setenv("CATALOG_PATH", "does-not-exist.json")
    monkeypatch.setattr(apic_catalog, "read_capabilities", lambda: copy.deepcopy(rows))
    registry = Catalog()
    allowed = {"permissions": ["supply.read"]}
    denied = {"permissions": []}
    results = registry.search(Search(goal="Check product inventory"), allowed)
    assert results[0]["id"] == "supply.check_inventory"
    assert "invocation" not in results[0] and "required_permissions" not in results[0]
    assert not registry.search(Search(goal="Check product inventory"), denied)
    assert not registry.listing(denied)
    with pytest.raises(HTTPException) as error:
        registry.resolve("supply.check_inventory", denied)
    assert error.value.status_code == 404
    next(row for row in rows if row["id"] == "supply.check_inventory")["status"] = "inactive"
    registry._expires = 0
    with pytest.raises(HTTPException) as error:
        registry.resolve("supply.check_inventory", allowed)
    assert error.value.status_code == 410


def test_expired_apic_cache_never_uses_local_or_stale_data(monkeypatch):
    rows = seed_rows()
    monkeypatch.setenv("CATALOG_PROVIDER", "apic")
    monkeypatch.setattr(apic_catalog, "read_capabilities", lambda: rows)
    registry = Catalog()
    original = registry.entries
    invalid = copy.deepcopy(rows)
    next(row for row in invalid if row["type"] == "action")["approval_required"] = False
    monkeypatch.setattr(apic_catalog, "read_capabilities", lambda: invalid)
    with pytest.raises(ValueError, match="Write capabilities require approval"):
        registry.reload()
    assert registry.entries is original
    registry._expires = 0
    with pytest.raises(HTTPException) as error:
        registry.listing({"permissions": ["supply.read"]})
    assert error.value.status_code == 503
    assert registry._entries is original
