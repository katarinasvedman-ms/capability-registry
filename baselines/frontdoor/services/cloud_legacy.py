"""Independent synthetic ERP API: Entra workloads, signed users, durable Blob records."""

import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import jwt
from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict, Field

from backend.auth import permissions_for_roles
from backend.store import digest
from services.legacy import INVENTORY

CAPABILITY = "operations.create_service_request"
logger = logging.getLogger("frontdoor.mock_erp")
SYNTHETIC_CASES = {
    "CASE-1042": {"owner": "alex", "title": "Store scanner replacement"},
    "CASE-1043": {"owner": "sam", "title": "Support workstation inspection"},
    "CASE-1044": {"owner": "alex", "title": "Warehouse equipment service"},
    "CASE-2042": {"owner": None, "title": "Signed-in user's synthetic demo case"},
}
bearer = HTTPBearer(auto_error=False)
delegation_header = APIKeyHeader(name="x-frontdoor-delegation", auto_error=False)


@dataclass(frozen=True)
class Settings:
    tenant: str
    audience: str
    allowed_callers: frozenset[str]
    public_key: str
    storage_url: str
    container: str
    client_id: str | None = None
    synthetic_case_owner: str | None = None

    @classmethod
    def environment(cls):
        tenant = str(UUID(os.environ["MOCK_API_TENANT_ID"]))
        case_owner = os.getenv("MOCK_API_SYNTHETIC_CASE_OWNER")
        if case_owner:
            parts = case_owner.split(":")
            if len(parts) != 3 or parts[:2] != ["entra", tenant] or str(UUID(parts[2])) != parts[2]:
                raise RuntimeError("Synthetic case owner must be an explicit Entra principal")
        return cls(
            tenant=tenant,
            audience=str(UUID(os.environ["MOCK_API_AUDIENCE"])),
            allowed_callers=frozenset(
                str(UUID(value.strip()))
                for value in os.environ["MOCK_API_ALLOWED_CALLERS"].split(",")
                if value.strip()
            ),
            public_key=os.environ["MOCK_API_DELEGATION_PUBLIC_KEY"],
            storage_url=os.environ["MOCK_API_STORAGE_URL"],
            container=os.getenv("MOCK_API_CONTAINER", "requests"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            synthetic_case_owner=case_owner,
        )


class Authentication:
    def __init__(self, settings):
        self.settings = settings
        self.keys = PyJWKClient(
            f"https://login.microsoftonline.com/{settings.tenant}/discovery/v2.0/keys",
            lifespan=300,
            timeout=5,
        )

    def verify(self, token, assertion):
        settings = self.settings
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise jwt.InvalidTokenError()
            claims = jwt.decode(
                token,
                self.keys.get_signing_key_from_jwt(token).key,
                algorithms=["RS256"],
                audience=settings.audience,
                issuer=f"https://login.microsoftonline.com/{settings.tenant}/v2.0",
                options={
                    "strict_aud": True,
                    "require": ["exp", "nbf", "iat", "tid", "oid", "azp", "roles"],
                },
            )
            if (
                claims["tid"] != settings.tenant
                or claims.get("ver") != "2.0"
                or claims["azp"] not in settings.allowed_callers
                or claims.get("scp")
                or not isinstance(claims["roles"], list)
                or "Mock.Invoke" not in claims["roles"]
            ):
                raise jwt.InvalidTokenError()
            str(UUID(claims["oid"]))
            user = jwt.decode(
                assertion,
                settings.public_key,
                algorithms=["RS256"],
                audience=settings.audience,
                issuer="frontdoor-invoker",
                options={
                    "strict_aud": True,
                    "require": ["sub", "exp", "iat", "nbf", "auth_mode", "trace_id", "roles"],
                },
            )
            parts = user["sub"].split(":")
            if (
                len(parts) != 3
                or parts[:2] != ["entra", settings.tenant]
                or str(UUID(parts[2])) != parts[2]
                or user["auth_mode"] != "entra"
                or user["exp"] - user["iat"] > 120
                or not isinstance(user["roles"], list)
                or not all(isinstance(role, str) for role in user["roles"])
            ):
                raise jwt.InvalidTokenError()
            return user
        except jwt.PyJWKClientConnectionError:
            raise HTTPException(503, "Identity signing keys unavailable") from None
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(401, "Invalid workload authentication or user delegation") from None


class BlobRecords:
    def __init__(self, settings):
        self.credential = ManagedIdentityCredential(client_id=settings.client_id)
        self.service = BlobServiceClient(
            settings.storage_url,
            credential=self.credential,
            # The pinned preview SDK defaults to a future Storage REST version.
            api_version="2023-11-03",
            retry_total=2,
            connection_timeout=5,
            read_timeout=15,
        )
        self.container = self.service.get_container_client(settings.container)

    def close(self):
        self.service.close()
        self.credential.close()

    def read(self, record_id):
        try:
            return json.loads(
                self.container.get_blob_client(record_id).download_blob(timeout=15).readall()
            )
        except ResourceNotFoundError:
            raise HTTPException(404, "Request not found") from None
        except (AzureError, ValueError) as error:
            logger.warning(
                "durable_storage_read_failed type=%s code=%s",
                type(error).__name__,
                getattr(error, "error_code", "unknown"),
            )
            raise HTTPException(503, "Durable request storage unavailable") from None

    def create(self, record_id, record):
        try:
            self.container.get_blob_client(record_id).upload_blob(
                json.dumps(record, sort_keys=True),
                overwrite=False,
                timeout=15,
            )
            return record
        except ResourceExistsError:
            existing = self.read(record_id)
            if any(
                existing[key] != record[key]
                for key in ("owner", "input_hash", "approval_id", "capability")
            ):
                raise HTTPException(409, "Approval already used for a different request") from None
            return existing
        except AzureError as error:
            logger.warning(
                "durable_storage_create_failed type=%s code=%s",
                type(error).__name__,
                getattr(error, "error_code", "unknown"),
            )
            raise HTTPException(503, "Durable request storage unavailable") from None


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=500)
    approval_id: UUID = Field(strict=False)


class HardwareOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item: Literal["laptop"]
    approval_id: UUID = Field(strict=False)


def create_app(settings=None, authentication=None, records=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.environment()
        app.state.settings = config
        app.state.auth = authentication or Authentication(config)
        app.state.records = records or BlobRecords(config)
        try:
            yield
        finally:
            if records is None:
                app.state.records.close()

    app = FastAPI(
        title="Frontdoor synthetic SAP/ERP REST API",
        version="1.0.0",
        description="Synthetic demo data only. Workload authentication and signed user delegation required.",
        lifespan=lifespan,
    )

    def user(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        assertion: str | None = Depends(delegation_header),
    ):
        if credentials is None or assertion is None:
            raise HTTPException(401, "Workload bearer token and user delegation required")
        return request.app.state.auth.verify(credentials.credentials, assertion)

    def require_permission(identity, permission):
        if permission not in permissions_for_roles(identity["roles"]):
            raise HTTPException(403, "User permission denied")

    @app.get("/health", operation_id="health")
    def health():
        return {"status": "ok", "provider": "mock_erp_synthetic", "storage": "azure_blob"}

    @app.get("/legacy/inventory/{product_id}", operation_id="get_inventory_status")
    def inventory(product_id: str, identity=Depends(user)):
        require_permission(identity, "supply.read")
        if product_id not in INVENTORY:
            raise HTTPException(404, "Product not found")
        return {"provider": "mock_erp_synthetic", **INVENTORY[product_id]}

    @app.get("/legacy/requests/{request_id}", operation_id="get_service_request")
    def get_request(request_id: str, request: Request, identity=Depends(user)):
        require_permission(identity, "operations.read")
        if request_id in SYNTHETIC_CASES:
            fixture = SYNTHETIC_CASES[request_id]
            owner = (
                request.app.state.settings.synthetic_case_owner
                if request_id == "CASE-2042"
                else fixture["owner"]
            )
            if owner != identity["sub"]:
                raise HTTPException(404, "Request not found")
            return {
                "id": request_id,
                "title": fixture["title"],
                "status": "In progress",
                "details": "Synthetic Operations case; no real business data.",
                "provider": "mock_erp_synthetic",
            }
        if not re.fullmatch(r"SR-[A-F0-9]{64}", request_id):
            raise HTTPException(404, "Request not found")
        record = request.app.state.records.read(request_id)
        if record["owner"] != identity["sub"]:
            raise HTTPException(404, "Request not found")
        return record["result"]

    def create_record(request, identity, approval_id, capability, inputs, prefix, status):
        require_permission(identity, "operations.write")
        grant = identity.get("execution", {})
        if not isinstance(grant, dict) or (
            grant.get("approval_id") != approval_id
            or grant.get("capability") != capability
            or grant.get("input_hash") != digest(inputs)
            or grant.get("approved") is not True
        ):
            raise HTTPException(403, "Matching approved exact-input execution grant required")
        request_id = prefix + hashlib.sha256(approval_id.encode()).hexdigest().upper()
        result = {
            "id": request_id,
            "status": status,
            **inputs,
            "provider": "mock_erp_synthetic",
        }
        return request.app.state.records.create(
            request_id,
            {
                "owner": identity["sub"],
                "approval_id": approval_id,
                "capability": capability,
                "input_hash": digest(inputs),
                "result": result,
            },
        )["result"]

    @app.post("/legacy/requests", operation_id="create_service_request")
    def create_request(body: CreateRequest, request: Request, identity=Depends(user)):
        return create_record(
            request,
            identity,
            str(body.approval_id),
            CAPABILITY,
            {"title": body.title},
            "SR-",
            "Open",
        )

    @app.post("/legacy/hardware-orders", operation_id="order_hardware")
    def order_hardware(body: HardwareOrder, request: Request, identity=Depends(user)):
        return create_record(
            request,
            identity,
            str(body.approval_id),
            "operations.order_hardware",
            {"item": body.item},
            "HW-",
            "Submitted",
        )

    @app.get("/legacy/hardware-orders/{order_id}", operation_id="get_hardware_order")
    def get_hardware_order(order_id: str, request: Request, identity=Depends(user)):
        require_permission(identity, "operations.read")
        if not re.fullmatch(r"HW-[A-F0-9]{64}", order_id):
            raise HTTPException(404, "Hardware order not found")
        record = request.app.state.records.read(order_id)
        if record["owner"] != identity["sub"]:
            raise HTTPException(404, "Hardware order not found")
        return record["result"]

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
            )
            for path, operations in schema["paths"].items():
                if path.startswith("/legacy/"):
                    for operation in operations.values():
                        if isinstance(operation, dict) and "operationId" in operation:
                            operation["security"] = [{"HTTPBearer": [], "APIKeyHeader": []}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app


app = create_app()
