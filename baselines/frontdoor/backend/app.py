import asyncio
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.auth import auth_configuration, entra_authenticator, public_user
from backend.capabilities import Catalog, Search
from backend.config import (
    auth_mode,
    catalog_provider,
    ess_provider,
    gateway_provider,
    knowledge_provider,
    mode,
    service_secret,
    supply_provider,
)
from backend.delegation import initialize_signing_key
from backend.invoker import Invoker
from backend.orchestrator import Orchestrator
from backend.store import Store


@asynccontextmanager
async def lifespan(app):
    service_secret()
    auth_configuration()
    app.state.store = Store()
    initialize_signing_key()
    app.state.store.recover_runtime()
    app.state.catalog = Catalog()
    yield


app = FastAPI(title="Enterprise AI Portal API", lifespan=lifespan)
locks = defaultdict(asyncio.Lock)
jobs: dict[str, asyncio.Task] = {}
windows = defaultdict(deque)


def store(request: Request):
    return request.app.state.store


def catalog(request: Request):
    return request.app.state.catalog


def user(authorization: str = Header(default=""), db: Store = Depends(store)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer access token required")
    if auth_mode() == "entra":
        return entra_authenticator().authenticate(token, db)
    if not token:
        raise HTTPException(401, "Select a synthetic demo user first")
    try:
        session = db.get("sessions", token)
    except HTTPException:
        raise HTTPException(401, "Session not found") from None
    if session["expires_at"] < time.time():
        raise HTTPException(401, "Session expired")
    return db.user(session["user_id"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(StrictBody):
    user_id: str


class ChatRequest(StrictBody):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ApprovalRequest(StrictBody):
    approved: bool


class Invocation(StrictBody):
    capability_id: str
    inputs: dict
    conversation_id: str | None = None
    approval_id: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "mode": mode()}


@app.post("/api/session")
def session(body: SessionRequest, db: Store = Depends(store)):
    if auth_mode() != "mock":
        raise HTTPException(404, "Synthetic sessions are disabled")
    if body.user_id not in {"alex", "sam"}:
        raise HTTPException(404, "Synthetic user not found")
    identity = db.user(body.user_id)
    token = secrets.token_urlsafe(32)
    db.put(
        "sessions",
        token,
        identity["id"],
        {
            "user_id": identity["id"],
            "expires_at": time.time() + 86400,
        },
    )
    return {
        "token": token,
        "user": public_user(identity),
        "mode": mode(),
    }


@app.get("/api/auth/config")
def authentication_configuration():
    return auth_configuration()


@app.get("/api/me")
def me(identity=Depends(user)):
    return {"user": public_user(identity), "mode": mode()}


@app.get("/api/runtime")
def runtime(identity=Depends(user), db: Store = Depends(store)):
    cases = db.list("cases", identity["id"]) if "operations.read" in identity["permissions"] else []
    return {
        "mode": mode(),
        "auth_mode": auth_mode(),
        "demo_case_id": min((case["id"] for case in cases), default=None),
        "providers": {
            "discovery": "Azure API Center (permission-filtered)"
            if catalog_provider() == "apic"
            else "Local capability catalog (permission-filtered)",
            "orchestrator": "Microsoft Agent Framework"
            if mode() == "live"
            else "Deterministic simulated decision provider (offline)",
            "knowledge": "mock_foundry_iq"
            if knowledge_provider() == "mock"
            else (
                "Foundry IQ knowledge base (agentic retrieval; synthetic content)"
                if knowledge_provider() == "foundry_iq"
                else "azure_ai_search (synthetic indexed demo content)"
            ),
            "work_context": "mock_work_iq",
            "people": "Foundry ESS-style specialist (simulated HR backend)"
            if ess_provider() == "foundry"
            else "Employee Self-Service (simulated)",
            "supply": "Foundry agent over A2A (synthetic snapshot)"
            if supply_provider() == "foundry_a2a"
            else "Local A2A specialist",
            "operations": "APIM native MCP to ACA synthetic ERP"
            if gateway_provider() == "apim"
            else "Mock legacy ERP over MCP",
        },
        "azure": {
            "resource_group": "frontdoor",
            "preferred_region": "swedencentral",
            "fallback_region": "francecentral",
        },
    }


@app.get("/api/conversations")
def conversations(identity=Depends(user), db: Store = Depends(store)):
    return [
        {"id": row["id"], "title": row["title"]} for row in db.list("conversations", identity["id"])
    ]


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, identity=Depends(user), db: Store = Depends(store)):
    value = db.get("conversations", conversation_id, identity["id"])
    # Approval status can change independently of the stored transcript.
    for message in value["messages"]:
        if message.get("approval"):
            message["approval"] = db.get("approvals", message["approval"]["id"], identity["id"])
    return value


@app.get("/api/capabilities")
def capabilities(identity=Depends(user), registry: Catalog = Depends(catalog)):
    return registry.listing(identity)


@app.post("/api/capabilities/search")
def search(body: Search, identity=Depends(user), registry: Catalog = Depends(catalog)):
    return registry.search(body, identity)


@app.post("/api/catalog/reload")
def reload_catalog(identity=Depends(user), registry: Catalog = Depends(catalog)):
    if "operations.admin" not in identity["permissions"]:
        raise HTTPException(403, "Catalog administrator required")
    try:
        registry.reload()
    except (ValueError, OSError):
        raise HTTPException(
            422,
            "Catalog reload rejected; check schema, adapters and approval requirements. "
            "Previous catalog remains active.",
        ) from None
    return {"status": "reloaded"}


@app.get("/api/capabilities/{capability_id}")
def metadata(capability_id: str, identity=Depends(user), registry: Catalog = Depends(catalog)):
    entry = registry.entries.get(capability_id)
    if not entry or not registry.permitted(entry, identity):
        raise HTTPException(404, "Capability not found")
    return registry.metadata(entry)


@app.post("/api/capabilities/invoke")
async def invoke(
    body: Invocation,
    identity=Depends(user),
    db: Store = Depends(store),
    registry: Catalog = Depends(catalog),
):
    try:
        await asyncio.to_thread(registry.resolve, body.capability_id, identity)
    except HTTPException:
        db.audit(
            identity["id"],
            None,
            stage="execution",
            capability_id=body.capability_id,
            trace_id=str(uuid.uuid4()),
            status="failed",
            policy_decision="denied",
        )
        raise
    conv = (
        db.get("conversations", body.conversation_id, identity["id"])
        if body.conversation_id
        else db.new_conversation(identity["id"], body.capability_id)
    )
    async with locks[conv["id"]]:
        conv = db.get("conversations", conv["id"], identity["id"])
        invoker = Invoker(db, registry, identity, conv, lambda event: None)
        try:
            return await invoker.invoke(body.capability_id, body.inputs, body.approval_id)
        finally:
            db.put("conversations", conv["id"], identity["id"], conv)


@app.post("/api/approvals/{approval_id}")
async def approve(
    approval_id: str, body: ApprovalRequest, identity=Depends(user), db: Store = Depends(store)
):
    approval = db.get("approvals", approval_id, identity["id"])
    async with locks[approval["conversation_id"]]:
        return db.transition(
            approval_id, identity["id"], {"pending"}, "approved" if body.approved else "declined"
        )


@app.get("/api/lob/cases/{case_id}")
def case(case_id: str, identity=Depends(user), db: Store = Depends(store)):
    if "operations.read" not in identity["permissions"]:
        raise HTTPException(403, "Case access not permitted")
    return db.get("cases", case_id, identity["id"])


@app.get("/api/lob/expenses")
def expenses(identity=Depends(user)):
    if "operations.read" not in identity["permissions"]:
        raise HTTPException(403, "Expense application access not permitted")
    return {
        "title": "Travel expense report",
        "status": "Sample draft",
        "provider": "Mock Concur-style application",
        "expenses": [
            {
                "description": "Demo train journey",
                "category": "Travel",
                "amount": 450,
                "currency": "SEK",
            },
            {
                "description": "Demo business lunch",
                "category": "Meals",
                "amount": 180,
                "currency": "SEK",
            },
        ],
    }


@app.get("/api/audit")
def audit(conversation_id: str | None = None, identity=Depends(user), db: Store = Depends(store)):
    if conversation_id:
        db.get("conversations", conversation_id, identity["id"])
    return db.audits(identity["id"], conversation_id)


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str, identity=Depends(user), db: Store = Depends(store)):
    return db.get("tasks", task_id, identity["id"])


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, identity=Depends(user), db: Store = Depends(store)):
    value = db.get("tasks", task_id, identity["id"])
    job = jobs.get(task_id)
    if job and not job.done():
        job.cancel()
        return {"id": task_id, "status": "cancelling"}
    return {"id": task_id, "status": value["status"]}


@app.post("/api/chat")
async def chat(
    body: ChatRequest,
    identity=Depends(user),
    db: Store = Depends(store),
    registry: Catalog = Depends(catalog),
):
    window = windows[identity["id"]]
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= 30:
        raise HTTPException(429, "Conversation quota exceeded; retry shortly")
    window.append(now)
    conv = (
        db.get("conversations", body.conversation_id, identity["id"])
        if body.conversation_id
        else db.new_conversation(identity["id"], body.message)
    )
    conversation_id = conv["id"]
    task_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    db.put(
        "tasks",
        task_id,
        identity["id"],
        {
            "id": task_id,
            "conversation_id": conversation_id,
            "status": "running",
        },
    )

    async def execute():
        async with locks[conversation_id]:
            current = db.get("conversations", conversation_id, identity["id"])
            current["messages"].append({"role": "user", "content": body.message})
            invoker = Invoker(db, registry, identity, current, queue.put_nowait)
            try:
                async with asyncio.timeout(100):
                    result = await Orchestrator(invoker).run(body.message)
                current["messages"].append({"role": "assistant", **result})
                queue.put_nowait(result)
                db.put(
                    "tasks",
                    task_id,
                    identity["id"],
                    {
                        "id": task_id,
                        "conversation_id": conversation_id,
                        "status": "completed",
                        "result": result,
                    },
                )
            except asyncio.CancelledError:
                invoker.activity("task", status="cancelled", detail={"task_id": task_id})
                db.put(
                    "tasks",
                    task_id,
                    identity["id"],
                    {
                        "id": task_id,
                        "conversation_id": conversation_id,
                        "status": "cancelled",
                    },
                )
                queue.put_nowait(
                    {
                        "type": "error",
                        "message": "Task cancelled; check action status "
                        "before retrying a mutation.",
                    }
                )
            except Exception as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Task failed. Check service health and audit records; no mock fallback was used."
                )
                invoker.activity("task", status="failed", detail=message)
                queue.put_nowait({"type": "error", "message": message})
                current["messages"].append({"role": "assistant", "content": message})
                db.put(
                    "tasks",
                    task_id,
                    identity["id"],
                    {
                        "id": task_id,
                        "conversation_id": conversation_id,
                        "status": "failed",
                        "error": message,
                    },
                )
            finally:
                db.put("conversations", conversation_id, identity["id"], current)
                queue.put_nowait(None)

    running = asyncio.create_task(execute())
    jobs[task_id] = running

    def completed(job):
        jobs.pop(task_id, None)
        if job.cancelled():
            db.put(
                "tasks",
                task_id,
                identity["id"],
                {
                    "id": task_id,
                    "conversation_id": conversation_id,
                    "status": "cancelled",
                },
            )
            queue.put_nowait({"type": "error", "message": "Queued task cancelled."})
            queue.put_nowait(None)

    running.add_done_callback(completed)

    async def events():
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "conversation",
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                }
            )
            + "\n\n"
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield "data: " + json.dumps(event) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
