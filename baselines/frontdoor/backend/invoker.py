import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager

import httpx
from fastapi import HTTPException

from backend.auth import effective_identity
from backend.capabilities import Search
from backend.config import ess_provider, gateway_provider, supply_provider
from backend.delegation import service_headers
from backend.knowledge import search_knowledge
from backend.store import digest


class Invoker:
    windows = defaultdict(deque)

    def __init__(self, store, catalog, user, conversation, emit):
        self.store = store
        self.catalog = catalog
        self.user = user
        self.conversation = conversation
        self.emit = emit
        self.trace_id = str(uuid.uuid4())
        self.calls = 0

    def activity(self, stage, **details):
        event = self.store.audit(
            self.user["id"],
            self.conversation["id"],
            stage=stage,
            trace_id=self.trace_id,
            **details,
        )
        self.emit({"type": "activity", **event})

    @contextmanager
    def progress(self, stage, message):
        operation_id = str(uuid.uuid4())
        event = {
            "type": "progress",
            "operation_id": operation_id,
            "stage": stage,
            "message": message,
        }
        started = time.monotonic()
        status = "completed"
        self.emit({**event, "status": "running"})
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            self.emit(
                {
                    **event,
                    "status": status,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }
            )

    def discover(self, goal, domain=None, type=None):
        self.user = effective_identity(self.store, self.user)
        results = self.catalog.search(Search(goal=goal, domain=domain, type=type), self.user)
        self.activity(
            "discovery",
            status="completed",
            detail={
                "goal": goal,
                "candidates": [entry["id"] for entry in results],
            },
        )
        return results

    async def invoke(self, capability_id, inputs, approval_id=None, *, proposal_only=False):
        started = time.monotonic()
        entry = None
        executing_approval = False
        try:
            self.user = effective_identity(self.store, self.user)
            with self.progress("authorization", "Checking capability permissions and approval..."):
                entry = await asyncio.to_thread(self.catalog.resolve, capability_id, self.user)
            window = self.windows[self.user["id"]]
            while window and started - window[0] > 60:
                window.popleft()
            if len(window) >= 120:
                raise HTTPException(429, "Capability invocation quota exceeded; retry shortly")
            window.append(started)
            self.calls += 1
            if self.calls > 20:
                raise HTTPException(429, "Maximum capability calls per turn exceeded")
            self.catalog.validate(entry, inputs)
            if entry.approval_required:
                approval = self.prepare_approval(entry, inputs, approval_id)
                if approval["status"] == "completed":
                    return approval["result"]
                if approval["status"] != "approved" or proposal_only:
                    return {"approval_required": True, "approval": approval}
                self.store.transition(approval["id"], self.user["id"], {"approved"}, "executing")
                approval_id = approval["id"]
                executing_approval = True
            self.activity(
                "execution",
                capability_id=entry.id,
                owner=entry.owner,
                protocol=entry.protocol,
                status="running",
                policy_decision="allowed",
                detail={"request": inputs},
            )
            if entry.invocation["adapter"] == "a2a" and supply_provider() == "foundry_a2a":
                timeout = 120
            elif (
                entry.invocation == {"adapter": "published_agent", "tool": "ask"}
                and ess_provider() == "foundry"
            ):
                timeout = 125
            elif entry.invocation["adapter"] == "mcp" and gateway_provider() == "apim":
                timeout = 90
            else:
                timeout = 25
            label = {
                "mcp": "Communicating with the MCP gateway...",
                "a2a": "Waiting for the Foundry supply specialist...",
                "published_agent": "Working with the Foundry ESS-style specialist..."
                if entry.invocation.get("tool") == "ask" and ess_provider() == "foundry"
                else "Working with the simulated HR service...",
                "handoff": "Preparing the application link...",
            }.get(entry.protocol, "Running the permitted capability...")
            with self.progress("execution", label):
                async with asyncio.timeout(timeout):
                    result = await self.dispatch(entry, inputs, approval_id)
            if entry.approval_required:
                self.store.finish_approval(approval_id, self.user["id"], result)
            self.activity(
                "execution",
                capability_id=entry.id,
                owner=entry.owner,
                protocol=entry.protocol,
                status="completed",
                policy_decision="allowed",
                latency_ms=round((time.monotonic() - started) * 1000),
                detail={"response": result},
            )
            return result
        except BaseException as error:
            if executing_approval:
                try:
                    self.store.transition(approval_id, self.user["id"], {"executing"}, "failed")
                except HTTPException:
                    pass
            self.activity(
                "execution",
                capability_id=capability_id,
                owner=entry.owner if entry else None,
                protocol=entry.protocol if entry else None,
                status="failed",
                policy_decision="denied_or_failed",
                latency_ms=round((time.monotonic() - started) * 1000),
                detail=error.detail
                if isinstance(error, HTTPException)
                else "Downstream service failed; retry is bounded and audited",
            )
            if isinstance(
                error, (HTTPException, asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise
            raise HTTPException(
                504 if isinstance(error, TimeoutError) else 502,
                f"Capability service unavailable. Correlation ID: {self.trace_id}",
            ) from error

    def prepare_approval(self, entry, inputs, approval_id):
        pending = approval_id or self.conversation.get("pending")
        if pending:
            approval = self.store.get("approvals", pending, self.user["id"])
            if time.time() - approval["created_at"] > 1800:
                if approval["status"] in {"pending", "approved", "failed"}:
                    self.store.transition(
                        approval["id"], self.user["id"], {approval["status"]}, "superseded"
                    )
                return self.new_approval(entry, inputs)
            if (
                approval["conversation_id"] != self.conversation["id"]
                or approval["capability_id"] != entry.id
            ):
                if approval_id:
                    raise HTTPException(403, "Approval does not match this action")
            elif (
                approval["input_hash"] == digest(inputs)
                and approval.get("execution_target") == entry.invocation
            ):
                if approval["status"] in {"approved", "completed", "pending"}:
                    return approval
                if approval["status"] == "failed":
                    try:
                        result = self.store.get("mutations", approval["id"], self.user["id"])
                    except HTTPException:
                        return self.store.transition(
                            approval["id"], self.user["id"], {"failed"}, "pending"
                        )
                    self.store.finish_approval(approval["id"], self.user["id"], result)
                    return self.store.get("approvals", approval["id"], self.user["id"])
                if approval["status"] == "executing":
                    raise HTTPException(409, "Action already executing")
            if approval["status"] in {"pending", "approved", "failed"}:
                self.store.transition(
                    approval["id"], self.user["id"], {approval["status"]}, "superseded"
                )
        return self.new_approval(entry, inputs)

    def new_approval(self, entry, inputs):
        approval = self.store.approval(
            self.user["id"], self.conversation["id"], entry.id, inputs, entry.invocation
        )
        self.conversation["pending"] = approval["id"]
        self.activity(
            "approval",
            capability_id=entry.id,
            protocol=entry.protocol,
            status="pending",
            detail={"approval_id": approval["id"], "inputs": inputs},
        )
        return approval

    async def dispatch(self, entry, inputs, approval_id):
        adapter = entry.invocation["adapter"]
        if (
            adapter == "published_agent"
            and entry.invocation["tool"] == "ask"
            and ess_provider() == "foundry"
        ):
            from backend.foundry_ess import consult

            return await consult(self, inputs["question"])
        if adapter == "mcp" and gateway_provider() == "apim":
            from backend.apim_gateway import invoke

            return await invoke(
                entry,
                inputs,
                approval_id,
                self.user,
                self.trace_id,
                self.store,
                progress=self.progress,
                on_timing=lambda step, elapsed, status: self.activity(
                    "mcp_transport",
                    capability_id=entry.id,
                    protocol="mcp",
                    status=status,
                    latency_ms=elapsed,
                    detail={"step": step},
                ),
            )
        if adapter == "a2a" and supply_provider() == "foundry_a2a":
            from backend.foundry_supply import assess

            return await assess(
                inputs,
                self.trace_id,
                lambda status, task_id: self.activity(
                    "delegation",
                    capability_id=entry.id,
                    protocol="a2a",
                    status=status,
                    detail={"task_id": task_id, "provider": "foundry_a2a"},
                ),
            )
        audience = {"published_agent": "ess", "mcp": "gateway", "a2a": "supply"}.get(adapter)
        headers = service_headers(self.user, audience, self.trace_id) if audience else {}
        if adapter == "published_agent":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    os.getenv("ESS_URL", "http://127.0.0.1:8003") + "/invoke",
                    headers=headers,
                    json={
                        "tool": entry.invocation["tool"],
                        "inputs": inputs,
                        "approval_id": approval_id,
                        "trace_id": self.trace_id,
                    },
                )
                if response.status_code >= 400:
                    raise HTTPException(
                        response.status_code, response.json().get("detail", "HR error")
                    )
                return response.json()
        if adapter == "mcp":
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with (
                httpx2.AsyncClient(headers=headers, timeout=20) as client,
                streamable_http_client(
                    os.getenv("GATEWAY_URL", "http://127.0.0.1:8001") + "/mcp/",
                    http_client=client,
                ) as (read, write),
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if entry.invocation["tool"] not in {tool.name for tool in tools.tools}:
                        raise HTTPException(502, "Gateway tool not advertised")
                    arguments = {**inputs, "user_id": self.user["id"], "trace_id": self.trace_id}
                    if entry.approval_required:
                        arguments["approval_id"] = approval_id
                    result = await session.call_tool(entry.invocation["tool"], arguments)
                    if result.is_error:
                        raise HTTPException(502, "Gateway rejected the tool call")
                    structured = result.structured_content
                    if structured is not None:
                        return structured
                    return json.loads(result.content[0].text)
        if adapter == "a2a":
            return await self.delegate_a2a(inputs, headers, entry.id)
        if adapter == "handoff":
            if entry.invocation["tool"] == "open_expenses":
                return {
                    "application": "concur",
                    "title": "Mock Concur expenses",
                    "status": "Navigation ready",
                    "url": "/lob/concur",
                }
            case = self.store.get("cases", inputs["case_id"], self.user["id"])
            return {
                "case_id": case["id"],
                "title": case["title"],
                "status": case["status"],
                "url": f"/lob/cases/{case['id']}",
            }
        if adapter == "knowledge":
            return {
                "citations": await asyncio.to_thread(
                    search_knowledge, "annual leave policy", self.user
                )
            }
        if adapter == "local" and entry.invocation["tool"] == "admin_report":
            return {"summary": "Synthetic administrative Operations summary", "open_cases": 3}
        raise HTTPException(422, "Unsupported server-side adapter")

    async def delegate_a2a(self, inputs, headers, capability_id):
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import a2a_pb2 as p

        url = os.getenv("SUPPLY_URL", "http://127.0.0.1:8002")
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            card = await A2ACardResolver(client, url).get_agent_card()
            # Use our configured address, not an unrestricted address from discovery.
            for interface in card.supported_interfaces:
                interface.url = url + "/"
            a2a = ClientFactory(ClientConfig(httpx_client=client, streaming=True)).create(card)
            request = p.SendMessageRequest(
                message=p.Message(
                    role=p.ROLE_USER,
                    message_id=str(uuid.uuid4()),
                    parts=[
                        p.Part(
                            text=json.dumps(
                                {
                                    **inputs,
                                    "user_id": self.user["id"],
                                    "trace_id": self.trace_id,
                                }
                            )
                        )
                    ],
                )
            )
            result = None
            task_id = None
            async for event in a2a.send_message(request):
                if event.HasField("task"):
                    task_id = event.task.id
                    for artifact in event.task.artifacts:
                        for part in artifact.parts:
                            if part.text:
                                result = json.loads(part.text)
                if event.HasField("status_update"):
                    self.activity(
                        "delegation",
                        capability_id=capability_id,
                        protocol="a2a",
                        status=p.TaskState.Name(event.status_update.status.state),
                        detail={"task_id": event.status_update.task_id},
                    )
                    if event.status_update.status.state in {
                        p.TASK_STATE_FAILED,
                        p.TASK_STATE_REJECTED,
                    }:
                        raise HTTPException(502, "Supply agent could not assess this product")
                if event.HasField("artifact_update"):
                    for part in event.artifact_update.artifact.parts:
                        if part.text:
                            result = json.loads(part.text)
            if result is None:
                raise HTTPException(502, "Supply specialist returned no assessment artifact")
            return {"task_id": task_id, **result}
