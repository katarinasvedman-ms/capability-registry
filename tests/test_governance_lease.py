import copy
import hashlib
import time
from pathlib import Path

import pytest

pytest.importorskip("rfc8785")
jwt = pytest.importorskip("jwt")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from governance.lease import LeaseError, canonical_inputs, issue, verify  # noqa: E402
from governance.policy import Call  # noqa: E402
from governance.policy.evaluator import DeterministicEvaluator  # noqa: E402


@pytest.fixture
def action(imported_catalog):
    entry = next(row for row in imported_catalog if row["id"] == "mock.deploy_release")
    return Call(
        entry,
        {"release": "v1.2.3"},
        "task-1",
        "release-oid",
        frozenset(entry["permissions"]),
        "synthetic-reviewer",
    )


@pytest.fixture
def signer():
    class Signer:
        key_id = "test-key-version"
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        async def sign(self, digest):
            from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

            return self.private_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    return Signer()


@pytest.mark.parametrize("change", ["inputs", "task_id", "identity", "version", "pin", "expired"])
async def test_exact_lease_rejects_context_mutation(action, signer, binding, change):
    from dataclasses import replace

    token, claims = await issue(action, binding, signer)
    public_key = signer.private_key.public_key()
    assert verify(token, public_key, signer.key_id, action, binding) == claims
    modified, pin, clock = action, binding, time.time
    if change == "inputs":
        modified = replace(action, inputs={"release": "v9.9.9"})
    elif change == "task_id":
        modified = replace(action, task_id="other-task")
    elif change == "identity":
        modified = replace(action, identity="coder-oid")
    elif change == "version":
        modified = replace(action, entry={**action.entry, "version": "2.0.0"})
    elif change == "pin":
        pin = {**binding, "version": "2"}
    else:

        def clock():
            return claims["exp"]

    with pytest.raises(LeaseError):
        verify(token, public_key, signer.key_id, modified, pin, clock=clock)


def test_canonicalisation_preserves_values_not_json_formatting():
    schema = {"type": "object"}
    assert canonical_inputs({"b": 1.0, "a": -0.0}, schema) == b'{"a":0,"b":1}'
    assert canonical_inputs({"a": 0, "b": 1}, schema) == b'{"a":0,"b":1}'
    assert hashlib.sha256(canonical_inputs({"a": "x y"}, schema)).digest() != (
        hashlib.sha256(canonical_inputs({"a": "xy"}, schema)).digest()
    )
    with pytest.raises(LeaseError):
        canonical_inputs({"value": float("nan")}, schema)


async def test_invalid_inputs_cannot_be_signed(action, signer, binding):
    from dataclasses import replace

    with pytest.raises(LeaseError):
        await issue(replace(action, inputs={"release": "not-a-release"}), binding, signer)


def test_policy_requires_approval_and_never_leases_skill_loading(action, imported_catalog):
    from dataclasses import replace

    import yaml

    path = Path(__file__).parents[1] / "governance" / "policy" / "default.yaml"
    policy = DeterministicEvaluator(yaml.safe_load(path.read_text()))
    assert policy.evaluate(action).action == "allow"
    pending = policy.evaluate(replace(action, approver=None))
    assert pending.action == "require_approval" and pending.lease_required
    assert policy.evaluate(replace(action, permissions=frozenset())).action == "deny"
    skill = next(entry for entry in imported_catalog if entry["type"] == "skill")
    decision = policy.evaluate(replace(action, entry=skill, inputs={}))
    assert decision.action == "allow" and not decision.lease_required
    retired = copy.deepcopy(action.entry)
    retired["lifecycle"] = "retired"
    assert policy.evaluate(replace(action, entry=retired)).action == "deny"


async def test_unknown_signing_key_and_unsigned_leases_fail_closed(action, signer, binding):
    token, claims = await issue(action, binding, signer)
    with pytest.raises(LeaseError):
        verify(token, signer.private_key.public_key(), "other-key", action, binding)
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": signer.key_id})
    with pytest.raises(LeaseError):
        verify(unsigned, signer.private_key.public_key(), signer.key_id, action, binding)


@pytest.mark.parametrize("mutate", [False, True])
async def test_real_maf_middleware_stops_pending_approval_before_dispatch(
    action, signer, binding, mutate
):
    from dataclasses import replace
    from types import SimpleNamespace

    import yaml
    from agent_framework import FunctionInvocationContext, MiddlewareFailure

    from governance.middleware.maf import LeaseMiddleware, lease_headers

    policy_path = Path(__file__).parents[1] / "governance" / "policy" / "default.yaml"
    audit, dispatched = [], []
    middleware = LeaseMiddleware(
        DeterministicEvaluator(yaml.safe_load(policy_path.read_text())),
        signer,
        {action.entry["id"]: action.entry},
        {action.entry["id"]: binding},
        audit.append,
    )
    context = FunctionInvocationContext(
        function=SimpleNamespace(additional_properties={"capability_id": action.entry["id"]}),
        arguments=action.inputs,
        kwargs={"lease_context": replace(action, approver=None)},
    )

    async def dispatch():
        dispatched.append(lease_headers({}))

    with pytest.raises(MiddlewareFailure):
        await middleware.process(context, dispatch)
    assert not dispatched and audit[-1]["decision"] == "require_approval"
    assert audit[-1]["lease_id"] is None and audit[-1]["approver"] is None
    context.kwargs["lease_context"] = action
    if mutate:
        context.arguments = {"release": "v9.9.9"}
        with pytest.raises(MiddlewareFailure):
            await middleware.process(context, dispatch)
        assert not dispatched and audit[-1]["decision"] == "deny"
        assert audit[-1]["lease_id"] is None
        return
    await middleware.process(context, dispatch)
    assert len(dispatched) == 1 and dispatched[0]["X-Capability-Lease"]
    assert audit[-1]["decision"] == "allow" and audit[-1]["lease_id"]
    assert lease_headers({}) == {}
