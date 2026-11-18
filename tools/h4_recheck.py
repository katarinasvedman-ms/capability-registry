from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from tools.capture_sources import ROOT
from tools.h4_preflight import DIRECTORY, Preflight, SafetyStop, now


async def run():
    state = json.loads((DIRECTORY / "evidence.json").read_text())
    if state.get("safety_stop") or state["status"] != "blocked":
        raise ValueError("Only a blocked, non-stopped read-only role recheck is allowed")
    first = next(row for row in state["role_trials"] if row["label"] == "Foundry User")
    target = datetime.fromisoformat(first["at"]) + timedelta(seconds=900)
    wait = max(0, (target - datetime.now(UTC)).total_seconds())
    experiment = Preflight()
    experiment.state = state
    experiment.state["fallback_propagation_control"] = {
        "first_probe_at": first["at"],
        "not_before": target.isoformat(),
        "wait_seconds": wait,
    }
    experiment.save()
    try:
        await asyncio.sleep(wait)
        capture = json.loads((ROOT / "catalog" / "historical-binding.json").read_text())
        binding = {**capture["binding"], "version": "2"}
        experiment.state["fallback_propagation_control"]["repeated_at"] = now()
        success = await experiment.probe(binding, "Foundry User")
        if success:
            raise ValueError("Baseline accessible without safety stop; proceed to project phase")
        # Compare the skill-only immutable version without changing the identity or grant.
        await experiment.probe({**binding, "version": "1"}, "Foundry User")
        experiment.state.update(
            status="blocked",
            reason=("Foundry User still denied on general versions 1 and 2 after fifteen minutes"),
        )
    except SafetyStop as error:
        experiment.state.update(
            status="stopped", safety_stop=True, reason=str(error), stopped_at=now()
        )
    finally:
        experiment.save()
        await experiment.client.aclose()
        await experiment.credential.close()
    print(
        json.dumps(
            {
                key: experiment.state.get(key)
                for key in ("status", "reason", "fallback_propagation_control")
            },
            indent=2,
        )
    )
    return experiment.state["status"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only fallback-role propagation recheck")
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(2 if asyncio.run(run()) == "stopped" else 1)
