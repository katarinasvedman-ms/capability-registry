import argparse
import json

from tools.capture_sources import ROOT, write_json
from tools.provision_governance import ARM, DIRECTORY, Deployment, az


def main(nano=False):
    gate = json.loads((ROOT / ".artifacts" / "gate.json").read_text())
    if gate["h5_entry_gate"] != "open":
        raise ValueError("Reported live H6/H7 and dynamic H5 gate required")
    suffix = "-nano" if nano else ""
    settings = json.loads((ROOT / "evaluation" / f"settings{suffix}.json").read_text())
    if not (ROOT / "evaluation" / f"dataset{suffix}.json").exists():
        raise ValueError("Freeze H5 settings/labels before provisioning")
    deployment = Deployment()
    identifier = (
        ARM
        + "/providers/Microsoft.CognitiveServices/accounts/factory-skills-resource"
        + "/deployments/"
        + settings["deployment"]
    )
    payload = {
        "sku": {"name": settings["sku"], "capacity": settings["capacity"]},
        "tags": {
            "purpose": "capability-registry-testbed",
            "owner": "katarina.svedman",
            "run": "f3e5ad02",
        },
        "properties": {
            "model": {
                "format": "OpenAI",
                "name": settings["model"],
                "version": settings["model_version"],
            },
            "versionUpgradeOption": settings["version_upgrade"],
            "raiPolicyName": settings["rai_policy"],
        },
    }
    request_path = DIRECTORY / f"selector-deployment-request{suffix}.json"
    write_json(request_path, payload)
    deployment.resource(
        "selector_model_nano" if nano else "selector_model",
        "modelDeployment",
        identifier,
        [
            "rest",
            "--method",
            "PUT",
            "--url",
            "https://management.azure.com" + identifier + "?api-version=2025-06-01",
            "--body",
            "@" + str(request_path),
        ],
    )
    actual = az(
        "rest",
        "--method",
        "GET",
        "--url",
        "https://management.azure.com" + identifier + "?api-version=2025-06-01",
    )
    write_json(DIRECTORY / f"selector-deployment{suffix}.json", actual)
    if actual["properties"]["model"] != payload["properties"]["model"]:
        # Additional service annotations are acceptable; exact pin fields are not.
        if any(
            actual["properties"]["model"].get(k) != v
            for k, v in payload["properties"]["model"].items()
        ):
            raise RuntimeError("Unexpected deployed model/version")
    if (
        actual.get("tags") != payload["tags"]
        or actual["properties"]["provisioningState"] != "Succeeded"
    ):
        raise RuntimeError("Tagged model deployment not yet verified")
    print("New tagged selector model/version verified; no existing grant or deployment changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--nano", action="store_true")
    main(parser.parse_args().nano)
