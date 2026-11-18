from __future__ import annotations

import copy
import hashlib
import time
from types import SimpleNamespace

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from governance.lease import LeaseError, input_hash
from governance.lease.contract import pin_hash
from governance.runtime import application, body, handshake

app = application()


@app.post("/benchmark/prepare")
async def prepare_baseline(request: Request):
    runtime = request.app.state.runtime
    caller = await runtime.authenticate(request.headers.get("authorization"))
    if caller["oid"] != runtime.config.get("benchmark_principal"):
        raise HTTPException(403, "Restricted synthetic benchmark preparation")
    value = await body(request)
    call = runtime.call(
        value["capability_id"], value["inputs"], caller["oid"], value["task_id"], value["approver"]
    )
    if runtime.policy.evaluate(call).action != "allow":
        raise HTTPException(403, "Baseline policy denied")
    token = value["lease"]
    try:
        claims = await runtime.verify_lease(token, call, runtime.bindings[call.entry["id"]])
        await runtime.consume(claims["jti"], "gateway", claims["exp"])
    except LeaseError as error:
        raise HTTPException(403, "Invalid baseline lease") from error
    runtime.prepared = {
        key: item for key, item in runtime.prepared.items() if item["exp"] > time.time()
    }
    if len(runtime.prepared) >= 64:
        raise HTTPException(429, "Too many prepared fixture leases")
    runtime.prepared[hashlib.sha256(token.encode()).hexdigest()] = claims
    return {"prepared": True, "lease_id": claims["jti"]}


@app.post("/a2a")
async def a2a(request: Request):
    runtime = request.app.state.runtime
    caller = await runtime.authenticate(request.headers.get("authorization"))
    message = await body(request)
    params = message["params"]
    inputs = params["message"]["parts"][0]["data"]
    call = runtime.call(
        "mock.action_agent",
        inputs,
        caller["oid"],
        request.headers.get("x-task-id"),
        request.headers.get("x-approver") or None,
    )
    if runtime.policy.evaluate(call).action != "allow":
        raise HTTPException(403, "Handoff policy denied")
    lease = request.headers.get("x-capability-lease")
    try:
        claims = await runtime.verify_lease(lease, call, runtime.bindings[call.entry["id"]])
        await runtime.consume(claims["jti"], "handoff-gateway", claims["exp"])
    except LeaseError as error:
        raise HTTPException(403, "Invalid handoff lease") from error
    proof = await runtime.credential.get_token("https://management.azure.com/.default")
    params["metadata"] = {
        "capability-registry/context": {
            "lease": lease,
            "identity": caller["oid"],
            "task_id": call.task_id,
            "approver": call.approver,
        }
    }
    response = await runtime.client.post(
        runtime.bindings[call.entry["id"]]["endpoint"],
        json=message,
        headers={"Authorization": f"Bearer {proof.token}"},
    )
    runtime.audit(
        {
            "service": "gateway",
            "protocol": "a2a",
            "lease_id": claims["jti"],
            "request": message,
            "status": response.status_code,
            "response": response.json(),
        }
    )
    return JSONResponse(response.json(), status_code=response.status_code)


class ForwardCredential:
    def __init__(self, token):
        self.token = token

    async def get_token(self, *_):
        return SimpleNamespace(token=self.token, expires_on=time.time() + 3600)

    async def close(self):
        pass


@app.post("/baseline/mcp")
@app.post("/mcp")
async def mcp(request: Request):
    gateway_started = time.perf_counter()
    runtime = request.app.state.runtime
    caller = await runtime.authenticate(request.headers.get("authorization"))
    baseline = request.url.path == "/baseline/mcp"
    if baseline and caller["oid"] != runtime.config.get("benchmark_principal"):
        raise HTTPException(403, "Restricted synthetic benchmark control")
    message = await body(request)
    if message.get("method") != "tools/call":
        tools = [
            {
                "name": name.removeprefix("mock."),
                "description": row["description"],
                "inputSchema": row["inputs"],
            }
            for name, row in runtime.entries.items()
            if name in {"mock.create_ticket", "mock.deploy_release", "mock.get_build_status"}
        ]
        result = handshake(message, tools)
        if "id" not in message:
            return Response(status_code=202)
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}
    params = message.get("params", {})
    name = params.get("name", "")
    capability = "mock." + name
    inputs = copy.deepcopy(params.get("arguments", {}))
    reserved = inputs.pop("_capability_lease", None)
    lease = request.headers.get("x-capability-lease") or reserved
    task = request.headers.get("x-task-id")
    approver = request.headers.get("x-approver") or None
    call = runtime.call(capability, inputs, caller["oid"], task, approver)
    binding = runtime.bindings[capability]
    record = {
        "service": "gateway",
        "request": message,
        "identity": caller["oid"],
        "capability_id": capability,
        "task_id": task,
        "request_headers": dict(request.headers),
        "carriage": "header" if request.headers.get("x-capability-lease") else "reserved",
        "benchmark_baseline": baseline,
    }
    try:
        decision = runtime.policy.evaluate(call)
        if decision.action != "allow":
            raise LeaseError("Policy denied runtime call")
        started = time.perf_counter()
        claims = None
        if decision.lease_required:
            if baseline:
                if not lease:
                    raise LeaseError("Baseline also requires a preissued lease")
                claims = runtime.prepared.pop(hashlib.sha256(lease.encode()).hexdigest(), None)
                if not claims or (
                    claims["exp"] <= time.time()
                    or claims["capability_id"] != capability
                    or claims["version"] != call.entry["version"]
                    or claims["registry_pin"] != pin_hash(binding)
                    or claims["inputs_hash"] != input_hash(inputs, call.entry["inputs"])
                    or claims["agent_identity"] != caller["oid"]
                    or claims["task_id"] != task
                    or claims["approver"] != approver
                ):
                    raise LeaseError("Baseline differs from its single-use prepared call")
            else:
                claims = await runtime.verify_lease(lease, call, binding)
                await runtime.consume(claims["jti"], "gateway", claims["exp"])
        verification = (time.perf_counter() - started) * 1000
        proof = await runtime.credential.get_token("https://management.azure.com/.default")
        context = {
            "gateway_assertion": proof.token,
            "lease": lease,
            "task_id": task,
            "identity": caller["oid"],
            "approver": approver,
            "approval_id": claims["jti"] if claims and approver else None,
        }
        transport = FoundryTransport(ForwardCredential(request.headers["authorization"][7:]))
        try:
            registry = Registry(
                list(runtime.entries.values()),
                {
                    key: value
                    for key, value in runtime.bindings.items()
                    if "project_endpoint" in value
                },
                transport,
                lambda event: runtime.audit({"service": "core", **event}),
            )
            result = await registry.execute(
                [capability],
                capability,
                caller["oid"],
                "Active approved contract; exact signed lease and trusted caller policy",
                lambda entry, pin: transport.call_tool(
                    pin, pin["tool_name"], inputs, context=context
                ),
            )
            record.update(http=transport.evidence)
        finally:
            await transport.aclose()
        record.update(
            lease_id=claims["jti"] if claims else None,
            outcome="completed",
            verification_ms=verification,
            response=result,
            gateway_total_ms=(time.perf_counter() - gateway_started) * 1000,
        )
        runtime.audit(record)
        result.setdefault("_meta", {})["governance"] = {
            "verification_ms": verification,
            "gateway_total_ms": record["gateway_total_ms"],
            "benchmark_baseline": baseline,
        }
        return JSONResponse(
            {"jsonrpc": "2.0", "id": message.get("id"), "result": result},
            headers={"X-Verification-Ms": str(verification)},
        )
    except LeaseError as error:
        record.update(outcome="denied", reason=str(error), status=403)
        runtime.audit(record)
        raise HTTPException(403, "Lease rejected") from error
    except Exception as error:
        runtime.audit({**record, "outcome": "upstream_failed", "reason": type(error).__name__})
        raise HTTPException(502, "Upstream failed closed") from error
