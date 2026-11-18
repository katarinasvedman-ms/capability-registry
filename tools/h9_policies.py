from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from azure.identity.aio import CertificateCredential

from governance.runtime import sanitized
from tools.capture_sources import ROOT, write_json
from tools.h9_access import TOOLS_USER
from tools.h9_spike import API_VERSION, NAME, SUB, Spike, now
from tools.live_support import AsyncCliCredential
from tools.provision_governance import Deployment


async def request(spike, evidence, method, url, token, body=None):
    spike.check_time()
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        }
        response = await client.request(method, url, headers=headers, json=body)
        try:
            result = response.json()
        except json.JSONDecodeError:
            result = {"raw": response.text}
        evidence.append(
            sanitized(
                {
                    "at": now(),
                    "method": method,
                    "url": url,
                    "request_headers": headers,
                    "request": body,
                    "status": response.status_code,
                    "response_headers": dict(response.headers),
                    "response": result,
                }
            )
        )
        spike.save()
        print(method, url, response.status_code, json.dumps(result)[:1600])
        return response, result


def set_policies(spike, policies):
    path = ROOT / ".artifacts" / "h9" / "policies.json"
    write_json(path, policies)
    result = spike.cli(
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
        "--policies",
        "@" + str(path),
    )
    spike.state["general_server"] = result
    spike.save()


def hook_policy():
    return {
        "type": "agentHooks",
        "id": "h9-existing-reference-verifier",
        "scope": "resource",
        "endpoint": {"url": Deployment().config()["gateway_url"] + "/mcp"},
        "mode": "failClosed",
        "authentication": {"managedIdentity": {"resource": "https://ai.azure.com"}},
        "hooks": {"preToolCall": {}},
    }


async def probe(spike, phase):
    record = {"at": now(), "phase": phase, "http": []}
    spike.state.setdefault("q3_attempts", []).append(record)
    if phase in {"native", "cel-context"}:
        credential = AsyncCliCredential()
        try:
            token = await credential.get_token("https://management.azure.com/.default")
            server = spike.state["general_server"]
            original = server["properties"].get("policies", [])
            policy = {
                "type": "cel",
                "id": "h9-lease-feature-probe",
                "scope": "resource",
                "expression": "false",
            }
            record["purpose"] = "CEL discriminator discovery; not a JWT/JCS verifier"
            if phase == "cel-context":
                policy = {
                    "type": "ipFilter",
                    "id": "h9-cel-input-context",
                    "action": "Deny",
                    "cidrRanges": ["0.0.0.0/0", "::/0"],
                    "when": "@cel(request.body != null && tool.arguments != null)",
                }
                record["purpose"] = (
                    "Probe the required input context through the documented policy when field; "
                    "not an implemented JWT/JCS verifier"
                )
            response, _ = await request(
                spike,
                record["http"],
                "PATCH",
                "https://management.azure.com" + server["id"] + "?api-version=" + API_VERSION,
                token.token,
                {"properties": {"policies": [policy]}},
            )
            if response.is_success:
                set_policies(spike, original)
                record["restored_after_unproven_acceptance"] = True
        finally:
            await credential.close()
        record["completed_at"] = now()
        spike.save()
        return

    config = Deployment().config()
    h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
    identity = h4["release"]
    server = spike.state["general_server"]
    endpoint = server["properties"]["gatewayEndpoints"][0]["url"]
    original = (
        spike.state["hook_original"]
        if phase == "hook-probe"
        else server["properties"].get("policies", [])
    )
    try:
        if phase in {"hook", "hook-probe"}:
            record["policy"] = hook_policy()
            if phase == "hook":
                set_policies(spike, [record["policy"]])
            record["readback"] = spike.state["general_server"]
        else:
            source = {
                "kind": "mcp",
                "namespace": "reference",
                "required": True,
                "mcp": {"url": config["gateway_url"] + "/mcp", "transport": "streamableHttp"},
                "credentials": {
                    "type": "managedIdentity",
                    "managedIdentity": {"resource": "https://ai.azure.com"},
                },
            }
            path = ROOT / ".artifacts" / "h9" / "reference-endpoints.json"
            write_json(path, [source])
            server = spike.cli(
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
                "reference",
                "--endpoints",
                "@" + str(path),
                "--description",
                "purpose=capability-registry-testbed;owner=katarina.svedman;run=f3e5ad02;phase=h9",
                "--failure-mode",
                "failClosed",
            )
            spike.state["reference_server"] = server
            spike.save()
            spike.grant(identity["principal_id"], TOOLS_USER, server["id"])
            endpoint = server["properties"]["gatewayEndpoints"][0]["url"]
        async with CertificateCredential(
            identity["tenant_id"],
            identity["client_id"],
            certificate_path=identity["certificate"],
        ) as credential:
            token = await credential.get_token("https://ai.gateway.azure.com/.default")
            ticket = next(
                t["name"]
                for t in spike.state["q1_probes"][-1]["tools"]["tools"]
                if t["name"].endswith("create_ticket")
            )
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            if phase in {"hook", "hook-probe"}:
                body.update(
                    method="tools/call",
                    params={
                        "name": ticket,
                        "arguments": {"title": "H9 hook integration fixture"},
                    },
                )
            await request(spike, record["http"], "POST", endpoint, token.token, body)
    finally:
        if phase in {"hook", "hook-probe"}:
            set_policies(spike, original)
        record["completed_at"] = now()
        spike.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["native", "cel-context", "hook", "hook-install", "hook-probe", "fallback"]
    )
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    spike = Spike()
    spike.check_time()
    if args.phase == "hook-install":
        spike.state["hook_original"] = spike.state["general_server"]["properties"].get(
            "policies", []
        )
        spike.state["hook_installed_at"] = now()
        spike.save()
        set_policies(spike, [hook_policy()])
    else:
        asyncio.run(probe(spike, args.phase))


if __name__ == "__main__":
    main()
