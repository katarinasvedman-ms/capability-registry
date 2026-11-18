import io
import json
import time
import urllib.error
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.auth import EntraAuthenticator, entra_authenticator
from backend.delegation import service_headers
from services.security import ServiceAuth, require_permission

TENANT = str(uuid4())
API = str(uuid4())
SPA = str(uuid4())
ALICE = str(uuid4())
BOB = str(uuid4())


@pytest.fixture
def entra(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_API_CLIENT_ID", API)
    monkeypatch.setenv("ENTRA_SPA_CLIENT_ID", SPA)
    entra_authenticator.cache_clear()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = [json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {"kid": "first"}]
    fetches = []

    def fetch(opener, request, **kwargs):
        fetches.append(request.full_url)
        return io.BytesIO(json.dumps({"keys": keys}).encode())

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fetch)

    def token(changes=None, signing_key=None, kid="first"):
        now = int(time.time())
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": API,
            "tid": TENANT,
            "oid": ALICE,
            "azp": SPA,
            "ver": "2.0",
            "scp": "access_as_user",
            "roles": ["Portal.User", "People.Reader"],
            "iat": now,
            "nbf": now - 1,
            "exp": now + 300,
        }
        claims.update(changes or {})
        return jwt.encode(claims, signing_key or key, algorithm="RS256", headers={"kid": kid})

    yield client, token, keys, fetches
    entra_authenticator.cache_clear()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": SPA},
        {"iss": "https://example.invalid/v2.0"},
        {"tid": str(uuid4())},
        {"azp": str(uuid4())},
        {"exp": 1},
        {"nbf": int(time.time()) + 3600},
        {"scp": ""},
        {"ver": "1.0"},
        {"roles": "Operations.Requester"},
        {"oid": "alex"},
    ],
)
def test_invalid_access_tokens_fail_closed(entra, changes):
    client, token, _, _ = entra
    assert client.get("/api/capabilities", headers=bearer(token(changes))).status_code == 401


def test_signature_id_token_and_mock_session_are_rejected(entra):
    client, token, _, _ = entra
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.get("/api/me", headers=bearer(token(signing_key=other_key))).status_code == 401
    assert client.get("/api/me", headers=bearer(token({"scp": None}))).status_code == 401
    assert client.get("/api/me", headers=bearer("alex")).status_code == 401
    assert client.post("/api/session", json={"user_id": "alex"}).status_code == 404
    assert client.get("/api/auth/config").json()["client_id"] == SPA


def test_roles_filter_discovery_and_direct_invocation_independently(entra):
    client, token, _, _ = entra
    headers = bearer(token({"roles": ["Portal.User", "Imaginary.Administrator"]}))
    user = client.get("/api/me", headers=headers).json()["user"]
    assert user["id"] == f"entra:{TENANT}:{ALICE}"
    assert user["country"] == "Unconfigured"
    assert not any(
        row["id"].startswith("operations.")
        for row in client.get("/api/capabilities", headers=headers).json()
    )
    assert (
        client.get("/api/capabilities/operations.admin_report", headers=headers).status_code == 404
    )
    assert (
        client.post(
            "/api/capabilities/invoke",
            headers=headers,
            json={
                "capability_id": "operations.create_service_request",
                "inputs": {"title": "scanner"},
            },
        ).status_code
        == 404
    )
    assert client.get("/api/capabilities", headers=bearer(token({"roles": []}))).json() == []
    assert (
        client.post(
            "/api/capabilities/search",
            headers=headers,
            json={
                "goal": "service request",
                "permissions": ["operations.write"],
            },
        ).status_code
        == 422
    )


def test_owned_records_and_signed_downstream_use_entra_subject_not_alex(entra):
    client, token, _, _ = entra
    alice = bearer(token())
    bob = bearer(token({"oid": BOB}))
    alice_id = client.get("/api/me", headers=alice).json()["user"]["id"]
    client.get("/api/me", headers=bob)
    store = client.app.state.store
    conv = store.new_conversation(alice_id, "Isolated conversation")
    approval = store.approval(
        alice_id,
        conv["id"],
        "people.request_leave",
        {"start_date": "2027-01-11", "end_date": "2027-01-12"},
    )
    store.put("tasks", "entra-task", alice_id, {"id": "entra-task", "status": "completed"})
    store.put("cases", "CASE-2200", alice_id, {"id": "CASE-2200"})
    assert client.get(f"/api/conversations/{conv['id']}", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/approvals/{approval['id']}", headers=bob, json={"approved": True}
        ).status_code
        == 404
    )
    assert client.get("/api/tasks/entra-task", headers=bob).status_code == 404
    bob_ops = bearer(token({"oid": BOB, "roles": ["Operations.Reader"]}))
    assert client.get("/api/lob/cases/CASE-2200", headers=bob_ops).status_code == 404
    store.put("balances", alice_id, alice_id, {"remaining_days": 17})
    result = client.post(
        "/api/capabilities/invoke",
        headers=alice,
        json={
            "capability_id": "people.leave_balance",
            "inputs": {},
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["remaining_days"] == 17
    profile = store.user(alice_id)
    profile["enabled"] = False
    store.put("users", alice_id, alice_id, profile)
    assert client.get("/api/me", headers=alice).status_code == 403
    profile["enabled"] = True
    store.put("users", alice_id, alice_id, profile)


def test_key_rotation_and_signing_provider_failure(entra, monkeypatch):
    client, token, keys, fetches = entra
    assert client.get("/api/me", headers=bearer(token())).status_code == 200
    entra_authenticator().keys.cooldown_duration = 0
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys[:] = [
        json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rotated.public_key())) | {"kid": "second"}
    ]
    assert (
        client.get("/api/me", headers=bearer(token(signing_key=rotated, kid="second"))).status_code
        == 200
    )
    assert len(fetches) == 2

    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unavailable)
    assert client.get("/api/me", headers=bearer(token(kid="unknown"))).status_code == 503


def test_downstream_rejects_unsigned_and_wrong_audience_and_subject(entra):
    client, token, _, _ = entra
    identity = EntraAuthenticator().authenticate(token(), client.app.state.store)
    app = FastAPI()
    app.add_middleware(ServiceAuth, audience="gateway")

    @app.get("/check")
    def check(request: Request):
        return require_permission(request.query_params["user"], "leave.read")["id"]

    with TestClient(app) as service:
        headers = service_headers(identity, "gateway", "trace")
        assert (
            service.get("/check", params={"user": identity["id"]}, headers=headers).status_code
            == 200
        )
        assert service.get("/check", params={"user": "alex"}, headers=headers).status_code == 403
        wrong = service_headers(identity, "ess", "trace")
        assert (
            service.get("/check", params={"user": identity["id"]}, headers=wrong).status_code == 401
        )
        del headers["x-frontdoor-delegation"]
        headers["x-demo-user"] = "alex"
        assert service.get("/check", params={"user": "alex"}, headers=headers).status_code == 401


def test_employee_attributes_require_trusted_server_profile(entra, monkeypatch, tmp_path):
    client, token, _, _ = entra
    headers = bearer(
        token(
            {"oid": BOB, "country": "Sweden", "name": "Alex", "permissions": ["operations.admin"]}
        )
    )
    user = client.get("/api/me", headers=headers).json()["user"]
    assert user["country"] == "Unconfigured"
    assert user["name"] != "Alex"
    path = tmp_path / "profiles.json"
    monkeypatch.setenv("ENTRA_PROFILE_PATH", str(path))
    path.write_text(
        json.dumps(
            {
                user["id"]: {
                    "name": "Synthetic profile",
                    "country": "Sweden",
                    "manager": "Demo manager",
                    "team": "Demo team",
                    "enabled": True,
                    "demo_balance_days": 19,
                    "demo_case_id": "CASE-2242",
                }
            }
        )
    )
    assert client.get("/api/me", headers=headers).json()["user"]["country"] == "Sweden"
    balance = client.post(
        "/api/capabilities/invoke",
        headers=headers,
        json={
            "capability_id": "people.leave_balance",
            "inputs": {},
        },
    )
    assert balance.json()["remaining_days"] == 19
    own_case = bearer(token({"oid": BOB, "roles": ["Operations.Reader"]}))
    foreign_case = bearer(token({"roles": ["Operations.Reader"]}))
    assert client.get("/api/lob/cases/CASE-2242", headers=own_case).status_code == 200
    assert client.get("/api/lob/cases/CASE-2242", headers=foreign_case).status_code == 404
    assert (
        client.get("/api/capabilities/operations.admin_report", headers=headers).status_code == 404
    )
    configuration = json.loads(path.read_text())
    configuration[user["id"]]["demo_case_id"] = "CASE-1042"
    path.write_text(json.dumps(configuration))
    assert client.get("/api/me", headers=headers).status_code == 503
    assert client.app.state.store.get("cases", "CASE-1042", "alex")["id"] == "CASE-1042"
    path.write_text('{"invalid":')
    assert client.get("/api/me", headers=headers).status_code == 503
