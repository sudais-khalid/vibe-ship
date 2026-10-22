# Python

Covers FastAPI, Starlette, Litestar, Django, Flask, Quart, Sanic, aiohttp, Falcon, Streamlit, Gradio, Dash, Chainlit, plus Celery, RQ, Dramatiq, and arq workers. Package managers: uv, Poetry, PDM, Pipenv, pip.

## Rules that prevent the classic breakages

1. **Install into a virtualenv at a fixed path and copy that path.** Never use `pip install --user` into `/root/.local`, because the non-root runtime user can't read `/root` and the app dies with `ModuleNotFoundError`.
2. **Same base image tag in build and runtime stages,** so the venv's interpreter path and ABI match.
3. **`-slim` images have no curl or wget.** Use the Python healthcheck below (audit `VS-D020` catches this).
4. `ENV PYTHONUNBUFFERED=1` so logs appear immediately, and `PYTHONDONTWRITEBYTECODE=1`.
5. Production servers only: gunicorn, uvicorn, granian, or hypercorn. `manage.py runserver`, `flask run`, and `--reload` are dev servers (audit `VS-D085`).
6. Packages that need compilers (psycopg2 non-binary, mysqlclient, lxml without wheels): install `build-essential` and `*-dev` headers in the **build** stage only, and the runtime `.so` libraries (`libpq5`, `default-libmysqlclient-dev` → `libmariadb3`) in the runtime stage.

## Server command by framework

| Framework | CMD (exec form) | Port |
|---|---|---|
| FastAPI / Starlette / Litestar | `["uvicorn", "<module>:<app>", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]` | 8000 |
| Same, multi-core on one host | `["gunicorn", "<module>:<app>", "-k", "uvicorn_worker.UvicornWorker", "-b", "0.0.0.0:8000"]` (add `uvicorn-worker`) | 8000 |
| Django (WSGI) | `["gunicorn", "<project>.wsgi:application", "-b", "0.0.0.0:8000", "--access-logfile", "-"]` | 8000 |
| Django (ASGI / channels) | `["uvicorn", "<project>.asgi:application", "--host", "0.0.0.0", "--port", "8000"]` | 8000 |
| Flask / Falcon (WSGI) | `["gunicorn", "<module>:app", "-b", "0.0.0.0:8000"]`. App factory: `"<module>:create_app()"` | 8000 |
| Quart | `["hypercorn", "<module>:app", "--bind", "0.0.0.0:8000"]` | 8000 |
| Streamlit | `["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]` | 8501 |
| Gradio | `["python", "app.py"]` with `GRADIO_SERVER_NAME=0.0.0.0` | 7860 |
| Celery worker | `["celery", "-A", "<app>", "worker", "--loglevel=INFO", "--concurrency=2"]` | none |
| Celery beat | `["celery", "-A", "<app>", "beat", "--loglevel=INFO"]` (exactly **one** replica) | none |
| RQ / Dramatiq / arq | `["rq", "worker", "--url", "$REDIS_URL"]` → use shell-form via `sh -c "exec ..."` if env expansion is needed | none |

`detect.py` reports the `entrypoint` (`app.main:app`, `mysite.wsgi:application`). Use it verbatim.

Gunicorn reads `WEB_CONCURRENCY` for the worker count, so set it in env instead of hardcoding. Rule of thumb: `2 × cores + 1` for sync workers on a VM. In K8s, run 1–2 workers per container and scale with replicas.

## Template: uv (preferred when `uv.lock` exists)

```dockerfile
# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:${PYTHON_VERSION}-slim AS runtime
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

## Template: pip + requirements.txt

```dockerfile
# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim AS build
# Only if a dependency compiles C code:
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

FROM python:${PYTHON_VERSION}-slim AS runtime
# Runtime libs matching the build headers, e.g. libpq5:
# RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app . .
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:8000", "--access-logfile", "-"]
```

`requirements.txt` without pinned versions (`fastapi` instead of `fastapi==0.120.1`) isn't reproducible. Suggest `pip freeze` or `uv pip compile`, and note it in DEPLOYMENT.md.

## Poetry / PDM / Pipenv

- **Poetry:** in the build stage run `pip install poetry==2.2.1` (pin it), then `POETRY_VIRTUALENVS_IN_PROJECT=1 poetry install --only main --no-root --no-interaction`, copy the source, `poetry install --only main`. Copy `/app/.venv` exactly like the uv template.
- **PDM:** `pip install pdm && pdm install --prod --frozen-lockfile --no-editable`, which also produces `.venv`.
- **Pipenv:** `pip install pipenv && PIPENV_VENV_IN_PROJECT=1 pipenv install --deploy`.

## Django specifics

- `collectstatic` at build time needs settings importable without real secrets: `RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput` (adapt the env var name the settings read). Serve static with WhiteNoise (suggest it) or from the reverse proxy.
- Migrations run as a one-off (`python manage.py migrate --noinput`), never inside the web CMD with multiple replicas, because concurrent migrations race. In compose, that's a `migrate` service plus `service_completed_successfully`.
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `SECURE_PROXY_SSL_HEADER` must be env-driven. Flag hardcoded values in DEPLOYMENT.md.

## Workers

Use the same image with a different `command:`. For Celery, add a worker service, and a beat service only if the code uses periodic tasks. Worker health in compose is `celery -A app inspect ping -d celery@$$HOSTNAME`, which is slow, so use `interval: 60s`.

## .dockerignore additions

```
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
*.egg-info/
staticfiles/
media/
```
