import json
import os
import re
import time
import uuid
from contextlib import contextmanager, nullcontext
from urllib.parse import urlsplit

import httpx2
from azure.identity.aio import DefaultAzureCredential
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.delegation import cloud_delegation_assertion


def configuration():
    url = os.environ.get("APIM_MCP_URL", "")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".azure-api.net")
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/mcp")
    ):
        raise RuntimeError("APIM_MCP_URL must be a configured Azure APIM HTTPS MCP endpoint")
    audience = str(uuid.UUID(os.environ.get("MOCK_API_AUDIENCE", "")))
    return url, audience


def validate_result(tool, inputs, value):
    valid = isinstance(value, dict)
    if tool == "get_inventory_status":
        valid = valid and value.get("product_id") == inputs["product_id"]
    elif tool == "get_service_request":
        valid = valid and value.get("id") == inputs["case_id"]
    elif tool == "get_hardware_order":
        valid = valid and value.get("id") == inputs["order_id"]
    elif tool == "order_hardware":
        valid = (
            valid
            and bool(re.fullmatch(r"HW-[A-F0-9]{64}", str(value.get("id", ""))))
            and value.get("item") == inputs["item"]
            and value.get("status") == "Submitted"
        )
    elif tool == "create_service_request":
        valid = (
            valid
            and isinstance(value.get("id"), str)
            and bool(value["id"])
            and value.get("title") == inputs["title"]
            and value.get("status") == "Open"
        )
    else:
        valid = False
    if not valid:
        raise HTTPException(502, "APIM gateway returned an invalid business response")
    return value


async def invoke(
    entry, inputs, approval_id, identity, trace_id, store, *, progress=None, on_timing=None
):
    @contextmanager
    def step(stage, message):
        started = time.monotonic()
        status = "completed"
        try:
            with progress(stage, message) if progress else nullcontext():
                yield
        except BaseException:
            status = "failed"
            raise
        finally:
            if on_timing:
                on_timing(stage, round((time.monotonic() - started) * 1000), status)

    url, audience = configuration()
    tool = entry.invocation["tool"]
    capability = {
        "create_service_request": "operations.create_service_request",
        "order_hardware": "operations.order_hardware",
    }.get(tool)
    if entry.approval_required and capability is None:
        raise HTTPException(422, "Unsupported approved MCP operation")
    grant = cloud_delegation_assertion(
        identity,
        audience,
        trace_id,
        store,
        approval_id if entry.approval_required else None,
        capability if entry.approval_required else None,
        inputs if entry.approval_required else None,
    )
    arguments = dict(inputs)
    if tool == "get_service_request":
        arguments = {"request_id": inputs["case_id"]}
    if entry.approval_required:
        arguments["approval_id"] = approval_id
    with step("mcp_auth", "Authenticating to the MCP gateway..."):
        async with DefaultAzureCredential() as credential:
            token = await credential.get_token(f"api://{audience}/.default")
    async with (
        httpx2.AsyncClient(
            headers={
                "Authorization": f"Bearer {token.token}",
                "x-frontdoor-delegation": grant,
                "x-trace-id": trace_id,
            },
            timeout=60,
            follow_redirects=False,
        ) as client,
        streamable_http_client(url, http_client=client) as (read, write),
        ClientSession(read, write) as session,
    ):
        with step("mcp_initialize", "Establishing the MCP gateway session..."):
            await session.initialize()
        with step("mcp_tools", "Reading the gateway's advertised MCP tools..."):
            tools = await session.list_tools()
        if tool not in {item.name for item in tools.tools}:
            raise HTTPException(502, "APIM gateway tool not advertised")
        with step("mcp_call", "Waiting for the ERP tool through the MCP gateway..."):
            result = await session.call_tool(tool, arguments)
        if result.is_error:
            raise HTTPException(502, "APIM gateway rejected the tool call")
        value = result.structured_content
        if value is None:
            value = json.loads(result.content[0].text)
        return validate_result(tool, inputs, value)
