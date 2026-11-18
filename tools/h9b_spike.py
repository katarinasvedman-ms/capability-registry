from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from azure.identity.aio import CertificateCredential
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from governance.runtime import sanitized
from tools.capture_sources import ROOT, sha256, write_json
from tools.governance_live import Experiment
from tools.h9_access import TOOLS_USER
from tools.h9_spike import API_VERSION, RESOURCE, Spike, now
from tools.h9_spike import DIRECTORY as H9_DIRECTORY
from tools.provision_governance import PREFIX, SHORT, Deployment

DIRECTORY = ROOT / ".artifacts" / "h9b"
STATE = DIRECTORY / "state.json"
LABELS = {
    "purpose": "capability-registry-testbed",
    "owner": "katarina.svedman",
    "run": "f3e5ad02",
    "phase": "h9b",
}


class H9b(Spike):
    def __init__(self):
        super().__init__()
        self.state = (
            json.loads(STATE.read_text())
            if STATE.exists()
            else {
                "run": "h9b-f3e5ad02",
                "started_at": "2026-11-18T09:46:37.635000+00:00",
                "deadline": "2026-11-18T10:46:37.635000+00:00",
                "created": [],
                "intents": [],
                "commands": [],
                "http": [],
                "versions": self.state["versions"],
            }
        )
        self.environment["AZURE_EXTENSION_DIR"] = str(H9_DIRECTORY / "cli-extensions")

    def save(self):
        write_json(STATE, sanitized(self.state))

    def arm(self, method, identifier, payload=None):
        args = [
            "rest",
            "--method",
            method,
            "--url",
            "https://management.azure.com" + identifier + "?api-version=" + API_VERSION,
        ]
        if payload is not None:
            path = DIRECTORY / "arm-input.json"
            write_json(path, payload)
            args += ["--body", "@" + str(path)]
        return self.cli(*args)

    async def http(self, client, method, url, *, headers=None, value=None, label=None):
        self.check_time()
        started = time.perf_counter()
        response = await client.request(method, url, headers=headers, json=value)
        try:
            content = response.json()
        except ValueError:
            content = response.text
        self.state["http"].append(
            {
                "at": now(),
                "label": label,
                "method": method,
                "url": url,
                "request_headers": headers,
                "request": value,
                "status": response.status_code,
                "response_headers": dict(response.headers),
                "response": content,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        )
        self.save()
        print(label or method, response.status_code, flush=True)
        return response

    async def baseline(self, label="baseline"):
        if label == "baseline" and label in self.state:
            raise RuntimeError("Refusing to overwrite the frozen H9b baseline")
        experiment = Experiment()
        experiment.state = {"http": []}
        experiment.save = lambda: None
        try:
            snapshot = await experiment.snapshot()
            await experiment.privileged_probe()
        finally:
            await experiment.client.aclose()
            for credential in experiment.credentials.values():
                await credential.close()
        result = {
            "at": now(),
            "toolbox": snapshot,
            "bindings_hash": sha256((ROOT / "catalog" / "bindings.json").read_bytes()),
            "main_report_hash": sha256((ROOT / "docs" / "hypothesis-report.md").read_bytes()),
            "gateway": self.arm("GET", RESOURCE),
            "servers": self.arm("GET", RESOURCE + "/workspaces/default/toolServers")["value"],
            "apps": {
                service: self.cli(
                    "containerapp", "show", "-g", "factory-skills", "-n", PREFIX + "-" + service
                )
                for service in ("gateway", "consumer", "mock")
            },
            "assignments": self.cli("role", "assignment", "list", "--all"),
        }
        self.state[label] = result
        self.save()
        if label != "baseline":
            before = self.state["baseline"]
            for key in ("toolbox", "bindings_hash", "main_report_hash"):
                if before[key] != result[key]:
                    raise RuntimeError("STOP: immutable baseline changed: " + key)
            old = {r["id"]: r for r in before["assignments"]}
            current = {r["id"]: r for r in result["assignments"]}
            if any(current.get(key) != row for key, row in old.items()):
                raise RuntimeError("STOP: an existing assignment changed")
        print(label, "recorded", flush=True)

    def viewer(self):
        if "viewer" in self.state:
            return
        if self.cli("ad", "app", "list", "--display-name", "viewer-test"):
            raise RuntimeError("Refusing adoption of an existing viewer-test")
        tags = [key + "=" + value for key, value in LABELS.items()]
        self.state["intents"].append({"kind": "application", "name": "viewer-test"})
        self.save()
        app = self.cli(
            "rest",
            "--method",
            "POST",
            "--url",
            "https://graph.microsoft.com/v1.0/applications",
            "--body",
            json.dumps(
                {
                    "displayName": "viewer-test",
                    "signInAudience": "AzureADMyOrg",
                    "tags": tags,
                    "description": ";".join(tags),
                }
            ),
        )
        self.state["created"].append(
            {"kind": "application", "id": app["id"], "appId": app["appId"]}
        )
        self.save()
        sp = self.cli("ad", "sp", "create", "--id", app["appId"])
        self.state["created"].append(
            {"kind": "servicePrincipal", "id": sp["id"], "appId": app["appId"]}
        )
        self.save()
        self.cli(
            "rest",
            "--method",
            "PATCH",
            "--url",
            "https://graph.microsoft.com/v1.0/servicePrincipals/" + sp["id"],
            "--body",
            json.dumps({"tags": tags, "description": ";".join(tags)}),
        )
        folder = DIRECTORY / "credentials"
        folder.mkdir(parents=True, exist_ok=True)
        account = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
        subprocess.run(
            [
                "icacls",
                str(folder),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(OI)(CI)(F)",
                "*S-1-5-18:(OI)(CI)(F)",
            ],
            capture_output=True,
            check=True,
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        start = datetime.now(UTC) - timedelta(minutes=5)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "viewer-test")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(start + timedelta(days=1, minutes=5))
            .sign(key, hashes.SHA256())
        )
        public = folder / "viewer-test.cer"
        public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pem = folder / "viewer-test.pem"
        pem.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            + cert.public_bytes(serialization.Encoding.PEM)
        )
        self.cli(
            "ad",
            "app",
            "credential",
            "reset",
            "--id",
            app["appId"],
            "--append",
            "--cert",
            "@" + str(public),
            "--display-name",
            "capability-registry-testbed-h9b",
            "--end-date",
            cert.not_valid_after_utc.isoformat(),
        )
        self.state["viewer"] = {
            "tenant_id": "199ab2fd-dc94-4a42-8438-1144eab8c1b4",
            "client_id": app["appId"],
            "principal_id": sp["id"],
            "application_id": app["id"],
            "certificate": str(pem),
            "expires": cert.not_valid_after_utc.isoformat(),
        }
        self.save()
        scope = next(s["id"] for s in self.state["baseline"]["servers"] if s["name"] == "general")
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "h9b:" + sp["id"] + scope))
        assignment = self.cli(
            "role",
            "assignment",
            "create",
            "--name",
            identifier,
            "--assignee-object-id",
            sp["id"],
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            TOOLS_USER,
            "--scope",
            scope,
            "--description",
            ";".join(tags),
        )
        self.state["created"].append(
            {"kind": "roleAssignment", "id": assignment["id"], "principalId": sp["id"]}
        )
        self.save()

    async def viewer_probe(self):
        identity = self.state["viewer"]
        gateway = self.state["baseline"]["gateway"]
        self.state["viewer_assignments"] = self.cli(
            "role", "assignment", "list", "--all", "--assignee-object-id", identity["principal_id"]
        )
        async with (
            CertificateCredential(
                identity["tenant_id"],
                identity["client_id"],
                certificate_path=identity["certificate"],
            ) as credential,
            httpx.AsyncClient(timeout=45) as client,
        ):
            for name in ("general", "privileged"):
                server = next(s for s in self.state["baseline"]["servers"] if s["name"] == name)
                access = await credential.get_token("https://ai.gateway.azure.com/.default")
                response = await self.http(
                    client,
                    "POST",
                    server["properties"]["gatewayEndpoints"][0]["url"],
                    headers={
                        "Authorization": "Bearer " + access.token,
                        "Accept": "application/json, text/event-stream",
                    },
                    value={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    label="viewer:" + name,
                )
                if name == "privileged" and response.status_code != 403:
                    raise RuntimeError("STOP: viewer privileged access not 403")
                if name == "general" and response.status_code == 200:
                    names = [t["name"] for t in response.json()["result"]["tools"]]
                    expected = server["properties"]["allowList"]
                    if sorted(names) != sorted(expected):
                        raise RuntimeError("STOP: viewer tools outside allowed view")
            access = await credential.get_token("https://ai.azure.com/.default")
            endpoint = (
                "https://factory-skills-resource.services.ai.azure.com/api/projects/"
                "factory-skills/toolboxes/factory-skills/versions/3/mcp?api-version=v1"
            )
            response = await self.http(
                client,
                "POST",
                endpoint,
                headers={
                    "Authorization": "Bearer " + access.token,
                    "Accept": "application/json, text/event-stream",
                },
                value={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                label="viewer:direct-toolbox",
            )
            if response.status_code not in (401, 403):
                raise RuntimeError("STOP: viewer can reach Toolbox")
        print("Gateway identity", gateway["identity"]["principalId"], flush=True)

    def deploy(self, services, tag):
        deployment = Deployment()
        image = SHORT + ".azurecr.io/governance:" + tag
        for service in services:
            original = self.state["baseline"]["apps"][service]
            config = json.loads(
                next(
                    e["value"]
                    for e in original["properties"]["template"]["containers"][0]["env"]
                    if e["name"] == "GOV_CONFIG"
                )
            )
            config["trusted_upstreams"] = [
                self.state["baseline"]["gateway"]["identity"]["principalId"]
            ]
            if tag != "20261118-h9b-capture":
                viewer = self.state["viewer"]["principal_id"]
                h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
                config["callers"][viewer] = copy.deepcopy(
                    config["callers"][h4["coder"]["principal_id"]]
                )
                config["h9b_gateway_urls"] = {
                    s["name"]: s["properties"]["gatewayEndpoints"][0]["url"]
                    for s in self.state["baseline"]["servers"]
                    if s["name"] in {"general", "privileged"}
                }
            identity = deployment.state["resources"][service + "_identity"]
            app = self.cli(
                "containerapp",
                "update",
                "-g",
                "factory-skills",
                "-n",
                PREFIX + "-" + service,
                "--image",
                image,
                "--set-env-vars",
                "GOV_CONFIG=" + json.dumps(config, separators=(",", ":")),
                "AZURE_CLIENT_ID=" + identity["clientId"],
                "SERVICE=" + service,
                timeout=240,
            )
            self.state.setdefault("deployments", []).append(
                {
                    "at": now(),
                    "service": service,
                    "image": image,
                    "revision": app["properties"]["latestRevisionName"],
                }
            )
            self.save()

    def configure_profile(self):
        config = Deployment().config()
        for view in ("general", "privileged"):
            server = next(s for s in self.state["baseline"]["servers"] if s["name"] == view)
            endpoints = [
                {
                    "id": server["properties"]["endpoints"][0].get("id", "h9b-verifier"),
                    "kind": "mcp",
                    "namespace": view,
                    "mode": "failClosed",
                    "mcp": {
                        "endpoint": {"url": config["gateway_url"] + "/h9b/mcp/" + view},
                        "transport": "streamableHttp",
                    },
                    "authentication": {"managedIdentity": {"resource": "https://ai.azure.com"}},
                }
            ]
            result = self.arm(
                "PATCH",
                server["id"],
                {
                    "properties": {
                        "endpoints": endpoints,
                        "policies": server["properties"].get("policies", []),
                    }
                },
            )
            self.state.setdefault("profile_servers", {})[view] = result
            self.save()

    def set_hook(self, enabled):
        server = next(s for s in self.state["baseline"]["servers"] if s["name"] == "general")
        policies = copy.deepcopy(server["properties"].get("policies", []))
        if enabled:
            policies.append(
                {
                    "type": "agentHooks",
                    "id": "h9b-real-capture",
                    "scope": "resource",
                    "endpoint": {"url": Deployment().config()["gateway_url"] + "/h9b/hook-capture"},
                    "mode": "failClosed",
                    "authentication": {"managedIdentity": {"resource": "https://ai.azure.com"}},
                    "hooks": {"preToolCall": {}},
                }
            )
        self.arm("PATCH", server["id"], {"properties": {"policies": policies}})
        self.state["hook_enabled"] = enabled
        self.save()

    async def capture(self):
        config = Deployment().config()
        identity = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())["release"]
        async with (
            CertificateCredential(
                identity["tenant_id"],
                identity["client_id"],
                certificate_path=identity["certificate"],
            ) as credential,
            httpx.AsyncClient(timeout=90) as client,
        ):
            access = await credential.get_token("https://ai.azure.com/.default")
            headers = {"Authorization": "Bearer " + access.token}
            response = await self.http(
                client,
                "POST",
                config["consumer_url"] + "/leases",
                headers=headers,
                value={
                    "capability_id": "mock.create_ticket",
                    "arguments": {
                        "title": "H9b real hook payload fixture",
                    },
                    "approved": True,
                },
                label="capture:lease",
            )
            response.raise_for_status()
            lease = response.json()
            gateway_token = await credential.get_token("https://ai.gateway.azure.com/.default")
            server = next(s for s in self.state["baseline"]["servers"] if s["name"] == "general")
            await self.http(
                client,
                "POST",
                server["properties"]["gatewayEndpoints"][0]["url"],
                headers={
                    "Authorization": "Bearer " + gateway_token.token,
                    "Accept": "application/json, text/event-stream",
                    "X-Capability-Lease": lease["lease"],
                    "X-Task-Id": lease["task_id"],
                    "X-Approver": lease["approver"],
                    "X-H9b-Marker": "real-hook-header-probe",
                },
                value={
                    "jsonrpc": "2.0",
                    "id": 91,
                    "method": "tools/call",
                    "params": {
                        "name": next(
                            n
                            for n in server["properties"]["allowList"]
                            if n.endswith("create_ticket")
                        ),
                        "arguments": {"title": "H9b real hook payload fixture"},
                        "_meta": {"h9b-correlation": "capture-91"},
                    },
                },
                label="capture:gateway-call",
            )
            response = await self.http(
                client,
                "GET",
                config["gateway_url"] + "/evidence",
                headers=headers,
                label="capture:evidence",
            )
            response.raise_for_status()
            captures = [e for e in response.json()["events"] if e.get("service") == "hook-capture"]
            self.state["hook_captures"] = captures
            self.save()
            print(json.dumps(captures, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "baseline",
            "viewer",
            "viewer-probe",
            "deploy-capture",
            "deploy-profile",
            "configure-profile",
            "hook-on",
            "capture",
            "hook-off",
            "final",
        ],
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    spike = H9b()
    spike.check_time()
    if args.phase in {"baseline", "final"}:
        asyncio.run(spike.baseline(args.phase))
    elif args.phase == "viewer":
        spike.viewer()
    elif args.phase == "viewer-probe":
        asyncio.run(spike.viewer_probe())
    elif args.phase == "deploy-capture":
        spike.deploy(["gateway"], "20261118-h9b-capture")
    elif args.phase == "deploy-profile":
        spike.deploy(["consumer", "mock", "gateway"], "20261118-h9b-profile")
    elif args.phase == "configure-profile":
        spike.configure_profile()
    elif args.phase == "capture":
        asyncio.run(spike.capture())
    elif args.phase.startswith("hook-"):
        spike.set_hook(args.phase == "hook-on")


if __name__ == "__main__":
    main()
