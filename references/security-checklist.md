# Security checklist

Apply every item when generating. Items that can't be done from inside the repo go into DEPLOYMENT.md's "You still need to" list, never silently skipped. Rule IDs refer to `scripts/audit.py --list-rules`.

## Secrets
- [ ] No secret in a Dockerfile `ENV`/`ARG`, compose literal, workflow literal, or manifest (VS-D040/D041, C020, G030, K040, S001).
- [ ] `.env` and `.env.*` in `.gitignore` (with `!.env.example`) and `.dockerignore` (VS-R001, D101). If `.env` is already tracked, tell the user to `git rm --cached .env` **and rotate every value** (VS-R002).
- [ ] `.env.example` lists every var `detect.py` found (`env_vars`), with placeholders and a one-line comment each.
- [ ] Compose uses `${VAR:?message}` for required secrets so a missing value fails loudly instead of starting with an empty password.
- [ ] Build-time secrets (private registries, npm tokens) use `RUN --mount=type=secret,id=...` + `docker build --secret id=...,env=...`.
- [ ] CI uses `${{ secrets.X }}`, and cloud logins use **OIDC** (`id-token: write`) instead of long-lived keys.

## Image hardening
- [ ] Multi-stage, with only runtime artifacts in the final stage (VS-D030).
- [ ] Pinned base tags, never `:latest` (VS-D001/D002). Digest pinning plus Dependabot/Renovate for strict supply chains (VS-D003).
- [ ] Non-root `USER` in the final stage, or a base that is non-root by design: distroless `:nonroot`, chiseled, `nginx-unprivileged` (VS-D010/D011).
- [ ] No `sudo`, no `chmod 777`, no sshd/port 22 (VS-D012/D013/D014).
- [ ] No `curl | sh`, and `ADD <url>` only with `--checksum` (VS-D052/D050).
- [ ] Minimal base: alpine/slim/distroless. Package installs use `--no-install-recommends` / `--no-cache`.
- [ ] OCI labels for traceability: `org.opencontainers.image.source=https://github.com/OWNER/REPO` (also links GHCR packages to the repo), `revision`, `version`. `docker/metadata-action` adds these in CI.

## Runtime
- [ ] Only the app's port is published. Databases and caches stay on the internal network (VS-C030).
- [ ] No `privileged`, docker.sock, host namespaces, or `SYS_ADMIN` (VS-C001–C005, K030, K035, K036).
- [ ] K8s: `runAsNonRoot`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`, and a NetworkPolicy.
- [ ] Compose extras when the app allows them: `read_only: true` + `tmpfs: [/tmp]`, `security_opt: ["no-new-privileges:true"]`, `cap_drop: [ALL]`.
- [ ] TLS terminated at the platform or reverse proxy, HSTS enabled, and the app trusts forwarded proto (see `servers.md`).

## Supply chain / CI
- [ ] Workflow `permissions:` least-privilege at top level, widened per job (VS-G020/G021).
- [ ] Actions pinned to commit SHAs via `scripts/pin_actions.py` (VS-G001/G002), kept fresh by Dependabot `github-actions`.
- [ ] No untrusted `${{ github.event.* }}` inside `run:` (VS-G011). No `pull_request_target` that checks out PR code (VS-G010).
- [ ] Image scan (Trivy) with SARIF upload, plus dependency updates (Dependabot/Renovate) (VS-G050).
- [ ] SBOM + provenance attestations, plus keyless cosign signing on push (VS-G051).
- [ ] Push only from the default branch or tags, never from PRs (VS-G041).

## Application-level (flag, don't auto-fix: these change app behaviour)
- [ ] Health endpoint exists. If not, offer a minimal `/healthz`.
- [ ] Binds `0.0.0.0` and reads `PORT`.
- [ ] CORS isn't `*` on authenticated APIs.
- [ ] Rate limiting on public auth endpoints.
- [ ] Debug mode is off in production (`DEBUG=False`, `APP_DEBUG=false`, `NODE_ENV=production`, `RAILS_ENV=production`).
- [ ] Error pages don't leak stack traces.
