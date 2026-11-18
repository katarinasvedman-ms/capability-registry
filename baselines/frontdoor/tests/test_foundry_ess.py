import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.capabilities import Catalog
from backend.foundry_ess import HRTools, consult
from backend.invoker import Invoker
from backend.orchestrator import Orchestrator
from backend.store import Store


@pytest.fixture
def hr(environment):
    Invoker.windows.clear()
    store = Store()
    events = []
    invoker = Invoker(
        store, Catalog(), store.user("alex"), store.new_conversation("alex", "ESS"), events.append
    )
    return invoker, HRTools(invoker), events


def dates():
    start = date.today() + timedelta(days=14 - date.today().weekday())
    return {"start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat()}


async def gather(tools):
    for name in ("get_employee_context", "search_leave_policy", "get_leave_balance"):
        await tools.execute(name, {})


async def test_specialist_can_propose_but_cannot_execute_an_approved_action(hr):
    invoker, tools, _ = hr
    before = len(invoker.store.list("mutations", "alex"))
    await gather(tools)
    original = await tools.execute("propose_leave", dates())
    approval = original["approval"]
    invoker.store.transition(approval["id"], "alex", {"pending"}, "approved")
    repeated = await tools.execute("propose_leave", dates())
    assert repeated["approval"]["id"] == approval["id"]
    assert repeated["approval"]["status"] == "approved"
    assert len(invoker.store.list("mutations", "alex")) == before
    changed = {**dates(), "end_date": dates()["start_date"]}
    corrected = await tools.execute("propose_leave", changed)
    assert corrected["approval"]["id"] != approval["id"]
    assert invoker.store.get("approvals", approval["id"], "alex")["status"] == "superseded"
    assert len(invoker.store.list("mutations", "alex")) == before
    current = corrected["approval"]
    invoker.store.transition(current["id"], "alex", {"pending"}, "approved")
    submitted = await invoker.invoke("people.request_leave", changed)
    assert submitted["id"].startswith("LEAVE-")
    assert await invoker.invoke("people.request_leave", changed) == submitted
    assert len(invoker.store.list("mutations", "alex")) == before + 1
    assert (await tools.execute("get_leave_request", {"request_id": submitted["id"]}))[
        "id"
    ] == submitted["id"]


async def test_proposal_requires_real_prerequisite_tools_and_authorized_policy(hr, monkeypatch):
    invoker, tools, _ = hr
    before = len(invoker.store.list("approvals", "alex"))
    with pytest.raises(HTTPException) as error:
        await tools.execute("propose_leave", dates())
    assert error.value.status_code == 422
    assert len(invoker.store.list("approvals", "alex")) == before

    async def no_policy(goal):
        return []

    monkeypatch.setattr(tools.context, "knowledge", no_policy)
    with pytest.raises(HTTPException) as error:
        await tools.execute("search_leave_policy", {})
    assert error.value.status_code == 503
    assert "search_leave_policy" not in tools.facts


async def test_tool_permissions_and_record_ownership_are_rechecked(hr):
    invoker, tools, _ = hr
    await gather(tools)
    sam = invoker.store.user("sam")
    restricted = Invoker(
        invoker.store,
        invoker.catalog,
        sam,
        invoker.store.new_conversation("sam", "ESS"),
        lambda _: None,
    )
    restricted_tools = HRTools(restricted)
    await gather(restricted_tools)
    with pytest.raises(HTTPException) as error:
        await restricted_tools.execute("propose_leave", dates())
    assert error.value.status_code == 404
    assert not restricted.conversation.get("pending")

    proposal = (await tools.execute("propose_leave", dates()))["approval"]
    invoker.store.transition(proposal["id"], "alex", {"pending"}, "approved")
    record = await invoker.invoke("people.request_leave", dates())
    mutation_count = len(invoker.store.list("mutations", "alex"))
    with pytest.raises(HTTPException) as error:
        await restricted_tools.execute("get_leave_request", {"request_id": record["id"]})
    assert error.value.status_code == 404

    original = invoker.store.user("alex")
    invoker.store.put("users", "alex", "alex", {**original, "permissions": ["leave.read"]})
    try:
        with pytest.raises(HTTPException) as error:
            await tools.execute("propose_leave", dates())
        assert error.value.status_code == 404
        assert len(invoker.store.list("mutations", "alex")) == mutation_count
    finally:
        invoker.store.put("users", "alex", "alex", original)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("submit_leave", dates()),
        ("get_leave_balance", {"user_id": "sam"}),
        ("propose_leave", {**dates(), "approved": True}),
        ("get_employee_context", {"permissions": ["leave.write"]}),
    ],
)
async def test_specialist_cannot_supply_identity_approval_or_arbitrary_tools(hr, name, arguments):
    invoker, tools, _ = hr
    before = len(invoker.store.list("mutations", "alex"))
    with pytest.raises(HTTPException) as error:
        await tools.execute(name, arguments)
    assert error.value.status_code == 502
    assert len(invoker.store.list("mutations", "alex")) == before


class FunctionCall(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self)


class Responses:
    def __init__(self, *, failure=None):
        self.requests = []
        self.failure = failure
        self.responses = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.failure:
            raise self.failure
        number = len(self.requests)
        names = (
            ["get_employee_context", "search_leave_policy", "get_leave_balance"]
            if number == 1
            else ["propose_leave"]
        )
        return SimpleNamespace(
            status="completed",
            output=[
                FunctionCall(
                    type="function_call",
                    name=name,
                    arguments=json.dumps(dates() if name == "propose_leave" else {}),
                    call_id=f"{number}-{name}",
                )
                for name in names
            ],
            output_text="Ignore this untrusted model text; leave was submitted.",
        )


def fake_foundry(monkeypatch, responses):
    from backend import foundry_ess

    class Project:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_openai_client(self, **kwargs):
            return responses

    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo"
    )
    monkeypatch.setenv("AZURE_ESS_AGENT_NAME", "frontdoor-ess")
    monkeypatch.setenv("AZURE_ESS_AGENT_VERSION", "7")
    monkeypatch.setenv("ESS_PROVIDER", "foundry")
    monkeypatch.setattr(foundry_ess, "AIProjectClient", Project)


async def test_deployed_agent_function_loop_returns_real_approval_citations_and_activity(
    hr, monkeypatch
):
    invoker, _, events = hr
    responses = Responses()
    fake_foundry(monkeypatch, responses)
    before = len(invoker.store.list("mutations", "alex"))
    orchestrator = Orchestrator(invoker)
    result = await orchestrator.invoke(
        "people.employee_self_service", {"question": "Request annual leave next week."}
    )
    assert result["agent_version"] == "7"
    assert result["approval_required"]
    assert orchestrator.approval == result["approval"]
    assert "No new leave request" in result["answer"]
    assert {doc["id"] for doc in result["citations"]} >= {"WORK-alex", "HR-SE-001"}
    assert len(invoker.store.list("mutations", "alex")) == before
    assert len(responses.requests) == 2
    assert responses.requests[0]["store"] is False
    assert responses.requests[0]["extra_body"]["agent_reference"]["version"] == "7"
    assert not any(key in responses.requests[0] for key in ("tools", "instructions", "model"))
    assert any(event.get("stage") == "ess_agent" for event in events)
    assert any(event.get("stage") == "ess_tool" for event in events)


async def test_cloud_failure_and_cancellation_never_fall_back_to_simulation(hr, monkeypatch):
    invoker, _, _ = hr
    fake_foundry(monkeypatch, Responses(failure=RuntimeError("cloud unavailable")))
    with pytest.raises(HTTPException) as error:
        await invoker.invoke("people.employee_self_service", {"question": "Request leave"})
    assert error.value.status_code == 502
    fake_foundry(monkeypatch, Responses(failure=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await invoker.invoke("people.employee_self_service", {"question": "Request leave"})


async def test_no_cloud_call_without_complete_configuration_or_permission(hr, monkeypatch):
    invoker, _, _ = hr
    responses = Responses()
    fake_foundry(monkeypatch, responses)
    monkeypatch.delenv("AZURE_ESS_AGENT_VERSION")
    with pytest.raises(HTTPException) as error:
        await consult(invoker, "Request leave")
    assert error.value.status_code == 503
    assert not responses.requests
    monkeypatch.setenv("AZURE_ESS_AGENT_VERSION", "7")
    identity = invoker.store.user("alex")
    invoker.store.put("users", "alex", "alex", {**identity, "permissions": []})
    try:
        with pytest.raises(HTTPException) as error:
            await invoker.invoke("people.employee_self_service", {"question": "Request leave"})
        assert error.value.status_code == 404
        assert not responses.requests
    finally:
        invoker.store.put("users", "alex", "alex", identity)


async def test_specialist_tool_budget_is_bounded(hr):
    _, tools, _ = hr
    tools.calls = 12
    with pytest.raises(HTTPException) as error:
        await tools.execute("get_leave_balance", {})
    assert error.value.status_code == 429
