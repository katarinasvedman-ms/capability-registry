from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess

import httpx

from adapters.core.foundry import FoundryTransport
from adapters.core.registry import same_input_schema
from mocks.definitions import TOOLS
from tools.capture_sources import ROOT, write_json
from tools.live_support import AsyncCliCredential

CONNECTION = "capability-registry-mock-f3e5ad02"
PROJECT_ARM = (
    "/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills"
    "/providers/Microsoft.CognitiveServices/accounts/factory-skills-resource/projects/factory-skills"
)


def command(*args):
    env = {**os.environ, "AZURE_DEV_USER_AGENT": "microsoft_foundry_skill"}
    completed = subprocess.run(
        ["azd", *args], env=env, text=True, capture_output=True, timeout=180, check=False
    )
    if completed.returncode:
        raise ValueError(completed.stderr[-4000:])
    return json.loads(completed.stdout)


async def run():
    capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
    binding = capture["binding"]
    endpoint = binding["project_endpoint"]
    toolbox = binding["toolbox"]
    evidence_path = ROOT / ".artifacts" / "mock-publication.json"
    if evidence_path.exists():
        raise ValueError(
            "Publication evidence exists; reuse the recorded pin instead of publishing again"
        )
    before = command("ai", "toolbox", "show", toolbox, "--project-endpoint", endpoint, "-o", "json")
    if before["toolbox"]["default_version"] != binding["version"]:
        raise ValueError(
            "Default changed since the approved historical binding; publication refused"
        )
    request = json.loads((ROOT / "mocks" / "connection.json").read_text())
    url = (
        f"https://management.azure.com{PROJECT_ARM}/connections/{CONNECTION}?api-version=2025-06-01"
    )
    credential = AsyncCliCredential()
    transport = FoundryTransport(credential)
    evidence = {
        "before": before,
        "connection_request": {"method": "PUT", "url": url, "body": request},
    }
    try:
        token = await credential.get_token("https://management.azure.com/.default")
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            headers = {"Authorization": f"Bearer {token.token}"}
            existing = await client.get(url, headers=headers)
            if existing.status_code != 404:
                existing.raise_for_status()
                properties = existing.json()["properties"]
                if any(
                    properties.get(key) != value for key, value in request["properties"].items()
                ):
                    raise ValueError("Existing same-name connection differs; will not overwrite it")
                response = existing
            else:
                response = await client.put(url, headers=headers, json=request)
                response.raise_for_status()
            connection = response.json()
            evidence["connection_response"] = {
                "status": response.status_code,
                "id": connection["id"],
                "properties": {
                    key: connection["properties"][key] for key in ("category", "authType", "target")
                },
            }
        write_json(ROOT / ".artifacts" / "mock-connection.json", evidence)
        published = command(
            "ai",
            "toolbox",
            "connection",
            "add",
            toolbox,
            CONNECTION,
            "--from-version",
            binding["version"],
            "--project-endpoint",
            endpoint,
            "-o",
            "json",
        )
        evidence["publication"] = published
        write_json(evidence_path, evidence)
        after = command(
            "ai", "toolbox", "show", toolbox, "--project-endpoint", endpoint, "-o", "json"
        )
        evidence["after"] = after
        evidence["default_unchanged"] = before["toolbox"] == after["toolbox"]
        evidence["default_content_unchanged"] = before["version"] == after["version"]
        write_json(evidence_path, evidence)
        versions = command(
            "ai",
            "toolbox",
            "versions",
            "list",
            toolbox,
            "--project-endpoint",
            endpoint,
            "-o",
            "json",
        )
        evidence["versions_after"] = versions
        write_json(evidence_path, evidence)
        if not (evidence["default_unchanged"] and evidence["default_content_unchanged"]):
            raise ValueError(
                "Concurrent/default change observed; no corrective promotion attempted"
            )
        print(json.dumps(published, indent=2))
    finally:
        await transport.aclose()


async def inspect_existing():
    evidence = json.loads((ROOT / ".artifacts" / "mock-connection.json").read_text())
    binding = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())["binding"]
    transport = FoundryTransport(AsyncCliCredential())
    try:
        base = f"{binding['project_endpoint']}/toolboxes/{binding['toolbox']}"
        data, _ = await transport.request("GET", f"{base}/versions?api-version=v1")
        versions = json.loads(data)
        write_json(ROOT / ".artifacts" / "mock-versions.json", versions)
        default, _ = await transport.request("GET", f"{base}?api-version=v1")
        evidence.update(versions_after=versions, default_after=json.loads(default))
        matches = [
            item
            for item in versions["data"]
            if any(tool.get("server_label") == CONNECTION for tool in item["tools"])
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one mock version; will not silently choose another")
        version = matches[0]
        evidence["publication"] = version
        evidence["default_unchanged"] = (
            evidence["default_after"]["default_version"]
            == evidence["before"]["toolbox"]["default_version"]
        )
        current_default = await transport.version(binding)
        evidence["default_content_unchanged"] = all(
            current_default[key] == value for key, value in evidence["before"]["version"].items()
        )
        evidence["skill_pins_unchanged"] = (
            version["skills"] == evidence["before"]["version"]["skills"]
        )
        pinned = {**binding, "version": version["version"]}
        tools = await transport.list_tools(pinned)
        evidence["advertised_tools"] = tools
        write_json(ROOT / ".artifacts" / "mock-tool-inventory.json", tools)
        captures = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
        bindings = {
            name: {**binding, "member_version": number}
            for name, number in captures["skill_pins"].items()
        }
        for name, definition in TOOLS.items():
            aliases = [
                alias
                for alias in tools
                if alias == name or alias.endswith("__" + name) or alias.endswith("_" + name)
            ]
            if len(aliases) != 1 or not same_input_schema(
                tools[aliases[0]]["inputSchema"], definition["inputSchema"]
            ):
                raise ValueError(f"Ambiguous or changed pinned mock tool schema: {name}")
            capability = "operations.order_hardware" if name == "order_hardware" else f"mock.{name}"
            bindings[capability] = {**pinned, "tool_name": aliases[0]}
        write_json(ROOT / "catalog" / "bindings.json", bindings)
        evidence["http"] = transport.evidence
        evidence["verified"] = all(
            evidence[key]
            for key in ("default_unchanged", "default_content_unchanged", "skill_pins_unchanged")
        )
        write_json(ROOT / ".artifacts" / "mock-publication-readback.json", evidence)
        write_json(ROOT / ".artifacts" / "mock-publication.json", evidence)
        print(
            json.dumps(
                {
                    "version": version["version"],
                    "default_version": evidence["default_after"]["default_version"],
                    "verified": evidence["verified"],
                    "tools": list(tools),
                },
                indent=2,
            )
        )
        if not evidence["verified"]:
            raise ValueError("Default or skill pins changed; readback is not verified")
    finally:
        await transport.aclose()


def main():
    parser = argparse.ArgumentParser(
        description="Publish only the authorized mock connection; never promote"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    asyncio.run(inspect_existing() if args.verify_existing else run())


if __name__ == "__main__":
    main()
