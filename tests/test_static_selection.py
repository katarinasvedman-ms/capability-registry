import copy
import json

import pytest
from ai_factory.contracts import canonical
from ai_factory.provider import ProviderSnapshot

from selection import SelectionError, select
from tools.capture_sources import ROOT


def context(role="coder", technologies=("dotnet",), **kwargs):
    return {
        "role": role,
        "technologies": [*technologies, "copilot-sdk-python-1"],
        "tasks": ["implementation", "testing"],
        **kwargs,
    }


def test_provider_parity_without_reusing_permitted_or_required_maps(reviewed_catalog):
    policy = json.loads(
        (ROOT / "baselines" / "ai-factory" / "examples" / "provider-policy.json").read_text()
    )
    records = {entry["id"]: {} for entry in reviewed_catalog if entry["type"] == "skill"}
    baseline = ProviderSnapshot(records, {}).assignments(policy, ["coder", "tester"])
    metadata_only_policy = {"allowed_skills": policy["allowed_skills"]}
    selected = {
        role: select(
            context(role),
            reviewed_catalog,
            metadata_only_policy,
        ).candidate_ids
        for role in ("coder", "tester")
    }
    assert canonical(selected) == canonical(baseline)
    assert selected == {
        "coder": ["dotnet-development", "test-strategy"],
        "tester": ["dotnet-development", "test-strategy"],
    }


def test_unknown_controls_fail_closed(imported_catalog):
    imported_catalog = copy.deepcopy(imported_catalog)
    for entry in imported_catalog:
        entry["risk_tier"] = None
    skills = [entry["id"] for entry in imported_catalog if entry["type"] == "skill"]
    with pytest.raises(SelectionError, match="No approved"):
        select(
            context(),
            imported_catalog,
            {"allowed_skills": skills},
        )


def test_existing_provider_policy_remains_a_ceiling(reviewed_catalog):
    allowed = [entry["id"] for entry in reviewed_catalog if entry["type"] == "skill"]
    with pytest.raises(SelectionError, match="exceeds"):
        select(
            context(technologies=["java"]),
            reviewed_catalog,
            {"allowed_skills": allowed, "permitted_skills": {"coder": ["test-strategy"]}},
        )


def test_cycles_permissions_and_privileged_entries_are_not_bypassed(reviewed_catalog):
    catalog = copy.deepcopy(reviewed_catalog)
    skill = next(entry for entry in catalog if entry["id"] == "dotnet-development")
    skill["requires"] = ["dotnet-development"]
    ctx = context()
    policy = {"allowed_skills": ["dotnet-development"]}
    with pytest.raises(SelectionError, match="Cyclic"):
        select(ctx, catalog, policy)
    skill["requires"] = []
    skill["risk_tier"] = "privileged"
    with pytest.raises(SelectionError, match="No approved"):
        select(ctx, catalog, policy)
    skill["risk_tier"] = "general"
    skill["permissions"] = ["secret.read"]
    with pytest.raises(SelectionError, match="No approved"):
        select(ctx, catalog, policy)


@pytest.mark.parametrize("role", ["coder", "tester"])
def test_bridge_parity_and_forced_prerequisite_failure(reviewed_catalog, role):
    from tools.verify_selection import bridge_parity

    evidence = bridge_parity(reviewed_catalog, role)
    assert evidence["equal"]
    assert evidence["unmet_prerequisite"]["both_rejected"]
    assert evidence["missing_runtime"]["both_rejected"]


@pytest.mark.parametrize(
    ("version", "accepted"),
    [("7.0", False), ("8", True), ("9.1", True), ("10", False), (None, False)],
)
def test_technology_constraints_and_forced_requirements(reviewed_catalog, version, accepted):
    catalog = copy.deepcopy(reviewed_catalog)
    skill = next(entry for entry in catalog if entry["id"] == "dotnet-development")
    skill["technologies"].append({"name": "dotnet", "constraint": ">=8,<10"})
    policy = {"allowed_skills": ["dotnet-development"]}
    ctx = context(technology_versions={"dotnet": version})
    if accepted:
        assert select(ctx, catalog, policy).candidate_ids == ["dotnet-development"]
    else:
        with pytest.raises(SelectionError):
            select(ctx, catalog, policy)
        policy["required_skills"] = {"coder": ["dotnet-development"]}
        with pytest.raises(SelectionError, match="constraint"):
            select(ctx, catalog, policy)


def test_domain_operation_and_missing_requires_are_enforced(reviewed_catalog):
    catalog = copy.deepcopy(reviewed_catalog)
    policy = {"allowed_skills": ["dotnet-development"]}
    for scope in ({"domains": ["hr"]}, {"operations": ["write"]}):
        with pytest.raises(SelectionError):
            select(context(**scope), catalog, policy)
    skill = next(entry for entry in catalog if entry["id"] == "dotnet-development")
    skill["requires"] = ["missing"]
    with pytest.raises(SelectionError, match="missing"):
        select(context(), catalog, policy)
