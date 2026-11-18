from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
from azure.identity.aio import CertificateCredential

from selection.model import ProjectModel
from selection.retrieval import choose
from tools.assess_gate import governance_reported, governance_supported
from tools.capture_sources import ROOT, sha256, write_json

DIRECTORY = ROOT / ".artifacts" / "h5"


def correct(case, ids, *, optional=True):
    if case["metric"] == "factory_exact_set":
        expected = set(case["expected"])
        return expected <= set(ids) <= expected | (set(case["optional"]) if optional else set())
    return ids[:1] == case["expected"]


async def run(smoke=False, nano=False):
    suffix = "-nano" if nano else ""
    settings_bytes = (ROOT / "evaluation" / f"settings{suffix}.json").read_bytes()
    dataset_bytes = (ROOT / "evaluation" / f"dataset{suffix}.json").read_bytes()
    catalog_bytes = (ROOT / "catalog" / "catalog.json").read_bytes()
    settings, dataset, catalog = map(json.loads, (settings_bytes, dataset_bytes, catalog_bytes))
    gate = json.loads((ROOT / ".artifacts" / "gate.json").read_text())
    live = json.loads((ROOT / ".artifacts" / "governance" / "live.json").read_text())
    if (
        gate["h5_entry_gate"] != "open"
        or not all(governance_supported(live))
        or not governance_reported()
    ):
        raise ValueError("Current H6/H7 report and dynamic gate required")
    if dataset["settings_sha256"] != sha256(settings_bytes) or dataset["catalog_sha256"] != sha256(
        catalog_bytes
    ):
        raise ValueError("Frozen evaluation settings/catalog changed")
    output = (DIRECTORY / "nano" if nano else DIRECTORY) / (
        "smoke.json" if smoke else "evaluation.json"
    )
    if output.exists():
        raise ValueError("Retain existing H5 evidence; no silent rerun/overwriting")
    h4 = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())["release"]
    credential = CertificateCredential(
        h4["tenant_id"], h4["client_id"], certificate_path=h4["certificate"]
    )
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "settings": settings,
        "dataset_sha256": sha256(dataset_bytes),
        "catalog_sha256": sha256(catalog_bytes),
        "entry_gate": gate,
        "identity": h4["principal_id"],
        "results": [],
        "http": [],
        "selector_code": {
            name: sha256((ROOT / "selection" / name).read_bytes())
            for name in ("retrieval.py", "model.py", "static.py", "frontdoor.py")
        },
    }
    write_json(output, evidence)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            model = ProjectModel(client, credential, settings, evidence["http"])
            cases = dataset["cases"][:1] if smoke else dataset["cases"]
            for repetition in range(1 if smoke else settings["repetitions"]):
                for case in cases:
                    row = {
                        "id": case["id"],
                        "metric": case["metric"],
                        "split": case["split"],
                        "expected": case["expected"],
                        "optional": case["optional"],
                        "semantic_target": case.get("semantic_target"),
                        "repetition": repetition,
                    }
                    evidence["results"].append(row)
                    for mode in ("static", "bm25", "model"):
                        if mode == "model":
                            await model.pace()
                        started = time.perf_counter()
                        try:
                            result = await choose(
                                case["context"],
                                catalog,
                                case["policy"],
                                mode=mode,
                                model=model,
                                settings=settings,
                            )
                            row[mode] = {
                                **result,
                                "correct": correct(case, result["ids"]),
                                "strict_correct": correct(case, result["ids"], optional=False),
                            }
                        except Exception as error:
                            row[mode] = {
                                "error_type": type(error).__name__,
                                "error": str(error)[:1000],
                                "correct": False,
                                "strict_correct": False,
                                "elapsed_ms": (time.perf_counter() - started) * 1000,
                            }
                        write_json(output, evidence)
                    if smoke and "error" in row["model"]:
                        raise RuntimeError("Model transport smoke failed; see recorded raw HTTP")
            evidence["requests"] = model.requests
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            write_json(output, evidence)
    finally:
        await credential.close()
    print(("Smoke" if smoke else "Frozen evaluation") + f" recorded: {output.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--nano", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.smoke, args.nano))
