from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
from datetime import UTC, datetime

from adapters.copilot_sdk import integrate
from adapters.core import Registry
from adapters.core.foundry import FoundryTransport
from selection import select
from tools.capture_sources import ROOT, write_json
from tools.live_support import AsyncCliCredential

sys.path.insert(0, str(ROOT / "baselines" / "ai-factory" / "src"))
from ai_factory.provider import ToolboxSkillProvider  # noqa: E402
from copilot import CopilotClient  # noqa: E402
from copilot.generated.rpc import PermissionDecisionDeniedByRules  # noqa: E402


async def run_mode(client, mode, root, catalog, capture, model):
    directory = root / mode
    directory.mkdir()
    binding = capture["binding"]
    transport = FoundryTransport(AsyncCliCredential())
    audit = []
    records = await transport.list_skills(binding)
    selected = select(
        {
            "role": "coder",
            "technologies": ["unknown-stack", "copilot-sdk-python-1"],
            "tasks": ["implementation", "testing"],
            "domains": ["platform"],
            "operations": ["read"],
        },
        catalog,
        {"allowed_skills": list(records)},
    )
    bindings = {
        name: {**binding, "member_version": capture["skill_pins"][name]}
        for name in selected.candidate_ids
    }
    registry = Registry(catalog, bindings, transport, audit.append)
    provider_class = integrate(
        ToolboxSkillProvider,
        registry,
        identity="operator-authenticated-sdk-testbed",
        rationale=selected.rationale,
    )
    provider = provider_class(
        {
            "assignments": {"coder": selected.candidate_ids},
            "skills": records,
            "distribution": {**binding, "skill_versions": capture["skill_pins"]},
        },
        "coder",
    )
    events = []
    session = None
    evidence = {
        "mode": mode,
        "model": model,
        "selected": selected.candidate_ids,
        "native_activation": False,
    }

    def on_event(notification):
        record = notification.to_dict()
        events.append(record)
        with (directory / "native-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    try:
        if mode == "bridge":
            await provider.list_skills()
            for name in selected.candidate_ids:
                await provider.read_skill(name)
        session = await client.create_session(
            model=model,
            name=f"registry-testbed-{mode}",
            working_directory=str(directory),
            config_directory=str(directory),
            tools=[],
            available_tools=["skill"],
            on_permission_request=lambda *_: PermissionDecisionDeniedByRules(rules=[]),
            skill_directories=[str(provider.root)] if mode == "bridge" else [],
            skill_provider=provider if mode == "provider" else None,
            enable_skills=True,
            included_builtin_skills=[],
            enable_config_discovery=False,
            skip_custom_instructions=True,
            enable_on_demand_instruction_discovery=False,
            enable_file_hooks=False,
            enable_host_git_operations=False,
            enable_session_store=False,
            agent_directories=[],
            plugin_directories=[],
            hook_directories=[],
            instruction_directories=[],
            included_builtin_agents=[],
            custom_agents=[],
            mcp_servers={},
            interactive_user=False,
            on_event=on_event,
            system_message={
                "mode": "replace",
                "content": (
                    "This is a synthetic native skill activation test. Before responding, "
                    "load test-strategy with the native skill tool. No filesystem, shell, "
                    "network, write, or deployment tools are authorized. Do not claim tests ran."
                ),
            },
        )
        native_catalog = await session.rpc.skills.list()
        write_json(directory / "native-skill-catalog.json", native_catalog.to_dict())
        if {skill.name for skill in native_catalog.skills} != set(selected.candidate_ids):
            raise ValueError("Native SDK catalog differs from the core selection")
        await session.send_and_wait(
            "Use test-strategy. Give two focused regression tests for a synthetic integer "
            "calculator add(a,b), with expected results. Do not execute anything.",
            timeout=180,
        )
        provider.raise_if_failed()
        activations = [
            record for record in events if record["type"] in {"skill.invoked", "skill.invoked.ref"}
        ]
        if not activations or any(
            record["data"]["name"] not in selected.candidate_ids for record in activations
        ):
            raise ValueError("No matching native SDK skill activation event")
        evidence.update(native_activation=True, activations=activations)
    except Exception as error:
        evidence.update(error_type=type(error).__name__, error=str(error))
    finally:
        if session:
            await session.disconnect()
        evidence.update(
            core_audit=audit,
            http=transport.evidence,
            materialized=sorted(provider.loaded),
            provider_failure=provider.failure,
            native_event_count=len(events),
        )
        temporary_root = provider.root
        await provider.aclose()
        evidence["temporary_directory_removed"] = not temporary_root.exists()
        write_json(directory / "evidence.json", evidence)
    return evidence


async def run(model):
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = ROOT / ".artifacts" / "sdk-live" / stamp
    root.mkdir(parents=True)
    client = CopilotClient(
        working_directory=str(root),
        base_directory=str(root / "sdk"),
        builtin_plugin_directories=[],
        env={key: value for key, value in os.environ.items() if key != "COPILOT_CLI_PATH"},
    )
    evidence = {
        "sdk_version": importlib.metadata.version("github-copilot-sdk"),
        "attempt_directory": str(root.relative_to(ROOT)),
        "modes": [],
    }
    try:
        await client.start()
        status = await client.get_status()
        evidence["cli_version"] = status.version
        auth = await client.get_auth_status()
        if not auth.isAuthenticated:
            raise ValueError("Native SDK is not authenticated")
        models = await client.list_models()
        evidence["available_models"] = [item.id for item in models]
        if model not in evidence["available_models"]:
            raise ValueError(f"Requested smoke-test model unavailable: {model}")
        for mode in ("bridge", "provider"):
            result = await run_mode(client, mode, root, catalog, capture, model)
            evidence["modes"].append(result)
            write_json(root / "summary.json", evidence)
    except Exception as error:
        evidence.update(error_type=type(error).__name__, error=str(error))
    finally:
        await client.stop()
        evidence["supported"] = len(evidence["modes"]) == 2 and all(
            item["native_activation"] for item in evidence["modes"]
        )
        write_json(root / "summary.json", evidence)
        write_json(ROOT / ".artifacts" / "sdk-live.json", evidence)
    print(
        json.dumps(
            {
                key: value
                for key, value in evidence.items()
                if key not in {"available_models", "modes"}
            },
            indent=2,
        )
    )
    return evidence["supported"]


def main():
    parser = argparse.ArgumentParser(
        description="Live native SDK activation: bridge, then provider"
    )
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--model", default="claude-haiku-4.5")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.model)) else 1)


if __name__ == "__main__":
    main()
