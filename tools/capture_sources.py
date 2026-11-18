from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "factory-skills": (
        "catalog.json",
        "catalog.schema.json",
        "evaluations.json",
        "pyproject.toml",
        "requirements.lock",
        "README.md",
        "skills/**/*.md",
        "tools/*.py",
        "tests/*.py",
    ),
    "ai-factory": (
        "pyproject.toml",
        "requirements.lock",
        "README.md",
        "role-isolation-report.md",
        "src/ai_factory/*.py",
        "tests/*.py",
        "examples/*.json",
        "tests/fixtures/**/*",
    ),
    "frontdoor": (
        "pyproject.toml",
        "requirements.txt",
        "requirements.lock.txt",
        "ARCHITECTURE.md",
        "backend/*.py",
        "backend/catalog.json",
        "services/*.py",
        "tests/*.py",
    ),
}
EXTENSIONS = {
    ".py",
    ".md",
    ".json",
    ".toml",
    ".lock",
    ".txt",
    ".cs",
    ".csproj",
    ".sql",
    ".java",
    ".xml",
    ".yaml",
    ".yml",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "--no-pager", *args], text=True, encoding="utf-8"
    ).rstrip("\r\n")


def selected_files(root: Path, patterns: tuple[str, ...]) -> dict[str, bytes]:
    result = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            relative = path.relative_to(root)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
                raise ValueError(f"Refusing source symlink: {relative}")
            result[relative.as_posix()] = path.read_bytes()
    if not result:
        raise ValueError(f"No sources found in {root}")
    return result


def changes_from_head(root: Path, head: str, files: dict[str, bytes]) -> list[dict]:
    tree = {}
    for line in git(root, "ls-tree", "-r", "--full-tree", head).splitlines():
        metadata, path = line.split("\t", 1)
        tree[path] = metadata.split()[2]
    changes = []
    for path, data in files.items():
        if path not in tree:
            changes.append({"path": path, "change": "not-in-recorded-head"})
        else:
            blob = f"blob {len(data)}\0".encode() + data
            algorithm = hashlib.sha1 if len(tree[path]) == 40 else hashlib.sha256
            if algorithm(blob).hexdigest() != tree[path]:
                changes.append({"path": path, "change": "modified-from-recorded-head"})
    return changes


def capture(source_parent: Path, destination: Path) -> dict:
    if destination.exists():
        raise ValueError("Snapshot already exists; choose a fresh destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provenance = {"schema_version": 1, "sources": {}}
    with tempfile.TemporaryDirectory(
        prefix="capability-capture-", ignore_cleanup_errors=True
    ) as directory:
        staging = Path(directory) / "sources"
        staging.mkdir()
        for name, patterns in SOURCES.items():
            source = (source_parent / name).resolve(strict=True)
            before = selected_files(source, patterns)
            head = git(source, "rev-parse", "HEAD")
            status = git(source, "status", "--porcelain", "--untracked-files=all")
            for relative, data in before.items():
                target = staging / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if (
                before != selected_files(source, patterns)
                or head != git(source, "rev-parse", "HEAD")
                or status != git(source, "status", "--porcelain", "--untracked-files=all")
            ):
                raise ValueError(f"Sources changed during capture: {name}")
            provenance["sources"][name] = {
                "head": head,
                "selected_worktree_changes": changes_from_head(source, head, before),
                "status_basis": (
                    "Captured bytes versus recorded HEAD; index staging state not retained"
                ),
                "files": {key: sha256(data) for key, data in before.items()},
            }
        write_json(staging / "provenance.json", provenance)
        shutil.copytree(staging, destination)
    return provenance


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def verify(destination: Path, source_parent: Path | None = None) -> None:
    provenance = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    for name, source in provenance["sources"].items():
        for relative, expected in source["files"].items():
            if sha256((destination / name / relative).read_bytes()) != expected:
                raise ValueError(f"Captured source drift: {name}/{relative}")
            if source_parent is not None:
                if sha256((source_parent / name / relative).read_bytes()) != expected:
                    raise ValueError(f"Original source changed: {name}/{relative}")


def refresh_status(destination: Path, source_parent: Path) -> None:
    verify(destination)
    provenance = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    for name, source in provenance["sources"].items():
        root = source_parent / name
        files = {path: (destination / name / path).read_bytes() for path in source["files"]}
        source["selected_worktree_changes"] = changes_from_head(root, source["head"], files)
        source["status_basis"] = (
            "Captured bytes versus recorded HEAD; index staging state not retained"
        )
    write_json(destination / "provenance.json", provenance)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture allowlisted read-only WIP baselines")
    parser.add_argument("--source-parent", type=Path, default=ROOT.parent)
    parser.add_argument("--output", type=Path, default=ROOT / "baselines")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--check-original", action="store_true")
    parser.add_argument("--refresh-status", action="store_true")
    args = parser.parse_args()
    if args.refresh_status:
        refresh_status(args.output, args.source_parent)
        print("Status metadata derived from captured bytes and recorded HEAD objects")
    elif args.verify:
        verify(args.output, args.source_parent if args.check_original else None)
        print("Snapshot hashes verified")
    else:
        result = capture(args.source_parent, args.output)
        print(f"Captured {sum(len(s['files']) for s in result['sources'].values())} files")


if __name__ == "__main__":
    main()
