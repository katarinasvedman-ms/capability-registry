from __future__ import annotations

import argparse
import asyncio
import difflib
import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from adapters.core.foundry import FoundryTransport
from tools.capture_sources import ROOT, sha256, write_json
from tools.live_support import AsyncCliCredential

SOURCE = Path(r"C:\Users\kapeltol\src-new\factory-skills")
NAME = "dotnet-development"


async def run():
    capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
    binding = capture["binding"]
    transport = FoundryTransport(AsyncCliCredential())
    stage = ROOT / ".artifacts" / "dotnet-proposal"
    stage.mkdir(exist_ok=True)
    evidence = {
        "created_at": datetime.now(UTC).isoformat(),
        "binding": binding,
        "member_version": capture["skill_pins"][NAME],
        "published": False,
    }
    try:
        records = await transport.list_skills(binding)
        instructions = await transport.read_resource(binding, f"skill://{NAME}/SKILL.md")
        pinned = {"SKILL.md": instructions}
        if records[NAME]["type"] == "archive":
            content = await transport.read_resource(binding, f"skill://{NAME}.zip")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                pinned = {
                    item.filename: archive.read(item)
                    for item in archive.infolist()
                    if not item.is_dir()
                }
        package = SOURCE / "skills" / NAME
        allowed = {".md", ".py", ".json", ".yaml", ".yml", ".cs", ".csproj"}
        paths = sorted(path for path in package.rglob("*") if path.is_file())
        if any(path.is_symlink() or path.suffix not in allowed for path in paths):
            raise ValueError(
                "Current skill contains a symlink or unreviewed file type; capture refused"
            )
        current = {path.relative_to(package).as_posix(): path.read_bytes() for path in paths}
        if sum(map(len, current.values())) > 20 * 1024 * 1024:
            raise ValueError("Current source package exceeds capture limit")
        captured = {
            path.relative_to(
                ROOT / "baselines" / "factory-skills" / "skills" / NAME
            ).as_posix(): path.read_bytes()
            for path in (ROOT / "baselines" / "factory-skills" / "skills" / NAME).rglob("*")
            if path.is_file()
        }
        source_catalog = json.loads((SOURCE / "catalog.json").read_text())
        source_entry = next(item for item in source_catalog["skills"] if item["id"] == NAME)
        head = subprocess.run(
            ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        files = []
        diff = []
        for name in sorted(pinned.keys() | current.keys()):
            old, new = pinned.get(name, b""), current.get(name, b"")
            if old != new:
                diff.extend(
                    difflib.unified_diff(
                        old.decode("utf-8").splitlines(keepends=True),
                        new.decode("utf-8").splitlines(keepends=True),
                        fromfile=f"pinned-toolbox-v{binding['version']}-skill-v1/{name}",
                        tofile=f"current-source/{name}",
                    )
                )
            files.append(
                {
                    "path": name,
                    "pinned_sha256": sha256(old) if name in pinned else None,
                    "source_sha256": sha256(new) if name in current else None,
                    "changed": old != new,
                }
            )
        for name, data in current.items():
            path = stage / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        diff_path = ROOT / "docs" / "changes" / "dotnet-pinned-v1-to-current.diff"
        diff_path.parent.mkdir(exist_ok=True)
        diff_path.write_text("".join(diff), encoding="utf-8", newline="\n")
        front = yaml.safe_load(instructions.decode().split("---", 2)[1])
        evidence.update(
            source_head=head,
            source_semantic_version=source_entry["version"],
            pinned_frontmatter=front,
            files=files,
            current_matches_frozen_snapshot=current == captured,
            diff=str(diff_path.relative_to(ROOT)),
            http=transport.evidence,
            publication_plan={
                "skill": (
                    f"POST {binding['project_endpoint']}/skills/{NAME}/versions?api-version=v1"
                ),
                "verify": "GET the newly assigned skill version's content "
                "and compare all staged file hashes",
                "toolbox": (
                    f"POST {binding['project_endpoint']}/toolboxes/"
                    f"{binding['toolbox']}/versions?api-version=v1"
                ),
                "composition": "Branch the reviewed original default v1; change only "
                "the .NET reference to the newly assigned numeric pin",
                "default": "Never change the default; promotion requires "
                "the user's separate decision",
                "source_pattern": (
                    r"baselines\factory-skills\tools\publish_foundry.py publish_roles "
                    "delta/hash verification and normal skill/Toolbox version APIs"
                ),
            },
        )
        write_json(stage / "proposal.json", evidence)
        write_json(
            ROOT / "docs" / "changes" / "dotnet-proposal.json",
            {key: value for key, value in evidence.items() if key != "http"},
        )
        print(
            json.dumps(
                {
                    key: evidence[key]
                    for key in (
                        "published",
                        "source_semantic_version",
                        "current_matches_frozen_snapshot",
                        "diff",
                    )
                },
                indent=2,
            )
        )
    finally:
        await transport.aclose()


def main():
    parser = argparse.ArgumentParser(
        description="Read-only pinned/current .NET diff and publication proposal"
    )
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
