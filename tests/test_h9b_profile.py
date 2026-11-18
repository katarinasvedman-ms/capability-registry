import copy

import httpx
import pytest

pytest.importorskip("agent_framework")
pytest.importorskip("rfc8785")

from consumer import app as consumer  # noqa: E402
from gateway import app as gateway  # noqa: E402
from governance.execution import (  # noqa: E402
    profile_binding,
    sign_execution,
    verify_execution,
)
from governance.lease import LeaseError, issue  # noqa: E402
from mocks import secured  # noqa: E402
from tests.test_governance_gateway import boundary as boundary  # noqa: E402, F401


@pytest.mark.parametrize(
    "capability,inputs,changed",
    [
        ("mock.create_ticket", {"title": "Original"}, {"title": "Changed"}),
        ("mock.deploy_release", {"release": "v1.2.3"}, {"release": "v9.9.9"}),
    ],
)
async def test_profile_binds_lease_subject_not_transport_and_rejects_replay(
    boundary, monkeypatch, capability, inputs, changed
):
    runtime = boundary.runtime
    runtime.config["trusted_upstreams"] = ["ai-gateway"]
    runtime.config["consumer_url"] = "http://consumer"
    runtime.config["callers"]["viewer"] = {
        **runtime.config["callers"]["release"],
        "risk_tiers": ["general"],
    }
    consumer.app.state.runtime = runtime
    monkeypatch.setattr(consumer, "KeyVaultSigner", lambda *_: boundary.signer)
    original_client = runtime.client
    runtime.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=consumer.app), base_url="http://consumer"
    )
    identity = "viewer" if capability.endswith("create_ticket") else "release"
    view = "general" if identity == "viewer" else "privileged"
    call = runtime.call(capability, inputs, identity, "task", "synthetic")
    binding = profile_binding(runtime.bindings, capability)
    token, _ = await issue(call, binding, boundary.signer)
    original_binding = copy.deepcopy(runtime.bindings[capability])

    async def invoke(client, arguments, lease=token, upstream="ai-gateway"):
        return await client.post(
            "/h9b/mcp/" + view,
            headers={"Authorization": "Bearer " + boundary.access(upstream)},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": binding["tool_name"],
                    "arguments": {
                        **arguments,
                        **({"_capability_lease": lease} if lease else {}),
                    },
                },
            },
        )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway"
        ) as client:
            assert (await invoke(client, changed)).status_code == 403
            assert (await invoke(client, inputs, lease=None)).status_code == 403
            assert (await invoke(client, inputs, upstream="release")).status_code == 403
            assert not boundary.calls
            response = await invoke(client, inputs)
            assert response.status_code == 200, response.text
            assert boundary.calls == [inputs]
            assert boundary.mock.events[-1]["accepted"]
            assert runtime.events[-1]["identity"] == identity
            assert runtime.events[-1]["transport_identity"] == "ai-gateway"
            assert (await invoke(client, inputs)).status_code == 403
            assert boundary.calls == [inputs]
            assert runtime.bindings[capability] == original_binding
    finally:
        await runtime.client.aclose()
        await original_client.aclose()


async def test_mock_requires_distinct_execution_assertion_not_gateway_identity_or_lease(boundary):
    runtime = boundary.runtime
    capability = "mock.create_ticket"
    call = runtime.call(capability, {"title": "Original"}, "release", "task", "synthetic")
    lease, claims = await issue(
        call, profile_binding(runtime.bindings, capability), boundary.signer
    )
    assertion = await sign_execution(lease, claims, boundary.signer)
    assert (
        verify_execution(assertion, runtime.public_key, boundary.signer.key_id, lease, claims)[
            "agent_identity"
        ]
        == "release"
    )
    with pytest.raises(LeaseError):
        verify_execution(lease, runtime.public_key, boundary.signer.key_id, lease, claims)
    with pytest.raises(LeaseError):
        verify_execution(
            assertion, runtime.public_key, boundary.signer.key_id, lease + "changed", claims
        )
    context = {
        "provider_profile": "h9b",
        "lease": lease,
        "identity": "release",
        "task_id": "task",
        "approver": "synthetic",
        "gateway_assertion": boundary.access("gateway", "https://management.azure.com/"),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured.app), base_url="http://mock"
    ) as client:
        for invalid in (None, lease, boundary.access("gateway", "https://management.azure.com/")):
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "create_ticket",
                        "arguments": call.inputs,
                        "_meta": {
                            "capability-registry/context": {
                                **context,
                                "execution_assertion": invalid,
                            }
                        },
                    },
                },
            )
            assert response.status_code == 403
    assert not any(e["accepted"] for e in boundary.mock.events)
