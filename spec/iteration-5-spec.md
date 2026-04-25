# Iteration 5 Spec — Revision Cycle

## Goal

Researcher can respond to checker feedback by uploading replacement objects and resubmitting the request. Old versions are superseded; metadata carries forward. Resubmission returns the request to `SUBMITTED`, triggering a new agent review cycle.

---

## New OpenAPI paths

### POST /requests/{id}/objects/{object_id}/replace

Upload a replacement for an existing output object. Creates a new `OutputObject` with:
- Same `logical_object_id` as the original
- `version = original.version + 1`
- `replaces_id = original.id`
- `state = PENDING`

The original object transitions to `SUPERSEDED`.

**Auth**: Researcher on the project.

**Preconditions**:
1. Request in `CHANGES_REQUESTED` state.
2. Original object must belong to this request.
3. Original object must be in `CHANGES_REQUESTED` or `REJECTED` state (cannot replace an already-approved object).

**Request**: multipart/form-data with `file`, `output_type`, `statbarn` (same as upload).

**Response 201**: `OutputObjectRead`

**Side effects**:
- Original object → `SUPERSEDED`
- New metadata created if missing, or existing metadata preserved (same `logical_object_id`)
- Audit event: `object.replaced`

---

### POST /requests/{id}/resubmit

Resubmit a request after making changes.

**Auth**: Request owner or admin.

**Preconditions**:
1. Request in `CHANGES_REQUESTED` state.
2. At least one PENDING object exists (replacement was uploaded).

**State transitions**:
- Request: `CHANGES_REQUESTED` → `SUBMITTED`
- Sets `submitted_at` to now
- All previous reviews remain in DB (audit trail)
- Agent review job enqueued again

**Response 200**: `RequestRead`

**Audit event**: `request.resubmitted`

---

### GET /requests/{id}/objects/{object_id}/versions

List all versions of the same logical object (all `OutputObject` records sharing the same `logical_object_id`), ordered by version ASC.

**Auth**: Project member or admin.

**Response 200**:
```json
{
  "items": [OutputObjectRead]
}
```

---

## State transitions (Iteration 5 scope)

| From | To | Trigger | Actor |
|------|----|---------|-------|
| CHANGES_REQUESTED | SUBMITTED | Researcher resubmits | researcher |
| OutputObject: CHANGES_REQUESTED/REJECTED | SUPERSEDED | Replacement uploaded | researcher |

After resubmission, the agent review cycle runs again:
SUBMITTED → AGENT_REVIEW → HUMAN_REVIEW (same as iteration 3).

---

## Audit events

| Event | Trigger |
|-------|---------|
| `object.replaced` | Replacement object uploaded |
| `request.resubmitted` | Request resubmitted after changes |

---

## DB changes

No new tables. No migration needed.

---

## Testing strategy

- Replace object: happy path (upload replacement, old → SUPERSEDED)
- Replace object: wrong request state (not CHANGES_REQUESTED)
- Replace object: cannot replace approved object
- Replace object: metadata preserved across versions
- Resubmit: happy path (CHANGES_REQUESTED → SUBMITTED)
- Resubmit: wrong state
- Resubmit: requires pending objects
- Version history: returns all versions ordered
- Full cycle: upload → submit → agent review → human changes_requested → replace → resubmit

---

## File changes

| File | Change |
|------|--------|
| `src/trevor/routers/requests.py` | Add replace, resubmit, versions endpoints |
| `tests/test_requests.py` | Add revision cycle tests |
