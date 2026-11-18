import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "baselines" / "ai-factory" / "src"))


@pytest.fixture
def imported_catalog():
    return json.loads((ROOT / "catalog" / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture
def reviewed_catalog(imported_catalog):
    return copy.deepcopy(imported_catalog)


@pytest.fixture
def binding():
    return {
        "project_endpoint": "https://test.services.ai.azure.com/api/projects/general",
        "toolbox": "testbox",
        "version": "1",
        "risk_tier": "general",
        "member_version": "1",
    }


class FakeTransport:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []
        self.skill_versions = {entry["id"]: "1" for entry in entries if entry["type"] == "skill"}

    async def version(self, binding):
        self.calls.append(("version", binding["version"]))
        return {
            "skills": [
                {"name": name, "version": version} for name, version in self.skill_versions.items()
            ]
        }

    async def list_tools(self, binding):
        return {
            entry["invocation"]["tool"]: {
                "name": entry["invocation"]["tool"],
                "inputSchema": entry["inputs"],
            }
            for entry in self.entries
            if entry["type"] == "tool"
        }


@pytest.fixture
def transport(reviewed_catalog):
    return FakeTransport(reviewed_catalog)
