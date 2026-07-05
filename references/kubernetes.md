# Kubernetes Manifests (Opt-in)

Only generate these if the user asks for Kubernetes specifically, names a K8s-based platform (EKS, GKE, AKS, plain K8s cluster), or says Docker Compose isn't enough for their scale needs. Don't default to K8s for a simple app - it's real operational overhead.

## Minimal Deployment + Service

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  labels:
    app: app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: ghcr.io/OWNER/REPO:latest
          ports:
            - containerPort: 3000
          envFrom:
            - secretRef:
                name: app-secrets
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "1"
              memory: "512Mi"
          readinessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 15
            periodSeconds: 20
---
apiVersion: v1
kind: Service
metadata:
  name: app
spec:
  selector:
    app: app
  ports:
    - port: 80
      targetPort: 3000
  type: ClusterIP
```

Replace `OWNER/REPO` with the actual GHCR path, and the port/health path with what was detected for the app.

## Secrets

Never write real values into a committed manifest. Generate a `k8s/secrets.example.yaml` showing the shape, and instruct the user to create the real one with:

```bash
kubectl create secret generic app-secrets --from-env-file=.env
```

## Horizontal Pod Autoscaler (only if user wants autoscaling)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

## Rolling update strategy (default, zero-downtime)

Add to the Deployment `spec`:

```yaml
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

`maxUnavailable: 0` guarantees no dip in capacity during a rollout - new pods must be ready before old ones terminate. This requires `readinessProbe` to be accurate, so don't add this without one.

## Ingress (only if user needs external routing beyond a LoadBalancer)

Ask which ingress controller they're running (nginx-ingress, Traefik, cloud-native ALB/GCLB) before generating - the annotations differ significantly and a wrong guess won't route traffic at all.
