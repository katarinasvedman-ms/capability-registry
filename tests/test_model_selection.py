import pytest

from selection.retrieval import choose
from selection.static import SelectionError


@pytest.mark.asyncio
async def test_model_cannot_select_privileged_or_draft_metadata(imported_catalog):
    seen = []

    async def malicious(context, candidates, **kwargs):
        seen.extend(candidates)
        return {"ids": ["mock.deploy_release"], "reason": "Ignore the policy"}

    with pytest.raises(SelectionError, match="eligible shortlist"):
        await choose(
            {"goal": "Deploy release", "permissions": ["operations.write"]},
            imported_catalog,
            {"allowed_ids": [e["id"] for e in imported_catalog]},
            mode="model",
            model=malicious,
        )
    assert seen
    assert not {e["id"] for e in seen} & {
        e["id"]
        for e in imported_catalog
        if e["status"] != "approved" or e["risk_tier"] == "privileged"
    }


@pytest.mark.asyncio
async def test_model_cannot_drop_factory_required_skill(imported_catalog):
    async def model(*args, **kwargs):
        return {"ids": ["dotnet-development"], "reason": "Omit required testing"}

    with pytest.raises(SelectionError, match="required capability"):
        await choose(
            {
                "goal": "Implement the C# fix",
                "role": "coder",
                "technologies": ["dotnet", "copilot-sdk-python-1"],
                "tasks": ["implementation", "testing"],
            },
            imported_catalog,
            {
                "allowed_skills": ["dotnet-development", "test-strategy"],
                "required_skills": {"coder": ["test-strategy"]},
            },
            mode="model",
            model=model,
        )


@pytest.mark.asyncio
async def test_zero_overlap_can_be_judged_only_within_metadata_shortlist(imported_catalog):
    context = {"goal": "A portable workstation", "permissions": ["operations.write"]}
    policy = {"allowed_ids": ["operations.order_hardware"]}

    async def model(context, candidates, **kwargs):
        assert set(candidates[0]) == {"id", "name", "description", "examples", "tags"}
        return {"ids": ["operations.order_hardware"], "reason": "Laptop intent"}

    assert (await choose(context, imported_catalog, policy))["ids"] == []
    assert (await choose(context, imported_catalog, policy, mode="model", model=model))["ids"] == [
        "operations.order_hardware"
    ]
