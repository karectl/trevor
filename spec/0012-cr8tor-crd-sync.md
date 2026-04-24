# ADR-0012 — CR8TOR CRD Sync: Project and User Mapping

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor does not own the project or user model. These are managed by CR8TOR via three Kubernetes CRD kinds across two API groups:

| Kind | API Group | Purpose |
|------|-----------|---------|
| `Project` | `research.karectl.io/v1alpha1` | Project definition and resources |
| `Group` | `identity.karectl.io/v1alpha1` | Project membership via group/subgroup structure |
| `User` | `identity.karectl.io/v1alpha1` | Individual user identity and attributes |

trevor must periodically reconcile these CRDs into its own database to know:
- Which projects exist and their status
- Which users belong to which projects
- What role those users hold within a project (for trevor's RBAC model)

trevor has **read-only** access to all CRDs (C-06).

---

## CRD structure analysis

### Project CRD

```yaml
apiVersion: research.karectl.io/v1alpha1
kind: Project
metadata:
  name: lancs-tre-proj-1                          # k8s resource name → Project.crd_name
  labels:
    cr8tor.io/project-id: 355783fb-...            # canonical UUID → Project.id
    cr8tor.io/created-at: '20260413'
spec:
  description: cardiometabolicfactors             # → Project.display_name (fallback to metadata.name)
  resources: [...]                                # not synced to trevor DB (not needed)
```

**Canonical project ID**: `cr8tor.io/project-id` label. trevor uses this UUID as its `Project.id`, preserving continuity with the rest of the karectl platform.

**Project status**: CR8TOR does not currently expose a `status` field on the Project CRD. trevor infers status from CRD presence:
- CRD exists → `active`
- CRD previously seen but now absent from the cluster → `archived`
- (Future: CR8TOR may add a `spec.suspended` field — trevor will add handling then)

### Group CRDs

Three groups are created per project following a naming convention:

| Group name pattern | Role in trevor |
|-------------------|----------------|
| `{project-name}` | Parent group — members here get `researcher` role |
| `{project-name}-analyst` | Analyst subgroup — members get `researcher` role |
| `{project-name}-admin` | Admin subgroup — members get no automatic trevor role¹ |

¹ Project admin in CR8TOR does not automatically confer `output_checker` or `senior_checker` in trevor. These are assigned explicitly by a `tre_admin` via trevor's own UI. This maintains the separation of concerns: CR8TOR manages workspace access; trevor manages airlock review authority.

**Membership resolution**: trevor reads `spec.members` from both the parent group and the `-analyst` subgroup. The union (deduplicated) is the set of `researcher` members for that project.

```yaml
# lancs-tre-proj-1-analyst
spec:
  members:
    - hardingmp    # → ProjectMembership(user=hardingmp, project=lancs-tre-proj-1, role=researcher)
```

**Note**: A user appearing in multiple groups for the same project results in a single `ProjectMembership` record (the `researcher` role is idempotent across parent and analyst groups).

### User CRD

```yaml
apiVersion: identity.karectl.io/v1alpha1
kind: User
metadata:
  name: hardingmp                     # → User.crd_name; also Keycloak username
spec:
  username: hardingmp                 # → User.username (Keycloak subject lookup key)
  given_name: Mike
  family_name: Harding
  affiliation: Lancaster University
  email: mph@lancaster.ac.uk          # → User.email
  enabled: true                       # → if false, mark User.active = False
  password: hardingmp                 # IGNORED by trevor — Keycloak manages credentials
```

trevor syncs `given_name`, `family_name`, `email`, `enabled`, and `affiliation` from the User CRD. The `keycloak_sub` (JWT `sub` claim) is populated on first successful Keycloak login — trevor cannot derive it from the CRD alone.

---

## Decision

### Sync mechanism: Watch + periodic reconcile

trevor uses two complementary sync strategies:

**1. Kubernetes Watch (real-time)**  
A long-running watch on `Project`, `Group`, and `User` CRDs via the Kubernetes Python client. Events (ADDED, MODIFIED, DELETED) are processed immediately and enqueued as ARQ jobs. This keeps trevor responsive to project changes.

**2. Periodic full reconcile (CronJob)**  
A CronJob runs every 5 minutes to perform a full list-and-reconcile pass. This handles watch reconnection gaps and ensures consistency even if watch events are missed.

```yaml
# cronjob-crd-sync
schedule: "*/5 * * * *"
```

### Sync algorithm

```python
async def reconcile_projects(k8s_client, session):
    # 1. List all Project CRDs
    projects = await k8s_client.list("research.karectl.io", "v1alpha1", "projects")
    seen_ids = set()

    for crd in projects.items:
        project_id = UUID(crd.metadata.labels["cr8tor.io/project-id"])
        seen_ids.add(project_id)
        await upsert_project(session, project_id, crd)

    # 2. Archive projects no longer in cluster
    await archive_missing_projects(session, seen_ids)


async def reconcile_memberships(k8s_client, session):
    # 1. List all Group CRDs
    groups = await k8s_client.list("identity.karectl.io", "v1alpha1", "groups")

    # 2. For each project, derive researcher memberships from
    #    parent group + -analyst subgroup members
    for project_crd_name, member_usernames in extract_researcher_memberships(groups):
        await sync_researcher_memberships(session, project_crd_name, member_usernames)
    # Note: output_checker / senior_checker memberships are NOT derived from CRDs
    # They are managed exclusively in trevor's DB by tre_admin


async def reconcile_users(k8s_client, session):
    users = await k8s_client.list("identity.karectl.io", "v1alpha1", "users")
    for crd in users.items:
        await upsert_user_from_crd(session, crd)
        # keycloak_sub remains null until first login
```

### Role mapping table

| CR8TOR group | trevor role assigned | Notes |
|-------------|---------------------|-------|
| `{project}` (parent) | `researcher` | Members synced from `spec.members` |
| `{project}-analyst` | `researcher` | Same; union with parent |
| `{project}-admin` | *(none)* | No automatic trevor role; `tre_admin` assigns checker roles manually |

### Conflict guard

The sync job enforces the constraint that a user cannot hold both `researcher` and `output_checker`/`senior_checker` on the same project (C-04). If a `tre_admin` has manually assigned a checker role to a user who is also a CRD-synced researcher on that project, the sync job:

1. Logs a `WARN` audit event: `crd_sync.role_conflict_detected`
2. Does **not** remove the manually-assigned checker role (trevor-DB roles take precedence for checker assignments)
3. Emits a notification to `tre_admin` users to resolve the conflict

This is an operational edge case but must be handled gracefully rather than silently.

### User `keycloak_sub` resolution

The CRD provides `spec.username`. On first Keycloak login, the JWT `preferred_username` claim is matched against `User.username` to associate the `keycloak_sub`. Until this association is made:
- The user record exists in trevor DB (from CRD sync)
- The user cannot log into trevor (no `keycloak_sub` → no session)
- Once logged in, `keycloak_sub` is permanently set

---

## RBAC permissions required

```yaml
# ServiceAccount: trevor
# ClusterRole bound to trevor ServiceAccount
rules:
  - apiGroups: ["research.karectl.io"]
    resources: ["projects"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["identity.karectl.io"]
    resources: ["groups", "users"]
    verbs: ["get", "list", "watch"]
```

---

## Consequences

- **Positive**: trevor automatically discovers new projects and researchers as they are created in CR8TOR — no manual registration step.
- **Positive**: User data (name, email, affiliation) stays current with the CRD without trevor maintaining a separate sync from Keycloak's admin API.
- **Positive**: Clear separation: CR8TOR owns workspace membership; trevor owns airlock review authority. CR8TOR changes cannot accidentally grant or revoke checker access.
- **Negative**: `-admin` group members do not automatically get any trevor role — TRE admins must be explicitly configured in trevor. This is intentional but requires an operational step on project creation.
- **Mitigation**: trevor emits a notification to `tre_admin` users when a new project is synced with no checker assignments yet.
- **Negative**: Watch reconnection gaps mean brief delays in reflecting CRD changes. Mitigated by the 5-minute reconcile CronJob.
- **Negative**: `keycloak_sub` is only resolved on first login — users who have never logged into trevor cannot be assigned as checkers by UUID lookup alone. Mitigation: checker assignment in the UI uses username/email search (resolved from User CRD sync) rather than requiring a prior login.
