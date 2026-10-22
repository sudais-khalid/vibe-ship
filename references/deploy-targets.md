# Deploy targets

Only generate a target's config when the user names that target, or when its config file already exists (`detect.py` → `existing.platform`). Every target below consumes the same Dockerfile, so the image is the contract. For each target, write its config file, add a deploy job to CI gated on `main` + tests, and list the secrets the user has to create in DEPLOYMENT.md.

## Choosing (if the user asks for advice)

| Situation | Recommend |
|---|---|
| Side project / MVP, one service + Postgres | Render, Railway, or Fly.io |
| Full control, cheapest, multiple services | Single VPS (Hetzner/DO/Linode) + Compose + Caddy, or Kamal |
| Scale-to-zero HTTP API | Google Cloud Run, Azure Container Apps |
| Already on AWS | App Runner (simple) or ECS Fargate (flexible) |
| Many services, platform team, autoscaling | Kubernetes (EKS/GKE/AKS/DOKS) |
| Pure static frontend | Cloudflare Pages / Netlify / Vercel / GitHub Pages (no container) |
| Next.js with ISR/edge features | Vercel (native), or the container on any target above |

## Single VPS: Compose + Caddy (zero vendor lock-in)

Files: `compose.yaml` (app + services + caddy from `servers.md`), `Caddyfile`, `.env` on the server, created by hand and never committed.

CI deploy job (after the image is pushed to GHCR):

```yaml
  deploy:
    needs: image
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: production
    concurrency: { group: deploy-production, cancel-in-progress: false }
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          fingerprint: ${{ secrets.DEPLOY_HOST_FINGERPRINT }}
          script: |
            cd /opt/app
            echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            IMAGE_TAG=sha-${{ github.sha }} docker compose pull app
            IMAGE_TAG=sha-${{ github.sha }} docker compose up -d --remove-orphans --wait
            docker image prune -f
```

The compose app service uses `image: ghcr.io/owner/repo:${IMAGE_TAG:-latest}` (keep `build: .` in an override for local dev). `--wait` blocks until healthchecks pass, so a broken release fails the job. Secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (a dedicated deploy key), `DEPLOY_HOST_FINGERPRINT`. The `GITHUB_TOKEN` pull only works while the workflow runs. For private images pulled later, use a read-only PAT on the server.

Server setup checklist for DEPLOYMENT.md: Docker Engine + compose plugin, firewall allowing 22/80/443 only, a non-root deploy user in the `docker` group, unattended security upgrades, and a backup cron for volumes.

## Kamal (Rails 8 default, works for any stack)

`config/deploy.yml`:

```yaml
service: app
image: owner/app
servers:
  web: [203.0.113.10]
  job:
    hosts: [203.0.113.10]
    cmd: bin/jobs                     # worker, stack-specific
proxy:
  ssl: true
  host: app.example.com
  app_port: 3000
  healthcheck: { path: /up }
registry:
  server: ghcr.io
  username: owner
  password: [KAMAL_REGISTRY_PASSWORD]
env:
  secret: [DATABASE_URL, SECRET_KEY_BASE]
accessories:
  db:
    image: postgres:18-alpine
    host: 203.0.113.10
    env: { secret: [POSTGRES_PASSWORD] }
    directories: ["data:/var/lib/postgresql"]
```

Secrets live in `.kamal/secrets`, which is gitignored and read from env/password managers. Deploy with `kamal setup` once, then `kamal deploy`. In CI, use `ruby/setup-ruby` + `gem install kamal` + an SSH key secret.

## Fly.io: `fly.toml`

```toml
app = "my-app"
primary_region = "iad"

[build]
  dockerfile = "Dockerfile"

[deploy]
  release_command = "python manage.py migrate --noinput"   # one-off migrations, stack-specific

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 1

  [[http_service.checks]]
    grace_period = "15s"
    interval = "30s"
    method = "GET"
    path = "/healthz"
    timeout = "3s"

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

CI: `superfly/flyctl-actions/setup-flyctl@v1` + `flyctl deploy --remote-only`, with secret `FLY_API_TOKEN` (`fly tokens create deploy`). App secrets: `fly secrets set KEY=...`. Postgres: Fly Managed Postgres or an external provider.

## Render: `render.yaml` (Blueprint)

```yaml
services:
  - type: web
    name: app
    runtime: docker
    dockerfilePath: ./Dockerfile
    plan: starter
    healthCheckPath: /healthz
    preDeployCommand: python manage.py migrate --noinput
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: app-db, property: connectionString }
      - key: SECRET_KEY
        generateValue: true
  - type: worker
    name: worker
    runtime: docker
    dockerCommand: celery -A app worker --loglevel=INFO
    envVars:
      - fromGroup: app-env
databases:
  - name: app-db
    plan: basic-256mb
```

Render auto-deploys on push. Turn that off and trigger it from CI after tests by calling the deploy hook (`curl -fsS -X POST "$RENDER_DEPLOY_HOOK_URL"`) with secret `RENDER_DEPLOY_HOOK_URL`. Render injects `PORT`, so the app must read it.

## Railway: `railway.json`

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "healthcheckPath": "/healthz",
    "healthcheckTimeout": 60,
    "preDeployCommand": ["npx prisma migrate deploy"],
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5
  }
}
```

CI: `npm i -g @railway/cli && railway up --service app --ci`, with secret `RAILWAY_TOKEN` (a project token). Railway injects `PORT`.

## Heroku: `heroku.yml`

```yaml
build:
  docker:
    web: Dockerfile
release:
  image: web
  command: ["./bin/migrate"]
run:
  web: node dist/server.js
```

Run `heroku stack:set container` once. Heroku sets `$PORT`, and the app **must** bind to it. There's no EXPOSE or HEALTHCHECK support: Heroku uses its own router checks.

## DigitalOcean App Platform: `.do/app.yaml`

```yaml
name: app
services:
  - name: web
    dockerfile_path: Dockerfile
    github: { repo: owner/repo, branch: main, deploy_on_push: true }
    http_port: 8080
    health_check: { http_path: /healthz }
    instance_size_slug: apps-s-1vcpu-0.5gb
    envs:
      - key: DATABASE_URL
        scope: RUN_TIME
        value: ${db.DATABASE_URL}
databases:
  - name: db
    engine: PG
    version: "17"
```

## Google Cloud Run

No config file is needed. CI uses Workload Identity Federation (OIDC, **no JSON keys**):

```yaml
    permissions: { contents: read, id-token: write }
    steps:
      - uses: actions/checkout@v7
      - uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOY_SA }}
      - uses: google-github-actions/deploy-cloudrun@v3
        with:
          service: app
          region: us-central1
          image: us-central1-docker.pkg.dev/${{ vars.GCP_PROJECT }}/app/app:${{ github.sha }}
          flags: --port=8080 --min-instances=0 --max-instances=10 --cpu=1 --memory=512Mi
          secrets: DATABASE_URL=database-url:latest
```

Cloud Run sets `PORT` (default 8080), uses its own startup/liveness probes, and ignores Dockerfile HEALTHCHECK, so distroless runtimes are ideal here. Push the image to Artifact Registry, not GHCR. Store secrets in Secret Manager.

## AWS

- **App Runner** (simplest): source = ECR image, port, health path `/healthz`, plus an instance role for Secrets Manager. CI: `aws-actions/configure-aws-credentials` with `role-to-assume` (OIDC), `aws-actions/amazon-ecr-login`, push, then `aws apprunner start-deployment` (or auto-deploy on ECR push).
- **ECS Fargate:** write `.aws/task-definition.json` with `containerDefinitions[].healthCheck`, `logConfiguration` (awslogs), and `secrets` from SSM/Secrets Manager ARNs, plus `readonlyRootFilesystem: true` when the app allows it. CI: `aws-actions/amazon-ecs-render-task-definition` → `aws-actions/amazon-ecs-deploy-task-definition` with `wait-for-service-stability: true`. The ALB target group health check path is `/healthz`.
- **OIDC everywhere:** `permissions: id-token: write` + an IAM role trust policy limited to `repo:owner/repo:ref:refs/heads/main`. Never use long-lived `AWS_ACCESS_KEY_ID` secrets.
- Infrastructure (VPC, ALB, ECS cluster, RDS) is IaC territory: offer Terraform/OpenTofu or AWS Copilot. Don't hand-write it uninvited.

## Azure Container Apps

CI: `azure/login@v3` with OIDC (`client-id`, `tenant-id`, `subscription-id` as vars) → `azure/container-apps-deploy-action@v2` with `imageToDeploy`, `targetPort: 8080`, `ingress: external`. Probes are configured on the container app, and secrets come from Key Vault references.

## Kubernetes

See `kubernetes.md`. Deploy from CI with `kubectl apply -k k8s/overlays/production` or `helm upgrade --install --atomic --wait`, or better, GitOps (Argo CD / Flux) watching the repo, where CI only bumps the image tag.

## Serverless / edge (not containers)

- **Vercel / Netlify / Cloudflare Pages:** for static and framework-native deploys, a `vercel.json` / `netlify.toml` / `wrangler.toml` is all that's needed. No Dockerfile. Generate one only if the user still wants a portable container.
- **AWS Lambda container images:** base `public.ecr.aws/lambda/<runtime>:<ver>`, `CMD ["app.handler"]`, no HTTP server, no EXPOSE. Use the AWS Lambda Web Adapter to run an existing HTTP app unchanged.
