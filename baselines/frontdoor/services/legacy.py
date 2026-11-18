from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.store import Store
from services.security import service_user

router = APIRouter(prefix="/legacy")
INVENTORY = {
    "P-100": {
        "product_id": "P-100",
        "available": 80,
        "next_week_demand": 150,
        "expected_delivery": 40,
        "supplier_status": "Delayed",
    },
    "P-200": {
        "product_id": "P-200",
        "available": 240,
        "next_week_demand": 100,
        "expected_delivery": 100,
        "supplier_status": "On schedule",
    },
}


class ServiceRequest(BaseModel):
    title: str
    approval_id: str


class HardwareOrder(BaseModel):
    item: Literal["laptop"]
    approval_id: str


@router.post("/hardware-orders")
def order_hardware(body: HardwareOrder, request: Request):
    user = service_user(request, "operations.write")
    return Store().mutate(
        user["id"], body.approval_id, "operations.order_hardware", {"item": body.item}
    )


@router.get("/hardware-orders/{order_id}")
def get_hardware_order(order_id: str, request: Request):
    user = service_user(request, "operations.read")
    result = Store().get("requests", order_id, user["id"])
    if not result["id"].startswith("HW-"):
        raise HTTPException(404, "Hardware order not found")
    return result


@router.get("/inventory/{product_id}")
def inventory(product_id: str, request: Request):
    service_user(request, "supply.read")
    if product_id not in INVENTORY:
        raise HTTPException(404, "Product not found")
    return {"provider": "Mock legacy ERP", **INVENTORY[product_id]}


@router.get("/requests/{case_id}")
def get_request(case_id: str, request: Request):
    user = service_user(request, "operations.read")
    return Store().get("cases" if case_id.startswith("CASE-") else "requests", case_id, user["id"])


@router.post("/requests")
def create_request(body: ServiceRequest, request: Request):
    user = service_user(request, "operations.write")
    return Store().mutate(
        user["id"], body.approval_id, "operations.create_service_request", {"title": body.title}
    )
