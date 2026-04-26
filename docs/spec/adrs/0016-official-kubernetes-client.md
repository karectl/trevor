# ADR-0016 — CRD Sync: Official `kubernetes` Python Client

**Status**: Accepted  
**Date**: 2026-04  
**Deciders**: trevor project lead  
**Supersedes**: ADR-0014 (kr8s)  
**Related**: ADR-0012 (CRD sync design)

---

## Context

ADR-0014 selected `kr8s` as the Kubernetes client. After further consideration, the official `kubernetes` Python client is preferred.

Options evaluated:

| Client | Async | Maturity | Custom resources | Notes |
|---|---|---|---|---|
| `kr8s` | Native | Moderate | `new_class()` | Newer project, smaller community |
| `kubernetes` (official) | Via executor | High | `CustomObjectsApi` | CNCF-maintained, most widely used |
| `kubernetes_asyncio` | Native | Low | `CustomObjectsApi` | Unofficial fork, poorly maintained |

---

## Decision

Use the **official `kubernetes` Python client** (`kubernetes>=31.0`).

Kubernetes API calls are inherently infrequent (periodic reconcile every 5 minutes). Wrapping synchronous calls with `asyncio.get_event_loop().run_in_executor` is the standard pattern and adds negligible overhead at this call frequency.

### Rationale

- **Stability**: CNCF-maintained, tracks the Kubernetes API spec precisely, extensive production use
- **Custom resource support**: `CustomObjectsApi.list_namespaced_custom_object` provides exactly what CRD sync needs — no codegen required
- **In-cluster auth**: `config.load_incluster_config()` with `load_kube_config()` fallback is the canonical pattern
- **Ecosystem alignment**: same client used by most Kubernetes operators and controllers in Python

### Usage pattern

```python
from kubernetes import client, config

def _load_k8s_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

def _list_crds_sync(group: str, version: str, plural: str, namespace: str) -> list[dict]:
    _load_k8s_config()
    api = client.CustomObjectsApi()
    result = api.list_namespaced_custom_object(
        group=group, version=version, namespace=namespace, plural=plural,
    )
    return result.get("items", [])

# Wrap for async use in ARQ jobs
async def list_project_crds(namespace: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_list_crds_sync, "research.karectl.io", "v1alpha1", "projects", namespace)
    )
```

### Test strategy

Tests pass pre-parsed CRD data dicts directly to `crd_sync_service` functions — no Kubernetes client is invoked. `crd.py` functions are mocked at the job level in worker tests.

---

## Consequences

- **Positive**: Battle-tested, CNCF-maintained, comprehensive documentation
- **Positive**: No async wrapper library needed — executor pattern is simple and well-understood
- **Positive**: Consistent with patterns used in the broader Kubernetes Python ecosystem
- **Negative**: Sync calls wrapped with executor — acceptable at 5-minute reconcile frequency
- **Negative**: Larger dependency footprint than `kr8s` (~5MB vs ~50KB). Acceptable for a server-side service.
