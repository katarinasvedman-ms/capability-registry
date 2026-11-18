import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def environment(tmp_path_factory):
    for port in (18001, 18002, 18003):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                pytest.fail(f"Test port {port} is occupied; run backend test suites sequentially")
    root = tmp_path_factory.mktemp("frontdoor")
    settings = {
        "FRONTDOOR_DB": str(root / "test.db"),
        "SERVICE_SECRET": "test-only-service-secret",
        "CATALOG_PATH": str(root / "catalog.json"),
        "DEMO_MODE": "mock",
        "AUTH_MODE": "mock",
        "KNOWLEDGE_PROVIDER": "mock",
        "SUPPLY_PROVIDER": "mock",
        "ESS_PROVIDER": "mock",
        "GATEWAY_PROVIDER": "mock",
        "CATALOG_PROVIDER": "local",
        "GATEWAY_URL": "http://127.0.0.1:18001",
        "SUPPLY_URL": "http://127.0.0.1:18002",
        "ESS_URL": "http://127.0.0.1:18003",
    }
    previous = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    from backend.delegation import initialize_signing_key

    initialize_signing_key()
    shutil.copyfile(ROOT / "backend" / "catalog.json", settings["CATALOG_PATH"])
    children, logs = [], []
    try:
        for name, port in [("gateway", 18001), ("supply", 18002), ("ess", 18003)]:
            log = (root / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        f"services.{name}:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=ROOT,
                    env=os.environ.copy(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        for port in (18001, 18002, 18003):
            for _ in range(100):
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"Service {port} failed to start; logs: {root}")
        yield settings
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=10)
        for log in logs:
            log.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def client(environment):
    from backend.app import app, windows

    windows.clear()
    with TestClient(app) as client:
        yield client


def headers(client, user="alex"):
    response = client.post("/api/session", json={"user_id": user})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def chat(client, auth, message, conversation_id=None):
    response = client.post(
        "/api/chat",
        headers=auth,
        json={
            "message": message,
            "conversation_id": conversation_id,
        },
    )
    assert response.status_code == 200, response.text
    import json

    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    errors = [event for event in events if event["type"] == "error"]
    assert not errors, errors
    result = next(event for event in events if event["type"] == "result")
    conv_id = next(event["conversation_id"] for event in events if event["type"] == "conversation")
    return result, conv_id, events
