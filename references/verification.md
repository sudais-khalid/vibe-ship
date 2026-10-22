# Verification ladder

Never hand over generated files without climbing as far up this ladder as the environment allows. Report the highest rung reached, and say which rungs were skipped and why. "Docker not installed, so build not verified" is an acceptable answer. Claiming it works without running it is not.

| Rung | Command | Needs | Pass criteria |
|---|---|---|---|
| 1. Static audit | `python3 scripts/audit.py --path . --fail-on medium` | Python 3.8+ | Exit 0. Every remaining LOW/INFO is either fixed or listed in DEPLOYMENT.md with a reason. |
| 2. Compose renders | `docker compose config --quiet` | Docker CLI | Exit 0. Required `${VAR:?}` errors mean `.env` is missing: copy `.env.example` → `.env` with dummy values for the check only. |
| 3. Image builds | `docker build -t vibe-ship-check:local .` (per service; monorepo: `-f apps/x/Dockerfile .`) | Docker daemon | Exit 0. Read the last 30 lines on failure, fix, and rebuild. Don't hand over a red build. |
| 4. Container runs + healthy | `docker compose up -d --wait --wait-timeout 120` | Docker daemon | Every service reports healthy. On failure: `docker compose ps` + `docker compose logs --tail 50 <svc>`. |
| 5. Health endpoint answers | `curl -fsS http://127.0.0.1:<port>/<health_path>` | curl | HTTP 2xx |
| 6. Runs as non-root | `docker compose exec <app> id -u` (skip for distroless) | running stack | Non-zero UID |
| 7. Image hygiene | `docker image ls vibe-ship-check:local` (size), optionally `trivy image --severity CRITICAL,HIGH vibe-ship-check:local` | trivy optional | Size plausible for the stack (Go/Rust under 50 MB, Node/Python under 300 MB, JVM under 350 MB), no fixable CRITICAL |
| 8. Workflow lint | `actionlint` (if installed) | actionlint | Exit 0 |
| 9. K8s schema | `kubectl apply --dry-run=client -k k8s/...` or `kubeconform -strict` | kubectl | Exit 0 |
| 10. Cleanup | `docker compose down` (**never** `-v` on a user's real data), `docker image rm vibe-ship-check:local` | | Nothing left running that the user didn't start |

Rules:
- Rungs 3–6 start containers and pull images, which is visible activity on the user's machine. In an interactive session, ask once ("Build and smoke-test the images locally? This pulls base images and starts containers.") unless the user already asked for verification.
- Use dummy values only for the check, and delete any `.env` created just for it, unless the user wants to keep it. Never print real secret values.
- If a step fails because of the app itself (tests fail, the app crashes on boot from missing config), report it as an app issue with the log excerpt. Don't paper over it in the Dockerfile.
- Re-run rung 1 after every fix. It takes milliseconds.
