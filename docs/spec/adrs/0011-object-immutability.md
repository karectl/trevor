# ADR-0011 — Output Object Immutability and Checksum Verification

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

The integrity of the airlock process depends on being able to prove that the file a checker reviewed is identical to the file that was released. This requires immutability guarantees enforced at every layer.

---

## Decision

### Immutability model

An `OutputObject` record and the file it references in quarantine storage are **immutable from the moment of upload**. No operation may:
- Overwrite the file content at the existing storage key
- Update the `checksum_sha256`, `storage_key`, `filename`, or `size_bytes` fields on an existing `OutputObject`
- Delete an `OutputObject` record or the file it references (until the configured retention period, and then only by the automated cleanup job, never via the API)

A researcher who needs to change a file creates a **new `OutputObject`** via the replacement endpoint. The original object's state is set to `SUPERSEDED`; it is never deleted during the active review period.

### Storage key scheme

Storage keys in the quarantine bucket are constructed to be unique and non-guessable:

```
{project_id}/{request_id}/{logical_object_id}/{version}/{uuid4}-{filename}
```

Example:
```
proj-7f3a/req-8b2c/lobj-9d1e/v2/4a7f3c2b-table_1_suppressed.csv
```

This scheme means a file's location is unique by construction — a new upload can never accidentally overwrite an existing file.

### Upload verification

On upload, trevor:
1. Streams the file to S3 (multipart upload for files > 5MB)
2. Computes SHA-256 checksum during streaming (in-memory, no temp file)
3. Stores the checksum in `OutputObject.checksum_sha256`
4. Verifies against S3's ETag (MD5 for non-multipart; for multipart, trevor re-downloads and verifies SHA-256 independently)

### Release verification

Before assembling the RO-Crate, trevor:
1. Fetches each approved output file from quarantine
2. Recomputes SHA-256
3. Asserts it matches the stored `checksum_sha256`
4. Raises `IntegrityError` and halts release if any mismatch is found

The checksum of the final RO-Crate zip is also computed and stored in `ReleaseRecord.crate_checksum_sha256`.

### Checksums in the RO-Crate

Each file entity in `ro-crate-metadata.json` includes:
```json
{
  "@id": "data/table_1_suppressed.csv",
  "sha256": "abc123...",
  "contentSize": 12345
}
```

This allows recipients to independently verify the integrity of downloaded files.

### API enforcement

The API provides no endpoint for:
- `PUT /objects/{id}` — update existing object (does not exist)
- `DELETE /objects/{id}` — delete object (does not exist)
- `PATCH /objects/{id}/file` — replace file in place (does not exist)

The only mutation permitted on an `OutputObject` is a state transition (`state` field), which is governed by the workflow engine and audit-logged.

### Database enforcement

- `OutputObject.checksum_sha256`, `storage_key`, `filename`, `size_bytes` are set at insert time with no update path in the ORM.
- A DB trigger (PostgreSQL) or application-layer guard (SQLite dev) prevents UPDATE on these columns.
- The `AuditEvent` table has no UPDATE or DELETE grants to the application DB role.

---

## Consequences

- **Positive**: Complete provenance chain — every file that was ever reviewed is retained and verifiable.
- **Positive**: Release integrity is cryptographically verifiable end-to-end.
- **Positive**: No ambiguity about what a checker reviewed — the checksum links the review to the exact bytes.
- **Negative**: Storage costs grow over time as superseded versions accumulate. Mitigation: configurable retention policy runs cleanup after request closure (default 90 days). Retention period is documented as a compliance parameter.
- **Negative**: Large files cannot be edited in-place — researcher must re-upload the full file for any change. This is intentional and consistent with the immutability principle.
