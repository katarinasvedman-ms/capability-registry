from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from azure.identity.aio import CertificateCredential

from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from adapters.maf import integrate
from selection import select
from tools.capture_sources import ROOT, write_json
from tools.h4_preflight import DIRECTORY, now
from tools.verify_live_maf import source_row


def received_since(offset):
    log = ROOT / ".artifacts" / "mock-calls.jsonl"
    if not log.exists():
        return []
    with log.open("rb") as stream:
        stream.seek(offset)
        return [json.loads(line) for line in stream]


async def run():
    state = json.loads((DIRECTORY / "resumed.json").read_text())
    if state.get("safety_stop") or not state.get("matrix_passed"):
        raise ValueError("Passing project matrix required, with no safety stop")
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
    identity = state["release"]
    credential = CertificateCredential(
        identity["tenant_id"],
        identity["client_id"],
        certificate_path=identity["certificate"],
    )
    transport = FoundryTransport(credential)
    entries = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
    name = "mock.deploy_release"
    bindings[name]["identity"] = {
        key: identity[key] for key in ("tenant_id", "client_id", "principal_id")
    }
    audit = []
    registry = Registry(entries, bindings, transport, audit.append)
    log = ROOT / ".artifacts" / "mock-calls.jsonl"
    evidence = {"at": now(), "principal_id": identity["principal_id"], "passed": False}

    async def call_tool(**kwargs):
        return await transport.call_tool(
            kwargs["binding"],
            kwargs["binding"]["tool_name"],
            kwargs["inputs"],
            context={
                "identity": kwargs["identity"]["id"],
                "approval_id": kwargs["approval_id"],
                "trace_id": kwargs["trace_id"],
            },
        )

    try:
        with tempfile.TemporaryDirectory(prefix="capability-h4-maf-") as directory:
            path = Path(directory)
            store = Store(str(path / "test.db"))
            user = {
                **store.user("alex"),
                "id": identity["principal_id"],
                "name": "Synthetic release",
            }
            store.put("users", user["id"], user["id"], user)
            entry = registry.lookup(name)
            candidates = select(
                {
                    "goal": entry["name"],
                    "domain": entry["domain"],
                    "operation": "write",
                    "permissions": user["permissions"],
                },
                registry.list(),
                {"risk_tiers": ["privileged"]},
            ).candidate_ids
            if name not in {e["id"] for e in registry.filter(candidates)}:
                raise ValueError("Privileged deploy not selected")
            write_json(path / "catalog.json", [source_row(entry)])
            cls = integrate(
                Invoker,
                registry,
                candidates=candidates,
                rationale="Approved active privileged fixture; release project grant verified",
                call_tool=call_tool,
            )
            events = []
            invoker = cls(
                store,
                Catalog(path / "catalog.json"),
                user,
                store.new_conversation(user["id"], "Synthetic H4 deploy"),
                events.append,
            )
            inputs = {"release": "v0.0.4"}
            before = log.stat().st_size if log.exists() else 0
            pending = await invoker.invoke(name, inputs)
            denied_calls = received_since(before)
            evidence["without_approval"] = {
                "response": pending,
                "mock_received": denied_calls,
                "http": list(transport.evidence),
                "core_audit": list(audit),
                "terminated_before_dispatch": bool(pending.get("approval_required"))
                and not denied_calls
                and not transport.evidence
                and not audit,
            }
            write_json(DIRECTORY / "resumed-maf.json", evidence)
            if not evidence["without_approval"]["terminated_before_dispatch"]:
                raise ValueError("Unapproved invocation was not terminated before any dispatch")
            # The testbed completes the actual source approval state transition.
            approval = pending["approval"]["id"]
            store.transition(approval, user["id"], {"pending"}, "approved")
            before = log.stat().st_size if log.exists() else 0
            response = await invoker.invoke(name, inputs, approval)
            received = received_since(before)
            calls = [
                row
                for row in received
                if row["request"].get("method") == "tools/call"
                and row["request"].get("params", {}).get("name") == "deploy_release"
                and row["request"]["params"]["arguments"] == inputs
                and row["request"]["params"]
                .get("_meta", {})
                .get("capability-registry/context", {})
                .get("approval_id")
                == approval
            ]
            evidence["with_approval"] = {
                "response": response,
                "mock_received": received,
                "deploy_calls": calls,
                "core_audit": audit,
                "source_events": events,
                "http": transport.evidence,
            }
            evidence["passed"] = (
                len(calls) == 1
                and any(
                    row["operation"] == "tools/call" and row["status"] == 200
                    for row in transport.evidence
                )
                and any(
                    row["outcome"] == "completed"
                    and row["pin_verified"]
                    and row["identity"] == identity["principal_id"]
                    for row in audit
                )
            )
            if not evidence["passed"]:
                raise ValueError("Approved deploy lacks complete call/mock/audit evidence")
    finally:
        write_json(DIRECTORY / "resumed-maf.json", evidence)
        await transport.aclose()
    print(
        "Release certificate MAF: approved call received/audited; pending approval emitted no call"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live privileged MAF approval boundary")
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())
