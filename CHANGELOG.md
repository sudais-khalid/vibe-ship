# Changelog

## 2.0.0 - 2026-10-22

A rewrite focused on coverage, correctness and proof.

### Added

- `scripts/detect.py`: deterministic stack detection for 12 language families and 30+ frameworks, monorepos (Turborepo, Nx, pnpm/Yarn/npm workspaces), declared runtime versions, ports, entrypoints, health routes, backing services from real dependency names, and env vars read by the code.
- `scripts/pin_actions.py`: pins GitHub Actions to commit SHAs via `git ls-remote` (no token needed).
- `action.yml`: run the auditor as a GitHub Action with SARIF upload, job summary, and `score`/`grade` outputs.
- Per-stack references (`references/stacks/`): Node/Bun/Deno, Python, Go, Rust, JVM, .NET, Ruby, PHP, Elixir/BEAM, static/SSG, and others (Swift, Dart, Haskell, Crystal, Zig, C/C++).
- `services.md` (Postgres, MySQL, MariaDB, MongoDB, Redis/Valkey, RabbitMQ, Kafka KRaft, Elasticsearch/OpenSearch, ClickHouse, NATS, ...), `servers.md` (Caddy, Traefik, nginx, trusted proxies, app-server tuning, signals), `deploy-targets.md` (VPS, Kamal, Fly.io, Render, Railway, Heroku, DigitalOcean, Cloud Run, AWS, Azure), `verification.md`, `versions.md`.
- CI template with Trivy image scan + SARIF, SBOM + provenance, keyless cosign signing, Dependabot, plus a GitLab CI template.
- Test suite with fixtures for every stack, running on Linux/macOS/Windows with Python 3.9 and 3.14. Templates verified with real `docker build` + run + health checks.

### Changed

- `scripts/audit.py` rewritten: real Dockerfile parsing (continuations, heredocs, ARG-resolved base images, multi-stage `USER`/`HEALTHCHECK` inheritance), key-path YAML reading, **93 rules** with stable IDs across Dockerfile, .dockerignore, Compose, GitHub Actions, GitLab CI, Kubernetes and repo secrets; score + grade; `--format text|json|sarif|markdown`; `--fail-on`; inline and stage-scoped suppressions.
- Kubernetes manifests hardened by default (securityContext, startupProbe, PDB, NetworkPolicy, Kustomize overlays).
- SKILL.md workflow: detect, then generate, pin, audit, and build-verify, with Generate / Audit / Fix / Target modes.

### Fixed

- Python template installed packages into `/root/.local` and then ran as a non-root user, which crashed with `ModuleNotFoundError`. It now uses a venv at a fixed path.
- Node template used invalid Dockerfile syntax (`COPY ... 2>/dev/null || COPY`).
- Healthchecks used `curl` on slim images where it doesn't exist.
- Kubernetes example deployed `:latest`.
- Deprecated `actions-rs/toolchain` and unpinned `trivy-action@master` in CI templates.

## 1.0.0 - 2026-07-05

- Initial release: Dockerfile/compose/CI generation and a static audit script.
