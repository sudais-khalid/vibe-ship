# PHP: Laravel, Symfony, Slim, CakePHP, CodeIgniter, Laminas, Drupal, WordPress

Two production shapes. Pick one and say which in DEPLOYMENT.md:

| Shape | When | Containers |
|---|---|---|
| **FrankenPHP** (default) | New setups, single-service deploys, PaaS (Fly/Render/Railway/Cloud Run need one container that speaks HTTP) | 1 |
| **nginx + php-fpm** | Existing nginx tuning, shared hosting habits, the team asks for it | 2 (or 1 with supervisord, not recommended) |

## Template: FrankenPHP (Laravel shown; Symfony identical except paths)

```dockerfile
# syntax=docker/dockerfile:1
# ---- composer deps (no dev, no scripts: artisan isn't runnable yet) ----
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN --mount=type=cache,target=/tmp/cache composer install --no-dev --no-scripts --no-autoloader --prefer-dist \
    --no-interaction --ignore-platform-reqs
COPY . .
RUN composer dump-autoload --optimize --classmap-authoritative --no-dev

# ---- frontend assets (only if package.json + vite/mix exists) ----
FROM node:24-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- runtime ----
FROM dunglas/frankenphp:1-php8.4 AS runtime
RUN install-php-extensions pdo_pgsql redis intl zip opcache pcntl \
 && cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini" \
 && useradd -r -u 10001 app \
 && chown -R app:app /data/caddy /config/caddy
WORKDIR /app
COPY --from=vendor --chown=app:app /app /app
COPY --from=assets --chown=app:app /app/public/build /app/public/build
ENV SERVER_NAME=:8080 APP_ENV=production
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD curl -fsS http://127.0.0.1:8080/up || exit 1
CMD ["frankenphp", "run", "--config", "/etc/frankenphp/Caddyfile"]
```

- `install-php-extensions` is bundled in the image. Install what `composer.json` `ext-*` requires (`detect.py` lists `php_extensions`) plus the DB driver: `pdo_pgsql` (Postgres) or `pdo_mysql` (MySQL/MariaDB).
- `SERVER_NAME=:8080` serves plain HTTP on 8080 as non-root, and TLS is terminated by the platform or reverse proxy. For FrankenPHP to do HTTPS itself, set `SERVER_NAME=example.com`, run as root or with `setcap cap_net_bind_service`, and mount `/data` for certificates.
- Laravel `/up` is the built-in health route (Laravel 11+). Symfony: add a route, or check `/`.
- **Laravel Octane** (`detect.py` → `octane: true`): `CMD ["php", "artisan", "octane:frankenphp", "--host=0.0.0.0", "--port=8080"]` for worker mode with large throughput gains.
- Delete the `assets` stage if there's no frontend build.

## Template: nginx + php-fpm

```dockerfile
FROM php:8.4-fpm-alpine AS runtime
RUN apk add --no-cache fcgi \
 && docker-php-ext-install pdo_mysql opcache \
 && cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini"
WORKDIR /var/www/html
COPY --from=vendor --chown=www-data:www-data /app /var/www/html
USER www-data
EXPOSE 9000
HEALTHCHECK CMD SCRIPT_NAME=/ping SCRIPT_FILENAME=/ping REQUEST_METHOD=GET cgi-fcgi -bind -connect 127.0.0.1:9000 || exit 1
CMD ["php-fpm"]
```

Enable the FPM ping endpoint (`ping.path = /ping` in the pool config) for that healthcheck. The compose `web` service runs `nginx:1.29-alpine` with this server block mounted read-only:

```nginx
server {
  listen 8080;
  root /var/www/html/public;
  index index.php;
  location / { try_files $uri $uri/ /index.php?$query_string; }
  location ~ \.php$ {
    fastcgi_pass app:9000;
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    include fastcgi_params;
  }
  location ~ /\.(?!well-known) { deny all; }
}
```

nginx needs the static files too: build a second image `FROM nginx:1.29-alpine` with `COPY --from=vendor /app/public /var/www/html/public`, or share a volume.

## Laravel runtime checklist

- **Writable dirs:** `storage/` and `bootstrap/cache/` must be owned by the app user. The `--chown` above handles it.
- **Caches:** `php artisan config:cache && php artisan route:cache && php artisan view:cache && php artisan event:cache` need real env, so run them at container start (a small entrypoint script ending in `exec "$@"`), not at build.
- **Migrations:** one-off `php artisan migrate --force` (compose `migrate` service or K8s Job).
- **Queue worker:** same image, `command: ["php", "artisan", "queue:work", "--tries=3", "--max-time=3600"]`. Use Horizon if installed (`php artisan horizon`).
- **Scheduler:** one replica of `["php", "artisan", "schedule:work"]`.
- `APP_KEY` is a runtime secret.
- `LOG_CHANNEL=stderr` for container logging.

## Symfony

`APP_ENV=prod`, `composer dump-env prod` at build (no secrets in `.env`), `php bin/console cache:warmup` at build, and migrations with `doctrine:migrations:migrate --no-interaction` as a one-off.

## WordPress / Drupal

Don't build a custom image unless there's custom code. Use the official `wordpress:php8.4-apache` / `drupal:11-apache` images with a named volume for `wp-content` / `sites`, plus `mariadb:11.8`. Apache's master process runs as root and drops to `www-data` for workers. That's expected, so suppress `VS-D011` with the reason when auditing.
