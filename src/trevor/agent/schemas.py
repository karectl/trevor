"""Data structures for rule engine results."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class RuleResult:
    """Result of a single disclosure-control rule check."""

    rule: str
    passed: bool
    detail: str
    severity: str = "info"  # critical | warning | info


@dataclass
class ObjectAssessment:
    """Aggregate assessment of a single output object."""

    object_id: uuid.UUID
    statbarn_confirmed: bool
    rule_checks: list[RuleResult] = field(default_factory=list)
    disclosure_risk: str = "none"  # none | low | medium | high
    recommendation: str = "approve"  # approve | changes_requested | escalate
    explanation: str = ""
