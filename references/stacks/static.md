# Static sites and SPAs

Covers Vite (React/Vue/Svelte/Solid/Preact), Create React App, Angular, Astro (static output), SvelteKit `adapter-static`, Next.js `output: "export"`, Nuxt `generate`, Docusaurus, VitePress, Gatsby, Eleventy, Hugo, Jekyll, MkDocs, Flutter web, and plain HTML.

**Ask before containerizing a pure static site.** Vercel, Netlify, Cloudflare Pages, GitHub Pages, and S3+CloudFront serve static output better and cheaper than a container. Generate the container when the user wants one artifact for every environment, is self-hosting, or the SPA ships beside a backend in compose.

## Output directory by framework (`detect.py` → `static_out`)

| Framework | Build | Output |
|---|---|---|
| Vite / Vue CLI / Parcel / Astro / VitePress | `npm run build` | `dist` (`.vitepress/dist`) |
| Create React App / Docusaurus | `npm run build` | `build` |
| Angular 17+ | `ng build` | `dist/<project>/browser` (read `angular.json`) |
| Next.js export | `next build` with `output: "export"` | `out` |
| Nuxt generate | `nuxi generate` | `.output/public` |
| SvelteKit static | `vite build` | `build` |
| Gatsby | `gatsby build` | `public` |
| Eleventy | `npx @11ty/eleventy` | `_site` |
| Hugo | `hugo --minify` | `public` |
| Jekyll | `bundle exec jekyll build` | `_site` |
| MkDocs | `mkdocs build` | `site` |
| Flutter web | `flutter build web --release` | `build/web` |

## Template (Node-built SPA → unprivileged nginx)

```dockerfile
# syntax=docker/dockerfile:1
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
# Vite inlines VITE_* at build time - public values only, never secrets.
ARG VITE_API_URL
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
```

`nginx-unprivileged` already runs as uid 101 on port 8080, so no USER line is needed (the audit knows this image is non-root). Its default CMD runs nginx in the foreground.

## nginx.conf (write next to the Dockerfile)

```nginx
server {
  listen 8080;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;
  server_tokens off;

  gzip on;
  gzip_types text/plain text/css application/javascript application/json image/svg+xml;
  gzip_min_length 1024;

  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "SAMEORIGIN" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;

  location = /healthz { access_log off; add_header Content-Type text/plain; return 200 "ok"; }

  # Fingerprinted assets: cache forever
  location ~* \.(?:js|css|woff2?|png|jpg|jpeg|gif|svg|ico|webp|avif)$ {
    expires 1y;
    add_header Cache-Control "public, max-age=31536000, immutable";
    try_files $uri =404;
  }

  # SPA fallback (remove for multi-page static sites so real 404s stay 404s)
  location / {
    add_header Cache-Control "no-cache";
    try_files $uri $uri/ /index.html;
  }
}
```

A `location` that sets `add_header` replaces the server-level headers, so repeat the security headers inside locations if the user adds more.

## Runtime config without rebuilds

Build-time env (`VITE_*`, `REACT_APP_*`, `NG_APP_*`) gets frozen into the bundle. To run one image across environments, have the app fetch `/config.json` at startup, and generate that file at container start from env using nginx's built-in templates: put `config.json.template` in `/etc/nginx/templates/`, and the official entrypoint runs `envsubst` on `*.template` into `/etc/nginx/conf.d/`. Tell the user this changes app code and offer it, don't impose it.

## Non-Node static generators

```dockerfile
# Hugo
FROM alpine:3.24 AS build
RUN apk add --no-cache hugo git
WORKDIR /src
COPY . .
RUN hugo --minify --gc

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY --from=build --chown=101:101 /src/public /usr/share/nginx/html
```

- **Hugo extended / exact version:** download the release tarball with a checksum instead of using apk.
- **Jekyll:** `FROM ruby:3.4-slim AS build` → `bundle install` → `JEKYLL_ENV=production bundle exec jekyll build` → copy `_site`.
- **MkDocs:** `FROM python:3.14-slim AS build` → `pip install -r requirements.txt` (or `mkdocs-material`) → `mkdocs build` → copy `site`.
- **Flutter web:** `FROM ghcr.io/cirruslabs/flutter:stable AS build` → `flutter build web --release` → copy `build/web`.
- **Plain HTML:** a single stage, `FROM nginxinc/nginx-unprivileged:1.29-alpine` + `COPY --chown=101:101 . /usr/share/nginx/html`, with a `.dockerignore` that excludes everything except the site files.

## Caddy alternative (automatic HTTPS for single-VPS static hosting)

```dockerfile
FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
```

```caddyfile
{$SITE_ADDRESS::8080} {
  root * /srv
  encode zstd gzip
  try_files {path} /index.html
  file_server
  header /assets/* Cache-Control "public, max-age=31536000, immutable"
  respond /healthz 200
}
```
