from __future__ import annotations

import re

STOP = {"a", "an", "the", "is", "my", "our", "for", "in", "to", "of", "and", "i", "me"}


def discovery_matches(context, entry, policy=None):
    policy = policy or {}
    lifecycle = entry["lifecycle"]
    return (
        lifecycle != "retired"
        and (lifecycle != "deprecated" or policy.get("include_deprecated", False))
        and (not context.get("lifecycle") or lifecycle == context["lifecycle"])
        and (not context.get("kind") or entry["kind"] == context["kind"])
        and (not context.get("type") or entry["type"] == context["type"])
        and (not context.get("domain") or entry["domain"].lower() == context["domain"].lower())
        and (not context.get("operation") or entry["operation"] == context["operation"])
    )


def rank(context, catalog, policy=None):
    """Source-equivalent metadata ranking; callers must apply governance first."""
    tokens = set(re.findall(r"[a-z0-9]+", context["goal"].lower())) - STOP
    permissions = set(context["permissions"])
    ranked = []
    for entry in catalog:
        if not set(entry["permissions"]) <= permissions:
            continue
        if not discovery_matches(context, entry, policy):
            continue
        fields = [
            (entry["name"], 3),
            (entry["description"], 1),
            (" ".join(entry["examples"]), 2),
            (" ".join(entry["tags"]), 3),
            (entry["domain"], 1),
        ]
        score = sum(
            weight * len(tokens & set(re.findall(r"[a-z0-9]+", text.lower())))
            for text, weight in fields
        )
        if score >= 3:
            ranked.append((score, entry["id"]))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [name for _, name in ranked[: context.get("max_results", 4)]]


def search(context, catalog, policy=None):
    policy = policy or {}
    eligible = [
        entry
        for entry in catalog
        if entry["status"] == "approved"
        and entry["permissions"] is not None
        and entry["risk_tier"] in policy.get("risk_tiers", ["general"])
    ]
    return rank(context, eligible, policy)
