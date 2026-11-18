from __future__ import annotations

import hashlib
import json


def object_schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS = {
    "get_build_status": {
        "operation": "read",
        "approval_required": False,
        "inputSchema": object_schema(
            {"build_id": {"type": "string", "pattern": "^BUILD-[0-9]{1,8}$"}}, ["build_id"]
        ),
    },
    "create_ticket": {
        "operation": "write",
        "approval_required": False,
        "inputSchema": object_schema(
            {"title": {"type": "string", "minLength": 5, "maxLength": 200}}, ["title"]
        ),
    },
    "deploy_release": {
        "operation": "write",
        "approval_required": True,
        "inputSchema": object_schema(
            {"release": {"type": "string", "pattern": "^v[0-9]+\\.[0-9]+\\.[0-9]+$"}},
            ["release"],
        ),
    },
    "order_hardware": {
        "operation": "write",
        "approval_required": True,
        "inputSchema": {
            "type": "object",
            "properties": {"item": {"type": "string", "enum": ["laptop"]}},
            "required": ["item"],
            "additionalProperties": False,
        },
    },
}


def advertised_tools():
    return [
        {
            "name": name,
            "description": f"Synthetic testbed only: {name}. No external side effects.",
            "inputSchema": entry["inputSchema"],
            "annotations": {
                "readOnlyHint": entry["operation"] == "read",
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "_meta": {"approval_required": entry["approval_required"]},
        }
        for name, entry in TOOLS.items()
    ]


def result(name, inputs):
    digest = (
        hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()[:12]
        .upper()
    )
    if name == "get_build_status":
        return {"synthetic": True, **inputs, "status": "succeeded", "commit": "00000000"}
    prefix = {
        "create_ticket": "MOCK-TICKET",
        "deploy_release": "MOCK-DEPLOY",
        "order_hardware": "HW",
    }
    return {"synthetic": True, "id": f"{prefix[name]}-{digest}", **inputs}
