# Tiltfile — trevor local dev on k3d/kind
# Requires: tilt, k3d or kind, helm, kubectl

# ── Config ────────────────────────────────────────────────────────────────────
REGISTRY = "localhost:5005"
IMAGE_NAME = REGISTRY + "/trevor"

# ── Docker image ─────────────────────────────────────────────────────────────
docker_build(
    IMAGE_NAME,
    ".",
    dockerfile="Dockerfile",
    live_update=[
        sync("src/", "/app/src/"),
    ],
)

# ── Helm release ─────────────────────────────────────────────────────────────
k8s_yaml(
    helm(
        "helm/trevor",
        name="trevor",
        namespace="trevor-dev",
        values=["helm/trevor/values.yaml", "tilt-values.yaml"],
        set=[
            "image.repository=" + IMAGE_NAME,
            "image.tag=latest",
            "env.DEV_AUTH_BYPASS=true",
        ],
    )
)

k8s_resource(
    "trevor-trevor",
    port_forwards=["8000:8000"],
    labels=["app"],
)

# ── Local dev dependencies (MinIO, Keycloak, Redis) ──────────────────────────
# Uncomment and configure these once the respective Helm charts / manifests
# are added to the repo.

# k8s_yaml("deploy/dev/minio.yaml")
# k8s_yaml("deploy/dev/keycloak.yaml")
# k8s_yaml("deploy/dev/redis.yaml")
