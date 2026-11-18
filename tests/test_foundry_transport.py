import json
from types import SimpleNamespace

import httpx
import pytest

from adapters.core import RegistryError
from adapters.core.foundry import FoundryTransport


class Credential:
    closed = False

    async def get_token(self, scope):
        assert scope == "https://ai.azure.com/.default"
        return SimpleNamespace(token="synthetic-test-token")

    async def close(self):
        self.closed = True


async def test_pinned_mcp_initialization_and_multifile_resource(binding):
    operations = []

    async def handle(request):
        assert "/versions/1" in str(request.url)
        assert request.headers["Authorization"] == "Bearer synthetic-test-token"
        body = json.loads(request.content)
        operations.append(body["method"])
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif body["method"] == "notifications/initialized":
            return httpx.Response(204)
        elif body["method"] == "resources/read":
            result = {"contents": [{"uri": body["params"]["uri"], "text": "instructions"}]}
        else:
            result = {"tools": []}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    credential = Credential()
    transport = FoundryTransport(
        credential, httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    try:
        content = await transport.read_resource(binding, "skill://example/SKILL.md")
        assert content == b"instructions"
        assert operations == ["initialize", "notifications/initialized", "resources/read"]
        assert "synthetic-test-token" not in json.dumps(transport.evidence)
    finally:
        await transport.aclose()
    assert credential.closed


async def test_authorization_and_repeated_pagination_fail_closed(binding):
    async def deny(_request):
        return httpx.Response(403, text="do not expose response bodies")

    transport = FoundryTransport(
        Credential(), httpx.AsyncClient(transport=httpx.MockTransport(deny))
    )
    try:
        with pytest.raises(RegistryError, match="HTTP 403"):
            await transport.version(binding)
    finally:
        await transport.aclose()

    async def paginate(request):
        body = json.loads(request.content)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif body["method"] == "notifications/initialized":
            return httpx.Response(204)
        else:
            result = {"tools": [], "nextCursor": "again"}
        return httpx.Response(200, json={"id": body["id"], "result": result})

    transport = FoundryTransport(
        Credential(), httpx.AsyncClient(transport=httpx.MockTransport(paginate))
    )
    try:
        with pytest.raises(RegistryError, match="Repeated"):
            await transport.list_tools(binding)
    finally:
        await transport.aclose()
