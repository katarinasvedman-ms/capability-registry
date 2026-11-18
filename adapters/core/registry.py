from __future__ import annotations

import inspect
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


class RegistryError(ValueError):
    pass


def same_input_schema(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    dialect = "https://json-schema.org/draft/2020-12/schema"
    return {"$schema": dialect, **left} == {"$schema": dialect, **right}


class Transport(Protocol):
    async def version(self, binding: dict) -> dict: ...
    async def list_skills(self, binding: dict) -> dict: ...
    async def list_tools(self, binding: dict) -> dict: ...
    async def read_resource(self, binding: dict, uri: str) -> bytes: ...
    async def call_tool(self, binding: dict, name: str, inputs: dict): ...


def validate_binding(binding: dict) -> None:
    endpoint = urlsplit(binding.get("project_endpoint", ""))
    if (
        endpoint.scheme != "https"
        or endpoint.port not in (443, None)
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or not re.fullmatch(r"[a-zA-Z0-9-]+\.services\.ai\.azure\.com", endpoint.hostname or "")
        or not re.fullmatch(r"/api/projects/[a-zA-Z0-9_-]+", endpoint.path)
    ):
        raise RegistryError("Expected an Azure public-cloud Foundry project endpoint")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", binding.get("toolbox", "")):
        raise RegistryError("Invalid Toolbox name")
    if not isinstance(binding.get("version"), str) or not re.fullmatch(
        r"[0-9]+", binding["version"]
    ):
        raise RegistryError("Expected an explicit numeric Toolbox version")
    if binding.get("risk_tier") not in {"general", "privileged"}:
        raise RegistryError("Binding requires a verified project trust tier")
    if "member_version" in binding and (
        not isinstance(binding["member_version"], str)
        or not re.fullmatch(r"[0-9]+", binding["member_version"])
    ):
        raise RegistryError("Expected an explicit numeric member version")
    if "tool_name" in binding and (
        not isinstance(binding["tool_name"], str)
        or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,256}", binding["tool_name"])
    ):
        raise RegistryError("Invalid pinned MCP tool name")


@dataclass
class Registry:
    entries: list[dict]
    bindings: dict[str, dict]
    transport: Transport
    audit: object

    def __post_init__(self):
        self.catalog = {entry["id"]: entry for entry in self.entries}
        if len(self.catalog) != len(self.entries):
            raise RegistryError("Duplicate capability IDs")
        for binding in self.bindings.values():
            validate_binding(binding)

    def list(self) -> list[dict]:
        return [self.catalog[key] for key in sorted(self.catalog)]

    def lookup(self, capability_id: str) -> dict:
        """Resolve snapshot metadata for audit, including non-Toolbox historical routes."""
        if capability_id not in self.catalog:
            raise RegistryError("Unknown historical capability ID")
        return deepcopy(self.catalog[capability_id])

    def filter(self, candidate_ids: list[str]) -> list[dict]:
        if (
            len(candidate_ids) != len(set(candidate_ids))
            or not set(candidate_ids) <= self.catalog.keys()
        ):
            raise RegistryError("Selection contains duplicate or unknown capability IDs")
        return [self.catalog[key] for key in candidate_ids]

    async def resolve(self, entry: dict) -> dict:
        name = entry["id"]
        if self.catalog.get(name) != entry:
            raise RegistryError("Entry differs from the registry snapshot")
        binding = self.bindings.get(name)
        if not binding:
            raise RegistryError(f"No pinned registry binding for {name}")
        if entry["risk_tier"] != binding["risk_tier"]:
            raise RegistryError(f"Unknown or mismatched project trust tier: {name}")
        version = await self.transport.version(binding)
        if entry["type"] == "skill":
            pins = {item["name"]: item.get("version") for item in version.get("skills", [])}
            if pins.get(name) != binding.get("member_version") or not pins.get(name):
                raise RegistryError(f"Skill pin mismatch: {name}")
        else:
            tools = await self.transport.list_tools(binding)
            tool = binding.get("tool_name", entry["invocation"].get("tool"))
            if tool not in tools:
                raise RegistryError(f"Pinned Toolbox does not advertise tool: {name}")
            remote_approval = tools[tool].get("_meta", {}).get("approval_required", False)
            for server in version.get("tools", []):
                label = server.get("server_label", server.get("name", ""))
                if label and tool.startswith(label + "___"):
                    remote_approval |= server.get("require_approval") not in (None, "never")
            if remote_approval and not entry["approval_required"]:
                raise RegistryError(f"Contract weakens pinned tool approval floor: {name}")
            if not same_input_schema(tools[tool].get("inputSchema"), entry["inputs"]):
                raise RegistryError(f"Pinned tool schema mismatch: {name}")
        return binding

    async def execute(self, candidate_ids, capability_id, identity, rationale, invoke):
        outcome = "failed"
        entry = self.catalog.get(capability_id)
        binding = self.bindings.get(capability_id)
        pin_verified = False
        try:
            entries = {entry["id"]: entry for entry in self.filter(candidate_ids)}
            if capability_id not in entries:
                raise RegistryError("Capability was not selected")
            entry = entries[capability_id]
            if entry["status"] != "approved":
                raise RegistryError("Capability is not approved")
            if entry["lifecycle"] != "active":
                raise RegistryError("Capability is not operationally active")
            binding = await self.resolve(entry)
            pin_verified = True
            result = invoke(entry, binding)
            if inspect.isawaitable(result):
                result = await result
            outcome = "completed"
            return result
        finally:
            self.audit(
                {
                    "entry_id": capability_id,
                    "version": entry["version"] if entry else None,
                    "approval_scope": entry.get("approval_scope") if entry else None,
                    "kind": entry.get("kind") if entry else None,
                    "lifecycle": entry.get("lifecycle") if entry else None,
                    "registry_pin": {
                        key: binding[key]
                        for key in (
                            "project_endpoint",
                            "toolbox",
                            "version",
                            "member_version",
                            "risk_tier",
                            "tool_name",
                        )
                        if key in binding
                    }
                    if binding
                    else None,
                    "pin_verified": pin_verified,
                    "identity": identity,
                    "selection_rationale": rationale,
                    "outcome": outcome,
                }
            )
