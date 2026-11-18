from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime

from copilot import CopilotClient

from adapters.core.foundry import FoundryTransport
from tools.capture_sources import ROOT, sha256, write_json
from tools.h4_preflight import DIRECTORY, SafetyStop, now
from tools.live_support import AsyncCliCredential
from tools.verify_live_sdk import run_mode


async def run():
    state = json.loads((DIRECTORY / "evidence.json").read_text())
    if state.get("safety_stop"):
        raise ValueError("A safety stop already exists; no further live controls permitted")
    before = state["general_before"]
    capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
    binding = capture["binding"]
    transport = FoundryTransport(AsyncCliCredential())
    after = {"versions": {}, "instruction_hashes": {}}
    try:
        base = f"{binding['project_endpoint']}/toolboxes/{binding['toolbox']}"
        raw, _ = await transport.request("GET", f"{base}?api-version=v1")
        after["default"] = json.loads(raw)
        if after["default"] != before["default"]:
            raise SafetyStop("General default changed during H4")
        for version in ("1", "2"):
            pin = {**binding, "version": version}
            raw, _ = await transport.request(
                "GET", f"{transport.base(pin)}?api-version=v1", binding=pin
            )
            after["versions"][version] = {"raw": raw.decode(), "sha256": sha256(raw)}
            if after["versions"][version] != before["versions"][version]:
                raise SafetyStop(f"General Toolbox version {version} manifest changed")
        for name in sorted(capture["skill_pins"]):
            raw = await transport.read_resource(binding, f"skill://{name}/SKILL.md")
            after["instruction_hashes"][name] = sha256(raw)
            if after["instruction_hashes"][name] != before["instruction_hashes"][name]:
                raise SafetyStop(f"General skill content changed: {name}")
    except SafetyStop as error:
        state.update(status="stopped", safety_stop=True, reason=str(error), stopped_at=now())
        write_json(DIRECTORY / "evidence.json", state)
        raise
    except Exception as error:
        state.update(status="blocked", reason=f"Pin control unavailable: {type(error).__name__}")
        write_json(DIRECTORY / "evidence.json", state)
        raise
    finally:
        after["http"] = transport.evidence
        write_json(DIRECTORY / "general-after.json", after)
        await transport.aclose()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = DIRECTORY / f"bridge-{stamp}"
    root.mkdir()
    client = CopilotClient(
        working_directory=str(root),
        base_directory=str(root / "sdk"),
        builtin_plugin_directories=[],
        env={key: value for key, value in os.environ.items() if key != "COPILOT_CLI_PATH"},
    )
    try:
        await client.start()
        catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
        bridge = await run_mode(client, "bridge", root, catalog, capture, "claude-haiku-4.5")
    finally:
        await client.stop()
    result = {
        "general_pins_byte_identical": True,
        "general_default_unchanged": True,
        "bridge": bridge,
        "scope": "Native dev-session factory-provider bridge, not a deployed worker run",
    }
    write_json(DIRECTORY / "controls.json", result)
    if not bridge["native_activation"] or not bridge["temporary_directory_removed"]:
        state.update(
            status="blocked",
            reason="Native bridge control failed; causality of a split not established",
        )
        write_json(DIRECTORY / "evidence.json", state)
        raise ValueError("Native bridge control failed")
    print(
        "Both general manifests and all ten instruction hashes unchanged; native bridge activated"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live general-pin and native bridge controls")
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())
