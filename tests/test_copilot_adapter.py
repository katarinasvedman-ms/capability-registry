import io
import zipfile

import pytest
import yaml
from ai_factory.provider import ToolboxSkillProvider

from adapters.copilot_sdk import integrate
from adapters.core import Registry, RegistryError
from tools.capture_sources import ROOT


class SourceClient:
    def __init__(self, _binding):
        self.versions = None
        self.root = ROOT / "baselines" / "factory-skills" / "skills"
        self.records = {}
        self.closed = False
        for directory in sorted(self.root.iterdir()):
            front = yaml.safe_load(
                (directory / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
            )
            self.records[directory.name] = {
                "name": front["name"],
                "description": front["description"],
                "type": "archive",
                "foundry_version": "1",
            }

    async def catalog(self):
        self.versions = {name: "1" for name in self.records}
        return self.records

    async def read(self, uri):
        name = uri.removeprefix("skill://").split("/")[0].removesuffix(".zip")
        if not uri.endswith(".zip"):
            return (self.root / name / "SKILL.md").read_bytes()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path in (self.root / name).rglob("*"):
                if path.is_file():
                    archive.writestr(
                        path.relative_to(self.root / name).as_posix(), path.read_bytes()
                    )
        return buffer.getvalue()

    async def aclose(self):
        self.closed = True


def create_provider(reviewed_catalog, binding, transport):
    audit = []
    names = ["dotnet-development", "test-strategy"]
    client = SourceClient(binding)
    transport.evidence = []
    transport.list_skills = lambda _: client.catalog()
    transport.read_resource = lambda _, uri: client.read(uri)
    transport.aclose = client.aclose
    registry = Registry(
        reviewed_catalog, {name: binding for name in names}, transport, audit.append
    )
    cls = integrate(
        ToolboxSkillProvider, registry, identity="synthetic-coder", rationale="metadata baseline"
    )
    manifest = {
        "assignments": {"coder": names},
        "skills": client.records,
        "distribution": {
            **binding,
            "skill_versions": {name: "1" for name in client.records},
        },
    }
    return cls(manifest, "coder"), audit, client


async def test_provider_callbacks_materialize_real_multifile_snapshot_and_cleanup(
    reviewed_catalog, binding, transport
):
    provider, audit, client = create_provider(reviewed_catalog, binding, transport)
    root = provider.root
    try:
        descriptors = await provider.list_skills()
        assert [descriptor.name for descriptor in descriptors] == [
            "dotnet-development",
            "test-strategy",
        ]
        assert not (root / "dotnet-development" / "SKILL.md").exists()
        instructions = await provider.read_skill("dotnet-development")
        assert instructions == (
            ROOT / "baselines" / "factory-skills" / "skills" / "dotnet-development" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert (root / "dotnet-development" / "references" / "validation.md").is_file()
        assert len(audit) == 3
        assert all(row["outcome"] == "completed" for row in audit)
    finally:
        await provider.aclose()
    assert not root.exists()
    assert client.closed


async def test_unselected_skill_fails_without_bridge_fallback(reviewed_catalog, binding, transport):
    provider, audit, _ = create_provider(reviewed_catalog, binding, transport)
    try:
        await provider.list_skills()
        with pytest.raises(RegistryError, match="not selected"):
            await provider.read_skill("java-development")
        assert "no bridge fallback" in provider.failure
        assert audit[-1]["outcome"] == "failed"
    finally:
        await provider.aclose()
