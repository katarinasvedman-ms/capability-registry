import copy
import json

from tools.build_catalog import build
from tools.capture_sources import ROOT, verify
from tools.validate_catalog import validate

SCHEMA = json.loads((ROOT / "contracts" / "capability.schema.json").read_text())


def test_import_matches_sources_and_explicit_testbed_approval(imported_catalog):
    generated, gaps = build(ROOT / "baselines")
    assert generated == imported_catalog
    assert len(generated) == 28
    assert len([entry for entry in generated if entry["type"] == "skill"]) == 10
    approved = [entry for entry in generated if entry["status"] == "approved"]
    assert len(approved) == 15
    assert all(entry["approval_scope"] == "testbed" for entry in approved)
    assert all(entry["reviewer"] == "katarina.svedman" for entry in approved)
    assert all(entry["owner"] == "unassigned" for entry in generated if entry["type"] == "skill")
    assert set(gaps["entries"]) == {entry["id"] for entry in generated}
    assert validate(generated, SCHEMA) == []
    assert validate(generated, SCHEMA, ready=True)
    verify(ROOT / "baselines")


def test_schema_refuses_escape_hatches_and_invented_approval(imported_catalog):
    catalog = copy.deepcopy(imported_catalog)
    catalog[0]["metadata"] = {"anything": "not a contract"}
    assert any("Additional properties" in error for error in validate(catalog, SCHEMA))
    del catalog[0]["metadata"]
    catalog[0]["status"] = "approved"
    catalog[0]["reviewer"] = None
    assert validate(catalog, SCHEMA)


def test_validation_rejects_bad_inputs_dependencies_and_tier(reviewed_catalog, binding):
    catalog = copy.deepcopy(reviewed_catalog)
    skill = next(entry for entry in catalog if entry["type"] == "skill")
    tool = next(entry for entry in catalog if entry["type"] == "tool")
    tool["inputs"] = {"type": "made-up"}
    skill["requires"] = [skill["id"]]
    skill["risk_tier"] = "privileged"
    errors = validate(catalog, SCHEMA, {skill["id"]: binding})
    assert any("invalid input JSON Schema" in error for error in errors)
    assert any("Cyclic" in error for error in errors)
    assert any("trust tier" in error for error in errors)


def test_duplicate_ids_and_missing_references_are_rejected(imported_catalog):
    catalog = copy.deepcopy(imported_catalog)
    catalog[0]["requires"] = ["does-not-exist"]
    catalog.append(copy.deepcopy(catalog[0]))
    errors = validate(catalog, SCHEMA)
    assert any("Duplicate" in error for error in errors)
    assert any("missing requires reference" in error for error in errors)


def test_optional_requires_defaults_and_constraint_validation(imported_catalog):
    catalog = copy.deepcopy(imported_catalog)
    del catalog[0]["requires"]
    catalog[0]["technologies"] = [{"name": "dotnet", "constraint": ">=8,<10"}]
    assert validate(catalog, SCHEMA) == []
    catalog[0]["technologies"][0]["constraint"] = "run arbitrary code"
    assert any("Unsupported" in error for error in validate(catalog, SCHEMA))


def test_privileged_binding_cannot_disguise_a_general_project(imported_catalog):
    entry = copy.deepcopy(next(r for r in imported_catalog if r["id"] == "mock.deploy_release"))
    entry["risk_tier"] = "privileged"
    general = {
        "project_endpoint": "https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills",
        "toolbox": "factory-skills",
        "version": "2",
        "risk_tier": "privileged",
    }
    assert any(
        "privileged entry resolves to general" in error
        for error in validate([entry], SCHEMA, {entry["id"]: general})
    )
    privileged = {**general, "project_endpoint": general["project_endpoint"] + "-privileged"}
    assert validate([entry], SCHEMA, {entry["id"]: privileged}) == []
