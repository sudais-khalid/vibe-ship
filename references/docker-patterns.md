# Dockerfile Patterns by Stack

All templates follow the same shape: a **build stage** with full toolchain, a **slim runtime stage** with only what's needed to run, a non-root user, and a `HEALTHCHECK`. Adapt paths, ports, and commands to what you actually detected in the project - these are starting points, not copy-paste-blind.

---

## Node.js

```dockerfile
# ---- Build stage ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build 2>/dev/null || true   # no-op if there's no build step (plain Express apps etc.)

# ---- Runtime stage ----
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist 2>/dev/null || COPY --from=build /app .
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["node", "dist/index.js"]
```
Adjust the `CMD`/build output path based on `package.json`'s `main`/`scripts.start`. For frameworks like Next.js, use `next build` + `next start` and expose 3000; for static frontends (React/Vite build output), use a separate `nginx:alpine` runtime stage serving `/dist` instead of a Node runtime stage.

## Python

```dockerfile
# ---- Build stage ----
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=build /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
RUN useradd -m appuser
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Swap the `CMD` for `gunicorn app:app` (Flask/Django with WSGI) or `python manage.py runserver 0.0.0.0:8000` only for local dev - production Django should use `gunicorn`. Use `poetry export` first if the project uses Poetry instead of requirements.txt.

## Go

```dockerfile
FROM golang:1.22-alpine AS build
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app/server .

FROM alpine:3.19 AS runtime
WORKDIR /app
RUN adduser -D appuser
COPY --from=build /app/server .
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:8080/health || exit 1
CMD ["./server"]
```

## Java / Kotlin (Spring Boot / Gradle)

```dockerfile
FROM gradle:8-jdk21 AS build
WORKDIR /app
COPY . .
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
COPY --from=build /app/build/libs/*.jar app.jar
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:8080/actuator/health || exit 1
CMD ["java", "-jar", "app.jar"]
```

## Ruby (Rails)

```dockerfile
FROM ruby:3.3-slim AS build
WORKDIR /app
RUN apt-get update -qq && apt-get install -y build-essential libpq-dev
COPY Gemfile Gemfile.lock ./
RUN bundle install
COPY . .

FROM ruby:3.3-slim AS runtime
WORKDIR /app
RUN apt-get update -qq && apt-get install -y libpq5 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build /app .
RUN useradd -m appuser
USER appuser
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost:3000/up || exit 1
CMD ["bin/rails", "server", "-b", "0.0.0.0"]
```

## Rust

```dockerfile
FROM rust:1.79 AS build
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim AS runtime
WORKDIR /app
RUN useradd -m appuser
COPY --from=build /app/target/release/app_binary ./app
USER appuser
EXPOSE 8080
CMD ["./app"]
```
Replace `app_binary` with the actual binary name from `Cargo.toml`'s `[[bin]]` or package name.

## PHP (Laravel)

```dockerfile
FROM composer:2 AS build
WORKDIR /app
COPY . .
RUN composer install --no-dev --optimize-autoloader

FROM php:8.3-fpm-alpine AS runtime
WORKDIR /var/www
COPY --from=build /app .
RUN adduser -D appuser && chown -R appuser /var/www
USER appuser
EXPOSE 9000
CMD ["php-fpm"]
```
Pair with an nginx service in docker-compose.yml to actually serve HTTP traffic to php-fpm.

## Static frontend (React/Vue/Vite build output, no Node runtime needed)

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
```

---

## .dockerignore baseline (adapt per stack)

```
.git
.gitignore
.env
.env.*
!.env.example
node_modules
__pycache__
*.pyc
venv
.venv
dist
build
target
*.log
.DS_Store
coverage
.vscode
.idea
README.md
DEPLOYMENT.md
tests/
test/
**/*.test.*
```
