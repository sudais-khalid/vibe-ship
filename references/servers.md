# Servers: reverse proxies, TLS, app servers, process behaviour

## Do you need a reverse proxy?

| Target | Proxy needed? |
|---|---|
| PaaS (Fly, Render, Railway, Heroku, Cloud Run, App Runner, Azure Container Apps, DO App Platform) | **No.** The platform terminates TLS and routes. Listen on `$PORT`. |
| Kubernetes | Use an Ingress / Gateway (see `kubernetes.md`), not a proxy container per app. |
| Single VPS / bare metal with Compose | **Yes.** Caddy (default) or Traefik in front, for TLS + HTTP/2 + one entrypoint for multiple services. |
| Kamal | Built in (kamal-proxy). Don't add another. |

## Caddy (default for VPS): automatic HTTPS, zero config

```yaml
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data          # certificates - MUST persist or Let's Encrypt rate-limits you
      - caddy_config:/config
    depends_on:
      app: { condition: service_healthy }
    logging: *logging
```

```caddyfile
{$DOMAIN} {
  encode zstd gzip
  reverse_proxy app:3000 {
    health_uri /healthz
  }
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    X-Content-Type-Options nosniff
    Referrer-Policy strict-origin-when-cross-origin
    -Server
  }
}
api.{$DOMAIN} {
  reverse_proxy api:8000
}
```

- `DOMAIN` comes from `.env`, and the DNS A/AAAA record must point at the server before first start.
- Remove the app service's `ports:` mapping. Only Caddy publishes ports.

## Traefik (when there are many services or labels-based routing is preferred)

```yaml
  traefik:
    image: traefik:v3.6
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
      - --certificatesresolvers.le.acme.email=${ACME_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
    ports: ["80:80", "443:443"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro   # vibe-ship: ignore VS-C002 read-only, required for docker provider
      - traefik_certs:/letsencrypt
  app:
    labels:
      - traefik.enable=true
      - traefik.http.routers.app.rule=Host(`${DOMAIN}`)
      - traefik.http.routers.app.entrypoints=websecure
      - traefik.http.routers.app.tls.certresolver=le
      - traefik.http.services.app.loadbalancer.server.port=3000
```

The Docker socket mount is a real risk. Harden it with `tecnativa/docker-socket-proxy` (allow only `CONTAINERS=1`) and point Traefik at `tcp://socket-proxy:2375`. Mention this in DEPLOYMENT.md.

## nginx as reverse proxy (when the team already uses nginx)

```nginx
upstream app { server app:3000; keepalive 32; }
server {
  listen 8080;
  client_max_body_size 20m;
  location / {
    proxy_pass http://app;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;        # websockets
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 60s;
  }
}
```

TLS with nginx means certbot plus renewal cron, which is more moving parts. Prefer Caddy unless nginx is already in use.

## Behind any proxy, the app must trust forwarded headers

Otherwise redirects go to `http://`, rate limits see the proxy IP, and secure cookies break.

| Stack | Setting |
|---|---|
| Express | `app.set('trust proxy', 1)` |
| Fastify | `fastify({ trustProxy: true })` |
| Next.js / Nuxt | handled; set `HOSTNAME=0.0.0.0` |
| Django | `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `CSRF_TRUSTED_ORIGINS` |
| FastAPI / uvicorn | `--proxy-headers --forwarded-allow-ips "*"` |
| Flask | `ProxyFix(app.wsgi_app, x_for=1, x_proto=1)` |
| Rails | `config.assume_ssl = true` (7.1+), `config.force_ssl = true` |
| Laravel | `TrustProxies` middleware → `$proxies = '*'` |
| Spring Boot | `server.forward-headers-strategy=framework` |
| ASP.NET Core | `ForwardedHeadersOptions` + `app.UseForwardedHeaders()` |
| Phoenix | `Plug.RewriteOn` / `force_ssl: [rewrite_on: [:x_forwarded_proto]]` |
| Go | read `X-Forwarded-*` explicitly, or use chi `middleware.RealIP` |

These are app changes. List the exact line in DEPLOYMENT.md and offer to apply it. Don't silently edit app code.

## App servers and concurrency

| Runtime | Server | Concurrency knob | Default for 1 vCPU / 512 MB |
|---|---|---|---|
| Node | built-in | 1 process per container, scale replicas (cluster mode only on big single VMs) | 1 |
| Python WSGI | gunicorn | `WEB_CONCURRENCY` (workers), `--threads` | 2 workers |
| Python ASGI | uvicorn / gunicorn+uvicorn_worker / granian | `--workers` | 1–2 |
| Ruby | puma | `WEB_CONCURRENCY` (workers), `RAILS_MAX_THREADS` | 1 worker × 3 threads |
| PHP | FrankenPHP / php-fpm | `pm.max_children` ≈ (memory − 100 MB) / 40 MB | 8–10 |
| JVM | embedded Tomcat / Netty | thread pool; heap via `MaxRAMPercentage` | defaults |
| Go / Rust / .NET / Elixir | built-in | runtime auto-scales to cgroup CPU | defaults |

DB connections are workers × pool size × replicas, and that must stay below Postgres `max_connections` (100 by default). Above about 50 total, recommend PgBouncer, or a managed pooler such as Supabase/Neon/RDS Proxy.

## Process behaviour (all stacks)

- **Exec-form CMD** so the app is PID 1 and receives SIGTERM (`VS-D080`).
- **An init for runtimes that ignore signals as PID 1** (Node, Python without handlers, shell wrappers): `tini` in the image, or `init: true` in compose.
- **Graceful shutdown.** Orchestrators send SIGTERM, wait for the grace period (Docker 10s, K8s 30s), then SIGKILL. Apps should stop accepting new requests, finish in-flight ones, and close DB pools. Compose `stop_grace_period: 30s` for slow drains.
- **Logs to stdout/stderr** only, in JSON in production if the stack supports it easily.
- **Bind `0.0.0.0`, read `PORT`.** `127.0.0.1` inside a container is unreachable from outside it.
- **Stateless.** No sessions in memory and no uploads on local disk with more than 1 replica. Use Redis or the DB for sessions and object storage for uploads. Flag violations in DEPLOYMENT.md.
