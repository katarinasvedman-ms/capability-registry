import asyncio

import httpx

from tools.capture_sources import write_json
from tools.live_support import AsyncCliCredential
from tools.provision_governance import DIRECTORY, Deployment


async def main():
    credential = AsyncCliCredential()
    try:
        token = await credential.get_token("https://api.loganalytics.io/.default")
        workspace = Deployment().state["resources"]["workspace"]["customerId"]
        query = (
            "AppRequests | where TimeGenerated > ago(2h) "
            '| where Name startswith "GET " or Name startswith "POST " '
            "| summarize Requests=count(), Failures=countif(Success == false) by AppRoleName"
        )
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"https://api.loganalytics.azure.com/v1/workspaces/{workspace}/query",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"query": query, "timespan": "PT2H"},
            )
        write_json(
            DIRECTORY / "telemetry.json",
            {
                "workspace": workspace,
                "query": query,
                "status": response.status_code,
                "response": response.json(),
            },
        )
        response.raise_for_status()
        rows = response.json()["tables"][0]["rows"]
        print(rows)
        if not {"consumer", "gateway", "mock"} <= {row[0] for row in rows}:
            raise RuntimeError("Missing server spans for one or more services")
        query = (
            "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1h) "
            '| where ContainerAppName_s == "cr-gov-f3e5ad02-consumer" '
            '| where Log_s contains "Shutting down" or Log_s contains "shutdown complete" '
            'or Log_s contains "Finished server process" '
            "| project TimeGenerated, Log_s | order by TimeGenerated asc"
        )
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"https://api.loganalytics.azure.com/v1/workspaces/{workspace}/query",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"query": query, "timespan": "PT1H"},
            )
        write_json(
            DIRECTORY / "shutdown.json",
            {
                "query": query,
                "status": response.status_code,
                "response": response.json(),
            },
        )
        response.raise_for_status()
        shutdown = response.json()["tables"][0]["rows"]
        print(shutdown)
        if not any("Application shutdown complete" in row[1] for row in shutdown):
            raise RuntimeError("Graceful shutdown not yet verified in persisted ACA logs")
    finally:
        await credential.close()


if __name__ == "__main__":
    asyncio.run(main())
