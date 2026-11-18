from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from azure.identity.aio import CertificateCredential
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from adapters.core.foundry import FoundryTransport
from tools.capture_sources import ROOT, sha256, write_json
from tools.live_support import AsyncCliCredential
from tools.publish_mock import PROJECT_ARM

DIRECTORY = ROOT / ".artifacts" / "h4"
TENANT = "199ab2fd-dc94-4a42-8438-1144eab8c1b4"
SUBSCRIPTION = "8aebca5c-b21c-4fca-bbc9-dd77b898e99a"
TAGS = ["purpose=capability-registry-testbed", "owner=katarina.svedman", "h4=f3e5ad02"]
ROLE_IDS = (
    "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "eed3b665-ab3a-47b6-8f48-c9382fb1dad6",
    "142bfaed-a13f-4c2d-bed2-6db62c4a1009",
    "53ca6127-db72-4b80-b1b0-d745d6d5456d",
)


def now():
    return datetime.now(UTC).isoformat()


def exceeds_toolbox_access(role):
    prefix = "microsoft.cognitiveservices/accounts/aiservices/toolboxes/"
    return any(
        not action.casefold().startswith(prefix)
        for permission in role["properties"]["permissions"]
        for action in permission.get("dataActions", [])
    )


class SafetyStop(Exception):
    pass


class Preflight:
    def __init__(self):
        self.credential = AsyncCliCredential()
        self.client = httpx.AsyncClient(timeout=60, follow_redirects=False)
        self.tokens = {}
        self.state = {
            "run": "f3e5ad02",
            "tags": TAGS,
            "started_at": now(),
            "created": [],
            "http": [],
            "role_trials": [],
            "status": "in_progress",
            "privileged_project_created": False,
            "h5_started": False,
        }

    def save(self):
        write_json(
            DIRECTORY / "ownership.json",
            {
                "run": self.state["run"],
                "tags": TAGS,
                "created": self.state["created"],
                "tagging": (
                    "Entra objects use tags; ARM role assignments do not support tags "
                    "and carry these ownership labels in description instead."
                ),
            },
        )
        write_json(DIRECTORY / "evidence.json", self.state)

    async def request(self, method, url, body=None, *, allowed=(200, 201)):
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {
            "graph.microsoft.com",
            "management.azure.com",
        }:
            raise ValueError("Unexpected management/directory response URL")
        audience = (
            "https://graph.microsoft.com/.default"
            if url.startswith("https://graph.microsoft.com/")
            else "https://management.azure.com/.default"
        )
        token = self.tokens.get(audience)
        if token is None or token.expires_on <= time.time() + 300:
            token = await self.credential.get_token(audience)
            self.tokens[audience] = token
        response = await self.client.request(
            method,
            url,
            json=body,
            headers={"Authorization": f"Bearer {token.token}"},
        )
        self.state["http"].append(
            {
                "at": now(),
                "method": method,
                "url": url,
                "request": body,
                "status": response.status_code,
                "response": response.text,
            }
        )
        self.save()
        if response.status_code not in allowed:
            raise ValueError(f"{method} {url}: HTTP {response.status_code}")
        return response.json() if response.content else {}

    async def snapshot(self):
        capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
        binding = capture["binding"]
        transport = FoundryTransport(AsyncCliCredential())
        try:
            base = f"{binding['project_endpoint']}/toolboxes/{binding['toolbox']}"
            data, _ = await transport.request("GET", f"{base}?api-version=v1")
            default = json.loads(data)
            if default["default_version"] != "1":
                raise SafetyStop("General Toolbox default is no longer the approved version 1")
            snapshot = {"default": default, "versions": {}, "instruction_hashes": {}}
            for version in ("1", "2"):
                pin = {**binding, "version": version}
                raw, _ = await transport.request(
                    "GET",
                    f"{transport.base(pin)}?api-version=v1",
                    binding=pin,
                )
                snapshot["versions"][version] = {
                    "raw": raw.decode(),
                    "sha256": sha256(raw),
                }
                if json.loads(raw)["skills"] != [
                    {"name": name, "version": number}
                    for name, number in capture["skill_pins"].items()
                ]:
                    actual = {r["name"]: r["version"] for r in json.loads(raw)["skills"]}
                    if actual != capture["skill_pins"]:
                        raise SafetyStop("General skill pins changed before H4")
            for name in sorted(capture["skill_pins"]):
                content = await transport.read_resource(binding, f"skill://{name}/SKILL.md")
                snapshot["instruction_hashes"][name] = sha256(content)
            self.state["general_before"] = snapshot
            self.state["general_before_http"] = transport.evidence
            self.save()
            return {**binding, "version": "2"}
        finally:
            await transport.aclose()

    async def create_coder(self, name="factory-coder-test"):
        if name not in {"factory-coder-test", "release-test"}:
            raise ValueError("Unexpected test identity name")
        for collection in ("applications", "servicePrincipals"):
            query = (
                f"https://graph.microsoft.com/v1.0/{collection}"
                f"?$filter=displayName eq '{name}'&$select=id,appId,displayName,tags"
            )
            result = await self.request("GET", query)
            if result["value"]:
                raise ValueError(
                    "Same-name identity now exists; refusing to alter its credentials or grants"
                )
        folder = DIRECTORY / "credentials"
        folder.mkdir(exist_ok=True)
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
        pem = folder / f"{name}.pem"
        if pem.exists():
            cert = x509.load_pem_x509_certificate(pem.read_bytes())
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            start = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
            end = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
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
            with pem.open("xb") as stream:
                stream.write(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
                stream.write(cert.public_bytes(serialization.Encoding.PEM))
        # Graph key dates must not exceed the whole-second validity encoded in DER.
        start, end = cert.not_valid_before_utc, cert.not_valid_after_utc
        certificate = {"kind": "certificate", "path": str(pem), "expires": end.isoformat()}
        self.state["created"] = [
            row
            for row in self.state["created"]
            if row["kind"] != "certificate" or row["path"] != str(pem)
        ] + [certificate]
        self.save()
        application = await self.request(
            "POST",
            "https://graph.microsoft.com/v1.0/applications",
            {
                "displayName": name,
                "tags": TAGS,
                "keyCredentials": [
                    {
                        "keyId": str(uuid.uuid4()),
                        "type": "AsymmetricX509Cert",
                        "usage": "Verify",
                        "displayName": "capability-registry-testbed",
                        "startDateTime": start.isoformat(),
                        "endDateTime": end.isoformat(),
                        "key": base64.b64encode(
                            cert.public_bytes(serialization.Encoding.DER)
                        ).decode(),
                    }
                ],
            },
        )
        self.state["created"].append(
            {
                "kind": "application",
                "id": application["id"],
                "appId": application["appId"],
            }
        )
        self.save()
        principal = await self.request(
            "POST",
            "https://graph.microsoft.com/v1.0/servicePrincipals",
            {
                "appId": application["appId"],
                "tags": TAGS,
            },
        )
        self.state["created"].append(
            {
                "kind": "servicePrincipal",
                "id": principal["id"],
                "appId": application["appId"],
            }
        )
        identity = {
            "principal_id": principal["id"],
            "client_id": application["appId"],
            "tenant_id": TENANT,
            "certificate": str(pem),
        }
        self.state["coder" if name == "factory-coder-test" else "release"] = identity
        self.save()
        if name == "factory-coder-test":
            await self.assignments()
        return identity

    async def assignments(self):
        url = (
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION}"
            "/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01"
            f"&$filter=principalId eq '{self.state['coder']['principal_id']}'"
        )
        result = await self.request("GET", url)
        if result.get("nextLink"):
            raise ValueError("Unexpected paginated coder assignments; no isolation claim")
        for row in result["value"]:
            if row["properties"]["scope"].casefold() != PROJECT_ARM.casefold():
                raise SafetyStop("Coder has an unexpected grant outside the general project")
        self.state["coder_assignments"] = result["value"]
        self.save()

    async def grant(self, role, *, scope=PROJECT_ARM, identity=None):
        identity = identity or self.state["coder"]
        assignment = f"{scope}/providers/Microsoft.Authorization/roleAssignments/{uuid.uuid4()}"
        role_id = (
            f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization"
            f"/roleDefinitions/{role['name']}"
        )
        response = await self.request(
            "PUT",
            f"https://management.azure.com{assignment}?api-version=2022-04-01",
            {
                "properties": {
                    "principalId": identity["principal_id"],
                    "principalType": "ServicePrincipal",
                    "roleDefinitionId": role_id,
                    "description": ";".join(TAGS),
                }
            },
        )
        self.state["created"].append(
            {
                "kind": "roleAssignment",
                "id": response["id"],
                "principalId": identity["principal_id"],
                "roleDefinitionId": role_id,
            }
        )
        self.save()

    async def probe(self, binding, label):
        identity = self.state["coder"]
        credential = CertificateCredential(
            identity["tenant_id"],
            identity["client_id"],
            certificate_path=identity["certificate"],
        )
        transport = FoundryTransport(credential)
        pin = {
            **binding,
            "identity": {k: identity[k] for k in ("tenant_id", "client_id", "principal_id")},
        }
        probe = {"label": label, "at": now(), "success": False}
        try:
            tools = await transport.list_tools(pin)
            probe.update(success=True, advertised_tools=list(tools))
            self.state["role_trials"].append(probe)
            self.save()
            fallback = next(
                (
                    role
                    for role in self.state.get("role_definitions", [])
                    if role["name"] == ROLE_IDS[-1]
                ),
                None,
            )
            if label == "Foundry User" and fallback is None:
                raise ValueError("Fallback role permissions were not captured")
            if label == "Foundry User" and exceeds_toolbox_access(fallback):
                raise SafetyStop(
                    "Only the Foundry User fallback succeeded among tried candidates; "
                    "its Microsoft.CognitiveServices/* data action exceeds Toolbox access"
                )
            aliases = [name for name in tools if name.endswith("___deploy_release")]
            if aliases:
                trace = "h4-coder-boundary-probe-" + uuid.uuid4().hex
                result = await transport.call_tool(
                    pin,
                    aliases[0],
                    {"release": "v0.0.4"},
                    context={
                        "identity": identity["principal_id"],
                        "approval_id": "synthetic-h4-negative-boundary-probe",
                        "trace_id": trace,
                    },
                )
                log = ROOT / ".artifacts" / "mock-calls.jsonl"
                received = (
                    [json.loads(line) for line in log.read_text().splitlines() if trace in line]
                    if log.exists()
                    else []
                )
                probe.update(deploy_result=result, mock_received=received)
                self.save()
                raise SafetyStop(
                    "Coder reached deploy_release through unchanged general Toolbox version 2"
                )
        except SafetyStop:
            raise
        except Exception as error:
            probe.update(error_type=type(error).__name__, error=str(error))
            if probe not in self.state["role_trials"]:
                self.state["role_trials"].append(probe)
        finally:
            probe["http"] = transport.evidence
            self.save()
            await transport.aclose()
        return probe["success"]

    async def run(self):
        gate = json.loads((ROOT / ".artifacts" / "gate.json").read_text())
        if gate["gate"] != "open":
            raise ValueError("H4 entry gate is not open")
        write_json(DIRECTORY / "entry-gate.json", gate)
        binding = await self.snapshot()
        role_url = (
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION}"
            "/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"
        )
        roles = {"value": []}
        for _ in range(50):
            page = await self.request("GET", role_url)
            roles["value"].extend(page["value"])
            role_url = page.get("nextLink")
            if not role_url:
                break
        else:
            raise ValueError("Role definition pagination bound exceeded")
        write_json(DIRECTORY / "all-role-definitions.json", roles)
        selected = {row["name"]: row for row in roles["value"]}
        self.state["role_definitions"] = [selected[key] for key in ROLE_IDS]
        self.state["dedicated_toolbox_roles"] = [
            row
            for row in roles["value"]
            if row["properties"]["type"] == "BuiltInRole"
            and any(
                "toolbox" in action.lower()
                for permission in row["properties"]["permissions"]
                for action in permission.get("dataActions", [])
            )
        ]
        if self.state["dedicated_toolbox_roles"]:
            raise ValueError(
                "A dedicated Toolbox role exists; review it before generic role trials"
            )
        await self.create_coder()
        for key in ROLE_IDS[:-1]:
            role = selected[key]
            await self.grant(role)
            await asyncio.sleep(30)
            if await self.probe(binding, role["properties"]["roleName"]):
                raise ValueError(
                    "Narrow role works without general deploy path; project phase required"
                )
        self.state["propagation_control"] = {"started_at": now(), "wait_seconds": 900}
        self.save()
        started = time.monotonic()
        await asyncio.sleep(900)
        self.state["propagation_control"].update(
            repeated_at=now(),
            elapsed_seconds=time.monotonic() - started,
        )
        if await self.probe(binding, "Narrow-role union after fifteen minutes"):
            raise ValueError("Narrow union now works; isolate individual role before project phase")
        await self.grant(selected[ROLE_IDS[-1]])
        await self.assignments()
        for _ in range(10):
            if await self.probe(binding, "Foundry User"):
                break
            await asyncio.sleep(30)
        raise ValueError("No tested role succeeded within the bounded fallback propagation window")


async def execute():
    DIRECTORY.mkdir(exist_ok=True)
    experiment = Preflight()
    if (DIRECTORY / "ownership.json").exists():
        previous = json.loads((DIRECTORY / "evidence.json").read_text())
        if (
            previous.get("safety_stop")
            or previous["run"] != "f3e5ad02"
            or any(row["kind"] != "certificate" for row in previous["created"])
        ):
            raise ValueError("H4 objects or safety stop already exist; do not duplicate or resume")
        previous.setdefault("previous_attempts", []).append(
            {
                "status": previous["status"],
                "reason": previous.get("reason"),
                "stopped_at": previous.get("stopped_at"),
            }
        )
        experiment.state = previous
        experiment.state.update(status="in_progress", safety_stop=False)
        for field in ("reason", "error_type", "stopped_at"):
            experiment.state.pop(field, None)
    try:
        experiment.save()
        await experiment.run()
    except SafetyStop as error:
        experiment.state.update(
            status="stopped", safety_stop=True, reason=str(error), stopped_at=now()
        )
    except Exception as error:
        experiment.state.update(
            status="blocked",
            safety_stop=False,
            reason=str(error),
            error_type=type(error).__name__,
            stopped_at=now(),
        )
    finally:
        experiment.save()
        await experiment.client.aclose()
        await experiment.credential.close()
    print(
        json.dumps(
            {
                key: experiment.state.get(key)
                for key in (
                    "status",
                    "reason",
                    "coder",
                    "propagation_control",
                )
            },
            indent=2,
        )
    )
    return experiment.state["status"]


def main():
    parser = argparse.ArgumentParser(
        description="Gated certificate RBAC preflight; stops before unsafe split"
    )
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(2 if asyncio.run(execute()) == "stopped" else 1)


if __name__ == "__main__":
    main()
