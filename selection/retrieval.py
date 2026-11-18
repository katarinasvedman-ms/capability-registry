from __future__ import annotations

import math
import re
import time
from collections import Counter

from .frontdoor import STOP, discovery_matches
from .static import SelectionError, select
from .technologies import constraints_met


def eligible(context, catalog, policy):
    if context.get("role"):
        ids = set(select(context, catalog, policy).candidate_ids)
        return [entry for entry in catalog if entry["id"] in ids]
    allowed = set(policy.get("allowed_ids", [e["id"] for e in catalog]))
    permissions = set(context.get("permissions", []))
    result = [
        e
        for e in catalog
        if e["id"] in allowed
        and e["status"] == "approved"
        and e["risk_tier"] in policy.get("risk_tiers", ["general"])
        and e["permissions"] is not None
        and set(e["permissions"]) <= permissions
        and discovery_matches(context, e, policy)
        and constraints_met(
            e["technologies"],
            set(context.get("technologies", [])),
            context.get("technology_versions", {}),
        )
    ]
    while True:
        ids = {e["id"] for e in result}
        remaining = [e for e in result if set(e["requires"]) <= ids]
        if len(remaining) == len(result):
            return remaining
        result = remaining


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def metadata(entry):
    return {key: entry[key] for key in ("id", "name", "description", "examples", "tags")}


def bm25(goal, entries, *, k1=1.5, b=0.75):
    documents = [
        tokens(" ".join((e["name"], e["description"], *e["examples"], *e["tags"]))) for e in entries
    ]
    if not documents:
        return []
    average = sum(map(len, documents)) / len(documents) or 1
    frequencies = Counter(term for doc in documents for term in set(doc))
    query = set(tokens(goal))
    scored = []
    for entry, document in zip(entries, documents, strict=True):
        counts = Counter(document)
        score = 0.0
        for term in query:
            frequency = counts[term]
            if frequency:
                inverse = math.log(
                    1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                score += (
                    inverse
                    * frequency
                    * (k1 + 1)
                    / (frequency + k1 * (1 - b + b * len(document) / average))
                )
        scored.append({"id": entry["id"], "score": score})
    return sorted(scored, key=lambda row: (-row["score"], row["id"]))


async def choose(context, catalog, policy, *, mode="bm25", model=None, settings=None):
    settings = settings or {}
    if mode not in {"static", "bm25", "model"}:
        raise SelectionError("Unknown selector mode")
    started = time.perf_counter()
    candidates = eligible(context, catalog, policy)
    if mode == "static":
        ids = select(context, catalog if context.get("role") else candidates, policy).candidate_ids
        if not context.get("role"):
            ids = ids[:1]
        return {
            "ids": ids,
            "rationale": "Existing same-policy static selector",
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
    ranked = bm25(
        context["goal"],
        candidates,
        k1=settings.get("bm25_k1", 1.5),
        b=settings.get("bm25_b", 0.75),
    )
    shortlist = ranked[: settings.get("top_k", 5)]
    retrieval_ms = (time.perf_counter() - started) * 1000
    if mode == "model" and shortlist:
        if model is None:
            raise SelectionError("Model mode requires an explicit model client")
        entries = {e["id"]: e for e in candidates}
        answer = await model(
            context,
            [metadata(entries[row["id"]]) for row in shortlist],
            multiple=bool(context.get("role")),
        )
        ids = answer.get("ids")
        rationale = answer.get("reason", "")
        if (
            not isinstance(ids, list)
            or not all(isinstance(name, str) for name in ids)
            or len(ids) != len(set(ids))
            or not set(ids) <= {row["id"] for row in shortlist}
            or (not context.get("role") and len(ids) > 1)
            or not isinstance(rationale, str)
            or len(rationale) > 500
        ):
            raise SelectionError("Model output exceeds the eligible shortlist or response contract")
    else:
        ids = [row["id"] for row in shortlist if row["score"] > 0]
        if not context.get("role"):
            ids = ids[:1]
        rationale = (
            "BM25 over eligible contract metadata" if shortlist else "No eligible capabilities"
        )
    selected = {e["id"]: e for e in candidates}
    required = set(policy.get("required_skills", {}).get(context.get("role"), []))
    if not required <= set(ids) or any(
        not set(selected[name]["requires"]) <= set(ids) for name in ids
    ):
        raise SelectionError("Selection dropped a required capability")
    return {
        "ids": ids,
        "rationale": rationale,
        "shortlist": shortlist,
        "retrieval_ms": retrieval_ms,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "top1_margin_candidate": bool(
            not context.get("role")
            and shortlist
            and shortlist[0]["score"] > 0
            and (
                len(shortlist) == 1
                or shortlist[0]["score"]
                >= settings.get("top1_margin_ratio", 1.5) * shortlist[1]["score"]
            )
        ),
    }
