# trevor — Architectural Constraints

Constraints are non-negotiable properties of the system. They differ from ADRs in that they are not decisions to be revisited — they are fixed by the environment, regulation, or the platform contract.

---

## C-01 — Network boundary enforcement

trevor MUST operate across two distinct network zones:

- **Internal zone**: inside the TRE workspace network boundary. Researchers upload here. Output checking happens here. No unauthenticated external access.
- **External zone**: outside the TRE boundary. Only cleared, approved, packaged outputs are accessible here, via time-limited pre-signed URLs.

No file path may ever bypass this boundary transition. A file may only move from internal to external storage after passing a complete, recorded approval workflow.

---

## C-02 — Researcher isolation from storage

Researchers interact with files **only** through the trevor UI and API. They have no direct credentials for or access to any storage account (internal or external S3). trevor acts as the sole storage proxy.

---

## C-03 — Object immutability

Once an output object has been submitted to a request, its content is immutable. A researcher who needs to change a file MUST create a replacement object. The original object is retained in storage and in the audit trail permanently. No destructive operations on submitted objects are permitted.

This must be verifiable via SHA-256 checksums recorded at upload time and verified at each state transition.

---

## C-04 — Dual review requirement

Every output request MUST be reviewed by at least two distinct reviewers before it can be approved for release. One reviewer MAY be the autonomous agent. Both reviews MUST be recorded with a timestamp, reviewer identity, and decision. No single human or agent can both submit and approve a request. A researcher MUST NOT act as a checker on any request belonging to a project they are a member of.

---

## C-05 — Complete audit trail

Every state transition, every review decision, every metadata change, every file upload, every download event MUST be recorded with:
- Timestamp (UTC)
- Actor identity (user ID or `agent:trevor-agent`)
- Action type
- Before/after state where applicable

The audit log is append-only. No audit record may be deleted or modified.

---

## C-06 — Project model is read-only for trevor

trevor reads project and workspace data from Kubernetes CRDs maintained by CR8TOR. trevor MUST NOT write to these CRDs. If a project is deleted or suspended in CR8TOR, trevor MUST reflect this by preventing new requests while preserving existing audit history.

---

## C-07 — Kubernetes-native only

trevor is designed to run exclusively on Kubernetes. There is no supported path for running trevor outside of a Kubernetes cluster (e.g. bare Docker Compose for production). Local development may use k3s, kind, or minikube.

---

## C-08 — Horizontal scalability

The trevor application tier MUST be stateless. Session state, locks, and job state MUST be stored in the database or a shared cache, never in process memory. This enables safe horizontal scaling of application pods.

---

## C-09 — No SACRO tooling dependency

trevor does NOT depend on the SACRO Python library or ACRO tooling. Statistical disclosure control assessment is performed by trevor's own autonomous agent, which applies statbarn-based rules independently. This constraint exists to avoid coupling trevor's deployment to SACRO's release cycle and to allow the agent's rule set to evolve independently.

---

## C-10 — Authentication via karectl Keycloak only

trevor MUST use the karectl Keycloak instance for all authentication. trevor MUST NOT maintain its own user credential store. JWT tokens issued by Keycloak carry role claims that trevor uses for RBAC. trevor syncs role assignments into its own database for audit purposes but Keycloak is the source of truth for identity.

---

## C-11 — RO-Crate produced at release only

An RO-Crate package is generated as the final artefact of an approved egress request. trevor does NOT maintain a live/draft RO-Crate throughout the request lifecycle. The crate is assembled from the database's metadata and the approved objects at the point of release. This avoids maintaining a parallel metadata store that can drift from the database.

---

## C-12 — Ingress and egress are both in scope

trevor manages both directions of airlock:
- **Egress**: researcher submits outputs from inside the TRE for export to the outside world.
- **Ingress**: external data or code is submitted for import into a TRE workspace, subject to the same dual-review process.

The data flow direction differs but the review and audit model is identical.

---

## C-13 — Technology stack is fixed

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Backend framework | FastAPI | Platform standard |
| Validation | Pydantic v2 | Platform standard |
| ORM | SQLModel | Platform standard |
| Package / env management | uv | Platform standard |
| Linting / formatting | ruff | Platform standard |
| Pre-commit hooks | pre-commit | Platform standard |
| Frontend | Datastar (see ADR-0001) | Selected |
| Agent framework | Pydantic-AI (see ADR-0005) | Selected |
| LLM backend | OpenAI-compatible endpoint | Configurable via `AGENT_OPENAI_BASE_URL` |
| Object storage | S3-compatible (MinIO / AWS S3) | Platform standard |
| Auth | Keycloak OIDC | Platform standard |
| Container orchestration | Kubernetes | Platform standard |
| Task queue | ARQ + Redis | Selected (see ADR-0008) |

Deviations from this stack require a new ADR and explicit sign-off.
