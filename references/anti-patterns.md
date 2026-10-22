# Gotchas a static scan can't see

`scripts/audit.py` catches 90+ file-level mistakes (see `audit-rules.md`). These are the runtime and app-level traps that still break "it works on my machine" deployments. Check them during generation and list any hit in DEPLOYMENT.md.

| Gotcha | Symptom | Fix |
|---|---|---|
| App binds `127.0.0.1` / `localhost` | Container is healthy inside, but connections are refused from outside | Bind `0.0.0.0` (Rocket `ROCKET_ADDRESS`, Next `HOSTNAME=0.0.0.0`, Vite preview `--host`, Rails `-b 0.0.0.0`) |
| Hardcoded port, platform injects `PORT` | Works locally, 502 on Render/Heroku/Cloud Run | Read `PORT` with a default |
| `localhost` DB host in config | App can't reach the DB in compose | Use the service name (`db`, `redis`) via `DATABASE_URL` |
| Build-time env treated as runtime (`NEXT_PUBLIC_*`, `VITE_*`, `REACT_APP_*`) | Changing env on the server does nothing | Pass as build args, or switch to runtime `/config.json` |
| Secrets in public build-time env | API keys shipped to every browser | Server-side env only. Public prefixes are public. |
| Migrations in the startup of every replica | Lock timeouts, double-applied migrations | One-off job (compose `migrate`, K8s Job, `release_command`) |
| SQLite with >1 replica or no volume | Data loss, `database is locked` | Single replica + volume, or move to Postgres |
| In-memory sessions / local file uploads | Users logged out randomly, missing files behind a load balancer | Redis/DB sessions, object storage |
| Native modules built for glibc on alpine (musl) | `Error loading shared library` / segfaults | Use `-slim` (Debian) images for both stages |
| Healthcheck hits an auth-protected or DB-heavy route | Restart loops during DB blips | Dedicated cheap `/healthz` (liveness), with dependency checks only in readiness |
| Liveness probe without a startupProbe on slow boots (JVM, Rails) | Pod killed before it ever starts | `startupProbe` with a generous `failureThreshold` |
| Container clock/timezone assumptions | Off-by-hours cron jobs | Run in UTC and convert in the app. Set `TZ` only deliberately. |
| `docker compose down -v` in scripts | Production data deleted | Never `-v` outside dev, and say so in DEPLOYMENT.md |
| Postgres major bump on an existing volume | Container exits: "database files are incompatible" | `pg_upgrade` / dump-restore. A tag bump alone isn't a migration. |
| Reverse proxy without trusted-proxy config | Redirect loops, wrong client IPs, insecure cookies | See the trust-proxy table in `servers.md` |
| Large build context (no `.dockerignore`) | Slow builds, secrets in image layers | `.dockerignore` from the stack file |
| CPU limits on latency-sensitive services (K8s) | p99 spikes from CFS throttling | CPU requests only, memory limits always |
| `latest` tag in deploy manifests | Rollbacks impossible, nodes run different versions | Deploy `sha-<commit>` tags |
| Cron/scheduler running in every replica | Jobs execute N times | One dedicated scheduler instance, or a distributed lock |
| Graceful shutdown shorter than request time | Dropped requests during deploys | Raise `stop_grace_period` / `terminationGracePeriodSeconds` and add a preStop drain |
