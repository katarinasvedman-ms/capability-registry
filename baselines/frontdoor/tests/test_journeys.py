import json
from datetime import date, timedelta
from pathlib import Path

import httpx

from tests.conftest import chat, headers


def test_know_sources_and_work_isolation(client):
    alex = headers(client)
    result, conv, events = chat(client, alex, "What is our annual leave policy in Sweden?")
    assert "25" in result["content"]
    assert result["citations"][0]["provenance"] == "mock_foundry_iq"
    assert any(event["type"] == "delta" for event in events)
    sam = headers(client, "sam")
    result, _, _ = chat(client, sam, "Who is my manager and what team am I in?")
    assert "Robin Chen" in result["content"]
    assert "Jordan Lee" not in result["content"]
    assert "synthetic team planning meeting" in result["content"]
    assert result["citations"][0]["provenance"] == "mock_work_iq"
    assert client.get(f"/api/conversations/{conv}", headers=sam).status_code == 404
    assert client.get(f"/api/audit?conversation_id={conv}", headers=sam).status_code == 404


def test_leave_correction_approval_and_exactly_once(client):
    auth = headers(client)
    result, conv, _ = chat(client, auth, "Request annual leave next week.")
    first = result["approval"]
    assert first["status"] == "pending"
    assert any(doc["id"] == "HR-SE-001" for doc in result["citations"])
    assert any(doc["provenance"] == "mock_work_iq" for doc in result["citations"])
    result, _, _ = chat(client, auth, "Actually start Wednesday.", conv)
    second = result["approval"]
    assert second["id"] != first["id"]
    assert date.fromisoformat(second["inputs"]["start_date"]).weekday() == 2
    assert (
        client.post(
            f"/api/approvals/{first['id']}", headers=auth, json={"approved": True}
        ).status_code
        == 409
    )
    result, _, _ = chat(client, auth, "Confirm", conv)
    assert "confirmation card" in result["content"]
    assert (
        client.post(
            f"/api/approvals/{second['id']}", headers=auth, json={"approved": True}
        ).status_code
        == 200
    )
    result, _, _ = chat(client, auth, "Confirm", conv)
    assert "LEAVE-" in result["content"]
    first_result = result["content"]
    result, _, _ = chat(client, auth, "Confirm", conv)
    assert result["content"] == first_result
    result, _, _ = chat(client, auth, "Show me my leave request.", conv)
    assert "LEAVE-" in result["content"]


def test_hardware_order_approval_mcp_retry_read_and_isolation(client):
    auth = headers(client)
    db = client.app.state.store
    before = len(db.list("mutations", "alex"))
    result, conv, _ = chat(client, auth, "I need a new laptop.")
    approval = result["approval"]
    assert approval["capability_id"] == "operations.order_hardware"
    assert approval["inputs"] == {"item": "laptop"}
    assert len(db.list("mutations", "alex")) == before
    client.post(f"/api/approvals/{approval['id']}", headers=auth, json={"approved": True})
    result, _, events = chat(client, auth, "Confirm", conv)
    order = db.get("approvals", approval["id"], "alex")["result"]
    assert order["id"].startswith("HW-") and order["item"] == "laptop"
    assert any(e.get("protocol") == "mcp" and e.get("status") == "completed" for e in events)
    repeated, _, _ = chat(client, auth, "Confirm", conv)
    assert repeated["content"] == result["content"]
    shown, _, events = chat(client, auth, "Show my hardware order.", conv)
    assert order["id"] in shown["content"]
    assert any(e.get("capability_id") == "operations.get_hardware_order" for e in events)
    assert len(db.list("mutations", "alex")) == before + 1
    denied = client.post(
        "/api/capabilities/invoke",
        headers=headers(client, "sam"),
        json={"capability_id": "operations.order_hardware", "inputs": {"item": "laptop"}},
    )
    assert denied.status_code == 404
    hidden = client.post(
        "/api/capabilities/invoke",
        headers=headers(client, "sam"),
        json={
            "capability_id": "operations.get_hardware_order",
            "inputs": {"order_id": order["id"]},
        },
    )
    assert hidden.status_code == 502


def test_concur_handoff_navigation_and_application_authorization(client):
    auth = headers(client)
    result, _, events = chat(client, auth, "I need to submit my expenses in Concur.")
    assert result["handoff"]["url"] == "/lob/concur"
    assert result["handoff"]["application"] == "concur"
    assert result["approval"] is None
    assert "not transferred" in result["content"]
    assert any(e.get("stage") == "discovery" for e in events)
    assert any(e.get("protocol") == "handoff" for e in events)
    assert client.get("/api/lob/expenses").status_code == 401
    assert client.get("/api/lob/expenses", headers=auth).json()["status"] == "Sample draft"
    db = client.app.state.store
    profile = db.user("sam")
    db.put("users", "sam", "sam", {**profile, "permissions": ["knowledge.read"]})
    try:
        sam = headers(client, "sam")
        assert client.get("/api/lob/expenses", headers=sam).status_code == 403
        assert (
            client.post(
                "/api/capabilities/invoke",
                headers=sam,
                json={"capability_id": "operations.open_expenses", "inputs": {}},
            ).status_code
            == 404
        )
    finally:
        db.put("users", "sam", "sam", profile)


def test_operations_real_mcp_approval_and_audit(client):
    auth = headers(client)
    result, conv, _ = chat(client, auth, "Create a service request for a broken store scanner.")
    approval = result["approval"]
    assert approval["capability_id"] == "operations.create_service_request"
    client.post(f"/api/approvals/{approval['id']}", headers=auth, json={"approved": True})
    result, _, events = chat(client, auth, "Confirm", conv)
    assert "SR-" in result["content"]
    executions = [event for event in events if event.get("protocol") == "mcp"]
    assert any(event.get("status") == "completed" for event in executions)
    audit = client.get(f"/api/audit?conversation_id={conv}", headers=auth).json()
    assert any(
        event.get("detail", {}).get("response", {}).get("id", "").startswith("SR-")
        for event in audit
        if isinstance(event.get("detail"), dict)
    )
    assert "test-only-service-secret" not in json.dumps(audit)
    result, _, _ = chat(client, auth, "Show the request", conv)
    assert "SR-" in result["content"]


def test_supply_real_a2a_artifact_and_task_status(client):
    result, _, events = chat(
        client, headers(client), "Assess next week's supply risk for product P-100."
    )
    assert '"risk": "High"' in result["content"]
    assert result["citations"][0]["id"] == "SUPPLY-P-100"
    assert any(event.get("protocol") == "a2a" for event in events)
    assert any(event.get("stage") == "delegation" for event in events)


def test_handoff_case_permission(client):
    auth = headers(client)
    result, _, _ = chat(client, auth, "Open case CASE-1042 in the internal system.")
    assert result["handoff"]["url"] == "/lob/cases/CASE-1042"
    assert client.get("/api/lob/cases/CASE-1042", headers=auth).json()["id"] == "CASE-1042"
    assert client.get("/api/lob/cases/CASE-1042", headers=headers(client, "sam")).status_code == 404
    assert client.get("/api/lob/cases/CASE-9999", headers=auth).status_code == 404
    assert client.get("/api/runtime", headers=auth).json()["demo_case_id"] == "CASE-1042"
    assert (
        client.get("/api/runtime", headers=headers(client, "sam")).json()["demo_case_id"]
        == "CASE-1043"
    )


def test_restricted_identity_cannot_discover_inspect_or_invoke(client):
    auth = headers(client, "sam")
    matches = client.post(
        "/api/capabilities/search", headers=auth, json={"goal": "Admin-only Operations report"}
    ).json()
    assert all(match["id"] != "operations.admin_report" for match in matches)
    assert client.get("/api/capabilities/operations.admin_report", headers=auth).status_code == 404
    assert (
        client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={
                "capability_id": "operations.admin_report",
                "inputs": {},
            },
        ).status_code
        == 404
    )
    assert client.post("/api/catalog/reload", headers=auth).status_code == 403
    assert (
        client.post(
            "/api/session",
            json={
                "user_id": "sam",
                "permissions": ["operations.admin"],
            },
        ).status_code
        == 422
    )


def test_catalog_addition_without_orchestrator_change(client, environment):
    auth = headers(client)
    path = Path(environment["CATALOG_PATH"])
    original = path.read_text()
    try:
        entries = json.loads(original)
        entry = dict(next(item for item in entries if item["id"] == "supply.check_inventory"))
        entry.update(
            id="supply.warehouse_stock.v1",
            name="Warehouse stock lookup",
            description="Retrieve warehouse stock",
            examples=["warehouse stock"],
        )
        entries.append(entry)
        path.write_text(json.dumps(entries))
        assert client.post("/api/catalog/reload", headers=auth).status_code == 200
        matches = client.post(
            "/api/capabilities/search", headers=auth, json={"goal": "Warehouse stock lookup"}
        ).json()
        assert matches[0]["id"] == entry["id"]
        response = client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={
                "capability_id": entry["id"],
                "inputs": {"product_id": "P-100"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["available"] == 80
        write_entry = dict(
            next(item for item in entries if item["id"] == "operations.create_service_request")
        )
        write_entry.update(id="operations.scanner_repair.v2", name="Scanner repair request")
        entries.append(write_entry)
        path.write_text(json.dumps(entries))
        assert client.post("/api/catalog/reload", headers=auth).status_code == 200
        body = {"capability_id": write_entry["id"], "inputs": {"title": "Synthetic scanner repair"}}
        proposal = client.post("/api/capabilities/invoke", headers=auth, json=body).json()
        approval = proposal["approval"]
        client.post(f"/api/approvals/{approval['id']}", headers=auth, json={"approved": True})
        response = client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={
                **body,
                "conversation_id": approval["conversation_id"],
                "approval_id": approval["id"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"].startswith("SR-")
    finally:
        path.write_text(original)
        client.post("/api/catalog/reload", headers=auth)


def test_typed_inputs_deprecation_and_missing_dates(client):
    auth = headers(client)
    assert (
        client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={
                "capability_id": "supply.check_inventory",
                "inputs": {"product_id": "../bad"},
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={
                "capability_id": "operations.old_inventory",
                "inputs": {"product_id": "P-100"},
            },
        ).status_code
        == 410
    )
    result, conv, _ = chat(client, auth, "Request leave")
    assert "Which dates" in result["content"]
    start = date.today() + timedelta(days=14 - date.today().weekday())
    end = start + timedelta(days=1)
    result, _, _ = chat(client, auth, f"{start.isoformat()} to {end.isoformat()}", conv)
    assert result["approval"]["inputs"]["start_date"] == start.isoformat()


def test_invalid_catalog_reload_keeps_previous_registry(client, environment):
    auth = headers(client)
    path = Path(environment["CATALOG_PATH"])
    original = path.read_text()
    before = client.get("/api/capabilities", headers=auth).json()
    try:
        for field, value in [
            ("approval_required", False),
            ("protocol", "a2a"),
            ("input_schema", {"type": "unknown"}),
        ]:
            entries = json.loads(original)
            entry = next(item for item in entries if item["id"] == "people.request_leave")
            entry[field] = value
            path.write_text(json.dumps(entries))
            response = client.post("/api/catalog/reload", headers=auth)
            assert response.status_code == 422
            assert "Previous catalog remains active" in response.json()["detail"]
            assert client.get("/api/capabilities", headers=auth).json() == before
        path.write_text("{}")
        assert client.post("/api/catalog/reload", headers=auth).status_code == 422
        assert client.get("/api/capabilities", headers=auth).json() == before
    finally:
        path.write_text(original)


def test_a2a_alias_keeps_selected_capability_in_delegation_trace(client, environment):
    auth = headers(client)
    path = Path(environment["CATALOG_PATH"])
    original = path.read_text()
    try:
        entries = json.loads(original)
        alias = dict(next(item for item in entries if item["id"] == "supply.assess_risk"))
        alias.update(id="supply.disruption_review.v1", name="Review supply disruption")
        entries.append(alias)
        path.write_text(json.dumps(entries))
        assert client.post("/api/catalog/reload", headers=auth).status_code == 200
        response = client.post(
            "/api/capabilities/invoke",
            headers=auth,
            json={"capability_id": alias["id"], "inputs": {"product_id": "P-100"}},
        )
        assert response.status_code == 200
        assert response.json()["risk"] == "High"
        audit = client.get("/api/audit", headers=auth).json()
        delegation = [
            event
            for event in audit
            if event["stage"] == "delegation"
            and event["trace_id"]
            == next(
                event["trace_id"]
                for event in audit
                if event.get("capability_id") == alias["id"] and event["stage"] == "execution"
            )
        ]
        assert delegation
        assert all(event["capability_id"] == alias["id"] for event in delegation)
    finally:
        path.write_text(original)
        client.post("/api/catalog/reload", headers=auth)


def test_direct_services_reject_bypass(environment):
    for service in ("GATEWAY_URL", "ESS_URL", "SUPPLY_URL"):
        response = httpx.post(environment[service] + "/invoke", json={})
        assert response.status_code == 401


def test_unknown_goal_asks_for_clarification(client):
    result, _, _ = chat(client, headers(client), "flibbertigibbet")
    assert "clarify" in result["content"].lower()
