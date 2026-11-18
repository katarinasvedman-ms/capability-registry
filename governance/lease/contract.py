from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid

import jwt
import rfc8785
from jsonschema import Draft202012Validator, ValidationError

ISSUER = "urn:capability-registry:policy"
AUDIENCE = "urn:capability-registry:gateway"
MAX_TTL = 120


class LeaseError(ValueError):
    pass


def canonical_inputs(inputs, schema):
    try:
        Draft202012Validator(schema).validate(inputs)
        return rfc8785.dumps(inputs)
    except (ValueError, TypeError, ValidationError, rfc8785.CanonicalizationError) as error:
        raise LeaseError("Invalid or uncanonicalizable inputs") from error


def input_hash(inputs, schema):
    return hashlib.sha256(canonical_inputs(inputs, schema)).hexdigest()


def pin_hash(binding):
    return hashlib.sha256(
        rfc8785.dumps(
            {
                key: binding[key]
                for key in (
                    "project_endpoint",
                    "toolbox",
                    "version",
                    "member_version",
                    "tool_name",
                    "protocol",
                    "endpoint",
                )
                if key in binding
            }
        )
    ).hexdigest()


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def issue(call, binding, signer, *, ttl=60, clock=time.time, timings=None):
    if not 0 < ttl <= MAX_TTL:
        raise LeaseError("Lease lifetime exceeds the short-lived policy")
    canonical_started = time.perf_counter()
    now = int(clock())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ttl,
        "capability_id": call.entry["id"],
        "version": call.entry["version"],
        "registry_pin": pin_hash(binding),
        "inputs_hash": input_hash(call.inputs, call.entry["inputs"]),
        "task_id": call.task_id,
        "agent_identity": call.identity,
        "approver": call.approver,
    }
    header = {"alg": "RS256", "typ": "JWT", "kid": signer.key_id}
    signing_input = (b64(rfc8785.dumps(header)) + "." + b64(rfc8785.dumps(claims))).encode()
    canonical_ms = (time.perf_counter() - canonical_started) * 1000
    sign_started = time.perf_counter()
    signature = await signer.sign(hashlib.sha256(signing_input).digest())
    if timings is not None:
        timings.update(
            canonicalisation_ms=canonical_ms, signing_ms=(time.perf_counter() - sign_started) * 1000
        )
    return signing_input.decode() + "." + b64(signature), claims


def verify(token, public_key, key_id, call, binding, *, clock=time.time):
    """Pure cryptographic/context verification; the gateway separately consumes jti atomically."""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("kid") != key_id or header.get("alg") != "RS256":
            raise LeaseError("Unexpected signing key or algorithm")
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                "require": [
                    "iss",
                    "aud",
                    "jti",
                    "iat",
                    "exp",
                    "capability_id",
                    "version",
                    "registry_pin",
                    "inputs_hash",
                    "task_id",
                    "agent_identity",
                ],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        now = int(clock())
        if (
            type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or claims["iat"] > now
            or claims["exp"] <= now
            or not 0 < claims["exp"] - claims["iat"] <= MAX_TTL
            or not isinstance(claims["jti"], str)
        ):
            raise LeaseError("Invalid lease lifetime or identifier")
        uuid.UUID(claims["jti"])
        expected = {
            "capability_id": call.entry["id"],
            "version": call.entry["version"],
            "registry_pin": pin_hash(binding),
            "inputs_hash": input_hash(call.inputs, call.entry["inputs"]),
            "task_id": call.task_id,
            "agent_identity": call.identity,
            "approver": call.approver,
        }
        if any(claims.get(key) != value for key, value in expected.items()):
            raise LeaseError("Lease does not match the exact runtime call")
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
        raise LeaseError("Missing, expired, malformed or mismatched lease") from error


def public_key_from_jwk(jwk):
    return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
