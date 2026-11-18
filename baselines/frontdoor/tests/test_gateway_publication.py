import pytest

from scripts.deploy_gateway import Management, specification


def test_gateway_projection_requires_both_credentials_and_excludes_health():
    schema = specification("https://mock.example")
    operations = [operation for path in schema["paths"].values() for operation in path.values()]
    assert {operation["operationId"] for operation in operations} == {
        "get_inventory_status",
        "get_service_request",
        "create_service_request",
        "order_hardware",
        "get_hardware_order",
    }
    assert all(
        operation["security"] == [{"HTTPBearer": [], "APIKeyHeader": []}]
        for operation in operations
    )
    assert set(schema["components"]["schemas"]) == {"CreateRequest", "HardwareOrder"}
    assert schema["components"]["schemas"]["HardwareOrder"]["properties"]["item"]["enum"] == [
        "laptop"
    ]


def test_gateway_does_not_overwrite_foreign_api():
    calls = []

    def request(method, path, **_):
        calls.append((method, path))
        return {"properties": {"description": "Owned by another application"}}

    management = object.__new__(Management)
    management.request = request
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        management.owned_api("existing", {})
    assert calls == [("GET", "apis/existing")]
