# Other stacks

Same rules everywhere: multi-stage, pinned images, non-root user, exec-form CMD, a healthcheck tool that exists in the final image, and listening on `0.0.0.0`.

## Swift (Vapor, Hummingbird)

```dockerfile
FROM swift:6.2-noble AS build
WORKDIR /build
COPY Package.swift Package.resolved ./
RUN swift package resolve
COPY . .
RUN swift build -c release --static-swift-stdlib -Xlinker -s \
 && mkdir /staging && cp "$(swift build -c release --show-bin-path)/App" /staging/ \
 && find -L "$(swift build -c release --show-bin-path)" -regex '.*\.resources$' -exec cp -Ra {} /staging/ \;

FROM ubuntu:noble AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata curl && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 vapor
WORKDIR /app
COPY --from=build --chown=vapor /staging /app
USER vapor
EXPOSE 8080
HEALTHCHECK CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["./App"]
CMD ["serve", "--env", "production", "--hostname", "0.0.0.0", "--port", "8080"]
```

Replace `App` with the executable target name from Package.swift. Vapor's template ships a similar Dockerfile, so if one exists, audit it instead.

## Dart (Shelf, Dart Frog)

```dockerfile
FROM dart:3.9 AS build
WORKDIR /app
COPY pubspec.* ./
RUN dart pub get
COPY . .
RUN dart pub get --offline && dart compile exe bin/server.dart -o bin/server

FROM scratch
COPY --from=build /runtime/ /
COPY --from=build /app/bin/server /app/bin/server
USER 10001:10001
EXPOSE 8080
# vibe-ship: ignore VS-D022 scratch image has no tools; use platform probes
CMD ["/app/bin/server"]
```

Dart Frog: run `dart_frog build` first, then compile `build/bin/server.dart`.

## Haskell (Stack / Cabal)

Build on `haskell:9.10` with `stack build --copy-bins --local-bin-path /out` or `cabal install --installdir=/out --install-method=copy`. Run on `debian:trixie-slim` with `libgmp10 ca-certificates` and a non-root user. Cache `~/.stack` / `~/.cabal` with cache mounts.

## Crystal (Kemal, Lucky, Amber)

`FROM crystallang/crystal:1.17-alpine AS build` → `shards install --production` → `crystal build --release --static src/app.cr -o /out/app`, then `FROM alpine:3.24` (or `scratch` with CA certs) with a non-root user.

## Zig

`FROM alpine:3.24 AS build` + download a pinned Zig tarball with a checksum → `zig build -Doptimize=ReleaseSafe` → static binary → `gcr.io/distroless/static-debian13:nonroot`.

## C / C++ (CMake, Meson, Make)

```dockerfile
FROM debian:trixie-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ninja-build pkg-config \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build && cmake --install build --prefix /out

FROM debian:trixie-slim AS runtime
RUN useradd --system --uid 10001 app
COPY --from=build /out /usr/local
USER app
ENTRYPOINT ["/usr/local/bin/app"]
```

Add the runtime `.so` packages (`libssl3t64`, `libstdc++6` is already present) that `ldd` shows. With vcpkg/Conan, restore in the build stage with cache mounts.

## Perl, Lua/OpenResty, R (Shiny/Plumber), Julia

- **Perl** (Mojolicious/Dancer): `perl:5.40-slim` + `cpanm --installdeps --notest .` → `hypnotoad -f` / `starman`.
- **OpenResty:** `openresty/openresty:alpine` with config + Lua under `/usr/local/openresty/nginx`. Use non-root with `listen 8080` and writable temp paths.
- **R Shiny:** `rocker/shiny:4` (port 3838, runs as user `shiny`). **Plumber:** `rocker/r-ver:4` + `R -e "pr <- plumber::plumb('api.R'); pr\$run(host='0.0.0.0', port=8000)"`.
- **Julia:** `julia:1.11` with `Pkg.instantiate()` + `Pkg.precompile()` at build, and a non-root user with `JULIA_DEPOT_PATH` owned by it.

## Unknown stack

Don't guess. Report what `detect.py` found, ask the user for the build command, run command, and port, then apply the universal rules.
