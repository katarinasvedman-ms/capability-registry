from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from azure.identity.aio import CertificateCredential

from adapters.core.foundry import FoundryTransport
from tools.capture_sources import ROOT, write_json
from tools.h4_preflight import (
    DIRECTORY,
    PROJECT_ARM,
    ROLE_IDS,
    SUBSCRIPTION,
    TAGS,
    Preflight,
    SafetyStop,
    now,
)
from tools.live_support import AsyncCliCredential
from tools.publish_mock import CONNECTION

PRIVILEGED_ARM = PROJECT_ARM + "-privileged"
PRIVILEGED_CONNECTION = CONNECTION + "-privileged"
GENERAL = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())["binding"]
PRIVILEGED = {
    **GENERAL,
    "project_endpoint": GENERAL["project_endpoint"] + "-privileged",
    "toolbox": "capability-registry-privileged",
    "version": "1",
    "risk_tier": "privileged",
}
CUSTOM_ACTIONS = [
    "Microsoft.CognitiveServices/accounts/AIServices/agents/read",
    "Microsoft.CognitiveServices/accounts/AIServices/assets/read",
    "Microsoft.CognitiveServices/accounts/AIServices/responses/*",
    "Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action",
]
GENERAL_TOOLS = {"get_build_status", "create_ticket", "order_hardware"}


def logical_tools(tools):
    return {name.rsplit("___", 1)[-1] for name in tools}


def publication_body(version, *, privileged=False):
    server = dict(version["tools"][0])
    server.pop("name", None)
    server["allowed_tools"] = ["deploy_release"] if privileged else sorted(GENERAL_TOOLS)
    if privileged:
        server["project_connection_id"] = f"{PRIVILEGED_ARM}/connections/{PRIVILEGED_CONNECTION}"
        server["require_approval"] = "always"
    return {
        "description": (
            "purpose=capability-registry-testbed;owner=katarina.svedman;h4=f3e5ad02;"
            + ("privileged deploy only" if privileged else "general v3 without deploy")
        ),
        "tools": [server],
        "skills": [] if privileged else version["skills"],
    }


class ResumedH4(Preflight):
    """New authorization epoch; preserve report-three raw evidence unchanged."""

    def __init__(self):
        super().__init__()
        path = DIRECTORY / "resumed.json"
        if path.exists():
            self.state = json.loads(path.read_text())
        else:
            previous = json.loads((DIRECTORY / "evidence.json").read_text())
            self.state.update(
                created=previous["created"],
                coder=previous["coder"],
                general_before=previous["general_before"],
                authorization="report-4-project-boundary",
                prior_stop="Role breadth accepted by explicit revised user policy",
                role_breadth_is_limitation=True,
                safety_stop=False,
                probes=[],
            )

    def save(self):
        write_json(
            DIRECTORY / "ownership.json",
            {
                "run": self.state["run"],
                "tags": TAGS,
                "created": self.state["created"],
                "tagging": (
                    "Graph/project support tags; role definitions and assignments "
                    "use ownership descriptions because ARM does not support tags."
                ),
            },
        )
        write_json(DIRECTORY / "resumed.json", self.state)

    def require_safe(self):
        if self.state.get("safety_stop"):
            raise SafetyStop(self.state["reason"])

    async def probe_as(self, identity, binding, label, *, direct=False, expected=None):
        self.require_safe()
        credential = CertificateCredential(
            identity["tenant_id"],
            identity["client_id"],
            certificate_path=identity["certificate"],
        )
        pin = {
            **binding,
            "identity": {k: identity[k] for k in ("tenant_id", "client_id", "principal_id")},
        }
        transport = FoundryTransport(credential)
        observation = {"label": label, "at": now(), "binding": binding, "direct": direct}
        try:
            if direct:
                # Deliberately do not initialize: HTTP authorization must precede MCP dispatch.
                await transport.request(
                    "POST",
                    f"{transport.base(pin)}/mcp?api-version=v1",
                    body={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": self.state["deploy_tool_name"],
                            "arguments": {"release": "v0.0.4"},
                        },
                    },
                    binding=pin,
                )
            else:
                observation["tools"] = await transport.list_tools(pin)
        except Exception as error:
            observation.update(error_type=type(error).__name__, error=str(error))
        finally:
            observation["http"] = transport.evidence
            observation["status"] = transport.evidence[-1]["status"] if transport.evidence else None
            self.state["probes"].append(observation)
            self.save()
            await transport.aclose()
        if expected is not None and observation["status"] != expected:
            if identity == self.state["coder"] and binding["risk_tier"] == "privileged":
                raise SafetyStop(
                    f"Coder privileged {label}: expected 403, got {observation['status']}"
                )
            raise ValueError(f"{label}: expected {expected}, got {observation['status']}")
        return observation

    async def custom_role_setup(self):
        self.require_safe()
        gate = json.loads((ROOT / ".artifacts" / "gate.json").read_text())
        if gate["h4_entry_gate"] != "open":
            raise ValueError("H1-H3 entry prerequisites do not hold")
        if "release" in self.state or "custom_role" in self.state:
            raise ValueError("Custom-role setup already recorded; do not duplicate")
        await self.assignments()
        await self.create_coder("release-test")
        role_id = str(uuid.uuid4())
        scope = f"/subscriptions/{SUBSCRIPTION}"
        definition = {
            "properties": {
                "roleName": "Capability Registry Toolbox Candidate f3e5ad02",
                "description": ";".join(TAGS),
                "type": "CustomRole",
                "assignableScopes": [
                    PROJECT_ARM.rsplit("/providers/Microsoft.CognitiveServices", 1)[0]
                ],
                "permissions": [
                    {
                        "actions": [
                            "Microsoft.CognitiveServices/accounts/read",
                            "Microsoft.CognitiveServices/accounts/projects/read",
                        ],
                        "notActions": [],
                        "dataActions": CUSTOM_ACTIONS,
                        "notDataActions": [],
                    }
                ],
            }
        }
        response = await self.request(
            "PUT",
            f"https://management.azure.com{scope}/providers/Microsoft.Authorization/"
            f"roleDefinitions/{role_id}?api-version=2022-04-01",
            definition,
        )
        self.state["created"].append(
            {
                "kind": "roleDefinition",
                "id": response["id"],
                "roleName": definition["properties"]["roleName"],
            }
        )
        self.state["custom_role"] = response
        self.save()
        await self.grant({"name": role_id}, identity=self.state["release"])
        self.state["custom_granted_at"] = now()
        self.save()
        await self.probe_as(
            self.state["release"],
            {**GENERAL, "version": "1"},
            "Custom role initial",
        )

    async def custom_role_finish(self):
        from datetime import UTC, datetime, timedelta

        self.require_safe()
        target = datetime.fromisoformat(self.state["custom_granted_at"]) + timedelta(seconds=900)
        await asyncio.sleep(max(0, (target - datetime.now(UTC)).total_seconds()))
        result = await self.probe_as(
            self.state["release"],
            {**GENERAL, "version": "1"},
            "Custom role after full propagation",
        )
        worked = result["status"] == 200 and bool(result.get("tools") is not None)
        if not worked and result["status"] != 403:
            raise ValueError("Custom trial inconclusive: not an authorization denial")
        self.state.update(
            custom_role_supported=worked,
            selected_role=self.state["custom_role"]["name"] if worked else ROLE_IDS[-1],
            custom_role_finished_at=now(),
        )
        self.save()

    async def publish_general(self):
        self.require_safe()
        if "custom_role_supported" not in self.state:
            raise ValueError("Finish the custom-role propagation trial first")
        if "general_v3" in self.state:
            raise ValueError("General publication already recorded; no duplicate versions")
        transport = FoundryTransport(AsyncCliCredential())
        try:
            await self.check_pins(transport)
            raw, _ = await transport.request(
                "GET",
                f"{GENERAL['project_endpoint']}/toolboxes/{GENERAL['toolbox']}/versions?api-version=v1",
            )
            if {v["version"] for v in json.loads(raw)["data"]} != {"1", "2"}:
                raise ValueError("Unexpected existing versions; will not publish a guessed v3")
            source = json.loads(self.state["general_before"]["versions"]["2"]["raw"])
            body = publication_body(source)
            raw, _ = await transport.request(
                "POST",
                f"{GENERAL['project_endpoint']}/toolboxes/{GENERAL['toolbox']}/versions?api-version=v1",
                body=body,
            )
            version = json.loads(raw)
            self.state["general_v3"] = version
            self.state["created"].append(
                {
                    "kind": "toolboxVersion",
                    "id": f"factory-skills/{version['version']}",
                    "project_endpoint": GENERAL["project_endpoint"],
                    "description": body["description"],
                }
            )
            self.save()
            if version["version"] != "3" or version["skills"] != source["skills"]:
                raise SafetyStop("New general version has unexpected number or changed skill pins")
            tools = await transport.list_tools({**GENERAL, "version": "3"})
            self.state["general_v3_tools"] = tools
            if logical_tools(tools) != GENERAL_TOOLS:
                raise SafetyStop("General v3 tool list is not exactly the three authorized tools")
            await self.check_pins(transport)
        finally:
            self.state.setdefault("general_publication_http", []).extend(transport.evidence)
            self.save()
            await transport.aclose()

    async def check_pins(self, transport):
        from tools.capture_sources import sha256

        before = self.state["general_before"]
        base = f"{GENERAL['project_endpoint']}/toolboxes/{GENERAL['toolbox']}"
        raw, _ = await transport.request("GET", f"{base}?api-version=v1")
        # Creating v3 legitimately advances latest/updated_at, not default_version.
        current = json.loads(raw)
        if any(current[key] != before["default"][key] for key in ("id", "name", "default_version")):
            raise SafetyStop("General default changed")
        for version in ("1", "2"):
            pin = {**GENERAL, "version": version}
            raw, _ = await transport.request(
                "GET",
                f"{transport.base(pin)}?api-version=v1",
                binding=pin,
            )
            if sha256(raw) != before["versions"][version]["sha256"]:
                raise SafetyStop(f"Existing general version {version} bytes changed")

    async def provision(self):
        self.require_safe()
        if not self.state.get("general_v3_tools"):
            raise ValueError("Verify filtered general v3 before creating privileged resources")
        if self.state.get("privileged_version"):
            raise ValueError("Privileged publication already recorded; no duplicate versions")
        url = f"https://management.azure.com{PRIVILEGED_ARM}?api-version=2025-06-01"
        existing = await self.request("GET", url, allowed=(200, 404))
        if self.state.get("privileged_project_created"):
            if (
                not any(
                    row == {"kind": "project", "id": PRIVILEGED_ARM}
                    for row in self.state["created"]
                )
                or existing.get("tags") != dict(label.split("=", 1) for label in TAGS)
                or existing.get("id", "").casefold() != PRIVILEGED_ARM.casefold()
            ):
                raise ValueError("Previously owned project cannot be safely resumed")
        else:
            if existing.get("id"):
                raise ValueError("Privileged project already exists; refusing to adopt or alter it")
            project = await self.request(
                "PUT",
                url,
                {
                    "location": "swedencentral",
                    "kind": "AIServices",
                    "identity": {"type": "SystemAssigned"},
                    "tags": dict(label.split("=", 1) for label in TAGS),
                    "properties": {},
                },
                allowed=(200, 201, 202),
            )
            self.state["created"].append({"kind": "project", "id": PRIVILEGED_ARM})
            self.state["privileged_project_created"] = True
            self.save()
        for _ in range(60):
            project = await self.request("GET", url)
            status = project["properties"]["provisioningState"]
            if status == "Succeeded":
                break
            if status in {"Failed", "Canceled"}:
                raise ValueError(f"Privileged provisioning {status}")
            await asyncio.sleep(5)
        else:
            raise ValueError("Project provisioning timeout")
        self.state["project"] = project
        connection = json.loads((ROOT / "mocks" / "connection.json").read_text())
        connection["tags"] = dict(label.split("=", 1) for label in TAGS)
        connection["properties"]["metadata"].update(
            {
                "purpose": "capability-registry-testbed",
                "owner": "katarina.svedman",
                "h4": "f3e5ad02",
            }
        )
        connection_url = (
            f"https://management.azure.com{PRIVILEGED_ARM}/connections/"
            f"{PRIVILEGED_CONNECTION}?api-version=2025-06-01"
        )
        previous_connection = await self.request("GET", connection_url, allowed=(200, 404))
        if previous_connection.get("id"):
            owned_id = f"{PRIVILEGED_ARM}/connections/{PRIVILEGED_CONNECTION}"
            if not any(
                row == {"kind": "connection", "id": owned_id} for row in self.state["created"]
            ) or any(
                previous_connection["properties"].get(key) != value
                for key, value in connection["properties"].items()
            ):
                raise ValueError("Same-name connection is not the unchanged owned connection")
        else:
            created_connection = await self.request("PUT", connection_url, connection)
            self.state["privileged_connection"] = created_connection
            self.state["created"].append(
                {
                    "kind": "connection",
                    "id": f"{PRIVILEGED_ARM}/connections/{PRIVILEGED_CONNECTION}",
                }
            )
            self.save()
        await self.release_grants()
        transport = FoundryTransport(AsyncCliCredential())
        try:
            # ARM success can precede data-plane project availability.
            for attempt in range(31):
                try:
                    await transport.request(
                        "GET",
                        f"{PRIVILEGED['project_endpoint']}/toolboxes?api-version=v1",
                    )
                    break
                except ValueError:
                    response = transport.evidence[-1]
                    if (
                        response["status"] != 404
                        or "The project does not exist" not in response["response"]
                        or attempt == 30
                    ):
                        raise
                    await asyncio.sleep(30)
            source = json.loads(self.state["general_before"]["versions"]["2"]["raw"])
            body = publication_body(source, privileged=True)
            raw, _ = await transport.request(
                "POST",
                f"{PRIVILEGED['project_endpoint']}/toolboxes/{PRIVILEGED['toolbox']}/versions?api-version=v1",
                body=body,
            )
            version = json.loads(raw)
            self.state["privileged_version"] = version
            self.state["created"].append(
                {
                    "kind": "toolbox",
                    "id": PRIVILEGED["toolbox"],
                    "project_endpoint": PRIVILEGED["project_endpoint"],
                    "description": body["description"],
                }
            )
            self.save()
            if version["version"] != "1" or version.get("skills"):
                raise SafetyStop("Unexpected privileged composition/version")
            tools = await transport.list_tools(PRIVILEGED)
            if logical_tools(tools) != {"deploy_release"}:
                raise SafetyStop("Privileged composition is not deploy-only")
            self.state["deploy_tool_name"] = next(iter(tools))
            self.state["privileged_tools"] = tools
            self.save()
        finally:
            self.state.setdefault("privileged_publication_http", []).extend(transport.evidence)
            self.save()
            await transport.aclose()
        await self.rebind()

    async def release_grants(self):
        if self.state.get("release_granted_at"):
            return
        role = {"name": self.state["selected_role"]}
        if self.state["custom_role_supported"]:
            await self.grant(role, identity=self.state["coder"])
            # Replace only this experiment's owned broad fallback, never shared IAM.
            for item in self.state["created"]:
                if (
                    item["kind"] == "roleAssignment"
                    and item["principalId"] == self.state["coder"]["principal_id"]
                    and item["roleDefinitionId"].endswith(ROLE_IDS[-1])
                ):
                    await self.request(
                        "DELETE",
                        f"https://management.azure.com{item['id']}?api-version=2022-04-01",
                        allowed=(200, 204),
                    )
        else:
            await self.grant(role, identity=self.state["release"])
        await self.grant(role, identity=self.state["release"], scope=PRIVILEGED_ARM)
        self.state["release_granted_at"] = now()
        self.save()

    async def rebind(self):
        bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
        self.state["bindings_before"] = bindings
        for capability, definition in self.state["general_v3_tools"].items():
            logical = capability.rsplit("___", 1)[-1]
            name = "operations.order_hardware" if logical == "order_hardware" else f"mock.{logical}"
            bindings[name] = {**GENERAL, "version": "3", "tool_name": definition["name"]}
        bindings["mock.deploy_release"] = {
            **PRIVILEGED,
            "tool_name": self.state["deploy_tool_name"],
        }
        write_json(ROOT / "catalog" / "bindings.json", bindings)
        catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
        for entry in catalog:
            if entry["id"] == "mock.deploy_release":
                entry["risk_tier"] = "privileged"
        write_json(ROOT / "catalog" / "catalog.json", catalog)
        self.state["contract_rebound"] = True
        self.save()

    async def controls(self):
        import os
        from datetime import UTC, datetime

        from copilot import CopilotClient

        from tools.capture_sources import sha256
        from tools.verify_live_sdk import run_mode

        self.require_safe()
        transport = FoundryTransport(AsyncCliCredential())
        hashes = {}
        try:
            await self.check_pins(transport)
            for name, expected in self.state["general_before"]["instruction_hashes"].items():
                raw = await transport.read_resource(GENERAL, f"skill://{name}/SKILL.md")
                hashes[name] = sha256(raw)
                if hashes[name] != expected:
                    raise SafetyStop(f"Existing general instruction bytes changed: {name}")
            self.state["pin_controls"] = {
                "passed": True,
                "at": now(),
                "instruction_hashes": hashes,
                "http": transport.evidence,
            }
            self.save()
        finally:
            self.state.setdefault("control_http", []).extend(transport.evidence)
            self.save()
            await transport.aclose()
        root = DIRECTORY / ("resumed-bridge-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
        root.mkdir()
        client = CopilotClient(
            working_directory=str(root),
            base_directory=str(root / "sdk"),
            builtin_plugin_directories=[],
            env={key: value for key, value in os.environ.items() if key != "COPILOT_CLI_PATH"},
        )
        try:
            await client.start()
            catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
            capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
            result = await run_mode(client, "bridge", root, catalog, capture, "claude-haiku-4.5")
            self.state["bridge"] = result
            self.save()
            if not result["native_activation"] or not result["temporary_directory_removed"]:
                raise SafetyStop("Factory native bridge no longer succeeds")
        finally:
            await client.stop()

    async def matrix(self):
        self.require_safe()
        await self.assignments()
        general = {**GENERAL, "version": "3"}
        # A denial is required even on the first attempt; never retry away a boundary breach.
        await self.probe_as(
            self.state["coder"], PRIVILEGED, "coder privileged initialize", expected=403
        )
        await self.probe_as(
            self.state["coder"],
            PRIVILEGED,
            "coder privileged direct call",
            direct=True,
            expected=403,
        )
        coder = await self.probe_as(self.state["coder"], general, "coder general v3", expected=200)
        if logical_tools(coder["tools"]) != GENERAL_TOOLS:
            raise SafetyStop("Coder general v3 advertises deploy or lacks an expected tool")
        # Positive release controls have bounded propagation, unlike negative boundary probes.
        from datetime import UTC, datetime, timedelta

        deadline = datetime.fromisoformat(self.state["release_granted_at"]) + timedelta(seconds=960)
        for binding, label in ((general, "release general v3"), (PRIVILEGED, "release privileged")):
            for _ in range(34):
                result = await self.probe_as(self.state["release"], binding, label)
                if result["status"] == 200 and "tools" in result:
                    break
                if result["status"] != 403 or datetime.now(UTC) >= deadline:
                    raise ValueError(f"{label} positive control unavailable")
                await asyncio.sleep(30)
            else:
                raise ValueError(f"{label} did not propagate in bounded window")
        self.state["matrix_passed"] = True
        self.state["matrix_completed_at"] = now()
        self.save()

    async def repeat(self):
        from datetime import UTC, datetime, timedelta

        if not self.state.get("matrix_passed"):
            raise ValueError("Initial complete matrix required")
        target = datetime.fromisoformat(self.state["matrix_completed_at"]) + timedelta(seconds=900)
        await asyncio.sleep(max(0, (target - datetime.now(UTC)).total_seconds()))
        # Pin/bridge controls may complete while this bounded propagation wait runs.
        self.state = json.loads((DIRECTORY / "resumed.json").read_text())
        self.require_safe()
        self.state["repeat_started_at"] = now()
        result = await self.probe_as(
            self.state["coder"],
            {**GENERAL, "version": "3"},
            "repeat coder general v3",
            expected=200,
        )
        if logical_tools(result["tools"]) != GENERAL_TOOLS:
            raise SafetyStop("Repeated general v3 tool list changed")
        await self.probe_as(
            self.state["coder"],
            PRIVILEGED,
            "repeat coder privileged initialize",
            expected=403,
        )
        await self.probe_as(
            self.state["coder"],
            PRIVILEGED,
            "repeat coder privileged direct call",
            direct=True,
            expected=403,
        )
        self.state["repeat_passed"] = True
        self.save()

    async def finalize(self):
        from tools.assess_gate import h4_boundary_supported

        self.require_safe()
        maf = json.loads((DIRECTORY / "resumed-maf.json").read_text())
        negative = json.loads((DIRECTORY / "ci-negative.json").read_text())
        if not h4_boundary_supported(self.state, maf, negative):
            raise ValueError("Incomplete project-boundary evidence; cannot declare supported")
        self.state.update(
            status="completed",
            project_boundary_supported=True,
            completed_at=now(),
        )
        self.state.pop("reason", None)
        self.state.pop("error_type", None)
        self.save()


async def execute(phase):
    experiment = ResumedH4()
    try:
        experiment.require_safe()
        if experiment.state.get("status") == "blocked":
            experiment.state.setdefault("previous_failures", []).append(
                {
                    "at": now(),
                    "reason": experiment.state.get("reason"),
                    "error_type": experiment.state.get("error_type"),
                }
            )
        experiment.state["status"] = "in_progress"
        experiment.state.pop("reason", None)
        experiment.state.pop("error_type", None)
        experiment.save()
        await getattr(experiment, phase)()
        print(
            json.dumps(
                {
                    "phase": phase,
                    "status": experiment.state["status"],
                    "custom_role_supported": experiment.state.get("custom_role_supported"),
                }
            )
        )
    except SafetyStop as error:
        experiment.state.update(
            status="stopped", safety_stop=True, reason=str(error), stopped_at=now()
        )
        raise
    except Exception as error:
        experiment.state.update(
            status="blocked", reason=str(error), error_type=type(error).__name__
        )
        raise
    finally:
        experiment.save()
        await experiment.client.aclose()
        await experiment.credential.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicit revised-policy H4 phases, no promotion")
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "phase",
        choices=[
            "custom_role_setup",
            "custom_role_finish",
            "publish_general",
            "provision",
            "matrix",
            "repeat",
            "controls",
            "finalize",
        ],
    )
    args = parser.parse_args()
    asyncio.run(execute(args.phase))
