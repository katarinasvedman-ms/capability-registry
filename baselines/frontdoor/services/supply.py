import asyncio
import json
import os
import uuid

from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import a2a_pb2 as p
from fastapi import FastAPI

from backend.store import Store
from services.security import ServiceAuth, principal, require_permission
from services.supply_data import INSTRUCTIONS, SUPPLY_DATA


class SyntheticUser(User):
    def __init__(self, user_id):
        self.user_id = user_id

    @property
    def is_authenticated(self):
        return True

    @property
    def user_name(self):
        return self.user_id


class IdentityBuilder(DefaultServerCallContextBuilder):
    def build_user(self, request):
        user_id = principal.get()["id"]
        require_permission(user_id, "supply.read")
        return SyntheticUser(user_id)


class SupplyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        user_id = context.call_context.user.user_name
        require_permission(user_id, "supply.read")
        task_id, context_id = context.task_id, context.context_id
        await event_queue.enqueue_event(
            p.Task(
                id=task_id,
                context_id=context_id,
                status=p.TaskStatus(state=p.TASK_STATE_SUBMITTED),
                history=[context.message],
            )
        )
        await event_queue.enqueue_event(
            p.TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=p.TaskStatus(state=p.TASK_STATE_WORKING),
            )
        )
        payload = json.loads(context.get_user_input())
        product_id = payload.get("product_id")
        if product_id not in SUPPLY_DATA:
            await event_queue.enqueue_event(
                p.TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=p.TaskStatus(
                        state=p.TASK_STATE_FAILED,
                        message=p.Message(
                            message_id=str(uuid.uuid4()),
                            role=p.ROLE_AGENT,
                            parts=[p.Part(text="Unknown product; supply assessment unavailable.")],
                        ),
                    ),
                )
            )
            return
        await asyncio.sleep(0.05)
        data = SUPPLY_DATA[product_id]
        shortage = max(0, data["demand"] - data["available"] - data["expected_delivery"])
        risk = "High" if shortage else "Low"
        result = {
            "product_id": product_id,
            "risk": risk,
            "shortfall": shortage,
            "assessment": f"{risk} risk: projected shortfall {shortage} units. "
            f"Supplier delay {data['delay_days']} days.",
            "mitigations": ["Review alternate stock and expedite delivery"]
            if shortage
            else ["Monitor scheduled deliveries"],
            "provider": "Supply specialist (simulated)",
            "evidence": [
                {
                    "id": f"SUPPLY-{product_id}",
                    "title": f"Seeded supply snapshot {product_id}",
                    "section": "Next-week forecast",
                    "provenance": "mock_supply_agent",
                    "text": json.dumps(data),
                }
            ],
        }
        await event_queue.enqueue_event(
            p.TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=p.Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="Supply risk assessment",
                    parts=[p.Part(text=json.dumps(result))],
                ),
                last_chunk=True,
            )
        )
        await event_queue.enqueue_event(
            p.TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=p.TaskStatus(state=p.TASK_STATE_COMPLETED),
            )
        )
        Store().audit(
            user_id,
            None,
            stage="supply_specialist",
            protocol="a2a",
            trace_id=payload.get("trace_id"),
            status="completed",
            detail={"task_id": task_id, "result": result},
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            p.TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=p.TaskStatus(state=p.TASK_STATE_CANCELED),
            )
        )


card = p.AgentCard(
    name="Supply risk specialist (simulated)",
    description=INSTRUCTIONS,
    version="1.0.0",
    supported_interfaces=[
        p.AgentInterface(
            url=os.getenv("SUPPLY_URL", "http://127.0.0.1:8002") + "/",
            protocol_binding="JSONRPC",
            protocol_version="1.0",
        )
    ],
    capabilities=p.AgentCapabilities(streaming=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        p.AgentSkill(
            id="assess_risk",
            name="Assess product supply risk",
            description="Evaluate inventory, demand, deliveries and delay evidence.",
            tags=["supply", "risk", "inventory"],
        )
    ],
)
handler = DefaultRequestHandler(
    agent_executor=SupplyExecutor(), task_store=InMemoryTaskStore(), agent_card=card
)
app = FastAPI(title=card.name)
app.add_middleware(ServiceAuth, audience="supply")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/", context_builder=IdentityBuilder()),
)


@app.get("/health")
def health():
    return {"status": "ok", "service": card.name}
