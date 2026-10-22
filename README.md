# vibe-ship: production-ready Docker, CI/CD and deployment for any app (Claude Code skill)

[![Listed on ClaudePluginHub](https://www.claudepluginhub.com/badge/sudais-khalid-vibe-ship)](https://www.claudepluginhub.com/plugins/sudais-khalid-vibe-ship?ref=badge)
[![Available on CodeGuilds](https://img.shields.io/badge/Available_on-CodeGuilds-6366f1?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0id2hpdGUiIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6TTIgMTdsMTAgNSAxMC01TTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+)](https://codeguilds.dev/packages/vibe-ship)
[![Tests](https://github.com/sudais-khalid/vibe-ship/actions/workflows/test.yml/badge.svg)](https://github.com/sudais-khalid/vibe-ship/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

**vibe-ship is an open-source Claude Code skill that turns any codebase into a deployable, hardened application.** It detects your stack from the repo, writes the Dockerfile, `.dockerignore`, `compose.yaml`, `.env.example`, GitHub Actions or GitLab CI pipeline, and platform config (Kubernetes, Fly.io, Render, Railway, Cloud Run, AWS, Azure, VPS). It then checks everything with a 93-rule static auditor and real `docker build` runs before handing over.

> You bring the vibe. vibe-ship brings the infrastructure, and proves it works.

Built and maintained by [Sudais Khalid](https://sudaiskhalid.com).

---

## Quick start

```text
/plugin marketplace add sudais-khalid/vibe-ship
/plugin install vibe-ship@vibe-ship
```

Then, in any project, just say:

> **"vibe ship this"** · "dockerize this app" · "make this production-ready" · "deploy this to Fly.io" · "audit my Dockerfile"

Other install options are under [Install](#install).

## What you get

| File | What's in it |
|---|---|
| `Dockerfile` (per service) | Multi-stage, pinned base image, BuildKit cache mounts, non-root user, exec-form CMD with an init where needed, and a `HEALTHCHECK` whose tool **actually exists** in the final image |
| `.dockerignore` | Stack-specific, always excludes `.git` and `.env*` |
| `compose.yaml` | App + workers + detected Postgres/MySQL/Mongo/Redis/RabbitMQ/Kafka/Elasticsearch, readiness-gated `depends_on`, one-off migration job, named volumes at the correct data dirs, resource limits, log rotation, no database ports exposed |
| `.env.example` | Every environment variable your code actually reads, found by scanning the source |
| `.github/workflows/ci-cd.yml` | test → build → **Trivy scan (SARIF)** → push on main/tags only → **SBOM + provenance** → **keyless cosign signing** → optional deploy. Every action pinned to a commit SHA, least-privilege permissions |
| `.github/dependabot.yml` | Keeps actions, base images, compose images and dependencies current |
| Platform config | `fly.toml`, `render.yaml`, `railway.json`, `heroku.yml`, `.do/app.yaml`, Kamal `config/deploy.yml`, `Caddyfile`, ECS task definition, Kubernetes manifests, generated only for the target you choose |
| `DEPLOYMENT.md` | How to run it, how CI/CD works, exact secrets to create, and a checklist of what's left |

## Supported stacks

Detection is deterministic (`scripts/detect.py` reads manifests and lockfiles, it doesn't guess), and every stack has a tested template.

| Language / runtime | Frameworks detected | Package managers |
|---|---|---|
| **JavaScript / TypeScript** | Next.js (standalone), Nuxt, SvelteKit, Remix / React Router 7, Astro, SolidStart, TanStack Start, NestJS, Express, Fastify, Hono, Koa, Hapi, AdonisJS, Strapi, Medusa, Redwood | npm, pnpm, Yarn classic & Berry, Bun, Deno |
| **Python** | FastAPI, Django, Flask, Starlette, Litestar, Quart, Sanic, aiohttp, Falcon, Streamlit, Gradio, Dash, Chainlit + Celery / RQ / Dramatiq workers | uv, Poetry, PDM, Pipenv, pip |
| **Go** | net/http, Gin, Echo, Fiber, Chi, Gorilla, Huma, Connect, gRPC | Go modules |
| **Rust** | Axum, Actix Web, Rocket, Warp, Poem, Salvo, Loco, Tonic | Cargo |
| **Java / Kotlin / Scala / Clojure** | Spring Boot (layered jars), Quarkus, Micronaut, Ktor, Javalin, Vert.x, Dropwizard, Helidon, Play | Maven, Gradle, sbt, Leiningen |
| **.NET** | ASP.NET Core, Worker Service, F# | NuGet |
| **Ruby** | Rails 7/8 (Thruster, Solid Queue), Sinatra, Hanami, Roda + Sidekiq / GoodJob | Bundler |
| **PHP** | Laravel (+ Octane, Horizon), Symfony, Slim, CakePHP, CodeIgniter, Laminas, Drupal, WordPress, served by FrankenPHP or nginx + php-fpm | Composer |
| **Elixir / Erlang / Gleam** | Phoenix (mix releases), Plug, rebar3 | Mix, rebar3 |
| **Static / SPA** | Vite, React, Vue, Angular, Astro, Docusaurus, VitePress, Gatsby, Eleventy, Hugo, Jekyll, MkDocs, Flutter web | any |
| **Others** | Swift (Vapor, Hummingbird), Dart (Shelf, Dart Frog), Haskell, Crystal, Zig, C/C++ (CMake, Meson) | native |

**Monorepos:** Turborepo, Nx, pnpm/Yarn/npm workspaces, plus any `frontend/` + `backend/` layout, with one image per deployable service.

## Deploy targets

| Target | What vibe-ship writes |
|---|---|
| **Any VPS** (Hetzner, DigitalOcean, Linode, EC2...) | Compose + **Caddy automatic HTTPS** (or Traefik), SSH deploy job with `docker compose up --wait` |
| **Kamal** | `config/deploy.yml` with accessories |
| **Fly.io / Render / Railway / Heroku / DigitalOcean App Platform** | Native config with health checks and release/migration commands |
| **Google Cloud Run** | OIDC (Workload Identity) deploy job, no JSON keys |
| **AWS App Runner / ECS Fargate** | Task definition + OIDC role deploy |
| **Azure Container Apps** | OIDC deploy job |
| **Kubernetes** (EKS, GKE, AKS, DOKS, k3s) | Hardened Deployment, Service, PDB, NetworkPolicy, HPA, Ingress/Gateway, Kustomize overlays, migration Job |

## The auditor: 93 rules, no dependencies

`scripts/audit.py` is a standalone static analyzer for deployment configuration. It runs anywhere Python 3.8+ runs, needs no Docker daemon or network, and is useful on its own, with or without Claude.

```bash
python3 scripts/audit.py --path .                       # scored report
python3 scripts/audit.py --path . --fail-on high        # CI gate (exit 1)
python3 scripts/audit.py --path . --format sarif        # GitHub code scanning
python3 scripts/audit.py --path . --format markdown     # PR comment / docs
python3 scripts/audit.py --list-rules                   # full catalog
```

```text
====================================================================
VIBE-SHIP AUDIT  v2.0.0   score 0/100  grade F
====================================================================
[CRITICAL] VS-G010  .github/workflows/ci.yml:12
  Issue: pull_request_target checks out untrusted PR code
[CRITICAL] VS-D011  Dockerfile:1
  Issue: No USER in final stage - container runs as root
[HIGH] VS-D020  Dockerfile:9
  Issue: HEALTHCHECK uses a tool that does not exist in the final image: 'curl' is not installed in python:3.14-slim
[HIGH] VS-C061  compose.yaml:14
  Issue: Postgres 18+ volume mounted at the old /var/lib/postgresql/data path
```

**What it checks:**

- **Dockerfile:** base image pinning, root user (following `USER` through multi-stage chains), secrets in `ENV`/`ARG`/`COPY`, `curl | sh`, a `HEALTHCHECK` that can't run in the final image, shell-form CMD (no SIGTERM), `npm start` as PID 1, dev servers in production, lockfile-ignoring installs, layer-cache busting, apt/apk/pip hygiene.
- **.dockerignore:** missing file, `.env` / `.git` / `node_modules` leaks.
- **Compose:** privileged mode, Docker socket mounts, host namespaces, dangerous capabilities, hardcoded credentials, database ports exposed on all interfaces, stateful services without volumes, the Postgres 18 data-dir change, missing healthchecks / readiness conditions / restart policies / limits / log rotation.
- **GitHub Actions:** unpinned or branch-pinned actions, deprecated actions (`actions-rs`, `set-output`), `pull_request_target` pwn requests, script injection via `${{ github.event.* }}`, missing or excessive `permissions`, literal secrets, pushes from PRs, missing tests before deploy, no scanning, no SBOM/provenance.
- **GitLab CI:** `:latest` job images, literal credentials.
- **Kubernetes:** `:latest` images, missing probes and resources, privileged pods, root users, privilege escalation, writable root FS, capabilities, host namespaces, hostPath, literal secrets, committed `Secret` data.
- **Repo:** `.env` not gitignored or already tracked by git, plus credential patterns (AWS, GitHub, Stripe, Slack, OpenAI/Anthropic keys, private keys, database URLs with passwords).

Suppress a rule where it genuinely doesn't apply, with a reason:

```dockerfile
# vibe-ship: ignore VS-D022 Kubernetes probes cover health for this distroless image
```

### Use it as a GitHub Action

```yaml
jobs:
  deploy-config-audit:
    runs-on: ubuntu-latest
    permissions: { contents: read, security-events: write }
    steps:
      - uses: actions/checkout@v7
      - uses: sudais-khalid/vibe-ship@v2
        with:
          fail-on: high          # critical | high | medium | low | none
```

Findings appear in the repository's **Security → Code scanning** tab and in the job summary. Outputs: `score`, `grade`, `sarif-file`.

## How it works

```text
detect.py ──> stack templates ──> files ──> pin_actions.py ──> audit.py ──> docker build + run + health check
(evidence)    (references/)                 (SHA-pin CI)       (93 rules)    (when Docker is available)
```

1. **Detect.** It reads `package.json`, lockfiles, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`, `*.csproj`, `Gemfile`, `composer.json`, `mix.exs` and more. That gives it the framework, the runtime version you declared, port, entrypoint, health route, backing services from real dependency names, and the env vars your code reads.
2. **Generate.** It adapts verified per-stack templates from `references/` to what was detected. It never overwrites existing deployment files without asking, and it never changes your app code without asking.
3. **Harden.** It pins every GitHub Action to a commit SHA and runs the auditor on its own output until there are no MEDIUM-or-worse findings.
4. **Prove it.** When Docker is available, it builds the images, starts the stack, waits for healthchecks, curls the health endpoint, checks the container isn't root, and cleans up.

### Template verification results

The templates are extracted verbatim from `references/`, built, run, health-checked, and stopped. Docker 29.9, 2026-10-22:

| Template | Build (cold) | Image | Responds | Non-root UID | HEALTHCHECK | `docker stop` |
|---|---|---|---|---|---|---|
| Node 24 (alpine + tini) | 70 s | 243 MB | ✅ | 1000 | healthy | 0.4 s, exit 0 |
| Python 3.14 (slim + venv) | 164 s | 219 MB | ✅ | 10001 | healthy | 0.7 s, exit 0 |
| Go 1.27 (alpine runtime) | 45 s | 24 MB | ✅ | 10001 | healthy | 0.5 s |
| Rust 1.99 (distroless + tini) | 12 s | 44 MB | ✅ | 65532 | orchestrator probes | 0.7 s |
| Static (nginx-unprivileged) | 9 s | 81 MB | ✅ | 101 | healthy | 0.5 s, exit 0 |
| Compose: app + Postgres 18 + Redis 8 | n/a | n/a | ✅ | n/a | all healthy in 56 s | clean teardown |

Verification also caught three bugs a static review would miss, and all three are fixed:

- an empty `node_modules` COPY failing on zero-dependency apps
- Rust binaries ignoring SIGTERM as PID 1 (10 s stall, then SIGKILL)
- Postgres 18's moved `PGDATA`, now audit rule VS-C061

## How vibe-ship compares

| | Typical deployment skill / prompt | Dockerfile linter (e.g. hadolint) | **vibe-ship** |
|---|---|---|---|
| Writes working files | Sometimes | No | **Yes, the full set** |
| Stack detection | The model guesses | n/a | **Deterministic script** |
| Stacks with tested templates | A few | n/a | **30+ frameworks, 12 language families** |
| Compose, CI, K8s, platform configs | Partial | Dockerfile only | **All, wired together** |
| Static analysis | No | Dockerfile rules | **93 rules: Dockerfile, Compose, Actions, GitLab, K8s, secrets** |
| Knows whether `curl` exists in your final image | No | No | **Yes** |
| SARIF / CI gate / GitHub Action | No | Yes | **Yes** |
| Actions pinned to SHAs automatically | No | No | **Yes** |
| Verified by real builds | No | No | **Yes, when Docker is available** |
| Dependencies | n/a | Binary | **None (Python stdlib)** |

## Install

### Claude Code: plugin marketplace (recommended)

```text
/plugin marketplace add sudais-khalid/vibe-ship
/plugin install vibe-ship@vibe-ship
```

### Claude Code: manual

```bash
git clone https://github.com/sudais-khalid/vibe-ship.git ~/.claude/skills/vibe-ship
```

Or click **Code → Download ZIP**, extract it, and rename the folder to `vibe-ship` inside `~/.claude/skills/` (personal) or `.claude/skills/` (one project). Restart Claude Code and check `/skills`.

### Claude.ai, Claude Desktop, or the Claude API

Download **[vibe-ship.skill](./vibe-ship.skill)** and upload it under **Settings → Capabilities → Skills**, or through the Skills API.

## FAQ

### What is a Claude Code skill?

A skill is a folder with a `SKILL.md` file of instructions, plus optional scripts and reference files, that Claude loads automatically when a request matches. vibe-ship is a skill. It's packaged as a plugin for one-command install in Claude Code and as a `.skill` file for Claude.ai.

### Does vibe-ship overwrite my existing Dockerfile or pipeline?

No. If deployment files already exist, it switches to **Audit** or **Fix** mode, scores what you have, and edits in place with your approval. Application code (health routes, port binding, proxy settings) is never changed without asking.

### Do I need Docker installed?

Not to generate files or run the auditor, which is pure static analysis. With Docker installed, vibe-ship also builds and health-checks the result before handing it over.

### Can I use the auditor without Claude?

Yes. `python3 scripts/audit.py --path .` works on any machine with Python 3.8+, and the GitHub Action (`uses: sudais-khalid/vibe-ship@v2`) runs it in CI and uploads SARIF to code scanning.

### How is the auditor different from hadolint, Trivy or Checkov?

hadolint lints Dockerfile syntax and shell. Trivy and Checkov scan for vulnerabilities and broad IaC policy. vibe-ship focuses on **deployment correctness across files**: a `HEALTHCHECK` calling a binary the final image doesn't have, `USER` inherited through stages, Postgres 18's moved data directory, readiness-gated `depends_on`, `pull_request_target` checkouts, and secrets in layers. It complements those tools, and the generated pipeline runs Trivy too.

### Which Kubernetes and cloud platforms are supported?

Generic Kubernetes (EKS, GKE, AKS, DOKS, k3s, OpenShift) via Kustomize, with Helm on request. Platforms: Fly.io, Render, Railway, Heroku, DigitalOcean App Platform, Google Cloud Run, AWS App Runner and ECS Fargate, Azure Container Apps, Kamal, and any VPS with Docker Compose + Caddy.

### Is it free?

Yes. It's MIT-licensed open source. Your normal Claude usage applies.

## Repository layout

```text
SKILL.md                 workflow Claude follows (generate / audit / fix / target)
scripts/
  detect.py              deterministic stack detection (JSON or human output)
  audit.py               93-rule static auditor (text / json / sarif / markdown)
  pin_actions.py         pins GitHub Actions to commit SHAs
references/
  stacks/                node, python, go, rust, jvm, dotnet, ruby, php, elixir, static, other
  services.md            Postgres, MySQL, MongoDB, Redis, RabbitMQ, Kafka, Elasticsearch, ...
  servers.md             Caddy, Traefik, nginx, app servers, signals, trusted proxies
  deploy-targets.md      VPS, Kamal, Fly, Render, Railway, Heroku, DO, Cloud Run, AWS, Azure
  cicd-patterns.md       GitHub Actions, GitLab CI, Dependabot, other CI systems
  kubernetes.md          hardened manifests, Kustomize, Helm notes
  verification.md        build/run/health verification ladder
  versions.md            current default versions (verified 2026-10)
  audit-rules.md         generated rule catalog
  security-checklist.md, scalability-checklist.md, anti-patterns.md
tests/                   fixtures + unit tests (run on Linux, macOS, Windows)
action.yml               GitHub Action wrapper around audit.py
vibe-ship.skill          packaged skill for Claude.ai upload
```

## Contributing

Issues and pull requests are welcome, especially new stack templates, audit rules (each with a fixture test), and deploy targets. Run the tests with:

```bash
python -m unittest discover -s tests -v
```

Rebuild the uploadable package with `python tools/package_skill.py`.

## License

MIT, see [LICENSE](./LICENSE). © Sudais Khalid.
