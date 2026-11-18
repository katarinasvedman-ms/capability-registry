import copy

import pytest
from fastapi import HTTPException

from backend import knowledge
from backend.store import USERS


def test_live_retrieval_filters_before_query_and_rechecks_source_acl(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "azure_search")
    user = {**USERS["sam"], "id": "entra:test:employee"}
    documents = [
        {
            **knowledge.DOCUMENTS[0],
            "allowed_principals": [],
            "provenance": "azure_ai_search_synthetic_content",
        },
        {**knowledge.DOCUMENTS[0], "id": "PRIVATE", "allowed_principals": ["another-employee"]},
        {**knowledge.DOCUMENTS[0], "id": "FOREIGN", "country": "France", "allowed_principals": []},
        {**knowledge.DOCUMENTS[3], "allowed_principals": []},
    ]
    payloads = []

    def query(payload):
        payloads.append(payload)
        return copy.deepcopy(documents)

    monkeypatch.setattr(knowledge, "query_search_index", query)
    result = knowledge.search_knowledge("leave policy or country eq 'France'", user)
    assert [doc["id"] for doc in result] == ["HR-SE-001"]
    assert result[0]["provenance"] == "azure_ai_search_synthetic_content"
    assert payloads[0]["search"] == "leave policy or country eq 'France'"
    assert "country eq 'Sweden'" in payloads[0]["filter"]
    assert "permission eq 'operations.admin'" not in payloads[0]["filter"]
    assert "p eq 'entra:test:employee'" in payloads[0]["filter"]


def test_live_provider_failure_does_not_substitute_mock_sources(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "azure_search")

    def fail(_):
        raise HTTPException(503, "Unavailable")

    monkeypatch.setattr(knowledge, "query_search_index", fail)
    with pytest.raises(HTTPException) as error:
        knowledge.search_knowledge("annual leave", USERS["alex"])
    assert error.value.status_code == 503
