# Scalability and reliability checklist

Bake the first section in by default. Mention the second section as follow-ups, and implement it only when asked or when the chosen target needs it.

## Always
- [ ] **Healthchecks that work in the final image.** Use a tool that exists there: busybox `wget` on alpine, a language-native check on slim, exec form on distroless (VS-D020/D021). The audit verifies this.
- [ ] **Readiness-gated startup:** `depends_on: condition: service_healthy` for databases and caches, and `service_completed_successfully` for migrations (VS-C041).
- [ ] **Graceful shutdown:** exec-form CMD, tini/`init: true` for Node and Python, and no package-manager wrappers (VS-D080/D082). Use `stop_grace_period` when requests are long.
- [ ] **Resource limits** on every service: memory always. CPU limits in compose are fine, but in K8s prefer requests without CPU limits (VS-C051, K010).
- [ ] **Runtime memory tuning** matched to the limit: JVM `MaxRAMPercentage=75`, Go `GOMEMLIMIT≈90%`, Node `--max-old-space-size≈75%` for >1 GB limits.
- [ ] **Restart policy** `unless-stopped` (compose) or the K8s default (VS-C050).
- [ ] **Log rotation:** json-file `max-size`/`max-file` via a shared `x-logging` anchor (VS-C052).
- [ ] **Persistent named volumes** at the right data dir. Postgres 18+ uses `/var/lib/postgresql` (VS-C060/C061).
- [ ] **Stateless app containers.** Flag in-memory sessions, local uploads, and SQLite with >1 replica in DEPLOYMENT.md.
- [ ] **Migrations as a one-off step,** never in every replica's startup.
- [ ] **12-factor config:** everything via env, the same image for every environment, logs to stdout.
- [ ] **Immutable image tags in deploys** (`sha-<commit>`); `latest` is only a convenience pointer.

## Follow-ups (offer, don't impose)
- [ ] **Horizontal scaling:** K8s HPA / KEDA, ECS service autoscaling, Cloud Run max instances, Fly `min_machines_running`.
- [ ] **Connection pooling** once workers × replicas × pool approaches the DB limit (PgBouncer, RDS Proxy, Supabase pooler).
- [ ] **Background jobs** for slow requests: BullMQ, Celery, Sidekiq, Oban, Laravel queues, or Solid Queue.
- [ ] **CDN** for static assets and images: Cloudflare, CloudFront, Fastly.
- [ ] **Read replicas / caching layer** when read load dominates.
- [ ] **Multi-arch images** (amd64 + arm64) for Graviton/Ampere cost savings.
- [ ] **Observability:** OpenTelemetry SDK + collector, `/metrics` for Prometheus, structured JSON logs, error tracking (Sentry).
- [ ] **Backups and restore drills:** managed DB PITR, or scheduled `pg_dump` to object storage, plus a tested restore.
- [ ] **Infrastructure as code** (Terraform/OpenTofu, Pulumi) once a cloud is chosen. Ask the provider first.
- [ ] **Zero-downtime deploys:** K8s `maxUnavailable: 0` + readiness, compose `docker compose up -d --wait`, Kamal/Fly built-in.
