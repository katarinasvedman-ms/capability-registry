import argparse
import json

from tools.capture_sources import ROOT, write_json
from tools.governance_metrics import summarize


def main(nano=False):
    directory = ROOT / ".artifacts" / "h5"
    if nano:
        directory /= "nano"
    experiment = json.loads((directory / "evaluation.json").read_text())
    rows = experiment["results"]
    groups = {
        "all": rows,
        "frontdoor_top1": [r for r in rows if r["metric"] == "frontdoor_top1"],
        "factory_exact_set": [r for r in rows if r["metric"] == "factory_exact_set"],
        "frontdoor_positive": [
            r for r in rows if r["metric"] == "frontdoor_top1" and r["expected"]
        ],
        "frontdoor_empty": [
            r for r in rows if r["metric"] == "frontdoor_top1" and not r["expected"]
        ],
        "recorded_denied": [
            r
            for r in rows
            if r["split"] == "recorded" and r["semantic_target"] and not r["expected"]
        ],
        **{
            split: [r for r in rows if r["split"] == split]
            for split in ("in_sample", "recorded", "held_out", "source_assertion")
        },
    }
    results = {}
    for group, observations in groups.items():
        results[group] = {
            "n": len(observations),
            **{
                mode: {
                    "correct": sum(r[mode]["correct"] for r in observations),
                    "strict_correct": sum(r[mode]["strict_correct"] for r in observations),
                    "errors": sum("error" in r[mode] for r in observations),
                    "latency": summarize([r[mode]["elapsed_ms"] for r in observations]),
                }
                for mode in ("static", "bm25", "model")
            },
        }
    margin = [r for r in groups["frontdoor_top1"] if r["bm25"].get("top1_margin_candidate")]
    report = {
        "settings": experiment["settings"],
        "requests": experiment["requests"],
        "started_at": experiment["started_at"],
        "completed_at": experiment["completed_at"],
        "groups": results,
        "paired_added_over_static": summarize(
            [r["model"]["elapsed_ms"] - r["static"]["elapsed_ms"] for r in rows]
        ),
        "paired_added_over_bm25": summarize(
            [r["model"]["elapsed_ms"] - r["bm25"]["elapsed_ms"] for r in rows]
        ),
        "model_http": summarize([row["model_ms"] for row in experiment["http"]]),
        "retrieval": summarize(
            [r["bm25"]["retrieval_ms"] for r in rows if "retrieval_ms" in r["bm25"]]
        ),
        "margin_heuristic": {
            "n": len(margin),
            "denominator": len(groups["frontdoor_top1"]),
            "model_agreement": sum(r["model"].get("ids") == r["bm25"].get("ids") for r in margin),
            "bm25_correct": sum(r["bm25"]["correct"] for r in margin),
            "model_correct": sum(r["model"]["correct"] for r in margin),
            "note": (
                "Frozen positive top1 >= 1.5*runner-up heuristic, not semantic certainty; "
                "model not skipped"
            ),
        },
        "tokens": {
            field: sum(
                row.get("response", {}).get("usage", {}).get(field, 0)
                for row in experiment["http"]
                if isinstance(row.get("response"), dict)
            )
            for field in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "counterexamples": [
            {
                "id": r["id"],
                "expected": r["expected"],
                "optional": r["optional"],
                "selected": r["model"].get("ids"),
                "error": r["model"].get("error"),
            }
            for r in rows
            if not r["model"]["correct"]
        ],
    }
    write_json(directory / "metrics.json", report)
    print(experiment["settings"]["model"], "requests", experiment["requests"])
    for group, result in results.items():
        print(
            group,
            result["n"],
            {mode: result[mode]["correct"] for mode in ("static", "bm25", "model")},
        )
    print("added p50/p95", report["paired_added_over_static"])
    print("margin", report["margin_heuristic"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nano", action="store_true")
    main(parser.parse_args().nano)
