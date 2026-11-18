import os
import re
from functools import lru_cache
from urllib.parse import urlparse

import httpx
from azure.identity import DefaultAzureCredential
from fastapi import HTTPException

from backend.config import knowledge_provider

DOCUMENTS = [
    {
        "id": "HR-SE-001",
        "title": "Sweden annual leave policy (demo)",
        "section": "2.1",
        "updated_at": "2026-10-01",
        "country": "Sweden",
        "permission": "knowledge.read",
        "text": "Employees in Sweden receive 25 annual leave days per year in this synthetic "
        "policy. Leave uses working days, Monday to Friday. Check your remaining balance "
        "before booking. Submit dates for confirmation; requests remain subject to "
        "manager review. This is demo policy, not legal advice.",
        "provenance": "mock_foundry_iq",
    },
    {
        "id": "SUP-001",
        "title": "Supply risk procedure (demo)",
        "section": "3.2",
        "updated_at": "2026-10-01",
        "country": "Sweden",
        "permission": "supply.read",
        "text": "Assess projected demand against available inventory and expected deliveries. "
        "Flag delayed suppliers and cite the inventory and shipment evidence.",
        "provenance": "mock_foundry_iq",
    },
    {
        "id": "OPS-001",
        "title": "Store equipment runbook (demo)",
        "section": "1.4",
        "updated_at": "2026-10-01",
        "country": "Sweden",
        "permission": "operations.read",
        "text": "For a broken scanner, create a service request with the equipment description. "
        "Operations reviews the ticket and arranges repair or replacement.",
        "provenance": "mock_foundry_iq",
    },
    {
        "id": "OPS-ADMIN",
        "title": "Administrative operations (demo)",
        "section": "9.1",
        "updated_at": "2026-10-01",
        "country": "Sweden",
        "permission": "operations.admin",
        "text": "Administrative report contains a synthetic aggregate of store service requests.",
        "provenance": "mock_foundry_iq",
    },
]


def search_knowledge(goal, user):
    if knowledge_provider() == "foundry_iq":
        from backend.foundry_iq import retrieve

        return retrieve(goal, user)
    if knowledge_provider() == "azure_search":
        return search_live_knowledge(goal, user)
    tokens = set(re.findall(r"[a-z]+", goal.lower()))
    candidates = []
    for doc in DOCUMENTS:
        if doc["country"] != user["country"] or doc["permission"] not in user["permissions"]:
            continue
        overlap = len(tokens & set(re.findall(r"[a-z]+", (doc["text"] + doc["title"]).lower())))
        if overlap:
            candidates.append((overlap, doc))
    return [doc for _, doc in sorted(candidates, key=lambda row: -row[0])[:3]]


def search_settings():
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
    index = os.getenv("AZURE_SEARCH_INDEX", "frontdoor-knowledge")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".search.windows.net")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,127}", index)
    ):
        raise HTTPException(503, "Live knowledge requires an Azure AI Search endpoint and index")
    return endpoint, index


@lru_cache(maxsize=1)
def search_credential():
    return DefaultAzureCredential()


def query_search_index(payload):
    endpoint, index = search_settings()
    try:
        token = search_credential().get_token("https://search.azure.com/.default").token
        response = httpx.post(
            f"{endpoint}/indexes/{index}/docs/search",
            params={"api-version": "2024-07-01"},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["value"]
    except Exception as error:
        raise HTTPException(
            503, "Live knowledge provider is unavailable; no mock fallback"
        ) from error


def source_filter(user):
    def literal(value):
        return "'" + value.replace("'", "''") + "'"

    country = literal(user["country"])
    principal = literal(user["id"])
    permissions = " or ".join(
        "permission eq " + literal(permission) for permission in sorted(set(user["permissions"]))
    )
    return (
        f"country eq {country} and ({permissions}) and "
        f"(not allowed_principals/any() or allowed_principals/any(p: p eq {principal}))"
    )


def source_permitted(doc, user):
    return (
        doc.get("country") == user["country"]
        and doc.get("permission") in user["permissions"]
        and isinstance(doc.get("allowed_principals"), list)
        and (not doc["allowed_principals"] or user["id"] in doc["allowed_principals"])
    )


def search_live_knowledge(goal, user):
    if not user["permissions"]:
        return []
    documents = query_search_index(
        {
            "search": goal,
            "searchFields": "title,text",
            "queryType": "simple",
            "top": 3,
            "filter": source_filter(user),
            "select": "id,title,section,updated_at,country,permission,text,provenance,allowed_principals",
        }
    )
    result = []
    for doc in documents:
        if not source_permitted(doc, user):
            continue
        result.append(
            {
                key: doc[key]
                for key in (
                    "id",
                    "title",
                    "section",
                    "updated_at",
                    "country",
                    "permission",
                    "text",
                    "provenance",
                )
            }
        )
    return result


def work_context(user):
    calendar = ["Monday: synthetic team planning meeting"]
    notes = "Demo team reminder: confirm cover before requesting annual leave."
    return {
        "id": f"WORK-{user['id']}",
        "title": "Synthetic employee work context",
        "section": "Profile",
        "provenance": "mock_work_iq",
        "profile": {key: user[key] for key in ("name", "role", "country", "manager", "team")},
        "text": f"Your manager is {user['manager']} and your team is {user['team']}. "
        f"Your location is {user['country']}. Your portal role is {user['role']}. "
        f"Your demo calendar: {calendar[0]}. {notes}"
        + (
            f" Your synthetic demo case is {user['demo_case_id']}."
            if user.get("demo_case_id")
            else ""
        ),
        "calendar": calendar,
        "meeting_notes": notes,
    }
