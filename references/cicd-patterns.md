# CI/CD patterns

Every pipeline has the same shape:

```
test (lint + unit/integration, real DB via service containers)
  └─> image (build → scan → push on main/tags only → SBOM + provenance → sign)
        └─> deploy (only if the user named a target; see deploy-targets.md)
```

Defaults: GitHub Actions, GHCR (it authenticates with `GITHUB_TOKEN`, so no extra secrets), least-privilege `permissions`, every action pinned to a SHA (`scripts/pin_actions.py`), and timeouts on every job. Ask which registry only if the user mentions Docker Hub, ECR, GAR, or ACR, then list the secrets they need in DEPLOYMENT.md.

## GitHub Actions: full template (`.github/workflows/ci-cd.yml`)

```yaml
name: CI/CD

on:
  push:
    branches: [main]
    tags: ["v*.*.*"]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    # services:            # only if tests need them - mirror compose (see "Service containers")
    steps:
      - uses: actions/checkout@v7
      # --- stack-specific steps (next section) ---

  image:
    needs: test
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      packages: write          # push to GHCR
      security-events: write   # upload Trivy SARIF to the Security tab
      id-token: write          # keyless cosign signing
      attestations: write
    steps:
      - uses: actions/checkout@v7

      - uses: docker/setup-buildx-action@v4

      - id: meta
        uses: docker/metadata-action@v6
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=pr
            type=sha,prefix=sha-,format=long
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable={{is_default_branch}}

      # 1) Build once into the local daemon for scanning (cache makes step 3 near-instant)
      - uses: docker/build-push-action@v7
        with:
          context: .
          load: true
          tags: scan-target:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

      # 2) Scan the image: fail on fixable CRITICAL/HIGH, upload findings to code scanning
      - uses: aquasecurity/trivy-action@v0.36.0
        with:
          image-ref: scan-target:ci
          format: sarif
          output: trivy.sarif
          severity: CRITICAL,HIGH
          ignore-unfixed: true
          exit-code: "0"       # flip to "1" once the backlog is clean
      - uses: github/codeql-action/upload-sarif@v4
        if: always() && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)
        with:
          sarif_file: trivy.sarif
          category: trivy-image

      # 3) Push (never from PRs) with SBOM + max provenance attestations
      - if: github.event_name != 'pull_request'
        uses: docker/login-action@v4
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: push
        uses: docker/build-push-action@v7
        with:
          context: .
          # platforms: linux/amd64,linux/arm64   # needs docker/setup-qemu-action unless the Dockerfile cross-compiles
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          provenance: mode=max
          sbom: true

      # 4) Keyless signature (Sigstore, OIDC - no keys to manage)
      - if: github.event_name != 'pull_request'
        uses: sigstore/cosign-installer@v4
      - if: github.event_name != 'pull_request'
        env:
          DIGEST: ${{ steps.push.outputs.digest }}
          TAGS: ${{ steps.meta.outputs.tags }}
        run: |
          for tag in ${TAGS}; do cosign sign --yes "${tag}@${DIGEST}"; done
```

After writing it, run:

```bash
python3 scripts/pin_actions.py .github/workflows      # tags -> commit SHAs
python3 scripts/audit.py --path . --fail-on high      # must come out clean
```

Notes:
- The `if:` on upload-sarif skips fork PRs, which get a read-only token.
- Multi-arch: Go, Rust, and .NET templates cross-compile via `$BUILDPLATFORM`, so `platforms: linux/amd64,linux/arm64` is cheap. Interpreted stacks need `docker/setup-qemu-action@v4`, which is slow, so only add arm64 when the user deploys to ARM (Graviton, Ampere, Apple Silicon dev).
- Monorepo: one `image` job per service via `strategy.matrix` (`service: [web, api]`, `file: apps/${{ matrix.service }}/Dockerfile`, `images: ghcr.io/${{ github.repository }}-${{ matrix.service }}`), and add `dorny/paths-filter` to skip unchanged services.
- Making the GHCR package public, or linking it to the repo, is a manual step. Add it to the DEPLOYMENT.md checklist.

## Stack-specific `test` steps

**Node** (swap `npm` for the detected PM. For pnpm, add `pnpm/action-setup@v6` before setup-node):
```yaml
      - uses: actions/setup-node@v7
        with: { node-version-file: .nvmrc, cache: npm }   # or node-version: 24
      - run: npm ci
      - run: npm run lint --if-present
      - run: npm run typecheck --if-present
      - run: npm test --if-present
      - run: npm run build --if-present
```

**Bun:** `oven-sh/setup-bun@v2` → `bun install --frozen-lockfile` → `bun test`.
**Deno:** `denoland/setup-deno@v2` → `deno lint` → `deno test -A`.

**Python (uv):**
```yaml
      - uses: astral-sh/setup-uv@v10
        with: { enable-cache: true }
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run pytest -q
```
**Python (pip):** `actions/setup-python@v7` with `python-version-file: .python-version` and `cache: pip`, then `pip install -r requirements.txt -r requirements-dev.txt` (if it exists), `ruff check .`, `pytest -q`. Poetry: `pipx install poetry==2.2.1` + `poetry install` + `poetry run pytest`.

**Go:**
```yaml
      - uses: actions/setup-go@v7
        with: { go-version-file: go.mod }
      - run: go vet ./...
      - run: go test -race -count=1 ./...
```

**Rust:**
```yaml
      - uses: dtolnay/rust-toolchain@stable
        with: { components: "clippy, rustfmt" }
      - uses: Swatinem/rust-cache@v2
      - run: cargo fmt --check
      - run: cargo clippy --all-targets --locked -- -D warnings
      - run: cargo test --locked
```

**Java:** `actions/setup-java@v6` with `distribution: temurin`, `java-version: 25`, `cache: maven` (or `gradle`), then `./mvnw -B verify` or `./gradlew check`.

**.NET:** `actions/setup-dotnet@v6` with `dotnet-version: 10.0.x`, then `dotnet restore`, `dotnet build --no-restore -c Release`, and `dotnet test --no-build -c Release`.

**Ruby:** `ruby/setup-ruby@v1` with `bundler-cache: true`, then `bin/rails db:prepare` (needs a DB service) and `bin/rails test` / `bundle exec rspec`. Add `bin/rubocop` and `bin/brakeman` if present.

**PHP:** `shivammathur/setup-php@v2` with `php-version: "8.4"`, `extensions: pdo_pgsql, redis`, `coverage: none`, then `composer install --no-interaction --prefer-dist` and `vendor/bin/phpunit` / `vendor/bin/pest` / `php artisan test`.

**Elixir:** `erlef/setup-beam@v1` with `elixir-version` / `otp-version` from `.tool-versions`, then `mix deps.get`, `mix compile --warnings-as-errors`, `mix test`.

## Service containers (tests that need a DB)

```yaml
    services:
      postgres:
        image: postgres:18-alpine
        env: { POSTGRES_PASSWORD: postgres, POSTGRES_DB: test }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s --health-timeout 3s --health-retries 10
      redis:
        image: redis:8-alpine
        ports: ["6379:6379"]
        options: --health-cmd "redis-cli ping" --health-interval 5s --health-retries 10
    env:
      DATABASE_URL: postgres://postgres:postgres@localhost:5432/test
      REDIS_URL: redis://localhost:6379/0
```

These throwaway CI credentials are fine in the workflow, because they protect nothing. The audit ignores `postgres`/`test` placeholders.

## Dependency + base-image updates (`.github/dependabot.yml`)

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule: { interval: weekly }
    groups: { actions: { patterns: ["*"] } }
  - package-ecosystem: docker
    directory: /
    schedule: { interval: weekly }
  - package-ecosystem: docker-compose
    directory: /
    schedule: { interval: weekly }
  - package-ecosystem: npm          # pip / uv / gomod / cargo / maven / gradle / nuget / bundler / composer / mix
    directory: /
    schedule: { interval: weekly }
    groups:
      minor-and-patch: { update-types: [minor, patch] }
```

Dependabot updates SHA-pinned actions and keeps the version comment in sync. That's why SHA pinning has no maintenance cost. Renovate (`renovate.json` with `"extends": ["config:recommended", "helpers:pinGitHubActionDigests"]`) is the alternative if the user prefers it.

## Extra jobs worth offering (don't add uninvited)

- **Deployment-config audit in CI** with vibe-ship's own GitHub Action. It uploads findings to code scanning and fails on HIGH:
  ```yaml
  audit:
    runs-on: ubuntu-latest
    permissions: { contents: read, security-events: write }
    steps:
      - uses: actions/checkout@v7
      - uses: sudais-khalid/vibe-ship@v2
        with: { fail-on: high }
  ```
- **Dependency review on PRs:** `actions/dependency-review-action@v4` with `fail-on-severity: high`.
- **CodeQL:** `github/codeql-action/init@v4` + `analyze@v4` for supported languages.
- **Preview environments per PR:** only on platforms that support it (Render, Railway, Fly, Vercel).

## GitLab CI (`.gitlab-ci.yml`)

```yaml
stages: [test, build, deploy]

variables:
  IMAGE: $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  DOCKER_TLS_CERTDIR: "/certs"

test:
  stage: test
  image: node:24-alpine              # stack image, pinned
  cache: { key: { files: [package-lock.json] }, paths: [.npm/] }
  script:
    - npm ci --cache .npm --prefer-offline
    - npm test

build:
  stage: build
  image: docker:28
  services: [docker:28-dind]
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    - echo "$CI_REGISTRY_PASSWORD" | docker login -u "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
    - docker build --pull -t "$IMAGE" -t "$CI_REGISTRY_IMAGE:latest" .
    - docker push "$IMAGE" && docker push "$CI_REGISTRY_IMAGE:latest"

include:
  - template: Jobs/Container-Scanning.gitlab-ci.yml
  - template: Jobs/Dependency-Scanning.gitlab-ci.yml
  - template: Jobs/Secret-Detection.gitlab-ci.yml
```

dind needs a privileged runner. On shared runners that's fine; on self-managed runners, suggest Kaniko or Buildah instead.

## Other CI systems (only if the repo already uses them)

| System | File | Image build approach |
|---|---|---|
| Bitbucket Pipelines | `bitbucket-pipelines.yml` | `services: [docker]` + `docker build`; scanning via a `pipe: atlassian/...` |
| CircleCI | `.circleci/config.yml` | `setup_remote_docker` + `docker build`, or the `circleci/docker` orb |
| Azure Pipelines | `azure-pipelines.yml` | `Docker@2` task with `buildAndPush` + service connection |
| Jenkins | `Jenkinsfile` | `docker.build()` in a declarative pipeline; Trivy as a stage |
| Google Cloud Build | `cloudbuild.yaml` | `gcr.io/cloud-builders/docker` steps → Artifact Registry |
| AWS CodeBuild | `buildspec.yml` | `docker build` + `aws ecr get-login-password` |

Same shape everywhere: test, then build, then scan, then push on the default branch only, then deploy. Secrets come from the CI system's secret store.
