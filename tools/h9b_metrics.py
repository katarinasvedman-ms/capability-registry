from __future__ import annotations

import json

from tools.capture_sources import ROOT, write_json
from tools.governance_metrics import summarize


def report(state, key):
    results = {}
    for capability in ("mock.create_ticket", "mock.deploy_release"):
        rows = [r for r in state[key] if r["capability"] == capability and not r["warmup"]]
        result = {"samples_per_path": len(rows), "paths": {}}
        for path in ("reference", "ai_gateway"):
            values = [r[path] for r in rows]
            metrics = {
                field: summarize([v[field] for v in values])
                for field in (
                    "elapsed_ms",
                    "external_total_ms",
                    "verification_ms",
                    "backend_verification_ms",
                    "backend_business_ms",
                    "backend_total_ms",
                )
            }
            for field in ("policy_ms", "canonicalisation_ms", "signing_ms", "dispatch_ms"):
                metrics[field] = summarize([v["audit"][0][field] for v in values])
            if path == "ai_gateway":
                for field in (
                    "assertion_hop_ms",
                    "assertion_signing_ms",
                    "toolbox_ms",
                    "verifier_total_ms",
                ):
                    metrics[field] = summarize([v[field] for v in values])
                metrics["gateway_and_mcp_residual_ms"] = summarize(
                    [v["audit"][0]["dispatch_ms"] - v["verifier_total_ms"] for v in values]
                )
                metrics["toolbox_excluding_backend_ms"] = summarize(
                    [v["toolbox_ms"] - v["backend_total_ms"] for v in values]
                )
            else:
                metrics["gateway_total_ms"] = summarize([v["gateway_total_ms"] for v in values])
                metrics["gateway_and_mcp_residual_ms"] = summarize(
                    [v["audit"][0]["dispatch_ms"] - v["gateway_total_ms"] for v in values]
                )
            result["paths"][path] = metrics
        result["paired_added_elapsed_ms"] = summarize(
            [r["ai_gateway"]["elapsed_ms"] - r["reference"]["elapsed_ms"] for r in rows]
        )
        result["paired_added_dispatch_ms"] = summarize(
            [
                r["ai_gateway"]["audit"][0]["dispatch_ms"]
                - r["reference"]["audit"][0]["dispatch_ms"]
                for r in rows
            ]
        )
        results[capability] = result
    return results


def main():
    state = json.loads((ROOT / ".artifacts" / "h9b" / "state.json").read_text())
    result = {
        key: report(state, key)
        for key in ("measurements", "normalized_measurements")
        if state.get(key)
    }
    write_json(ROOT / ".artifacts" / "h9b" / "latency.json", result)
    for key, capabilities in result.items():
        print(key)
        for capability, values in capabilities.items():
            print(capability)
            for name, row in values.items():
                if name == "paths":
                    for path, metrics in row.items():
                        for metric, distribution in metrics.items():
                            print(
                                f"  {path}.{metric}: "
                                f"{distribution['p50_ms']:.3f}/{distribution['p95_ms']:.3f}"
                            )
                elif isinstance(row, dict):
                    print(f"  {name}: {row['p50_ms']:.3f}/{row['p95_ms']:.3f}")


if __name__ == "__main__":
    main()
