from __future__ import annotations

import json

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from governance.lease import LeaseError, canonical_inputs
from governance.runtime import application, body
from mocks.protocol import dispatch

app = application()


@app.post("/mcp")
async def mcp(request: Request):
    runtime = request.app.state.runtime
    message = await body(request)
    if message.get("method") != "tools/call":
        # Discovery is metadata-only; every executable call requires gateway identity.
        result = dispatch(message)
        return (
            Response(status_code=202)
            if "id" not in message
            else JSONResponse({"jsonrpc": "2.0", "id": message["id"], "result": result})
        )
    params = message.get("params", {})
    context = params.get("_meta", {}).get("capability-registry/context", {})
    record = {"service": "mock", "request": message, "accepted": False}
    try:
        await runtime.authenticate(
            "Bearer " + context.get("gateway_assertion", ""), gateway_only=True
        )
        capability = "mock." + params["name"]
        call = runtime.call(
            capability,
            params.get("arguments", {}),
            context.get("identity"),
            context.get("task_id"),
            context.get("approver"),
        )
        decision = runtime.policy.evaluate(call)
        if decision.action != "allow":
            raise LeaseError("Mock policy denied")
        if decision.lease_required:
            claims = await runtime.verify_lease(
                context.get("lease"), call, runtime.bindings[capability]
            )
            await runtime.consume(claims["jti"], "mock", claims["exp"])
            record["lease_id"] = claims["jti"]
        params["arguments"] = json.loads(canonical_inputs(call.inputs, call.entry["inputs"]))
        result = dispatch(message)
        record.update(accepted=True, response=result)
        runtime.audit(record)
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
    except (HTTPException, LeaseError, KeyError, TypeError, ValueError) as error:
        runtime.audit({**record, "reason": type(error).__name__, "status": 403})
        raise HTTPException(403, "Gateway identity and exact-call lease required") from error


@app.post("/a2a")
async def a2a(request: Request):
    runtime = request.app.state.runtime
    await runtime.authenticate(request.headers.get("authorization"), gateway_only=True)
    message = await body(request)
    params = message["params"]
    context = params.get("metadata", {}).get("capability-registry/context", {})
    inputs = params["message"]["parts"][0]["data"]
    call = runtime.call(
        "mock.action_agent",
        inputs,
        context["identity"],
        context["task_id"],
        context["approver"],
    )
    if runtime.policy.evaluate(call).action != "allow":
        raise HTTPException(403, "Handoff policy denied")
    try:
        claims = await runtime.verify_lease(
            context["lease"], call, runtime.bindings[call.entry["id"]]
        )
    except LeaseError as error:
        raise HTTPException(403, "Invalid handoff lease") from error
    await runtime.consume(claims["jti"], "a2a", claims["exp"])
    result = {
        "jsonrpc": "2.0",
        "id": message["id"],
        "result": {
            "kind": "message",
            "messageId": claims["jti"],
            "role": "agent",
            "parts": [{"kind": "text", "text": "Synthetic handoff accepted"}],
        },
    }
    runtime.audit(
        {
            "service": "sub-agent",
            "accepted": True,
            "request": message,
            "response": result,
            "lease_id": claims["jti"],
        }
    )
    return result
