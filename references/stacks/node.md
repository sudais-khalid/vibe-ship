# Node.js / Bun / Deno

Covers plain servers (Express, Fastify, Koa, Hono, Hapi, NestJS, AdonisJS, Strapi, Medusa...) and SSR frameworks (Next.js, Nuxt, SvelteKit, Remix / React Router 7, Astro SSR, SolidStart, TanStack Start). For SPA/static builds (Vite, CRA, Angular, Astro static, Docusaurus, Gatsby) see `static.md`.

## Decision table

| Detected | Runtime CMD | Build output to copy | Port | Needs prod `node_modules`? |
|---|---|---|---|---|
| Express/Fastify/Koa/Hono (JS) | `node server.js` (from `main`/`start`) | source | 3000 | yes |
| Same, TypeScript | `node dist/<entry>.js` | `dist/` | 3000 | yes |
| NestJS | `node dist/main` | `dist/` | 3000 | yes |
| Next.js with `output: "standalone"` | `node server.js` | `.next/standalone`, `.next/static`, `public` | 3000 | **no** (bundled) |
| Next.js without standalone | **add** `output: "standalone"` to `next.config.*` (tell the user), else `next start` | `.next`, `public`, `node_modules` | 3000 | yes |
| Nuxt 3/4 | `node .output/server/index.mjs` | `.output/` | 3000 | no |
| SvelteKit + `adapter-node` | `node build` | `build/`, `package.json` | 3000 | yes |
| SvelteKit other adapters | switch to `@sveltejs/adapter-node` (ask) | | | |
| Remix / React Router 7 | `react-router-serve ./build/server/index.js` (or `remix-serve`) | `build/` | 3000 | yes |
| Astro + `@astrojs/node` (standalone) | `node ./dist/server/entry.mjs` | `dist/` | 4321 | yes |
| SolidStart / TanStack Start / Analog (Nitro) | `node .output/server/index.mjs` | `.output/` | 3000 | no |

Never use `npm start` / `yarn start` as the CMD. Package managers don't forward SIGTERM, which makes shutdown slow and lossy (audit `VS-D082`). Resolve what the start script runs and call `node` directly.

## Package-manager specifics

| PM | Copy before install | Install (deps stage) | Prune for runtime |
|---|---|---|---|
| npm | `package.json package-lock.json` | `npm ci` | `npm prune --omit=dev` |
| pnpm | `package.json pnpm-lock.yaml` (+ `pnpm-workspace.yaml`, `.npmrc`) | `corepack enable && pnpm install --frozen-lockfile` | `pnpm prune --prod` |
| yarn berry (`.yarnrc.yml`) | `package.json yarn.lock .yarnrc.yml .yarn/releases` | `corepack enable && yarn install --immutable` | `yarn workspaces focus --production` |
| yarn classic | `package.json yarn.lock` | `yarn install --frozen-lockfile` | `yarn install --production --frozen-lockfile` |
| bun | `package.json bun.lock` (or `bun.lockb`) | `bun install --frozen-lockfile` | `bun install --production --frozen-lockfile` |

Node 25+ images no longer ship corepack. On those, add `RUN npm install -g corepack@0.34` (pinned) before `corepack enable`. If `package.json` has a `packageManager` field, corepack uses that exact version, which is the reproducible path.

No lockfile? Stop and tell the user to generate and commit one. Building without a lockfile isn't reproducible.

## Template: generic Node server (TypeScript or JS)

```dockerfile
# syntax=docker/dockerfile:1
ARG NODE_VERSION=24
FROM node:${NODE_VERSION}-alpine AS base
WORKDIR /app

# ---- deps: full install (incl. dev) for building ----
FROM base AS deps
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# ---- build ----
FROM deps AS build
COPY . .
# mkdir: apps with zero runtime deps have no node_modules, and COPY of a missing path fails
RUN npm run build --if-present && npm prune --omit=dev && mkdir -p node_modules

# ---- runtime ----
FROM base AS runtime
ENV NODE_ENV=production PORT=3000
RUN apk add --no-cache tini
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/package.json ./
COPY --from=build --chown=node:node /app/dist ./dist
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/healthz" >/dev/null || exit 1
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "dist/server.js"]
```

- `node` user (uid 1000) ships in the official image, so no `adduser` is needed.
- `tini` is PID 1. It forwards signals and reaps zombies, so the app gets SIGTERM even without its own handler.
- Plain JS with no build step: drop the `build` stage's `npm run build`, copy the source dirs instead of `dist/`, and set CMD to the real entry file.
- Alpine uses musl. If a native module fails (`sharp`, `canvas`, `bcrypt` prebuilt for glibc), switch both stages to `node:24-slim`, install `tini` with apt, and use a Node-native healthcheck (slim has no wget/curl): `CMD ["node", "-e", "fetch('http://127.0.0.1:'+process.env.PORT+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]`.

## Template: Next.js (standalone)

```dockerfile
# syntax=docker/dockerfile:1
ARG NODE_VERSION=24
FROM node:${NODE_VERSION}-alpine AS base
WORKDIR /app

FROM base AS deps
RUN apk add --no-cache libc6-compat
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    corepack enable && pnpm install --frozen-lockfile --store-dir /pnpm/store

FROM base AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Public (non-secret) build-time vars are baked into the bundle:
ARG NEXT_PUBLIC_API_URL
RUN corepack enable && pnpm build

FROM base AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN apk add --no-cache tini
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD wget -qO- http://127.0.0.1:3000/api/healthz >/dev/null || exit 1
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "server.js"]
```

- Needs `output: "standalone"` in `next.config.*`. If it's missing, add it and say so in the summary.
- If there is no `public/` directory, delete that COPY line, because COPY of a missing path fails the build.
- `NEXT_PUBLIC_*` values are compiled into client JS. Pass them as `--build-arg` and never put secrets there. Server-only env vars stay runtime env.
- Image optimization (`next/image`) uses `sharp`, which ships prebuilt musl binaries since Next 15. That's fine on alpine.
- ISR/cache: `.next/cache` is local per replica. With more than 1 replica, configure a shared cache handler (Redis) or accept per-instance caches. Note this in DEPLOYMENT.md.
- Health: add `app/api/healthz/route.ts` returning 200 only if the user agrees. Otherwise check `/`.

## Monorepos (Turborepo / Nx / pnpm workspaces)

- Build context is the **repo root**, so the shared lockfile and internal packages are visible: `docker build -f apps/web/Dockerfile .`
- Turborepo: prune first so each image only gets its own dependency graph:

```dockerfile
FROM base AS prune
RUN npm install -g turbo@2
COPY . .
RUN turbo prune web --docker          # -> out/json (manifests) + out/full (source)

FROM base AS deps
COPY --from=prune /app/out/json/ ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM deps AS build
COPY --from=prune /app/out/full/ ./
RUN corepack enable && pnpm turbo run build --filter=web
```

- Next.js standalone in a monorepo nests the server: copy `.next/standalone` and run `node apps/web/server.js`. Copy static into `apps/web/.next/static`.
- pnpm without turbo: `pnpm --filter <app> deploy --prod /out` produces a self-contained folder to copy into the runtime stage.
- In compose, one service per app, each with `build: { context: ., dockerfile: apps/<app>/Dockerfile }`.

## ORMs and migrations

- **Prisma:** `prisma generate` runs in the build stage (postinstall usually does it). Alpine needs `RUN apk add --no-cache openssl` in the runtime stage. Run migrations as a one-off (`npx prisma migrate deploy`), not on every container start. In compose, that's a `migrate` service with `restart: "no"` that the app `depends_on` with `condition: service_completed_successfully`. On K8s, it's a Job or Helm pre-upgrade hook. The Prisma CLI must be in the image that runs it, so either keep `prisma` in dependencies or use the `deps` stage as the migrate target (`target: deps`).
- **Drizzle / Knex / TypeORM / Sequelize:** same one-off pattern with their CLI (`drizzle-kit migrate`, `knex migrate:latest`, `typeorm migration:run`).

## Bun

```dockerfile
FROM oven/bun:1 AS build
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build --if-present

FROM oven/bun:1-slim AS runtime
WORKDIR /app
COPY --from=build --chown=bun:bun /app ./
USER bun
ENV NODE_ENV=production PORT=3000
EXPOSE 3000
HEALTHCHECK CMD ["bun", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["bun", "run", "src/index.ts"]
```

`bun build --compile` can produce a single binary, which is then distroless-friendly (`gcr.io/distroless/cc-debian13:nonroot`).

## Deno

```dockerfile
FROM denoland/deno:alpine AS runtime
WORKDIR /app
COPY deno.json deno.lock ./
RUN deno install --frozen
COPY . .
RUN deno cache main.ts
USER deno
EXPOSE 8000
CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read", "main.ts"]
```

Grant only the `--allow-*` permissions the app needs. Never use `-A` in production. Pin a version tag such as `denoland/deno:alpine-2.x.y`.

## .dockerignore additions

```
node_modules
.next
.nuxt
.output
.svelte-kit
dist
build
coverage
.turbo
*.log
.env*
!.env.example
```
