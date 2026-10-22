# Ruby: Rails, Sinatra, Hanami, Roda, Rack

## Rails ≥ 7.1: use the generated Dockerfile first

Rails 7.1+ generates a production Dockerfile, `bin/docker-entrypoint`, and `.dockerignore` (`rails new` or `bin/rails app:update`). Rails 8 also adds Kamal (`config/deploy.yml`) and Thruster. If those files exist, **audit and harden them instead of replacing them**: `python3 scripts/audit.py --path .`. The Rails team maintains them alongside the framework.

If they don't exist, generate an equivalent:

```dockerfile
# syntax=docker/dockerfile:1
ARG RUBY_VERSION=3.4
FROM ruby:${RUBY_VERSION}-slim AS base
WORKDIR /rails
RUN apt-get update -qq && apt-get install --no-install-recommends -y curl libjemalloc2 libvips postgresql-client \
 && rm -rf /var/lib/apt/lists/*
ENV RAILS_ENV=production BUNDLE_DEPLOYMENT=1 BUNDLE_PATH=/usr/local/bundle BUNDLE_WITHOUT=development:test \
    LD_PRELOAD=libjemalloc.so.2

FROM base AS build
RUN apt-get update -qq && apt-get install --no-install-recommends -y build-essential git libpq-dev libyaml-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*
COPY Gemfile Gemfile.lock ./
RUN bundle install && rm -rf ~/.bundle/ "${BUNDLE_PATH}"/ruby/*/cache "${BUNDLE_PATH}"/ruby/*/bundler/gems/*/.git \
 && bundle exec bootsnap precompile --gemfile
COPY . .
RUN bundle exec bootsnap precompile app/ lib/
RUN SECRET_KEY_BASE_DUMMY=1 ./bin/rails assets:precompile

FROM base AS runtime
RUN groupadd --system --gid 1000 rails && useradd rails --uid 1000 --gid 1000 --create-home --shell /bin/bash
COPY --from=build "${BUNDLE_PATH}" "${BUNDLE_PATH}"
COPY --from=build --chown=rails:rails /rails /rails
USER 1000:1000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s CMD curl -fsS http://127.0.0.1:3000/up || exit 1
CMD ["./bin/rails", "server", "-b", "0.0.0.0", "-p", "3000"]
```

- Swap `postgresql-client` and `libpq-dev` for `default-mysql-client` and `default-libmysqlclient-dev` (MySQL), or `libsqlite3-0` (SQLite), based on `detect.py` backing services.
- **Assets:** if `detect.py` reports `asset_build` (a package.json with esbuild/vite/tailwind), add a node install in the build stage before `assets:precompile`: `COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/` and install packages with the detected PM. jsbundling-rails calls `yarn build` / `npm run build` during precompile.
- **Rails 8 + Thruster:** `CMD ["./bin/thrust", "./bin/rails", "server"]` and `EXPOSE 80`. Thruster adds HTTP/2, compression, and X-Sendfile in front of Puma. Port 80 as non-root works because Docker allows unprivileged binding of ports below 1024 inside the container namespace (`net.ipv4.ip_unprivileged_port_start=0` is the Docker default).
- **Migrations:** `bin/rails db:prepare`, run once per deploy. Use a one-off service/job, or the generated `bin/docker-entrypoint`, which runs it when the command is `rails server`. That's fine for one replica but races with several.
- `/up` is the built-in health endpoint (Rails ≥ 7.1).
- `RAILS_MASTER_KEY` (or `SECRET_KEY_BASE`) is a runtime secret. Never use `ARG`.
- Logs: `RAILS_LOG_TO_STDOUT=1` (default in 7.1+ production config).

## Sidekiq / Solid Queue / GoodJob workers

Same image, different command: `["bundle", "exec", "sidekiq"]`, `["bin/jobs"]` (Solid Queue, Rails 8), `["bundle", "exec", "good_job", "start"]`. Sidekiq needs Redis. Solid Queue and GoodJob use the database.

## Sinatra / Roda / Hanami / plain Rack

```dockerfile
FROM ruby:3.4-slim AS build
RUN apt-get update -qq && apt-get install --no-install-recommends -y build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV BUNDLE_DEPLOYMENT=1 BUNDLE_WITHOUT=development:test
COPY Gemfile Gemfile.lock ./
RUN bundle install
COPY . .

FROM ruby:3.4-slim AS runtime
RUN useradd -r -u 10001 app
WORKDIR /app
ENV BUNDLE_DEPLOYMENT=1 BUNDLE_WITHOUT=development:test RACK_ENV=production
COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build --chown=app /app /app
USER app
EXPOSE 9292
HEALTHCHECK CMD ruby -rnet/http -e 'exit(Net::HTTP.get_response(URI("http://127.0.0.1:9292/healthz")).is_a?(Net::HTTPSuccess) ? 0 : 1)'
CMD ["bundle", "exec", "puma", "-b", "tcp://0.0.0.0:9292", "config.ru"]
```

`bundle exec puma` is fine as a CMD. Bundler execs into puma, so signals reach it, unlike `npm start`.

## Jekyll

That's a static site, so see `static.md`.
