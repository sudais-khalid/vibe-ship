# Kubernetes (opt-in)

Generate this only when the user asks for Kubernetes, names a K8s platform (EKS, GKE, AKS, DOKS, k3s, OpenShift), or already has manifests (`detect.py` → `existing.kubernetes/helm/kustomize`). For one service, Compose or a PaaS is less operational work, and it's fine to say so once.

Layout (Kustomize is built into kubectl, so it needs no extra tooling):

```
k8s/
  base/
    kustomization.yaml
    deployment.yaml
    service.yaml
    pdb.yaml
    hpa.yaml            # only if autoscaling was requested
    networkpolicy.yaml
    secret.example.yaml # shape only - never real values
  overlays/
    staging/kustomization.yaml
    production/kustomization.yaml
```

## Deployment: hardened by default

Every manifest below passes `scripts/audit.py` (no `VS-K*` findings).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  labels: { app.kubernetes.io/name: app }
spec:
  replicas: 2
  revisionHistoryLimit: 5
  selector:
    matchLabels: { app.kubernetes.io/name: app }
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }   # zero-downtime; requires an accurate readinessProbe
  template:
    metadata:
      labels: { app.kubernetes.io/name: app }
    spec:
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector: { matchLabels: { app.kubernetes.io/name: app } }
      containers:
        - name: app
          image: ghcr.io/OWNER/REPO:sha-0000000      # immutable tag; CI/kustomize sets the real one
          ports:
            - { name: http, containerPort: 8080 }
          envFrom:
            - configMapRef: { name: app-config }
            - secretRef: { name: app-secrets }
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { memory: 512Mi }                 # memory limit = OOM safety; CPU limit omitted to avoid throttling
          startupProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 5
            failureThreshold: 30                      # up to 150s to boot (JVM, Rails) before liveness kicks in
          readinessProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 15
            failureThreshold: 4
          lifecycle:
            preStop:
              sleep: { seconds: 5 }                  # let endpoints drain before SIGTERM (K8s >= 1.30)
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: tmp
          emptyDir: {}
```

Adapting it:
- **Port and probe paths** come from `detect.py`: Spring `/actuator/health/readiness` and `/liveness`, Rails `/up`, Quarkus `/q/health/ready` and `/live`.
- **`runAsUser`** must match the image's user. The templates use 10001. Exceptions: `node` = 1000, `nginx-unprivileged` = 101, .NET `APP_UID` = 1654, distroless `nonroot` = 65532.
- **Writable paths** under `readOnlyRootFilesystem` need `emptyDir` mounts: Next.js `/app/.next/cache`, Rails `/rails/tmp`, Laravel `storage/framework`, nginx `/tmp` (nginx-unprivileged writes there). If the app writes somewhere unexpected, mount it. Don't drop the setting.
- **preStop `sleep`** is native in K8s 1.30+. On older clusters use `exec: { command: ["sleep", "5"] }`, which needs a shell in the image.

## Service, PDB, NetworkPolicy

```yaml
apiVersion: v1
kind: Service
metadata: { name: app }
spec:
  selector: { app.kubernetes.io/name: app }
  ports: [{ name: http, port: 80, targetPort: http }]
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: app }
spec:
  minAvailable: 1
  selector: { matchLabels: { app.kubernetes.io/name: app } }
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: app }
spec:
  podSelector: { matchLabels: { app.kubernetes.io/name: app } }
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }   # adjust to the ingress controller namespace
      ports: [{ port: http }]
```

## HPA (only if autoscaling was requested)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: app }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: app }
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource: { name: cpu, target: { type: Utilization, averageUtilization: 70 } }
  behavior:
    scaleDown: { stabilizationWindowSeconds: 300 }
```

CPU-based HPA needs `requests.cpu` (set above) and metrics-server. Queue workers scale better on queue depth with KEDA. Mention that, don't install it.

## Ingress / Gateway

Ask which controller the cluster runs before writing this. Annotations differ, and a wrong guess routes nothing.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt   # only if cert-manager is installed
spec:
  ingressClassName: nginx                          # traefik / alb / gce / ...
  tls: [{ hosts: [app.example.com], secretName: app-tls }]
  rules:
    - host: app.example.com
      http:
        paths: [{ path: /, pathType: Prefix, backend: { service: { name: app, port: { name: http } } } }]
```

The ingress-nginx project is retired (end of maintenance March 2026). For new clusters, suggest the Gateway API (`HTTPRoute`) with Envoy Gateway, Traefik, Cilium, or the cloud controller. Keep the Ingress for clusters that already run a controller.

## Config and secrets

- `ConfigMap app-config` holds non-secret env (`LOG_LEVEL`, `PORT`).
- **Secrets are never committed.** Write `k8s/base/secret.example.yaml` with placeholder values only, and tell the user to create the real one:
  ```bash
  kubectl create secret generic app-secrets --from-env-file=.env.production
  ```
  For GitOps, recommend External Secrets Operator (AWS SM, GCP SM, Vault, 1Password), SOPS, or Sealed Secrets.
- The audit flags real data in committed `kind: Secret` (`VS-K041`) and literal secret env values (`VS-K040`).

## Migrations

A `Job` (or Helm `pre-upgrade` hook / Argo CD `PreSync` hook) using the same image and the migrate command from the stack file. Never run migrations in an `initContainer`: with N replicas that's N concurrent migrations.

## Kustomize glue

```yaml
# k8s/base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [deployment.yaml, service.yaml, pdb.yaml, networkpolicy.yaml]
configMapGenerator:
  - name: app-config
    literals: [LOG_LEVEL=info, PORT=8080]
images:
  - name: ghcr.io/OWNER/REPO
    newTag: sha-0000000
```

```yaml
# k8s/overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [../../base, ../../base/hpa.yaml]
namespace: app-prod
patches:
  - target: { kind: Deployment, name: app }
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
```

CI bumps the tag with `cd k8s/overlays/production && kustomize edit set image ghcr.io/OWNER/REPO:sha-${GITHUB_SHA}`, then `kubectl apply -k`, or commits for GitOps.

## Helm (only if the user asks or already uses Helm)

Use `helm create` for the skeleton, then move the hardened fields above into `values.yaml` (`securityContext`, `podSecurityContext`, `resources`, probes) and deploy with `helm upgrade --install app ./chart --atomic --wait --timeout 5m`. `audit.py` skips templated files (`{{ }}`), so render first to audit them: `helm template ./chart > /tmp/rendered.yaml && python3 scripts/audit.py --path /tmp/rendered.yaml`.

## Validate before handing over

```bash
kubectl apply --dry-run=client -k k8s/overlays/production     # schema sanity (no cluster changes)
kubectl kustomize k8s/overlays/production > /tmp/out.yaml && python3 scripts/audit.py --path /tmp/out.yaml
# if installed: kubeconform -strict -summary /tmp/out.yaml
```
