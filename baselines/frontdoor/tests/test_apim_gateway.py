from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import apim_gateway
from backend.capabilities import Capability

AUDIENCE = "0e28ed9e-bcb8-47fc-90d4-d61362f62f89"


@pytest.mark.parametrize(
    "url",
    [
        "https://untrusted.example/mcp",
        "http://configured.azure-api.net/tools/mcp",
        "https://configured.azure-api.net@untrusted.example/mcp",
        "https://configured.azure-api.net/tools/mcp?redirect=elsewhere",
    ],
)
def test_apim_rejects_untrusted_destination(monkeypatch, url):
    monkeypatch.setenv("APIM_MCP_URL", url)
    monkeypatch.setenv("MOCK_API_AUDIENCE", AUDIENCE)
    with pytest.raises(RuntimeError):
        apim_gateway.configuration()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,inputs,expected",
    [
        ("get_inventory_status", {"product_id": "P-100"}, {"product_id": "P-100"}),
        ("get_service_request", {"case_id": "SR-123"}, {"request_id": "SR-123"}),
        (
            "create_service_request",
            {"title": "Broken scanner"},
            {"title": "Broken scanner", "approval_id": "approved-id"},
        ),
        ("order_hardware", {"item": "laptop"}, {"item": "laptop", "approval_id": "approved-id"}),
        ("get_hardware_order", {"order_id": "HW-" + "A" * 64}, {"order_id": "HW-" + "A" * 64}),
    ],
)
async def test_apim_sends_native_arguments_and_separate_signed_user_grant(
    monkeypatch, tool, inputs, expected
):
    url = "https://configured.azure-api.net/sap-tools/mcp"
    monkeypatch.setenv("APIM_MCP_URL", url)
    monkeypatch.setenv("MOCK_API_AUDIENCE", AUDIENCE)
    calls = {}
    action = tool in {"create_service_request", "order_hardware"}
    entry = Capability(
        id="operations.renamed_request" if action else "supply.check_inventory",
        name="Synthetic tool",
        domain="Operations",
        description="Synthetic",
        type="action" if action else "read",
        protocol="mcp",
        invocation={"adapter": "mcp", "tool": tool},
        input_schema={},
        required_permissions=[],
        approval_required=action,
        owner="Operations",
        status="active",
        version="1.0",
    )
    identity, store = {"id": "entra:tenant:user"}, object()
    response = (
        {"product_id": "P-100", "available": 80}
        if tool == "get_inventory_status"
        else {"id": "SR-123", "title": "Broken scanner", "status": "Open"}
    )
    if tool in {"order_hardware", "get_hardware_order"}:
        response = {"id": "HW-" + "A" * 64, "item": "laptop", "status": "Submitted"}

    def grant(user, audience, trace, supplied_store, approval, capability, values):
        assert user == identity and supplied_store is store
        assert audience == AUDIENCE and trace == "trace-id"
        assert approval == ("approved-id" if action else None)
        expected_capability = {
            "create_service_request": "operations.create_service_request",
            "order_hardware": "operations.order_hardware",
        }.get(tool)
        assert capability == expected_capability
        assert values == (inputs if action else None)
        return "signed-user-grant"

    class Credential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            calls["credential_closed"] = True

        async def get_token(self, scope):
            assert scope == f"api://{AUDIENCE}/.default"
            return SimpleNamespace(token="workload-token")

    @asynccontextmanager
    async def transport(endpoint, http_client):
        assert endpoint == url
        assert http_client.follow_redirects is False
        assert http_client.headers["authorization"] == "Bearer workload-token"
        assert http_client.headers["x-frontdoor-delegation"] == "signed-user-grant"
        assert "x-demo-user" not in http_client.headers
        yield None, None

    class Session:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            calls["session_closed"] = True

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=tool)])

        async def call_tool(self, name, arguments):
            assert name == tool
            assert arguments == expected
            return SimpleNamespace(is_error=False, structured_content=response)

    monkeypatch.setattr(apim_gateway, "cloud_delegation_assertion", grant)
    monkeypatch.setattr(apim_gateway, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(apim_gateway, "streamable_http_client", transport)
    monkeypatch.setattr(apim_gateway, "ClientSession", Session)
    timings = []
    assert (
        await apim_gateway.invoke(
            entry,
            inputs,
            "approved-id" if action else None,
            identity,
            "trace-id",
            store,
            on_timing=lambda *measurement: timings.append(measurement),
        )
        == response
    )
    assert calls == {"credential_closed": True, "session_closed": True}
    assert [step for step, _, _ in timings] == [
        "mcp_auth",
        "mcp_initialize",
        "mcp_tools",
        "mcp_call",
    ]
    assert all(elapsed >= 0 and status == "completed" for _, elapsed, status in timings)


def test_apim_does_not_treat_a_business_error_as_a_successful_mutation():
    with pytest.raises(HTTPException) as error:
        apim_gateway.validate_result(
            "create_service_request", {"title": "Broken scanner"}, {"detail": "Approval denied"}
        )
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_apim_permission_and_confirmation_checks_precede_cloud_dispatch(
    environment, monkeypatch
):
    from backend.capabilities import Catalog
    from backend.invoker import Invoker
    from backend.store import Store

    monkeypatch.setenv("GATEWAY_PROVIDER", "apim")

    async def forbidden(*_):
        pytest.fail("Unauthorized or unconfirmed actions must not reach APIM")

    monkeypatch.setattr(apim_gateway, "invoke", forbidden)
    store = Store()
    user = store.user("sam")
    conversation = store.new_conversation(user["id"], "Denied inventory")
    invoker = Invoker(store, Catalog(), user, conversation, lambda _: None)
    with pytest.raises(HTTPException) as error:
        await invoker.invoke("supply.check_inventory", {"product_id": "P-100"})
    assert error.value.status_code == 404
    user = store.user("alex")
    conversation = store.new_conversation(user["id"], "Unconfirmed request")
    invoker = Invoker(store, Catalog(), user, conversation, lambda _: None)
    result = await invoker.invoke("operations.create_service_request", {"title": "Broken scanner"})
    assert result["approval_required"] is True
    assert result["approval"]["status"] == "pending"
