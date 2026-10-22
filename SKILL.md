---
name: vibe-ship
description: Generates and audits production-ready deployment setups for any app. Detects the stack from the repo (Node/Next.js/Bun/Deno, Python/Django/FastAPI, Go, Rust, Java/Spring, .NET, Ruby/Rails, PHP/Laravel, Elixir/Phoenix, static sites, monorepos) and writes hardened Dockerfiles, .dockerignore, compose.yaml with databases/caches/queues, .env.example, GitHub Actions or GitLab CI (test, build, scan, SBOM, signing, deploy), Kubernetes manifests, and configs for Fly.io, Render, Railway, Cloud Run, AWS, Azure, VPS+Caddy or Kamal. Verifies output with a 90+ rule static auditor (score, SARIF, CI exit codes) and real docker builds. Use when the user says "dockerize", "containerize", "deploy this", "production-ready", "vibe ship this", "add CI/CD", "write a Dockerfile/docker-compose", "deploy to Fly/Render/Cloud Run/ECS/Kubernetes/a VPS", or wants an existing Dockerfile, compose file, pipeline or K8s manifest reviewed, audited or fixed, even for one file.
---

# vibe-ship

Turns any codebase into something you can ship: hardened containers, a wired-up stack, a CI/CD pipeline, and the target platform's config. Every claim is backed by detection output, the audit score, and (when Docker is available) a real build and health check.

Scripts live in this skill's `scripts/` directory. They are standard-library Python 3.8+ with no installs. Invoke them as `python3 <skill-dir>/scripts/<name>.py`, or with `python` / `py -3` on Windows.

| Script | Purpose |
|---|---|
| `detect.py --path <repo> --json` | Evidence: services, stack, versions, ports, entrypoints, backing services, env vars, existing deploy files |
| `audit.py --path <repo>` | 90+ rules across Dockerfile, .dockerignore, Compose, GitHub Actions, GitLab CI, Kubernetes. Score, grade, `--format sarif\|json\|markdown`, `--fail-on high` |
| `pin_actions.py .github/workflows` | Pins every `uses:` to a commit SHA (`# vX.Y.Z` comment kept) via `git ls-remote` |

## Pick the mode

| The user wants... | Mode |
|---|---|
| deployment files that don't exist yet | **Generate** |
| an opinion on existing files ("review", "audit", "is this secure", "score my Dockerfile") | **Audit** |
| existing files improved ("fix", "harden", "make this production-ready" when files exist) | **Fix** (Audit, then apply) |
| to go live on a specific platform | **Target** (Generate or reuse the image, then add the platform layer) |

If `detect.py` reports existing Dockerfile/compose/CI and the user didn't explicitly ask to regenerate, use Audit or Fix. **Never overwrite an existing deployment file without saying so first.**

---

## Generate

### 1. Detect (evidence, not guesses)

```bash
python3 <skill-dir>/scripts/detect.py --path . --json
```

Read the result before anything else:
- `services[]` are what gets a Dockerfile. `packages[]` are libraries and workspace roots that don't.
- Per service: `language`, `framework`, `kind` (web/worker/static), `package_manager` + `lockfile`, `runtime_version` + `version_source`, `port`, `entrypoint`, `build_cmd`, `health_path`, `backing_services`, `workers`, `migrations`, `env_vars`, `notes`.
- `monorepo`: build with the repo root as context.
- `existing`: deployment files already present. `git.image_ref` is the GHCR image name.
- `warnings`: each one becomes either a question to the user or a DEPLOYMENT.md item.

If `services` is empty, or a framework/entrypoint is missing for a web service, **ask**. A wrong base image or start command is worse than none. If `detect.py` can't run, inspect the manifests by hand using the same checklist.

### 2. Decide the target (one short question at most)

The defaults need no question: **Docker Compose for local and single-server, GitHub Actions, and GHCR.** Ask only when the answer changes files and the user didn't say:
- **Where will it run?** VPS (Compose + Caddy, or Kamal), Fly.io, Render, Railway, Heroku, DigitalOcean, Cloud Run, AWS App Runner/ECS, Azure Container Apps, or Kubernetes.
- **Registry**, only if not GHCR, and **GitLab CI** if `.gitlab-ci.yml` exists or the remote is GitLab.

In a non-interactive context, use the defaults and state them in the summary.

### 3. Load only the references you need

| Need | Read |
|---|---|
| Dockerfile for each service | `references/stacks/<runtime>.md`: `node` (Node/Bun/Deno), `python`, `go`, `rust`, `jvm`, `dotnet`, `ruby`, `php`, `elixir` (BEAM/Gleam), `static` (SPA/SSG), `other` (Swift/Dart/Haskell/Crystal/Zig/C++/...) |
| Versions when the project declares none | `references/versions.md` |
| Postgres/MySQL/Mongo/Redis/RabbitMQ/Kafka/Elastic/... in compose | `references/services.md` |
| TLS, reverse proxy, app-server tuning, signals, trusted proxies | `references/servers.md` |
| Platform config + deploy job | `references/deploy-targets.md` |
| Pipelines (GitHub, GitLab, others) + Dependabot | `references/cicd-patterns.md` |
| Kubernetes / Helm / Kustomize | `references/kubernetes.md` |
| Final review | `references/security-checklist.md`, `references/scalability-checklist.md`, `references/anti-patterns.md` |

The templates are verified starting points. Adapt them to the detected entrypoint, port, package manager, and versions, and delete lines that don't apply (an asset stage with no frontend, a `public/` copy when there's no `public/`).

### 4. Write the files

| File | Contents |
|---|---|
| `Dockerfile` (one per service; monorepo: `apps/<svc>/Dockerfile`) | Multi-stage, pinned base, cache mounts, non-root, exec-form CMD, HEALTHCHECK with a tool that **exists in the final image** |
| `.dockerignore` | Stack baseline + `.git`, `.env*` (`!.env.example`), build output |
| `compose.yaml` | App + workers + detected backing services from `services.md`, healthchecks, `depends_on: condition: service_healthy`, one-off `migrate` service, named volumes, limits, restart, log rotation, no DB ports published |
| `compose.override.yaml` (optional) | Dev-only: source bind mounts, DB ports on `127.0.0.1`, dev command |
| `.env.example` | Every var in `env_vars` + compose interpolations, placeholders and a comment each |
| `.gitignore` | Add `.env`, `.env.*`, `!.env.example` if missing (append, never rewrite) |
| `.github/workflows/ci-cd.yml` | From `cicd-patterns.md`: test → image (scan, SBOM, provenance, sign) → deploy (if a target was named) |
| `.github/dependabot.yml` | actions + docker + docker-compose + the app's ecosystem, unless Renovate exists |
| Target config | `fly.toml`, `render.yaml`, `railway.json`, `heroku.yml`, `.do/app.yaml`, `config/deploy.yml`, `Caddyfile`, ECS task def, `k8s/`... as chosen |
| `DEPLOYMENT.md` | See "DEPLOYMENT.md contract" below |

Rules while writing:
- **Respect what the repo declares:** runtime version, package manager, lockfile, start script, port. Declared beats default.
- **Prefer the framework's own generator output** when present (Rails ≥ 7.1 Dockerfile, `mix phx.gen.release --docker`, Vapor's template). Audit and harden it rather than replacing it.
- **Don't change application code** (health routes, `0.0.0.0` binding, trusted proxies, `output: "standalone"`) without asking. Offer the exact change and put it in DEPLOYMENT.md.
- **No secrets anywhere** except `.env` (gitignored) and the platform/CI secret stores. Use `${VAR:?message}` for required compose secrets.

### 5. Harden the pipeline

```bash
python3 <skill-dir>/scripts/pin_actions.py .github/workflows
```

If there's no network, leave the tags in place and add "run pin_actions.py" to the DEPLOYMENT.md checklist.

### 6. Verify (required)

```bash
python3 <skill-dir>/scripts/audit.py --path . --fail-on medium
```

Fix every CRITICAL/HIGH/MEDIUM in your own output and re-run until it exits 0. Remaining LOW/INFO must be fixed or justified with an inline `# vibe-ship: ignore VS-XXXX <reason>`.

Then climb `references/verification.md` as far as the environment allows: `docker compose config`, `docker build`, `docker compose up --wait`, curl the health path, check the UID, and clean up. In an interactive session, ask once before pulling images or starting containers, unless the user already asked you to test it. Report the highest rung reached, honestly.

### 7. Summarize, don't dump

Reply in under 15 lines:
- the stack detected per service
- the files created or changed
- the services wired up
- the audit score and grade
- the verification rung reached (for example "built and healthy in 41s, image 128 MB, UID 10001")
- the app changes you recommend but didn't make
- a pointer to DEPLOYMENT.md

Don't paste the file contents. Offer to walk through any file.

---

## Audit

```bash
python3 <skill-dir>/scripts/audit.py --path <repo-or-file>                 # human report
python3 <skill-dir>/scripts/audit.py --path . --format markdown            # for PR comments / docs
python3 <skill-dir>/scripts/audit.py --path . --format sarif > vibe-ship.sarif   # GitHub code scanning
python3 <skill-dir>/scripts/audit.py --path . --fail-on high               # CI gate (exit 1)
python3 <skill-dir>/scripts/audit.py --list-rules                          # full rule catalog
```

It's a static scan: no Docker daemon or network, nothing executed. It parses Dockerfiles properly (continuations, heredocs, multi-stage inheritance), reads YAML by key path, and checks things generic linters miss:
- a HEALTHCHECK calling `curl` on a slim image that has no curl
- Postgres 18 volumes mounted at the old data dir
- `pull_request_target` "pwn requests" and script injection in workflows
- secrets in env or layers
- dev servers used as production commands
- K8s pod security

Present results:
1. Score and grade, plus a one-line verdict.
2. CRITICAL and HIGH findings first, each with `file:line`, why it matters in one sentence, and the concrete fix.
3. Group the MEDIUM/LOW findings and don't list INFO unless asked.
4. Offer to apply the fixes (that's Fix mode). Don't just list them.

For Helm charts, render first (`helm template`). Templated files are skipped and reported as such.

## Fix

Run Audit and apply fixes highest severity first, editing the existing files in place and keeping the user's structure and comments. Re-run the audit after each batch. When a fix changes behaviour (non-root user needing writable dirs, removing a published DB port, pinning a version), say what changed and why. Finish with before/after score and grade.

## Target

When an image setup already exists and the user wants it live somewhere, read `references/deploy-targets.md` for that platform. Write its config, add the deploy job (gated on main + tests, with `environment:` and `concurrency:`), and list the exact secrets and one-time setup commands in DEPLOYMENT.md.

---

## DEPLOYMENT.md contract

Short, scannable, specific to this repo:
1. **What was generated:** a table of each file and its purpose.
2. **Run locally:** `cp .env.example .env`, then `docker compose up --build`, then the URL and health URL.
3. **How CI/CD works:** triggers, what's gated, image tags (`sha-<commit>`, semver, `latest`), where findings show up.
4. **Deploy:** target-specific steps, the secrets to create (exact names), one-time setup.
5. **You still need to:** every warning, skipped step, and recommended app change, as checkboxes.
6. **Audit:** score and grade at handover, plus how to re-run it.

## Principles

- **Evidence over assumptions.** Detection output and audit results drive the files, and you ask when evidence is missing.
- **Verified, not plausible.** Nothing is "production-ready" until it passes the audit, and ideally a real build and health check.
- **Secure by default, practical by design.** Non-root, pinned, scanned, least privilege, plus the inline suppression with a reason when a rule genuinely doesn't apply.
- **Right-sized.** Compose and a PaaS by default. Kubernetes, IaC, autoscaling, and multi-region only on request or clear need.
- **Never clobber.** Existing files get audited and fixed in place. App code changes are offered, not imposed.
