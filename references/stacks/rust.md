# Rust

Covers Axum, Actix Web, Rocket, Warp, Poem, Salvo, Loco, Tonic (gRPC), and CLI/worker binaries.

`detect.py` reports `binary` (the package name or the first `[[bin]]`). Cargo turns `-` into `_` only for crate names, not binaries, so `target/release/<binary>` uses the name exactly as in Cargo.toml.

## Template (BuildKit cache mounts, no cargo-chef needed)

```dockerfile
# syntax=docker/dockerfile:1
ARG RUST_VERSION=1.99
ARG APP=svc
FROM rust:${RUST_VERSION}-slim AS build
ARG APP
WORKDIR /src
# tini-static: Rust binaries don't handle SIGTERM by default, and PID 1 ignores unhandled signals,
# so without an init `docker stop` / pod termination waits for the kill timeout (verified: 10s -> SIGKILL).
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
# Only if a crate links OpenSSL (prefer rustls features instead):
# RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,source=src,target=src \
    --mount=type=bind,source=Cargo.toml,target=Cargo.toml \
    --mount=type=bind,source=Cargo.lock,target=Cargo.lock \
    --mount=type=cache,target=/src/target \
    --mount=type=cache,target=/usr/local/cargo/git/db \
    --mount=type=cache,target=/usr/local/cargo/registry \
    cargo build --locked --release && cp target/release/${APP} /bin/app

FROM gcr.io/distroless/cc-debian13:nonroot AS runtime
COPY --from=build /usr/bin/tini-static /tini
COPY --from=build /bin/app /app
EXPOSE 8080
# vibe-ship: ignore VS-D022 orchestrator probes; add a `healthcheck` subcommand for in-container checks
ENTRYPOINT ["/tini", "--", "/app"]
```

- Better than tini: handle SIGTERM in the app for graceful drain (`tokio::signal` + axum `with_graceful_shutdown`). Keep tini anyway for zombie reaping.
- Add more `--mount=type=bind` lines for anything else the build reads: `build.rs`, `migrations/`, `templates/`, `.sqlx/` (sqlx offline mode), and workspace member dirs. Workspaces: bind the whole workspace or switch to `COPY . .` + cache mounts.
- **Compose/VPS with HEALTHCHECK:** use `debian:trixie-slim` as runtime, `RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* && useradd -r -u 10001 app`, then `USER app` and `HEALTHCHECK CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1`.
- **OpenSSL linked** (`needs_openssl` from detect): distroless `cc` has no libssl. Use `debian:trixie-slim` + `libssl3t64` at runtime, or ask to switch reqwest/sqlx to `rustls-tls`.
- **Static musl binary:** target `x86_64-unknown-linux-musl` (`rustup target add` + `musl-tools`), then `FROM gcr.io/distroless/static-debian13:nonroot`.
- **sqlx** compile-time checked queries need either a DB at build time (don't do that) or `SQLX_OFFLINE=true` with a committed `.sqlx/` directory.
- Bind to `0.0.0.0`, not `127.0.0.1`. Axum/Actix examples often hardcode `127.0.0.1:3000`, which is unreachable from outside the container. Check the source and flag it.
- Rocket defaults to `127.0.0.1:8000`: set `ROCKET_ADDRESS=0.0.0.0` and `ROCKET_PORT=8080`.
