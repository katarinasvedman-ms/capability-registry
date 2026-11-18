from __future__ import annotations

import ast
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from tools.capture_sources import ROOT, sha256, verify, write_json
from tools.validate_catalog import validate


def nonblank_lines(text):
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#") for line in text.splitlines()
    )


def code_counts():
    counts = {}
    for package in ("core", "copilot_sdk", "maf"):
        counts[package] = sum(
            nonblank_lines(path.read_text(encoding="utf-8"))
            for path in (ROOT / "adapters" / package).glob("*.py")
        )
    source = ROOT / "baselines" / "ai-factory" / "src" / "ai_factory" / "provider.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    inherited = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ToolboxSkillProvider":
            for method in node.body:
                if isinstance(method, ast.AsyncFunctionDef) and method.name == "read_skill":
                    inherited["copilot_materialization"] = nonblank_lines(
                        "\n".join(text.splitlines()[method.lineno - 1 : method.end_lineno])
                    )
    return {
        "method": (
            "Nonblank physical Python lines excluding comment-only lines; docstrings included"
        ),
        "new_adapter_code": counts,
        "inherited_source_code": inherited,
        "note": (
            "The original frontdoor Invoker/policy envelope is also inherited, "
            "not counted as new code"
        ),
    }


def read_evidence(name):
    path = ROOT / ".artifacts" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def h4_boundary_supported(state, maf, negative):
    probes = {row["label"]: row for row in state.get("probes", [])}
    expected = {
        "coder general v3": 200,
        "coder privileged initialize": 403,
        "coder privileged direct call": 403,
        "release general v3": 200,
        "release privileged": 200,
        "repeat coder general v3": 200,
        "repeat coder privileged initialize": 403,
        "repeat coder privileged direct call": 403,
    }
    for label, code in expected.items():
        probe = probes.get(label, {})
        if probe.get("status") != code or not probe.get("http"):
            return False
        if probe["http"][-1].get("status") != code:
            return False
        if code == 200 and (
            not any(
                row.get("operation") == "initialize" and row.get("status") == 200
                for row in probe["http"]
            )
            or not any(
                row.get("operation") == "tools/list" and row.get("status") == 200
                for row in probe["http"]
            )
        ):
            return False
        if "direct call" in label and probe["http"][-1].get("operation") != "tools/call":
            return False
        if "general v3" in label and {
            name.rsplit("___", 1)[-1] for name in probe.get("tools", {})
        } != {"get_build_status", "create_ticket", "order_hardware"}:
            return False
    try:
        elapsed = (
            datetime.fromisoformat(state["repeat_started_at"])
            - datetime.fromisoformat(state["matrix_completed_at"])
        ).total_seconds()
    except (KeyError, ValueError):
        return False
    approved = maf.get("with_approval", {})
    pending = maf.get("without_approval", {})
    return bool(
        elapsed >= 900
        and not state.get("safety_stop")
        and pending.get("terminated_before_dispatch")
        and not pending.get("mock_received")
        and not pending.get("http")
        and len(approved.get("deploy_calls", [])) == 1
        and any(
            row.get("operation") == "tools/call" and row.get("status") == 200
            for row in approved.get("http", [])
        )
        and any(
            row.get("pin_verified") and row.get("outcome") == "completed"
            for row in approved.get("core_audit", [])
        )
        and state.get("pin_controls", {}).get("passed")
        and state.get("bridge", {}).get("native_activation")
        and state.get("bridge", {}).get("temporary_directory_removed")
        and negative.get("rejected")
        and negative.get("exit_code") == 1
    )


def governance_supported(state):
    h6 = False
    try:
        rows = state["h6"]
        h6 = bool(
            state["h6_passed"]
            and not state.get("stop")
            and {row["capability_id"] for row in rows}
            == {"mock.deploy_release", "mock.create_ticket"}
            and len(rows) == 2
            and all(
                all(
                    row[field] == 403
                    for field in (
                        "test2_middleware_status",
                        "test2_gateway_status",
                        "test3_replay",
                        "test4_expired",
                        "test5_missing",
                        "test6_status",
                    )
                )
                and len(row["positive_mock"]) == 1
                and row["positive_mock"][0]["accepted"]
                and row["positive_mock"][0]["lease_id"]
                == row["test1"]["governance"]["audit"][0]["lease_id"]
                for row in rows
            )
            and state["before"] == state["final_controls"]
            and state["general_before"]["accepted"]
            and state["general_after"]["exposure_expected"]
            and not state["general_after"]["accepted"]
            and any(
                row["operation"] == "tools/call" and "HTTP 403" in row["response"]
                for row in state["general_after"]["http"]
            )
            and state["retired_tunnel_status"] == 404
            and any(
                "-privileged/" in row["url"]
                and row["status"] == 403
                and row.get("request", {}).get("method") == "tools/call"
                for row in state["http"]
                if isinstance(row.get("request"), dict)
            )
        )
        h7 = bool(
            h6
            and state["h7_passed"]
            and {row["carriage"] for row in state["h7"]} == {"header", "reserved"}
            and len(state["h7"]) == 2
            and all(
                len(row["mock"]) == 1
                and row["mock"][0]["accepted"]
                and "_capability_lease" not in row["mock"][0]["request"]["params"]["arguments"]
                and any(
                    event.get("carriage") == row["carriage"]
                    and event.get("lease_id") == row["mock"][0]["lease_id"]
                    for event in row["gateway"]
                )
                for row in state["h7"]
            )
            and len(state["h7_a2a"]["received"]) == 1
            and state["h7_a2a"]["received"][0]["accepted"]
        )
        return h6, h7
    except (KeyError, TypeError, IndexError):
        return h6, False


def assess(catalog, schema, bindings, evidence):
    structural_errors = validate(catalog, schema)
    readiness_errors = validate(catalog, schema, bindings, ready=True)
    grouped = defaultdict(list)
    for error in readiness_errors:
        name, _, cause = error.partition(": ")
        grouped[cause].append(name)
    frontdoor = evidence.get("frontdoor", {})
    source_fit = frontdoor.get("source_fit", {})
    selection = evidence.get("selection", {})
    provider_parity = selection.get("provider", {}).get("canonical_equal", False)
    bridge_parity = all(
        selection.get("bridge", {}).get(role, {}).get("equal", False)
        for role in ("coder", "tester")
    )
    domain_parity = (
        frontdoor.get("observations", {})
        .get("domain_contract_parity", {})
        .get("equal_filtered_results", False)
    )
    h1 = (
        not structural_errors
        and source_fit.get("supported", False)
        and not source_fit.get("counterexamples")
    )
    frontdoor_parity = frontdoor.get("parity", {}).get("supported", False)
    selection_cases = provider_parity and bridge_parity and domain_parity and frontdoor_parity
    maf = evidence.get("maf", {})
    live_tool_ids = [
        row["id"]
        for row in maf.get("observations", [])
        if row.get("completed")
        and row.get("mock_received")
        and any(
            call.get("operation") == "tools/call" and call.get("status") == 200
            for call in row.get("http", [])
        )
        and any(
            audit.get("entry_id") == row["id"]
            and audit.get("pin_verified")
            and audit.get("outcome") == "completed"
            for audit in maf.get("core_audit", [])
        )
    ]
    sdk = evidence.get("sdk", {})
    live_sdk_modes = [
        row["mode"]
        for row in sdk.get("modes", [])
        if row.get("native_activation")
        and any(
            audit.get("pin_verified")
            and audit.get("outcome") == "completed"
            and audit.get("entry_id") in row.get("selected", [])
            for audit in row.get("core_audit", [])
        )
        and any(
            event.get("type") in {"skill.invoked", "skill.invoked.ref"}
            for event in row.get("activations", [])
        )
    ]
    allowed_causes = {"draft, not a fixture", "owner unassigned"}
    readiness_allowed = not (set(grouped) - allowed_causes)
    required_tools = {
        "mock.get_build_status",
        "mock.create_ticket",
        "mock.deploy_release",
        "operations.order_hardware",
    }
    h2 = selection_cases and h1
    conditions = {
        "h1_supported": h1,
        "h2_supported": h2,
        "factory_provider_parity_coder_tester": provider_parity,
        "factory_bridge_parity_coder_tester": bridge_parity,
        "previously_colliding_frontdoor_pair_parity": domain_parity,
        "frontdoor_kind_lifecycle_parity": frontdoor_parity,
        "live_maf_call_tool": required_tools <= set(live_tool_ids) and maf.get("supported", False),
        "live_sdk_activation_through_core": {"bridge", "provider"} <= set(live_sdk_modes)
        and sdk.get("supported", False),
        "h3_evidence_unchanged": evidence.get("h3_unchanged", False),
        "only_allowed_readiness_causes": readiness_allowed,
        "no_h4_safety_stop": not evidence.get("h4", {}).get("safety_stop", False),
        "h4_not_blocked": evidence.get("h4", {}).get("status") != "blocked",
    }
    h6, h7 = governance_supported(evidence.get("governance", {}))
    h4 = h4_boundary_supported(
        evidence.get("h4", {}), evidence.get("h4_maf", {}), evidence.get("h4_negative", {})
    )
    return {
        "entries": len(catalog),
        "structural_errors": structural_errors,
        "readiness_errors": readiness_errors,
        "readiness_count": len(readiness_errors),
        "readiness_by_cause": dict(grouped),
        "readiness_counts_by_cause": {key: len(value) for key, value in grouped.items()},
        "hypotheses": {
            "H1": "supported"
            if h1
            else "not supported"
            if source_fit.get("counterexamples")
            else "partial",
            "H2": "supported" if h2 else "partial",
            "H3": "supported"
            if maf.get("supported") and sdk.get("supported") and live_tool_ids and live_sdk_modes
            else "partial",
            "H4": (
                "not supported"
                if evidence.get("h4", {}).get("safety_stop")
                else "supported"
                if h4_boundary_supported(
                    evidence.get("h4", {}),
                    evidence.get("h4_maf", {}),
                    evidence.get("h4_negative", {}),
                )
                else "partial"
            ),
            "H5": "partial",
            "H6": "supported" if h6 else "partial",
            "H7": "supported" if h7 else "partial",
            "H8": "partial",
        },
        "conditions": conditions,
        "live_maf_tool_ids": live_tool_ids,
        "live_sdk_modes": live_sdk_modes,
        "gate": "open" if all(conditions.values()) else "blocked",
        "h5_entry_gate": "open"
        if all(conditions.values()) and h4 and h6 and h7 and evidence.get("governance_reported")
        else "blocked",
        "h4_entry_gate": "open"
        if all(
            passed
            for key, passed in conditions.items()
            if key not in {"no_h4_safety_stop", "h4_not_blocked"}
        )
        else "blocked",
        "blockers": [name for name, passed in conditions.items() if not passed],
        "source_counterexamples": source_fit.get("counterexamples", []),
    }


def governance_reported():
    record = read_evidence(r"governance\report.json")
    live = read_evidence(r"governance\live.json")
    snapshot = ROOT / ".artifacts" / "governance" / "report-5.md"
    try:
        return bool(
            snapshot.exists()
            and sha256(snapshot.read_bytes()) == record["sha256"]
            and all(record[key] == live[key] for key in ("h6_passed", "h7_passed"))
            and datetime.fromisoformat(record["reported_at"])
            >= max(datetime.fromisoformat(live[key]) for key in ("h6_passed", "h7_passed"))
        )
    except (KeyError, ValueError, TypeError):
        return False


def model_evaluation_complete(experiment, dataset):
    try:
        cases = {case["id"]: case for case in dataset["cases"]}
        rows = experiment["results"]
        expected = Counter(
            (name, repetition)
            for name in cases
            for repetition in range(experiment["settings"]["repetitions"])
        )
        return bool(
            cases
            and datetime.fromisoformat(experiment["completed_at"])
            >= datetime.fromisoformat(experiment["started_at"])
            and Counter((row["id"], row["repetition"]) for row in rows) == expected
            and 0
            < experiment["requests"]
            == len(experiment["http"])
            <= experiment["settings"]["max_requests"]
            and all(
                all(
                    row[key] == cases[row["id"]][key]
                    for key in ("metric", "split", "expected", "optional")
                )
                and all(
                    isinstance(row[mode]["correct"], bool)
                    and isinstance(row[mode]["strict_correct"], bool)
                    and isinstance(row[mode]["elapsed_ms"], (int, float))
                    and ("ids" in row[mode] or "error" in row[mode])
                    for mode in ("static", "bm25", "model")
                )
                for row in rows
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def h5_observations():
    results = {}
    for name, suffix, folder in (("mini", "", ""), ("nano", "-nano", "nano")):
        dataset_path = ROOT / "evaluation" / f"dataset{suffix}.json"
        settings_path = ROOT / "evaluation" / f"settings{suffix}.json"
        experiment = read_evidence(str(Path("h5") / folder / "evaluation.json"))
        if not dataset_path.exists() or not settings_path.exists() or not experiment:
            results[name] = {"completed": False}
            continue
        dataset = json.loads(dataset_path.read_text())
        settings_bytes = settings_path.read_bytes()
        frozen = (
            experiment.get("dataset_sha256") == sha256(dataset_path.read_bytes())
            and dataset["settings_sha256"] == sha256(settings_bytes)
            and experiment["settings"] == json.loads(settings_bytes)
            and experiment["catalog_sha256"]
            == dataset["catalog_sha256"]
            == sha256((ROOT / "catalog" / "catalog.json").read_bytes())
            and all(
                experiment.get("selector_code", {}).get(filename)
                == sha256((ROOT / "selection" / filename).read_bytes())
                for filename in ("retrieval.py", "model.py", "static.py", "frontdoor.py")
            )
        )
        results[name] = {
            "completed": frozen and model_evaluation_complete(experiment, dataset),
            "frozen_inputs_match": frozen,
            "requests": experiment.get("requests"),
            "metrics_path": str(ROOT / ".artifacts" / "h5" / folder / "metrics.json"),
        }
    return results


def main():
    verify(ROOT / "baselines")
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    schema = json.loads((ROOT / "contracts" / "capability.schema.json").read_text())
    gaps = json.loads((ROOT / "catalog" / "field-gaps.json").read_text())
    bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
    summary = assess(
        catalog,
        schema,
        bindings,
        {
            "frontdoor": read_evidence("frontdoor.json"),
            "selection": read_evidence("selection-parity.json"),
            "maf": read_evidence("maf-live.json"),
            "sdk": read_evidence("sdk-live.json"),
            "h4": read_evidence(r"h4\resumed.json") or read_evidence(r"h4\evidence.json"),
            "h4_maf": read_evidence(r"h4\resumed-maf.json"),
            "h4_negative": read_evidence(r"h4\ci-negative.json"),
            "governance": read_evidence(r"governance\live.json"),
            "governance_reported": governance_reported(),
            "h3_unchanged": all(
                (ROOT / ".artifacts" / "round-2" / name).exists()
                and sha256((ROOT / ".artifacts" / name).read_bytes())
                == sha256((ROOT / ".artifacts" / "round-2" / name).read_bytes())
                for name in ("maf-live.json", "sdk-live.json")
            ),
        },
    )
    h4 = read_evidence(r"h4\resumed.json") or read_evidence(r"h4\evidence.json")
    summary.update(
        entries_with_unknowns=sum(bool(entry["unknown"]) for entry in gaps["entries"].values()),
        code_counts=code_counts(),
        readiness_before=len(read_evidence(r"round-1\gate.json").get("readiness_errors", [])),
        live_mutations_performed=[
            "Dedicated synthetic-only development tunnel",
            "General-project synthetic RemoteTool connection",
            "New immutable factory-skills Toolbox version 2; no promotion",
        ]
        + (
            [
                "Inventory-derived custom role and certificate release-test identity",
                "New general Toolbox version 3 excluding deploy_release; no promotion",
                "Privileged project/connection/deploy-only Toolbox; contract rebinding",
                "Owned certificate identities and project-scoped test role assignments",
            ]
            if h4.get("project_boundary_supported")
            else []
        ),
        h4_provisioned=any(row["kind"] != "certificate" for row in h4.get("created", [])),
        h4_status=h4.get("status", "unexecuted"),
        h4_reason=h4.get("reason"),
        h4_created_resource_counts={
            kind: sum(row["kind"] == kind for row in h4.get("created", []))
            for kind in (
                "application",
                "servicePrincipal",
                "roleAssignment",
                "certificate",
                "roleDefinition",
                "project",
                "connection",
                "toolboxVersion",
                "toolbox",
            )
        },
        h5_provisioned=any(
            value.get("properties", {}).get("provisioningState") == "Succeeded"
            for key, value in read_evidence(r"governance\deployment.json")
            .get("resources", {})
            .items()
            if key in {"selector_model", "selector_model_nano"}
        ),
        h5_evaluations=h5_observations(),
        h5_authorized=True,
    )
    for filename in (
        "factory-baseline.json",
        "frontdoor.json",
        "foundry-probe.json",
        "source-drift.json",
        "selection-parity.json",
        "maf-live.json",
        "sdk-live.json",
        "mock-publication.json",
    ):
        path = ROOT / ".artifacts" / filename
        if path.exists():
            summary[filename] = {"path": str(path.relative_to(ROOT))}
    write_json(ROOT / ".artifacts" / "gate.json", summary)
    print(
        json.dumps(
            {
                "entries": summary["entries"],
                "structural_errors": len(summary["structural_errors"]),
                "readiness_errors": len(summary["readiness_errors"]),
                "gate": summary["gate"],
                "h5_entry_gate": summary["h5_entry_gate"],
                "hypotheses": summary["hypotheses"],
                "conditions": summary["conditions"],
                "readiness_counts_by_cause": summary["readiness_counts_by_cause"],
                "code_counts": summary["code_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
