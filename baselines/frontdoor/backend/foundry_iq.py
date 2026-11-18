import os
import re
from urllib.parse import urlsplit

import httpx
from azure.core.exceptions import AzureError
from fastapi import HTTPException

from backend.knowledge import search_credential, source_filter, source_permitted

API_VERSION = "2026-08-01-preview"
OWNER = "Owned by frontdoor demo."
SOURCE_FIELDS = (
    "id",
    "title",
    "section",
    "updated_at",
    "country",
    "permission",
    "text",
    "provenance",
    "allowed_principals",
)


def settings():
    endpoint = os.getenv("FOUNDRY_IQ_ENDPOINT", "").rstrip("/")
    base = os.getenv("FOUNDRY_IQ_KNOWLEDGE_BASE", "frontdoor-knowledge-base")
    source = os.getenv("FOUNDRY_IQ_KNOWLEDGE_SOURCE", "frontdoor-policies")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".search.windows.net")
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,127}", name) for name in (base, source))
    ):
        raise HTTPException(
            503, "Foundry IQ requires a configured Search endpoint and knowledge base"
        )
    return endpoint, base, source


def query_knowledge_base(payload):
    endpoint, base, _ = settings()
    try:
        token = search_credential().get_token("https://search.azure.com/.default").token
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response = client.post(
                f"{endpoint}/knowledgebases/{base}/retrieve",
                params={"api-version": API_VERSION},
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            if response.status_code != 200:
                raise HTTPException(503, "Foundry IQ retrieval failed; no Search or mock fallback")
            return response.json()
    except (httpx.HTTPError, AzureError, ValueError) as error:
        raise HTTPException(503, "Foundry IQ retrieval is unavailable; no fallback") from error


def retrieve(goal, user):
    if not user["permissions"]:
        return []
    _, base, source = settings()
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": goal}]}],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "low"},
        "includeActivity": True,
        "maxRuntimeInSeconds": 30,
        "maxOutputDocuments": 3,
        "maxOutputSize": 6000,
        "knowledgeSourceParams": [
            {
                "kind": "searchIndex",
                "knowledgeSourceName": source,
                "alwaysQuerySource": True,
                "failOnError": True,
                "filterAddOn": source_filter(user),
                "includeReferences": True,
                "includeReferenceSourceData": True,
            }
        ],
    }
    data = query_knowledge_base(payload)
    try:
        activities = data["activity"]
        references = data["references"]
        if not isinstance(activities, list) or not isinstance(references, list):
            raise ValueError("Invalid retrieval envelope")
        sources = {}
        plan = []
        for item in activities:
            if item.get("error"):
                raise ValueError("Incomplete knowledge retrieval")
            if item["type"] == "searchIndex":
                if item["knowledgeSourceName"] != source:
                    raise ValueError("Unexpected knowledge source")
                sources[str(item["id"])] = item
            plan.append(
                {
                    key: item[key]
                    for key in ("type", "id", "elapsedMs", "count", "inputTokens", "outputTokens")
                    if key in item
                }
            )
        result = []
        seen = set()
        for reference in references:
            if (
                reference["type"] != "searchIndex"
                or str(reference["activitySource"]) not in sources
            ):
                raise ValueError("Unexpected reference source")
            document = reference["sourceData"]
            if (
                not isinstance(document, dict)
                or not set(SOURCE_FIELDS) <= document.keys()
                or reference["docKey"] != document["id"]
            ):
                raise ValueError("Missing or mismatched source evidence")
            if not source_permitted(document, user) or document["id"] in seen:
                continue
            seen.add(document["id"])
            result.append(
                {
                    **{key: document[key] for key in SOURCE_FIELDS if key != "allowed_principals"},
                    "provenance": "foundry_iq_synthetic_content",
                    "retrieval": {
                        "knowledge_base": base,
                        "knowledge_source": source,
                        "reference_id": reference["id"],
                        "activities": plan,
                    },
                }
            )
        return result[:3]
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            502, "Foundry IQ returned invalid or incomplete source evidence"
        ) from error
