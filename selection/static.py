from __future__ import annotations

from dataclasses import dataclass

from .frontdoor import discovery_matches, search
from .technologies import applicable, constraints_met, matches_names

ROLES = {"architect", "planner", "coder", "reviewer", "tester"}
TASKS = {
    "architecture",
    "planning",
    "implementation",
    "review",
    "testing",
    "legacy",
    "api",
    "database",
}


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionResult:
    candidate_ids: list[str]
    rationale: str


def select(context: dict, catalog: list[dict], policy: dict) -> SelectionResult:
    if context.get("role") is None and "goal" in context:
        return SelectionResult(
            search(context, catalog, policy),
            "Approved kind, lifecycle, domain, operation, lexical metadata and current permissions",
        )
    role = context.get("role")
    if role not in ROLES:
        raise SelectionError("Provide a known factory role")
    entries = {entry["id"]: entry for entry in catalog}
    if len(entries) != len(catalog):
        raise SelectionError("Duplicate catalog IDs")
    allowed = set(policy.get("allowed_skills", []))
    if not allowed <= entries.keys():
        raise SelectionError("Policy allows a skill absent from the catalog")
    required = policy.get("required_skills", {})
    if not set(required) <= ROLES:
        raise SelectionError("Required skills reference an unknown role")
    tech = set(context.get("technologies", []))
    permissions = set(context.get("permissions", []))
    tiers = set(policy.get("risk_tiers", ["general"]))
    versions = context.get("technology_versions", {})
    mode = context.get("mode", "provider")
    if mode not in {"bridge", "provider"}:
        raise SelectionError("Unknown selection mode")
    domains = context.get("domains")
    operations = context.get("operations")

    def eligible(name):
        entry = entries[name]
        return (
            entry["type"] == "skill"
            and entry["status"] == "approved"
            and entry["risk_tier"] in tiers
            and entry["permissions"] is not None
            and set(entry["permissions"]) <= permissions
            and name in allowed
            and role in entry["roles"]
            and discovery_matches(context, entry, policy)
            and (domains is None or entry["domain"].lower() in {d.lower() for d in domains})
            and (operations is None or entry["operation"] in operations)
        )

    tasks = set(context.get("tasks", []))
    if not tasks or not tasks <= TASKS:
        raise SelectionError("Provide known task categories")
    chosen, active = set(), set()

    def include(name):
        if name not in entries or not eligible(name):
            raise SelectionError(f"Skill is missing or unauthorized: {name}")
        if name in active:
            raise SelectionError("Cyclic dependency")
        entry = entries[name]
        if not constraints_met(entry["technologies"], tech, versions):
            raise SelectionError(f"Unmet technology constraint: {name}")
        if name not in chosen:
            active.add(name)
            for dependency in entry.get("requires", []):
                include(dependency)
            active.remove(name)
            chosen.add(name)

    for name, entry in sorted(entries.items()):
        if (
            eligible(name)
            and (
                matches_names(entry["technologies"], tech)
                if mode == "bridge"
                else applicable(entry["technologies"], tech, versions)
            )
            and ("*" in entry["tasks"] or tasks.intersection(entry["tasks"]))
        ):
            include(name)
    for name in required.get(role, []):
        include(name)
    if not chosen:
        raise SelectionError(f"No approved skills apply to role {role}")
    result = sorted(chosen)
    if "permitted_skills" in policy:
        permitted = policy["permitted_skills"]
        if (
            not isinstance(permitted, dict)
            or role not in permitted
            or not set(permitted) <= ROLES
            or not isinstance(permitted[role], list)
            or len(permitted[role]) != len(set(permitted[role]))
            or not set(result) <= set(permitted[role])
        ):
            raise SelectionError("Metadata selection exceeds the explicit provider policy")
    return SelectionResult(
        result, "Approved role, domain, operation, technology, task and requires metadata"
    )
