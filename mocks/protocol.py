from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from mocks.definitions import TOOLS, advertised_tools, result


def dispatch(message):
    method, params = message.get("method"), message.get("params", {})
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "capability-registry-synthetic", "version": "1.0.0"},
        }
    if method in {"ping", "notifications/initialized"}:
        return {}
    if method == "tools/list":
        return {"tools": advertised_tools()}
    if method in {"resources/list", "resources/templates/list", "prompts/list"}:
        key = {
            "resources/list": "resources",
            "resources/templates/list": "resourceTemplates",
            "prompts/list": "prompts",
        }[method]
        return {key: []}
    if method != "tools/call" or params.get("name") not in TOOLS:
        raise ValueError("Unknown synthetic method or tool")
    name = params["name"]
    inputs = params.get("arguments", {})
    errors = list(Draft202012Validator(TOOLS[name]["inputSchema"]).iter_errors(inputs))
    if errors:
        raise ValueError(errors[0].message)
    if TOOLS[name]["approval_required"]:
        context = params.get("_meta", {}).get("capability-registry/context", {})
        if not context.get("approval_id"):
            return {
                "isError": True,
                "content": [{"type": "text", "text": "Missing mock approval context"}],
            }
    payload = result(name, inputs)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "structuredContent": payload,
        "isError": False,
    }
