from __future__ import annotations

import copy
import json
import sys

from tools.capture_sources import ROOT, write_json

sys.path.insert(0, str(ROOT / "baselines" / "ai-factory" / "src"))
from ai_factory.contracts import RUNTIME, Snapshot, canonical, local_files  # noqa: E402
from ai_factory.provider import ProviderSnapshot  # noqa: E402
from ai_factory.selection import select as source_select  # noqa: E402

from selection import select  # noqa: E402


def outcome(action):
    try:
        return {"result": action()}
    except ValueError as error:
        return {"error": str(error)}


def bridge_parity(catalog, role):
    snapshot = Snapshot(local_files(ROOT / "baselines" / "factory-skills"), {})
    allowed = sorted(snapshot.entries)

    def compare(technologies, *, required=None, runtime=RUNTIME, only_skill=None):
        source = copy.deepcopy(snapshot)
        if runtime != RUNTIME:
            for name, entry in source.entries.items():
                if only_skill is None or name == only_skill:
                    entry["compatibility"] = [runtime]
        before = outcome(
            lambda: source_select(
                source, technologies, [role], ["implementation", "testing"], allowed, required
            )[role]
        )
        after = outcome(
            lambda: (
                select(
                    {
                        "role": role,
                        "technologies": [*technologies, RUNTIME],
                        "tasks": ["implementation", "testing"],
                        "mode": "bridge",
                    },
                    catalog
                    if runtime == RUNTIME
                    else [
                        {
                            **entry,
                            "technologies": [
                                {"name": runtime, "constraint": "*"}
                                if isinstance(item, dict)
                                and item["name"] == RUNTIME
                                and (only_skill is None or entry["id"] == only_skill)
                                else item
                                for item in entry["technologies"]
                            ],
                        }
                        for entry in catalog
                    ],
                    {"allowed_skills": allowed, "required_skills": required or {}},
                ).candidate_ids
            )
        )
        return {
            "source": before,
            "contract": after,
            "equal": ("error" in before and "error" in after) or before == after,
            "both_rejected": "error" in before and "error" in after,
        }

    result = compare(["dotnet"])
    result["unmet_prerequisite"] = compare([], required={role: ["dotnet-development"]})
    result["missing_runtime"] = compare(["dotnet"], runtime="copilot-sdk-python-2")
    result["one_incompatible_skill"] = compare(
        ["dotnet"], runtime="copilot-sdk-python-2", only_skill="dotnet-development"
    )
    result["unknown_stack"] = compare(["unknown-stack"])
    result["equal"] = all(
        part["equal"]
        for part in (
            {"equal": result["equal"]},
            result["unmet_prerequisite"],
            result["missing_runtime"],
            result["one_incompatible_skill"],
            result["unknown_stack"],
        )
    )
    return result


def run():
    catalog = json.loads((ROOT / "catalog" / "catalog.json").read_text())
    policy = json.loads(
        (ROOT / "baselines" / "ai-factory" / "examples" / "provider-policy.json").read_text()
    )
    baseline = ProviderSnapshot(
        {entry["id"]: {} for entry in catalog if entry["type"] == "skill"}, {}
    ).assignments(policy, ["coder", "tester"])
    selected = {
        role: select(
            {
                "role": role,
                "technologies": ["dotnet", RUNTIME],
                "tasks": ["implementation", "testing"],
            },
            catalog,
            {"allowed_skills": policy["allowed_skills"]},
        ).candidate_ids
        for role in ("coder", "tester")
    }
    bridge = {role: bridge_parity(catalog, role) for role in ("coder", "tester")}
    return {
        "provider": {
            "source": baseline,
            "contract": selected,
            "canonical_equal": canonical(baseline) == canonical(selected),
        },
        "bridge": bridge,
        "supported": canonical(baseline) == canonical(selected)
        and all(row["equal"] for row in bridge.values()),
        "selection_policy": (
            "Permitted/required maps were not used to generate provider candidates."
        ),
    }


def main():
    evidence = run()
    write_json(ROOT / ".artifacts" / "selection-parity.json", evidence)
    print(json.dumps(evidence, indent=2))
    raise SystemExit(0 if evidence["supported"] else 1)


if __name__ == "__main__":
    main()
