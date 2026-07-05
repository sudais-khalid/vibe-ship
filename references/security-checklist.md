# Security Checklist

Apply all of these when generating deployment files. Note any that need manual action (can't be automated from inside the repo) in `DEPLOYMENT.md` rather than skipping silently.

## Secrets
- [ ] No secrets, API keys, or passwords hardcoded anywhere in Dockerfile, compose file, or source.
- [ ] `.env` is in `.gitignore` (add it if missing).
- [ ] `.env.example` documents every required var with a placeholder, never a real value.
- [ ] CI secrets (registry tokens, deploy keys) referenced via `${{ secrets.X }}`, never inline.

## Container hardening
- [ ] Every Dockerfile runs as a non-root user (`USER appuser` before `CMD`).
- [ ] Base images are slim/alpine variants, not full OS images.
- [ ] Multi-stage builds so dev dependencies and build tools never ship in the final image.
- [ ] `.dockerignore` excludes `.git`, `.env`, and anything not needed at runtime - smaller attack surface + smaller image.
- [ ] Pin base image versions (e.g. `node:20-alpine`, not `node:latest`) for reproducibility and to avoid surprise breaking changes.

## Network & runtime
- [ ] Only expose the ports the app actually needs.
- [ ] Database/cache services in docker-compose.yml are not exposed to the host unless the user needs direct access - keep them on the internal Docker network only.
- [ ] `HEALTHCHECK` instruction present so orchestrators can detect and restart unhealthy containers.

## Dependencies
- [ ] Lockfiles (`package-lock.json`, `poetry.lock`, `Gemfile.lock`, etc.) are committed and used in the Docker build (`npm ci` not `npm install`) for reproducible builds.
- [ ] CI pipeline includes a dependency vulnerability scan step (see `cicd-patterns.md`).

## Application-level (flag for the user, don't auto-fix)
- [ ] Note if there's no `/health` or equivalent endpoint - the Dockerfile HEALTHCHECK and any orchestrator readiness probe needs one; ask the user to add a minimal one if missing rather than inventing app logic.
- [ ] Note if CORS looks wide open (`*`) in the code - flag it, don't silently change app behavior.
- [ ] Note if there's no rate limiting on public-facing APIs - mention as a follow-up, don't add a rate-limiting library uninvited since it changes runtime behavior.
