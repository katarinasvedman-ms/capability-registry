from __future__ import annotations

import re

PART = re.compile(r"(>=|<=|==|!=|>|<)\s*([0-9]+(?:\.[0-9]+)*)")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def parse_constraint(constraint):
    if constraint == "*":
        return []
    if not isinstance(constraint, str) or not constraint.strip():
        raise ValueError("Technology constraint must be a nonempty string")
    parts = [PART.fullmatch(part.strip()) for part in constraint.split(",")]
    if not all(parts):
        raise ValueError(f"Unsupported numeric technology constraint: {constraint}")
    return [part.groups() for part in parts]


def satisfies(version, constraint):
    parts = parse_constraint(constraint)
    if not parts:
        return True
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        return False
    actual = tuple(map(int, version.split(".")))
    for operator, bound in parts:
        expected = tuple(map(int, bound.split(".")))
        width = max(len(actual), len(expected))
        left = actual + (0,) * (width - len(actual))
        right = expected + (0,) * (width - len(expected))
        if not {
            ">=": left >= right,
            "<=": left <= right,
            "==": left == right,
            "!=": left != right,
            ">": left > right,
            "<": left < right,
        }[operator]:
            return False
    return True


def matches_names(requirements, technologies):
    names = {
        item if isinstance(item, str) else item["name"]
        for item in requirements
        if isinstance(item, str) or "constraint" not in item
    }
    return not names or "*" in names or bool(names & technologies)


def constraints_met(requirements, technologies, versions):
    for item in requirements:
        if isinstance(item, dict) and "constraint" in item:
            if item["name"] not in technologies or not satisfies(
                versions.get(item["name"]), item["constraint"]
            ):
                return False
    return True


def applicable(requirements, technologies, versions):
    return matches_names(requirements, technologies) and constraints_met(
        requirements, technologies, versions
    )
