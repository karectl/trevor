# Iteration 11 Spec — Production Helm Chart Completion

## Goal

Complete the trevor Helm chart so it is production-ready: all Kubernetes resources templated, secrets properly injected, worker deployment, ingress, autoscaling, migration init container, pod disruption budget, and network policies.

---

## Current state audit

### What exists

| File | Status | Notes |
|---|---|---|
| `Chart.yaml` | Complete | Metadata correct |
| `values.yaml` | Complete | All settings present |
| `templates/_helpers.tpl` | Complete | Standard helpers |
| `templates/deployment.yaml` | **Incomplete** | API only; no worker, no init container, no secret env vars |

### What is missing

| Template | Priority | Purpose |
|---|---|---|
| `templates/service.yaml` | **Critical** | ClusterIP service for the API pod |
| `templates/serviceaccount.yaml` | **Critical** | ServiceAccount (referenced in deployment but not created) |
| `templates/ingress.yaml` | High | Ingress resource (gated by `.Values.ingress.enabled`) |
| `templates/worker-deployment.yaml` | **Critical** | ARQ worker Deployment (gated by `.Values.worker.enabled`) |
| `templates/hpa.yaml` | High | HorizontalPodAutoscaler (gated by `.Values.autoscaling.enabled`) |
| `templates/pdb.yaml` | High | PodDisruptionBudget (prod HA) |
| `templates/networkpolicy.yaml` | Medium | Network policies for pod-to-pod traffic |
| `templates/migration-job.yaml` | High | Alembic migration Job (pre-upgrade hook) |
| `templates/NOTES.txt` | Low | Post-install usage notes |
| `deployment.yaml` updates | **Critical** | Wire `envFromSecrets`, add init container option, startup probe |

---

## 1. Service template

### `templates/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "trevor.fullname" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    {{- include "trevor.selectorLabels" . | nindent 4 }}
```

---

## 2. ServiceAccount template

### `templates/serviceaccount.yaml`

```yaml
{{- if .Values.serviceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "trevor.serviceAccountName" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
```

---

## 3. Ingress template

### `templates/ingress.yaml`

```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "trevor.fullname" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- if .Values.ingress.className }}
  ingressClassName: {{ .Values.ingress.className }}
  {{- end }}
  {{- if .Values.ingress.tls }}
  tls:
    {{- range .Values.ingress.tls }}
    - hosts:
        {{- range .hosts }}
        - {{ . | quote }}
        {{- end }}
      secretName: {{ .secretName }}
    {{- end }}
  {{- end }}
  rules:
    {{- range .Values.ingress.hosts }}
    - host: {{ .host | quote }}
      http:
        paths:
          {{- range .paths }}
          - path: {{ .path }}
            pathType: {{ .pathType }}
            backend:
              service:
                name: {{ include "trevor.fullname" $ }}
                port:
                  number: {{ $.Values.service.port }}
          {{- end }}
    {{- end }}
{{- end }}
```

---

## 4. Worker Deployment

### `templates/worker-deployment.yaml`

Separate Deployment for the ARQ worker. Same image, different command. Shares env vars and secrets with the API.

```yaml
{{- if .Values.worker.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "trevor.fullname" . }}-worker
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
    app.kubernetes.io/component: worker
spec:
  replicas: {{ .Values.worker.replicaCount }}
  selector:
    matchLabels:
      {{- include "trevor.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: worker
  template:
    metadata:
      labels:
        {{- include "trevor.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: worker
    spec:
      serviceAccountName: {{ include "trevor.serviceAccountName" . }}
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
      containers:
        - name: worker
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: {{ toYaml .Values.worker.command | nindent 12 }}
          resources:
            {{- toYaml .Values.worker.resources | nindent 12 }}
          env:
            {{- range $k, $v := .Values.env }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
          {{- range .Values.envFromSecrets }}
          envFrom:
            - secretRef:
                name: {{ .secretName }}
          {{- end }}
{{- end }}
```

**Note**: Worker has no ports, no probes (ARQ is a background process).

---

## 5. Updated API Deployment

### Changes to `templates/deployment.yaml`

1. **`envFromSecrets`** — wire secret references into the container spec.
2. **Init container** — optional Alembic migration before API starts.
3. **Startup probe** — give the app time to start before liveness kicks in.
4. **Configurable probe paths and timing**.

Key additions:

```yaml
spec:
  template:
    spec:
      {{- if .Values.migrations.enabled }}
      initContainers:
        - name: migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          command: ["uv", "run", "alembic", "upgrade", "head"]
          env:
            {{- range $k, $v := .Values.env }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
          {{- range .Values.envFromSecrets }}
          envFrom:
            - secretRef:
                name: {{ .secretName }}
          {{- end }}
      {{- end }}
      containers:
        - name: trevor
          ...
          startupProbe:
            httpGet:
              path: /health
              port: http
            failureThreshold: 30
            periodSeconds: 2
          env:
            ...
          {{- range .Values.envFromSecrets }}
          envFrom:
            - secretRef:
                name: {{ .secretName }}
          {{- end }}
```

### New values

```yaml
migrations:
  enabled: true  # run alembic upgrade head as init container
```

---

## 6. HorizontalPodAutoscaler

### `templates/hpa.yaml`

```yaml
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "trevor.fullname" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "trevor.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
{{- end }}
```

When `autoscaling.enabled=true`, the `replicaCount` in the Deployment is ignored by HPA (HPA manages replica count).

---

## 7. PodDisruptionBudget

### `templates/pdb.yaml`

```yaml
{{- if gt (int .Values.replicaCount) 1 }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "trevor.fullname" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
spec:
  minAvailable: 1
  selector:
    matchLabels:
      {{- include "trevor.selectorLabels" . | nindent 6 }}
{{- end }}
```

Only created when `replicaCount > 1`. Ensures at least 1 pod stays available during node drains and upgrades.

---

## 8. Network policies

### `templates/networkpolicy.yaml`

Optional, gated by `.Values.networkPolicy.enabled` (default `false`).

```yaml
{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "trevor.fullname" . }}
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "trevor.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Allow traffic from ingress controller
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.networkPolicy.ingressNamespace | default "ingress-nginx" }}
      ports:
        - port: 8000
          protocol: TCP
    # Allow traffic from Prometheus scraper
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.networkPolicy.monitoringNamespace | default "monitoring" }}
      ports:
        - port: 8000
          protocol: TCP
  egress:
    # Allow DNS
    - to:
        - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    # Allow PostgreSQL
    - to:
        - podSelector:
            matchLabels: {}
      ports:
        - port: 5432
          protocol: TCP
    # Allow Redis
    - to:
        - podSelector:
            matchLabels: {}
      ports:
        - port: 6379
          protocol: TCP
    # Allow S3 (SeaweedFS / external)
    - to:
        - podSelector:
            matchLabels: {}
      ports:
        - port: 8333
          protocol: TCP
        - port: 443
          protocol: TCP
    # Allow Keycloak
    - to:
        - podSelector:
            matchLabels: {}
      ports:
        - port: 8080
          protocol: TCP
    # Allow OTel collector
    - to:
        - podSelector:
            matchLabels: {}
      ports:
        - port: 4317
          protocol: TCP
{{- end }}
```

### New values

```yaml
networkPolicy:
  enabled: false
  ingressNamespace: "ingress-nginx"
  monitoringNamespace: "monitoring"
```

---

## 9. Migration Job (Helm hook alternative)

### `templates/migration-job.yaml`

An alternative to the init container approach: a Helm pre-upgrade hook Job.

```yaml
{{- if .Values.migrations.hookEnabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "trevor.fullname" . }}-migrate
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": pre-upgrade,pre-install
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: 3
  template:
    spec:
      serviceAccountName: {{ include "trevor.serviceAccountName" . }}
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
      restartPolicy: Never
      containers:
        - name: migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          command: ["uv", "run", "alembic", "upgrade", "head"]
          env:
            {{- range $k, $v := .Values.env }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
          {{- range .Values.envFromSecrets }}
          envFrom:
            - secretRef:
                name: {{ .secretName }}
          {{- end }}
{{- end }}
```

### Decision: init container vs Helm hook

| | Init container | Helm hook Job |
|---|---|---|
| Runs on every pod restart | Yes | No (only on install/upgrade) |
| Blocks pod startup | Yes | No (runs before deploy) |
| Visible in Helm history | No | Yes |
| Parallel migration risk | Yes (if multi-replica) | No (single Job) |
| **Recommendation** | Dev only | **Prod (default)** |

Use Helm hook Job (`migrations.hookEnabled=true`) for production. Init container (`migrations.enabled=true`) for dev/Tilt where simplicity matters.

### New values

```yaml
migrations:
  enabled: false       # init container (dev)
  hookEnabled: true    # Helm pre-upgrade hook Job (prod)
```

---

## 10. NOTES.txt

### `templates/NOTES.txt`

```
trevor {{ .Chart.AppVersion }} deployed to {{ .Release.Namespace }}.

API:
  kubectl port-forward svc/{{ include "trevor.fullname" . }} {{ .Values.service.port }}:{{ .Values.service.port }}
  curl http://localhost:{{ .Values.service.port }}/health

{{- if .Values.ingress.enabled }}
Ingress:
  {{- range .Values.ingress.hosts }}
  http{{ if $.Values.ingress.tls }}s{{ end }}://{{ .host }}
  {{- end }}
{{- end }}

Metrics: GET /metrics (Prometheus scrape target)

Logs: kubectl logs -f deploy/{{ include "trevor.fullname" . }}
```

---

## 11. Values additions summary

New values to add to `values.yaml`:

```yaml
migrations:
  enabled: false       # init container migration
  hookEnabled: true    # Helm hook Job migration (recommended for prod)

networkPolicy:
  enabled: false
  ingressNamespace: "ingress-nginx"
  monitoringNamespace: "monitoring"
```

---

## Complete file list

```
helm/trevor/
  Chart.yaml                          # UNCHANGED
  values.yaml                         # MODIFIED — add migrations, networkPolicy sections
  templates/
    _helpers.tpl                      # UNCHANGED
    deployment.yaml                   # MODIFIED — envFromSecrets, init container, startup probe
    service.yaml                      # NEW
    serviceaccount.yaml               # NEW
    ingress.yaml                      # NEW
    worker-deployment.yaml            # NEW
    hpa.yaml                          # NEW
    pdb.yaml                          # NEW
    networkpolicy.yaml                # NEW
    migration-job.yaml                # NEW
    NOTES.txt                         # NEW
```

---

## Test plan

1. `helm template trevor helm/trevor` renders without errors.
2. `helm template` with `ingress.enabled=true` includes Ingress resource.
3. `helm template` with `worker.enabled=false` omits worker Deployment.
4. `helm template` with `autoscaling.enabled=true` includes HPA.
5. `helm template` with `networkPolicy.enabled=true` includes NetworkPolicy.
6. `helm template` with `migrations.hookEnabled=true` includes migration Job with hook annotations.
7. `envFromSecrets` entries appear in both API and worker container specs.
8. `helm install --dry-run` against a test cluster succeeds.
9. Full `helm install` in k3d dev cluster brings all pods to Running.
10. Migration Job runs Alembic against PostgreSQL successfully on first install.
11. `helm upgrade` triggers migration Job before deploying new pods.
12. PDB is only created when `replicaCount > 1`.
