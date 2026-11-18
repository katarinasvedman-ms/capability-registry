import argparse
import asyncio
import json
import os
from datetime import UTC, datetime

from tools.capture_sources import ROOT, write_json
from tools.verify_live_sdk import CopilotClient, run_mode


async def main():
    root = (
        ROOT
        / ".artifacts"
        / "governance"
        / ("bridge-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    root.mkdir()
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    client = CopilotClient(
        working_directory=str(root),
        base_directory=str(root / "sdk"),
        builtin_plugin_directories=[],
    )
    try:
        await client.start()
        result = await run_mode(
            client,
            "bridge",
            root,
            json.loads((ROOT / "catalog" / "catalog.json").read_text()),
            json.loads((ROOT / "catalog" / "historical-binding.json").read_text()),
            "claude-haiku-4.5",
        )
        write_json(ROOT / ".artifacts" / "governance" / "bridge.json", result)
        if not result["native_activation"] or not result["temporary_directory_removed"]:
            raise RuntimeError("STOP: unchanged factory bridge activation failed")
        print("Native SDK bridge activation succeeds after secured mock migration")
    finally:
        await client.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(main())
