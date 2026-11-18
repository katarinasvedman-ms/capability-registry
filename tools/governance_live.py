from __future__ import annotations

import argparse
import asyncio
import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime

import httpx
from azure.identity.aio import CertificateCredential

from adapters.core.foundry import FoundryTransport
from governance.runtime import sanitized
from tools.capture_sources import ROOT, sha256, write_json
from tools.live_support import AsyncCliCredential
from tools.provision_governance import Deployment
from tools.publish_mock import CONNECTION, PROJECT_ARM

DIRECTORY = ROOT / ".artifacts" / "governance"
PATH = DIRECTORY / "live.json"


def now():
    return datetime.now(UTC).isoformat()


class Experiment:
    def __init__(self):
        self.state = (
            json.loads(PATH.read_text()) if PATH.exists() else {"http": [], "started_at": now()}
        )
        self.deployment = Deployment()
        self.config = self.deployment.config()
        self.h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
        self.credentials = {}
        self.client = httpx.AsyncClient(timeout=600, follow_redirects=False)
        self.bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())

    def save(self):
        write_json(PATH, sanitized(self.state))

    def credential(self, who):
        if who not in self.credentials:
            row = self.h4[who]
            self.credentials[who] = CertificateCredential(
                row["tenant_id"], row["client_id"], certificate_path=row["certificate"]
            )
        return self.credentials[who]

    async def headers(self, who="release"):
        token = await self.credential(who).get_token("https://ai.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}"}

    async def http(self, method, url, *, value=None, headers=None):
        response = await self.client.request(method, url, json=value, headers=headers)
        try:
            content = response.json()
        except ValueError:
            content = response.text
        self.state["http"].append(
            {
                "at": now(),
                "method": method,
                "url": url,
                "request": value,
                "request_headers": headers,
                "status": response.status_code,
                "response_headers": dict(response.headers),
                "response": content,
            }
        )
        self.save()
        return response

    async def snapshot(self):
        transport = FoundryTransport(self.credential("release"))
        result = {}
        for version in ("1", "2", "3"):
            binding = {**self.bindings["mock.get_build_status"], "version": version}
            raw, _ = await transport.request(
                "GET", transport.base(binding) + "?api-version=v1", binding=binding
            )
            result["general:" + version] = {"sha256": sha256(raw), "raw": raw.decode()}
        binding = self.bindings["mock.deploy_release"]
        raw, _ = await transport.request(
            "GET", transport.base(binding) + "?api-version=v1", binding=binding
        )
        result["privileged:1"] = {"sha256": sha256(raw), "raw": raw.decode()}
        base = transport.base(self.bindings["mock.get_build_status"]).split("/versions/")[0]
        raw, _ = await transport.request("GET", base + "?api-version=v1")
        result["default"] = json.loads(raw)["default_version"]
        self.state["http"].extend(transport.evidence)
        # Credentials are shared by this harness, so close the HTTP client only.
        await transport.client.aclose()
        return result

    async def privileged_probe(self):
        binding = self.bindings["mock.deploy_release"]
        response = await self.http(
            "POST",
            FoundryTransport.base(binding) + "/mcp?api-version=v1",
            headers={
                **await self.headers("coder"),
                "Accept": "application/json, text/event-stream",
            },
            value={
                "jsonrpc": "2.0",
                "id": 67,
                "method": "tools/call",
                "params": {"name": binding["tool_name"], "arguments": {"release": "v0.0.6"}},
            },
        )
        if response.status_code != 403:
            self.state["stop"] = "Privileged coder path returned other than 403"
            self.save()
            raise RuntimeError(self.state["stop"])

    async def general_probe(self, label):
        transport = FoundryTransport(self.credential("coder"))
        binding = {**self.bindings["mock.get_build_status"], "version": "2"}
        tools = await transport.list_tools(binding)
        name = self.bindings["mock.deploy_release"]["tool_name"]
        self.state[label] = {"exposure_expected": name in tools}
        try:
            result = await transport.call_tool(
                binding,
                name,
                {"release": "v0.0.6"},
                context={"approval_id": "synthetic-h6-route-probe"},
            )
            self.state[label]["result"] = result
            self.state[label]["accepted"] = True
        except Exception as error:
            self.state[label].update(accepted=False, error=str(error))
        finally:
            self.state[label]["http"] = transport.evidence
            await transport.client.aclose()
            self.save()
        if label == "general_after" and self.state[label]["accepted"]:
            self.state["stop"] = "Secured mock accepted the unleased general-v2 bypass"
            self.save()
            raise RuntimeError(self.state["stop"])

    async def before(self):
        if "before" in self.state:
            raise ValueError("Before evidence already recorded")
        self.state["before"] = await self.snapshot()
        self.save()
        await self.privileged_probe()
        await self.general_probe("general_before")
        self.save()
        print("Before split controls and direct coder routes recorded")

    async def switch(self):
        if self.state.get("stop") or "before" not in self.state:
            raise ValueError("Passing before controls required")
        if self.state.get("switched"):
            raise ValueError("Already switched")
        for service in ("mock", "gateway", "consumer"):
            response = await self.http("GET", self.config[service + "_url"] + "/readiness")
            response.raise_for_status()
        # Establish the ACA mock's fail-closed identity check before changing any route.
        response = await self.http(
            "POST",
            self.config["mock_url"] + "/mcp",
            value={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "deploy_release", "arguments": {"release": "v0.0.6"}},
            },
        )
        if response.status_code != 403:
            self.state["stop"] = "New mock does not reject unauthenticated/unleased execution"
            self.save()
            raise RuntimeError(self.state["stop"])
        self.state["identity_check_live_at"] = now()
        self.state["connections"] = []
        self.save()
        credential = AsyncCliCredential()
        try:
            token = await credential.get_token("https://management.azure.com/.default")
            headers = {"Authorization": f"Bearer {token.token}"}
            for scope, name in (
                (PROJECT_ARM, CONNECTION),
                (PROJECT_ARM + "-privileged", CONNECTION + "-privileged"),
            ):
                url = (
                    f"https://management.azure.com{scope}/connections/{name}?api-version=2025-06-01"
                )
                old = await self.http("GET", url, headers=headers)
                old.raise_for_status()
                properties = old.json()["properties"]
                if properties["authType"] != "None":
                    raise ValueError("Unexpected owned connection authentication")
                body = {
                    "properties": {
                        key: properties[key]
                        for key in ("category", "authType", "target", "isSharedToAll", "metadata")
                        if key in properties
                    }
                }
                self.state["connections"].append({"url": url, "before": copy.deepcopy(body)})
                self.save()
                body["properties"]["target"] = self.config["mock_url"] + "/mcp"
                updated = await self.http("PUT", url, headers=headers, value=body)
                updated.raise_for_status()
        finally:
            await credential.close()
        self.state["switched"] = now()
        self.state["after"] = await self.snapshot()
        self.save()
        if self.state["before"] != self.state["after"]:
            self.state["stop"] = "An existing immutable version/default changed"
            self.save()
            raise RuntimeError(self.state["stop"])
        await self.privileged_probe()
        await self.general_probe("general_after")
        print("Owned connection targets moved; immutable controls and rejected v2 bypass recorded")

    async def events(self, service):
        response = await self.http(
            "GET", self.config[service + "_url"] + "/evidence", headers=await self.headers()
        )
        response.raise_for_status()
        return response.json()["events"]

    async def retire(self):
        if not self.state.get("switched") or self.state.get("stop"):
            raise ValueError("Verified secure route switch required")
        command = [
            shutil.which("devtunnel"),
            "delete",
            "capability-registry-f3e5ad02.eun1",
            "--force",
            "--json",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.state["tunnel_retirement"] = {
            "at": now(),
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        self.save()
        if result.returncode:
            raise RuntimeError("Owned development tunnel deletion failed")
        self.state["tunnel_retired_at"] = now()
        self.save()
        url = "https://capability-registry-f3e5ad02-18765.eun1.devtunnels.ms/mcp"
        response = await self.http(
            "POST",
            url,
            headers=await self.headers("coder"),
            value={
                "jsonrpc": "2.0",
                "id": 67,
                "method": "tools/call",
                "params": {
                    "name": "deploy_release",
                    "arguments": {"release": "v0.0.6"},
                },
            },
        )
        self.state["retired_tunnel_status"] = response.status_code
        self.save()
        if response.status_code < 400:
            raise RuntimeError("Retired tunnel still responds successfully")
        print("Owned tunnel deleted; raw coder request to retired route recorded")

    async def h6(self):
        if self.state.get("stop") or not self.state.get("tunnel_retired_at"):
            raise ValueError("Route switch, retired tunnel and no safety stop required")
        results = []
        self.state["h6"] = results
        for capability, inputs, mutated in (
            ("mock.deploy_release", {"release": "v0.0.6"}, {"release": "v9.9.9"}),
            (
                "mock.create_ticket",
                {"title": "H6 original fixture"},
                {"title": "H6 mutated fixture"},
            ),
        ):
            payload = {"capability_id": capability, "arguments": inputs, "approved": True}
            headers = await self.headers()
            baseline = len(await self.events("mock"))
            pending = await self.http(
                "POST",
                self.config["consumer_url"] + "/responses",
                headers=headers,
                value={**payload, "approved": False},
            )
            if pending.status_code != 403 or len(await self.events("mock")) != baseline:
                raise RuntimeError("H6 pending approval failed to stop before the mock")
            response = await self.http(
                "POST", self.config["consumer_url"] + "/responses", headers=headers, value=payload
            )
            response.raise_for_status()
            received = (await self.events("mock"))[baseline:]
            accepted = [e for e in received if e.get("accepted")]
            if len(accepted) != 1 or accepted[0]["request"]["params"]["arguments"] != inputs:
                raise RuntimeError("H6 exact positive call/mock evidence missing")
            row = {
                "capability_id": capability,
                "test1": response.json(),
                "test6_status": pending.status_code,
                "positive_mock": accepted,
            }
            results.append(row)
            self.save()
            offset = len(await self.events("mock"))
            changed = await self.http(
                "POST",
                self.config["consumer_url"] + "/responses",
                headers=headers,
                value={**payload, "mutate_after_middleware": True},
            )
            if len(await self.events("mock")) != offset:
                self.state["stop"] = "H6 test 2 middleware-to-client mutation reached mock"
                self.save()
                raise RuntimeError(self.state["stop"])
            row["test2_middleware_status"] = changed.status_code
            if changed.status_code != 403:
                raise RuntimeError("Mutation did not fail closed")
            lease_response = await self.http(
                "POST", self.config["consumer_url"] + "/leases", headers=headers, value=payload
            )
            lease_response.raise_for_status()
            issued = lease_response.json()
            carriage = {
                **headers,
                "X-Capability-Lease": issued["lease"],
                "X-Task-Id": issued["task_id"],
                "X-Approver": issued["approver"],
            }

            async def invoke(arguments, wire_headers):
                return await self.http(
                    "POST",
                    self.config["gateway_url"] + "/mcp",
                    headers=wire_headers,
                    value={
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": capability.removeprefix("mock."),
                            "arguments": arguments,
                        },
                    },
                )

            offset = len(await self.events("mock"))
            changed = await invoke(mutated, carriage)
            if len(await self.events("mock")) != offset:
                self.state["stop"] = "H6 test 2 gateway-body mutation reached mock"
                self.save()
                raise RuntimeError(self.state["stop"])
            row["test2_gateway_status"] = changed.status_code
            if changed.status_code != 403:
                raise RuntimeError("Gateway hash mismatch not rejected")
            valid = await invoke(inputs, carriage)
            valid.raise_for_status()
            replay = await invoke(inputs, carriage)
            missing = await invoke(
                inputs, {k: v for k, v in carriage.items() if k != "X-Capability-Lease"}
            )
            expired_response = await self.http(
                "POST",
                self.config["consumer_url"] + "/leases",
                headers=headers,
                value={**payload, "expired_fixture": True},
            )
            expired_response.raise_for_status()
            expired = expired_response.json()
            stale = await invoke(
                inputs,
                {
                    **headers,
                    "X-Capability-Lease": expired["lease"],
                    "X-Task-Id": expired["task_id"],
                    "X-Approver": expired["approver"],
                },
            )
            row.update(
                test3_replay=replay.status_code,
                test4_expired=stale.status_code,
                test5_missing=missing.status_code,
            )
            self.save()
            if any(r.status_code != 403 for r in (replay, missing, stale)):
                raise RuntimeError("H6 replay/expiry/missing lease rejection failed")
        await self.privileged_probe()
        await self.general_probe("general_after")
        self.state["h6_passed"] = now()
        self.save()
        print(
            "H6 exact-call, two mutation points, replay, expiry, missing and approval tests passed"
        )

    async def close(self):
        self.save()
        await self.client.aclose()
        for credential in self.credentials.values():
            await credential.close()

    async def h7(self):
        if not self.state.get("h6_passed") or self.state.get("stop"):
            raise ValueError("Passing H6 evidence required")
        rows = []
        self.state["h7"] = rows
        for carriage in ("header", "reserved"):
            offset = len(await self.events("mock"))
            response = await self.http(
                "POST",
                self.config["consumer_url"] + "/responses",
                headers=await self.headers(),
                value={
                    "capability_id": "mock.deploy_release",
                    "arguments": {"release": "v0.0.7"},
                    "approved": True,
                    "carriage": carriage,
                },
            )
            response.raise_for_status()
            lease_id = response.json()["governance"]["audit"][0]["lease_id"]
            received = [
                e
                for e in (await self.events("mock"))[offset:]
                if e.get("accepted") and e.get("lease_id") == lease_id
            ]
            gateway = [e for e in await self.events("gateway") if e.get("lease_id") == lease_id]
            if len(received) != 1 or not any(e.get("carriage") == carriage for e in gateway):
                raise RuntimeError("H7 lacks matching actual client/gateway/mock carriage proof")
            if "_capability_lease" in received[0]["request"]["params"]["arguments"]:
                raise RuntimeError("Reserved argument was forwarded downstream")
            rows.append(
                {
                    "carriage": carriage,
                    "response": response.json(),
                    "gateway": gateway,
                    "mock": received,
                }
            )
            self.save()
        offset = len(await self.events("mock"))
        response = await self.http(
            "POST",
            self.config["consumer_url"] + "/handoff",
            headers=await self.headers(),
            value={
                "capability_id": "mock.action_agent",
                "arguments": {"title": "H7 synthetic handoff"},
                "approved": True,
            },
        )
        response.raise_for_status()
        received = [
            e
            for e in (await self.events("mock"))[offset:]
            if e.get("service") == "sub-agent" and e.get("accepted")
        ]
        if len(received) != 1:
            raise RuntimeError("A2A handoff lacks independent accepted mock evidence")
        self.state["h7_a2a"] = {"response": response.json(), "received": received}
        self.state["h7_passed"] = now()
        self.save()
        print("H7 actual pinned MAF header and reserved carriage, stripping and A2A passed")

    async def h8(self):
        if not self.state.get("h7_passed") or self.state.get("stop"):
            raise ValueError("Passing H6/H7 required")
        rows = []
        self.state["h8"] = rows
        for capability, inputs in (
            ("mock.deploy_release", {"release": "v0.0.8"}),
            ("mock.create_ticket", {"title": "H8 synthetic fixture"}),
        ):
            response = await self.http(
                "POST",
                self.config["consumer_url"] + "/benchmark",
                headers=await self.headers(),
                value={
                    "capability_id": capability,
                    "arguments": inputs,
                    "approved": True,
                    "samples": 20,
                },
            )
            response.raise_for_status()
            rows.append(response.json())
            self.save()
        self.state["h8_measured_at"] = now()
        self.save()
        print("H8 ACA paired samples collected with independent mock security floor retained")

    async def runtime(self):
        from openai.types.responses import Response

        response = await self.http(
            "POST",
            self.config["consumer_url"] + "/responses",
            headers=await self.headers(),
            value={
                "input": "Run the synthetic ticket fixture",
                "metadata": {
                    "capability_id": "mock.create_ticket",
                    "arguments": json.dumps({"title": "Responses protocol fixture"}),
                    "synthetic_approval": "completed",
                },
            },
        )
        response.raise_for_status()
        Response.model_validate(response.json())
        self.state["responses_protocol_validated"] = now()
        skills = []
        for name in ("architecture-analysis", "implementation-planning", "test-strategy"):
            response = await self.http(
                "GET",
                self.config["consumer_url"] + "/skills/" + name,
                headers=await self.headers(),
            )
            response.raise_for_status()
            result = response.json()
            if result["lease_required"] or not result["content"]:
                raise RuntimeError("Selected skill load contract failed")
            skills.append({"name": name, "content_sha256": sha256(result["content"].encode())})
        self.state["skills"] = skills
        self.state["final_controls"] = await self.snapshot()
        if self.state["final_controls"] != self.state["before"]:
            self.state["stop"] = "Existing immutable Toolbox/default controls changed"
            self.save()
            raise RuntimeError(self.state["stop"])
        self.save()
        print("Live Responses schema, three unleased skill loads and immutable controls passed")


async def main(phase):
    experiment = Experiment()
    try:
        await getattr(experiment, phase)()
    finally:
        await experiment.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["before", "switch", "retire", "h6", "h7", "h8", "runtime"]
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.phase))
