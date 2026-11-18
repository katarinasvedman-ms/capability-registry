import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from backend.config import auth_mode

ROLE_PERMISSIONS = {
    "Portal.User": {"knowledge.read", "context.read"},
    "People.Reader": {"leave.read"},
    "People.LeaveRequester": {"leave.read", "leave.write"},
    "Supply.Reader": {"supply.read"},
    "Operations.Reader": {"operations.read"},
    "Operations.Requester": {"operations.read", "operations.write"},
    "Portal.Administrator": {"operations.admin"},
}


def permissions_for_roles(roles):
    return sorted(set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in roles)))


def public_user(identity):
    return {key: identity[key] for key in ("id", "name", "role", "country")}


class EntraAuthenticator:
    def __init__(self):
        try:
            self.tenant = str(UUID(os.environ["ENTRA_TENANT_ID"]))
            self.audience = str(UUID(os.environ["ENTRA_API_CLIENT_ID"]))
            self.client = str(UUID(os.environ["ENTRA_SPA_CLIENT_ID"]))
        except (KeyError, ValueError) as error:
            raise RuntimeError(
                "Entra mode requires valid tenant, API and SPA client IDs"
            ) from error
        self.issuer = f"https://login.microsoftonline.com/{self.tenant}/v2.0"
        self.keys = PyJWKClient(
            f"https://login.microsoftonline.com/{self.tenant}/discovery/v2.0/keys",
            lifespan=300,
            timeout=5,
        )

    def claims(self, token):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise jwt.InvalidTokenError()
            signing_key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "scp", "azp"],
                    "strict_aud": True,
                },
            )
            if (
                claims["tid"] != self.tenant
                or claims.get("ver") != "2.0"
                or claims["azp"] != self.client
                or not isinstance(claims["oid"], str)
                or not isinstance(claims["scp"], str)
                or "access_as_user" not in claims["scp"].split()
                or not isinstance(claims.get("roles", []), list)
                or not all(isinstance(role, str) for role in claims.get("roles", []))
            ):
                raise jwt.InvalidTokenError()
            claims["oid"] = str(UUID(claims["oid"]))
            return claims
        except jwt.PyJWKClientConnectionError:
            raise HTTPException(503, "Identity signing keys are unavailable") from None
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, "Invalid API access token") from None

    def authenticate(self, token, store):
        claims = self.claims(token)
        principal_id = f"entra:{self.tenant}:{claims['oid']}"
        configured_profile = trusted_profile(principal_id)
        if configured_profile:
            profile = {
                key: value
                for key, value in configured_profile.items()
                if key != "demo_balance_days"
            }
            store.put("users", principal_id, principal_id, {"id": principal_id, **profile})
            if "demo_balance_days" in configured_profile:
                store.put(
                    "balances",
                    principal_id,
                    principal_id,
                    {
                        "remaining_days": configured_profile["demo_balance_days"],
                    },
                    only_new=True,
                )
            if configured_profile.get("demo_case_id"):
                case_id = configured_profile["demo_case_id"]
                store.put(
                    "cases",
                    case_id,
                    principal_id,
                    {
                        "id": case_id,
                        "title": "Signed-in user's synthetic demo case",
                        "status": "In progress",
                        "details": "Synthetic Operations case; no real business data.",
                    },
                    only_new=True,
                )
                try:
                    store.get("cases", case_id, principal_id)
                except HTTPException:
                    raise HTTPException(
                        503, "Configured demo case is not owned by this profile"
                    ) from None
        try:
            profile = store.user(principal_id)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            profile = {
                "id": principal_id,
                "name": "Signed-in user",
                "country": "Unconfigured",
                "manager": "Not configured",
                "team": "Not configured",
                "enabled": True,
            }
            store.put("users", principal_id, principal_id, profile, only_new=True)
        return effective_identity(
            store,
            {
                **profile,
                "auth_mode": "entra",
                "roles": claims.get("roles", []),
                "token_expires": claims["exp"],
            },
        )


def trusted_profile(principal_id):
    path = Path(os.getenv("ENTRA_PROFILE_PATH", "data/profiles/entra.json"))
    if not path.exists():
        return None
    try:
        profiles = json.loads(path.read_text(encoding="utf-8"))
        profile = profiles.get(principal_id)
        if profile is None:
            return None
        if (
            set(profile)
            - {"name", "country", "manager", "team", "enabled", "demo_balance_days", "demo_case_id"}
            or not all(
                isinstance(profile[key], str) for key in ("name", "country", "manager", "team")
            )
            or not isinstance(profile.get("enabled"), bool)
            or (
                "demo_balance_days" in profile
                and (
                    type(profile["demo_balance_days"]) is not int
                    or not 0 <= profile["demo_balance_days"] <= 365
                )
                or (
                    "demo_case_id" in profile
                    and (
                        not isinstance(profile["demo_case_id"], str)
                        or not re.fullmatch(r"CASE-\d{4}", profile["demo_case_id"])
                    )
                )
            )
        ):
            raise ValueError()
        return profile
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, "Trusted employee profile configuration is unavailable") from None


@lru_cache(maxsize=1)
def entra_authenticator():
    return EntraAuthenticator()


def effective_identity(store, identity):
    profile = store.user(identity["id"])
    if identity.get("auth_mode") != "entra":
        if identity["id"].startswith("entra:"):
            raise HTTPException(401, "Verified Entra identity required")
        return profile
    if identity.get("token_expires", 0) <= time.time():
        raise HTTPException(401, "Access token expired; sign in again")
    if not profile.get("enabled", True):
        raise HTTPException(403, "Application access disabled")
    roles = identity.get("roles", [])
    return {
        **profile,
        "auth_mode": "entra",
        "roles": roles,
        "role": ", ".join(roles) or "No assigned application roles",
        "permissions": permissions_for_roles(roles),
        "token_expires": identity["token_expires"],
    }


def auth_configuration():
    if auth_mode() == "mock":
        return {"mode": "mock"}
    provider = entra_authenticator()
    return {
        "mode": "entra",
        "tenant_id": provider.tenant,
        "client_id": provider.client,
        "authority": f"https://login.microsoftonline.com/{provider.tenant}",
        "scopes": [f"api://{provider.audience}/access_as_user"],
    }
