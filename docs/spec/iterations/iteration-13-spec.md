# Iteration 13 Spec — CRD Sync Reconciler

## Goal

Implement the CR8TOR CRD sync reconciler prescribed by ADR-0012. trevor automatically discovers projects, users, and researcher memberships from Kubernetes CRDs.

---

## Current state

| Component | Status |
|---|---|
| `Project`, `User`, `ProjectMembership` models | Implemented |
| `upsert_user` service | Implemented |
| Sample CRDs in `deploy/dev/crds/` + `deploy/dev/sample-project/` | Deployed via Tiltfile |
| CRD sync service | Missing |
| `kr8s` dependency | Not installed |
| ARQ cron job for periodic reconcile | Not wired |
| RBAC (ClusterRole/ClusterRoleBinding) | Not created |

---

## Scope

| Item | Decision |
|---|---|
| K8s client | `kr8s` (ADR-0014) |
| Sync mode | Periodic reconcile only (v1). Watch can be added later. |
| Frequency | ARQ cron every 5 minutes |
| Researcher memberships | Derived from Group CRDs per ADR-0012 |
| Checker memberships | Not touched — managed exclusively in trevor DB |
| User `keycloak_sub` | Left null until first login (existing behavior) |

Watch mode is deferred because:
- Periodic reconcile is simpler, testable, and sufficient for project creation frequency
- Watch requires long-running connection management and reconnect logic
- Can be added in a follow-up iteration if latency becomes an issue

---

## 1. Dependency

### Add `kr8s` to `pyproject.toml`

```toml
[project]
dependencies = [
    ...,
    "kr8s>=0.18",
]
```

---

## 2. CRD client module

### File: `src/trevor/crd.py`

Defines custom resource classes and provides typed access to CRD data.

```python
"""CR8TOR CRD client — custom resource definitions via kr8s."""

from __future__ import annotations

import kr8s
from kr8s.asyncio import get as kr8s_get

# Custom resource class definitions
ProjectCR = kr8s.objects.new_class(
    kind="Project",
    api_version="research.karectl.io/v1alpha1",
    namespaced=True,
)

GroupCR = kr8s.objects.new_class(
    kind="Group",
    api_version="identity.karectl.io/v1alpha1",
    namespaced=True,
)

UserCR = kr8s.objects.new_class(
    kind="User",
    api_version="identity.karectl.io/v1alpha1",
    namespaced=True,
)


async def list_project_crds(namespace: str) -> list:
    """List all Project CRDs in namespace."""
    return await kr8s_get(ProjectCR, namespace=namespace)


async def list_group_crds(namespace: str) -> list:
    """List all Group CRDs in namespace."""
    return await kr8s_get(GroupCR, namespace=namespace)


async def list_user_crds(namespace: str) -> list:
    """List all User CRDs in namespace."""
    return await kr8s_get(UserCR, namespace=namespace)
```

---

## 3. Sync service

### File: `src/trevor/services/crd_sync_service.py`

Pure business logic — takes parsed CRD data dicts, performs DB operations. No direct `kr8s` dependency (testable without K8s).

#### Helper: parse CRD data

```python
def parse_project_crd(crd_raw: dict) -> dict:
    """Extract project fields from a raw Project CRD dict.

    Returns:
        {
            "project_id": UUID | None,  # from cr8tor.io/project-id label
            "crd_name": str,            # metadata.name
            "display_name": str,        # spec.description or metadata.name
        }
    """
    metadata = crd_raw.get("metadata", {})
    labels = metadata.get("labels", {})
    spec = crd_raw.get("spec", {})

    project_id_str = labels.get("cr8tor.io/project-id")
    project_id = UUID(project_id_str) if project_id_str else None

    return {
        "project_id": project_id,
        "crd_name": metadata.get("name", ""),
        "display_name": spec.get("description", "") or metadata.get("name", ""),
    }


def parse_user_crd(crd_raw: dict) -> dict:
    """Extract user fields from a raw User CRD dict.

    Returns:
        {
            "crd_name": str,        # metadata.name
            "username": str,        # spec.username
            "given_name": str,
            "family_name": str,
            "email": str,
            "affiliation": str,
            "enabled": bool,
        }
    """
    metadata = crd_raw.get("metadata", {})
    spec = crd_raw.get("spec", {})
    return {
        "crd_name": metadata.get("name", ""),
        "username": spec.get("username", metadata.get("name", "")),
        "given_name": spec.get("given_name", ""),
        "family_name": spec.get("family_name", ""),
        "email": spec.get("email", ""),
        "affiliation": spec.get("affiliation", ""),
        "enabled": spec.get("enabled", True),
    }


def extract_researcher_memberships(groups_raw: list[dict]) -> dict[str, set[str]]:
    """From a list of Group CRD dicts, build {project_crd_name: {username, ...}}.

    Rules (ADR-0012):
    - Parent group `{project-name}` → members are researchers
    - Subgroup `{project-name}-analyst` → members are researchers
    - Subgroup `{project-name}-admin` → ignored (no automatic trevor role)
    - Deduplicate across parent + analyst
    """
    # Implementation:
    # 1. Collect all group names
    # 2. Identify parent groups (those without -analyst or -admin suffix)
    # 3. For each parent + its -analyst subgroup, union spec.members
    # Return {project_crd_name: {usernames}}
```

#### Reconcile functions

```python
async def reconcile_projects(
    project_crds: list[dict],
    session: AsyncSession,
) -> tuple[int, int, int]:
    """Sync Project CRDs into DB.

    Returns (created, updated, archived) counts.

    Algorithm:
    1. For each CRD: upsert Project (by crd_name). If cr8tor.io/project-id label
       exists, use that as Project.id (preserving karectl-wide UUID).
    2. Mark projects not in CRD list as archived.
    3. Update synced_at timestamp.
    """


async def reconcile_users(
    user_crds: list[dict],
    session: AsyncSession,
) -> tuple[int, int]:
    """Sync User CRDs into DB.

    Returns (created, updated) counts.

    Algorithm:
    1. For each CRD: upsert User by crd_name.
    2. Set active=False for users with enabled=false in CRD.
    3. Do NOT touch keycloak_sub (set on first login only).
    4. Update crd_synced_at timestamp.
    """


async def reconcile_memberships(
    researcher_map: dict[str, set[str]],
    session: AsyncSession,
) -> tuple[int, int]:
    """Sync researcher memberships from Group CRDs.

    Returns (created, removed) counts.

    Algorithm:
    1. For each project_crd_name → usernames:
       a. Look up Project by crd_name
       b. Look up each User by username
       c. Ensure ProjectMembership(role=researcher) exists
       d. Remove researcher memberships not in the CRD set
          (but NEVER remove checker/senior_checker memberships)
    2. Log WARN for role conflicts (user is both researcher and checker).
    """
```

#### Orchestrator

```python
async def full_reconcile(
    project_crds: list[dict],
    group_crds: list[dict],
    user_crds: list[dict],
    session: AsyncSession,
) -> dict:
    """Run full reconcile cycle. Returns stats dict."""
    user_stats = await reconcile_users(user_crds, session)
    project_stats = await reconcile_projects(project_crds, session)
    researcher_map = extract_researcher_memberships(group_crds)
    membership_stats = await reconcile_memberships(researcher_map, session)

    return {
        "users": {"created": user_stats[0], "updated": user_stats[1]},
        "projects": {"created": project_stats[0], "updated": project_stats[1], "archived": project_stats[2]},
        "memberships": {"created": membership_stats[0], "removed": membership_stats[1]},
    }
```

---

## 4. ARQ cron job

### Changes to `src/trevor/worker.py`

Add `crd_sync_job` as an ARQ cron job running every 5 minutes.

```python
async def crd_sync_job(ctx: dict[str, Any]) -> None:
    """Cron — reconcile CR8TOR CRDs into trevor DB."""
    from trevor.crd import list_group_crds, list_project_crds, list_user_crds
    from trevor.services.crd_sync_service import full_reconcile

    settings: Settings = ctx["settings"]
    session_factory = ctx["session_factory"]
    namespace = settings.crd_namespace  # new setting

    try:
        project_crds = [cr.raw for cr in await list_project_crds(namespace)]
        group_crds = [cr.raw for cr in await list_group_crds(namespace)]
        user_crds = [cr.raw for cr in await list_user_crds(namespace)]
    except Exception:
        logger.exception("crd_sync_job: failed to list CRDs")
        return

    async with session_factory() as session:
        stats = await full_reconcile(project_crds, group_crds, user_crds, session)
        logger.info("crd_sync_job: %s", stats)
```

Add to `WorkerSettings.cron_jobs`:

```python
cron_jobs = [
    cron(url_expiry_warning_job, hour={0}, minute=0, run_at_startup=False),
    cron(crd_sync_job, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}, run_at_startup=True),
]
```

---

## 5. Settings additions

### Changes to `src/trevor/settings.py`

```python
# CRD sync
crd_namespace: str = "trevor-dev"
crd_sync_enabled: bool = True
```

When `crd_sync_enabled=False` (tests, local dev without k3d), the cron job returns early.

---

## 6. RBAC manifest

### File: `deploy/dev/rbac-crd-reader.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: trevor-crd-reader
rules:
  - apiGroups: ["research.karectl.io"]
    resources: ["projects"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["identity.karectl.io"]
    resources: ["groups", "users"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: trevor-crd-reader
subjects:
  - kind: ServiceAccount
    name: default
    namespace: trevor-dev
roleRef:
  kind: ClusterRole
  name: trevor-crd-reader
  apiGroup: rbac.authorization.k8s.io
```

Wire into Tiltfile with `k8s_yaml()`.

---

## 7. Test plan

### File: `tests/test_crd_sync.py`

Tests use no Kubernetes cluster. They pass parsed CRD data dicts directly to the sync service functions.

#### Unit tests

| Test | Validates |
|---|---|
| `test_parse_project_crd` | Extracts project_id from label, crd_name, display_name |
| `test_parse_project_crd_no_label` | project_id is None when label missing |
| `test_parse_user_crd` | Extracts all user fields |
| `test_extract_researcher_memberships` | Parent + analyst groups merged, admin ignored |
| `test_extract_researcher_memberships_dedup` | User in both parent and analyst counted once |
| `test_reconcile_projects_create` | New CRD → new Project in DB |
| `test_reconcile_projects_update` | Changed CRD → updated display_name |
| `test_reconcile_projects_archive` | Missing CRD → status=archived |
| `test_reconcile_users_create` | New CRD → new User in DB |
| `test_reconcile_users_update` | Changed CRD → updated fields, keycloak_sub untouched |
| `test_reconcile_users_disable` | enabled=false → active=False |
| `test_reconcile_memberships_create` | Group member → researcher membership created |
| `test_reconcile_memberships_remove` | User removed from group → researcher membership deleted |
| `test_reconcile_memberships_preserve_checker` | Checker memberships never deleted by sync |
| `test_reconcile_memberships_role_conflict_logged` | User is researcher+checker → warning emitted |
| `test_full_reconcile` | End-to-end with all three CRD types |

---

## New / modified files

```
src/trevor/
  crd.py                              # NEW — kr8s custom resource definitions + list functions
  services/crd_sync_service.py        # NEW — parse, reconcile, full_reconcile
  worker.py                           # MODIFIED — add crd_sync_job cron
  settings.py                         # MODIFIED — crd_namespace, crd_sync_enabled
deploy/dev/
  rbac-crd-reader.yaml                # NEW — ClusterRole + ClusterRoleBinding
Tiltfile                              # MODIFIED — wire RBAC manifest
pyproject.toml                        # MODIFIED — add kr8s dependency
tests/
  test_crd_sync.py                    # NEW — 16+ unit tests
```

---

## Implementation order

1. `pyproject.toml` — add `kr8s>=0.18` dependency, `uv sync`
2. `src/trevor/crd.py` — custom resource classes + list functions
3. `src/trevor/services/crd_sync_service.py` — parse helpers, reconcile functions, full_reconcile
4. `src/trevor/settings.py` — add `crd_namespace`, `crd_sync_enabled`
5. `src/trevor/worker.py` — add `crd_sync_job`, wire into cron_jobs
6. `deploy/dev/rbac-crd-reader.yaml` — RBAC manifest
7. `Tiltfile` — wire RBAC YAML
8. `tests/test_crd_sync.py` — all unit tests
9. Run lint + format + full test suite
