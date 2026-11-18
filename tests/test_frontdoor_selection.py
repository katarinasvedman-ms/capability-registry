import copy
import json

from selection import select
from selection.frontdoor import rank
from tools.build_catalog import approve, frontdoor_contract
from tools.capture_sources import ROOT


def test_previously_colliding_domain_pair_and_operation_filter():
    rows = json.loads((ROOT / "baselines" / "frontdoor" / "backend" / "catalog.json").read_text())
    original = next(row for row in rows if row["id"] == "operations.order_hardware")
    changed = {**copy.deepcopy(original), "domain": "AnotherDomain"}
    context = {
        "goal": "new laptop",
        "domain": "Operations",
        "operation": "write",
        "permissions": ["operations.write"],
    }
    before = approve(frontdoor_contract(original))
    after = approve(frontdoor_contract(changed))
    assert before != after
    assert select(context, [before], {}).candidate_ids == ["operations.order_hardware"]
    assert select(context, [after], {}).candidate_ids == []
    assert select({**context, "operation": "read"}, [before], {}).candidate_ids == []


def test_source_knowledge_read_pair_remains_independent_of_operation():
    rows = json.loads((ROOT / "baselines" / "frontdoor" / "backend" / "catalog.json").read_text())
    row = next(row for row in rows if row["id"] == "people.leave_policy")
    knowledge = frontdoor_contract(row)
    read = frontdoor_contract({**row, "type": "read"})
    assert knowledge["operation"] == read["operation"] == "read"
    assert knowledge["kind"] == "knowledge"
    assert read["kind"] == "action"
    context = {"goal": row["name"], "permissions": row["required_permissions"]}
    assert rank({**context, "kind": "knowledge"}, [knowledge, {**read, "id": "read.copy"}]) == [
        row["id"]
    ]
    assert select(context, [knowledge, read | {"id": "read.copy"}], {}).candidate_ids == []


def test_lifecycle_and_approval_are_independent(reviewed_catalog):
    row = next(r for r in reviewed_catalog if r["id"] == "operations.order_hardware")
    context = {"goal": row["name"], "permissions": row["permissions"]}
    assert select(context, [row], {}).candidate_ids == [row["id"]]
    row["lifecycle"] = "deprecated"
    assert select(context, [row], {}).candidate_ids == []
    assert select(context, [row], {"include_deprecated": True}).candidate_ids == [row["id"]]
    assert (
        select(
            {**context, "lifecycle": "active"}, [row], {"include_deprecated": True}
        ).candidate_ids
        == []
    )
    row["lifecycle"] = "retired"
    assert select(context, [row], {"include_deprecated": True}).candidate_ids == []
