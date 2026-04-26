# Iteration 4 Spec — Human Review

## Goal

Output checkers can submit structured reviews on requests in `HUMAN_REVIEW` state. Two-reviewer rule (C-04) is enforced. State transitions to `APPROVED`, `REJECTED`, or `CHANGES_REQUESTED` based on review outcomes.

---

## New OpenAPI path: POST /requests/{id}/reviews

### Auth

Caller must be `output_checker` or `senior_checker` on the request's project. Caller must NOT be the request submitter (C-04). Caller must not have already reviewed this request.

### Request body

```json
{
  "decision": "approved | rejected | changes_requested",
  "summary": "Overall comment from the checker",
  "object_decisions": [
    {
      "object_id": "uuid",
      "decision": "approved | rejected | changes_requested",
      "feedback": "Per-object feedback text"
    }
  ]
}
```

### Validation

1. Request must be in `HUMAN_REVIEW` state.
2. Reviewer must be `output_checker` or `senior_checker` on the project.
3. Reviewer must NOT be the request submitter.
4. Reviewer must not have an existing human review on this request.
5. Each `object_id` in `object_decisions` must belong to this request.
6. All PENDING objects should have a decision (warning if missing, not error).

### Response 201

`ReviewRead` (same schema as iteration 3).

### Side effects

1. Create `Review` record with `reviewer_type=human`.
2. For each object_decision:
   - Update `OutputObject.state` to match the per-object decision.
   - Append feedback to `OutputObjectMetadata.checker_feedback` array.
3. Evaluate two-reviewer rule (see below).
4. Emit audit events.

---

## Two-reviewer rule (C-04)

After each human review is created, evaluate whether the request can transition:

**Count reviews** for this request where `reviewer_type IN (agent, human)`.

- Agent review counts as 1 reviewer.
- Need at least 2 total reviews (agent + 1 human, or 2 humans).

**Decision logic** (once 2+ reviews exist):

| Condition | Request transition |
|-----------|-------------------|
| All reviews `approved` | → `APPROVED` |
| Any review `rejected` | → `REJECTED` |
| Any review `changes_requested` (none rejected) | → `CHANGES_REQUESTED` |
| < 2 reviews | No transition (stay in `HUMAN_REVIEW`) |

Per-object state is set by the latest human reviewer's per-object decision. If both human reviewers decide on the same object, the stricter decision wins (rejected > changes_requested > approved).

---

## Audit events emitted

| Event | Trigger |
|-------|---------|
| `review.created` | Human review submitted |
| `request.approved` | Two-reviewer rule met, all approved |
| `request.rejected` | Two-reviewer rule met, any rejected |
| `request.changes_requested` | Two-reviewer rule met, changes needed |
| `object.state_changed` | Per-object state updated by reviewer |

---

## State transitions (Iteration 4 scope)

| From | To | Trigger | Actor |
|------|----|---------|-------|
| HUMAN_REVIEW | APPROVED | 2+ reviews, all approved | system |
| HUMAN_REVIEW | REJECTED | 2+ reviews, any rejected | system |
| HUMAN_REVIEW | CHANGES_REQUESTED | 2+ reviews, any changes_requested | system |

---

## Schema additions

### HumanReviewCreate (request body)

```python
class ObjectDecision(BaseModel):
    object_id: uuid.UUID
    decision: str  # approved | rejected | changes_requested
    feedback: str = ""

class HumanReviewCreate(BaseModel):
    decision: str  # approved | rejected | changes_requested
    summary: str
    object_decisions: list[ObjectDecision] = []
```

---

## DB changes

No new tables. No migration needed — Review model already supports human reviews.

`OutputObjectMetadata.checker_feedback` JSON array gets entries appended:
```json
{"reviewer_id": "uuid", "version": 1, "feedback": "text", "timestamp": "iso"}
```

---

## Testing strategy

### Unit tests
- POST review: happy path (checker submits review)
- POST review: submitter cannot review own request
- POST review: researcher cannot review (role check)
- POST review: duplicate review rejected
- POST review: wrong request state rejected
- Two-reviewer logic: agent + 1 human → transitions
- Two-reviewer logic: 1 human only → stays in HUMAN_REVIEW
- Per-object state updates
- Per-object feedback appended to metadata

### Fixtures needed
- `checker_setup`: second user with output_checker role on same project
- Request in HUMAN_REVIEW state with agent review already done

---

## File changes

| File | Change |
|------|--------|
| `src/trevor/schemas/review.py` | Add `HumanReviewCreate`, `ObjectDecision` |
| `src/trevor/routers/reviews.py` | Add `POST /{request_id}/reviews` |
| `tests/test_reviews.py` | Add human review tests |
| `tests/conftest.py` | Add `checker_setup` fixture |

---

## Out of scope (deferred)

- Checker dashboard UI (Datastar — later iteration)
- Notifications to researcher on outcome
- Senior checker override / escalation handling
