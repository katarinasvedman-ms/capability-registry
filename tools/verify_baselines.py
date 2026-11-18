from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from importlib.metadata import version

from tools.capture_sources import ROOT, verify, write_json


def main():
    verify(ROOT / "baselines")
    artifacts = ROOT / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(ROOT / "baselines" / "ai-factory" / "src"),
    }
    tests = ROOT / "baselines" / "ai-factory" / "tests"
    junit = artifacts / "factory-baseline.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-c",
            str(tests.parent / "pyproject.toml"),
            *(
                str(tests / name)
                for name in ("test_provider.py", "test_resolvers.py", "test_execution.py")
            ),
            "-o",
            f"cache_dir={artifacts / 'factory-pytest-cache'}",
            f"--junitxml={junit}",
        ],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (artifacts / "factory-baseline.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    suite = ET.parse(junit).getroot().find("testsuite") if junit.exists() else None
    summary = {
        "exit_code": result.returncode,
        "sdk_version": version("github-copilot-sdk"),
        "suite": dict(suite.attrib) if suite is not None else None,
        "source_heads": {
            key: value["head"]
            for key, value in json.loads((ROOT / "baselines" / "provenance.json").read_text())[
                "sources"
            ].items()
        },
    }
    write_json(artifacts / "factory-baseline.json", summary)
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr.strip())
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
