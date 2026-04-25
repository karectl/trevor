"""Prompt templates for the agent LLM and template-based narratives."""

from __future__ import annotations

from trevor.agent.schemas import ObjectAssessment

SYSTEM_PROMPT = """\
You are trevor-agent, an automated statistical disclosure control reviewer \
for a Trusted Research Environment. You receive the results of rule-based \
checks on research outputs and produce a concise, actionable review summary.

For each output object you will receive:
- filename, output type, declared statbarn
- rule check results (rule name, passed/failed, detail, severity)
- disclosure risk level and recommendation

Your task:
1. Write a per-object explanation (2-3 sentences) that a human checker can \
   understand, noting which rules failed and why.
2. Write an overall summary (2-4 sentences) covering the entire request.
3. Be precise about risk. Do not speculate beyond the rule results.
"""


def template_object_explanation(assessment: ObjectAssessment, filename: str) -> str:
    """Generate template-based explanation when LLM is disabled."""
    total = len(assessment.rule_checks)
    passed = sum(1 for r in assessment.rule_checks if r.passed)
    failed = total - passed
    return (
        f"Object {filename}: {total} rules checked, {passed} passed, "
        f"{failed} failed. Highest risk: {assessment.disclosure_risk}. "
        f"Recommendation: {assessment.recommendation}."
    )


def template_overall_summary(assessments: list[ObjectAssessment]) -> str:
    """Generate template-based overall summary when LLM is disabled."""
    total_objects = len(assessments)
    approved = sum(1 for a in assessments if a.recommendation == "approve")
    escalated = sum(1 for a in assessments if a.recommendation == "escalate")
    worst = "approve"
    for a in assessments:
        if a.recommendation == "escalate":
            worst = "escalate"
            break
        if a.recommendation == "changes_requested":
            worst = "changes_requested"
    return (
        f"Reviewed {total_objects} objects. {approved} recommended for approval, "
        f"{escalated} escalated. Overall recommendation: {worst}."
    )
