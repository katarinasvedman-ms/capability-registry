import asyncio
import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from backend.capabilities import Catalog
from backend.invoker import Invoker
from backend.knowledge import search_knowledge, work_context
from backend.store import Store, redact
from tests.conftest import chat, headers


def test_approval_is_bound_to_inputs_user_and_conversation(client):
    auth = headers(client)
    result, conv, _ = chat(client, auth, "Book annual leave next week.")
    approval = result["approval"]
    client.post(f"/api/approvals/{approval['id']}", headers=auth, json={"approved": True})
    altered = {**approval["inputs"], "end_date": "2027-01-01"}
    response = client.post(
        "/api/capabilities/invoke",
        headers=auth,
        json={
            "capability_id": "people.request_leave",
            "inputs": altered,
            "conversation_id": conv,
            "approval_id": approval["id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["approval_required"]
    assert response.json()["approval"]["status"] == "pending"
    assert response.json()["approval"]["id"] != approval["id"]
    assert (
        client.post(
            f"/api/approvals/{approval['id']}",
            headers=headers(client, "sam"),
            json={"approved": True},
        ).status_code
        == 404
    )
    _, other_conv, _ = chat(client, auth, "How many days do I have?")
    response = client.post(
        "/api/capabilities/invoke",
        headers=auth,
        json={
            "capability_id": "people.request_leave",
            "inputs": approval["inputs"],
            "conversation_id": other_conv,
            "approval_id": approval["id"],
        },
    )
    assert response.status_code == 403


def test_downstream_rejects_unapproved_mutation_and_excess_balance(environment):
    store = Store()
    start = date.today() + timedelta(days=14 - date.today().weekday())
    inputs = {"start_date": start.isoformat(), "end_date": (start + timedelta(days=90)).isoformat()}
    approval = store.approval("alex", "unit", "people.request_leave", inputs)
    with pytest.raises(HTTPException):
        store.mutate("alex", approval["id"], "people.request_leave", inputs)
    store.transition(approval["id"], "alex", {"pending"}, "executing")
    before = store.get("balances", "alex", "alex")
    with pytest.raises(HTTPException) as error:
        store.mutate("alex", approval["id"], "people.request_leave", inputs)
    assert error.value.status_code == 422
    assert store.get("balances", "alex", "alex") == before


def test_failed_transport_retry_reuses_mutation_id(environment):
    store = Store()
    conv = store.new_conversation("alex", "Interrupted action")
    entry = Catalog().entries["operations.create_service_request"]
    inputs = {"title": "Synthetic broken scanner"}
    approval = store.approval("alex", conv["id"], entry.id, inputs, entry.invocation)
    store.transition(approval["id"], "alex", {"pending"}, "executing")
    original = store.mutate("alex", approval["id"], entry.id, inputs)
    store.transition(approval["id"], "alex", {"executing"}, "failed")
    conv["pending"] = approval["id"]
    invoker = Invoker(store, Catalog(), store.user("alex"), conv, lambda _: None)
    recovered = invoker.prepare_approval(entry, inputs, None)
    assert recovered["status"] == "completed"
    assert recovered["result"]["id"] == original["id"]
    assert store.mutate("alex", approval["id"], entry.id, inputs) == original


def test_knowledge_acl_and_audit_redaction(environment):
    docs = search_knowledge("administrative operations", Store().user("sam"))
    assert all(doc["id"] != "OPS-ADMIN" for doc in docs)
    value = redact(
        {
            "authorization": "sensitive",
            "nested": {"token": "private"},
            "message": "mail person@example.test",
            "trace": "Authorization: Bearer private-token-value",
            "note": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJkZW1vIn0.private_signature",
        }
    )
    assert value["authorization"] == "[redacted]"
    assert value["nested"]["token"] == "[redacted]"
    assert "person@example.test" not in value["message"]
    assert "private-token-value" not in value["trace"]
    assert "private_signature" not in value["note"]


def test_context_exposes_only_own_profile_calendar_and_notes(environment):
    store = Store()
    context = work_context(store.user("sam"))
    assert context["profile"]["name"] == "Sam Taylor"
    assert context["profile"]["role"] == "Restricted employee"
    assert context["profile"]["manager"] == "Robin Chen"
    assert context["calendar"][0] in context["text"]
    assert context["meeting_notes"] in context["text"]
    assert "Jordan Lee" not in json.dumps(context)


def test_quota_rejection_is_audited_without_changing_approval(environment, monkeypatch):
    store = Store()
    conv = store.new_conversation("alex", "Quota rejection")
    entry = Catalog().entries["operations.create_service_request"]
    inputs = {"title": "Synthetic quota scanner"}
    approval = store.approval("alex", conv["id"], entry.id, inputs, entry.invocation)
    store.transition(approval["id"], "alex", {"pending"}, "approved")
    monkeypatch.setitem(Invoker.windows, "alex", deque([time.monotonic()] * 120))
    invoker = Invoker(store, Catalog(), store.user("alex"), conv, lambda _: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoker.invoke(entry.id, inputs, approval["id"]))
    assert error.value.status_code == 429
    assert store.get("approvals", approval["id"], "alex")["status"] == "approved"
    assert not store.list("mutations", "alex") or all(
        row.get("title") != inputs["title"] for row in store.list("mutations", "alex")
    )
    event = store.audits("alex", conv["id"])[0]
    assert event["capability_id"] == entry.id
    assert event["owner"] == "Operations"
    assert event["protocol"] == "mcp"
    assert event["latency_ms"] >= 0
    assert event["policy_decision"] == "denied_or_failed"
    assert event["trace_id"] == invoker.trace_id


def test_gateway_deadline_bounds_in_process_legacy_work(environment, monkeypatch):
    from fastapi.routing import APIRoute

    from services import gateway
    from services.security import principal

    async def delayed():
        await asyncio.sleep(10)
        return {"unexpected": "completed"}

    monkeypatch.setattr(gateway, "LEGACY_TIMEOUT", 0.02)
    monkeypatch.setattr(
        gateway.app.router,
        "routes",
        [APIRoute("/legacy/delayed", delayed, methods=["GET"]), *gateway.app.router.routes],
    )
    identity = principal.set(Store().user("alex"))
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                gateway.legacy_call(
                    "GET", "/legacy/delayed", "alex", "legacy-deadline", "operations.read"
                )
            )
    finally:
        principal.reset(identity)
    assert error.value.status_code == 504
    assert "legacy-deadline" in error.value.detail
    event = next(
        event for event in Store().audits("alex") if event["trace_id"] == "legacy-deadline"
    )
    assert event["status"] == "failed"
    assert event["detail"]["error"] == "Legacy API timeout"
    assert event["latency_ms"] < 1000


@pytest.mark.parametrize("queued", [False, True])
def test_running_and_queued_tasks_can_be_cancelled(client, monkeypatch, queued):
    from backend.orchestrator import Orchestrator

    started = threading.Event()

    async def wait_for_cancellation(self, message):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Orchestrator, "run", wait_for_cancellation)
    auth = headers(client)
    store = Store()
    conv = store.new_conversation("alex", "Cancellation boundary")

    def request_chat(message):
        return client.post(
            "/api/chat",
            headers=auth,
            json={"conversation_id": conv["id"], "message": message},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request_chat, "First task")
        try:
            assert started.wait(5)
            tasks = [
                task
                for task in store.list("tasks", "alex")
                if task["conversation_id"] == conv["id"]
            ]
            running_id = tasks[0]["id"]
            target_id = running_id
            result = first
            if queued:
                result = pool.submit(request_chat, "Queued task")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    tasks = [
                        task
                        for task in store.list("tasks", "alex")
                        if task["conversation_id"] == conv["id"]
                    ]
                    if len(tasks) == 2:
                        break
                    time.sleep(0.01)
                assert len(tasks) == 2
                target_id = next(task["id"] for task in tasks if task["id"] != running_id)
            assert (
                client.post(
                    f"/api/tasks/{target_id}/cancel", headers=headers(client, "sam")
                ).status_code
                == 404
            )
            response = client.post(f"/api/tasks/{target_id}/cancel", headers=auth)
            assert response.json()["status"] == "cancelling"
            assert '"type": "error"' in result.result(timeout=5).text
            assert (
                client.get(f"/api/tasks/{target_id}", headers=auth).json()["status"] == "cancelled"
            )
            if queued:
                assert (
                    client.get(f"/api/tasks/{running_id}", headers=auth).json()["status"]
                    == "running"
                )
        finally:
            for task in store.list("tasks", "alex"):
                if task["conversation_id"] == conv["id"]:
                    client.post(f"/api/tasks/{task['id']}/cancel", headers=auth)
            first.result(timeout=5)
    saved = store.get("conversations", conv["id"], "alex")
    assert [message["content"] for message in saved["messages"]] == ["First task"]
    assert any(event["status"] == "cancelled" for event in store.audits("alex", conv["id"]))


def test_restart_reconciles_committed_action_and_interrupted_tasks(environment):
    store = Store()
    inputs = {"title": "Synthetic restart scenario"}
    approval = store.approval("alex", "restart", "operations.create_service_request", inputs)
    store.transition(approval["id"], "alex", {"pending"}, "executing")
    result = store.mutate("alex", approval["id"], approval["capability_id"], inputs)
    store.put("tasks", "restart-task", "alex", {"id": "restart-task", "status": "running"})
    store.recover_runtime()
    assert store.get("tasks", "restart-task", "alex")["status"] == "interrupted"
    recovered = store.get("approvals", approval["id"], "alex")
    assert recovered["status"] == "completed"
    assert recovered["result"]["id"] == result["id"]


def test_expired_proposal_is_replaced_not_executed(environment):
    import time

    store = Store()
    conv = store.new_conversation("alex", "Expired proposal")
    entry = Catalog().entries["operations.create_service_request"]
    inputs = {"title": "Synthetic expired scanner"}
    approval = store.approval("alex", conv["id"], entry.id, inputs)
    approval["created_at"] = time.time() - 1801
    store.put("approvals", approval["id"], "alex", approval)
    with pytest.raises(HTTPException) as error:
        store.transition(approval["id"], "alex", {"pending"}, "approved")
    assert error.value.status_code == 409
    conv["pending"] = approval["id"]
    invoker = Invoker(store, Catalog(), store.user("alex"), conv, lambda _: None)
    renewed = invoker.prepare_approval(entry, inputs, None)
    assert renewed["status"] == "pending"
    assert renewed["id"] != approval["id"]
