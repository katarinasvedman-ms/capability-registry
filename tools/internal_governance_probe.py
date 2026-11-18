import asyncio
import json
import os

from governance.runtime import Runtime, sanitized


async def main():
    if os.environ.get("SERVICE") != "gateway":
        raise ValueError("This probe must execute under the owned ACA gateway identity")
    runtime = Runtime()
    try:
        token = await runtime.credential.get_token("https://management.azure.com/.default")
        for name, inputs in (
            ("deploy_release", {"release": "v0.0.6"}),
            ("create_ticket", {"title": "Missing lease identity probe"}),
        ):
            request = {
                "jsonrpc": "2.0",
                "id": 67,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": inputs,
                    "_meta": {
                        "capability-registry/context": {
                            "gateway_assertion": token.token,
                            "identity": runtime.config["benchmark_principal"],
                            "task_id": "gateway-identity-no-lease",
                            "approver": "synthetic:" + runtime.config["benchmark_principal"],
                        },
                    },
                },
            }
            response = await runtime.client.post(runtime.config["mock_url"] + "/mcp", json=request)
            print(
                json.dumps(
                    sanitized(
                        {
                            "probe": "valid_gateway_identity_without_lease",
                            "request": request,
                            "status": response.status_code,
                            "response": response.text,
                        }
                    )
                ),
                flush=True,
            )
            if response.status_code != 403:
                raise RuntimeError("STOP: gateway-only mock accepted a call without a valid lease")
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
