# Iteration 2 Spec — Airlock Request Lifecycle (Researcher Side)

## OpenAPI paths

### AirlockRequest

#### POST /requests
Create new request in DRAFT state.

Request body:
```json
{
  "project_id": "uuid",
  "direction": "egress | ingress",
  "title": "string",
  "description": "string"
}
```

Response 201:
```json
{
  "id": "uuid",
  "project_id": "uuid",
  "direction": "egress",
  "status": "DRAFT",
  "title": "string",
  "description": "string",
  "submitted_by": "uuid",
  "submitted_at": null,
  "updated_at": "datetime",
  "closed_at": null,
  "object_count": 0
}
```

Errors: 403 if user not researcher on project. 404 if project not found or archived.

---

#### GET /requests
List requests. Researchers see own project requests. Checkers see requests on their projects. Admins see all.

Query params: `project_id`, `status`, `direction`, `limit` (default 50), `offset` (default 0).

Response 200: `{ "items": [...], "total": int }`

---

#### GET /requests/{id}
Get single request with objects summary.

Response 200: same shape as POST 201 response plus `objects: [OutputObjectRead]`.

Errors: 403 if no project membership. 404 if not found.

---

#### POST /requests/{id}/submit
Transition DRAFT → SUBMITTED. Enqueues agent review job.

No request body. Requires at least one OutputObject in PENDING state.

Response 200: updated request.

Errors: 409 if status != DRAFT. 422 if no objects.

---

### OutputObject

#### POST /requests/{id}/objects
Upload file. Multipart form: `file` (binary), `output_type` (str), `statbarn` (str).

Server streams to quarantine S3, computes SHA-256 inline, creates OutputObject record.

S3 key: `{project_id}/{request_id}/{logical_object_id}/{version}/{uuid4}-{filename}`

Response 201:
```json
{
  "id": "uuid",
  "request_id": "uuid",
  "logical_object_id": "uuid",
  "version": 1,
  "replaces_id": null,
  "filename": "string",
  "output_type": "string",
  "statbarn": "string",
  "storage_key": "string",
  "checksum_sha256": "string",
  "size_bytes": int,
  "state": "PENDING",
  "uploaded_at": "datetime",
  "uploaded_by": "uuid"
}
```

Errors: 403 if not researcher on project. 409 if request not in DRAFT state.

---

#### GET /requests/{id}/objects
List all objects for request.

Response 200: `{ "items": [OutputObjectRead] }`

---

#### GET /requests/{id}/objects/{object_id}
Get single object.

Response 200: OutputObjectRead.

---

#### PATCH /requests/{id}/objects/{object_id}/metadata
Set/update OutputObjectMetadata for a logical object.

Request body:
```json
{
  "title": "string",
  "description": "string",
  "researcher_justification": "string",
  "suppression_notes": "string",
  "tags": {}
}
```

Response 200: updated metadata record.

---

#### GET /requests/{id}/objects/{object_id}/metadata
Get metadata for logical object.

Response 200: OutputObjectMetadataRead.

---

### AuditEvent

#### GET /requests/{id}/audit
List audit events for request. Ordered by timestamp ASC.

Response 200: `{ "items": [AuditEventRead] }`

---

## DB migration

### New tables

#### airlock_requests
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| project_id | UUID FK → projects.id | |
| direction | VARCHAR | egress / ingress |
| status | VARCHAR | AirlockRequestStatus enum |
| title | VARCHAR | |
| description | VARCHAR | default "" |
| submitted_by | UUID FK → users.id | |
| submitted_at | DATETIME | nullable |
| updated_at | DATETIME | |
| closed_at | DATETIME | nullable |

#### output_objects
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| request_id | UUID FK → airlock_requests.id | |
| version | INTEGER | 1-indexed |
| replaces_id | UUID FK → output_objects.id | nullable |
| logical_object_id | UUID | index |
| filename | VARCHAR | |
| output_type | VARCHAR | |
| statbarn | VARCHAR | |
| storage_key | VARCHAR | |
| checksum_sha256 | VARCHAR | |
| size_bytes | INTEGER | |
| state | VARCHAR | OutputObjectState enum |
| uploaded_at | DATETIME | |
| uploaded_by | UUID FK → users.id | |

#### output_object_metadata
| Column | Type | Notes |
|--------|------|-------|
| logical_object_id | UUID PK | |
| title | VARCHAR | |
| description | VARCHAR | default "" |
| researcher_justification | VARCHAR | default "" |
| suppression_notes | VARCHAR | default "" |
| checker_feedback | JSON | default [] |
| tags | JSON | default {} |
| updated_at | DATETIME | |

#### audit_events
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| request_id | UUID FK → airlock_requests.id | nullable; index |
| actor_id | VARCHAR | user UUID str or "agent:trevor-agent" or "system" |
| event_type | VARCHAR | namespaced e.g. request.submitted |
| payload | JSON | |
| timestamp | DATETIME | UTC server-set; index |

### Enum values

**AirlockRequestStatus**: DRAFT, SUBMITTED, AGENT_REVIEW, HUMAN_REVIEW, CHANGES_REQUESTED, APPROVED, REJECTED, RELEASING, RELEASED

**OutputObjectState**: PENDING, APPROVED, REJECTED, CHANGES_REQUESTED, SUPERSEDED

**AirlockDirection**: egress, ingress

**OutputType** (initial set): tabular, figure, model, code, report, other

---

## State machine rules

- DRAFT → SUBMITTED: requires ≥1 PENDING object; emits `request.submitted`
- SUBMITTED → AGENT_REVIEW: set by ARQ worker on job start; emits `request.agent_review_started`
- AGENT_REVIEW → HUMAN_REVIEW: set by ARQ worker on completion; emits `review.created`
- HUMAN_REVIEW → APPROVED / REJECTED / CHANGES_REQUESTED: set by checker POST /reviews (Iteration 3)
- CHANGES_REQUESTED → SUBMITTED: on researcher resubmit (Iteration 5)
- APPROVED → RELEASING: triggered on second approval; emits `request.releasing`
- RELEASING → RELEASED: set by ARQ release job (Iteration 6)

This iteration implements: DRAFT → SUBMITTED only. AGENT_REVIEW onward is Iteration 3.

---

## Audit events emitted (Iteration 2)

| Event | Trigger |
|-------|---------|
| `request.created` | POST /requests |
| `object.uploaded` | POST /requests/{id}/objects |
| `object.metadata_updated` | PATCH metadata |
| `request.submitted` | POST /requests/{id}/submit |
