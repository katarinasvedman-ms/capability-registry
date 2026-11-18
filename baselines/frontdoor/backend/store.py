import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from backend.config import database_path

audit_logger = logging.getLogger("frontdoor.audit")
if not audit_logger.handlers:
    audit_handler = logging.StreamHandler()
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False

USERS = {
    "alex": {
        "id": "alex",
        "name": "Alex Morgan",
        "role": "Demo manager",
        "country": "Sweden",
        "manager": "Jordan Lee",
        "team": "Store Operations",
        "permissions": [
            "knowledge.read",
            "context.read",
            "leave.read",
            "leave.write",
            "supply.read",
            "operations.read",
            "operations.write",
            "operations.admin",
        ],
    },
    "sam": {
        "id": "sam",
        "name": "Sam Taylor",
        "role": "Restricted employee",
        "country": "Sweden",
        "manager": "Robin Chen",
        "team": "Customer Support",
        "permissions": ["knowledge.read", "context.read", "leave.read", "operations.read"],
    },
}


def digest(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(
                secret in key.lower()
                for secret in (
                    "token",
                    "secret",
                    "authorization",
                    "password",
                    "manager",
                    "employee_name",
                )
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [redacted]", value)
        value = re.sub(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
            "[redacted token]",
            value,
        )
        return re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[redacted email]", value)
    return value


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or database_path()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                  kind TEXT, id TEXT, owner TEXT, data TEXT,
                  PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, conversation_id TEXT,
                  data TEXT);
            """)
        for user_id, user in USERS.items():
            self.put("users", user_id, user_id, user, only_new=True)
            self.put("balances", user_id, user_id, {"remaining_days": 25}, only_new=True)
        for case_id, owner, title in [
            ("CASE-1042", "alex", "Store scanner replacement"),
            ("CASE-1043", "sam", "Support workstation inspection"),
            ("CASE-1044", "alex", "Warehouse equipment service"),
        ]:
            self.put(
                "cases",
                case_id,
                owner,
                {
                    "id": case_id,
                    "title": title,
                    "status": "In progress",
                    "details": "Synthetic case. Assigned to the local Operations support team.",
                },
                only_new=True,
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, key, owner, value, only_new=False):
        clause = "OR IGNORE" if only_new else "OR REPLACE"
        with self.connect() as db:
            db.execute(
                f"INSERT {clause} INTO records VALUES (?,?,?,?)",
                (kind, key, owner, json.dumps(value)),
            )
        return value

    def get(self, kind, key, owner=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT owner,data FROM records WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
        if not row or (owner is not None and row["owner"] != owner):
            raise HTTPException(404, "Record not found")
        return json.loads(row["data"])

    def list(self, kind, owner):
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM records WHERE kind=? AND owner=? ORDER BY rowid DESC",
                (kind, owner),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def user(self, user_id):
        return self.get("users", user_id, user_id)

    def audit(self, user_id, conversation_id, **event):
        event = redact({"timestamp": time.time(), **event})
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(owner,conversation_id,data) VALUES (?,?,?)",
                (user_id, conversation_id, json.dumps(event)),
            )
        audit_logger.info(json.dumps({"event": "audit", **event}))
        return event

    def audits(self, user_id, conversation_id=None):
        with self.connect() as db:
            rows = db.execute(
                """SELECT data FROM audit WHERE owner=?
                AND (? IS NULL OR conversation_id=?) ORDER BY id DESC LIMIT 200""",
                (user_id, conversation_id, conversation_id),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def new_conversation(self, user_id, title):
        key = str(uuid.uuid4())
        return self.put(
            "conversations",
            key,
            user_id,
            {
                "id": key,
                "title": title[:70],
                "messages": [],
                "pending": None,
                "last_request": None,
                "draft": {},
            },
        )

    def approval(self, user_id, conversation_id, capability_id, inputs, execution_target=None):
        key = str(uuid.uuid4())
        return self.put(
            "approvals",
            key,
            user_id,
            {
                "id": key,
                "conversation_id": conversation_id,
                "capability_id": capability_id,
                "inputs": inputs,
                "input_hash": digest(inputs),
                "execution_target": execution_target,
                "status": "pending",
                "created_at": time.time(),
                "result": None,
            },
        )

    def transition(self, approval_id, user_id, expected, target):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM records WHERE kind='approvals' AND id=? AND owner=?",
                (approval_id, user_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Approval not found")
            approval = json.loads(row["data"])
            if approval["status"] not in expected:
                raise HTTPException(409, f"Approval is {approval['status']}")
            if target in {"approved", "executing"} and time.time() - approval["created_at"] > 1800:
                raise HTTPException(409, "Approval expired; propose the action again")
            approval["status"] = target
            db.execute(
                "UPDATE records SET data=? WHERE kind='approvals' AND id=?",
                (json.dumps(approval), approval_id),
            )
        return approval

    def recover_runtime(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT kind,id,owner,data FROM records WHERE kind IN ('tasks','approvals')"
            ).fetchall()
            for row in rows:
                value = json.loads(row["data"])
                if row["kind"] == "tasks" and value["status"] == "running":
                    value.update(
                        status="interrupted", error="Backend restarted; inspect saved state"
                    )
                elif row["kind"] == "approvals" and value["status"] == "executing":
                    mutation = db.execute(
                        "SELECT data FROM records WHERE kind='mutations' AND id=? AND owner=?",
                        (row["id"], row["owner"]),
                    ).fetchone()
                    value.update(
                        status="completed" if mutation else "failed",
                        result=json.loads(mutation["data"]) if mutation else None,
                    )
                else:
                    continue
                db.execute(
                    "UPDATE records SET data=? WHERE kind=? AND id=?",
                    (json.dumps(value), row["kind"], row["id"]),
                )

    def finish_approval(self, approval_id, user_id, result):
        approval = self.get("approvals", approval_id, user_id)
        approval.update(status="completed", result=result)
        self.put("approvals", approval_id, user_id, approval)

    def require_execution(self, user_id, approval_id, capability_id, inputs):
        approval = self.get("approvals", approval_id, user_id)
        expected_targets = {
            "people.request_leave": {"adapter": "published_agent", "tool": "leave"},
            "operations.create_service_request": {
                "adapter": "mcp",
                "tool": "create_service_request",
            },
            "operations.order_hardware": {"adapter": "mcp", "tool": "order_hardware"},
        }
        same_operation = approval["capability_id"] == capability_id or (
            expected_targets.get(capability_id) is not None
            and approval.get("execution_target") == expected_targets[capability_id]
        )
        if (
            approval["status"] not in {"executing", "completed"}
            or not same_operation
            or approval["input_hash"] != digest(inputs)
            or time.time() - approval["created_at"] > 1800
        ):
            raise HTTPException(403, "No matching approved execution")
        return approval

    def mutate(self, user_id, approval_id, capability_id, inputs):
        self.require_execution(user_id, approval_id, capability_id, inputs)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM records WHERE kind='mutations' AND id=?", (approval_id,)
            ).fetchone()
            if row:
                return json.loads(row["data"])
            if capability_id == "people.request_leave":
                from datetime import date

                start = date.fromisoformat(inputs["start_date"])
                end = date.fromisoformat(inputs["end_date"])
                if end < start or start < date.today():
                    raise HTTPException(422, "Leave dates must be ordered and not in the past")
                from datetime import timedelta

                days = sum(
                    (start + timedelta(days=n)).weekday() < 5 for n in range((end - start).days + 1)
                )
                balance_row = db.execute(
                    "SELECT data FROM records WHERE kind='balances' AND id=?", (user_id,)
                ).fetchone()
                balance = json.loads(balance_row["data"])
                if days < 1 or days > balance["remaining_days"]:
                    raise HTTPException(
                        422, "Invalid working-day count or insufficient leave balance"
                    )
                balance["remaining_days"] -= days
                db.execute(
                    "UPDATE records SET data=? WHERE kind='balances' AND id=?",
                    (json.dumps(balance), user_id),
                )
                result = {
                    "id": f"LEAVE-{approval_id[:8].upper()}",
                    "status": "Submitted",
                    **inputs,
                    "working_days": days,
                    **balance,
                }
            elif capability_id == "operations.order_hardware":
                result = {
                    "id": f"HW-{approval_id[:8].upper()}",
                    "status": "Submitted",
                    "provider": "mock_erp_synthetic",
                    **inputs,
                }
            else:
                result = {"id": f"SR-{approval_id[:8].upper()}", "status": "Open", **inputs}
            db.execute(
                "INSERT INTO records VALUES ('mutations',?,?,?)",
                (approval_id, user_id, json.dumps(result)),
            )
            db.execute(
                "INSERT INTO records VALUES ('requests',?,?,?)",
                (result["id"], user_id, json.dumps(result)),
            )
        return result
