from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from adapters.core import Registry
from adapters.maf import integrate
from selection.frontdoor import rank
from selection.frontdoor import search as contract_search
from tools.build_catalog import approve, frontdoor_contract
from tools.capture_sources import ROOT, verify, write_json


def discovery_context(query, permissions):
    filters = {
        "knowledge": {"type": "tool", "kind": "knowledge"},
        "read": {"type": "tool", "kind": "action", "operation": "read"},
        "action": {"type": "tool", "kind": "action", "operation": "write"},
        "agent": {"type": "agent"},
        "handoff": {"type": "workflow", "kind": "handoff"},
    }
    return {
        "goal": query.goal,
        "domain": query.domain,
        "permissions": permissions,
        "max_results": query.max_results,
        **(filters.get(query.type, {"type": "unrepresentable"}) if query.type else {}),
    }


def source_roundtrip(entry):
    source_type = (
        "agent"
        if entry["type"] == "agent"
        else "handoff"
        if entry["type"] == "workflow"
        else "knowledge"
        if entry["kind"] == "knowledge"
        else "action"
        if entry["operation"] == "write"
        else "read"
    )
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
        "type": source_type,
        "status": "inactive" if entry["lifecycle"] == "retired" else entry["lifecycle"],
        "input_schema": entry["inputs"],
        "required_permissions": entry["permissions"],
    }


async def run() -> dict:
    verify(ROOT / "baselines")
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
    from backend.capabilities import Catalog, Search
    from backend.invoker import Invoker
    from backend.store import Store
    from fastapi import HTTPException

    logging.getLogger("frontdoor.audit").disabled = True
    path = ROOT / "baselines" / "frontdoor" / "backend" / "catalog.json"
    source = Catalog(path)
    rows = json.loads(path.read_text())
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    contracts = [
        entry for entry in catalog if entry["protocol"] == "mcp" and entry["status"] == "approved"
    ]

    binding = {
        "project_endpoint": "https://fixture.services.ai.azure.com/api/projects/general",
        "toolbox": "fixture-mcp",
        "version": "1",
        "risk_tier": "general",
    }

    class Transport:
        async def version(self, _binding):
            return {"skills": [], "tools": []}

        async def list_tools(self, _binding):
            return {
                entry["invocation"]["tool"]: {"inputSchema": entry["inputs"]} for entry in contracts
            }

    core_audit, calls = [], []
    registry = Registry(
        contracts, {entry["id"]: binding for entry in contracts}, Transport(), core_audit.append
    )

    async def call_tool(**kwargs):
        calls.append(kwargs)
        return {"id": "HW-SYNTHETIC", "synthetic": True}

    cls = integrate(
        Invoker,
        registry,
        candidates=["operations.order_hardware"],
        rationale="synthetic integration fixture",
        call_tool=call_tool,
    )
    observations = {}
    with tempfile.TemporaryDirectory(prefix="capability-frontdoor-") as directory:
        store = Store(str(Path(directory) / "test.db"))
        user = store.user("alex")
        restricted = store.user("sam")
        conversation = store.new_conversation("alex", "Isolated adapter verification")
        invoker = cls(store, source, user, conversation, lambda _: None)
        restricted_invoker = cls(
            store,
            source,
            restricted,
            store.new_conversation("sam", "Isolated restricted verification"),
            lambda _: None,
        )
        query = Search(goal="new laptop")
        assert source.search(query, user)[0]["id"] == "operations.order_hardware"
        assert "operations.order_hardware" not in {
            entry["id"] for entry in source.search(query, restricted)
        }
        observations["source_discovery_permission_filter"] = True
        try:
            await restricted_invoker.invoke("operations.order_hardware", {"item": "laptop"})
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError("Restricted invocation unexpectedly succeeded")
        assert calls == []
        observations["unauthorized_invocation_denied"] = True

        try:
            await invoker.invoke("operations.order_hardware", {"item": "server"})
        except HTTPException as error:
            assert error.status_code == 422
        else:
            raise AssertionError("Invalid input unexpectedly succeeded")
        assert calls == []
        observations["input_validation_before_core"] = True

        proposal = await invoker.invoke("operations.order_hardware", {"item": "laptop"})
        assert proposal["approval_required"] and calls == []
        approval_id = proposal["approval"]["id"]
        store.transition(approval_id, "alex", {"pending"}, "approved")
        observations["approval_before_tool_call"] = True
        updated = {**user, "permissions": []}
        store.put("users", "alex", "alex", updated)
        try:
            await invoker.invoke("operations.order_hardware", {"item": "laptop"}, approval_id)
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError("Revoked permission unexpectedly succeeded")
        assert calls == []
        observations["permissions_rechecked_after_approval"] = True
        store.put("users", "alex", "alex", user)

        result = await invoker.invoke("operations.order_hardware", {"item": "laptop"}, approval_id)
        repeated = await invoker.invoke(
            "operations.order_hardware", {"item": "laptop"}, approval_id
        )
        assert result == repeated == {"id": "HW-SYNTHETIC", "synthetic": True}
        assert len(calls) == 1
        assert calls[0]["approval_id"] == approval_id
        assert calls[0]["identity"]["id"] == "alex"
        assert calls[0]["inputs"] == {"item": "laptop"}
        observations["approved_call_and_idempotent_replay"] = True
        handoff = await invoker.invoke("operations.open_expenses", {})
        assert handoff["url"] == "/lob/concur" and len(calls) == 1
        observations["handoff_unchanged"] = True

        original = next(row for row in rows if row["id"] == "operations.order_hardware")
        changed = copy.deepcopy(original)
        changed["domain"] = "AnotherDomain"
        Catalog.validated_entries([changed])
        first = Path(directory) / "domain-a.json"
        second = Path(directory) / "domain-b.json"
        write_json(first, [original])
        write_json(second, [changed])
        filtered_query = Search(goal="new laptop", domain="Operations")
        before = Catalog(first).search(filtered_query, user)
        after = Catalog(second).search(filtered_query, user)
        assert before and not after
        contract_before = contract_search(
            {
                "goal": filtered_query.goal,
                "domain": "Operations",
                "operation": "write",
                "permissions": user["permissions"],
            },
            [approve(frontdoor_contract(original))],
        )
        contract_after = contract_search(
            {
                "goal": filtered_query.goal,
                "domain": "Operations",
                "operation": "write",
                "permissions": user["permissions"],
            },
            [approve(frontdoor_contract(changed))],
        )
        assert contract_before == [entry["id"] for entry in before]
        assert contract_after == [entry["id"] for entry in after]
        assert frontdoor_contract(original) != frontdoor_contract(changed)
        observations["domain_contract_parity"] = {
            "same_contract": False,
            "equal_filtered_results": True,
            "source_original_ids": [entry["id"] for entry in before],
            "source_modified_ids": [entry["id"] for entry in after],
            "contract_original_ids": contract_before,
            "contract_modified_ids": contract_after,
            "interpretation": "Counterfactual fixture, not a change to captured sources",
        }
        fit_checks = []
        for field, capability_id, value, query in (
            (
                "type",
                "people.leave_policy",
                "read",
                Search(goal="annual leave policy", type="knowledge"),
            ),
            ("status", "operations.old_inventory", "active", Search(goal="inventory")),
        ):
            original = next(row for row in rows if row["id"] == capability_id)
            changed = {**copy.deepcopy(original), field: value}
            Catalog.validated_entries([original, changed | {"id": "counterexample.copy"}])
            write_json(first, [original])
            write_json(second, [changed])
            fixture_user = {"permissions": original["required_permissions"]}
            before = [entry["id"] for entry in Catalog(first).search(query, fixture_user)]
            after = [entry["id"] for entry in Catalog(second).search(query, fixture_user)]
            assert before != after
            assert frontdoor_contract(original) != frontdoor_contract(changed)
            context = discovery_context(query, fixture_user["permissions"])
            contract_before = rank(context, [frontdoor_contract(original)])
            contract_after = rank(context, [frontdoor_contract(changed)])
            assert (contract_before, contract_after) == (before, after)
            fit_checks.append(
                {
                    "field": field,
                    "capability_id": capability_id,
                    "original_value": original[field],
                    "counterfactual_value": value,
                    "same_contract": False,
                    "source_original_ids": before,
                    "source_modified_ids": after,
                    "contract_original_ids": contract_before,
                    "contract_modified_ids": contract_after,
                    "restored": True,
                    "query": query.model_dump(),
                    "interpretation": (
                        "Valid frozen-source counterfactual, not a source modification"
                    ),
                }
            )
        assert all(source_roundtrip(frontdoor_contract(row)) == row for row in rows)
        cases = []
        all_permissions = sorted({p for row in rows for p in row["required_permissions"]})
        projected = [frontdoor_contract(row) for row in rows]
        approved_ids = {row["id"] for row in catalog if row["status"] == "approved"}
        source_ids = {row["id"] for row in rows}
        frontdoor_catalog = [row for row in catalog if row["id"] in source_ids]
        write_json(first, [row for row in rows if row["id"] in approved_ids])
        governed_source = Catalog(first)
        for goal in sorted({text for row in rows for text in [row["name"], *row["examples"]]}):
            for kind in (None, "knowledge", "read", "action", "agent", "handoff", "unknown"):
                for domain in (None, "Operations", "People", "Supply", "UnknownDomain"):
                    for permissions in (
                        [],
                        all_permissions,
                        ["operations.read"],
                        ["operations.write"],
                    ):
                        query = Search(goal=goal, type=kind, domain=domain, max_results=5)
                        context = discovery_context(query, permissions)
                        expected = [
                            r["id"] for r in source.search(query, {"permissions": permissions})
                        ]
                        actual = rank(context, projected)
                        governed_expected = [
                            r["id"]
                            for r in governed_source.search(query, {"permissions": permissions})
                        ]
                        governed_actual = contract_search(context, frontdoor_catalog)
                        assert expected == actual, (query, expected, actual)
                        assert governed_expected == governed_actual, (
                            query,
                            governed_expected,
                            governed_actual,
                        )
                        cases.append(
                            {
                                "query": query.model_dump(),
                                "permissions": permissions,
                                "source_ids": expected,
                                "contract_ids": actual,
                                "governed_source_ids": governed_expected,
                                "governed_contract_ids": governed_actual,
                            }
                        )
        lifecycle_checks = []
        original = next(row for row in rows if row["id"] == "operations.old_inventory")
        for status in ("active", "deprecated", "inactive"):
            row = {**original, "status": status}
            write_json(first, [row])
            query = Search(goal="inventory")
            permissions = row["required_permissions"]
            expected = [r["id"] for r in Catalog(first).search(query, {"permissions": permissions})]
            context = discovery_context(query, permissions)
            entry = frontdoor_contract(row)
            assert rank(context, [entry]) == expected
            assert (
                rank({**context, "lifecycle": "retired"}, [entry], {"include_deprecated": True})
                == []
            )
            lifecycle_checks.append(
                {
                    "source_status": status,
                    "lifecycle": entry["lifecycle"],
                    "source_ids": expected,
                    "contract_ids": rank(context, [entry]),
                }
            )
        kind_checks = []
        for kind in ("knowledge", "action", "handoff"):
            # Independent source taxonomy, not the projection function used as its own oracle.
            ids = {
                row["id"]
                for row in rows
                if (
                    (
                        kind == "knowledge"
                        and (
                            row["type"] == "knowledge"
                            or (row["type"] == "agent" and row["protocol"] != "a2a")
                        )
                    )
                    or (kind == "action" and row["type"] in {"read", "action"})
                    or (
                        kind == "handoff" and (row["type"] == "handoff" or row["protocol"] == "a2a")
                    )
                )
            }
            write_json(first, [row for row in rows if row["id"] in ids])
            query = Search(goal="leave inventory hardware risk expenses", max_results=5)
            expected = [
                r["id"] for r in Catalog(first).search(query, {"permissions": all_permissions})
            ]
            actual = rank({**discovery_context(query, all_permissions), "kind": kind}, projected)
            assert actual == expected
            kind_checks.append({"kind": kind, "source_ids": expected, "contract_ids": actual})
        write_json(ROOT / ".artifacts" / "frontdoor-parity.json", cases)

    return {
        "runtime": "frozen frontdoor Catalog and Invoker",
        "transport": "in-memory protocol fixture, not live Foundry or a model session",
        "catalog_controls": "Explicit persisted testbed approvals; no synthetic reviewer overlay",
        "observations": observations,
        "core_audit": core_audit,
        "source_fit": {
            "supported": True,
            "counterexamples": [],
            "restored_counterexamples": fit_checks,
            "lossless_source_roundtrips": len(rows),
        },
        "parity": {
            "supported": True,
            "case_count": len(cases),
            "kind_filters": kind_checks,
            "lifecycle_filters": lifecycle_checks,
            "governance": (
                "Full metadata parity; actual selection applies the same explicit fixture "
                "ceiling to both oracles"
            ),
            "cases": ".artifacts\\frontdoor-parity.json",
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run isolated source Invoker integration and fit proof"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts" / "frontdoor.json")
    args = parser.parse_args()
    result = asyncio.run(run())
    write_json(args.output, result)
    print(f"Frontdoor checks passed; evidence: {args.output}")


if __name__ == "__main__":
    main()
