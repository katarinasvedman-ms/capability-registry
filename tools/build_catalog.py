from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from mocks.definitions import TOOLS
from tools.capture_sources import ROOT, verify, write_json

APPROVAL = json.loads((ROOT / "catalog" / "testbed-approval.json").read_text(encoding="utf-8"))


def approve(entry):
    entry.update(
        status="approved",
        risk_tier=APPROVAL.get("risk_tiers", {}).get(entry["id"], "general"),
        reviewer=APPROVAL["reviewer"],
        approved_at=APPROVAL["approved_at"],
        approval_scope=APPROVAL["approval_scope"],
    )
    return entry


def frontdoor_contract(capability: dict) -> dict:
    kind = {"agent": "agent", "handoff": "workflow"}.get(capability["type"], "tool")
    discovery = {
        "knowledge": "knowledge",
        "read": "action",
        "action": "action",
        "handoff": "handoff",
        "agent": "handoff" if capability["protocol"] == "a2a" else "knowledge",
    }[capability["type"]]
    return {
        "id": capability["id"],
        "type": kind,
        "kind": discovery,
        "domain": capability["domain"],
        "operation": {"action": "write", "handoff": "handoff"}.get(capability["type"], "read"),
        "version": capability["version"],
        "status": "draft",
        "lifecycle": {"inactive": "retired"}.get(capability["status"], capability["status"]),
        "protocol": capability["protocol"],
        "invocation": capability["invocation"],
        "name": capability["name"],
        "description": capability["description"],
        "examples": capability["examples"],
        "tags": capability["tags"],
        "owner": capability["owner"],
        "inputs": capability["input_schema"],
        "roles": [],
        "technologies": [],
        "tasks": [],
        "requires": [],
        "risk_tier": None,
        "permissions": capability["required_permissions"],
        "approval_required": capability["approval_required"],
        "reviewer": None,
        "approved_at": None,
        "approval_scope": None,
    }


def build(sources: Path) -> tuple[list[dict], dict]:
    verify(sources)
    factory = sources / "factory-skills"
    skills = json.loads((factory / "catalog.json").read_text(encoding="utf-8"))["skills"]
    evaluations = json.loads((factory / "evaluations.json").read_text(encoding="utf-8"))
    examples = {entry["skill"]: [entry["task"]] for entry in evaluations}
    entries, gaps = [], {}
    for skill in skills:
        name = skill["id"]
        if len(skill["compatibility"]) != 1:
            raise ValueError(
                f"{name}: alternative runtime compatibility needs an explicit encoding"
            )
        text = (factory / skill["path"] / "SKILL.md").read_text(encoding="utf-8")
        front = yaml.safe_load(text.split("---", 2)[1])
        entry = {
            "id": name,
            "type": "skill",
            "kind": "knowledge",
            "lifecycle": "active",
            "domain": "platform",
            "operation": "read",
            "version": skill["version"],
            "status": "draft",
            "protocol": "mcp-resource",
            "invocation": {"uri": f"skill://{name}/SKILL.md"},
            "name": front["name"],
            "description": front["description"],
            "examples": examples[name],
            "tags": [],
            "owner": skill.get("owner") or "unassigned",
            "inputs": None,
            "roles": skill["roles"],
            "technologies": [
                *skill["technologies"],
                *({"name": name, "constraint": "*"} for name in skill["prerequisites"]),
                *({"name": name, "constraint": "*"} for name in skill["compatibility"]),
            ],
            "tasks": skill["tasks"],
            "requires": skill["dependencies"],
            "risk_tier": None,
            "permissions": [],
            "approval_required": False,
            "reviewer": None,
            "approved_at": None,
            "approval_scope": None,
        }
        approve(entry)
        entries.append(entry)
        gaps[name] = {
            "unknown": [
                "owner",
            ],
            "unrepresented_source_fields": {},
            "mapping": (
                "Technology prerequisites and the single runtime compatibility label "
                "are required unconstrained technologies, not capability IDs."
            ),
            "inferred": {"kind": "knowledge", "lifecycle": "active"},
            "unspecified_applicability": ["tags"],
            "status": "Explicit testbed approval, not production approval",
        }
    frontdoor = json.loads(
        (sources / "frontdoor" / "backend" / "catalog.json").read_text(encoding="utf-8")
    )
    for capability in frontdoor:
        name = capability["id"]
        entry = frontdoor_contract(capability)
        if name in APPROVAL["frontdoor"]:
            approve(entry)
        entries.append(entry)
        gaps[name] = {
            "unknown": []
            if entry["status"] == "approved"
            else ["risk_tier", "reviewer", "approved_at"],
            "unspecified_applicability": ["roles", "technologies", "tasks", "requires"],
            "unrepresented_source_fields": {},
            "source_mapping": {
                "type": capability["type"],
                "kind": entry["kind"],
                "lifecycle": entry["lifecycle"],
                "status": capability["status"],
            },
            "inferred": {"kind": entry["kind"]} if capability["type"] == "agent" else {},
            "status": "Explicit testbed approval only for the named fixture IDs; otherwise draft",
        }
    for name in ("get_build_status", "create_ticket", "deploy_release"):
        tool = TOOLS[name]
        entry = {
            "id": f"mock.{name}",
            "type": "tool",
            "kind": "knowledge" if tool["operation"] == "read" else "action",
            "lifecycle": "active",
            "domain": "release",
            "operation": tool["operation"],
            "version": "1.0.0",
            "status": "draft",
            "protocol": "mcp",
            "invocation": {"adapter": "mcp", "tool": name},
            "name": name.replace("_", " "),
            "description": f"Synthetic testbed {name}; no real side effects.",
            "examples": [f"Test {name}"],
            "tags": ["mock", "testbed"],
            "owner": "unassigned",
            "inputs": tool["inputSchema"],
            "roles": [],
            "technologies": [],
            "tasks": [],
            "requires": [],
            "risk_tier": None,
            "permissions": [
                "operations.read" if tool["operation"] == "read" else "operations.write"
            ],
            "approval_required": tool["operation"] == "write",
            "reviewer": None,
            "approved_at": None,
            "approval_scope": None,
        }
        approve(entry)
        entries.append(entry)
        gaps[entry["id"]] = {
            "unknown": ["owner"],
            "unrepresented_source_fields": {},
            "inferred": {"kind": entry["kind"], "lifecycle": "active"},
            "status": "Explicit synthetic testbed fixture approval",
        }
    return sorted(entries, key=lambda entry: entry["id"]), {
        "entries": gaps,
        "notes": [
            "Null values are unknowns, not permission grants.",
            "Approval is limited to the explicit testbed decision; "
            "unassigned ownership remains a readiness issue.",
            "Empty applicability on imported frontdoor entries means unspecified, not verified.",
            "requires contains capability IDs, not source technology prerequisites. "
            "They are not interchangeable.",
            "Constrained technology objects are conjunctive requirements; constraint '*' "
            "requires presence without asserting a version. Plain strings are alternatives.",
            "Captured factory entries have one runtime compatibility label each. "
            "Alternative runtime labels need an agreed encoding, not conjunction.",
            "Source discovery type read maps to kind action plus operation read; "
            "knowledge maps to kind knowledge. Agent type remains explicit.",
            "Source lifecycle inactive maps to retired; approval is independent.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build truthful draft imports from frozen sources")
    parser.add_argument("--sources", type=Path, default=ROOT / "baselines")
    parser.add_argument("--output", type=Path, default=ROOT / "catalog")
    args = parser.parse_args()
    catalog, gaps = build(args.sources)
    write_json(args.output / "catalog.json", catalog)
    write_json(args.output / "field-gaps.json", gaps)
    print(f"Imported {len(catalog)} entries; explicit testbed approvals applied")


if __name__ == "__main__":
    main()
