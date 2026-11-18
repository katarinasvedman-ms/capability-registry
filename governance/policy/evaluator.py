from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Call:
    entry: dict
    inputs: dict
    task_id: str
    identity: str
    permissions: frozenset[str]
    approver: str | None = None


@dataclass(frozen=True)
class Decision:
    action: Literal["allow", "deny", "require_approval"]
    lease_required: bool
    reason: str


class Evaluator(Protocol):
    def evaluate(self, call: Call) -> Decision: ...


class DeterministicEvaluator:
    """Version-one contract policy, deliberately without tool-name rules."""

    def __init__(self, policy):
        if policy["version"] != 1:
            raise ValueError("Unsupported policy version")
        self.policy = policy

    def evaluate(self, call):
        entry, policy = call.entry, self.policy
        if (
            not call.identity
            or not call.task_id
            or entry["status"] != policy["approved_status"]
            or entry["lifecycle"] != policy["active_lifecycle"]
            or entry["risk_tier"] not in policy["permitted_risk_tiers"]
            or entry["permissions"] is None
            or not set(entry["permissions"]) <= call.permissions
        ):
            return Decision("deny", False, "Unapproved, inactive or unauthorized capability")
        if entry["type"] == "skill":
            return Decision("allow", False, "Skill loading remains selection-controlled")
        side_effect = entry["operation"] in policy["side_effecting_operations"]
        leased = side_effect or (
            entry["risk_tier"] == "privileged" and policy["lease_privileged_reads"]
        )
        approval = entry["approval_required"] or (
            entry["risk_tier"] == "privileged"
            and side_effect
            and policy["approval_required_for_privileged_side_effects"]
        )
        if approval and not call.approver:
            return Decision("require_approval", leased, "Exact-call approval not completed")
        return Decision("allow", leased, "Contract policy allows this runtime call")
