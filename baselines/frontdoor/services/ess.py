from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict

from backend.knowledge import search_knowledge
from backend.store import Store
from services.security import ServiceAuth, service_user

app = FastAPI(title="Employee Self-Service (simulated)")
app.add_middleware(ServiceAuth, audience="ess")


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    inputs: dict
    approval_id: str | None = None
    trace_id: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "Employee Self-Service (simulated)"}


@app.post("/invoke")
def invoke(body: Invocation, request: Request):
    user = service_user(request, "leave.write" if body.tool == "leave" else "leave.read")
    store = Store()
    if body.tool == "balance":
        result = store.get("balances", user["id"], user["id"])
    elif body.tool == "request":
        result = store.get("requests", body.inputs["request_id"], user["id"])
    elif body.tool == "leave":
        result = store.mutate(user["id"], body.approval_id, "people.request_leave", body.inputs)
    elif body.tool == "ask":
        result = {
            "answer": "Employee Self-Service (simulated): check your policy and balance, "
            "then propose leave dates in the portal for explicit confirmation.",
            "citations": search_knowledge(body.inputs.get("question", ""), user),
        }
    else:
        from fastapi import HTTPException

        raise HTTPException(404, "Published-agent operation not found")
    store.audit(
        user["id"],
        None,
        stage="published_agent",
        protocol="published_agent",
        trace_id=body.trace_id,
        status="completed",
        detail=body.tool,
    )
    return {"provider": "Employee Self-Service (simulated)", **result}
