import asyncio

import pytest
from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    ResponseStream,
)

from backend.capabilities import Catalog
from backend.invoker import Invoker
from backend.orchestrator import Orchestrator
from backend.store import Store
from tests.conftest import headers


class ScriptedModelClient(FunctionInvocationLayer, BaseChatClient):
    def __init__(self, **kwargs):
        super().__init__()
        self.turn = 0
        self.observed_tools = set()
        self.closed = False

    async def close(self):
        self.closed = True

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.observed_tools.update(tool.name for tool in options.get("tools", []))
        turn = self.turn
        self.turn += 1

        async def updates():
            if turn == 0:
                content = Content.from_function_call(
                    "discovery-1", "find_capabilities", arguments={"goal": "check leave balance"}
                )
            elif turn == 1:
                content = Content.from_function_call(
                    "invoke-1",
                    "invoke_capability",
                    arguments={"capability_id": "people.leave_balance", "inputs_json": "{}"},
                )
            else:
                content = Content.from_text("MAF completed discovery and balance lookup.")
            yield ChatResponseUpdate(role="assistant", response_id=f"r-{turn}", contents=[content])
            if turn == 2:
                yield ChatResponseUpdate(
                    role="assistant",
                    response_id=f"r-{turn}",
                    contents=[Content.from_text(content.text, merge_mode="replace")],
                )

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)


async def test_live_path_uses_real_maf_tool_loop_with_scripted_model(environment, monkeypatch):
    import agent_framework.foundry

    client = ScriptedModelClient()
    monkeypatch.setattr(agent_framework.foundry, "FoundryChatClient", lambda **kwargs: client)
    monkeypatch.setenv("DEMO_MODE", "live")
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.test/api/projects/demo")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT", "test-deployment")
    store = Store()
    conv = store.new_conversation("alex", "MAF runtime test")
    events = []
    invoker = Invoker(store, Catalog(), store.user("alex"), conv, events.append)
    result = await Orchestrator(invoker).run("How many leave days do I have?")
    assert result["content"] == "MAF completed discovery and balance lookup."
    assert (
        "".join(event["text"] for event in events if event["type"] == "delta") == result["content"]
    )
    assert client.closed
    assert client.turn == 3
    assert client.observed_tools == {
        "find_capabilities",
        "invoke_capability",
        "search_knowledge",
        "get_work_context",
        "create_handoff_link",
    }
    assert any(event.get("stage") == "discovery" for event in events)
    assert any(
        event.get("capability_id") == "people.leave_balance" and event.get("status") == "completed"
        for event in events
    )
    progress = [event for event in events if event["type"] == "progress"]
    active = set()
    for event in progress:
        if event["status"] == "running":
            active.add(event["operation_id"])
        else:
            assert event["operation_id"] in active
            active.remove(event["operation_id"])
    assert not active
    discovery = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "progress" and event["stage"] == "discovery"
    )
    assert discovery < next(
        index for index, event in enumerate(events) if event["type"] == "activity"
    )


async def test_progress_closes_the_failed_operation_on_cancellation(environment):
    store = Store()
    events = []
    invoker = Invoker(
        store,
        Catalog(),
        store.user("alex"),
        store.new_conversation("alex", "Cancel"),
        events.append,
    )
    with pytest.raises(asyncio.CancelledError):
        with invoker.progress("knowledge", "Searching policies..."):
            raise asyncio.CancelledError()
    assert [event["status"] for event in events] == ["running", "failed"]
    assert events[0]["operation_id"] == events[1]["operation_id"]


def test_live_configuration_failure_does_not_fall_back_to_mock(client, monkeypatch):
    import json

    monkeypatch.setenv("DEMO_MODE", "live")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT", raising=False)
    response = client.post(
        "/api/chat",
        headers=headers(client),
        json={
            "message": "What is our annual leave policy in Sweden?",
        },
    )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert any(
        event["type"] == "error" and "Live mode requires" in event["message"] for event in events
    )
    assert not any(event["type"] == "result" for event in events)
