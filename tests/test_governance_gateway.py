import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("agent_framework")
pytest.importorskip("rfc8785")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # noqa: E402

from gateway import app as gateway  # noqa: E402
from governance.lease import LeaseError, issue  # noqa: E402
from governance.runtime import Runtime  # noqa: E402
from mocks import secured  # noqa: E402


def test_a2a_fixture_uses_the_same_common_contract():
    from jsonschema import Draft202012Validator

    root = Path(__file__).parents[1]
    validator = Draft202012Validator(
        json.loads((root / "contracts" / "capability.schema.json").read_text())
    )
    for entry in json.loads((root / "catalog" / "governance-fixtures.json").read_text()):
        validator.validate(entry)


async def test_consumer_model_selection_is_filtered_and_does_not_execute(boundary, monkeypatch):
    from consumer import app as consumer

    consumer.app.state.runtime = boundary.runtime
    boundary.runtime.config["callers"]["release"]["risk_tiers"] = ["general"]

    async def malicious(context, candidates, **kwargs):
        assert "mock.deploy_release" not in {entry["id"] for entry in candidates}
        return {"ids": ["mock.deploy_release"], "reason": "Attempt to widen the ceiling"}

    monkeypatch.setattr(consumer, "ProjectModel", lambda *_: malicious)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=consumer.app), base_url="http://consumer"
    ) as client:
        response = await client.post(
            "/selection",
            headers={"Authorization": "Bearer " + boundary.access("release")},
            json={"input": "Deploy a release", "mode": "model"},
        )
    assert response.status_code == 403
    assert not boundary.calls


@pytest.fixture
def boundary(monkeypatch, imported_catalog):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tenant = "synthetic-tenant"

    def access(oid, audience="https://ai.azure.com"):
        return jwt.encode(
            {
                "iss": f"https://sts.windows.net/{tenant}/",
                "aud": audience,
                "tid": tenant,
                "oid": oid,
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
            },
            private,
            algorithm="RS256",
            headers={"kid": "identity"},
        )

    class Credential:
        async def get_token(self, *_):
            return SimpleNamespace(
                token=access("gateway", "https://management.azure.com/"),
                expires_on=time.time() + 600,
            )

        async def close(self):
            pass

    class Signer:
        key_id = "lease-test-key"

        async def sign(self, digest):
            return private.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    permissions = sorted({p for e in imported_catalog for p in (e["permissions"] or [])})
    config = {
        "tenant": tenant,
        "gateway_oid": "gateway",
        "key_id": Signer.key_id,
        "callers": {
            "release": {"permissions": permissions, "risk_tiers": ["general", "privileged"]}
        },
    }
    used = set()

    async def consume(jti, partition, expires):
        key = (partition, jti)
        if key in used:
            raise LeaseError("Replay")
        used.add(key)

    runtimes = []
    for application in (gateway.app, secured.app):
        runtime = Runtime(config, Credential())
        runtime.public_key = private.public_key()
        runtime.keys = {"identity": private.public_key()}
        runtime.consume = consume
        application.state.runtime = runtime
        runtimes.append(runtime)
    calls = []

    class Transport:
        def __init__(self, _credential):
            self.evidence = []

        async def version(self, binding):
            return {"tools": [], "skills": []}

        async def list_tools(self, binding):
            entry = next(
                row
                for row in imported_catalog
                if row["invocation"].get("tool") == binding["tool_name"].split("___")[-1]
            )
            return {binding["tool_name"]: {"inputSchema": entry["inputs"]}}

        async def call_tool(self, binding, name, inputs, *, context):
            calls.append(copy.deepcopy(inputs))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=secured.app), base_url="http://mock"
            ) as client:
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": name.split("___")[-1],
                            "arguments": inputs,
                            "_meta": {"capability-registry/context": context},
                        },
                    },
                )
            response.raise_for_status()
            return response.json()["result"]

        async def aclose(self):
            pass

    monkeypatch.setattr(gateway, "FoundryTransport", Transport)
    return SimpleNamespace(
        runtime=runtimes[0], mock=runtimes[1], signer=Signer(), access=access, calls=calls
    )


@pytest.mark.parametrize(
    "name,inputs,changed",
    [
        ("deploy_release", {"release": "v1.2.3"}, {"release": "v9.9.9"}),
        ("create_ticket", {"title": "Original title"}, {"title": "Mutated title"}),
    ],
)
async def test_gateway_exact_call_mutation_replay_and_reserved_stripping(
    boundary, name, inputs, changed
):
    runtime = boundary.runtime
    call = runtime.call("mock." + name, inputs, "release", "task", "synthetic")
    token, _ = await issue(call, runtime.bindings[call.entry["id"]], boundary.signer)
    headers = {
        "Authorization": "Bearer " + boundary.access("release"),
        "X-Task-Id": "task",
        "X-Approver": "synthetic",
        "X-Capability-Lease": token,
    }

    async def invoke(client, arguments, carriage=headers):
        return await client.post(
            "/mcp",
            headers=carriage,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway"
    ) as client:
        assert (await invoke(client, changed)).status_code == 403
        assert not boundary.calls
        assert (
            await invoke(
                client, inputs, {k: v for k, v in headers.items() if k != "X-Capability-Lease"}
            )
        ).status_code == 403
        assert not boundary.calls
        results = await asyncio.gather(invoke(client, inputs), invoke(client, inputs))
        assert sorted(r.status_code for r in results) == [200, 403]
        assert boundary.calls == [inputs]
        new_token, _ = await issue(call, runtime.bindings[call.entry["id"]], boundary.signer)
        no_header = {k: v for k, v in headers.items() if k != "X-Capability-Lease"}
        response = await invoke(client, {**inputs, "_capability_lease": new_token}, no_header)
        assert response.status_code == 200 and boundary.calls[-1] == inputs
        runtime.config["benchmark_principal"] = "release"
        prepared_token, _ = await issue(call, runtime.bindings[call.entry["id"]], boundary.signer)
        prepared = await client.post(
            "/benchmark/prepare",
            headers=no_header,
            json={
                "capability_id": call.entry["id"],
                "inputs": inputs,
                "task_id": "task",
                "approver": "synthetic",
                "lease": prepared_token,
            },
        )
        assert prepared.status_code == 200
        before = len(boundary.calls)
        changed_baseline = await client.post(
            "/baseline/mcp",
            headers={
                **headers,
                "X-Capability-Lease": prepared_token,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": changed},
            },
        )
        assert changed_baseline.status_code == 403
        assert len(boundary.calls) == before


async def test_mock_rejects_direct_caller_even_with_a_valid_lease(boundary):
    runtime = boundary.runtime
    call = runtime.call(
        "mock.deploy_release", {"release": "v1.0.0"}, "release", "task", "synthetic"
    )
    token, _ = await issue(call, runtime.bindings[call.entry["id"]], boundary.signer)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured.app), base_url="http://mock"
    ) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "deploy_release",
                    "arguments": call.inputs,
                    "_meta": {
                        "capability-registry/context": {
                            "gateway_assertion": boundary.access(
                                "release", "https://management.azure.com/"
                            ),
                            "lease": token,
                            "identity": "release",
                            "task_id": "task",
                            "approver": "synthetic",
                        }
                    },
                },
            },
        )
    assert response.status_code == 403
    assert not any(e["accepted"] for e in boundary.mock.events)


@pytest.mark.parametrize("carriage", ["header", "reserved"])
async def test_consumer_real_mcp_header_provider_after_native_middleware(
    boundary, monkeypatch, carriage
):
    import socket
    import threading

    import uvicorn
    from fastapi import FastAPI

    from consumer import app as consumer

    host = FastAPI()
    host.mount("/", gateway.app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(host, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    boundary.runtime.config["gateway_url"] = f"http://127.0.0.1:{sock.getsockname()[1]}"
    consumer.app.state.runtime = boundary.runtime
    monkeypatch.setattr(consumer, "KeyVaultSigner", lambda *_: boundary.signer)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=consumer.app), base_url="http://consumer"
        ) as client:
            response = await client.post(
                "/responses",
                headers={
                    "Authorization": "Bearer " + boundary.access("release"),
                },
                json={
                    "capability_id": "mock.deploy_release",
                    "arguments": {"release": "v1.2.3"},
                    "approved": True,
                    "carriage": carriage,
                },
            )
            assert response.status_code == 200, response.text
            from openai.types.responses import Response

            Response.model_validate(response.json())
            assert boundary.calls == [{"release": "v1.2.3"}]
            assert response.json()["governance"]["audit"][0]["lease_id"]
            mutated = await client.post(
                "/responses",
                headers={
                    "Authorization": "Bearer " + boundary.access("release"),
                },
                json={
                    "capability_id": "mock.deploy_release",
                    "arguments": {"release": "v1.2.3"},
                    "approved": True,
                    "mutate_after_middleware": True,
                    "carriage": carriage,
                },
            )
            assert mutated.status_code == 403
            assert boundary.calls == [{"release": "v1.2.3"}]
            if carriage == "header":
                boundary.runtime.config["benchmark_principal"] = "release"
                measured = await client.post(
                    "/benchmark",
                    headers={
                        "Authorization": "Bearer " + boundary.access("release"),
                    },
                    json={
                        "capability_id": "mock.deploy_release",
                        "arguments": {"release": "v1.2.3"},
                        "approved": True,
                        "samples": 1,
                    },
                )
                assert measured.status_code == 200, measured.text
                pair = measured.json()["samples"][0]
                assert pair["baseline"]["benchmark_baseline"]
                assert not pair["governed"]["benchmark_baseline"]
                assert pair["governed"]["audit"][0]["signing_ms"] > 0
                assert len(boundary.calls) == 7
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        sock.close()
        assert not thread.is_alive()


async def test_native_agent_handoff_uses_an_independent_a2a_verifier(boundary, monkeypatch):
    from consumer import app as consumer

    async def route(request):
        target = secured.app if request.url.host == "mock" else gateway.app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url=str(request.url)
        ) as client:
            response = await client.request(
                request.method, str(request.url), headers=request.headers, content=request.content
            )
        return response

    boundary.runtime.config["gateway_url"] = "http://gateway"
    boundary.runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(route))
    consumer.app.state.runtime = boundary.runtime
    monkeypatch.setattr(consumer, "KeyVaultSigner", lambda *_: boundary.signer)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=consumer.app), base_url="http://consumer"
    ) as client:
        response = await client.post(
            "/handoff",
            headers={
                "Authorization": "Bearer " + boundary.access("release"),
            },
            json={
                "capability_id": "mock.action_agent",
                "arguments": {"title": "Synthetic task"},
                "approved": True,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["audit"][0]["capability_id"] == "mock.action_agent"
    received = [e for e in boundary.mock.events if e.get("service") == "sub-agent"]
    assert len(received) == 1 and received[0]["accepted"]
