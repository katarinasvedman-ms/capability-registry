import json

import httpx
import pytest

from adapters.core import RegistryError
from adapters.core.foundry import FoundryTransport
from tests.test_foundry_transport import Credential


async def test_resource_reads_retry_but_tool_invocations_do_not(binding):
    counts = {"resources/read": 0, "tools/call": 0}

    async def handle(request):
        body = json.loads(request.content)
        operation = body["method"]
        if operation == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif operation == "notifications/initialized":
            return httpx.Response(204)
        elif operation == "resources/read":
            counts[operation] += 1
            if counts[operation] == 1:
                return httpx.Response(503)
            result = {"contents": [{"uri": body["params"]["uri"], "text": "safe"}]}
        else:
            counts[operation] += 1
            return httpx.Response(503)
        return httpx.Response(200, json={"id": body["id"], "result": result})

    transport = FoundryTransport(
        Credential(), httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    try:
        assert await transport.read_resource(binding, "skill://example/SKILL.md") == b"safe"
        with pytest.raises(RegistryError, match="503"):
            await transport.call_tool(binding, "create", {})
        assert counts == {"resources/read": 2, "tools/call": 1}
    finally:
        await transport.aclose()
