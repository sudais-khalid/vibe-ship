# Go

Covers net/http, Gin, Echo, Fiber, Chi, Gorilla, Huma, Connect, gRPC, Buffalo, Beego.

`detect.py` reports `main_packages` (`.` or `./cmd/<name>`). Build the one that serves HTTP. If there are several `cmd/*`, ask which one, or build one image per binary with `--build-arg CMD=./cmd/worker`.

## Template

```dockerfile
# syntax=docker/dockerfile:1
ARG GO_VERSION=1.27
FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-alpine AS build
WORKDIR /src
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=bind,source=go.mod,target=go.mod \
    --mount=type=bind,source=go.sum,target=go.sum \
    go mod download
COPY . .
ARG TARGETOS TARGETARCH CMD=./cmd/server
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags="-s -w" -o /out/app ${CMD}

# Runtime A (default for Compose / VPS / PaaS): small, has busybox wget for HEALTHCHECK
FROM alpine:3.24 AS runtime
RUN apk add --no-cache ca-certificates tzdata && adduser -D -u 10001 app
WORKDIR /app
COPY --from=build /out/app /app/app
USER app
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
ENTRYPOINT ["/app/app"]
```

**Runtime B (Kubernetes / Cloud Run, where the platform runs probes):** no shell, no package manager, non-root built in.

```dockerfile
FROM gcr.io/distroless/static-debian13:nonroot AS runtime
WORKDIR /app
COPY --from=build /out/app /app/app
EXPOSE 8080
# vibe-ship: ignore VS-D022 liveness/readiness probes are defined by the orchestrator
ENTRYPOINT ["/app/app"]
```

If the user wants an in-container healthcheck on distroless, the binary must check itself. Offer to add a `-healthcheck` flag that GETs `127.0.0.1:$PORT/healthz` and exits 0/1, then use `HEALTHCHECK CMD ["/app/app", "-healthcheck"]` (exec form; there's no shell).

## Notes

- `--platform=$BUILDPLATFORM` + `GOOS/GOARCH` cross-compiles natively, which makes multi-arch (`linux/amd64,linux/arm64`) builds fast with no QEMU emulation of the compiler.
- **CGO:** `detect.py` sets `needs_cgo` (for example `mattn/go-sqlite3`, `confluent-kafka-go`). Then use `CGO_ENABLED=1`, add `build-base` in the build stage, and use a glibc/musl-matched runtime (`alpine:3.24` built on alpine, or `gcr.io/distroless/base-debian13` built on `golang:1.27-bookworm`). Or swap to a pure-Go driver (`modernc.org/sqlite`), with the user's approval.
- `embed` directives need those files in the build context, so check `.dockerignore` doesn't exclude them.
- Private modules: `RUN --mount=type=secret,id=netrc,target=/root/.netrc go mod download` and `GOPRIVATE`. Never use `ARG GITHUB_TOKEN`.
- `GOMEMLIMIT`: set it to about 90% of the container memory limit (`GOMEMLIMIT=460MiB` for 512M) so the GC works before the OOM killer does. Go 1.25+ also respects cgroup CPU limits for `GOMAXPROCS` automatically.
- Signals: the Go runtime exits on SIGTERM even as PID 1, so no tini is needed (verified: stops in ~0.5s). Graceful shutdown still needs the server to handle SIGTERM with `signal.NotifyContext` + `srv.Shutdown(ctx)`. If it doesn't, mention it as a follow-up.
