# ADR-0005 — Agentic Output Checking: Advisory, Statbarn-Based

**Status**: Accepted  
**Date**: 2025-01  
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

The agent is invoked as an async background task via FastAPI's `BackgroundTasks` or a lightweight task queue (ARQ / Celery — see ADR-0008). It:

1. Fetches the output objects from quarantine storage
2. Runs statbarn classification and rule checks
3. Calls an LLM (configurable — Anthropic Claude, or local model) for the narrative explanation component
4. Writes a `Review` record with `reviewer_type=agent`
5. Emits a `review.created` audit event
6. Triggers notification to assigned checkers

The LLM call is optional and configurable. If disabled, the agent produces rule-check-only reports without narrative explanation.

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
- **Negative**: Agent rule engine must be maintained as statbarn rules evolve.
- **Negative**: LLM-generated narrative introduces a cost and latency dependency. Mitigation: LLM is optional; rule-check-only mode is always available.
- **Mitigation for rule drift**: Statbarn rules are versioned in the trevor codebase. The rule set version is recorded in each agent review.
