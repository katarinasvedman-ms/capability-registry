import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.auth import effective_identity
from backend.config import database_path, service_secret
from backend.store import digest


def public_key_path():
    return Path(database_path()).parent / ".delegation-public.pem"


def private_key_path():
    return Path(
        os.getenv(
            "DELEGATION_PRIVATE_KEY_PATH", str(Path(database_path()).parent / ".delegation-key.pem")
        )
    )


def initialize_signing_key():
    path = private_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            with path.open("xb") as file:
                file.write(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
            if os.name != "nt":
                path.chmod(0o600)
        except FileExistsError:
            pass
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    public_key_path().write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def service_headers(identity, audience, trace_id):
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": "frontdoor-invoker",
            "aud": audience,
            "sub": identity["id"],
            "iat": now,
            "nbf": now,
            "exp": min(now + 120, int(identity.get("token_expires", now + 120))),
            "auth_mode": identity.get("auth_mode", "mock"),
            "roles": identity.get("roles", []),
            "trace_id": trace_id,
        },
        private_key_path().read_bytes(),
        algorithm="RS256",
    )
    return {
        "Authorization": f"Bearer {service_secret()}",
        "x-frontdoor-delegation": assertion,
        "x-trace-id": trace_id,
    }


def verify_delegation(assertion, audience, store):
    try:
        claims = jwt.decode(
            assertion,
            public_key_path().read_bytes(),
            algorithms=["RS256"],
            audience=audience,
            issuer="frontdoor-invoker",
            options={"require": ["sub", "exp", "iat", "nbf", "auth_mode", "trace_id"]},
        )
        identity = {
            "id": claims["sub"],
            "auth_mode": claims["auth_mode"],
            "roles": claims.get("roles", []),
            "token_expires": claims["exp"],
        }
        return effective_identity(store, identity)
    except (jwt.PyJWTError, OSError, ValueError, TypeError):
        raise HTTPException(401, "Invalid downstream delegation") from None


def cloud_delegation_assertion(
    identity, audience, trace_id, store, approval_id=None, capability_id=None, inputs=None
):
    """Mint a short cloud grant; mutation authority comes only from persisted approval."""
    identity = effective_identity(store, identity)
    if identity.get("auth_mode") != "entra":
        raise HTTPException(403, "Cloud delegation requires an Entra user")
    now = int(time.time())
    # Allow small signer/server clock skew without extending either expiration deadline.
    issued = now - 5
    claims = {
        "iss": "frontdoor-invoker",
        "aud": audience,
        "sub": identity["id"],
        "iat": issued,
        "nbf": issued,
        "exp": min(issued + 120, int(identity.get("token_expires", issued + 120))),
        "auth_mode": "entra",
        "roles": identity.get("roles", []),
        "trace_id": trace_id,
    }
    if approval_id is not None:
        approval = store.require_execution(identity["id"], approval_id, capability_id, inputs)
        claims["exp"] = min(claims["exp"], int(approval["created_at"] + 1800))
        claims["execution"] = {
            "approval_id": approval_id,
            "capability": capability_id,
            "input_hash": digest(inputs),
            "approved": True,
        }
    return jwt.encode(claims, private_key_path().read_bytes(), algorithm="RS256")
