import pytest

from mocks.definitions import result
from mocks.protocol import dispatch


def call(name, arguments, approval_id=None):
    return dispatch(
        {
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
                "_meta": {"capability-registry/context": {"approval_id": approval_id}},
            },
        }
    )


def test_mock_calls_are_deterministic_and_approval_is_observable():
    inputs = {"title": "Synthetic test ticket"}
    assert call("create_ticket", inputs)["structuredContent"] == result("create_ticket", inputs)
    assert call("create_ticket", inputs) == call("create_ticket", inputs)
    assert call("deploy_release", {"release": "v1.2.3"})["isError"]
    approved = call("deploy_release", {"release": "v1.2.3"}, "SYNTHETIC-APPROVAL")
    assert approved["structuredContent"]["synthetic"]
    with pytest.raises(ValueError):
        call("create_ticket", {"title": "bad", "unexpected": True})
