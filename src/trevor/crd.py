"""CR8TOR CRD client — custom resource access via official kubernetes client."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)


def _load_k8s_config() -> None:
    """Load in-cluster config, falling back to kubeconfig for local dev."""
    from kubernetes import client, config  # noqa: F401

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _list_crds_sync(group: str, version: str, plural: str, namespace: str) -> list[dict]:
    """Synchronous CRD list call — run via executor to avoid blocking."""
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    result = api.list_namespaced_custom_object(
        group=group,
        version=version,
        namespace=namespace,
        plural=plural,
    )
    items: list[dict] = result.get("items", [])
    return items


async def list_project_crds(namespace: str) -> list[dict]:
    """List all Project CRDs in namespace."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        partial(_list_crds_sync, "research.karectl.io", "v1alpha1", "projects", namespace),
    )


async def list_group_crds(namespace: str) -> list[dict]:
    """List all Group CRDs in namespace."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        partial(_list_crds_sync, "identity.karectl.io", "v1alpha1", "groups", namespace),
    )


async def list_user_crds(namespace: str) -> list[dict]:
    """List all User CRDs in namespace."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        partial(_list_crds_sync, "identity.karectl.io", "v1alpha1", "users", namespace),
    )
