import json
import os
import re

import httpx
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from fastapi import HTTPException

API_VERSION = "2024-03-01"
METADATA = "frontdoor-capabilities"
OWNER = "Owned by frontdoor demo."


def resource_id():
    value = os.getenv("APIC_RESOURCE_ID", "")
    if not re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[\w.()-]+"
        r"/providers/Microsoft\.ApiCenter/services/[a-zA-Z0-9-]{3,90}",
        value,
    ):
        raise ValueError("APIC_RESOURCE_ID must identify the configured Azure API Center")
    return value


def read_capabilities():
    base = f"https://management.azure.com{resource_id()}/workspaces/default/apis"
    url = f"{base}?api-version={API_VERSION}"
    rows = []
    try:
        with (
            DefaultAzureCredential() as credential,
            httpx.Client(timeout=10, follow_redirects=False) as client,
        ):
            token = credential.get_token("https://management.azure.com/.default").token
            for _ in range(20):
                if not url.startswith(base + "?"):
                    raise ValueError("API Center returned an unexpected inventory continuation")
                response = client.get(url, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                page = response.json()
                for asset in page["value"]:
                    properties = asset["properties"]
                    custom = properties.get("customProperties", {})
                    if not properties.get("description", "").startswith(OWNER):
                        continue
                    if METADATA not in custom:
                        continue
                    capabilities = json.loads(custom[METADATA])
                    if not isinstance(capabilities, list):
                        raise ValueError("API Center capability metadata must be an array")
                    for row in capabilities:
                        row = dict(row)
                        lifecycle = properties.get("lifecycleStage")
                        if lifecycle in {"retired", "design", "development"}:
                            row["status"] = "inactive"
                        elif lifecycle == "deprecated":
                            row["status"] = "deprecated"
                        rows.append(row)
                url = page.get("nextLink")
                if not url:
                    break
            else:
                raise ValueError("API Center inventory exceeded the bounded page limit")
    except (httpx.HTTPError, AzureError) as error:
        raise HTTPException(
            503, "API Center discovery is unavailable; no local catalog fallback"
        ) from error
    if not rows:
        raise ValueError("API Center contains no owned capability metadata")
    return rows
