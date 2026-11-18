import pytest

from tools import h4_preflight, h4_resume


@pytest.mark.parametrize(
    "role,broad,expected_calls",
    [("Foundry User", True, 0), ("Reader", True, 1), ("Foundry User", False, 1)],
)
async def test_h4_stops_at_first_breadth_or_alternate_path_failure(
    monkeypatch, tmp_path, role, broad, expected_calls
):
    calls = []

    class Transport:
        def __init__(self, _credential):
            self.evidence = []

        async def list_tools(self, _binding):
            self.evidence.append({"operation": "tools/list", "status": 200})
            return {"server___deploy_release": {}}

        async def call_tool(self, *args, **kwargs):
            calls.append((args, kwargs))
            self.evidence.append({"operation": "tools/call", "status": 200})
            return {"structuredContent": {"synthetic": True}}

        async def aclose(self):
            pass

    monkeypatch.setattr(h4_preflight, "FoundryTransport", Transport)
    monkeypatch.setattr(h4_preflight, "CertificateCredential", lambda *a, **k: object())
    monkeypatch.setattr(h4_preflight, "ROOT", tmp_path)
    experiment = h4_preflight.Preflight()
    monkeypatch.setattr(experiment, "save", lambda: None)
    experiment.state["coder"] = {
        "tenant_id": "tenant",
        "client_id": "client",
        "principal_id": "coder",
        "certificate": "unused.pem",
    }
    experiment.state["role_definitions"] = [
        {
            "name": h4_preflight.ROLE_IDS[-1],
            "properties": {
                "permissions": [
                    {
                        "dataActions": [
                            "Microsoft.CognitiveServices/*"
                            if broad
                            else "Microsoft.CognitiveServices/accounts/AIServices/toolboxes/*"
                        ]
                    }
                ]
            },
        }
    ]
    try:
        with pytest.raises(h4_preflight.SafetyStop, match="fallback|general Toolbox"):
            await experiment.probe({}, role)
        assert len(calls) == expected_calls
        assert experiment.state["role_trials"][-1]["success"]
    finally:
        await experiment.client.aclose()
        await experiment.credential.close()


@pytest.mark.parametrize("status", [200, 401, 404, 429, 500, None])
async def test_resumed_boundary_never_retries_or_accepts_non_403(monkeypatch, status):
    instances = []

    class Transport:
        def __init__(self, _credential):
            self.evidence = []
            instances.append(self)

        base = staticmethod(lambda _: "https://test/mcp")

        async def request(self, *args, **kwargs):
            self.evidence.append({"operation": "tools/call", "status": status})
            if status != 200:
                raise ValueError("Synthetic denial")
            return b"{}", {}

        async def aclose(self):
            pass

    monkeypatch.setattr(h4_resume, "FoundryTransport", Transport)
    monkeypatch.setattr(h4_resume, "CertificateCredential", lambda *a, **k: object())
    experiment = object.__new__(h4_resume.ResumedH4)
    identity = {
        "tenant_id": "tenant",
        "client_id": "client",
        "principal_id": "coder",
        "certificate": "unused.pem",
    }
    experiment.state = {
        "coder": identity,
        "deploy_tool_name": "server___deploy_release",
        "probes": [],
    }
    monkeypatch.setattr(experiment, "save", lambda: None)
    with pytest.raises(h4_preflight.SafetyStop, match="expected 403"):
        await experiment.probe_as(
            identity,
            {"risk_tier": "privileged"},
            "direct",
            direct=True,
            expected=403,
        )
    assert len(instances) == 1
    assert len(instances[0].evidence) == 1


def test_split_publication_keeps_original_composition_immutable():
    import copy

    source = {
        "tools": [
            {
                "type": "mcp",
                "name": "server",
                "server_label": "server",
                "project_connection_id": "general-connection",
            }
        ],
        "skills": [{"type": "skill_reference", "name": "shared", "version": "1"}],
    }
    before = copy.deepcopy(source)
    general = h4_resume.publication_body(source)
    privileged = h4_resume.publication_body(source, privileged=True)
    assert general["skills"] == source["skills"]
    assert set(general["tools"][0]["allowed_tools"]) == {
        "get_build_status",
        "create_ticket",
        "order_hardware",
    }
    assert privileged["skills"] == []
    assert privileged["tools"][0]["allowed_tools"] == ["deploy_release"]
    assert privileged["tools"][0]["require_approval"] == "always"
    assert source == before
