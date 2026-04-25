# Iteration 3 Spec — Agent Review

## Goal

When a request reaches `SUBMITTED`, trevor automatically runs a statbarn-based review, records it as a `Review` with `reviewer_type=agent`, and transitions the request through `AGENT_REVIEW` → `HUMAN_REVIEW`.

---

## New model: Review

Added to `models/request.py` (or new `models/review.py`).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| request_id | UUID FK → airlock_requests.id | index |
| reviewer_id | UUID FK → users.id | nullable — null for agent |
| reviewer_type | VARCHAR | ReviewerType enum: `human` / `agent` |
| decision | VARCHAR | ReviewDecision enum: `approved` / `rejected` / `changes_requested` |
| summary | VARCHAR | Overall narrative |
| findings | JSON | Array of per-object findings (see schema below) |
| created_at | DATETIME | |

### Findings JSON schema (per-object entry)

```json
{
  "object_id": "uuid",
  "statbarn_confirmed": true,
  "rule_checks": [
    {"rule": "min_cell_count", "passed": false, "detail": "Table contains cells < 10"}
  ],
  "disclosure_risk": "low | medium | high | none",
  "recommendation": "approve | changes_requested | escalate",
  "explanation": "Human-readable narrative (LLM-generated if enabled, else templated)"
}
```

---

## DB migration

### New table: reviews

See columns above. Single new `create_table` + index on `request_id`.

No changes to existing tables.

---

## OpenAPI paths

### GET /requests/{id}/reviews
List all reviews for a request. Ordered by `created_at` ASC.

Auth: any project member or admin.

Response 200:
```json
{
  "items": [ReviewRead]
}
```

### GET /requests/{id}/reviews/{review_id}
Get single review with full findings.

Response 200: `ReviewRead`

---

### ReviewRead schema

```json
{
  "id": "uuid",
  "request_id": "uuid",
  "reviewer_id": "uuid | null",
  "reviewer_type": "agent | human",
  "decision": "approved | rejected | changes_requested",
  "summary": "string",
  "findings": [...],
  "created_at": "datetime"
}
```

No POST endpoint for reviews in this iteration — the agent creates reviews internally. Human review POST is Iteration 4.

---

## Statbarn rule engine design

### Architecture

`src/trevor/agent/rules.py` — pure-function rule engine. No LLM dependency. No I/O. Deterministic.

`src/trevor/agent/agent.py` — Pydantic-AI agent. Calls rule engine, optionally calls LLM for narrative, assembles `Review` record.

`src/trevor/agent/prompts.py` — system prompt and template strings.

### Rule engine interface

```python
@dataclass
class RuleResult:
    rule: str           # e.g. "min_cell_count"
    passed: bool
    detail: str         # human-readable explanation

@dataclass
class ObjectAssessment:
    object_id: UUID
    statbarn_confirmed: bool
    rule_checks: list[RuleResult]
    disclosure_risk: str      # none / low / medium / high
    recommendation: str       # approve / changes_requested / escalate
```

```python
def assess_object(
    output_type: str,
    statbarn: str,
    file_content: bytes,
    filename: str,
    metadata: OutputObjectMetadata,
) -> ObjectAssessment:
    """Run all applicable rules for the given statbarn + output_type."""
```

### Initial rule set (from SACRO framework)

Rules are keyed by `(statbarn, output_type)`. Initial set:

| Rule | Applies to | Check |
|------|-----------|-------|
| `min_cell_count` | tabular | All cells in frequency/cross-tab ≥ threshold (default 10) |
| `dominance_rule` | tabular | Top-N contributors don't dominate (p% rule; default p=70, N=2) |
| `p_percent_rule` | tabular | Second-largest contributor is ≥ p% of largest |
| `missing_values_flagged` | tabular | Missing/suppressed cells explicitly marked, not just empty |
| `statbarn_matches_type` | all | Declared statbarn is plausible for the file's actual content type |
| `justification_present` | all | `researcher_justification` is non-empty |
| `suppression_documented` | all | If statbarn implies SDC, `suppression_notes` is non-empty |
| `file_not_empty` | all | File size > 0 |
| `no_individual_records` | tabular | Row count suggests aggregated data, not individual records (heuristic) |

Rules that require parsing (tabular checks) use `polars` for CSV/parquet. Non-parseable files get `statbarn_matches_type` check only.

Each rule has a `severity`: `critical` (→ high risk), `warning` (→ medium risk), `info` (→ low risk).

`disclosure_risk` is the max severity of any failing rule. No failing rules → `none`.

`recommendation`:
- All pass → `approve`
- Any warning-level fail → `changes_requested`
- Any critical-level fail → `escalate`

### LLM narrative (optional)

When `AGENT_LLM_ENABLED=true`:
- Pydantic-AI agent receives rule check results as context
- LLM generates a natural-language summary per object and an overall summary
- Summary stored in `Review.summary` and each finding's `explanation`

When `AGENT_LLM_ENABLED=false`:
- Template-based narrative from rule results: "Object {filename}: {N} rules checked, {M} passed, {K} failed. Highest risk: {level}. Recommendation: {rec}."
- No external API call

---

## ARQ job: agent_review_job

### Trigger

When `POST /requests/{id}/submit` succeeds:
1. Request transitions to `SUBMITTED` (already implemented)
2. Enqueue `agent_review_job(request_id=str(req.id))` via ARQ

In `DEV_AUTH_BYPASS` / test mode without Redis: run agent review inline (synchronous fallback) or skip.

### Job flow

```
agent_review_job(ctx, request_id):
  1. Get DB session from ctx (worker startup creates engine + session factory)
  2. Load AirlockRequest, verify status == SUBMITTED
  3. Transition status → AGENT_REVIEW
  4. Emit audit event: request.agent_review_started
  5. For each OutputObject (state=PENDING):
     a. Fetch file from quarantine S3 (or skip in dev mode)
     b. Load OutputObjectMetadata
     c. Run assess_object()
  6. If AGENT_LLM_ENABLED:
     a. Call Pydantic-AI agent with assessments as context
     b. Get narrative summaries
  7. Create Review record:
     - reviewer_id=null, reviewer_type=agent
     - decision = worst recommendation across objects (escalate → changes_requested, else majority)
     - findings = list of per-object assessments with explanations
  8. Transition status → HUMAN_REVIEW
  9. Emit audit event: review.created (payload includes review_id)
  10. Commit
  11. (Future: notify checkers)
```

### Error handling

If agent job fails:
- Request stays in `AGENT_REVIEW` (not rolled back to SUBMITTED)
- Audit event: `request.agent_review_failed` with error detail
- Human checkers can still be notified (Iteration 4 will handle this — a failed agent review doesn't block human review)
- Job can be retried via ARQ retry mechanism

---

## Settings additions

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `AGENT_OPENAI_BASE_URL` | str | `""` | OpenAI-compatible LLM endpoint |
| `AGENT_MODEL_NAME` | str | `"gpt-4o"` | Model identifier |
| `AGENT_API_KEY` | str | `""` | API key for LLM |
| `AGENT_LLM_ENABLED` | bool | `false` | Enable LLM narrative generation |
| `AGENT_MIN_CELL_COUNT` | int | `10` | Threshold for min_cell_count rule |
| `AGENT_DOMINANCE_P` | int | `70` | p-percent for dominance rule |

---

## State transitions (Iteration 3 scope)

| From | To | Trigger | Actor |
|------|----|---------|-------|
| SUBMITTED | AGENT_REVIEW | ARQ job starts | system |
| AGENT_REVIEW | HUMAN_REVIEW | ARQ job completes | agent:trevor-agent |

These transitions were specced in Iteration 2 but not implemented. Iteration 3 implements them.

---

## Audit events emitted (Iteration 3)

| Event | Trigger |
|-------|---------|
| `request.agent_review_started` | Job transitions to AGENT_REVIEW |
| `review.created` | Agent review record written |
| `request.agent_review_failed` | Job error (review not created) |

---

## Testing strategy

### Unit tests (no LLM, no S3, no Redis)

- **Rule engine tests**: known CSV/parquet inputs → expected RuleResult outputs. Cover each rule individually.
- **assess_object tests**: full assessment pipeline with mock file content.
- **Agent review job tests**: mock DB session + mock file content. Verify:
  - State transitions: SUBMITTED → AGENT_REVIEW → HUMAN_REVIEW
  - Review record created with correct structure
  - Audit events emitted
  - Error handling: failed assessment doesn't crash job

### With LLM (optional, integration)

- Use `pydantic_ai.models.test.TestModel` for deterministic LLM output in CI.
- Verify narrative is populated in Review.summary and finding explanations.

### Inline fallback test

- Test that submit endpoint works in dev mode without Redis (inline agent review or graceful skip).

---

## File layout

```
src/trevor/
  agent/
    __init__.py
    rules.py           # statbarn rule engine (pure functions)
    agent.py           # Pydantic-AI agent definition
    prompts.py         # system prompt, templates
    schemas.py         # RuleResult, ObjectAssessment, AgentReviewReport Pydantic models
  models/
    review.py          # Review SQLModel (or add to request.py)
  schemas/
    review.py          # ReviewRead
  routers/
    reviews.py         # GET /requests/{id}/reviews
```

---

## Dependencies to add

| Package | Purpose |
|---------|---------|
| `pydantic-ai` | Agent framework (already in ADR-0005) |
| `polars` | Already in deps — used for CSV/parquet parsing in rule engine |

---

## Out of scope (deferred)

- Human review POST endpoint (Iteration 4)
- Checker notification when agent report ready (noted as future in job flow)
- LLM streaming to UI (Iteration 4 checker dashboard)
- Agent retry UI for admins
