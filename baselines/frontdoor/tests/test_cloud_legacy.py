import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.store import Store, digest
from services.cloud_legacy import CAPABILITY, Authentication, BlobRecords, Settings, create_app

TENANT = str(uuid.uuid4())
AUDIENCE = str(uuid.uuid4())
CALLER = str(uuid.uuid4())
OWNER = f"entra:{TENANT}:{uuid.uuid4()}"
OTHER = f"entra:{TENANT}:{uuid.uuid4()}"


class ConditionalBlobs:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def get_blob_client(self, name):
        def upload_blob(value, overwrite, **kwargs):
            assert overwrite is False
            with self.lock:
                if name in self.data:
                    raise ResourceExistsError("already exists")
                self.data[name] = value.encode()

        def download_blob(**kwargs):
            with self.lock:
                if name not in self.data:
                    raise ResourceNotFoundError("missing")
                data = self.data[name]
            return SimpleNamespace(readall=lambda: data)

        return SimpleNamespace(upload_blob=upload_blob, download_blob=download_blob)


@pytest.fixture
def cloud():
    workload_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    user_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        user_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings = Settings(
        TENANT,
        AUDIENCE,
        frozenset([CALLER]),
        public,
        "unused",
        "requests",
        synthetic_case_owner=OWNER,
    )
    auth = Authentication(settings)
    auth.keys = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=workload_key.public_key())
    )
    records = BlobRecords.__new__(BlobRecords)
    records.container = ConditionalBlobs()

    def headers(workload=None, delegation=None):
        now = int(time.time())
        token = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": AUDIENCE,
            "tid": TENANT,
            "oid": str(uuid.uuid4()),
            "azp": CALLER,
            "ver": "2.0",
            "roles": ["Mock.Invoke"],
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            **(workload or {}),
        }
        grant = {
            "iss": "frontdoor-invoker",
            "aud": AUDIENCE,
            "sub": OWNER,
            "auth_mode": "entra",
            "roles": ["Supply.Reader", "Operations.Requester"],
            "trace_id": "test",
            "iat": now,
            "nbf": now,
            "exp": now + 120,
            **(delegation or {}),
        }
        return {
            "Authorization": "Bearer "
            + jwt.encode(token, workload_key, algorithm="RS256", headers={"kid": "workload"}),
            "x-frontdoor-delegation": jwt.encode(grant, user_key, algorithm="RS256"),
        }

    with TestClient(create_app(settings, auth, records)) as client:
        yield SimpleNamespace(
            client=client, headers=headers, records=records, user_key=user_key, settings=settings
        )


def execution(approval_id, title):
    return {
        "approved": True,
        "approval_id": approval_id,
        "capability": CAPABILITY,
        "input_hash": digest({"title": title}),
    }


def test_public_schema_and_fail_closed_business_api(cloud):
    assert cloud.client.get("/health").status_code == 200
    schema = cloud.client.get("/openapi.json").json()
    assert schema["paths"]["/legacy/requests"]["post"]["operationId"] == "create_service_request"
    for path, operations in schema["paths"].items():
        if path.startswith("/legacy/"):
            for operation in operations.values():
                assert operation["security"] == [{"HTTPBearer": [], "APIKeyHeader": []}]
    assert cloud.client.get("/legacy/inventory/P-100").status_code == 401
    assert (
        cloud.client.get("/legacy/inventory/P-100", headers={"x-demo-user": "alex"}).status_code
        == 401
    )
    response = cloud.client.get("/legacy/inventory/P-100", headers=cloud.headers())
    assert response.status_code == 200
    assert response.json()["available"] == 80
    assert (
        cloud.client.get("/legacy/inventory/P-200", headers=cloud.headers()).json()["available"]
        == 240
    )


def test_hardware_order_requires_matching_grant_and_preserves_owned_idempotency(cloud):
    approval = str(uuid.uuid4())
    body = {"item": "laptop", "approval_id": approval}
    grant = {
        "approved": True,
        "approval_id": approval,
        "capability": "operations.order_hardware",
        "input_hash": digest({"item": "laptop"}),
    }
    assert (
        cloud.client.post("/legacy/hardware-orders", json=body, headers=cloud.headers()).status_code
        == 403
    )
    wrong = {**grant, "capability": CAPABILITY}
    assert (
        cloud.client.post(
            "/legacy/hardware-orders",
            json=body,
            headers=cloud.headers(delegation={"execution": wrong}),
        ).status_code
        == 403
    )
    headers = cloud.headers(delegation={"execution": grant})
    created = cloud.client.post("/legacy/hardware-orders", json=body, headers=headers)
    assert created.status_code == 200
    order = created.json()
    assert order["id"].startswith("HW-") and order["status"] == "Submitted"
    assert cloud.client.post("/legacy/hardware-orders", json=body, headers=headers).json() == order
    assert (
        cloud.client.get("/legacy/hardware-orders/" + order["id"], headers=cloud.headers()).json()
        == order
    )
    assert (
        cloud.client.get(
            "/legacy/hardware-orders/" + order["id"],
            headers=cloud.headers(delegation={"sub": OTHER}),
        ).status_code
        == 404
    )
    assert (
        cloud.client.post(
            "/legacy/hardware-orders",
            json=body,
            headers=cloud.headers(delegation={"roles": ["Portal.User"], "execution": grant}),
        ).status_code
        == 403
    )
    assert (
        cloud.client.post(
            "/legacy/hardware-orders", json={**body, "item": "desktop"}, headers=headers
        ).status_code
        == 422
    )


def test_synthetic_cases_preserve_explicit_owner_and_local_identity_boundaries(cloud):
    case = cloud.client.get("/legacy/requests/CASE-2042", headers=cloud.headers())
    assert case.status_code == 200
    assert case.json()["provider"] == "mock_erp_synthetic"
    assert (
        cloud.client.get(
            "/legacy/requests/CASE-2042", headers=cloud.headers(delegation={"sub": OTHER})
        ).status_code
        == 404
    )
    assert (
        cloud.client.get("/legacy/requests/CASE-1042", headers=cloud.headers()).status_code == 404
    )
    assert (
        cloud.client.get(
            "/legacy/requests/CASE-2042",
            headers=cloud.headers(delegation={"roles": ["Portal.User"]}),
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": str(uuid.uuid4())},
        {"azp": str(uuid.uuid4())},
        {"roles": []},
        {"roles": ["Portal.Administrator"]},
        {"tid": str(uuid.uuid4())},
        {"scp": "access_as_user"},
        {"exp": 1},
        {"ver": "1.0"},
    ],
)
def test_wrong_workload_token_rejected(cloud, changes):
    assert (
        cloud.client.get(
            "/legacy/inventory/P-100", headers=cloud.headers(workload=changes)
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_mode": "mock", "sub": "alex"},
        {"aud": str(uuid.uuid4())},
        {"exp": 1},
        {"roles": "Supply.Reader"},
        {"sub": f"entra:{uuid.uuid4()}:{uuid.uuid4()}"},
    ],
)
def test_wrong_user_assertion_rejected(cloud, changes):
    assert (
        cloud.client.get(
            "/legacy/inventory/P-100", headers=cloud.headers(delegation=changes)
        ).status_code
        == 401
    )


def test_tampered_grant_and_denied_business_role(cloud):
    headers = cloud.headers()
    token = headers["x-frontdoor-delegation"].split(".")
    token[2] = ("A" if token[2][0] != "A" else "B") + token[2][1:]
    headers["x-frontdoor-delegation"] = ".".join(token)
    assert cloud.client.get("/legacy/inventory/P-100", headers=headers).status_code == 401
    assert (
        cloud.client.get(
            "/legacy/inventory/P-100", headers=cloud.headers(delegation={"roles": ["Portal.User"]})
        ).status_code
        == 403
    )


def test_exact_bound_approval_atomic_retry_and_owner_isolation(cloud):
    approval_id = str(uuid.uuid4())
    body = {"title": "Synthetic scanner fault", "approval_id": approval_id}
    grant = execution(approval_id, body["title"])
    headers = cloud.headers(delegation={"execution": grant})
    assert (
        cloud.client.post("/legacy/requests", json=body, headers=cloud.headers()).status_code == 403
    )
    changed = {**body, "title": "Different inputs"}
    assert cloud.client.post("/legacy/requests", json=changed, headers=headers).status_code == 403
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(
                lambda _: cloud.client.post("/legacy/requests", json=body, headers=headers),
                range(12),
            )
        )
    assert all(response.status_code == 200 for response in responses)
    assert len({json.dumps(response.json(), sort_keys=True) for response in responses}) == 1
    assert len(cloud.records.container.data) == 1
    result = responses[0].json()
    assert (
        cloud.client.get("/legacy/requests/" + result["id"], headers=cloud.headers()).json()
        == result
    )
    assert (
        cloud.client.get(
            "/legacy/requests/" + result["id"], headers=cloud.headers(delegation={"sub": OTHER})
        ).status_code
        == 404
    )
    other_owner = cloud.headers(delegation={"sub": OTHER, "execution": grant})
    assert cloud.client.post("/legacy/requests", json=body, headers=other_owner).status_code == 409
    rebound = cloud.headers(delegation={"execution": execution(approval_id, changed["title"])})
    assert cloud.client.post("/legacy/requests", json=changed, headers=rebound).status_code == 409
    expired = cloud.headers(delegation={"execution": grant, "exp": 1})
    assert cloud.client.post("/legacy/requests", json=body, headers=expired).status_code == 401


def test_backend_signer_checks_persisted_approval_before_minting(monkeypatch):
    from backend.delegation import (
        cloud_delegation_assertion,
        initialize_signing_key,
        public_key_path,
    )

    root = Path(__file__).resolve().parents[1] / "data" / ("cloud-test-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    monkeypatch.setenv("FRONTDOOR_DB", str(root / "test.db"))
    monkeypatch.setenv("DELEGATION_PRIVATE_KEY_PATH", str(root / "key.pem"))
    try:
        initialize_signing_key()
        store = Store()
        store.put("users", OWNER, OWNER, {"id": OWNER, "enabled": True})
        identity = {
            "id": OWNER,
            "auth_mode": "entra",
            "roles": ["Operations.Requester"],
            "token_expires": int(time.time()) + 300,
        }
        inputs = {"title": "Test"}
        approval = store.approval(OWNER, "cloud-test", CAPABILITY, inputs)

        def mint(values=inputs):
            return cloud_delegation_assertion(
                identity, AUDIENCE, "trace", store, approval["id"], CAPABILITY, values
            )

        with pytest.raises(HTTPException):
            mint()
        store.transition(approval["id"], OWNER, {"pending"}, "approved")
        store.transition(approval["id"], OWNER, {"approved"}, "executing")
        token = mint()
        claims = jwt.decode(
            token, public_key_path().read_bytes(), algorithms=["RS256"], audience=AUDIENCE
        )
        assert claims["execution"] == execution(approval["id"], "Test")
        with pytest.raises(HTTPException):
            mint({"title": "Changed"})
        store.transition(approval["id"], OWNER, {"executing"}, "denied")
        with pytest.raises(HTTPException):
            mint()
    finally:
        for attempt in range(10):
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)
