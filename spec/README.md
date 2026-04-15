# trevor — Spec Directory

**trevor** is the egress/airlock microservice for the karectl Trusted Research Environment (TRE) platform. It manages the controlled import and export of research outputs across the TRE security boundary.

## Directory structure

```
spec/
├── README.md               — this file
├── CONSTRAINTS.md          — hard architectural and operational constraints
├── GLOSSARY.md             — canonical domain terminology
├── DOMAIN_MODEL.md         — entity relationships and lifecycle state machines
├── ITERATION_PLAN.md       — ordered delivery slices
└── adr/
    ├── 0001-frontend-framework.md
    ├── 0002-storage-architecture.md
    ├── 0003-database-strategy.md
    ├── 0004-ro-crate-profile.md
    ├── 0005-agentic-checking.md
    ├── 0006-metadata-lineage.md
    ├── 0007-authentication.md
    ├── 0008-kubernetes-deployment.md
    ├── 0009-notification-abstraction.md
    ├── 0010-two-person-review.md
    └── 0011-object-immutability.md
```

## ADR status legend

| Status | Meaning |
|--------|---------|
| `Proposed` | Under discussion, not yet binding |
| `Accepted` | Binding — implementation must follow |
| `Superseded` | Replaced by a later ADR (link provided) |
| `Deprecated` | No longer applicable |

## Relationship to karectl

trevor is a karectl microservice. It:
- Reads project and workspace definitions from Kubernetes CRDs managed by **CR8TOR**
- Delegates identity and authentication to the karectl **Keycloak** instance
- Runs its own isolated database and storage references
- Is deployed via Helm chart into the karectl Kubernetes cluster

trevor does **not** own the project model. It subscribes to it.
