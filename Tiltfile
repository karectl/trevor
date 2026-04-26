# Tiltfile — trevor local dev on k3d/kind

NAMESPACE = "trevor-dev"

# Tilt pushes to localhost:5005 (host-accessible), but k3s pulls from
# trevor-registry:5000 (container network DNS). host_from_cluster handles
# the URL translation so containerd never tries HTTPS on a plain-HTTP registry.
default_registry(
    "localhost:5005",
    host_from_cluster="trevor-registry:5000",
)

# IMAGE_NAME is the bare image name used in docker_build and Helm.
# default_registry rewrites it: push to localhost:5005/trevor,
# in-cluster pull from trevor-registry:5000/trevor.
IMAGE_NAME = "trevor"

# ── Docker image ─────────────────────────────────────────────────────────────
docker_build(
    IMAGE_NAME,
    ".",
    dockerfile="Dockerfile",
    live_update=[
        sync("src/", "/app/src/"),
    ],
)

# ── Dev infrastructure ────────────────────────────────────────────────────────
k8s_yaml("deploy/dev/postgres.yaml")
k8s_yaml("deploy/dev/redis.yaml")
k8s_yaml("deploy/dev/seaweedfs.yaml")
k8s_yaml("deploy/dev/seaweedfs-buckets-job.yaml")
k8s_yaml("deploy/dev/keycloak-realm.yaml")
k8s_yaml("deploy/dev/keycloak.yaml")

# ── CR8TOR CRDs + sample project ─────────────────────────────────────────────
# CRD definitions must be applied before CR instances.
k8s_yaml(listdir("deploy/dev/crds/"))
k8s_yaml(listdir("deploy/dev/sample-project/"))
k8s_resource(
    new_name="cr8tor-crds",
    objects=[
        "projects.research.karectl.io:customresourcedefinition",
        "users.identity.karectl.io:customresourcedefinition",
        "groups.identity.karectl.io:customresourcedefinition",
        "keycloakclients.identity.karectl.io:customresourcedefinition",
        "vdiinstances.karectl.io:customresourcedefinition",
    ],
    labels=["cr8tor"],
)
k8s_resource(
    new_name="sample-project",
    objects=[
        "lancs-tre-proj-1:project",
        "hardingmp:user",
        "lancs-tre-proj-1:group",
        "lancs-tre-proj-1-analyst:group",
        "lancs-tre-proj-1-admin:group",
    ],
    resource_deps=["cr8tor-crds"],
    labels=["cr8tor"],
)

# ── Helm release (trevor app + worker) ────────────────────────────────────────
k8s_yaml(
    helm(
        "helm/trevor",
        name="trevor",
        namespace=NAMESPACE,
        set=[
            "image.repository=" + IMAGE_NAME,
            "image.tag=latest",
            "replicaCount=1",
            "worker.replicaCount=1",
            "env.DEV_AUTH_BYPASS=false",
            "env.LOG_LEVEL=DEBUG",
            "env.LOG_FORMAT=console",
            "env.DATABASE_URL=postgresql+asyncpg://trevor:trevor@postgres:5432/trevor",
            "env.REDIS_URL=redis://redis:6379/0",
            "env.KEYCLOAK_URL=http://localhost:8080",
            "env.KEYCLOAK_INTERNAL_URL=http://keycloak:8080",
            "env.KEYCLOAK_REALM=karectl",
            "env.KEYCLOAK_CLIENT_ID=trevor",
            "env.S3_ENDPOINT_URL=http://seaweedfs:8333",
            "env.S3_ACCESS_KEY_ID=devaccess",
            "env.S3_SECRET_ACCESS_KEY=devsecret",
            "env.S3_QUARANTINE_BUCKET=trevor-quarantine",
            "env.S3_RELEASE_BUCKET=trevor-release",
            "env.SECRET_KEY=tilt-dev-secret-key",
        ],
    )
)

# ── Resources & port forwards ────────────────────────────────────────────────
k8s_resource("trevor-trevor", port_forwards=["8000:8000"], labels=["app"],
             resource_deps=["postgres", "redis", "seaweedfs"])
k8s_resource("postgres", port_forwards=["5432:5432"], labels=["infra"])
k8s_resource("redis", port_forwards=["6379:6379"], labels=["infra"])
k8s_resource("seaweedfs", port_forwards=["8333:8333", "9333:9333"], labels=["infra"])
k8s_resource("keycloak", port_forwards=["8080:8080"], labels=["infra"])

# ── Dev DB seed ───────────────────────────────────────────────────────────────
# Upserts Keycloak test users + project memberships into postgres.
# Runs once after trevor and keycloak are healthy.
local_resource(
    "seed-dev-db",
    cmd="uv run python scripts/seed-dev-db.py",
    resource_deps=["trevor-trevor", "keycloak"],
    labels=["app"],
)
