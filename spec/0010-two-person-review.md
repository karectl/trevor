# ADR-0010 — Two-Person Review Rule

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

Airlock requests require at least two distinct reviewers to approve before release (C-04). This is a four-eyes principle. The agent may count as one reviewer. The rules around role segregation (researcher ≠ checker on same project) must be enforced at every approval step.

---

## Decision

### Rule set

1. Every airlock request requires **exactly two reviews** before reaching `APPROVED` state.
2. **Review 1** is always the agent review (runs automatically). It can produce `approve`, `changes_requested`, or `escalate`.
3. **Review 2** must be performed by a human with `output_checker` or `senior_checker` role on the project.
4. If Review 1 is `approve`, the human checker may accept the agent's findings (making the agent review count as Review 1) or override with their own decision.
5. If Review 1 is `changes_requested` or `escalate`, the human checker must provide their own full review.
6. **No single human** may provide both reviews (the agent is not a human — agent + human is always permitted).
7. **The submitting researcher** may not review their own request, ever.
8. **A project member** (any role) may not review requests for that project.
9. Requests flagged `escalate` by the agent require the second reviewer to be a `senior_checker`.

### Approval state machine detail

```
[AGENT_REVIEW complete]
        │
        ├── agent: approve
        │       └─► HUMAN_REVIEW (any assigned checker)
        │               ├── human: approve  ──────────────────► APPROVED
        │               ├── human: changes_requested  ────────► CHANGES_REQUESTED
        │               └── human: reject  ───────────────────► REJECTED
        │
        ├── agent: changes_requested
        │       └─► HUMAN_REVIEW (any assigned checker)
        │               └── (same as above)
        │
        └── agent: escalate
                └─► HUMAN_REVIEW (senior_checker only)
                        └── (same as above)
```

### What "accepting agent findings" means

A human checker who agrees with the agent does not need to re-examine each object independently. They can mark the request as "agent findings accepted" plus add their own summary comment. This is recorded as:

```json
{
  "reviewer_type": "human",
  "decision": "approved",
  "accepted_agent_findings": true,
  "summary": "Agent findings are correct. Suppression applied correctly."
}
```

This distinction is preserved in the audit trail so it is clear the human did not perform an independent check.

### Enforcement

Enforced at the API layer in the `POST /requests/{id}/reviews` endpoint:

```python
async def validate_reviewer(request: AirlockRequest, current_user: User, session: AsyncSession):
    if current_user.id == request.submitted_by:
        raise Forbidden("Submitter cannot review their own request")
    membership = await get_project_membership(request.project_id, current_user.id, session)
    if membership:
        raise Forbidden("Project member cannot review requests for their project")
    if request.requires_senior_checker and membership.role != Role.senior_checker:
        raise Forbidden("This request requires a senior checker")
    existing_reviews = await get_reviews(request.id, session)
    human_reviews = [r for r in existing_reviews if r.reviewer_type == "human"]
    if any(r.reviewer_id == current_user.id for r in human_reviews):
        raise Forbidden("Cannot review the same request twice")
```

---

## Consequences

- **Positive**: Clear, auditable dual-review trail on every approved request.
- **Positive**: Agent review reduces burden on human checkers for low-risk outputs without sacrificing the two-reviewer requirement.
- **Positive**: Escalation path (agent flags → senior checker required) adds a proportionate third tier without hardcoding a three-tier review for everything.
- **Negative**: If no senior checker is assigned to a project and the agent escalates, the request is blocked. Mitigation: `tre_admin` is notified and must assign a senior checker; this is an operational process requirement.
