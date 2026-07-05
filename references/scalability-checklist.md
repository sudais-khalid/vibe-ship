# Scalability Checklist

These are sane defaults to bake in automatically. Anything beyond this tier (autoscaling, IaC, multi-region) should only be added if the user explicitly asks or names a target cloud platform - don't over-engineer a side project into a Kubernetes cluster uninvited.

## Bake in by default
- [ ] `HEALTHCHECK` in every Dockerfile so orchestrators (Docker Swarm, Kubernetes, ECS, Railway, etc.) can detect unhealthy instances and restart/replace them.
- [ ] App reads its port from an env var (e.g. `PORT`) rather than hardcoding, so it works behind any load balancer or PaaS.
- [ ] Stateless app containers - session/state should live in the database or a cache (Redis), not in-memory or on local disk, so the app can run multiple replicas safely. If you spot in-memory session storage or local file writes for user data, flag it in `DEPLOYMENT.md` rather than silently rearchitecting.
- [ ] docker-compose.yml sets resource limits (`mem_limit`, `cpus`) on each service as a starting point, tuned conservatively - the user should adjust based on real usage.
- [ ] Database/cache services use named volumes so data survives container restarts.
- [ ] Logs go to stdout/stderr (12-factor style) rather than files, so they work with any log aggregator.

## Mention as follow-ups (don't implement uninvited)
- Horizontal autoscaling (Kubernetes HPA, ECS service autoscaling) - needs a real orchestrator target.
- CDN in front of static assets.
- Database read replicas / connection pooling (e.g. PgBouncer) once traffic actually warrants it.
- Queue-based async processing for slow endpoints (e.g. background jobs via BullMQ, Celery, Sidekiq) if the app has long-running requests.
- Infrastructure as Code (Terraform/Pulumi) if deploying to a specific cloud - ask before choosing a provider.

## Example docker-compose resource limits block

```yaml
services:
  app:
    build: .
    ports:
      - "3000:3000"
    env_file: .env
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/health"]
      interval: 30s
      timeout: 3s
      retries: 3
    depends_on:
      - db

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - db_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M

volumes:
  db_data:
```
