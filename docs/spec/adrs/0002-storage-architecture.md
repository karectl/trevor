# ADR-0002 — Storage Architecture: Two-Bucket S3 Model

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

AzureTRE's airlock implementation moves data through multiple storage accounts at different stages, adding operational complexity and latency. The brief requires a simpler, metadata-driven approach that still enforces the security boundary.

The key constraints are:
- Researchers must never have direct storage credentials.
- Files under review must not be accessible outside the TRE.
- Only approved, packaged outputs may cross to external storage.
- The transition must be auditable and verifiable via checksum.

---

## Decision

Use a **two-bucket model**:

### Bucket 1: Quarantine (`trevor-quarantine`)

- **Location**: Inside TRE network boundary
- **Access**: trevor application only (IAM role / service account)
- **Contents**: All uploaded files (ingress and egress) while under review
- **Lifecycle**: Files are retained for the configured retention period after request closure (default: 90 days), then deleted by a background job. The metadata and audit trail are never deleted.
- **Encryption**: Server-side encryption required (AES-256 or KMS-managed)

### Bucket 2: Release (`trevor-release`)

- **Location**: Accessible from outside TRE boundary
- **Access**: trevor application only for writes; public-accessible only via pre-signed URLs
- **Contents**: Approved RO-Crate packages (egress only)
- **Lifecycle**: Files retained for the configured download window (default: 30 days), then deleted. Metadata record is retained permanently.
- **Encryption**: Server-side encryption required

### Data flow

```
[Researcher uploads file]
        │
        ▼
trevor API ──── streams ────► Quarantine bucket
        │                     (storage_key recorded, checksum computed)
        │
[Review process in trevor DB]
        │
[Request approved]
        │
        ▼
trevor background job:
  1. Assembles RO-Crate from DB metadata + files in quarantine
  2. Streams RO-Crate zip to Release bucket
  3. Verifies checksum
  4. Generates pre-signed URL (configurable TTL, default 7 days)
  5. Delivers URL via notification system
  6. Records ReleaseRecord in DB
```

### Why not a single bucket with prefix-based ACLs?

Bucket-level policies are simpler to audit, less error-prone than prefix ACLs, and provide a cleaner security boundary. A misconfiguration in prefix ACLs could silently expose quarantine content. Separate buckets make the boundary explicit and independently auditable.

### Ingress flow

For ingress (importing data into the TRE), the flow is reversed:
- External submitter uploads to a dedicated ingress staging location (pre-signed PUT URL, time-limited)
- trevor copies to quarantine on confirmation
- Review proceeds as normal
- On approval, trevor generates a pre-signed URL to the quarantine object for the workspace to consume, or triggers a workspace-specific delivery mechanism

---

## Consequences

- **Positive**: Clear security boundary; no possibility of pre-release data leaking to the release bucket.
- **Positive**: No file shuffling through intermediate accounts.
- **Positive**: Storage operations are a background concern — the review workflow is entirely metadata-driven in the DB.
- **Negative**: Requires two S3 bucket configurations in Helm values.
- **Negative**: Ingress staging for external submitters needs careful URL management (handled by trevor's ingress request flow).

---

## Configuration

```yaml
# Helm values (trevor)
storage:
  quarantine:
    bucket: trevor-quarantine
    endpoint: https://minio.internal.karectl.example
    region: us-east-1
  release:
    bucket: trevor-release
    endpoint: https://s3.example.com
    region: us-east-1
  presigned_url_ttl_seconds: 604800  # 7 days
  quarantine_retention_days: 90
  release_retention_days: 30
```

Credentials are injected via Kubernetes secrets and mounted as environment variables. trevor uses `boto3` (via `aioboto3` for async) for all S3 operations.
