# Elixir / Erlang / Gleam (BEAM)

## Phoenix: prefer the official generator

`mix phx.gen.release --docker` (Phoenix ≥ 1.6) writes a correct release Dockerfile, `rel/overlays/bin/server`, and `bin/migrate`. If the project has Phoenix, suggest running it and then **audit** the result. The template below is equivalent, for when the user can't run mix locally.

```dockerfile
# syntax=docker/dockerfile:1
ARG ELIXIR_VERSION=1.19.0
ARG OTP_VERSION=28.1
ARG DEBIAN_VERSION=trixie-20250929-slim
ARG BUILDER_IMAGE="hexpm/elixir:${ELIXIR_VERSION}-erlang-${OTP_VERSION}-debian-${DEBIAN_VERSION}"
ARG RUNNER_IMAGE="debian:${DEBIAN_VERSION}"

FROM ${BUILDER_IMAGE} AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV MIX_ENV=prod
RUN mix local.hex --force && mix local.rebar --force
COPY mix.exs mix.lock ./
RUN mix deps.get --only $MIX_ENV
RUN mkdir config
COPY config/config.exs config/${MIX_ENV}.exs config/
RUN mix deps.compile
COPY priv priv
COPY lib lib
COPY assets assets
RUN mix assets.deploy
RUN mix compile
COPY config/runtime.exs config/
COPY rel rel
RUN mix release

FROM ${RUNNER_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 openssl libncurses6 locales ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8 MIX_ENV=prod PHX_SERVER=true PORT=4000
WORKDIR /app
RUN useradd --system --uid 10001 app && chown app /app
COPY --from=build --chown=app:root /app/_build/prod/rel/my_app ./
USER app
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD curl -fsS http://127.0.0.1:4000/healthz || exit 1
CMD ["/app/bin/server"]
```

- Replace `my_app` with `app_name` from `detect.py` (the `app:` atom in mix.exs).
- Remove `COPY assets` / `mix assets.deploy` for API-only apps, and `COPY rel` if there's no `rel/` dir. In that case CMD is `["/app/bin/my_app", "start"]` with `PHX_SERVER=true`.
- Build and runtime **must** use the same Debian snapshot, because the release links against the build OS's OpenSSL and ncurses.
- **Migrations:** `/app/bin/migrate` (generated) or `/app/bin/my_app eval "MyApp.Release.migrate"`, run as a one-off.
- **Runtime env:** `SECRET_KEY_BASE`, `DATABASE_URL`, and `PHX_HOST` come from `config/runtime.exs`.
- **Clustering** (libcluster / `dns_cluster`): set `RELEASE_DISTRIBUTION=name`, `RELEASE_NODE=my_app@$(hostname -i)`, and a shared `RELEASE_COOKIE` secret. On K8s, use a headless Service.
- **Health:** Phoenix has no built-in route. Offer a tiny plug at `/healthz`, or check `/`.

## Plain Elixir / Erlang (rebar3) / Gleam

- **Elixir non-Phoenix:** same template minus assets. The release command comes from `mix release`.
- **Erlang:** `FROM erlang:28 AS build` → `rebar3 as prod release` → copy `_build/prod/rel/<app>` to the same Debian runtime, then `CMD ["/app/bin/<app>", "foreground"]`.
- **Gleam:** `FROM ghcr.io/gleam-lang/gleam:v1.x-erlang-alpine AS build` → `gleam export erlang-shipment` → copy `build/erlang-shipment` to an `erlang:28-alpine` runtime, then `CMD ["/app/entrypoint.sh", "run"]`. Check the current Gleam tag on GHCR before pinning.
