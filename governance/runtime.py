from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from contextlib import asynccontextmanager
from email.utils import formatdate
from pathlib import Path

import httpx
import jwt
import yaml
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request

from governance.lease import LeaseError, public_key_from_jwk, verify
from governance.policy import Call
from governance.policy.evaluator import DeterministicEvaluator

ROOT = Path(__file__).resolve().parents[1]


def sanitized(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower()
            in {
                "authorization",
                "gateway_assertion",
                "lease",
                "_capability_lease",
                "x-capability-lease",
                "capability_lease",
                "execution_assertion",
                "x-gateway-authorization",
            }
            else sanitized(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


class Runtime:
    def __init__(self, config=None, credential=None, client=None):
        self.config = config or json.loads(os.environ["GOV_CONFIG"])
        self.credential = credential or DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self.client = client or httpx.AsyncClient(timeout=90, follow_redirects=False)
        self.entries = {
            e["id"]: e for e in json.loads((ROOT / "catalog" / "catalog.json").read_text())
        }
        self.bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
        for entry in json.loads((ROOT / "catalog" / "governance-fixtures.json").read_text()):
            self.entries[entry["id"]] = entry
            self.bindings[entry["id"]] = {
                "endpoint": self.config.get("mock_url", "http://mock") + "/a2a",
                "protocol": "a2a",
                "version": "1",
            }
        self.policy = DeterministicEvaluator(
            yaml.safe_load((ROOT / "governance" / "policy" / "default.yaml").read_text())
        )
        self.events = []
        self.keys = {}
        self.public_key = None
        self.prepared = {}
        self.key_lock = asyncio.Lock()

    def audit(self, event):
        row = sanitized({"at": time.time(), **event})
        self.events.append(row)
        self.events = self.events[-2000:]
        print(json.dumps(row), flush=True)

    async def close(self):
        await self.client.aclose()
        await self.credential.close()

    async def authenticate(self, authorization, *, gateway_only=False, upstream_only=False):
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise HTTPException(403, "Authenticated identity required")
        token = authorization[7:]
        if len(token) > 16000:
            raise HTTPException(403, "Invalid access token")
        try:
            header = jwt.get_unverified_header(token)
            kid = header["kid"]
            if header.get("alg") != "RS256":
                raise ValueError("Unexpected algorithm")
            if kid not in self.keys:
                response = await self.client.get(
                    f"https://login.microsoftonline.com/{self.config['tenant']}/discovery/v2.0/keys"
                )
                response.raise_for_status()
                self.keys = {k["kid"]: public_key_from_jwk(k) for k in response.json()["keys"]}
            claims = jwt.decode(
                token,
                self.keys[kid],
                algorithms=["RS256"],
                audience=[
                    "https://ai.azure.com",
                    "https://management.azure.com/",
                    "https://management.azure.com",
                ],
                issuer=[
                    f"https://sts.windows.net/{self.config['tenant']}/",
                    f"https://login.microsoftonline.com/{self.config['tenant']}/v2.0",
                ],
                options={"require": ["exp", "iat", "oid", "tid", "aud", "iss"]},
            )
            oid = claims["oid"]
            allowed = (
                {self.config["gateway_oid"]}
                if gateway_only
                else self.config.get("trusted_upstreams", [])
                if upstream_only
                else self.config["callers"]
            )
            if claims["tid"] != self.config["tenant"] or oid not in allowed:
                raise ValueError("Unpermitted principal")
            if gateway_only and claims["aud"] not in {
                "https://management.azure.com/",
                "https://management.azure.com",
            }:
                raise ValueError("Unexpected gateway audience")
            if not gateway_only and claims["aud"] != "https://ai.azure.com":
                raise ValueError("Unexpected caller audience")
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(403, "Invalid or unpermitted identity") from error

    def call(self, capability, inputs, identity, task_id, approver):
        if capability not in self.entries or identity not in self.config["callers"]:
            raise HTTPException(403, "Unknown capability or caller")
        entry = self.entries[capability]
        caller = self.config["callers"][identity]
        if entry["risk_tier"] not in caller["risk_tiers"]:
            raise HTTPException(403, "Trust tier denied")
        return Call(
            copy.deepcopy(entry),
            inputs,
            task_id,
            identity,
            frozenset(caller["permissions"]),
            approver,
        )

    async def verify_lease(self, token, call, binding):
        if not isinstance(token, str) or len(token) > 16000:
            raise LeaseError("Missing or oversized lease")
        async with self.key_lock:
            if self.public_key is None:
                access = await self.credential.get_token("https://vault.azure.net/.default")
                response = await self.client.get(
                    self.config["key_id"] + "?api-version=7.5",
                    headers={"Authorization": f"Bearer {access.token}"},
                )
                response.raise_for_status()
                key = response.json()["key"]
                if key["kid"] != self.config["key_id"]:
                    raise LeaseError("Unexpected public key version")
                self.public_key = public_key_from_jwk(key)
        return verify(token, self.public_key, self.config["key_id"], call, binding)

    async def consume(self, lease_id, partition, expires):
        token = await self.credential.get_token("https://storage.azure.com/.default")
        response = await self.client.post(
            self.config["table_url"],
            headers={
                "Authorization": f"Bearer {token.token}",
                "x-ms-version": "2019-02-02",
                "x-ms-date": formatdate(usegmt=True),
                "Accept": "application/json;odata=nometadata",
                "Prefer": "return-no-content",
            },
            json={"PartitionKey": partition, "RowKey": lease_id, "Expires": expires},
        )
        if response.status_code == 409:
            raise LeaseError("Lease already consumed")
        response.raise_for_status()


async def body(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "Request too large")
    try:
        message = json.loads(data)
        if not isinstance(message, dict):
            raise ValueError()
        return message
    except ValueError as error:
        raise HTTPException(400, "Invalid JSON object") from error


def application():
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = Runtime()
        from governance.telemetry import setup

        provider, tracer = setup(
            app.state.runtime.config["insights_connection"], os.getenv("SERVICE", "local")
        )
        app.state.tracer = tracer
        try:
            yield
        finally:
            await app.state.runtime.close()
            provider.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def trace_request(request, call_next):
        tracer = getattr(request.app.state, "tracer", None)
        if tracer is None:
            return await call_next(request)
        with tracer.start_as_current_span(request.method + " " + request.url.path) as span:
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            return response

    @app.get("/readiness")
    async def readiness():
        return {"ready": True, "service": os.getenv("SERVICE", "local")}

    @app.get("/evidence")
    async def evidence(request: Request):
        runtime = request.app.state.runtime
        await runtime.authenticate(request.headers.get("authorization"))
        return {"events": runtime.events}

    return app


def handshake(message, tools):
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "capability-registry-governed", "version": "1.0.0"},
        }
    if method in {"ping", "notifications/initialized"}:
        return {}
    if method == "tools/list":
        return {"tools": tools}
    if method in {"resources/list", "resources/templates/list", "prompts/list"}:
        return {
            {
                "resources/list": "resources",
                "resources/templates/list": "resourceTemplates",
                "prompts/list": "prompts",
            }[method]: []
        }
    raise HTTPException(400, "Unsupported MCP method")
