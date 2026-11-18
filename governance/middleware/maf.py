from __future__ import annotations

import time
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace

from agent_framework import FunctionMiddleware, MiddlewareFailure

from governance.lease import LeaseError, input_hash, issue

CURRENT_LEASE = ContextVar("capability_lease", default=None)


def lease_headers(_runtime_kwargs):
    token = _runtime_kwargs.get("capability_lease") or CURRENT_LEASE.get()
    return {"X-Capability-Lease": token} if token else {}


class LeaseMiddleware(FunctionMiddleware):
    def __init__(self, evaluator, signer, entries, bindings, audit, *, carriage="header"):
        if carriage not in {"header", "reserved"}:
            raise ValueError("Unknown lease carriage")
        self.evaluator, self.signer = evaluator, signer
        self.entries, self.bindings, self.audit = entries, bindings, audit
        self.argument = "capability_lease" if carriage == "header" else "_capability_lease"

    async def process(self, context, call_next):
        trusted = context.kwargs.get("lease_context")
        capability_id = context.function.additional_properties.get("capability_id")
        if trusted is None or capability_id not in self.entries:
            raise MiddlewareFailure("Missing trusted runtime capability context")
        call = replace(
            trusted, entry=self.entries[capability_id], inputs=deepcopy(dict(context.arguments))
        )
        policy_started = time.perf_counter()
        decision = self.evaluator.evaluate(call)
        policy_ms = (time.perf_counter() - policy_started) * 1000
        record = {
            "lease_id": None,
            "capability_id": capability_id,
            "version": call.entry["version"],
            "inputs_hash": None,
            "task_id": call.task_id,
            "identity": call.identity,
            "decision": decision.action,
            "approver": call.approver,
            "gateway_result": None,
            "policy_ms": policy_ms,
        }
        carrier = None
        try:
            if call.approver and (
                trusted.entry["id"] != capability_id
                or input_hash(trusted.inputs, call.entry["inputs"])
                != input_hash(call.inputs, call.entry["inputs"])
            ):
                record["decision"] = "deny"
                raise LeaseError("Approval belongs to a different capability or inputs")
            if decision.action != "allow":
                raise MiddlewareFailure(decision.reason)
            if decision.lease_required:
                token, claims = await issue(
                    call, self.bindings[capability_id], self.signer, timings=record
                )
                record.update(lease_id=claims["jti"], inputs_hash=claims["inputs_hash"])
                context.metadata["capability_lease"] = token
                context.kwargs[self.argument] = token
                carrier = CURRENT_LEASE.set(token)
                if (
                    input_hash(dict(context.arguments), call.entry["inputs"])
                    != record["inputs_hash"]
                ):
                    raise LeaseError("Arguments changed during lease issuance")
            dispatch_started = time.perf_counter()
            await call_next()
            record["dispatch_ms"] = (time.perf_counter() - dispatch_started) * 1000
            if decision.lease_required and (
                input_hash(dict(context.arguments), call.entry["inputs"]) != record["inputs_hash"]
            ):
                raise LeaseError("Arguments changed after authorization")
            record["gateway_result"] = "dispatch_returned_unverified"
        except Exception as error:
            record["gateway_result"] = "terminated"
            raise MiddlewareFailure("Runtime call denied or failed closed") from error
        finally:
            if carrier is not None:
                CURRENT_LEASE.reset(carrier)
                context.kwargs.pop(self.argument, None)
            self.audit(record)
