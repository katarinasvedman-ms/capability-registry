from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.core.registry import validate_binding
from tools.capture_sources import sha256, write_json


def main():
    parser = argparse.ArgumentParser(
        description="Project a nonsecret historical binding for probes"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output; historical binding captures are immutable")
    data = args.source.read_bytes()
    source = json.loads(data)
    binding = {key: source[key] for key in ("project_endpoint", "toolbox", "version")}
    binding["risk_tier"] = "general"
    validate_binding(binding)
    skills = source["skills"]
    if not isinstance(skills, dict):
        parser.error("Expected an immutable skill-name to version mapping")
    for version in skills.values():
        validate_binding({**binding, "member_version": version})
    write_json(
        args.output,
        {
            "binding": binding,
            "skill_pins": skills,
            "original_binding_sha256": sha256(data),
            "interpretation": "Historical immutable input, not latest-release or approval evidence",
        },
    )
    if data != args.source.read_bytes():
        raise ValueError("Original binding changed during capture")
    print("Historical binding captured without altering the source")


if __name__ == "__main__":
    main()
