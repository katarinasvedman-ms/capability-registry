from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from adapters.core.registry import validate_binding
from selection.technologies import parse_constraint
from tools.capture_sources import ROOT, sha256, write_json


def validate(
    catalog: list, schema: dict, bindings: dict | None = None, *, ready=False
) -> list[str]:
    errors = []
    if not isinstance(catalog, list):
        return ["Catalog must be an array"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    ids = set()
    for index, entry in enumerate(catalog):
        problems = sorted(validator.iter_errors(entry), key=lambda error: str(list(error.path)))
        for error in problems:
            errors.append(f"entry {index}: {list(error.path)}: {error.message}")
        if problems:
            continue
        name = entry["id"]
        if name in ids:
            errors.append(f"Duplicate capability: {name}")
        ids.add(name)
        if entry["inputs"] is not None:
            try:
                Draft202012Validator.check_schema(entry["inputs"])
            except Exception:
                errors.append(f"{name}: invalid input JSON Schema")
        if entry["type"] != "skill" and entry["invocation"]["adapter"] != entry["protocol"]:
            errors.append(f"{name}: invocation adapter must match protocol")
        for technology in entry["technologies"]:
            if isinstance(technology, dict) and "constraint" in technology:
                try:
                    parse_constraint(technology["constraint"])
                except ValueError as error:
                    errors.append(f"{name}: {error}")
        if ready and entry["owner"] == "unassigned":
            errors.append(f"{name}: owner unassigned")
        if ready and entry["status"] == "draft":
            errors.append(f"{name}: draft, not a fixture")
        binding = (bindings or {}).get(name)
        if binding:
            try:
                validate_binding(binding)
            except (ValueError, TypeError) as error:
                errors.append(f"{name}: {error}")
            if entry["risk_tier"] != binding.get("risk_tier"):
                errors.append(f"{name}: unknown or mismatched project trust tier")
            if entry["risk_tier"] == "privileged":
                tiers = json.loads((ROOT / "catalog" / "project-tiers.json").read_text())
                endpoint = binding.get("project_endpoint", "").casefold()
                if endpoint not in {p.casefold() for p in tiers["privileged"]} or endpoint in {
                    p.casefold() for p in tiers["general"]
                }:
                    errors.append(
                        f"{name}: privileged entry resolves to general or unverified project"
                    )
        elif (
            ready and entry["status"] == "approved" and entry["protocol"] in {"mcp", "mcp-resource"}
        ):
            errors.append(f"{name}: no pinned Toolbox binding")
    active, visited = set(), set()

    def visit(name):
        if name in active:
            errors.append(f"Cyclic capability dependency: {name}")
            return
        if name in visited:
            return
        visited.add(name)
        active.add(name)
        entry = next(
            (item for item in catalog if isinstance(item, dict) and item.get("id") == name), {}
        )
        for dependency in entry.get("requires", []):
            if dependency not in ids:
                errors.append(f"{name}: missing requires reference {dependency}")
            else:
                visit(dependency)
        active.remove(name)

    for name in sorted(ids):
        visit(name)
    return errors


async def probe(binding: dict, transport) -> dict:
    result = {"binding": binding, "verified": False}
    try:
        version = await transport.version(binding)
        skills = await transport.list_skills(binding)
        tools = await transport.list_tools(binding)
        result.update(
            verified=True,
            version=version,
            skill_index=skills,
            tools=tools,
        )
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["error"] = str(error) if isinstance(error, ValueError) else "Probe unavailable"
    return result


async def live(args) -> int:
    from azure.identity import AzureCliCredential

    class AsyncCliCredential:
        def __init__(self):
            self.credential = AzureCliCredential(process_timeout=120)

        async def get_token(self, scope):
            return await asyncio.to_thread(self.credential.get_token, scope)

        async def close(self):
            await asyncio.to_thread(self.credential.close)

    capture = json.loads(args.binding.read_text(encoding="utf-8")) if args.binding else None
    transport = FoundryTransport(AsyncCliCredential())
    try:
        if args.probe:
            result = await probe(capture["binding"], transport)
            if result["verified"]:
                actual = {item["name"]: item["version"] for item in result["version"]["skills"]}
                result["captured_pins_match"] = actual == capture["skill_pins"]
                result["verified"] = result["captured_pins_match"]
                if args.compare_snapshot:
                    result["instruction_comparison"] = {}
                    for name in sorted(capture["skill_pins"]):
                        content = await transport.read_resource(
                            capture["binding"], f"skill://{name}/SKILL.md"
                        )
                        local = (
                            ROOT / "baselines" / "factory-skills" / "skills" / name / "SKILL.md"
                        ).read_bytes()
                        result["instruction_comparison"][name] = {
                            "matches_snapshot": content == local,
                            "live_sha256": sha256(content),
                            "snapshot_sha256": sha256(local),
                        }
        else:
            catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
            bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
            registry = Registry(catalog, bindings, transport, lambda _: None)
            result = {"verified": True, "entries": {}}
            for entry in catalog:
                if entry["type"] not in {"skill", "tool"}:
                    continue
                try:
                    await registry.resolve(entry)
                    result["entries"][entry["id"]] = "verified"
                except Exception as error:
                    result["verified"] = False
                    result["entries"][entry["id"]] = (
                        str(error) if isinstance(error, ValueError) else type(error).__name__
                    )
        result["http"] = transport.evidence
        write_json(args.evidence, result)
        print(f"Live Toolbox probe verified={result['verified']}; evidence: {args.evidence}")
        return 0 if result["verified"] else 1
    finally:
        await transport.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate draft imports or approved registry readiness"
    )
    parser.add_argument("--catalog", type=Path, default=ROOT / "catalog" / "catalog.json")
    parser.add_argument(
        "--schema", type=Path, default=ROOT / "contracts" / "capability.schema.json"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument(
        "--probe", action="store_true", help="Inspect one historical Toolbox, not readiness"
    )
    parser.add_argument(
        "--ready", action="store_true", help="Require approvals and skill/tool pins"
    )
    parser.add_argument("--binding", type=Path, help="Explicit nonsecret Foundry Toolbox binding")
    parser.add_argument("--bindings", type=Path, help="Capability-ID to pinned binding map")
    parser.add_argument("--compare-snapshot", action="store_true")
    parser.add_argument("--evidence", type=Path, default=ROOT / ".artifacts" / "foundry-probe.json")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    bindings = json.loads(args.bindings.read_text(encoding="utf-8")) if args.bindings else {}
    if args.live and not (args.ready and args.bindings):
        parser.error("--live requires --ready and --bindings for every skill/tool entry")
    errors = validate(catalog, schema, bindings, ready=args.ready)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    if args.live or args.probe:
        if args.probe and not args.binding:
            parser.error("--probe requires a captured --binding")
        raise SystemExit(asyncio.run(live(args)))
    print(f"{len(catalog)} entries structurally valid ({'ready' if args.ready else 'schema only'})")


if __name__ == "__main__":
    main()
