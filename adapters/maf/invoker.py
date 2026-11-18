from __future__ import annotations

from adapters.core import RegistryError


def integrate(invoker_type, registry, *, candidates, rationale, call_tool):
    """Override only MCP dispatch beneath the existing Invoker's policy envelope."""

    class CoreInvoker(invoker_type):
        async def dispatch(self, entry, inputs, approval_id):
            if entry.protocol != "mcp" or entry.id not in registry.catalog:
                return await super().dispatch(entry, inputs, approval_id)

            async def invoke(contract, binding):
                if (
                    contract["invocation"] != entry.invocation
                    or contract["inputs"] != entry.input_schema
                    or contract["permissions"] != entry.required_permissions
                    or contract["approval_required"] != entry.approval_required
                    or contract["version"] != entry.version
                    or contract["domain"] != entry.domain
                    or contract["operation"]
                    != {"action": "write", "handoff": "handoff"}.get(entry.type, "read")
                ):
                    raise RegistryError("Runtime policy differs from pinned contract")
                return await call_tool(
                    binding=binding,
                    tool=entry.invocation["tool"],
                    inputs=inputs,
                    identity=self.user,
                    approval_id=approval_id,
                    trace_id=self.trace_id,
                )

            return await registry.execute(candidates, entry.id, self.user["id"], rationale, invoke)

    return CoreInvoker
