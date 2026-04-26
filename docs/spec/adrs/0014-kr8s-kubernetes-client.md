# ADR-0014 — CRD Sync: `kr8s` Async Kubernetes Client

**Status**: Superseded by ADR-0016  
**Date**: 2026-04  
**Deciders**: trevor project lead  
**Supersedes**: None  
**Related**: ADR-0012 (CRD sync design)

---

## Context

ADR-0012 prescribes a watch + periodic reconcile pattern for syncing CR8TOR CRDs into trevor's database. The implementation needs a Kubernetes Python client. Options:

1. **`kubernetes` (official)** — synchronous by default; `kubernetes_asyncio` fork adds async but is poorly maintained
2. **`kr8s`** — modern, async-native, lightweight Kubernetes client. Supports custom resources, watches, and list operations natively. Apache 2.0 license.
3. **`httpx` + raw API calls** — maximum control but requires reimplementing auth, discovery, pagination, and watch protocol

---

## Decision

Use **`kr8s`** as the Kubernetes client library.

### Rationale

- Async-native — fits trevor's async architecture (FastAPI, SQLModel, aioboto3, ARQ)
- First-class custom resource support via `kr8s.objects.new_class()`
- Built-in watch support with automatic reconnection
- Lightweight — no dependency on the heavy `kubernetes` client protobuf stack
- Actively maintained, growing adoption in the Python K8s ecosystem
- Automatic in-cluster auth via service account token

### Usage pattern

```python
import kr8s

# Define custom resource classes
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

# List all
projects = await kr8s.asyncio.get(ProjectCR, namespace="trevor-dev")

# Watch
async for event, obj in kr8s.asyncio.watch(ProjectCR, namespace="trevor-dev"):
    if event in ("ADDED", "MODIFIED"):
        await upsert_project(session, obj)
    elif event == "DELETED":
        await archive_project(session, obj)
```

### Test strategy

Tests mock `kr8s` at the function level — no real cluster needed. The reconcile service functions accept pre-parsed CRD data dicts, keeping business logic testable without K8s dependencies.

---

## Consequences

- **Positive**: Clean async API aligns with trevor's architecture
- **Positive**: Custom resource support without YAML codegen
- **Positive**: Lightweight dependency (~50KB vs ~5MB for official client)
- **Negative**: Less established than official client — risk of breaking changes. Mitigation: pin version, integration test in CI with k3d
- **Negative**: New dependency. Mitigation: `kr8s` has minimal transitive dependencies (httpx, pydantic)
