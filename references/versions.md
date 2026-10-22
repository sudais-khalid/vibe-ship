# Default versions

**Order of precedence:** the version the project declares (`detect.py` reports it with its source) beats this table. Use this table only when nothing is declared, and then write the chosen version into the project (`.nvmrc`, `.python-version`, `ARG` default) so the next build doesn't drift.

Last verified against Docker Hub / GHCR / GitHub tags: **2026-10-22**. Re-verify anything older than ~3 months with the commands at the bottom, because a 404 on a base image fails the whole build.

## Runtimes (build / runtime images)

| Stack | Default | Build image | Runtime image | Notes |
|---|---|---|---|---|
| Node.js | 24 (Active LTS) | `node:24-alpine` | `node:24-alpine` | 22 = maintenance LTS. 26 becomes LTS late Oct 2026. Node 25+ no longer bundles corepack (`npm i -g corepack`). |
| Bun | 1 | `oven/bun:1` | `oven/bun:1-slim` / `oven/bun:1-distroless` | |
| Deno | 2 | `denoland/deno:alpine` | `denoland/deno:alpine` | Pin `denoland/deno:alpine-2.x.y` in real projects. |
| Python | 3.14 | `python:3.14-slim` | `python:3.14-slim` | 3.15 is out (Oct 2026); wait for wheels before defaulting. |
| uv | 0.13 | `ghcr.io/astral-sh/uv:0.13` (copy `/uv` binary) | n/a | |
| Go | 1.27 | `golang:1.27-alpine` | `alpine:3.24` or `gcr.io/distroless/static-debian13:nonroot` | |
| Rust | 1.99 | `rust:1.99-slim` | `gcr.io/distroless/cc-debian13:nonroot` or `debian:trixie-slim` | |
| Java | 25 (LTS) | `eclipse-temurin:25-jdk` / `maven:3.9-eclipse-temurin-25` / `gradle:9-jdk25-alpine` | `eclipse-temurin:25-jre-alpine` | 21 = previous LTS. |
| .NET | 10.0 (LTS) | `mcr.microsoft.com/dotnet/sdk:10.0` | `mcr.microsoft.com/dotnet/aspnet:10.0-alpine` or `:10.0-noble-chiseled` | Chiseled = no shell, non-root by default. |
| Ruby | 3.4 | `ruby:3.4-slim` | `ruby:3.4-slim` | 4.0 is current; use it when the app's `.ruby-version` says so. |
| PHP | 8.4 | `composer:2` | `dunglas/frankenphp:1-php8.4` or `php:8.4-fpm-alpine` | 8.5 available. |
| Elixir | 1.19 / OTP 28 | `hexpm/elixir:1.19.0-erlang-28.1-debian-trixie-20250929-slim` | `debian:trixie-20250929-slim` | Keep build and runtime Debian snapshot identical. |
| Swift | 6.2 | `swift:6.2-noble` | `swift:6.2-noble-slim` | |
| Dart | 3.9 | `dart:3.9` | `scratch` + `/runtime/` | |
| Static serve | nginx 1.29 | n/a | `nginxinc/nginx-unprivileged:1.29-alpine` | Listens on 8080 as uid 101. |
| Base OS | | | `alpine:3.24`, `debian:trixie-slim` | |

## Backing services

| Service | Image | Data dir (mount a named volume here) |
|---|---|---|
| PostgreSQL | `postgres:18-alpine` | `/var/lib/postgresql` (**18+ changed this**; ≤17 used `/var/lib/postgresql/data`) |
| MySQL | `mysql:8.4` | `/var/lib/mysql` |
| MariaDB | `mariadb:11.8` | `/var/lib/mysql` |
| MongoDB | `mongo:8` | `/data/db` |
| Redis | `redis:8-alpine` | `/data` |
| Valkey (BSD Redis fork) | `valkey/valkey:8-alpine` | `/data` |
| RabbitMQ | `rabbitmq:4-management-alpine` | `/var/lib/rabbitmq` |
| Kafka (KRaft) | `apache/kafka:4.1.0` | `/var/lib/kafka/data` |
| Elasticsearch | `elasticsearch:9.1.0` | `/usr/share/elasticsearch/data` |
| OpenSearch | `opensearchproject/opensearch:3` | `/usr/share/opensearch/data` |
| ClickHouse | `clickhouse/clickhouse-server:25.8` | `/var/lib/clickhouse` |
| Meilisearch | `getmeili/meilisearch:v1.16` | `/meili_data` |
| Qdrant | `qdrant/qdrant:v1.15.0` | `/qdrant/storage` |
| Memcached | `memcached:1.6-alpine` | (none) |
| NATS | `nats:2-alpine` | `/data` with `-js` |
| Mail catcher (dev) | `axllent/mailpit:v1.27` | (none) |
| S3-compatible (dev) | `chrislusf/seaweedfs` or `localstack/localstack:4` | `/data` |
| Reverse proxy | `caddy:2`, `traefik:v3.6`, `nginx:1.29-alpine` | `/data` (Caddy certs) |

MinIO stopped publishing free community images in late 2025, so `minio/minio:latest` is gone. Use SeaweedFS or LocalStack locally, and real S3/R2/GCS in production.

## GitHub Actions (latest release tags, 2026-10)

| Action | Tag | Action | Tag |
|---|---|---|---|
| actions/checkout | v7 | docker/setup-buildx-action | v4 |
| actions/setup-node | v7 | docker/login-action | v4 |
| actions/setup-python | v7 | docker/metadata-action | v6 |
| actions/setup-go | v7 | docker/build-push-action | v7 |
| actions/setup-java | v6 | docker/setup-qemu-action | v4 |
| actions/setup-dotnet | v6 | aquasecurity/trivy-action | v0.36.0 |
| ruby/setup-ruby | v1 | github/codeql-action/upload-sarif | v4 |
| dtolnay/rust-toolchain | `stable` branch (pin SHA) | sigstore/cosign-installer | v4 |
| astral-sh/setup-uv | v10 | actions/upload-artifact | v7 |
| pnpm/action-setup | v6 | oven-sh/setup-bun | v2 |
| shivammathur/setup-php | 2.40.0 | erlef/setup-beam | v1 |
| denoland/setup-deno | v2 | | |

After writing any workflow, run `python3 scripts/pin_actions.py .github/workflows` to pin every tag to its commit SHA.

## Re-verify

```bash
# Docker Hub tag exists? (200 = yes)
curl -s -o /dev/null -w '%{http_code}\n' https://hub.docker.com/v2/repositories/library/node/tags/24-alpine
# Latest release tag of an action
git ls-remote --tags --refs https://github.com/actions/checkout | awk -F/ '{print $3}' | sort -V | tail -1
# Runtime support windows
curl -s https://endoflife.date/api/v1/products/nodejs/
```
