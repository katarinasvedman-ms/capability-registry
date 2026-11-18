from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from mocks.definitions import TOOLS
from mocks.protocol import dispatch

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
LOG = Path(os.environ.get("MOCK_CALL_LOG", ".artifacts/mock-calls.jsonl"))
LOCK = asyncio.Lock()
LIMIT = 64 * 1024


@app.get("/healthz")
async def health():
    return {"synthetic": True, "status": "ready", "tools": sorted(TOOLS)}


@app.post("/mcp")
async def mcp(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > LIMIT:
            return JSONResponse({"error": "Synthetic request size limit"}, status_code=413)
    message = None
    try:
        message = json.loads(body)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("Expected a JSON-RPC 2.0 object")
        payload = {"jsonrpc": "2.0", "id": message.get("id"), "result": dispatch(message)}
    except (ValueError, KeyError, TypeError) as error:
        if not isinstance(message, dict):
            message = {"invalid_request": body.decode(errors="replace")}
        payload = {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32602, "message": str(error)},
        }
    notification = "id" not in message and "invalid_request" not in message
    response = None if notification else payload
    async with LOCK:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"request": message, "response": response}) + "\n")
    return Response(status_code=202) if notification else JSONResponse(payload)
