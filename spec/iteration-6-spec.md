# Iteration 6 Spec — Release (RO-Crate + Pre-signed URL)

## Goal

Approved requests are packaged as RO-Crate, uploaded to the release S3 bucket, and a pre-signed download URL is generated. A `ReleaseRecord` tracks each release.

---

## New model: ReleaseRecord

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| request_id | UUID FK → airlock_requests.id | unique |
| crate_storage_key | str | Key in release S3 bucket |
| crate_checksum_sha256 | str | SHA-256 of the zip |
| presigned_url | str | Current pre-signed GET URL |
| url_expires_at | datetime | When the URL expires |
| delivered_to | JSON | List of email addresses (future) |
| created_at | datetime | |

---

## DB migration

New table: `release_records`. Single `create_table` + unique index on `request_id`.

---

## New settings

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `PRESIGNED_URL_TTL` | int | `604800` | Pre-signed URL TTL in seconds (7 days) |

---

## OpenAPI paths

### POST /requests/{id}/release

Trigger release of an approved request. Admin only.

**Preconditions**:
1. Request in `APPROVED` state.
2. No existing ReleaseRecord for this request.

**Behaviour**:
1. Transition request to `RELEASING`.
2. In dev mode: run release inline. In prod: enqueue `release_job` via ARQ.
3. Return 202 Accepted.

**Response 202**: `{"status": "releasing"}`

### GET /requests/{id}/release

Get the release record for a request.

**Auth**: Project member or admin.

**Response 200**: `ReleaseRecordRead` (or 404 if not released).

---

## ReleaseRecordRead schema

```json
{
  "id": "uuid",
  "request_id": "uuid",
  "crate_storage_key": "string",
  "crate_checksum_sha256": "string",
  "presigned_url": "string",
  "url_expires_at": "datetime",
  "delivered_to": [],
  "created_at": "datetime"
}
```

---

## RO-Crate assembly

### Service: `src/trevor/services/release_service.py`

```python
async def assemble_and_release(request_id, session, settings) -> ReleaseRecord:
```

Steps:
1. Load request, approved objects (latest version per logical_object_id), metadata, reviews.
2. Verify checksums: for each approved object, re-fetch from quarantine and verify SHA-256 matches stored checksum. Fail if mismatch.
3. Build RO-Crate using `rocrate` library:
   - Root dataset: request title, description, project, dates
   - File entities: one per approved object with metadata + TRE extensions
   - Person entities: researcher (submitter)
   - CreateAction: approval event
4. Zip the crate directory (in-memory).
5. Compute SHA-256 of the zip.
6. Upload zip to release S3 bucket: key = `releases/{request_id}/ro-crate-{request_id}.zip`
7. Generate pre-signed GET URL with configurable TTL.
8. Create ReleaseRecord.
9. Transition request: RELEASING → RELEASED.
10. Emit audit events.

### Dev mode (DEV_AUTH_BYPASS)

Skip S3 operations. Create ReleaseRecord with placeholder values. Still build the crate metadata in memory for testing.

---

## ARQ job: release_job

```python
async def release_job(ctx, request_id: str) -> None:
```

Same pattern as agent_review_job: session factory from ctx, load request, call release_service.

---

## State transitions

| From | To | Trigger |
|------|----|---------|
| APPROVED | RELEASING | Admin triggers release |
| RELEASING | RELEASED | Crate built + uploaded |

---

## Audit events

| Event | Trigger |
|-------|---------|
| `request.releasing` | Release triggered |
| `request.released` | Crate uploaded, URL generated |
| `request.release_failed` | Release job error |

---

## Testing strategy

- RO-Crate assembly: build crate from test data, verify metadata.json structure
- Release service: mock S3, verify ReleaseRecord creation
- POST /release: happy path (APPROVED → RELEASING)
- POST /release: wrong state
- POST /release: duplicate release blocked
- GET /release: returns record
- GET /release: 404 when not released
- Checksum verification in assembly

---

## File changes

| File | Change |
|------|--------|
| `src/trevor/models/release.py` | ReleaseRecord SQLModel |
| `src/trevor/models/__init__.py` | Export ReleaseRecord |
| `src/trevor/schemas/release.py` | ReleaseRecordRead |
| `src/trevor/services/release_service.py` | assemble_and_release |
| `src/trevor/routers/releases.py` | POST + GET endpoints |
| `src/trevor/app.py` | Wire releases router |
| `src/trevor/worker.py` | Add release_job |
| `src/trevor/settings.py` | PRESIGNED_URL_TTL |
| `alembic/versions/` | New migration |
| `tests/test_releases.py` | Release tests |

---

## Out of scope

- Email notification delivery (placeholder `delivered_to`)
- URL refresh endpoint (for extending expired URLs)
- Quarantine cleanup job
