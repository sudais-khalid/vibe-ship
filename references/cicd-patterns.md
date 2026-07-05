# CI/CD Patterns (GitHub Actions)

Every pipeline has the same shape: **lint/test → build image → push (on main only)**. Fill in the stack-specific steps below. Default registry is GHCR (`ghcr.io`) since it authenticates with the built-in `GITHUB_TOKEN` - no extra secrets needed. Switch to Docker Hub/ECR/GCR only if the user asks, and note in `DEPLOYMENT.md` which secrets they'll need to add.

## Base template (all stacks)

```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # --- stack-specific setup + test steps go here ---

  build-and-push:
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:latest,ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

## Stack-specific `test` job steps

**Node.js**
```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci
      - run: npm run lint --if-present
      - run: npm test --if-present
```

**Python**
```yaml
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: pip install ruff pytest
      - run: ruff check .
      - run: pytest
```

**Go**
```yaml
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: go vet ./...
      - run: go test ./...
```

**Java/Kotlin (Gradle)**
```yaml
      - uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '21'
      - run: ./gradlew test
```

**Ruby**
```yaml
      - uses: ruby/setup-ruby@v1
        with:
          ruby-version: '3.3'
          bundler-cache: true
      - run: bundle exec rspec
```

**Rust**
```yaml
      - uses: actions-rs/toolchain@v1
        with:
          toolchain: stable
      - run: cargo test
      - run: cargo clippy -- -D warnings
```

## Security scanning step (add to every pipeline)

Add this as its own job or step, non-blocking to start (`continue-on-error: true`) so it doesn't break existing pipelines on adoption - flip to blocking once the user is ready:

```yaml
      - name: Dependency vulnerability scan
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          exit-code: '0'   # set to '1' once the user wants this to fail the build
          severity: 'CRITICAL,HIGH'
```

## Deploy step (optional, ask before adding)

Only add an actual deploy step if the user names a target platform. Common ones:

- **Render/Railway/Fly.io**: usually a CLI action + API token secret (`flyctl deploy`, etc.)
- **AWS ECS**: `aws-actions/amazon-ecs-deploy-task-definition`
- **Kubernetes**: `kubectl apply` or `helm upgrade` against a cluster, needs kubeconfig as a secret
- **VPS via SSH**: `appleboy/ssh-action` to pull the new image and restart the container

Don't guess a deploy target - ask, since getting this wrong means writing secrets/credentials workflow for infrastructure that doesn't exist.
