from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
from azure.identity.aio import CertificateCredential

from governance.runtime import sanitized
from tools.capture_sources import ROOT, write_json
from tools.provision_governance import AZ_COMMAND, SUB
from tools.publish_mock import PROJECT_ARM

DIRECTORY = ROOT / ".artifacts" / "h9"
STATE = DIRECTORY / "state.json"
CLI_VERSION = "1.0.0b34"
CLI_SHA256 = "f4f1c509ccf0e8c5f135604f318bc60077eb14b4cef9036456f266874b9c5c86"
BASE_CLI_VERSION = "2.93.0"
API_VERSION = "2026-11-01"
NAME = "cr-h9-f3e5ad02-aigw"
RESOURCE = (
    f"/subscriptions/{SUB}/resourceGroups/factory-skills"
    f"/providers/Microsoft.ApiManagement/aigateways/{NAME}"
)
TAGS = {
    "purpose": "capability-registry-testbed",
    "owner": "katarina.svedman",
    "run": "f3e5ad02",
    "phase": "h9",
}


def now():
    return datetime.now(UTC).isoformat()


class Spike:
    def __init__(self):
        if STATE.exists():
            self.state = json.loads(STATE.read_text())
        else:
            self.state = {
                "run": "h9-f3e5ad02",
                "started_at": "2026-11-18T08:26:02.808000+00:00",
                "deadline": "2026-11-18T09:11:02.808000+00:00",
                "created": [],
                "intents": [],
                "commands": [],
                "http": [],
                "versions": {
                    "azure_cli": BASE_CLI_VERSION,
                    "aigateway": CLI_VERSION,
                    "aigateway_sha256": CLI_SHA256,
                    "arm_api": API_VERSION,
                },
            }
        self.environment = {
            **os.environ,
            "AZURE_EXTENSION_DIR": str(DIRECTORY / "cli-extensions"),
            "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
            "AZURE_AIGATEWAY_API_VERSION": API_VERSION,
        }

    def save(self):
        write_json(STATE, self.state)

    def check_time(self):
        if datetime.now(UTC) >= datetime.fromisoformat(self.state["deadline"]):
            raise TimeoutError("H9 spike time box exhausted; preserve evidence and report")

    def cli(self, *args, timeout=120, json_output=True):
        self.check_time()
        command = [*AZ_COMMAND, *args]
        if json_output:
            command += ["--output", "json", "--only-show-errors"]
        started = now()
        result = subprocess.run(
            command, env=self.environment, capture_output=True, text=True, timeout=timeout
        )
        self.state["commands"].append(
            {
                "at": started,
                "completed_at": now(),
                "arguments": list(args),
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        self.save()
        if result.returncode:
            raise RuntimeError(f"H9 CLI failed: {args[:3]}; see local raw evidence")
        return json.loads(result.stdout) if json_output and result.stdout.strip() else result.stdout

    def bootstrap(self):
        version = self.cli("version")
        if version["azure-cli"] != BASE_CLI_VERSION:
            raise ValueError("Pinned Azure CLI version differs")
        installed = self.cli("extension", "list")
        selected = next((e for e in installed if e["name"] == "aigateway"), None)
        if selected and selected["version"] != CLI_VERSION:
            raise ValueError("Unmanifested extension version; refusing an implicit upgrade")
        if not selected:
            wheel = DIRECTORY / f"aigateway-{CLI_VERSION}-py3-none-any.whl"
            url = f"https://github.com/Azure/aigateway/releases/download/cli-v{CLI_VERSION}/{wheel.name}"
            response = httpx.get(url, follow_redirects=True, timeout=45)
            self.state["cli_download"] = {
                "url": url,
                "status": response.status_code,
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
            self.save()
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != CLI_SHA256:
                raise ValueError("Official wheel checksum differs; do not install")
            wheel.write_bytes(response.content)
            self.cli("extension", "add", "--source", str(wheel), "--yes", timeout=180)
        self.state["cli_version"] = self.cli("aigateway", "version")
        self.save()
        print(json.dumps(self.state["cli_version"], indent=2))

    def help(self):
        for parts in (("create",), ("mcp", "create"), ("mcp", "source", "add")):
            result = self.cli("aigateway", *parts, "--help", json_output=False)
            write_json(DIRECTORY / ("help-" + "-".join(parts) + ".json"), {"help": result})
            print(result)

    def create(self):
        if "gateway" in self.state:
            print("Owned gateway already recorded; use inspect")
            return
        existing = self.cli("resource", "list", "--subscription", SUB, "-g", "factory-skills")
        if any(row["id"].casefold() == RESOURCE.casefold() for row in existing):
            raise ValueError("Unmanifested gateway already exists; refusing adoption")
        self.state["intents"].append({"kind": "aiGateway", "id": RESOURCE})
        self.save()
        gateway = self.cli(
            "aigateway",
            "create",
            "--subscription",
            SUB,
            "-g",
            "factory-skills",
            "-n",
            NAME,
            "-l",
            "swedencentral",
            "--sku",
            "Standard",
            "--system-assigned",
            "true",
            "--tags",
            *[f"{key}={value}" for key, value in TAGS.items()],
            "--no-wait",
            "true",
        )
        self.state["gateway"] = gateway
        self.state["created"].append({"kind": "aiGateway", "id": RESOURCE})
        self.save()
        print(json.dumps(gateway, indent=2))

    def inspect(self):
        gateway = self.cli(
            "aigateway",
            "show",
            "--subscription",
            SUB,
            "-g",
            "factory-skills",
            "-n",
            NAME,
        )
        if gateway["id"].casefold() != RESOURCE.casefold():
            raise ValueError("Unexpected gateway ID")
        self.state["gateway"] = gateway
        self.save()
        print(json.dumps(gateway, indent=2))

    def grant(self, principal, role, scope):
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"h9:{principal}:{role}:{scope}"))
        resource = f"{scope}/providers/Microsoft.Authorization/roleAssignments/{identifier}"
        if any(row["id"].casefold() == resource.casefold() for row in self.state["created"]):
            return
        self.state["intents"].append({"kind": "roleAssignment", "id": resource})
        self.save()
        result = self.cli(
            "role",
            "assignment",
            "create",
            "--subscription",
            SUB,
            "--name",
            identifier,
            "--assignee-object-id",
            principal,
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            role,
            "--scope",
            scope,
            "--description",
            ";".join(f"{key}={value}" for key, value in TAGS.items()),
        )
        self.state["created"].append(
            {
                "kind": "roleAssignment",
                "id": result["id"],
                "principalId": principal,
            }
        )
        self.save()

    def backend(self):
        gateway = self.state["gateway"]
        principal = gateway["identity"]["principalId"]
        self.grant(principal, "53ca6127-db72-4b80-b1b0-d745d6d5456d", PROJECT_ARM)
        bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
        from adapters.core.foundry import FoundryTransport

        binding = {**bindings["mock.create_ticket"], "version": "3"}
        endpoint = FoundryTransport.base(binding) + "/mcp?api-version=v1"
        credentials = {
            "type": "managedIdentity",
            "managedIdentity": {"resource": "https://ai.azure.com"},
        }
        source = {
            "kind": "mcp",
            "namespace": "general",
            "required": True,
            "mcp": {"url": endpoint, "transport": "streamableHttp"},
            "credentials": credentials,
        }
        path = DIRECTORY / "general-endpoints.json"
        write_json(path, [source])
        self.state["general_source"] = source
        self.save()
        server = self.cli(
            "aigateway",
            "mcp",
            "create",
            "--subscription",
            SUB,
            "-g",
            "factory-skills",
            "--gateway-name",
            NAME,
            "-n",
            "general",
            "--endpoints",
            "@" + str(path),
            "--description",
            ";".join(f"{key}={value}" for key, value in TAGS.items()),
            "--failure-mode",
            "failClosed",
        )
        self.state["general_server"] = server
        self.save()
        h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
        self.grant(
            h4["release"]["principal_id"], "dce03f8d-857f-4e40-a90d-67770fa6ecd2", server["id"]
        )
        print(json.dumps(server, indent=2))

    def renew(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
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
            check=True,
            capture_output=True,
        )
        for label, name in (("coder", "factory-coder-test"), ("release", "release-test")):
            identity = h4[label]
            pem = Path(identity["certificate"])
            old = x509.load_pem_x509_certificate(pem.read_bytes())
            if old.not_valid_after_utc > datetime.now(UTC):
                print(name, "existing certificate remains valid")
                continue
            start = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
            end = start + timedelta(days=1, minutes=5)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(start)
                .not_valid_after(end)
                .sign(key, hashes.SHA256())
            )
            public = folder / f"{name}.cer"
            public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            self.cli(
                "ad",
                "app",
                "credential",
                "reset",
                "--id",
                identity["client_id"],
                "--append",
                "--cert",
                "@" + str(public),
                "--display-name",
                "capability-registry-testbed-h9",
                "--end-date",
                cert.not_valid_after_utc.isoformat(),
            )
            pem.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                + cert.public_bytes(serialization.Encoding.PEM)
            )
            self.state.setdefault("renewed_certificates", []).append(
                {
                    "identity": name,
                    "client_id": identity["client_id"],
                    "old_expires": old.not_valid_after_utc.isoformat(),
                    "expires": cert.not_valid_after_utc.isoformat(),
                    "public_fingerprint": cert.fingerprint(hashes.SHA256()).hex(),
                }
            )
            self.save()
            print(name, "certificate renewed without changing identity or grants")

    async def probe(self, approved=False):
        if self.state.get("q1_passed"):
            raise ValueError("Q1 already passed; do not silently repeat a side effect")
        h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
        identity = h4["release"]
        endpoint = self.state["general_server"]["properties"]["gatewayEndpoints"][0]["url"]
        probe = {
            "at": now(),
            "endpoint": endpoint,
            "http": [],
            "scenario": "matching middleware lease" if approved else "unleased preflight",
        }
        self.state.setdefault("q1_probes", []).append(probe)
        credential = CertificateCredential(
            identity["tenant_id"],
            identity["client_id"],
            certificate_path=identity["certificate"],
        )
        try:
            scope = "https://ai.gateway.azure.com/Data.All"
            try:
                token = await credential.get_token(scope)
            except Exception as error:
                probe["requested_scope_error"] = str(error)
                scope = "https://ai.gateway.azure.com/.default"
                token = await credential.get_token(scope)
            probe["scope_used"] = scope
            claims = jwt.decode(token.token, options={"verify_signature": False})
            probe["token_metadata_unverified"] = {
                key: claims.get(key) for key in ("aud", "oid", "tid", "roles", "scp")
            }
            self.save()
            headers = {
                "Authorization": "Bearer " + token.token,
                "Accept": "application/json, text/event-stream",
            }
            sequence = 0
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:

                async def http(url, value=None, *, auth_headers=None, method="POST"):
                    response = await client.request(
                        method, url, headers=auth_headers or headers, json=value
                    )
                    try:
                        recorded_body = json.dumps(sanitized(response.json()))
                    except json.JSONDecodeError:
                        recorded_body = response.text
                    probe["http"].append(
                        sanitized(
                            {
                                "at": now(),
                                "method": method,
                                "url": url,
                                "request_headers": (auth_headers or headers).copy(),
                                "request": value,
                                "status": response.status_code,
                                "response_headers": dict(response.headers),
                                "response": recorded_body,
                            }
                        )
                    )
                    self.save()
                    return response

                async def rpc(method, params=None, *, notification=False):
                    nonlocal sequence
                    self.check_time()
                    sequence += 1
                    body = {"jsonrpc": "2.0", "method": method}
                    if not notification:
                        body["id"] = sequence
                    if params is not None:
                        body["params"] = params
                    response = await http(endpoint, body)
                    response.raise_for_status()
                    if "mcp-session-id" in response.headers:
                        headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
                    if notification:
                        return {}
                    if "text/event-stream" in response.headers.get("content-type", ""):
                        messages = [
                            json.loads(line[5:].strip())
                            for line in response.text.splitlines()
                            if line.startswith("data:")
                        ]
                        message = next((r for r in messages if r.get("id") == sequence), {})
                    else:
                        message = response.json()
                    if "error" in message or "result" not in message:
                        raise RuntimeError(f"MCP {method} returned an error; see raw HTTP")
                    return message["result"]

                initialized = await rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "capability-registry-h9", "version": "1"},
                    },
                )
                headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
                await rpc("notifications/initialized", notification=True)
                listed = await rpc("tools/list", {})
                probe["tools"] = listed
                names = [
                    tool["name"]
                    for tool in listed.get("tools", [])
                    if tool["name"].endswith("create_ticket")
                ]
                if len(names) != 1:
                    raise RuntimeError("Federated create_ticket absent or ambiguous")
                inputs = {
                    "title": "H9 approved federation fixture"
                    if approved
                    else "H9 federation fixture"
                }
                params = {"name": names[0], "arguments": inputs}
                if approved:
                    from tools.provision_governance import Deployment

                    config = Deployment().config()
                    ai_token = await credential.get_token("https://ai.azure.com/.default")
                    ai_headers = {"Authorization": "Bearer " + ai_token.token}
                    before = await http(
                        config["mock_url"] + "/evidence",
                        method="GET",
                        auth_headers=ai_headers,
                    )
                    before.raise_for_status()
                    offset = len(before.json()["events"])
                    issued = await http(
                        config["consumer_url"] + "/leases",
                        {
                            "capability_id": "mock.create_ticket",
                            "arguments": inputs,
                            "approved": True,
                        },
                        auth_headers=ai_headers,
                    )
                    issued.raise_for_status()
                    lease = issued.json()
                    headers.update(
                        {
                            "X-Capability-Lease": lease["lease"],
                            "X-Task-Id": lease["task_id"],
                            "X-Approver": lease["approver"],
                        }
                    )
                    params["_meta"] = {
                        "capability-registry/context": {
                            "identity": identity["principal_id"],
                            "lease": lease["lease"],
                            "task_id": lease["task_id"],
                            "approver": lease["approver"],
                            "approval_id": lease["audit"][0]["lease_id"],
                        }
                    }
                    probe["issuance_audit"] = lease["audit"]
                result = await rpc("tools/call", params)
                probe["call"] = result
                if approved:
                    after = await http(
                        config["mock_url"] + "/evidence", method="GET", auth_headers=ai_headers
                    )
                    after.raise_for_status()
                    probe["ai_gateway_mock"] = after.json()["events"][offset:]
                    reference = await http(
                        config["gateway_url"] + "/mcp",
                        {
                            "jsonrpc": "2.0",
                            "id": 100,
                            "method": "tools/call",
                            "params": {"name": "create_ticket", "arguments": inputs},
                        },
                        auth_headers={
                            **ai_headers,
                            "X-Capability-Lease": lease["lease"],
                            "X-Task-Id": lease["task_id"],
                            "X-Approver": lease["approver"],
                        },
                    )
                    reference.raise_for_status()
                    final = await http(
                        config["mock_url"] + "/evidence", method="GET", auth_headers=ai_headers
                    )
                    final.raise_for_status()
                    probe["reference_mock"] = final.json()["events"][
                        offset + len(probe["ai_gateway_mock"]) :
                    ]
                    probe["reference_call"] = reference.json()
                    if not result.get("isError") or any(
                        event.get("accepted") for event in probe["ai_gateway_mock"]
                    ):
                        raise RuntimeError(
                            "Unexpected AI Gateway acceptance; review before continuing"
                        )
                    if (
                        len(probe["reference_mock"]) != 1
                        or not probe["reference_mock"][0]["accepted"]
                    ):
                        raise RuntimeError("Same-lease reference positive control failed")
                    probe["same_lease_reference_positive"] = True
                if result.get("isError"):
                    raise RuntimeError("Federated tool returned an execution error")
                self.state["q1_passed"] = now()
                probe["passed"] = True
        except Exception as error:
            probe.update(error_type=type(error).__name__, error=str(error))
            print(type(error).__name__, str(error))
        finally:
            await credential.close()
            self.save()
        print(json.dumps(sanitized(probe), indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=["bootstrap", "help", "create", "inspect", "renew", "backend", "probe", "approved"],
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    spike = Spike()
    spike.check_time()
    spike.save()
    result = spike.probe(True) if args.phase == "approved" else getattr(spike, args.phase)()
    if args.phase in {"probe", "approved"}:
        asyncio.run(result)


if __name__ == "__main__":
    main()
