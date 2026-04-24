# ADR-0005 — Agentic Output Checking: Advisory, Statbarn-Based, Pydantic-AI

**Status**: Accepted  
**Date**: 2025-01  
**Updated**: 2025-01 (Pydantic-AI selected as agent framework; LLM backend clarified)  
**Deciders**: trevor project lead

---

## Context

trevor needs to incorporate autonomous output checking alongside human review. The design space includes:

- Whether the agent is a gate (blocks human review until passed) or advisory
- What rule framework the agent uses
- How the agent report is surfaced to human checkers
- How disagreements between agent and human are handled
- Whether the agent counts toward the two-reviewer requirement

Reference: [outputchecking.org/operations/](https://outputchecking.org/operations/) and the attached SACRO guide.

---

## Decision

### 1. Advisory, not a gate

The agent review is **advisory**. It runs automatically when a request reaches `SUBMITTED` state and produces a structured report before human review begins. Human checkers are not blocked if the agent is unavailable or returns an error — they receive a notification indicating the agent report is pending or failed, and can proceed.

Rationale: Gating on the agent creates operational fragility. If the agent is down, no requests can move forward. The agent's primary value is as a productivity aid for human checkers, not as a security enforcement mechanism. The human reviewer is always accountable for the final decision.

### 2. Statbarn-based rule framework

The agent assesses each output object against statbarn rules from the SACRO framework. The agent does **not** use the SACRO Python library (see C-09). Instead, trevor maintains its own rule engine, seeded with the published statbarn rule set.

Each output object is assessed based on:
- Its declared `output_type` and `statbarn` classification
- The file content (for supported types: CSV, markdown tables, parquet)
- The researcher's justification and suppression notes

The agent produces a per-object finding with:
- `statbarn_confirmed`: whether the declared statbarn matches the detected output type
- `rule_checks`: list of applicable rules and pass/fail results
- `disclosure_risk`: `none` / `low` / `medium` / `high`
- `recommendation`: `approve` / `changes_requested` / `escalate`
- `explanation`: human-readable narrative for the checker

### 3. Agent as a reviewer

The agent review counts as **one of the two required reviews**, subject to the condition that a human checker explicitly accepts the agent's findings (or overrides them with their own decision). A human reviewer must always be the second reviewer — two agent reviews are never sufficient.

The agent is recorded with identity `agent:trevor-agent` in the `Review` table.

### 4. Implementation approach

The agent is invoked as an ARQ background task (see ADR-0008) triggered by the `request.submitted` event. It:

1. Fetches the output objects from quarantine storage
2. Runs deterministic statbarn classification and rule checks (no LLM required)
3. Calls the LLM via Pydantic-AI for the narrative explanation component
4. Writes a `Review` record with `reviewer_type=agent`
5. Emits a `review.created` audit event
6. Triggers notification to assigned checkers

#### Agent framework: Pydantic-AI

**Pydantic-AI** is used as the agent framework. It integrates natively with Pydantic v2 models (already a platform standard — C-13) and provides:

- **Structured output**: agent tool calls and responses are typed Pydantic models — no JSON parsing, no prompt engineering for output format
- **OpenAI-compatible backend**: `pydantic_ai.models.openai.OpenAIModel` accepts any OpenAI-compatible endpoint URL, covering the karectl-provided endpoint without vendor lock-in
- **Dependency injection**: agent dependencies (S3 client, rule engine, request context) are typed and injected via `RunContext[Deps]` — testable without mocking the LLM
- **Retry and error handling**: built-in validation retry loop if the model returns a malformed response
- **Streaming**: supports streamed responses for progressive report rendering via SSE to the checker UI

```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

model = OpenAIModel(
    model_name=settings.agent_model_name,  # e.g. "gpt-4o"
    base_url=settings.agent_openai_base_url,  # OpenAI-compatible endpoint
    api_key=settings.agent_api_key,
)

agent = Agent(
    model=model,
    deps_type=AgentDeps,
    result_type=AgentReviewReport,  # Pydantic model — structured output
    system_prompt=SYSTEM_PROMPT,
)
```

The `AgentReviewReport` Pydantic model mirrors the structured findings stored in the `Review.findings` JSON column.

The LLM call covers narrative explanation only. The deterministic rule checks (statbarn classification, threshold checks, suppression validation) run before the LLM call and are passed as context, so the LLM is used for synthesis and communication, not for rule evaluation.

The LLM call is optional and configurable. If `AGENT_LLM_ENABLED=false`, the agent produces rule-check-only reports without narrative explanation.

### 5. Human override

Human checkers can:
- **Accept** the agent's findings (agent review counts as review 1; human approval counts as review 2)
- **Override** specific per-object findings (their override is recorded alongside the agent's original finding)
- **Escalate** to a senior checker (if the agent flags `high` disclosure risk)

The agent's report is **never modified**. Overrides are recorded as additional annotations on the agent's `Review` record, not mutations of it.

### 6. Disagreement handling

If a human checker's decision contradicts the agent's recommendation:
- The human decision stands.
- The disagreement is flagged in the audit trail.
- If the agent recommended `escalate` and the human checker approves without escalation, the senior checker is automatically notified for awareness (not for veto).

---

## Consequences

- **Positive**: Reduces checker workload for low-risk, well-documented outputs.
- **Positive**: Consistent application of statbarn rules regardless of checker experience.
- **Positive**: Agent reports serve as a training aid for newer checkers.
- **Positive**: Pydantic-AI's structured output eliminates prompt-engineering fragility for report format — the schema is enforced at the type level.
- **Positive**: OpenAI-compatible endpoint means the LLM provider can be swapped (hosted, local, different vendor) by changing two config values.
- **Positive**: Pydantic-AI dependency injection makes the agent fully unit-testable without a live LLM (use `pydantic_ai.models.test.TestModel`).
- **Negative**: Agent rule engine must be maintained as statbarn rules evolve. Mitigation: rule set is versioned; version is recorded in each agent review.
- **Negative**: LLM call introduces cost and latency. Mitigation: deterministic rules run first and are cheap; LLM call is scoped to narrative synthesis only and is optional.
