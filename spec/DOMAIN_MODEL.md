# trevor — Domain Model

## Entity relationship overview

```
Project (from CRD)
  │
  ├─── ProjectMembership ──── User
  │         (role: researcher / output_checker / senior_checker)
  │
  └─── AirlockRequest
            │   direction: ingress | egress
            │   status: DRAFT → ... → RELEASED | REJECTED
            │
            ├─── OutputObject (version 1)
            │         │  statbarn, checksum, storage_key
            │         │  state: PENDING | APPROVED | SUPERSEDED | ...
            │         │
            │         └─── OutputObjectMetadata  ← shared across versions
            │                   (title, description, justification,
            │                    suppression_notes, checker_feedback[])
            │
            ├─── OutputObject (version 2, replaces version 1)
            │         │  same metadata record, updated
            │         └─── (checksum, storage_key for new file)
            │
            ├─── Review (agent)
            │         reviewer: agent:trevor-agent
            │         decision: changes_requested
            │         findings[]: per-object structured feedback
            │
            ├─── Review (human checker)
            │         reviewer: user_id
            │         decision: approved
            │         findings[]: per-object structured feedback
            │
            └─── AuditEvent[]  (append-only log of all transitions)
```

---

## Core entities

### User
Synced from Keycloak on first login. trevor holds a local shadow record for audit FK integrity.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | trevor-internal |
| `keycloak_sub` | str | Keycloak subject claim |
| `email` | str | |
| `display_name` | str | |
| `created_at` | datetime | |

---

### Project
Read from Kubernetes CRD. trevor caches a denormalised copy to avoid hammering the k8s API. Refreshed via a watch/reconcile loop.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | trevor-internal |
| `crd_name` | str | k8s CRD resource name |
| `display_name` | str | |
| `status` | enum | `active` / `suspended` / `archived` |
| `synced_at` | datetime | Last CRD sync |

---

### ProjectMembership
Defines a user's role within a project. A user may have different roles on different projects.

| Field | Type | Notes |
|-------|------|-------|
| `user_id` | UUID FK | |
| `project_id` | UUID FK | |
| `role` | enum | `researcher` / `output_checker` / `senior_checker` |
| `assigned_by` | UUID FK | `tre_admin` who assigned |
| `assigned_at` | datetime | |

**Constraint**: A user with `researcher` role on project P MUST NOT also hold `output_checker` or `senior_checker` on project P.

---

### AirlockRequest
The primary workflow entity.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID FK | |
| `direction` | enum | `egress` / `ingress` |
| `status` | enum | See lifecycle in GLOSSARY |
| `title` | str | Researcher-supplied |
| `description` | str | |
| `submitted_by` | UUID FK | User |
| `submitted_at` | datetime | |
| `updated_at` | datetime | |
| `closed_at` | datetime | null until terminal state |

---

### OutputObject
An immutable file submitted as part of a request.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `request_id` | UUID FK | |
| `version` | int | 1-indexed within a lineage chain |
| `replaces_id` | UUID FK | null for v1; points to previous version |
| `logical_object_id` | UUID | Shared across all versions in a lineage |
| `filename` | str | Original filename |
| `output_type` | enum | See Output Type in GLOSSARY |
| `statbarn` | str | Statbarn code (researcher-assigned, checker-verifiable) |
| `storage_key` | str | Key in quarantine S3 bucket |
| `checksum_sha256` | str | Computed at upload |
| `size_bytes` | int | |
| `state` | enum | `PENDING` / `APPROVED` / `REJECTED` / `CHANGES_REQUESTED` / `SUPERSEDED` |
| `uploaded_at` | datetime | |
| `uploaded_by` | UUID FK | |

---

### OutputObjectMetadata
One record per `logical_object_id`. Accumulates annotations across versions.

| Field | Type | Notes |
|-------|------|-------|
| `logical_object_id` | UUID PK | FK to the lineage |
| `title` | str | |
| `description` | str | |
| `researcher_justification` | str | Why this output is necessary |
| `suppression_notes` | str | What SDC was applied |
| `checker_feedback` | JSON | Array of `{reviewer_id, version, feedback, timestamp}` |
| `tags` | JSON | Arbitrary key-value pairs |
| `updated_at` | datetime | |

---

### Review
A recorded decision from a checker (human or agent) on a whole request.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `request_id` | UUID FK | |
| `reviewer_id` | UUID FK | null for agent |
| `reviewer_type` | enum | `human` / `agent` |
| `decision` | enum | `approved` / `rejected` / `changes_requested` |
| `summary` | str | Overall comment |
| `findings` | JSON | Array of `{object_id, decision, feedback}` |
| `created_at` | datetime | |

---

### AuditEvent
Append-only log. Never updated or deleted.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `request_id` | UUID FK | null for system events |
| `actor_id` | str | User UUID or `agent:trevor-agent` or `system` |
| `event_type` | str | Namespaced: `request.submitted`, `object.uploaded`, `review.created`, `request.released`, etc. |
| `payload` | JSON | Event-specific detail |
| `timestamp` | datetime | UTC, server-set |

---

### ReleaseRecord
Created when a request reaches `RELEASED` state.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `request_id` | UUID FK | |
| `crate_storage_key` | str | Key in release S3 bucket |
| `crate_checksum_sha256` | str | |
| `presigned_url` | str | Current signed URL |
| `url_expires_at` | datetime | |
| `delivered_to` | JSON | List of email addresses notified |
| `created_at` | datetime | |

---

## State machine: AirlockRequest

```
┌─────────┐    submit()     ┌───────────┐   agent completes  ┌──────────────┐
│  DRAFT  │ ─────────────► │ SUBMITTED │ ─────────────────► │ AGENT_REVIEW │
└─────────┘                 └───────────┘                    └──────┬───────┘
                                                                     │ agent report ready
                                                              ┌──────▼───────┐
                              ┌───────────────────────────── │ HUMAN_REVIEW │
                              │  changes_requested            └──────┬───────┘
                              ▼                                      │ approved / rejected
                 ┌────────────────────┐                    ┌─────────▼──────────┐
                 │ CHANGES_REQUESTED  │                    │ APPROVED / REJECTED │
                 └──────────┬─────────┘                    └─────────┬──────────┘
                            │ researcher resubmits                   │ (if approved)
                            └──────────────────────────────►  ┌──────▼───────┐
                                                               │  RELEASING   │
                                                               └──────┬───────┘
                                                                      │ crate built + URL sent
                                                               ┌──────▼───────┐
                                                               │   RELEASED   │
                                                               └──────────────┘
```

---

## Lineage chain: OutputObject versions

```
logical_object_id: 7f3a...
                │
    ┌───────────┴────────────┐
    │                        │
OutputObject v1          OutputObject v2
id: aaa                  id: bbb
replaces: null           replaces: aaa
state: SUPERSEDED        state: PENDING
storage_key: /...        storage_key: /...
checksum: abc123         checksum: def456
                              │
                         OutputObjectMetadata (logical_object_id: 7f3a...)
                         — title set by researcher at v1
                         — checker_feedback from v1 review carried forward
                         — suppression_notes updated by researcher at v2
```

---

## Notification model

Notifications are triggered by state transitions and delivered via a pluggable backend. Supported backends: SMTP (default), webhook, in-app (database-backed notification table). Multiple backends can be active simultaneously.

Events that trigger notifications:

| Event | Recipient(s) |
|-------|-------------|
| `request.submitted` | Assigned checkers |
| `review.created` (agent) | Assigned checkers (agent report ready) |
| `request.changes_requested` | Submitting researcher |
| `request.approved` | Submitting researcher, project lead |
| `request.rejected` | Submitting researcher |
| `request.released` | Submitting researcher, designated download recipients |
| `presigned_url.expiring_soon` | Submitting researcher |
