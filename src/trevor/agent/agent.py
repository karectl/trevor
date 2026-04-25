"""Pydantic-AI agent — orchestrates rule engine + optional LLM narrative."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, cast

from trevor.agent.prompts import template_object_explanation, template_overall_summary
from trevor.agent.schemas import ObjectAssessment

logger = logging.getLogger(__name__)

AGENT_ACTOR_ID = "agent:trevor-agent"


def _assessment_to_finding(assessment: ObjectAssessment, filename: str) -> dict[str, Any]:
    """Convert ObjectAssessment to the findings JSON format stored in Review."""
    return {
        "object_id": str(assessment.object_id),
        "statbarn_confirmed": assessment.statbarn_confirmed,
        "rule_checks": [dataclasses.asdict(r) for r in assessment.rule_checks],
        "disclosure_risk": assessment.disclosure_risk,
        "recommendation": assessment.recommendation,
        "explanation": assessment.explanation or template_object_explanation(assessment, filename),
    }


def decide_overall(assessments: list[ObjectAssessment]) -> str:
    """Pick overall decision from per-object recommendations.

    escalate in any → changes_requested (agent can't reject; humans decide).
    changes_requested in any → changes_requested.
    All approve → approved.
    """
    for a in assessments:
        if a.recommendation == "escalate":
            return "changes_requested"
    for a in assessments:
        if a.recommendation == "changes_requested":
            return "changes_requested"
    return "approved"


async def run_agent_review(
    assessments: list[tuple[ObjectAssessment, str]],
    *,
    llm_enabled: bool = False,
    openai_base_url: str = "",
    model_name: str = "gpt-4o",
    api_key: str = "",
) -> dict[str, Any]:
    """Run agent review: rule results → optional LLM → Review data dict.

    Args:
        assessments: list of (ObjectAssessment, filename) tuples
        llm_enabled: whether to call LLM for narrative
        openai_base_url: OpenAI-compatible base URL
        model_name: model identifier
        api_key: API key

    Returns:
        dict with keys: decision, summary, findings (ready for Review creation)
    """
    all_assessments = [a for a, _ in assessments]
    filenames = {str(a.object_id): fn for a, fn in assessments}

    if llm_enabled and openai_base_url and api_key:
        summary, explanations = await _run_llm_narrative(
            assessments, openai_base_url=openai_base_url, model_name=model_name, api_key=api_key
        )
        # Apply LLM explanations to assessments
        for assessment, _ in assessments:
            oid = str(assessment.object_id)
            if oid in explanations:
                assessment.explanation = explanations[oid]
    else:
        summary = template_overall_summary(all_assessments)

    findings = [_assessment_to_finding(a, filenames[str(a.object_id)]) for a, _ in assessments]
    decision = decide_overall(all_assessments)

    return {
        "decision": decision,
        "summary": summary,
        "findings": findings,
    }


async def _run_llm_narrative(
    assessments: list[tuple[ObjectAssessment, str]],
    *,
    openai_base_url: str,
    model_name: str,
    api_key: str,
) -> tuple[str, dict[str, str]]:
    """Call LLM via Pydantic-AI for narrative generation.

    Returns (overall_summary, {object_id: explanation}).
    """
    from pydantic import BaseModel
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from trevor.agent.prompts import SYSTEM_PROMPT

    class ReviewNarrative(BaseModel):
        overall_summary: str
        object_explanations: dict[str, str]  # object_id → explanation

    provider = OpenAIProvider(base_url=openai_base_url, api_key=api_key)
    model = OpenAIChatModel(model_name, provider=provider)
    agent = cast(
        Agent[None, ReviewNarrative],
        Agent(
            model,
            system_prompt=SYSTEM_PROMPT,
            output_type=ReviewNarrative,
        ),
    )

    # Build user prompt from assessments
    lines = []
    for assessment, filename in assessments:
        lines.append(f"## Object: {filename} (id: {assessment.object_id})")
        lines.append(
            f"Risk: {assessment.disclosure_risk}, Recommendation: {assessment.recommendation}"
        )
        for check in assessment.rule_checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{status}] {check.rule}: {check.detail}")
        lines.append("")

    user_prompt = "\n".join(lines)

    try:
        result = await agent.run(user_prompt)
        narrative = result.output
        return narrative.overall_summary, narrative.object_explanations
    except Exception:  # noqa: BLE001
        logger.exception("LLM narrative generation failed; falling back to templates")
        all_assessments = [a for a, _ in assessments]
        return template_overall_summary(all_assessments), {}
