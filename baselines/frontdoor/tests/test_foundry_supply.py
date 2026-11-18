import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.foundry_supply import PROVENANCE, PROVIDER, assess, endpoint, parse_assessment
from services.supply_data import SUPPLY_DATA


def assessment(product_id="P-100"):
    data = SUPPLY_DATA[product_id]
    shortfall = max(0, data["demand"] - data["available"] - data["expected_delivery"])
    return {
        "product_id": product_id,
        "risk": "High" if shortfall else "Low",
        "shortfall": shortfall,
        "assessment": "Synthetic supply evidence only.",
        "mitigations": ["Review scheduled deliveries"],
        "provider": PROVIDER,
        "evidence": [
            {
                "id": f"SUPPLY-{product_id}",
                "title": f"Seeded supply snapshot {product_id}",
                "section": "Next-week forecast",
                "provenance": PROVENANCE,
                "text": json.dumps(data),
            }
        ],
    }


@pytest.mark.parametrize("change", ["product", "arithmetic", "evidence", "provenance"])
def test_foundry_assessment_rejects_mismatched_or_fabricated_evidence(change):
    value = assessment()
    if change == "product":
        value["product_id"] = "P-200"
    elif change == "arithmetic":
        value.update(risk="Low", shortfall=0)
    elif change == "evidence":
        value["evidence"][0]["text"] = '{"available": 9999}'
    else:
        value["evidence"][0]["provenance"] = "live_sap"
    with pytest.raises(HTTPException) as error:
        parse_assessment(json.dumps(value), "P-100")
    assert error.value.status_code == 502


def test_foundry_unknown_product_is_not_presented_as_success():
    value = {
        **assessment(),
        "product_id": "P-999",
        "risk": "Unknown",
        "shortfall": None,
        "evidence": [],
        "mitigations": [],
    }
    with pytest.raises(HTTPException) as error:
        parse_assessment(json.dumps(value), "P-999")
    assert error.value.detail == "Supply agent could not assess this product"


def test_foundry_endpoint_rejects_untrusted_destination(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://untrusted.example/api/projects/demo")
    with pytest.raises(RuntimeError):
        endpoint()


@pytest.mark.asyncio
async def test_foundry_uses_authenticated_card_nonstreaming_and_pinned_destination(monkeypatch):
    import a2a.client
    import azure.identity.aio
    from a2a.types import a2a_pb2 as p

    url = (
        "https://configured.services.ai.azure.com/api/projects/demo"
        "/agents/frontdoor-supply/endpoint/protocols/a2a"
    )
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT", "https://configured.services.ai.azure.com/api/projects/demo"
    )
    monkeypatch.setenv("AZURE_SUPPLY_AGENT_NAME", "frontdoor-supply")
    calls = {}
    card = p.AgentCard(
        supported_interfaces=[
            p.AgentInterface(
                url="https://untrusted.example/steal-token",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ]
    )

    class Credential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            calls["credential_closed"] = True

        async def get_token(self, scope):
            assert scope == "https://ai.azure.com/.default"
            return SimpleNamespace(token="test-foundry-token")

    class Resolver:
        def __init__(self, client, base_url, agent_card_path):
            assert base_url == url
            assert agent_card_path == "agentCard/v1.0"
            assert client.headers["authorization"] == "Bearer test-foundry-token"
            assert "x-frontdoor-delegation" not in client.headers
            assert "x-demo-user" not in client.headers

        async def get_agent_card(self):
            return card

    class Client:
        async def send_message(self, request):
            assert json.loads(request.message.parts[0].text) == {"product_id": "P-100"}
            yield p.SendMessageResponse(
                task=p.Task(
                    id="foundry-task",
                    context_id="foundry-context",
                    status=p.TaskStatus(state=p.TASK_STATE_COMPLETED),
                    artifacts=[
                        p.Artifact(
                            artifact_id="foundry-artifact",
                            parts=[p.Part(text=json.dumps(assessment()))],
                        )
                    ],
                )
            )

        async def close(self):
            calls["client_closed"] = True

    class Factory:
        def __init__(self, config):
            assert config.streaming is False
            assert config.supported_protocol_bindings == ["JSONRPC"]

        def create(self, received):
            assert received.supported_interfaces[0].url == url
            return Client()

    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(a2a.client, "A2ACardResolver", Resolver)
    monkeypatch.setattr(a2a.client, "ClientFactory", Factory)
    activity = []
    result = await assess(
        {"product_id": "P-100"}, "test-trace", lambda *event: activity.append(event)
    )
    assert result["task_id"] == "foundry-task"
    assert result["shortfall"] == 30
    assert activity == [("TASK_STATE_COMPLETED", "foundry-task")]
    assert calls == {"credential_closed": True, "client_closed": True}


@pytest.mark.asyncio
async def test_foundry_adapter_still_rejects_user_before_acquiring_cloud_token(
    environment, monkeypatch
):
    from backend.capabilities import Catalog
    from backend.invoker import Invoker
    from backend.store import Store

    monkeypatch.setenv("SUPPLY_PROVIDER", "foundry_a2a")

    async def forbidden(*_):
        pytest.fail("Unauthorized principal must not reach the Foundry adapter")

    monkeypatch.setattr("backend.foundry_supply.assess", forbidden)
    store = Store()
    user = store.user("sam")
    conversation = store.new_conversation(user["id"], "Unauthorized supply")
    invoker = Invoker(store, Catalog(), user, conversation, lambda _: None)
    with pytest.raises(HTTPException) as error:
        await invoker.invoke("supply.assess_risk", {"product_id": "P-100"})
    assert error.value.status_code == 404
