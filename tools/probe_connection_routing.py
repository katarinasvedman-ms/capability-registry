from __future__ import annotations

import argparse
import asyncio
import copy
import json
from datetime import UTC, datetime

import httpx
from azure.identity.aio import CertificateCredential

from adapters.core.foundry import FoundryTransport
from tools.capture_sources import ROOT, write_json
from tools.live_support import AsyncCliCredential
from tools.publish_mock import CONNECTION, PROJECT_ARM

EVIDENCE = ROOT / ".artifacts" / "governance" / "connection-routing.json"


async def run():
    if EVIDENCE.exists():
        raise ValueError("Routing evidence already exists; inspect it rather than rerun blindly")
    evidence = {"started_at": datetime.now(UTC).isoformat(), "http": []}
    credential = AsyncCliCredential()
    resumed = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
    identity = resumed["release"]
    transport = FoundryTransport(
        CertificateCredential(
            identity["tenant_id"], identity["client_id"], certificate_path=identity["certificate"]
        )
    )
    binding = json.loads((ROOT / "catalog" / "bindings.json").read_text())["mock.get_build_status"]
    connection_url = (
        f"https://management.azure.com{PROJECT_ARM}/connections/{CONNECTION}?api-version=2025-06-01"
    )
    try:
        before = await transport.version(binding)
        evidence["version_before"] = before
        token = await credential.get_token("https://management.azure.com/.default")
        async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
            headers = {"Authorization": f"Bearer {token.token}"}

            async def request(method, value=None):
                response = await client.request(method, connection_url, headers=headers, json=value)
                evidence["http"].append(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "method": method,
                        "url": connection_url,
                        "request": value,
                        "status": response.status_code,
                        "response": response.text,
                    }
                )
                write_json(EVIDENCE, evidence)
                response.raise_for_status()
                return response.json()

            original = await request("GET")
            properties = original["properties"]
            if properties["authType"] != "None" or properties["target"] != (
                "https://capability-registry-f3e5ad02-18765.eun1.devtunnels.ms/mcp"
            ):
                raise ValueError("Connection differs from the exact owned synthetic baseline")
            saved = {
                "properties": {
                    key: properties[key]
                    for key in ("category", "authType", "target", "isSharedToAll", "metadata")
                    if key in properties
                }
            }
            probe = copy.deepcopy(saved)
            probe["properties"]["target"] = (
                properties["target"].removesuffix("/mcp") + "/routing-probe"
            )
            # The only mutation is to this testbed-owned, unauthenticated RemoteTool target.
            # Restore even if a read-only synthetic invocation fails.
            try:
                await request("PUT", probe)
                observed = await request("GET")
                if observed["properties"]["target"] != probe["properties"]["target"]:
                    raise ValueError("Connection target did not change")
                try:
                    evidence["tool_result"] = await transport.call_tool(
                        binding, binding["tool_name"], {"build_id": "BUILD-267"}
                    )
                except Exception as error:
                    evidence["tool_error"] = str(error)
                evidence["tool_http"] = transport.evidence
                write_json(EVIDENCE, evidence)
            finally:
                await request("PUT", saved)
                restored = await request("GET")
                evidence["connection_restored"] = all(
                    restored["properties"].get(key) == value
                    for key, value in saved["properties"].items()
                )
                write_json(EVIDENCE, evidence)
            evidence["version_after"] = await transport.version(binding)
            evidence["version_unchanged"] = before == evidence["version_after"]
            evidence["complete"] = True
            write_json(EVIDENCE, evidence)
            if not (evidence["connection_restored"] and evidence["version_unchanged"]):
                raise RuntimeError("Routing probe restore/control failed")
    finally:
        await credential.close()
        await transport.aclose()
    print("Owned connection target probe complete and restored; inspect saved raw evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())
