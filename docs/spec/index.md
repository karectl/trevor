---
icon: lucide/file-text
---

# Specification

Authoritative design documents for trevor. **Spec-first rule**: each iteration requires writing specs *before* implementation.

## Documents

| Document | Contents |
|----------|----------|
| [Constraints](constraints.md) | Non-negotiable architectural constraints (C-01 – C-13) |
| [Domain Model](domain-model.md) | Entity definitions, state machines, field-level detail |
| [Glossary](glossary.md) | Canonical domain terminology |
| [Iteration Plan](iteration-plan.md) | Ordered delivery plan |

## Architecture Decision Records

| ADR | Title | Status |
|-----|-------|--------|
| [0001](adrs/0001-frontend-framework.md) | Frontend Framework: Datastar | Accepted |
| [0002](adrs/0002-storage-architecture.md) | Storage Architecture: Two-Bucket S3 | Accepted |
| [0003](adrs/0003-database-strategy.md) | Database Strategy: SQLite → PostgreSQL | Accepted |
| [0004](adrs/0004-ro-crate-profile.md) | RO-Crate: Default Profile, Release-Only | Accepted |
| [0005](adrs/0005-agentic-checking.md) | Agentic Checking: Pydantic-AI + Statbarn | Accepted |
| [0006](adrs/0006-metadata-lineage.md) | Metadata Lineage: Linear Versioning | Accepted |
| [0007](adrs/0007-authentication.md) | Authentication: Keycloak OIDC + Local RBAC | Accepted |
| [0008](adrs/0008-kubernetes-deployment.md) | Kubernetes Deployment Architecture | Accepted |
| [0009](adrs/0009-notification-abstraction.md) | Notification Abstraction Layer | Accepted |
| [0010](adrs/0010-two-person-review.md) | Two-Person Review Rule | Accepted |
| [0011](adrs/0011-object-immutability.md) | Object Immutability + Checksum Verification | Accepted |
| [0012](adrs/0012-cr8tor-crd-sync.md) | CR8TOR CRD Sync: Project + User Mapping | Accepted |

### ADR status legend

| Status | Meaning |
|--------|---------|
| `Proposed` | Under discussion, not yet binding |
| `Accepted` | Binding — implementation must follow |
| `Superseded` | Replaced by a later ADR |
| `Deprecated` | No longer applicable |

## Iteration specs

| Iteration | Spec |
|-----------|------|
| 2 | [Airlock Request Lifecycle](iterations/iteration-2-spec.md) |
| 3 | [Agent Review](iterations/iteration-3-spec.md) |
| 4 | [Human Review](iterations/iteration-4-spec.md) |
| 5 | [Revision Cycle](iterations/iteration-5-spec.md) |
| 6 | [Release / RO-Crate](iterations/iteration-6-spec.md) |
| 7 | [Admin Dashboard & Metrics](iterations/iteration-7-spec.md) |
| 7.5 | [Datastar UI](iterations/iteration-7.5-spec.md) |
