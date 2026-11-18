from __future__ import annotations

import json
import math

from tools.capture_sources import write_json
from tools.provision_governance import DIRECTORY


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    return ordered[lower] + (ordered[math.ceil(position)] - ordered[lower]) * (position - lower)


def summarize(values):
    return {"n": len(values), "p50_ms": percentile(values, 0.5), "p95_ms": percentile(values, 0.95)}


def main():
    evidence = json.loads((DIRECTORY / "live.json").read_text())
    report = {"measured_at": evidence["h8_measured_at"], "results": []}
    for experiment in evidence["h8"]:
        samples = experiment["samples"]
        metrics = {}
        for mode in ("baseline", "governed"):
            metrics[mode + "_elapsed"] = summarize([row[mode]["elapsed_ms"] for row in samples])
            metrics[mode + "_verification"] = summarize(
                [row[mode]["verification_ms"] for row in samples]
            )
            metrics[mode + "_gateway_residence"] = summarize(
                [row[mode]["gateway_total_ms"] for row in samples]
            )
            metrics[mode + "_mcp_transport_session"] = summarize(
                [
                    row[mode]["audit"][0]["dispatch_ms"] - row[mode]["gateway_total_ms"]
                    for row in samples
                ]
            )
        metrics["paired_added_elapsed"] = summarize(
            [row["governed"]["elapsed_ms"] - row["baseline"]["elapsed_ms"] for row in samples]
        )
        for field in ("policy_ms", "canonicalisation_ms", "signing_ms"):
            metrics[field] = summarize([row["governed"]["audit"][0][field] for row in samples])
        report["results"].append(
            {
                "capability_id": experiment["capability_id"],
                "warmup_pairs": experiment["warmup_pairs"],
                "baseline_scope": experiment["baseline_scope"],
                "metrics": metrics,
            }
        )
    write_json(DIRECTORY / "latency.json", report)
    for result in report["results"]:
        print(result["capability_id"])
        for name, values in result["metrics"].items():
            print(f"  {name}: {values['p50_ms']:.3f} / {values['p95_ms']:.3f} ms")


if __name__ == "__main__":
    main()
