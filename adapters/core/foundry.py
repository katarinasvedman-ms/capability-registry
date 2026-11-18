from __future__ import annotations

import asyncio
import base64
import json
import re
import time

import httpx

from .registry import RegistryError, validate_binding

MAX_BYTES = 20 * 1024 * 1024


class FoundryTransport:
    """Bounded, version-pinned MCP transport. Credentials and HTTP clients are injected."""

    def __init__(self, credential, client=None):
        self.credential = credential
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self.sessions = {}
        self.sequence = 0
        self.evidence = []
        self.token = None

    @staticmethod
    def base(binding):
        validate_binding(binding)
        return (
            f"{binding['project_endpoint']}/toolboxes/"
            f"{binding['toolbox']}/versions/{binding['version']}"
        )

    async def request(self, method, url, *, body=None, headers=None, binding=None):
        operation = body.get("method") if body else "toolbox.version"
        safe = method == "GET" or operation in {"resources/read", "resources/list", "tools/list"}
        for attempt in range(3 if safe else 1):
            before = len(self.evidence)
            started = time.perf_counter()
            try:
                return await self._request_once(
                    method, url, body=body, headers=headers, binding=binding
                )
            except (RegistryError, httpx.TransportError) as error:
                transient = isinstance(error, httpx.TransportError) or str(error) in {
                    "Foundry HTTP 429",
                    "Foundry HTTP 500",
                    "Foundry HTTP 502",
                    "Foundry HTTP 503",
                    "Foundry HTTP 504",
                }
                if not safe or not transient or attempt == 2:
                    raise
            finally:
                if len(self.evidence) == before:
                    self.evidence.append(
                        {
                            "method": method,
                            "url": url,
                            "operation": operation,
                            "status": None,
                            "bytes": 0,
                        }
                    )
                self.evidence[-1].update(
                    attempt=attempt + 1,
                    seconds=time.perf_counter() - started,
                    toolbox=(binding or {}).get("toolbox"),
                    toolbox_version=(binding or {}).get("version"),
                )
            await asyncio.sleep(0.25 * 2**attempt)

    async def _request_once(self, method, url, *, body=None, headers=None, binding=None):
        if self.token is None or getattr(self.token, "expires_on", 0) <= time.time() + 300:
            self.token = await self.credential.get_token("https://ai.azure.com/.default")
        expected = (binding or {}).get("identity")
        if expected:
            try:
                payload = self.token.token.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                matches = (
                    claims.get("tid") == expected["tenant_id"]
                    and claims.get("oid") == expected["principal_id"]
                    and (claims.get("appid") or claims.get("azp")) == expected["client_id"]
                    and claims.get("aud") == "https://ai.azure.com"
                )
            except (ValueError, KeyError, IndexError):
                matches = False
            if not matches:
                raise RegistryError("Credential does not match the pinned role identity")
        request_headers = {
            "Authorization": f"Bearer {self.token.token}",
            "Accept": "application/json, text/event-stream",
            "Foundry-Features": "Skills=V1Preview",
            **(headers or {}),
        }
        async with self.client.stream(method, url, json=body, headers=request_headers) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise RegistryError("Foundry response exceeds size limit")
            self.evidence.append(
                {
                    "method": method,
                    "url": url,
                    "operation": body.get("method") if body else "toolbox.version",
                    "status": response.status_code,
                    "bytes": len(data),
                    "request": body,
                    "response": data.decode("utf-8", errors="replace"),
                }
            )
            if not response.is_success:
                raise RegistryError(f"Foundry HTTP {response.status_code}")
            return bytes(data), dict(response.headers)

    async def version(self, binding):
        data, _ = await self.request("GET", f"{self.base(binding)}?api-version=v1", binding=binding)
        result = json.loads(data)
        pins = result.get("skills", [])
        names = [item.get("name") for item in pins]
        if len(set(names)) != len(names):
            raise RegistryError("Duplicate skill pins")
        for item in pins:
            validate_binding({**binding, "member_version": item.get("version")})
        return result

    async def rpc(self, binding, method, params=None, *, notification=False):
        base = self.base(binding)
        if method != "initialize" and base not in self.sessions:
            initialized = await self.rpc(
                binding,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "capability-registry-testbed", "version": "0.1.0"},
                },
            )
            self.sessions[base]["MCP-Protocol-Version"] = initialized["protocolVersion"]
            await self.rpc(binding, "notifications/initialized", notification=True)
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = self.sequence
        if params is not None:
            body["params"] = params
        data, headers = await self.request(
            "POST",
            f"{base}/mcp?api-version=v1",
            body=body,
            headers=self.sessions.get(base, {}),
            binding=binding,
        )
        if method == "initialize":
            self.sessions[base] = {}
        if "mcp-session-id" in headers:
            self.sessions[base]["Mcp-Session-Id"] = headers["mcp-session-id"]
        if notification:
            return {}
        if "text/event-stream" in headers.get("content-type", ""):
            messages = [
                json.loads(line[5:].strip())
                for line in data.decode().splitlines()
                if line.startswith("data:")
            ]
            message = next((item for item in messages if item.get("id") == body["id"]), {})
        else:
            message = json.loads(data)
        if message.get("id") != body["id"] or "result" not in message or "error" in message:
            raise RegistryError(f"Foundry MCP {method} failed")
        return message["result"]

    async def list_tools(self, binding):
        tools, seen, cursor = {}, set(), None
        for _ in range(100):
            page = await self.rpc(binding, "tools/list", {"cursor": cursor} if cursor else {})
            for tool in page.get("tools", []):
                if tool["name"] in tools:
                    raise RegistryError("Duplicate MCP tool")
                tools[tool["name"]] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                raise RegistryError("Repeated MCP cursor")
            seen.add(cursor)
        raise RegistryError("MCP pagination limit exceeded")

    async def read_resource(self, binding, uri):
        result = await self.rpc(binding, "resources/read", {"uri": uri})
        contents = result.get("contents", [])
        if len(contents) != 1 or contents[0].get("uri") != uri:
            raise RegistryError("Ambiguous MCP resource")
        item = contents[0]
        if isinstance(item.get("text"), str) and "blob" not in item:
            return item["text"].encode()
        if isinstance(item.get("blob"), str) and "text" not in item:
            return base64.b64decode(item["blob"], validate=True)
        raise RegistryError("Unsupported MCP resource encoding")

    async def list_skills(self, binding):
        manifest = await self.version(binding)
        versions = {item["name"]: item["version"] for item in manifest.get("skills", [])}
        resources, seen, cursor = {}, set(), None
        for _ in range(100):
            page = await self.rpc(binding, "resources/list", {"cursor": cursor} if cursor else {})
            for resource in page.get("resources", []):
                uri = resource.get("uri")
                if not isinstance(uri, str) or uri in resources:
                    raise RegistryError("Invalid or duplicate MCP resource")
                resources[uri] = resource
                if len(resources) > 2048:
                    raise RegistryError("MCP resource count exceeds limit")
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise RegistryError("Repeated MCP cursor")
            seen.add(cursor)
        else:
            raise RegistryError("MCP resource pagination limit exceeded")
        if "skill://index.json" not in resources:
            raise RegistryError("Skill index was not advertised")
        data = await self.read_resource(binding, "skill://index.json")
        skills = json.loads(data)["skills"]
        result = {}
        for skill in skills:
            name, kind, description = (
                skill.get("name", ""),
                skill.get("type"),
                skill.get("description"),
            )
            uri = f"skill://{name}.zip" if kind == "archive" else f"skill://{name}/SKILL.md"
            if (
                not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or len(name) > 64
                or name in result
                or name not in versions
                or not isinstance(description, str)
                or not 1 <= len(description) <= 1024
                or kind not in {"skill-md", "archive"}
                or skill.get("url") != uri
                or uri not in resources
                or f"skill://{name}/SKILL.md" not in resources
            ):
                raise RegistryError("Invalid pinned skill discovery metadata")
            result[name] = {
                "name": name,
                "description": description,
                "type": kind,
                "foundry_version": versions[name],
            }
        if set(result) != set(versions) or len(result) > 1024:
            raise RegistryError("Skill index and manifest pins disagree")
        return result

    async def call_tool(self, binding, name, inputs, *, context=None):
        params = {"name": name, "arguments": inputs}
        if context is not None:
            params["_meta"] = {"capability-registry/context": context}
        result = await self.rpc(binding, "tools/call", params)
        if result.get("isError"):
            raise RegistryError("MCP tool reported an error")
        return result

    async def aclose(self):
        try:
            await self.client.aclose()
        finally:
            await self.credential.close()
