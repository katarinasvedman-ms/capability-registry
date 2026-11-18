from __future__ import annotations

import copy
import json
import subprocess
import sys

from tools.capture_sources import ROOT, write_json


def main():
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    bindings = json.loads((ROOT / "catalog" / "bindings.json").read_text())
    entry = copy.deepcopy(next(row for row in catalog if row["id"] == "mock.deploy_release"))
    entry["risk_tier"] = "privileged"
    binding = {
        **bindings[entry["id"]],
        "project_endpoint": json.loads((ROOT / "catalog" / "project-tiers.json").read_text())[
            "general"
        ][0],
        "risk_tier": "privileged",
    }
    directory = ROOT / ".artifacts" / "h4"
    catalog_path, bindings_path = (
        directory / "negative-catalog.json",
        directory / "negative-bindings.json",
    )
    write_json(catalog_path, [entry])
    write_json(bindings_path, {entry["id"]: binding})
    command = [
        sys.executable,
        "-m",
        "tools.validate_catalog",
        "--offline",
        "--catalog",
        str(catalog_path),
        "--bindings",
        str(bindings_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    evidence = {
        "command": command,
        "expected_exit_code": 1,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "rejected": result.returncode == 1
        and "privileged entry resolves to general" in result.stdout,
    }
    write_json(directory / "ci-negative.json", evidence)
    if not evidence["rejected"]:
        raise ValueError("Privileged-to-general negative check did not fail as designed")
    print(result.stdout.strip())
    print("Negative CLI case rejected with exit code 1, as required")


if __name__ == "__main__":
    main()
