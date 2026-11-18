from __future__ import annotations

import argparse

from tools.capture_sources import ROOT, git, sha256, verify, write_json


def main():
    parser = argparse.ArgumentParser(
        description="Report source evolution without recapturing baselines"
    )
    parser.add_argument("--output", default=str(ROOT / ".artifacts" / "source-drift.json"))
    args = parser.parse_args()
    import json
    from pathlib import Path

    verify(ROOT / "baselines")
    provenance = json.loads((ROOT / "baselines" / "provenance.json").read_text())
    report = {}
    for name, source in provenance["sources"].items():
        root = ROOT.parent / name
        changed = []
        for path, digest in source["files"].items():
            original = root / path
            if not original.is_file() or sha256(original.read_bytes()) != digest:
                changed.append(path)
        report[name] = {
            "captured_head": source["head"],
            "current_head": git(root, "rev-parse", "HEAD"),
            "changed_captured_files": changed,
        }
    write_json(Path(args.output), report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
