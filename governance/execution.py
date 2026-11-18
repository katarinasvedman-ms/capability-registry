from __future__ import annotations

import hashlib
import time

import jwt
import rfc8785

from governance.lease import LeaseError
from governance.lease.contract import b64

EXECUTION_ISSUER = "urn:capability-registry:verifier"
EXECUTION_AUDIENCE = "urn:capability-registry:mock-execution"


def profile_binding(bindings, capability):
    binding = dict(bindings[capability])
    if capability in {"mock.create_ticket", "mock.get_build_status"}:
        binding["version"] = "3"
    return binding


def execution_claims(lease, claims):
    return {
        key: claims[key]
        for key in (
            "jti",
            "capability_id",
            "version",
            "registry_pin",
            "inputs_hash",
            "task_id",
            "agent_identity",
            "approver",
        )
    } | {"lease_digest": hashlib.sha256(lease.encode()).hexdigest()}


async def sign_execution(lease, claims, signer):
    now = int(time.time())
    payload = {
        **execution_claims(lease, claims),
        "iss": EXECUTION_ISSUER,
        "aud": EXECUTION_AUDIENCE,
        "iat": now,
        "exp": min(now + 30, claims["exp"]),
    }
    if payload["exp"] <= now:
        raise LeaseError("Lease expired before execution assertion")
    header = {"alg": "RS256", "typ": "execution+jwt", "kid": signer.key_id}
    encoded = b64(rfc8785.dumps(header)) + "." + b64(rfc8785.dumps(payload))
    signature = await signer.sign(hashlib.sha256(encoded.encode()).digest())
    return encoded + "." + b64(signature)


def verify_execution(token, public_key, key_id, lease, claims):
    try:
        header = jwt.get_unverified_header(token)
        if header != {"alg": "RS256", "typ": "execution+jwt", "kid": key_id}:
            raise LeaseError("Unexpected execution assertion header")
        proof = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=EXECUTION_AUDIENCE,
            issuer=EXECUTION_ISSUER,
            options={"require": ["iat", "exp", "jti"]},
        )
        expected = execution_claims(lease, claims)
        if (
            any(proof.get(key) != value for key, value in expected.items())
            or type(proof["iat"]) is not int
            or type(proof["exp"]) is not int
            or not 0 < proof["exp"] - proof["iat"] <= 30
            or proof["exp"] > claims["exp"]
        ):
            raise LeaseError("Execution assertion is not bound to this exact verified lease")
        return proof
    except (jwt.PyJWTError, TypeError, ValueError, KeyError) as error:
        raise LeaseError("Invalid execution assertion") from error
