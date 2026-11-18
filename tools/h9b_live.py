from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid

import httpx
from azure.identity.aio import CertificateCredential

from governance.runtime import sanitized
from tools.capture_sources import ROOT
from tools.h9b_spike import H9b, now
from tools.provision_governance import Deployment


class Live:
    def __init__(self):
        self.spike = H9b()
        self.spike.check_time()
        self.config = Deployment().config()
        h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
        self.identities = {**h4, "viewer": self.spike.state["viewer"]}
        self.credentials = {}
        self.client = httpx.AsyncClient(timeout=180)
        self.bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
        self.servers = {
            s["name"]: s["properties"]["gatewayEndpoints"][0]["url"]
            for s in self.spike.state["baseline"]["servers"]
        }

    def credential(self, who):
        self.spike.check_time()
        if who not in self.credentials:
            row = self.identities[who]
            self.credentials[who] = CertificateCredential(
                row["tenant_id"], row["client_id"], certificate_path=row["certificate"]
            )
        return self.credentials[who]

    async def headers(self, who, *, gateway=False, both=False):
        self.spike.check_time()
        scope = (
            "https://ai.gateway.azure.com/.default" if gateway else "https://ai.azure.com/.default"
        )
        token = await self.credential(who).get_token(scope)
        result = {
            "Authorization": "Bearer " + token.token,
            "Accept": "application/json, text/event-stream",
        }
        if both:
            access = await self.credential(who).get_token("https://ai.gateway.azure.com/.default")
            result["X-Gateway-Authorization"] = "Bearer " + access.token
        return result

    async def http(self, method, url, *, headers=None, value=None, label=None):
        return await self.spike.http(
            self.client, method, url, headers=headers, value=value, label=label
        )

    async def events(self, service):
        response = await self.http(
            "GET",
            self.config[service + "_url"] + "/evidence",
            headers=await self.headers("release"),
            label="evidence:" + service,
        )
        response.raise_for_status()
        return response.json()["events"]

    async def count(self, service="mock"):
        events = await self.events(service)
        return len(
            [e for e in events if e.get("service") == "h9b-verifier"]
            if service == "gateway"
            else events
        )

    def stop(self, reason):
        self.spike.state["stop"] = {"at": now(), "reason": reason}
        self.spike.save()
        raise RuntimeError("STOP: " + reason)

    async def issue(self, who, capability, inputs, **options):
        response = await self.http(
            "POST",
            self.config["consumer_url"] + "/h9b/leases",
            headers=await self.headers(who),
            value={"capability_id": capability, "arguments": inputs, "approved": True, **options},
            label="lease:" + capability,
        )
        response.raise_for_status()
        return response.json()

    async def invoke(self, who, capability, inputs, issued=None, *, header=False):
        view = "privileged" if capability.endswith("deploy_release") else "general"
        arguments = dict(inputs)
        headers = await self.headers(who, gateway=True)
        if issued:
            if header:
                headers["X-Capability-Lease"] = issued["lease"]
            else:
                arguments["_capability_lease"] = issued["lease"]
        return await self.http(
            "POST",
            self.servers[view],
            headers=headers,
            value={
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "tools/call",
                "params": {
                    "name": view + "_" + self.bindings[capability]["tool_name"],
                    "arguments": arguments,
                },
            },
            label="gateway-call:" + capability,
        )

    @staticmethod
    def succeeded(response):
        if response.status_code != 200:
            return False
        message = response.json()
        return (
            "result" in message and not message["result"].get("isError") and "error" not in message
        )

    async def consumer(self, who, capability, inputs, *, profile=True, **options):
        return await self.http(
            "POST",
            self.config["consumer_url"] + ("/h9b/responses" if profile else "/responses"),
            headers=await self.headers(who, both=profile),
            value={"capability_id": capability, "arguments": inputs, "approved": True, **options},
            label=("profile-consumer:" if profile else "reference-consumer:") + capability,
        )

    async def smoke(self):
        issued = await self.issue("viewer", "mock.create_ticket", {"title": "H9b smoke fixture"})
        response = await self.invoke(
            "viewer", "mock.create_ticket", {"title": "H9b smoke fixture"}, issued
        )
        print(response.text[:3000])
        self.spike.state.setdefault("smoke", []).append(
            {
                "at": now(),
                "status": response.status_code,
                "success": self.succeeded(response),
            }
        )
        self.spike.save()
        for service in ("gateway", "consumer", "mock"):
            events = await self.events(service)
            print(service, json.dumps(events[-3:])[:6500])

    async def suite(self):
        if self.spike.state.get("stop"):
            raise RuntimeError("Safety stop already recorded")
        rows = self.spike.state.setdefault("conformance", [])
        if rows:
            raise RuntimeError("Do not overwrite conformance evidence")
        for who, capability, inputs, changed in (
            (
                "viewer",
                "mock.create_ticket",
                {"title": "H9b exact conformance"},
                {"title": "H9b mutated conformance"},
            ),
            ("release", "mock.deploy_release", {"release": "v0.9.2"}, {"release": "v9.9.9"}),
        ):
            row = {"caller": who, "capability": capability, "tests": {}}
            rows.append(row)
            offset = await self.count()
            response = await self.consumer(who, capability, inputs)
            after = await self.events("mock")
            if response.status_code != 200 or len(after) != offset + 1 or not after[-1]["accepted"]:
                raise RuntimeError("Exact positive control failed")
            row["tests"]["1_exact"] = {
                "status": response.status_code,
                "mock_delta": len(after) - offset,
                "result": response.json(),
            }
            offset = await self.count()
            response = await self.consumer(who, capability, inputs, mutate_after_middleware=True)
            if await self.count() != offset:
                self.stop("Mutation between middleware and MCP client reached mock")
            if response.status_code != 403:
                raise RuntimeError("Middleware mutation did not fail closed")
            row["tests"]["2_middleware_mutation"] = {
                "status": response.status_code,
                "mock_delta": 0,
            }
            issued = await self.issue(who, capability, inputs)
            response = await self.invoke(who, capability, changed, issued)
            if await self.count() != offset:
                self.stop("Mutated gateway-body call reached mock")
            if self.succeeded(response):
                raise RuntimeError("Gateway mutation unexpectedly succeeded")
            row["tests"]["2_gateway_mutation"] = {"status": response.status_code, "mock_delta": 0}
            valid = await self.invoke(who, capability, inputs, issued)
            if not self.succeeded(valid) or await self.count() != offset + 1:
                raise RuntimeError("Replay setup exact call failed")
            offset = await self.count()
            replay = await self.invoke(who, capability, inputs, issued)
            if await self.count() != offset:
                self.stop("Replayed call reached mock")
            if self.succeeded(replay):
                raise RuntimeError("Replay unexpectedly succeeded")
            row["tests"]["3_replay"] = {"status": replay.status_code, "mock_delta": 0}
            expired = await self.issue(who, capability, inputs, expired_fixture=True)
            response = await self.invoke(who, capability, inputs, expired)
            if self.succeeded(response) or await self.count() != offset:
                raise RuntimeError("Expired lease did not stop before mock")
            row["tests"]["4_expiry"] = {"status": response.status_code, "mock_delta": 0}
            response = await self.invoke(who, capability, inputs)
            if self.succeeded(response) or await self.count() != offset:
                raise RuntimeError("Missing lease did not stop before mock")
            row["tests"]["5_no_lease"] = {"status": response.status_code, "mock_delta": 0}
            response = await self.consumer(who, capability, inputs, approved=False)
            if response.status_code != 403 or await self.count() != offset:
                raise RuntimeError("Incomplete approval did not stop before mock")
            row["tests"]["6_incomplete_approval"] = {
                "status": response.status_code,
                "mock_delta": 0,
            }
            self.spike.save()
        before = await self.count("gateway")
        offset = await self.count()
        response = await self.invoke("viewer", "mock.deploy_release", {"release": "v0.9.2"})
        if response.status_code != 403:
            self.stop("Viewer privileged ToolServer request did not return 403")
        if await self.count("gateway") != before or await self.count() != offset:
            self.stop("Viewer outside-view attempt reached verifier or mock")
        self.spike.state["viewer_deploy_denial"] = {
            "status": response.status_code,
            "verifier_delta": 0,
            "mock_delta": 0,
        }
        self.spike.state["conformance_passed_at"] = now()
        self.spike.save()

    async def carriage(self):
        offset = await self.count()
        issued = await self.issue("viewer", "mock.create_ticket", {"title": "H9b header isolation"})
        response = await self.invoke(
            "viewer", "mock.create_ticket", {"title": "H9b header isolation"}, issued, header=True
        )
        delta = await self.count() - offset
        self.spike.state["header_probe"] = {
            "at": now(),
            "status": response.status_code,
            "success": self.succeeded(response),
            "mock_delta": delta,
        }
        if not self.succeeded(response) and delta:
            self.stop("Failed header-carriage request reached mock")
        self.spike.save()

    async def metrics(self, *, normalized=False):
        if not self.spike.state.get("conformance_passed_at") or self.spike.state.get("stop"):
            raise RuntimeError("Passing conformance required")
        key = "normalized_measurements" if normalized else "measurements"
        rows = self.spike.state.setdefault(key, [])
        if rows:
            raise RuntimeError("Do not overwrite metrics")
        for capability, inputs in (
            ("mock.create_ticket", {"title": "H9b paired ACA measurement"}),
            ("mock.deploy_release", {"release": "v0.9.8"}),
        ):
            for index in range(22):
                pair = {"capability": capability, "index": index, "warmup": index < 2}
                modes = [True, False] if index % 2 else [False, True]
                for profile in modes:
                    started = time.perf_counter()
                    response = await self.consumer(
                        "release",
                        capability,
                        inputs,
                        profile=profile,
                        **({"carriage": "reserved"} if normalized else {}),
                    )
                    response.raise_for_status()
                    pair["ai_gateway" if profile else "reference"] = {
                        "external_total_ms": (time.perf_counter() - started) * 1000,
                        **response.json()["governance"],
                    }
                rows.append(pair)
                self.spike.save()
        self.spike.state["measurements_completed_at"] = now()
        self.spike.save()

    async def normalized_metrics(self):
        await self.metrics(normalized=True)

    async def boundaries(self):
        before = await self.count("gateway")
        offset = await self.count()
        hidden = self.bindings["mock.create_ticket"]["tool_name"].replace(
            "create_ticket", "order_hardware"
        )
        response = await self.http(
            "POST",
            self.servers["general"],
            headers=await self.headers("viewer", gateway=True),
            value={
                "jsonrpc": "2.0",
                "id": 93,
                "method": "tools/call",
                "params": {
                    "name": "general_" + hidden,
                    "arguments": {
                        "requester_id": "synthetic",
                        "item": "synthetic",
                        "quantity": 1,
                    },
                },
            },
            label="viewer:hidden-order-hardware",
        )
        if (
            self.succeeded(response)
            or await self.count("gateway") != before
            or await self.count() != offset
        ):
            self.stop("Viewer hidden-tool request escaped its gateway view")
        self.spike.state["hidden_tool_denial"] = {
            "status": response.status_code,
            "verifier_delta": 0,
            "mock_delta": 0,
        }
        inputs = {"title": "H9b assertion mandatory fixture"}
        issued = await self.issue("viewer", "mock.create_ticket", inputs)
        response = await self.http(
            "POST",
            self.config["mock_url"] + "/mcp",
            headers=await self.headers("viewer"),
            value={
                "jsonrpc": "2.0",
                "id": 94,
                "method": "tools/call",
                "params": {
                    "name": "create_ticket",
                    "arguments": inputs,
                    "_meta": {
                        "capability-registry/context": {
                            "provider_profile": "h9b",
                            "lease": issued["lease"],
                            "identity": self.identities["viewer"]["principal_id"],
                            "task_id": issued["task_id"],
                            "approver": issued["approver"],
                        }
                    },
                },
            },
            label="mock:valid-lease-without-execution-assertion",
        )
        events = await self.events("mock")
        if response.status_code != 403 or len(events) != offset + 1 or events[-1]["accepted"]:
            self.stop("Mock accepted a lease without verifier execution assertion")
        self.spike.state["assertion_required"] = {
            "status": response.status_code,
            "accepted": False,
        }
        self.spike.save()

    async def sessions(self):
        import httpx2
        from agent_framework import MCPStreamableHTTPTool

        probes = self.spike.state.setdefault("session_probes", [])
        if probes:
            raise RuntimeError("Do not overwrite session evidence")
        for rotating in (False, True):
            row = {"rotating_lease_header": rotating, "wire": [], "calls": []}
            probes.append(row)
            tokens = [
                await self.issue(
                    "viewer",
                    "mock.create_ticket",
                    {
                        "title": f"H9b same-session fixture {rotating} {index}",
                    },
                )
                for index in range(2)
            ]
            holder = {"lease": tokens[0]["lease"]}
            auth = await self.headers("viewer", gateway=True)

            def headers(_):
                return {
                    **auth,
                    **({"X-Capability-Lease": holder["lease"]} if rotating else {}),
                }

            async def on_request(request):
                content = request.content
                try:
                    value = json.loads(content) if content else None
                except ValueError:
                    value = None
                row["wire"].append(
                    sanitized(
                        {
                            "method": request.method,
                            "url": str(request.url),
                            "headers": dict(request.headers),
                            "request": value,
                        }
                    )
                )

            async def on_response(response):
                row.setdefault("response_headers", []).append(dict(response.headers))

            offset = await self.count()
            async with httpx2.AsyncClient(
                timeout=90, event_hooks={"request": [on_request], "response": [on_response]}
            ) as transport:
                async with MCPStreamableHTTPTool(
                    name="h9b-session",
                    url=self.servers["general"],
                    http_client=transport,
                    header_provider=headers,
                    load_prompts=False,
                    additional_tool_argument_names=["_capability_lease"],
                    parse_tool_results=lambda result: json.dumps(result.model_dump(by_alias=True)),
                ) as tool:
                    name = next(f.name for f in tool.functions if f.name.endswith("create_ticket"))
                    for index, token in enumerate(tokens):
                        holder["lease"] = token["lease"]
                        start = time.perf_counter()
                        result = await tool.call_tool(
                            name,
                            title=f"H9b same-session fixture {rotating} {index}",
                            _capability_lease=token["lease"],
                        )
                        if json.loads(result).get("isError"):
                            raise RuntimeError("Session fixture call failed")
                        row["calls"].append({"elapsed_ms": (time.perf_counter() - start) * 1000})
            events = await self.events("mock")
            if len(events) != offset + 2 or not all(e["accepted"] for e in events[offset:]):
                raise RuntimeError("Session probe lacks two matching mock acceptances")
            row["initialize_count"] = sum(
                w.get("request", {}).get("method") == "initialize"
                for w in row["wire"]
                if w.get("request")
            )
            row["session_ids"] = sorted(
                {
                    value
                    for h in row.get("response_headers", [])
                    for key, value in h.items()
                    if key.lower() == "mcp-session-id"
                }
            )
            self.spike.save()
            print("session", rotating, row["initialize_count"], row["session_ids"])

    async def evidence(self):
        self.spike.state["final_events"] = {
            service: await self.events(service) for service in ("gateway", "consumer", "mock")
        }
        expected = {
            mode["audit"][0]["lease_id"]
            for key in ("measurements", "normalized_measurements")
            for row in self.spike.state.get(key, [])
            for name in ("reference", "ai_gateway")
            for mode in [row[name]]
        }
        mock = self.spike.state["final_events"]["mock"]
        counts = {
            lease_id: sum(e.get("accepted") and e.get("lease_id") == lease_id for e in mock)
            for lease_id in expected
        }
        if any(value != 1 for value in counts.values()):
            raise RuntimeError("Some measured calls lack exactly one matching accepted mock event")
        self.spike.state["measurement_mock_checks"] = {
            "leases": len(expected),
            "each_accepted_exactly_once": True,
        }
        self.spike.save()

    async def close(self):
        self.spike.save()
        await self.client.aclose()
        for credential in self.credentials.values():
            await credential.close()


async def run(phase):
    live = Live()
    try:
        await getattr(live, phase)()
    finally:
        await live.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "smoke",
            "suite",
            "carriage",
            "metrics",
            "normalized_metrics",
            "boundaries",
            "sessions",
            "evidence",
        ],
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    H9b().check_time()
    asyncio.run(run(args.phase))


if __name__ == "__main__":
    main()
