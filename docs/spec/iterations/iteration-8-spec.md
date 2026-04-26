# Iteration 8 Spec — Ingress Flow

## Goal

Complete ingress direction (import into TRE). External submitters upload files via pre-signed PUT URLs. Same dual-review pipeline as egress. Approved files delivered to workspace via pre-signed GET URL.

---

## Current state

Already implemented:
- `AirlockDirection.INGRESS` enum value in model
- `create_request` accepts `direction=ingress`
- Full review pipeline (agent + human) works for any direction
- Release service assembles RO-Crate for approved requests

**Not yet implemented** (this iteration):
1. Admin/senior_checker can create ingress requests (not just researchers)
2. Pre-signed PUT URL generation for external file submission
3. Upload confirmation + checksum verification after external PUT
4. Delivery endpoint: pre-signed GET URL for workspace to consume approved files
5. UI: ingress-specific views

---

## Design decisions

### Who creates ingress requests?

`tre_admin` or `senior_checker` on the target project. Researchers do NOT create ingress requests — they receive data, they don't initiate import. This differs from egress where researchers create requests.

### External upload via pre-signed PUT

Ingress files come from outside the TRE. trevor generates a pre-signed PUT URL for each output object slot. The external submitter uploads directly to S3 quarantine bucket. trevor then confirms upload and computes checksum.

Flow:
```
Admin creates ingress request (DRAFT)
  → Admin adds object slots (filename, output_type, expected source)
  → trevor generates pre-signed PUT URL per slot
  → External party uploads file to pre-signed PUT URL
  → Admin confirms upload (trevor fetches HEAD, computes checksum)
  → Admin submits request → agent review → human review → approved
  → Workspace fetches via pre-signed GET URL (delivery)
```

### Delivery vs Release

Egress: approved → RO-Crate assembled → release bucket → pre-signed GET for external download.
Ingress: approved → files stay in quarantine bucket → pre-signed GET generated for workspace to pull from quarantine.

No RO-Crate for ingress (C-11 says crate assembled at RELEASED, but ingress delivery is to internal workspace, not external publication). Ingress requests transition to `RELEASED` after workspace acknowledges delivery.

---

## Data model changes

### New fields on `OutputObject`

| Field | Type | Notes |
|-------|------|-------|
| `upload_url_generated_at` | datetime \| null | When pre-signed PUT URL was generated |

No persistent storage of pre-signed URLs (security: they expire, shouldn't be stored).

### New model: `DeliveryRecord`

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `request_id` | UUID FK | |
| `delivery_type` | enum | `workspace_pull` / `direct_copy` (future) |
| `delivered_at` | datetime | |
| `delivered_by` | UUID FK | Admin who triggered delivery |
| `delivery_metadata` | JSON | `{workspace_id, presigned_url_expires_at, ...}` |

Mirrors `ReleaseRecord` for egress. Both represent terminal delivery.

---

## DB migration

```
-- Add nullable column to output_objects
ALTER TABLE output_objects ADD COLUMN upload_url_generated_at TIMESTAMP;

-- New table
CREATE TABLE delivery_records (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES airlock_requests(id),
    delivery_type VARCHAR NOT NULL DEFAULT 'workspace_pull',
    delivered_at TIMESTAMP NOT NULL,
    delivered_by UUID NOT NULL REFERENCES users(id),
    delivery_metadata JSON NOT NULL DEFAULT '{}',
    UNIQUE(request_id)
);
```

---

## New API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/requests/{id}/objects/{oid}/upload-url` | Admin/Senior | Generate pre-signed PUT URL for ingress object |
| `POST` | `/requests/{id}/objects/{oid}/confirm-upload` | Admin/Senior | Confirm external upload, compute checksum |
| `POST` | `/requests/{id}/deliver` | `tre_admin` | Generate delivery URL for workspace |
| `GET` | `/requests/{id}/delivery` | Member/Admin | Get delivery record |

### `POST /requests/{id}/objects/{oid}/upload-url`

**Preconditions**: request is `DRAFT`, direction is `ingress`.

**Response**:
```json
{
  "upload_url": "https://s3.../presigned-put?...",
  "expires_in": 3600,
  "storage_key": "project/request/object/1/uuid-filename"
}
```

Generates S3 pre-signed PUT URL. Sets `upload_url_generated_at` on object. Object must exist (created via `POST /requests/{id}/objects` with empty file or metadata-only).

### `POST /requests/{id}/objects/{oid}/confirm-upload`

**Preconditions**: request is `DRAFT`, direction is `ingress`, `upload_url_generated_at` is set.

trevor does `HEAD` on the S3 key to verify upload completed. Reads object to compute SHA-256 checksum. Updates `checksum_sha256` and `size_bytes` on OutputObject.

**Response**: Updated `OutputObjectRead`.

### `POST /requests/{id}/deliver`

**Preconditions**: request is `APPROVED`, direction is `ingress`.

Generates pre-signed GET URLs for all approved objects in quarantine bucket. Creates `DeliveryRecord`. Transitions request to `RELEASING` → `RELEASED`.

**Response**: `DeliveryRecordRead` with object download URLs.

### `GET /requests/{id}/delivery`

Returns `DeliveryRecordRead` if exists.

---

## Modified endpoints

### `POST /requests/{id}/objects` (upload)

For ingress requests: allow creating object slot WITHOUT file upload. Accept `filename` as Form field. Storage key generated but no S3 upload. Checksum and size set to empty/zero until `confirm-upload`.

Add check: if `direction == ingress`, file upload is optional. If no file provided, create placeholder object awaiting external upload.

### `POST /requests/{id}/submit`

No changes needed — works for both directions. Agent review runs same rules regardless of direction.

### `POST /requests` (create)

Allow `tre_admin` and `senior_checker` (on target project) to create ingress requests, not just researchers. Add auth check: if direction is `ingress`, require admin or senior_checker role instead of researcher.

---

## New schemas

```python
class UploadUrlResponse(BaseModel):
    upload_url: str
    expires_in: int
    storage_key: str

class DeliveryRecordRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    delivery_type: str
    delivered_at: datetime
    delivered_by: uuid.UUID
    delivery_metadata: dict[str, Any]

class DeliveryObjectUrl(BaseModel):
    object_id: uuid.UUID
    filename: str
    download_url: str
    checksum_sha256: str
    size_bytes: int

class DeliveryResponse(DeliveryRecordRead):
    object_urls: list[DeliveryObjectUrl]
```

---

## Storage module additions

```python
async def generate_presigned_put_url(
    *, bucket: str, key: str, content_type: str, expires_in: int = 3600,
    settings: Settings | None = None,
) -> str:
    """Pre-signed PUT URL for external upload to quarantine."""

async def head_object(
    *, bucket: str, key: str, settings: Settings | None = None,
) -> dict:
    """HEAD object — returns content_length, content_type, etag."""
```

---

## UI endpoints (new)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/ui/requests/new-ingress` | Admin/Senior | Ingress request creation form |
| `POST` | `/ui/requests/ingress` | Admin/Senior | Create ingress request via form |
| `GET` | `/ui/requests/{id}/ingress-upload` | Admin/Senior | Manage ingress object uploads |
| `POST` | `/ui/requests/{id}/objects/{oid}/generate-url` | Admin/Senior | Generate upload URL via UI |
| `POST` | `/ui/requests/{id}/objects/{oid}/confirm` | Admin/Senior | Confirm upload via UI |
| `POST` | `/ui/requests/{id}/deliver` | `tre_admin` | Trigger delivery via UI |

### Templates (new/modified)

```
src/trevor/templates/
  researcher/
    request_detail.html       # MODIFIED: show delivery info for ingress requests
  admin/
    ingress_create.html       # NEW: ingress request creation form
    ingress_upload.html       # NEW: manage external uploads (URL generation, confirm)
    delivery_panel.html       # NEW: delivery status + URLs (component)
```

### UI flow

1. Admin navigates to "New Ingress Request" → form with project, title, description
2. On request detail: "Add Object Slot" form (filename, output_type, expected source description)
3. Per object slot: "Generate Upload URL" button → displays pre-signed PUT URL + instructions
4. After external party uploads: "Confirm Upload" button → trevor verifies + shows checksum
5. All objects confirmed → "Submit" button → standard review pipeline
6. After approval: "Deliver" button → generates workspace download URLs

---

## Test plan

### API tests (`test_ingress.py`)

1. Admin creates ingress request → 201, direction=ingress
2. Researcher cannot create ingress request → 403
3. Create object slot without file (ingress) → 201, checksum empty
4. Generate pre-signed PUT URL → 200, URL returned
5. Confirm upload (mock S3 HEAD) → 200, checksum populated
6. Submit ingress request → standard pipeline
7. Deliver approved ingress request → 200, delivery record created
8. Deliver non-approved request → 409
9. Get delivery record → 200
10. Deliver egress request → 409 (wrong direction)

### UI tests (`test_ui.py` additions)

11. Ingress create form renders → 200
12. Ingress upload management page renders → 200
13. Delivery panel shows on approved ingress request

---

## Implementation order

1. DB migration: `upload_url_generated_at` column + `delivery_records` table
2. `DeliveryRecord` model + schema
3. Storage: `generate_presigned_put_url`, `head_object`
4. Modify `create_request` auth: admin/senior for ingress
5. Modify `upload_object`: optional file for ingress
6. New endpoints: `upload-url`, `confirm-upload`, `deliver`, `delivery`
7. Tests (API)
8. UI templates + routes
9. UI tests
10. Update docs (API reference, UI guide)
