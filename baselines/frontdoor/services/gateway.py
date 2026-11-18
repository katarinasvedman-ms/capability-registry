import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import MCPServer

from backend.config import service_secret
from backend.store import Store
from services.legacy import router
from services.security import ServiceAuth, delegation, require_permission

LEGACY_TIMEOUT = 10

mcp = MCPServer(
    "Local AI/API Gateway", instructions="Synthetic legacy Operations and inventory tools"
)
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/", stateless_http=True, json_response=True, host="0.0.0.0"
)


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AI/API Gateway - simulated legacy ERP", lifespan=lifespan)
app.add_middleware(ServiceAuth, audience="gateway")
app.include_router(router)
app.mount("/mcp", mcp_app)


async def legacy_call(method, path, user_id, trace_id, permission, body=None):
    require_permission(user_id, permission)
    started = time.monotonic()
    try:
        # ASGI transports do not enforce HTTPX network timeouts.
        async with (
            asyncio.timeout(LEGACY_TIMEOUT),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://legacy.local",
                timeout=LEGACY_TIMEOUT,
                headers={
                    "Authorization": f"Bearer {service_secret()}",
                    "x-demo-user": user_id,
                    "x-frontdoor-delegation": delegation.get(),
                    "x-trace-id": trace_id,
                },
            ) as client,
        ):
            response = await client.request(method, path, json=body)
            if response.status_code >= 400:
                raise HTTPException(
                    response.status_code, response.json().get("detail", "Legacy API error")
                )
            result = response.json()
    except (TimeoutError, HTTPException) as error:
        message = "Legacy API timeout" if isinstance(error, TimeoutError) else error.detail
        Store().audit(
            user_id,
            None,
            stage="legacy_api",
            protocol="mcp",
            trace_id=trace_id,
            status="failed",
            latency_ms=round((time.monotonic() - started) * 1000),
            detail={"method": method, "path": path, "error": message},
        )
        if isinstance(error, TimeoutError):
            raise HTTPException(504, f"{message}. Correlation ID: {trace_id}") from error
        raise
    Store().audit(
        user_id,
        None,
        stage="legacy_api",
        protocol="mcp",
        trace_id=trace_id,
        status="completed",
        latency_ms=round((time.monotonic() - started) * 1000),
        detail={"method": method, "path": path, "request": body, "response": result},
    )
    return result


@mcp.tool()
async def get_service_request(case_id: str, user_id: str, trace_id: str) -> dict:
    """Read a permitted mock legacy service request or case."""
    return await legacy_call(
        "GET", f"/legacy/requests/{case_id}", user_id, trace_id, "operations.read"
    )


@mcp.tool()
async def create_service_request(title: str, user_id: str, approval_id: str, trace_id: str) -> dict:
    """Create a mock legacy service request; matching server-side approval is mandatory."""
    return await legacy_call(
        "POST",
        "/legacy/requests",
        user_id,
        trace_id,
        "operations.write",
        {"title": title, "approval_id": approval_id},
    )


@mcp.tool()
async def order_hardware(item: str, user_id: str, approval_id: str, trace_id: str) -> dict:
    """Order a synthetic laptop; exact-input server-side approval is mandatory."""
    return await legacy_call(
        "POST",
        "/legacy/hardware-orders",
        user_id,
        trace_id,
        "operations.write",
        {"item": item, "approval_id": approval_id},
    )


@mcp.tool()
async def get_hardware_order(order_id: str, user_id: str, trace_id: str) -> dict:
    """Read the employee's own synthetic hardware order."""
    return await legacy_call(
        "GET", f"/legacy/hardware-orders/{order_id}", user_id, trace_id, "operations.read"
    )


@mcp.tool()
async def get_inventory_status(product_id: str, user_id: str, trace_id: str) -> dict:
    """Read synthetic inventory for a permitted product."""
    return await legacy_call(
        "GET", f"/legacy/inventory/{product_id}", user_id, trace_id, "supply.read"
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "AI/API Gateway (MCP)"}
