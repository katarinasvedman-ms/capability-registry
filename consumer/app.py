from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx
import jwt
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    Content,
    FunctionInvocationContext,
    FunctionInvocationLayer,
    FunctionMiddleware,
    FunctionTool,
    MCPStreamableHTTPTool,
    Message,
    MiddlewareFailure,
)
from fastapi import HTTPException, Request

from adapters.copilot_sdk.provider import RegistrySkillClient
from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from gateway.app import ForwardCredential
from governance.execution import profile_binding, sign_execution
from governance.lease import LeaseError, issue
from governance.lease.keyvault import KeyVaultSigner
from governance.middleware.maf import LeaseMiddleware, lease_headers
from governance.runtime import application, body
from selection.model import ProjectModel
from selection.retrieval import choose
from selection.static import SelectionError

app = application()


@app.post("/selection")
async def selection_response(request: Request):
    runtime = request.app.state.runtime
    caller = await runtime.authenticate(request.headers.get("authorization"))
    message = await body(request)
    goal = message.get("input")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
        raise HTTPException(400, "A bounded textual input is required")
    if any(
        key in message and not isinstance(message[key], str)
        for key in ("kind", "type", "domain", "operation", "role")
    ) or any(
        key in message
        and (
            not isinstance(message[key], list)
            or len(message[key]) > 20
            or not all(isinstance(item, str) for item in message[key])
        )
        for key in ("tasks", "technologies")
    ):
        raise HTTPException(400, "Invalid selection context")
    permitted = ["architecture-analysis", "implementation-planning", "test-strategy"]
    profile = runtime.config["callers"][caller["oid"]]
    context = {
        "goal": goal,
        "permissions": profile["permissions"],
        **{key: message[key] for key in ("kind", "type", "domain", "operation") if key in message},
    }
    if message.get("role"):
        context.update(
            {
                "role": message["role"],
                "tasks": message.get("tasks", []),
                "technologies": message.get("technologies", []),
            }
        )
    policy = {
        "allowed_ids": [name for name in runtime.entries if name.startswith("mock.")] + permitted,
        "allowed_skills": permitted,
        "risk_tiers": profile["risk_tiers"],
    }
    settings = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation" / "settings-nano.json").read_text()
    )
    mode = message.get("mode", "static")
    model = ProjectModel(
        runtime.client,
        ForwardCredential(request.headers["authorization"][7:]),
        settings,
    )
    try:
        result = await choose(
            context,
            list(runtime.entries.values()),
            policy,
            mode=mode,
            model=model,
            settings=settings,
        )
    except SelectionError as error:
        raise HTTPException(403, "Selection rejected") from error
    except httpx.HTTPError as error:
        raise HTTPException(503, "Model selection unavailable; no fallback performed") from error
    runtime.audit(
        {
            "service": "consumer",
            "operation": "selection",
            "identity": caller["oid"],
            "mode": mode,
            **result,
        }
    )
    return result


@app.get("/skills/{name}")
async def skill(name: str, request: Request):
    runtime = request.app.state.runtime
    caller = await runtime.authenticate(request.headers.get("authorization"))
    permitted = ["architecture-analysis", "implementation-planning", "test-strategy"]
    if name not in permitted:
        raise HTTPException(403, "Skill is not selected for this reference consumer")
    call = runtime.call(name, {}, caller["oid"], "skill-activation", None)
    if runtime.policy.evaluate(call).action != "allow":
        raise HTTPException(403, "Skill policy denied")
    transport = FoundryTransport(ForwardCredential(request.headers["authorization"][7:]))
    try:
        registry = Registry(
            list(runtime.entries.values()),
            {key: value for key, value in runtime.bindings.items() if "project_endpoint" in value},
            transport,
            lambda event: runtime.audit({"service": "core", **event}),
        )
        provider = RegistrySkillClient(transport, runtime.bindings[name])
        catalog = await provider.catalog()
        if name not in catalog:
            raise HTTPException(403, "Pinned skill absent")
        content = await registry.execute(
            permitted,
            name,
            caller["oid"],
            "On-demand selected skill; no execution lease",
            lambda entry, pin: provider.read(entry["invocation"]["uri"]),
        )
        return {"name": name, "lease_required": False, "content": content.decode()}
    finally:
        await transport.aclose()


@app.post("/handoff")
async def handoff_response(request: Request):
    runtime, message, call, signer = await prepare(request)
    if call.entry["id"] != "mock.action_agent":
        raise HTTPException(400, "Expected the synthetic A2A fixture")
    audit = []

    async def handoff(title: str, **_kwargs):
        response = await runtime.client.post(
            runtime.config["gateway_url"] + "/a2a",
            headers={
                "Authorization": request.headers["authorization"],
                "X-Task-Id": call.task_id,
                "X-Approver": call.approver or "",
                **lease_headers(_kwargs),
            },
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": {
                        "kind": "message",
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"kind": "data", "data": {"title": title}}],
                    }
                },
            },
        )
        response.raise_for_status()
        return response.json()

    tool = FunctionTool(
        name="handoff",
        description=call.entry["description"],
        func=handoff,
        input_model=call.entry["inputs"],
        additional_properties={"capability_id": call.entry["id"]},
    )
    agent = Agent(
        client=FixtureClient("handoff", call.inputs),
        tools=[tool],
        middleware=[
            LeaseMiddleware(runtime.policy, signer, runtime.entries, runtime.bindings, audit.append)
        ],
    )
    await agent.run(
        "Hand off the synthetic task", function_invocation_kwargs={"lease_context": call}
    )
    for event in audit:
        runtime.audit({"service": "consumer", "protocol": "a2a", **event})
    return {"status": "completed", "audit": audit}


class FixtureClient(FunctionInvocationLayer, BaseChatClient):
    """Deterministic tool-call fixture; not model-based selection or H5 evidence."""

    def __init__(self, name, inputs):
        super().__init__()
        self.name, self.inputs = name, inputs
        self.sent = False

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        if stream:
            raise ValueError("This deterministic fixture uses nonstreaming responses")
        if not self.sent:
            self.sent = True
            return ChatResponse(
                messages=[
                    Message(
                        "assistant",
                        [
                            Content.from_function_call(
                                call_id="call_fixture", name=self.name, arguments=self.inputs
                            )
                        ],
                    )
                ]
            )
        return ChatResponse(messages=[Message("assistant", ["Synthetic tool turn completed"])])


class MutationFixture(FunctionMiddleware):
    async def process(self, context, call_next):
        key = "release" if "release" in context.arguments else "title"
        context.arguments = {
            **context.arguments,
            key: "v9.9.9" if key == "release" else "Mutated fixture title",
        }
        await call_next()


async def prepare(request):
    runtime = request.app.state.runtime
    claims = await runtime.authenticate(request.headers.get("authorization"))
    message = await body(request)
    if message.get("stream"):
        raise HTTPException(400, "The deterministic fixture supports nonstreaming responses only")
    metadata = message.get("metadata") or {}
    if "capability_id" in metadata and "capability_id" not in message:
        message = {
            **message,
            "capability_id": metadata["capability_id"],
            "arguments": json.loads(metadata.get("arguments", "{}")),
            "approved": metadata.get("synthetic_approval") == "completed",
        }
    capability = message.get("capability_id", "mock.create_ticket")
    task = message.get("task_id") or str(uuid.uuid4())
    approver = "synthetic:" + claims["oid"] if message.get("approved") else None
    call = runtime.call(capability, message.get("arguments", {}), claims["oid"], task, approver)
    signer = KeyVaultSigner(runtime.config["key_id"], runtime.credential, runtime.client)
    return runtime, message, call, signer


@app.post("/h9b/execution-assertions")
async def execution_assertion(request: Request):
    runtime = request.app.state.runtime
    await runtime.authenticate(request.headers.get("authorization"), gateway_only=True)
    message = await body(request)
    try:
        lease = message["lease"]
        if not isinstance(lease, str) or len(lease) > 16000:
            raise LeaseError("Missing or oversized lease")
        untrusted = jwt.decode(lease, options={"verify_signature": False})
        call = runtime.call(
            message["capability_id"],
            message["arguments"],
            untrusted.get("agent_identity"),
            untrusted.get("task_id"),
            untrusted.get("approver"),
        )
        if call.entry["id"] not in {"mock.create_ticket", "mock.deploy_release"}:
            raise LeaseError("Not a provider execution capability")
        if runtime.policy.evaluate(call).action != "allow":
            raise LeaseError("Execution policy denied")
        binding = profile_binding(runtime.bindings, call.entry["id"])
        claims = await runtime.verify_lease(lease, call, binding)
        started = time.perf_counter()
        signer = KeyVaultSigner(runtime.config["key_id"], runtime.credential, runtime.client)
        token = await sign_execution(lease, claims, signer)
        return {
            "execution_assertion": token,
            "signing_ms": (time.perf_counter() - started) * 1000,
        }
    except (LeaseError, jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(403, "Execution assertion denied") from error


@app.post("/h9b/leases")
@app.post("/leases")
async def leases(request: Request):
    runtime, message, call, signer = await prepare(request)
    audit = []
    bindings = (
        {name: profile_binding(runtime.bindings, name) for name in runtime.bindings}
        if request.url.path == "/h9b/leases"
        else runtime.bindings
    )
    middleware = LeaseMiddleware(runtime.policy, signer, runtime.entries, bindings, audit.append)
    captured = {}
    context = FunctionInvocationContext(
        function=type(
            "FixtureFunction", (), {"additional_properties": {"capability_id": call.entry["id"]}}
        )(),
        arguments=call.inputs,
        kwargs={"lease_context": call},
    )

    async def capture():
        captured.update(lease_headers({}))

    try:
        await middleware.process(context, capture)
    except MiddlewareFailure as error:
        for event in audit:
            runtime.audit({"service": "consumer", **event})
        raise HTTPException(403, "Approval or policy denied") from error
    token = captured.get("X-Capability-Lease")
    if message.get("expired_fixture"):
        token, expired_claims = await issue(
            call, bindings[call.entry["id"]], signer, clock=lambda: time.time() - 180
        )
        audit[-1].update(
            lease_id=expired_claims["jti"],
            inputs_hash=expired_claims["inputs_hash"],
            fixture="expired",
            issued_at=expired_claims["iat"],
            expires_at=expired_claims["exp"],
        )
    for event in audit:
        runtime.audit({"service": "consumer", **event})
    return {"lease": token, "task_id": call.task_id, "approver": call.approver, "audit": audit}


@app.post("/responses")
async def responses(request: Request):
    runtime, message, call, signer = await prepare(request)
    return await run_fixture(runtime, message, call, signer, request.headers["authorization"])


@app.post("/h9b/responses")
async def profile_responses(request: Request):
    runtime, message, call, signer = await prepare(request)
    if call.entry["id"] not in {"mock.create_ticket", "mock.deploy_release"}:
        raise HTTPException(403, "Only the two synthetic leased profile fixtures")
    authorization = request.headers.get("x-gateway-authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Gateway-audience caller token required")
    return await run_fixture(runtime, message, call, signer, authorization, profile=True)


class TimingOnly(FunctionMiddleware):
    def __init__(self, audit):
        self.audit = audit

    async def process(self, context, call_next):
        start = time.perf_counter()
        await call_next()
        self.audit.append({"dispatch_ms": (time.perf_counter() - start) * 1000})


async def run_fixture(
    runtime, message, call, signer, authorization, *, preissued=None, profile=False
):
    audit = []
    gateway_timings = {}
    started = time.perf_counter()
    carriage = message.get("carriage", "reserved" if profile else "header")
    bindings = (
        {name: profile_binding(runtime.bindings, name) for name in runtime.bindings}
        if profile
        else runtime.bindings
    )

    def headers(_kwargs):
        return {
            "Authorization": authorization,
            "X-Task-Id": call.task_id,
            "X-Approver": call.approver or "",
            **(
                {"X-Capability-Lease": preissued}
                if preissued
                else lease_headers(_kwargs)
                if carriage == "header"
                else {}
            ),
        }

    def parse_result(result):
        value = result.model_dump(by_alias=True)
        gateway_timings.update(value.get("_meta", {}).get("governance", {}))
        gateway_timings.update(value.get("_meta", {}).get("h9b", {}))
        gateway_timings.update(value.get("_meta", {}).get("backend", {}))
        return json.dumps(value.get("structuredContent") or value.get("content"))

    try:
        async with MCPStreamableHTTPTool(
            name="governed",
            url=(
                runtime.config["h9b_gateway_urls"][call.entry["risk_tier"]]
                if profile
                else runtime.config["gateway_url"] + ("/baseline/mcp" if preissued else "/mcp")
            ),
            header_provider=headers,
            load_prompts=False,
            parse_tool_results=parse_result,
            additional_tool_argument_names=(
                ["_capability_lease"] if carriage == "reserved" else []
            ),
        ) as tool:
            name = (
                call.entry["risk_tier"] + "_" + bindings[call.entry["id"]]["tool_name"]
                if profile
                else call.entry["id"].removeprefix("mock.")
            )
            functions = [f for f in tool.functions if f.name == name]
            if len(functions) != 1:
                raise ValueError("Selected tool missing or ambiguous")
            functions[0].additional_properties["capability_id"] = call.entry["id"]
            middleware = [
                LeaseMiddleware(
                    runtime.policy,
                    signer,
                    runtime.entries,
                    bindings,
                    audit.append,
                    carriage=carriage,
                )
            ]
            if preissued:
                middleware = [TimingOnly(audit)]
            if message.get("mutate_after_middleware"):
                middleware.append(MutationFixture())
            agent = Agent(
                client=FixtureClient(name, call.inputs),
                tools=functions,
                middleware=middleware,
                instructions="Execute the single synthetic fixture call.",
            )
            result = await agent.run(
                "Run the fixture",
                function_invocation_kwargs={
                    "lease_context": call,
                    **({"capability_lease": preissued} if preissued else {}),
                },
            )
        elapsed = (time.perf_counter() - started) * 1000
        for event in audit:
            runtime.audit({"service": "consumer", **event})
        return {
            "id": "resp_" + uuid.uuid4().hex,
            "created_at": time.time(),
            "model": "deterministic-governance-fixture",
            "object": "response",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": {"type": "function", "name": name},
            "tools": [
                {
                    "type": "function",
                    "name": name,
                    "description": call.entry["description"],
                    "parameters": call.entry["inputs"],
                    "strict": True,
                }
            ],
            "output": [
                {
                    "type": "message",
                    "id": "msg_" + uuid.uuid4().hex,
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": result.text, "annotations": []}],
                }
            ],
            "governance": {"audit": audit, "elapsed_ms": elapsed, **gateway_timings},
        }
    except MiddlewareFailure as error:
        for event in audit:
            runtime.audit({"service": "consumer", **event})
        raise HTTPException(403, "Runtime invocation terminated") from error


@app.post("/benchmark")
async def benchmark(request: Request):
    runtime, message, call, signer = await prepare(request)
    if call.identity != runtime.config.get("benchmark_principal"):
        raise HTTPException(403, "Restricted synthetic benchmark control")
    count = message.get("samples", 20)
    if (
        type(count) is not int
        or not 1 <= count <= 30
        or (call.entry["id"] not in {"mock.deploy_release", "mock.create_ticket"})
    ):
        raise HTTPException(400, "Bounded side-effecting fixture samples only")
    if runtime.policy.evaluate(call).action != "allow":
        raise HTTPException(403, "Exact-call synthetic approval required")
    rows = []
    for index in range(count + 2):
        pair = {}
        order = ["baseline", "governed"] if index % 2 else ["governed", "baseline"]
        for mode in order:
            preissued = None
            if mode == "baseline":
                preissued, _ = await issue(call, runtime.bindings[call.entry["id"]], signer)
                prepared = await runtime.client.post(
                    runtime.config["gateway_url"] + "/benchmark/prepare",
                    headers={"Authorization": request.headers["authorization"]},
                    json={
                        "capability_id": call.entry["id"],
                        "inputs": call.inputs,
                        "task_id": call.task_id,
                        "approver": call.approver,
                        "lease": preissued,
                    },
                )
                prepared.raise_for_status()
            result = await run_fixture(
                runtime, {}, call, signer, request.headers["authorization"], preissued=preissued
            )
            pair[mode] = result["governance"]
        if index >= 2:
            rows.append(pair)
    return {
        "capability_id": call.entry["id"],
        "samples": rows,
        "warmup_pairs": 2,
        "baseline_scope": (
            "Transport-equivalent ACA proxy; lease preissued outside the timed span; "
            "signature/nonce verification completed in a separate preparation request; "
            "single-use prepared context checked again at dispatch; "
            "gateway identity and independent mock lease verification remain mandatory."
        ),
    }
