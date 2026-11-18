import argparse
import asyncio
import json

from openai.types.responses import Response

from governance.runtime import sanitized
from tools.capture_sources import ROOT, write_json
from tools.governance_live import Experiment, now

PATH = ROOT / ".artifacts" / "h5" / "hosted.json"


class HostedExperiment(Experiment):
    def __init__(self):
        if PATH.exists():
            raise ValueError("Retain existing hosted proof; no silent overwriting")
        super().__init__()
        self.state = {"http": [], "started_at": now()}

    def save(self):
        write_json(PATH, sanitized(self.state))

    async def verify(self):
        self.state["image"] = self.deployment.state["images"][self.deployment.state["image_tag"]][
            "digest"
        ]
        for service in ("consumer", "gateway", "mock"):
            result = await self.http("GET", self.config[service + "_url"] + "/readiness")
            result.raise_for_status()
        self.state["before"] = await self.snapshot()
        original = json.loads((ROOT / ".artifacts" / "governance" / "live.json").read_text())[
            "before"
        ]
        if self.state["before"] != original:
            raise RuntimeError("Immutable Toolbox/default controls changed before hosted proof")
        offset = len(await self.events("mock"))
        selections = {}
        for mode in ("static", "bm25", "model"):
            result = await self.http(
                "POST",
                self.config["consumer_url"] + "/selection",
                headers=await self.headers(),
                value={"input": "Test create_ticket", "mode": mode},
            )
            result.raise_for_status()
            selections[mode] = result.json()
            if selections[mode]["ids"] != ["mock.create_ticket"]:
                raise RuntimeError("Hosted ticket selection fixture failed")
        if len(await self.events("mock")) != offset:
            raise RuntimeError("Selection executed a mock tool")
        self.state["selection"] = selections
        denied = await self.http(
            "POST",
            self.config["consumer_url"] + "/selection",
            headers=await self.headers("coder"),
            value={"input": "Deploy a release", "mode": "model"},
        )
        denied.raise_for_status()
        self.state["coder_selection"] = denied.json()
        if "mock.deploy_release" in denied.json()["ids"]:
            raise RuntimeError("Model exceeded coder trust tier")
        inputs = {"title": "H5 hosted selected ticket"}
        response = await self.http(
            "POST",
            self.config["consumer_url"] + "/responses",
            headers=await self.headers(),
            value={
                "input": "Execute the selected synthetic ticket",
                "metadata": {
                    "capability_id": selections["model"]["ids"][0],
                    "arguments": json.dumps(inputs),
                    "synthetic_approval": "completed",
                },
            },
        )
        response.raise_for_status()
        Response.model_validate(response.json())
        self.state["execution"] = response.json()
        received = (await self.events("mock"))[offset:]
        self.state["mock"] = received
        if (
            len(received) != 1
            or not received[0]["accepted"]
            or received[0]["request"]["params"]["arguments"] != inputs
            or received[0]["lease_id"] != response.json()["governance"]["audit"][0]["lease_id"]
        ):
            raise RuntimeError("Hosted selected/approved lease path did not reach exactly one mock")
        await self.privileged_probe()
        await self.general_probe("general_after")
        self.state["after"] = await self.snapshot()
        if self.state["after"] != self.state["before"]:
            raise RuntimeError("Immutable Toolbox/default controls changed")
        self.state["passed_at"] = now()
        self.save()
        print("Hosted nano selection, leased call and immutable controls passed")


async def main():
    experiment = HostedExperiment()
    try:
        await experiment.verify()
    finally:
        await experiment.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(main())
