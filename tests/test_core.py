import asyncio

import pytest

from adapters.core import Registry, RegistryError
from adapters.core.registry import same_input_schema


async def test_common_pinning_filtering_and_failure_audit(reviewed_catalog, binding, transport):
    audit = []
    name = "dotnet-development"
    registry = Registry(reviewed_catalog, {name: binding}, transport, audit.append)
    assert [entry["id"] for entry in registry.filter([name])] == [name]
    result = await registry.execute(
        [name], name, "synthetic-coder", "metadata selected", lambda entry, pin: pin["version"]
    )
    assert result == "1"
    transport.skill_versions[name] = "2"
    with pytest.raises(RegistryError, match="pin mismatch"):
        await registry.execute([name], name, "synthetic-coder", "unchanged", lambda *_: "wrong")
    assert [row["outcome"] for row in audit] == ["completed", "failed"]
    assert all(row["identity"] == "synthetic-coder" for row in audit)
    assert audit[0]["version"] == registry.catalog[name]["version"]
    assert audit[0]["registry_pin"]["version"] == "1"
    assert audit[0]["pin_verified"] is True
    assert audit[1]["pin_verified"] is False


async def test_unselected_and_draft_invocation_is_denied(
    imported_catalog, reviewed_catalog, binding, transport
):
    name = "dotnet-development"
    audit = []
    next(entry for entry in imported_catalog if entry["id"] == name)["status"] = "draft"
    registry = Registry(imported_catalog, {name: binding}, transport, audit.append)
    with pytest.raises(RegistryError, match="not approved"):
        await registry.execute([name], name, "coder", "", lambda *_: None)
    registry = Registry(reviewed_catalog, {name: binding}, transport, audit.append)
    with pytest.raises(RegistryError, match="not selected"):
        await registry.execute([], name, "coder", "", lambda *_: None)
    assert len(audit) == 2
    assert transport.calls == []


async def test_cancellation_is_audited(reviewed_catalog, binding, transport):
    audit = []
    registry = Registry(reviewed_catalog, {"dotnet-development": binding}, transport, audit.append)

    async def cancel(*_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await registry.execute(
            ["dotnet-development"], "dotnet-development", "coder", "test", cancel
        )
    assert audit[-1]["outcome"] == "failed"


def test_foundry_default_dialect_annotation_does_not_weaken_schema_pinning():
    schema = {"type": "object", "additionalProperties": False}
    annotated = {**schema, "$schema": "https://json-schema.org/draft/2020-12/schema"}
    assert same_input_schema(schema, annotated)
    assert not same_input_schema(schema, {**annotated, "additionalProperties": True})
    assert not same_input_schema(
        schema, {**annotated, "$schema": "http://json-schema.org/draft-07/schema#"}
    )


@pytest.mark.parametrize("lifecycle", ["deprecated", "retired"])
async def test_historical_pin_resolves_without_permitting_new_execution(
    reviewed_catalog, binding, transport, lifecycle
):
    entry = next(r for r in reviewed_catalog if r["id"] == "dotnet-development")
    entry["lifecycle"] = lifecycle
    audit = []
    registry = Registry(reviewed_catalog, {entry["id"]: binding}, transport, audit.append)
    assert await registry.resolve(entry) == binding
    called = []
    with pytest.raises(RegistryError, match="not operationally active"):
        await registry.execute(
            [entry["id"]], entry["id"], "coder", "old snapshot", lambda *_: called.append(True)
        )
    assert not called
    assert audit[-1]["lifecycle"] == lifecycle
    assert audit[-1]["outcome"] == "failed"


@pytest.mark.parametrize("lifecycle", ["deprecated", "retired"])
def test_non_toolbox_historical_routes_remain_resolvable(reviewed_catalog, transport, lifecycle):
    entry = next(r for r in reviewed_catalog if r["id"] == "operations.open_expenses")
    entry["lifecycle"] = lifecycle
    registry = Registry([entry], {}, transport, lambda _: None)
    historical = registry.lookup(entry["id"])
    assert historical["lifecycle"] == lifecycle
    assert historical["invocation"] == entry["invocation"]
    historical["invocation"]["tool"] = "changed"
    assert registry.lookup(entry["id"])["invocation"] == entry["invocation"]
    assert not transport.calls


@pytest.mark.parametrize("source", ["mock", "toolbox"])
async def test_contract_cannot_weaken_remote_approval_floor(
    reviewed_catalog, binding, transport, source
):
    entry = next(r for r in reviewed_catalog if r["id"] == "mock.get_build_status")

    class ApprovalTransport:
        async def version(self, _binding):
            return {
                "tools": [{"server_label": "server", "require_approval": "always"}]
                if source == "toolbox"
                else []
            }

        async def list_tools(self, _binding):
            return {
                "server___get_build_status": {
                    "inputSchema": entry["inputs"],
                    "_meta": {"approval_required": source == "mock"},
                }
            }

    pin = {**binding, "tool_name": "server___get_build_status"}
    registry = Registry([entry], {entry["id"]: pin}, ApprovalTransport(), lambda _: None)
    with pytest.raises(RegistryError, match="approval floor"):
        await registry.resolve(entry)
    entry["approval_required"] = True
    assert await registry.resolve(entry) == pin
