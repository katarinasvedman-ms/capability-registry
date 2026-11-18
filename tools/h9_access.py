from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from azure.identity.aio import CertificateCredential

from governance.runtime import sanitized
from tools.capture_sources import ROOT, write_json
from tools.h9_spike import NAME, SUB, Spike, now
from tools.publish_mock import PROJECT_ARM

TOOLS_USER = "dce03f8d-857f-4e40-a90d-67770fa6ecd2"
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def setup(spike):
    h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
    names = [tool["name"] for tool in spike.state["q1_probes"][-1]["tools"]["tools"]]
    allowed = [name for name in names if name.endswith(("get_build_status", "create_ticket"))]
    if len(allowed) != 2:
        raise ValueError("Unexpected upstream names")
    spike.state["general_server"] = spike.cli(
        "aigateway",
        "mcp",
        "update",
        "--subscription",
        SUB,
        "-g",
        "factory-skills",
        "--gateway-name",
        NAME,
        "-n",
        "general",
        "--allow-list",
        json.dumps(allowed),
    )
    spike.save()
    spike.grant(h4["coder"]["principal_id"], TOOLS_USER, spike.state["general_server"]["id"])
    spike.grant(
        spike.state["gateway"]["identity"]["principalId"],
        FOUNDRY_USER,
        PROJECT_ARM + "-privileged",
    )
    from adapters.core.foundry import FoundryTransport

    bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
    source = {
        "kind": "mcp",
        "namespace": "privileged",
        "required": True,
        "mcp": {
            "url": FoundryTransport.base(bindings["mock.deploy_release"]) + "/mcp?api-version=v1",
            "transport": "streamableHttp",
        },
        "credentials": {
            "type": "managedIdentity",
            "managedIdentity": {"resource": "https://ai.azure.com"},
        },
    }
    path = ROOT / ".artifacts" / "h9" / "privileged-endpoints.json"
    write_json(path, [source])
    spike.state["privileged_source"] = source
    spike.save()
    spike.state["privileged_server"] = spike.cli(
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
        "privileged",
        "--endpoints",
        "@" + str(path),
        "--description",
        "purpose=capability-registry-testbed;owner=katarina.svedman;run=f3e5ad02;phase=h9",
        "--failure-mode",
        "failClosed",
    )
    spike.save()
    spike.grant(h4["release"]["principal_id"], TOOLS_USER, spike.state["privileged_server"]["id"])


async def probe(spike, *, blocked=False, readonly=False):
    h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
    records = {"at": now(), "blocked_fixture": blocked, "readonly": readonly, "http": []}
    spike.state.setdefault("q2_probes", []).append(records)
    endpoints = {
        name: spike.state[name + "_server"]["properties"]["gatewayEndpoints"][0]["url"]
        for name in ("general", "privileged", "reference")
        if name + "_server" in spike.state
    }
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for label in ("release", "coder"):
            identity = h4[label]
            async with CertificateCredential(
                identity["tenant_id"],
                identity["client_id"],
                certificate_path=identity["certificate"],
            ) as credential:
                token = await credential.get_token("https://ai.gateway.azure.com/.default")
                headers = {
                    "Authorization": "Bearer " + token.token,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-03-26",
                }

                async def rpc(server, method, params):
                    spike.check_time()
                    body = {
                        "jsonrpc": "2.0",
                        "id": len(records["http"]) + 1,
                        "method": method,
                        "params": params,
                    }
                    response = await client.post(endpoints[server], headers=headers, json=body)
                    try:
                        result = response.json()
                    except json.JSONDecodeError:
                        result = {"raw": response.text}
                    records["http"].append(
                        sanitized(
                            {
                                "at": now(),
                                "caller": label,
                                "server": server,
                                "method": "POST",
                                "url": endpoints[server],
                                "request_headers": headers,
                                "request": body,
                                "status": response.status_code,
                                "response_headers": dict(response.headers),
                                "response": result,
                            }
                        )
                    )
                    spike.save()
                    print(
                        label,
                        server,
                        method,
                        response.status_code,
                        [t["name"] for t in result.get("result", {}).get("tools", [])],
                    )
                    return response, result

                await rpc("general", "tools/list", {})
                if readonly:
                    for server in ("privileged", "reference"):
                        if server in endpoints:
                            await rpc(server, "tools/list", {})
                    continue
                ticket = next(
                    t["name"]
                    for t in spike.state["q1_probes"][-1]["tools"]["tools"]
                    if t["name"].endswith("create_ticket")
                )
                if blocked:
                    await rpc(
                        "general",
                        "tools/call",
                        {
                            "name": ticket,
                            "arguments": {"title": "H9 blocked access fixture"},
                        },
                    )
                    continue
                _, privileged = await rpc("privileged", "tools/list", {})
                if label == "release":
                    names = [
                        t["name"]
                        for t in privileged.get("result", {}).get("tools", [])
                        if t["name"].endswith("deploy_release")
                    ]
                    if len(names) != 1:
                        raise RuntimeError(
                            "Release caller cannot discover exactly one privileged tool"
                        )
                    records["privileged_tool"] = names[0]
                    await rpc(
                        "privileged",
                        "tools/call",
                        {
                            "name": names[0],
                            "arguments": {"release": "v0.0.6"},
                        },
                    )
                if label == "coder":
                    deploy = records["privileged_tool"]
                    for server, tool in (
                        ("privileged", deploy),
                        ("general", deploy),
                        ("general", "general_capability-registry-mock-f3e5ad02___deploy_release"),
                    ):
                        await rpc(
                            server,
                            "tools/call",
                            {
                                "name": tool,
                                "arguments": {"release": "v0.0.6"},
                            },
                        )
    records["completed_at"] = now()
    spike.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["setup", "probe", "readonly", "block", "unblock", "blocked-probe"]
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    spike = Spike()
    spike.check_time()
    if args.phase == "setup":
        setup(spike)
    elif args.phase in {"block", "unblock"}:
        ticket = next(
            t["name"]
            for t in spike.state["q1_probes"][-1]["tools"]["tools"]
            if t["name"].endswith("create_ticket")
        )
        server = spike.cli(
            "aigateway",
            "mcp",
            "update",
            "--subscription",
            SUB,
            "-g",
            "factory-skills",
            "--gateway-name",
            NAME,
            "-n",
            "general",
            "--blocked",
            json.dumps([ticket] if args.phase == "block" else []),
        )
        spike.state["general_server"] = server
        spike.save()
    else:
        asyncio.run(
            probe(spike, blocked=args.phase == "blocked-probe", readonly=args.phase == "readonly")
        )


if __name__ == "__main__":
    main()
