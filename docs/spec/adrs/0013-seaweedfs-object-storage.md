# ADR-0013: Replace MinIO with SeaweedFS for S3-compatible Object Storage

## Status

Accepted

## Date

2026-04-26

## Context

trevor requires S3-compatible object storage for quarantine and release buckets. The initial design assumed MinIO as the local dev and self-hosted S3 backend.

MinIO changed its license from Apache 2.0 to AGPLv3 (as of June 2023). The AGPL has significant implications for organisations deploying MinIO as infrastructure:

1. **AGPL copyleft scope** — any software interacting with an AGPL-licensed service over a network may trigger disclosure obligations, depending on legal interpretation. While trevor only uses the S3 API (not MinIO internals), the risk is non-trivial for a government/research TRE deployment where legal review overhead is high.
2. **Commercial license cost** — MinIO's enterprise license is per-node, per-year. For a research environment with constrained budgets, this is an unnecessary expense when alternatives exist.
3. **Operational risk** — relying on a single vendor with an aggressive licensing strategy creates supply-chain risk.

### Alternatives considered

| Option | License | S3 compat | Kubernetes-native | Notes |
|---|---|---|---|---|
| MinIO | AGPLv3 / commercial | Full | Yes (operator) | License concern |
| SeaweedFS | Apache 2.0 | Good (S3 gateway) | Yes (Helm chart) | Lightweight, Apache-licensed |
| Ceph/RADOS | LGPL 2.1 | Full (RGW) | Yes (Rook) | Heavy for dev; good for prod |
| AWS S3 | N/A (SaaS) | Native | N/A | Prod-only; no local dev |
| LocalStack | Apache 2.0 | Partial | No official chart | Dev-only; not suitable for prod |

### Decision drivers

- Must be S3-compatible (trevor uses `aioboto3` — any S3 API works).
- Must run in Kubernetes (C-07).
- Must be usable for both local dev and self-hosted production.
- License must be permissive (Apache 2.0 or equivalent).
- Lightweight enough for k3d/kind local dev clusters.

## Decision

Replace MinIO with **SeaweedFS** as the S3-compatible object storage backend for both local development and self-hosted production.

- **Dev**: SeaweedFS runs as a single-node deployment in k3d/kind via Tilt.
- **Prod (self-hosted)**: SeaweedFS deployed via Helm chart with configurable replication.
- **Prod (cloud)**: AWS S3 / Azure Blob (S3-compatible) remains supported — trevor is storage-agnostic via `aioboto3`.

## Consequences

### Positive

- Apache 2.0 license eliminates all AGPL concerns.
- SeaweedFS is lighter than Ceph for small-to-medium deployments.
- S3 gateway mode is compatible with `aioboto3` — no code changes needed in trevor.
- Single binary makes local dev simpler than MinIO operator.

### Negative

- SeaweedFS S3 gateway does not implement 100% of the S3 API (e.g., some multipart edge cases). trevor's usage (PUT, GET, pre-signed URLs, list objects) is well within the supported subset.
- Smaller community than MinIO — fewer Stack Overflow answers, but documentation is adequate.
- Team must learn SeaweedFS operational patterns (master/volume/filer/s3 topology).

### Neutral

- `S3_ENDPOINT_URL` env var continues to abstract the backend — no application code changes.
- `aioboto3` calls remain identical; only infrastructure manifests change.
- Existing ADR-0002 (storage architecture) is unaffected — it specifies S3 API, not a specific implementation.

## Implementation notes

- Update `Tiltfile` to deploy SeaweedFS instead of MinIO.
- Update `sample.env` default `S3_ENDPOINT_URL` comment to reference SeaweedFS.
- Update `docs/runbook.md` S3 section.
- Update Helm chart values comments.
- Create SeaweedFS dev manifest (`deploy/dev/seaweedfs.yaml`) for Tilt.
- Verify all S3 operations (upload, download, pre-signed PUT/GET, list) against SeaweedFS S3 gateway.
