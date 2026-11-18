import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field

from backend.config import catalog_provider


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    domain: str
    description: str
    examples: list[str] = []
    tags: list[str] = []
    type: Literal["knowledge", "read", "action", "agent", "handoff"]
    protocol: Literal["mcp", "a2a", "published_agent", "handoff", "knowledge", "local"]
    invocation: dict[str, str]
    input_schema: dict
    required_permissions: list[str]
    approval_required: bool
    owner: str
    status: Literal["active", "deprecated", "inactive"]
    version: str


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=2000)
    domain: str | None = None
    type: str | None = None
    max_results: int = Field(default=4, ge=1, le=5)


class Catalog:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("CATALOG_PATH", "backend/catalog.json"))
        self.provider = "local" if path is not None else catalog_provider()
        self._lock = threading.Lock()
        self._expires = 0
        self.reload()

    def reload(self):
        self._refresh(force=True)

    def ensure_fresh(self):
        if self.provider == "apic" and time.monotonic() >= self._expires:
            try:
                self._refresh(force=False)
            except (ValueError, KeyError, TypeError) as error:
                raise HTTPException(
                    503, "API Center catalog refresh rejected; no local catalog fallback"
                ) from error

    @property
    def entries(self):
        self.ensure_fresh()
        return self._entries

    def _refresh(self, force):
        with self._lock:
            if not force and time.monotonic() < self._expires:
                return
            if self.provider == "apic":
                from backend.apic_catalog import read_capabilities

                rows = read_capabilities()
            else:
                rows = json.loads(self.path.read_text())
            entries = self.validated_entries(rows)
            self._entries = entries
            self._expires = time.monotonic() + 60

    @staticmethod
    def validated_entries(rows):
        if not isinstance(rows, list):
            raise ValueError("Catalog must be an array of capabilities")
        entries = [Capability.model_validate(row) for row in rows]
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Duplicate capability IDs")
        for entry in entries:
            try:
                Draft202012Validator.check_schema(entry.input_schema)
            except SchemaError as error:
                raise ValueError("Invalid capability input schema") from error
            if entry.type == "action" and not entry.approval_required:
                raise ValueError("Write capabilities require approval")
            if set(entry.invocation) != {"adapter", "tool"}:
                raise ValueError("Catalog invocation contains only adapter and tool identifiers")
            if entry.invocation["adapter"] != entry.protocol or not entry.invocation["tool"]:
                raise ValueError("Catalog protocol must match its supported invocation adapter")
        return {entry.id: entry for entry in entries}

    def permitted(self, entry, user):
        return set(entry.required_permissions) <= set(user["permissions"])

    def resolve(self, capability_id, user):
        entry = self.entries.get(capability_id)
        if not entry or not self.permitted(entry, user):
            raise HTTPException(404, "Capability not found")
        if entry.status != "active":
            raise HTTPException(410, "Capability is deprecated or inactive")
        return entry

    def metadata(self, entry):
        return entry.model_dump(exclude={"invocation", "required_permissions"})

    def listing(self, user):
        return [
            self.metadata(entry) for entry in self.entries.values() if self.permitted(entry, user)
        ]

    def search(self, query: Search, user):
        tokens = set(re.findall(r"[a-z0-9]+", query.goal.lower())) - {
            "a",
            "an",
            "the",
            "is",
            "my",
            "our",
            "for",
            "in",
            "to",
            "of",
            "and",
            "i",
            "me",
        }
        ranked = []
        for entry in self.entries.values():
            if entry.status != "active" or not self.permitted(entry, user):
                continue
            if query.domain and entry.domain.lower() != query.domain.lower():
                continue
            if query.type and entry.type != query.type:
                continue
            fields = [
                (entry.name, 3),
                (entry.description, 1),
                (" ".join(entry.examples), 2),
                (" ".join(entry.tags), 3),
                (entry.domain, 1),
            ]
            score = sum(
                weight * len(tokens & set(re.findall(r"[a-z0-9]+", text.lower())))
                for text, weight in fields
            )
            if score >= 3:
                ranked.append((score, entry.id, self.metadata(entry)))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [{**row[2], "score": row[0]} for row in ranked[: query.max_results]]

    def validate(self, entry, inputs):
        errors = list(
            Draft202012Validator(entry.input_schema, format_checker=FormatChecker()).iter_errors(
                inputs
            )
        )
        if errors:
            raise HTTPException(422, errors[0].message)
