from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from adapters.maf import integrate
from selection import select
from tools.capture_sources import ROOT, write_json
from tools.live_support import AsyncCliCredential


def source_row(entry):
    return {
        key: entry[key]
        for key in (
            "id",
            "name",
            "domain",
            "description",
            "examples",
            "tags",
            "protocol",
            "invocation",
            "approval_required",
            "owner",
            "version",
        )
    } | {
        "type": (
            "agent"
            if entry["type"] == "agent"
            else "knowledge"
            if entry["kind"] == "knowledge"
            else {"read": "read", "write": "action", "handoff": "handoff"}[entry["operation"]]
        ),
        "status": "inactive" if entry["lifecycle"] == "retired" else entry["lifecycle"],
        "input_schema": entry["inputs"],
        "required_permissions": entry["permissions"],
    }


async def run():
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        AUTH_MODE="mock",
        DEMO_MODE="mock",
        CATALOG_PROVIDER="local",
        GATEWAY_PROVIDER="mock",
        ESS_PROVIDER="mock",
        SUPPLY_PROVIDER="mock",
        KNOWLEDGE_PROVIDER="mock",
        SERVICE_SECRET="synthetic-testbed-not-a-credential",
    )
    sys.path.insert(0, str(ROOT / "baselines" / "frontdoor"))
    from backend.capabilities import Catalog
    from backend.invoker import Invoker
    from backend.store import Store

    logging.getLogger("frontdoor.audit").disabled = True
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = ROOT / ".artifacts" / "maf-live" / stamp
    root.mkdir(parents=True)
    log = ROOT / ".artifacts" / "mock-calls.jsonl"
    offset = log.stat().st_size if log.exists() else 0
    transport = FoundryTransport(AsyncCliCredential())
    audit, observations = [], []
    registry = Registry(catalog, bindings, transport, audit.append)
    fixtures = {
        "mock.get_build_status": {"build_id": "BUILD-42"},
        "mock.create_ticket": {"title": "Synthetic testbed ticket"},
        "mock.deploy_release": {"release": "v1.2.3"},
        "operations.order_hardware": {"item": "laptop"},
    }

    async def call_tool(**kwargs):
        context = {
            "identity": kwargs["identity"]["id"],
            "approval_id": kwargs["approval_id"],
            "trace_id": kwargs["trace_id"],
        }
        return await transport.call_tool(
            kwargs["binding"],
            kwargs["binding"].get("tool_name", kwargs["tool"]),
            kwargs["inputs"],
            context=context,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="capability-maf-live-") as directory:
            path = Path(directory)
            store = Store(str(path / "test.db"))
            user = store.user("alex")
            entries = [registry.catalog[name] for name in fixtures]
            write_json(path / "catalog.json", [source_row(entry) for entry in entries])
            source = Catalog(path / "catalog.json")
            await transport.list_tools(bindings["mock.get_build_status"])
            for name, inputs in fixtures.items():
                entry = registry.catalog[name]
                candidates = select(
                    {
                        "goal": entry["name"],
                        "domain": entry["domain"],
                        "operation": entry["operation"],
                        "permissions": user["permissions"],
                    },
                    registry.list(),
                    {"risk_tiers": ["general"]},
                ).candidate_ids
                filtered = registry.filter(candidates)
                assert name in {item["id"] for item in filtered}
                cls = integrate(
                    Invoker,
                    registry,
                    candidates=candidates,
                    rationale="Contract metadata: domain, operation, lexical score and permissions",
                    call_tool=call_tool,
                )
                events = []
                invoker = cls(
                    store,
                    source,
                    user,
                    store.new_conversation("alex", f"Synthetic live {name}"),
                    events.append,
                )
                observation = {
                    "id": name,
                    "listed": True,
                    "filtered_ids": candidates,
                    "inputs": inputs,
                    "completed": False,
                }
                start = len(transport.evidence)
                try:
                    response = await invoker.invoke(name, inputs)
                    if entry["approval_required"]:
                        assert response["approval_required"]
                        assert not any(
                            row["operation"] == "tools/call" for row in transport.evidence[start:]
                        )
                        approval = response["approval"]["id"]
                        observation["approval_before_call"] = approval
                        store.transition(approval, "alex", {"pending"}, "approved")
                        response = await invoker.invoke(name, inputs, approval)
                        repeated = await invoker.invoke(name, inputs, approval)
                        assert repeated == response
                        observation["idempotent_replay"] = True
                    observation.update(response=response, completed=True)
                except Exception as error:
                    observation.update(
                        error_type=type(error).__name__,
                        error=str(error),
                        detail=getattr(error, "detail", None),
                    )
                observation.update(http=transport.evidence[start:], source_events=events)
                observations.append(observation)
                write_json(root / "observations.json", observations)
    finally:
        await transport.aclose()
        received = []
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(offset)
                for line in stream:
                    record = json.loads(line)
                    if record["request"].get("method") == "tools/call":
                        received.append(record)
        for observation in observations:
            tool = registry.catalog[observation["id"]]["invocation"]["tool"]
            matching = [
                record
                for record in received
                if record["request"].get("params", {}).get("name") == tool
                and record["request"]["params"].get("arguments") == observation["inputs"]
            ]
            observation["mock_received"] = matching
            observation["completed"] = observation["completed"] and bool(matching)
        evidence = {
            "runtime": "Frozen source Invoker policy envelope; common core; live Foundry MCP",
            "maf_core_version": importlib.metadata.version("agent-framework-core"),
            "identity": (
                "Synthetic alex policy identity; operator Azure CLI credential, "
                "not worker isolation"
            ),
            "create_ticket_policy": (
                "The remote mock does not require confirmation. The original source Catalog "
                "requires confirmation for every action; this stricter consumer policy is retained."
            ),
            "attempt_directory": str(root.relative_to(ROOT)),
            "observations": observations,
            "core_audit": audit,
            "mock_received": received,
            "supported": len(observations) == 4 and all(item["completed"] for item in observations),
        }
        write_json(root / "evidence.json", evidence)
        write_json(ROOT / ".artifacts" / "maf-live.json", evidence)
    print(json.dumps({row["id"]: row["completed"] for row in observations}, indent=2))
    return evidence["supported"]


def main():
    parser = argparse.ArgumentParser(
        description="Live MAF adapter through pinned synthetic MCP tools"
    )
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(0 if asyncio.run(run()) else 1)


if __name__ == "__main__":
    main()
