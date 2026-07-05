# Anti-Patterns (Gotchas)

Concrete mistakes to avoid by construction when generating files, and to flag when auditing existing ones.

## Dockerfile

| Anti-pattern | Why it's a problem | Fix |
|---|---|---|
| `FROM node:latest` (or any `:latest`) | Non-reproducible builds; breaks silently on upstream updates | Pin exact version: `FROM node:20-alpine` |
| `COPY . .` before installing dependencies | Busts Docker layer cache on every code change, dependencies reinstall every build | Copy lockfile/manifest first, install, then `COPY . .` |
| No `USER` instruction | Container runs as root - a container escape becomes a host root exploit | Add `RUN adduser` + `USER appuser` before `CMD` |
| Secrets in `ENV` or `ARG` | Baked into image layers, visible via `docker history` even after removal | Pass at runtime via `--env-file` / compose `environment:`, or BuildKit secret mounts for build-time secrets |
| Single-stage build for compiled/bundled apps | Ships full toolchain (compilers, dev deps) in production image - bigger attack surface and image size | Multi-stage: build stage + slim runtime stage |
| Image over ~500MB-1GB for a simple app | Usually means dev dependencies or unnecessary OS packages leaked into the final layer | Audit what's actually copied into the runtime stage |
| `apt-get install` without `rm -rf /var/lib/apt/lists/*` in the same `RUN` | Package manager cache persists in the layer even if you clean up in a later line | Clean up in the same `RUN` command |
| No `HEALTHCHECK` | Orchestrators can't detect a hung/broken container | Add a `HEALTHCHECK` hitting a real endpoint |
| Hardcoded `EXPOSE`/port not matching app config | Container works locally, breaks when the platform expects a different port | Read port from env var (e.g. `PORT`), document it |

## docker-compose.yml

| Anti-pattern | Why it's a problem | Fix |
|---|---|---|
| Database ports exposed to host (`5432:5432`) unnecessarily | Widens attack surface for no reason if nothing outside the compose network needs direct access | Only expose ports actually needed from the host |
| No named volumes for stateful services | Data lost on `docker compose down` | Add named volumes for db/cache data dirs |
| Plaintext credentials in the compose file | Same problem as Dockerfile secrets, just relocated | Use `env_file: .env` and keep `.env` out of git |
| No resource limits | One runaway service can starve the whole host | Set `mem_limit`/`cpus` per service as a starting point |

## CI/CD

| Anti-pattern | Why it's a problem | Fix |
|---|---|---|
| No test/lint step before build | Broken code gets built and pushed | Always run tests before the build-and-push job, gate the latter on the former |
| Build-and-push runs on every branch/PR | Pollutes the registry with untagged/throwaway images | Gate push behind `if: github.ref == 'refs/heads/main'` |
| Secrets referenced by literal value instead of `${{ secrets.X }}` | Secret ends up in the workflow file/logs | Always use the secrets context |
| No dependency vulnerability scan | Known CVEs ship silently | Add a scan step (e.g. Trivy), even non-blocking to start |
