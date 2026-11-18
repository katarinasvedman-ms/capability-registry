import copy
import json

import pytest

from tools.assess_gate import (
    assess,
    governance_supported,
    h4_boundary_supported,
    model_evaluation_complete,
)
from tools.capture_sources import ROOT

SCHEMA = json.loads((ROOT / "contracts" / "capability.schema.json").read_text())


def test_model_measurement_completion_requires_every_frozen_case_and_repetition():
    case = {
        "id": "fixture",
        "metric": "frontdoor_top1",
        "split": "held_out",
        "expected": [],
        "optional": [],
    }
    outcome = {"ids": ["wrong"], "correct": False, "strict_correct": False, "elapsed_ms": 1}
    evidence = {
        "started_at": "2026-11-17T10:00:00+00:00",
        "completed_at": "2026-11-17T10:01:00+00:00",
        "settings": {"repetitions": 2, "max_requests": 120},
        "requests": 2,
        "http": [{}, {}],
        "results": [
            {
                **case,
                "repetition": repetition,
                **{mode: outcome.copy() for mode in ("static", "bm25", "model")},
            }
            for repetition in range(2)
        ],
    }
    assert model_evaluation_complete(evidence, {"cases": [case]})
    evidence["results"][1]["repetition"] = 0
    assert not model_evaluation_complete(evidence, {"cases": [case]})


def test_model_measurement_completion_rejects_modified_labels_or_missing_output():
    case = {
        "id": "fixture",
        "metric": "frontdoor_top1",
        "split": "held_out",
        "expected": [],
        "optional": [],
    }
    evidence = {
        "started_at": "2026-11-17T10:00:00+00:00",
        "completed_at": "2026-11-17T10:01:00+00:00",
        "settings": {"repetitions": 1, "max_requests": 120},
        "requests": 1,
        "http": [{}],
        "results": [
            {
                **case,
                "repetition": 0,
                **{
                    mode: {"ids": [], "correct": True, "strict_correct": True, "elapsed_ms": 1}
                    for mode in ("static", "bm25", "model")
                },
            }
        ],
    }
    assert model_evaluation_complete(evidence, {"cases": [case]})
    evidence["results"][0]["expected"] = ["modified-label"]
    assert not model_evaluation_complete(evidence, {"cases": [case]})
    evidence["results"][0]["expected"] = []
    del evidence["results"][0]["model"]
    assert not model_evaluation_complete(evidence, {"cases": [case]})


def governance_evidence():
    rows = []
    for capability in ("mock.deploy_release", "mock.create_ticket"):
        rows.append(
            {
                "capability_id": capability,
                "test1": {"governance": {"audit": [{"lease_id": capability}]}},
                "positive_mock": [{"accepted": True, "lease_id": capability}],
                **dict.fromkeys(
                    (
                        "test2_middleware_status",
                        "test2_gateway_status",
                        "test3_replay",
                        "test4_expired",
                        "test5_missing",
                        "test6_status",
                    ),
                    403,
                ),
            }
        )
    return {
        "h6": rows,
        "h6_passed": "measured",
        "h7_passed": "measured",
        "before": {"pin": "same"},
        "final_controls": {"pin": "same"},
        "general_before": {"accepted": True},
        "general_after": {
            "accepted": False,
            "exposure_expected": True,
            "http": [{"operation": "tools/call", "response": "downstream HTTP 403"}],
        },
        "retired_tunnel_status": 404,
        "http": [
            {
                "url": "https://test/projects/test-privileged/toolboxes/tool/versions/1/mcp",
                "status": 403,
                "request": {"method": "tools/call"},
            }
        ],
        "h7": [
            {
                "carriage": mode,
                "mock": [
                    {
                        "accepted": True,
                        "lease_id": mode,
                        "request": {"params": {"arguments": {"title": "Synthetic"}}},
                    }
                ],
                "gateway": [{"carriage": mode, "lease_id": mode}],
            }
            for mode in ("header", "reserved")
        ],
        "h7_a2a": {"received": [{"accepted": True}]},
    }


def test_governance_gate_rejects_mutation_success_even_with_passing_timestamp():
    evidence = governance_evidence()
    assert governance_supported(evidence) == (True, True)
    evidence["h6"][0]["test2_gateway_status"] = 200
    assert governance_supported(evidence) == (False, False)


def test_governance_gate_rejects_leaked_reserved_lease():
    evidence = governance_evidence()
    evidence["h7"][1]["mock"][0]["request"]["params"]["arguments"]["_capability_lease"] = "leak"
    assert governance_supported(evidence) == (True, False)


def simulated_evidence():
    return {
        "frontdoor": {
            "source_fit": {"supported": True, "counterexamples": []},
            "observations": {"domain_contract_parity": {"equal_filtered_results": True}},
            "parity": {"supported": True},
        },
        "h3_unchanged": True,
        "selection": {
            "provider": {"canonical_equal": True},
            "bridge": {"coder": {"equal": True}, "tester": {"equal": True}},
        },
        "maf": {
            "supported": True,
            "observations": [
                {
                    "id": name,
                    "completed": True,
                    "mock_received": [{"request": {"method": "tools/call"}}],
                    "http": [{"operation": "tools/call", "status": 200}],
                }
                for name in (
                    "mock.get_build_status",
                    "mock.create_ticket",
                    "mock.deploy_release",
                    "operations.order_hardware",
                )
            ],
            "core_audit": [
                {
                    "entry_id": name,
                    "pin_verified": True,
                    "outcome": "completed",
                }
                for name in (
                    "mock.get_build_status",
                    "mock.create_ticket",
                    "mock.deploy_release",
                    "operations.order_hardware",
                )
            ],
        },
        "sdk": {
            "supported": True,
            "modes": [
                {
                    "mode": mode,
                    "selected": ["test-strategy"],
                    "native_activation": True,
                    "core_audit": [
                        {
                            "entry_id": "test-strategy",
                            "pin_verified": True,
                            "outcome": "completed",
                        }
                    ],
                    "activations": [{"type": "skill.invoked"}],
                }
                for mode in ("bridge", "provider")
            ],
        },
    }


def fixture_bindings(catalog, binding):
    return {
        entry["id"]: {
            **copy.deepcopy(binding),
            "risk_tier": entry["risk_tier"],
            **(
                {
                    "project_endpoint": json.loads(
                        (ROOT / "catalog" / "project-tiers.json").read_text()
                    )["privileged"][0]
                }
                if entry["risk_tier"] == "privileged"
                else {}
            ),
        }
        for entry in catalog
        if entry["status"] == "approved" and entry["protocol"] in {"mcp", "mcp-resource"}
    }


def test_source_loss_blocks_gate_despite_other_evidence(imported_catalog, binding):
    evidence = simulated_evidence()
    bindings = fixture_bindings(imported_catalog, binding)
    assert assess(imported_catalog, SCHEMA, bindings, evidence)["gate"] == "open"
    evidence["frontdoor"]["source_fit"]["counterexamples"] = [{"field": "type"}]
    result = assess(imported_catalog, SCHEMA, bindings, evidence)
    assert result["gate"] == "blocked"
    assert result["conditions"]["only_allowed_readiness_causes"]
    assert result["conditions"]["live_maf_call_tool"]
    assert result["conditions"]["live_sdk_activation_through_core"]


def test_h4_safety_stop_does_not_rewrite_passing_entry_hypotheses(imported_catalog, binding):
    evidence = simulated_evidence()
    evidence["h4"] = {"safety_stop": True}
    result = assess(imported_catalog, SCHEMA, fixture_bindings(imported_catalog, binding), evidence)
    assert result["h4_entry_gate"] == "open"
    assert result["gate"] == "blocked"
    assert result["hypotheses"]["H1"] == result["hypotheses"]["H2"] == "supported"
    assert result["blockers"] == ["no_h4_safety_stop"]


def test_blocked_h4_positive_control_cannot_authorize_more_provisioning(imported_catalog, binding):
    evidence = simulated_evidence()
    evidence["h4"] = {"status": "blocked", "safety_stop": False}
    result = assess(imported_catalog, SCHEMA, fixture_bindings(imported_catalog, binding), evidence)
    assert result["h4_entry_gate"] == "open"
    assert result["gate"] == "blocked"
    assert result["hypotheses"]["H4"] == "partial"


@pytest.mark.parametrize(
    "missing", ["event", "verified_pin", "binding", "tool", "unchanged", "frontdoor"]
)
def test_incomplete_proof_or_readiness_cannot_open_gate(imported_catalog, binding, missing):
    evidence = simulated_evidence()
    bindings = fixture_bindings(imported_catalog, binding)
    if missing == "event":
        evidence["sdk"]["modes"][0]["activations"] = []
    elif missing == "verified_pin":
        evidence["sdk"]["modes"][0]["core_audit"][0]["pin_verified"] = False
    elif missing == "binding":
        del bindings["dotnet-development"]
    elif missing == "tool":
        evidence["maf"]["observations"].pop()
    elif missing == "unchanged":
        evidence["h3_unchanged"] = False
    else:
        evidence["frontdoor"]["parity"]["supported"] = False
    assert assess(imported_catalog, SCHEMA, bindings, evidence)["gate"] == "blocked"


@pytest.mark.parametrize("failure", ["wrong_http", "early_repeat", "no_approval_leak", "no_audit"])
def test_boundary_support_requires_raw_denials_propagation_and_approval_evidence(failure):
    probes = []
    for label, code in {
        "coder general v3": 200,
        "coder privileged initialize": 403,
        "coder privileged direct call": 403,
        "release general v3": 200,
        "release privileged": 200,
        "repeat coder general v3": 200,
        "repeat coder privileged initialize": 403,
        "repeat coder privileged direct call": 403,
    }.items():
        probes.append(
            {
                "label": label,
                "status": code,
                "http": (
                    [
                        {"operation": "initialize", "status": 200},
                        {"operation": "tools/list", "status": 200},
                    ]
                    if code == 200
                    else [
                        {
                            "operation": "tools/call" if "direct call" in label else "initialize",
                            "status": 403,
                        }
                    ]
                ),
                "tools": {
                    name: {}
                    for name in (
                        "server___get_build_status",
                        "server___create_ticket",
                        "server___order_hardware",
                    )
                },
            }
        )
    state = {
        "probes": probes,
        "matrix_completed_at": "2026-11-16T12:00:00+00:00",
        "repeat_started_at": "2026-11-16T12:15:00+00:00",
        "pin_controls": {"passed": True},
        "bridge": {"native_activation": True, "temporary_directory_removed": True},
    }
    maf = {
        "without_approval": {"terminated_before_dispatch": True, "mock_received": [], "http": []},
        "with_approval": {
            "deploy_calls": [{"request": {}}],
            "http": [{"operation": "tools/call", "status": 200}],
            "core_audit": [{"pin_verified": True, "outcome": "completed"}],
        },
    }
    negative = {"rejected": True, "exit_code": 1}
    assert h4_boundary_supported(state, maf, negative)
    if failure == "wrong_http":
        probes[-1]["http"][-1]["status"] = 404
    elif failure == "early_repeat":
        state["repeat_started_at"] = "2026-11-16T12:14:59+00:00"
    elif failure == "no_approval_leak":
        maf["without_approval"]["mock_received"] = [{"request": {"method": "tools/call"}}]
    else:
        maf["with_approval"]["core_audit"] = []
    assert not h4_boundary_supported(state, maf, negative)
