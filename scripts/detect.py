#!/usr/bin/env python3
"""
vibe-ship stack detector

Deterministically inspects a project and reports what it is, so the
generated deployment files are based on evidence instead of guesses:

  - every deployable service (root, monorepo packages, frontend/backend dirs)
  - language, framework, package manager + lockfile, declared runtime version
  - port, build/start commands, entrypoint, health route
  - backing services the code depends on (Postgres, Redis, Kafka, ...)
  - environment variables the code reads
  - deployment files that already exist (so nothing gets clobbered)

Standard library only. Python 3.8+. Never executes project code.

Usage:
    python3 detect.py --path /path/to/project            # human summary
    python3 detect.py --path /path/to/project --json     # machine-readable
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover - depends on interpreter
    try:
        import tomli as _toml
    except ImportError:
        _toml = None

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "target", "out", ".next", ".nuxt", ".output", ".svelte-kit",
    ".turbo", ".cache", "coverage", ".terraform", ".idea", ".vscode", "bin", "obj",
    "tmp", "log", "logs", ".gradle", ".dart_tool", "_build", "deps", ".elixir_ls",
    "examples", "example", "fixtures", "testdata", "test", "tests", "__tests__",
    "e2e", "cypress", "playwright-report", ".github", ".devcontainer", "Pods",
}
MAX_DEPTH = 3
MAX_SOURCE_FILES = 4000
MAX_FILE_BYTES = 512 * 1024

SOURCE_EXTS = {
    ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".vue", ".svelte",
    ".astro", ".py", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".cs", ".fs",
    ".rb", ".php", ".ex", ".exs", ".swift", ".dart", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".clj", ".gleam", ".hs", ".zig", ".cr", ".properties", ".yml", ".yaml",
}

# --------------------------------------------------------------------------
# Backing services: dependency name -> service. Matched against exact
# dependency names (not substrings) per ecosystem, to avoid false positives.
# --------------------------------------------------------------------------
SERVICE_DEPS = {
    "postgres": {
        "pg", "postgres", "pg-promise", "@neondatabase/serverless", "@vercel/postgres",
        "psycopg", "psycopg2", "psycopg2-binary", "psycopg-binary", "asyncpg",
        "github.com/lib/pq", "github.com/jackc/pgx", "github.com/jackc/pgx/v4",
        "github.com/jackc/pgx/v5", "gorm.io/driver/postgres", "tokio-postgres",
        "postgres-types", "npgsql", "npgsql.entityframeworkcore.postgresql",
        "org.postgresql:postgresql", "postgresql", "pg_search", "postgrex",
        "doctrine/dbal-postgres",
    },
    "mysql": {
        "mysql", "mysql2", "pymysql", "mysqlclient", "aiomysql", "mysql-connector-python",
        "github.com/go-sql-driver/mysql", "gorm.io/driver/mysql", "mysql_async",
        "mysql.data", "pomelo.entityframeworkcore.mysql", "mysqlconnector",
        "com.mysql:mysql-connector-j", "mysql:mysql-connector-java", "myxql",
        "org.mariadb.jdbc:mariadb-java-client", "mariadb",
    },
    "mongodb": {
        "mongoose", "mongodb", "pymongo", "motor", "beanie", "mongoengine",
        "go.mongodb.org/mongo-driver", "go.mongodb.org/mongo-driver/v2", "mongodb.driver",
        "mongoid", "mongo", "org.springframework.boot:spring-boot-starter-data-mongodb",
        "mongodb/mongodb",
    },
    "redis": {
        "redis", "ioredis", "bullmq", "bull", "bee-queue", "@upstash/redis", "connect-redis",
        "rq", "django-redis", "celery-redis", "aioredis", "redis-py-cluster", "hiredis",
        "github.com/redis/go-redis/v9", "github.com/go-redis/redis/v8", "github.com/gomodule/redigo",
        "sidekiq", "resque", "redis-rails", "predis/predis", "stackexchange.redis",
        "org.springframework.boot:spring-boot-starter-data-redis", "io.lettuce:lettuce-core",
        "redis.clients:jedis", "redix", "fred", "deadpool-redis", "valkey", "valkey-glide",
    },
    "rabbitmq": {
        "amqplib", "amqp-connection-manager", "pika", "aio-pika", "kombu",
        "github.com/rabbitmq/amqp091-go", "github.com/streadway/amqp", "bunny", "sneakers",
        "lapin", "rabbitmq.client", "org.springframework.boot:spring-boot-starter-amqp",
        "php-amqplib/php-amqplib", "amqp",
    },
    "kafka": {
        "kafkajs", "@confluentinc/kafka-javascript", "confluent-kafka", "kafka-python",
        "aiokafka", "github.com/segmentio/kafka-go", "github.com/ibm/sarama",
        "github.com/shopify/sarama", "github.com/confluentinc/confluent-kafka-go/v2",
        "rdkafka", "confluent.kafka", "org.springframework.kafka:spring-kafka",
        "org.apache.kafka:kafka-clients", "ruby-kafka", "karafka", "brod",
    },
    "elasticsearch": {
        "@elastic/elasticsearch", "elasticsearch", "elasticsearch-dsl",
        "github.com/elastic/go-elasticsearch/v8", "nest", "elastic.clients.elasticsearch",
        "elasticsearch-model", "searchkick",
        "org.springframework.boot:spring-boot-starter-data-elasticsearch",
    },
    "opensearch": {"@opensearch-project/opensearch", "opensearch-py", "opensearch-ruby"},
    "memcached": {"memjs", "memcached", "pymemcache", "python-memcached", "pylibmc", "dalli"},
    "nats": {"nats", "nats-py", "github.com/nats-io/nats.go", "async-nats", "gnat"},
    "clickhouse": {
        "@clickhouse/client", "clickhouse-driver", "clickhouse-connect",
        "github.com/clickhouse/clickhouse-go/v2", "clickhouse",
    },
    "meilisearch": {"meilisearch", "meilisearch-python-sdk", "github.com/meilisearch/meilisearch-go"},
    "minio": {"minio", "github.com/minio/minio-go/v7"},
    "mailpit": {"nodemailer", "django-anymail", "flask-mail", "fastapi-mail", "actionmailer", "swiftmailer/swiftmailer", "symfony/mailer"},
}
SQLITE_DEPS = {"sqlite3", "better-sqlite3", "@libsql/client", "github.com/mattn/go-sqlite3",
               "modernc.org/sqlite", "rusqlite", "aiosqlite", "microsoft.entityframeworkcore.sqlite",
               "org.xerial:sqlite-jdbc", "exqlite", "ecto_sqlite3"}
WORKER_DEPS = {"celery", "dramatiq", "rq", "arq", "huey", "sidekiq", "good_job", "solid_queue",
               "resque", "bullmq", "bull", "bee-queue", "agenda", "graphile-worker", "oban",
               "github.com/hibiken/asynq", "laravel/horizon"}

ENV_SERVICE_HINTS = [  # env var names that imply a backing service
    (r"\bREDIS_(URL|HOST|URI)\b|\bVALKEY_URL\b", "redis"),
    (r"\bMONGO(DB)?_(URI|URL|HOST)\b", "mongodb"),
    (r"\b(RABBITMQ|AMQP|CLOUDAMQP)_URL\b", "rabbitmq"),
    (r"\bKAFKA_(BROKERS|BOOTSTRAP_SERVERS)\b", "kafka"),
    (r"\b(ELASTICSEARCH|ELASTIC)_(URL|HOST|NODE)\b", "elasticsearch"),
    (r"\bMEILI(SEARCH)?_(HOST|URL)\b", "meilisearch"),
]

ENV_PATTERNS = [
    r"process\.env\.([A-Z][A-Z0-9_]{1,63})",
    r"process\.env\[\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]\s*\]",
    r"import\.meta\.env\.([A-Z][A-Z0-9_]{1,63})",
    r"Deno\.env\.get\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"Bun\.env\.([A-Z][A-Z0-9_]{1,63})",
    r"os\.environ\[\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"os\.environ\.get\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"os\.Getenv\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"os\.LookupEnv\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"env::var\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"System\.getenv\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"System\.get_env\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"Environment\.GetEnvironmentVariable\(\s*\"([A-Z][A-Z0-9_]{1,63})\"",
    r"ENV\[\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]\s*\]",
    r"ENV\.fetch\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"\benv\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"getenv\(\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]",
    r"\$\{([A-Z][A-Z0-9_]{1,63})(?::[^}]*)?\}",  # Spring/compose style placeholders
]
ENV_IGNORE = {"NODE_ENV", "PATH", "HOME", "PWD", "USER", "SHELL", "TERM", "HOSTNAME", "CI",
              "DEBUG", "TZ", "LANG", "TMPDIR", "VITE_USER_NODE_ENV", "DEV", "PROD", "MODE", "SSR",
              "BASE_URL", "GITHUB_TOKEN"}

HEALTH_RE = re.compile(r"""['"`](/(?:api/)?(?:healthz?|health-check|healthcheck|livez|readyz|ready|ping|status|up))['"`]""")
PORT_RES = [
    re.compile(r"PORT\s*(?:\|\||\?\?|or)\s*['\"]?(\d{4,5})"),
    re.compile(r"getenv\(\s*['\"]PORT['\"]\s*,\s*['\"]?(\d{4,5})"),
    re.compile(r"environ\.get\(\s*['\"]PORT['\"]\s*,\s*['\"]?(\d{4,5})"),
    re.compile(r"\.listen\(\s*(\d{4,5})"),
    re.compile(r"(?:--port|-p)[\s=]+(\d{4,5})"),
    re.compile(r"server\.port\s*[=:]\s*(\d{4,5})"),
    re.compile(r"\bport\s*[=:]\s*(\d{4,5})\b"),
    re.compile(r"ListenAndServe\(\s*\"[^\"]*:(\d{4,5})\""),
    re.compile(r"\.Run\(\s*\":(\d{4,5})\"\)"),
    re.compile(r"bind\(\s*\(?\s*\"[\d.]+\"\s*,\s*(\d{4,5})"),
    re.compile(r"0\.0\.0\.0:(\d{4,5})"),
]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def read(path, limit=MAX_FILE_BYTES):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except OSError:
        return ""


def exists(d, *names):
    return any(os.path.exists(os.path.join(d, n)) for n in names)


def first_existing(d, *names):
    for n in names:
        if os.path.exists(os.path.join(d, n)):
            return n
    return None


def load_toml(path):
    text = read(path)
    if not text:
        return {}
    if _toml is not None:
        try:
            return _toml.loads(text)
        except Exception:
            return {}
    return {}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def toml_section_keys(text, section):
    """Regex fallback: keys of a [section] table when no TOML parser exists."""
    m = re.search(r"^\[" + re.escape(section) + r"\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not m:
        return []
    return re.findall(r"^\s*([A-Za-z0-9_.\-]+)\s*=", m.group(1), re.M)


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def clean_version(spec):
    """'>=3.11,<4' -> '3.11', '^20.11' -> '20.11', 'v22' -> '22', '~> 3.3.0' -> '3.3.0'."""
    if not spec:
        return None
    m = re.search(r"(\d+(?:\.\d+){0,2})", str(spec))
    return m.group(1) if m else None


def norm_dep(name):
    return name.strip().lower().replace("_", "-") if name else name


# --------------------------------------------------------------------------
# Dependency extraction per ecosystem -> set of normalized names
# --------------------------------------------------------------------------
def node_deps(pkg):
    deps = set()
    for key in ("dependencies", "optionalDependencies", "peerDependencies"):
        deps.update(norm_dep(k) for k in (pkg.get(key) or {}))
    return deps


def node_dev_deps(pkg):
    return {norm_dep(k) for k in (pkg.get("devDependencies") or {})}


def python_deps(d):
    deps = set()
    for fn in os.listdir(d):
        if re.match(r"requirements.*\.(txt|in)$", fn):
            for line in read(os.path.join(d, fn)).splitlines():
                line = line.split("#")[0].strip()
                if not line or line.startswith("-"):
                    continue
                m = re.match(r"([A-Za-z0-9_.\-]+)", line)
                if m:
                    deps.add(norm_dep(m.group(1)))
    pp = os.path.join(d, "pyproject.toml")
    if os.path.exists(pp):
        data = load_toml(pp)
        text = read(pp)
        specs = list((data.get("project") or {}).get("dependencies") or [])
        for group in ((data.get("project") or {}).get("optional-dependencies") or {}).values():
            specs.extend(group)
        poetry = ((data.get("tool") or {}).get("poetry") or {})
        deps.update(norm_dep(k) for k in (poetry.get("dependencies") or {}) if k.lower() != "python")
        if not data:  # no TOML parser available
            specs.extend(re.findall(r"['\"]([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*[<>=!~;\"']", text))
            deps.update(norm_dep(k) for k in toml_section_keys(text, "tool.poetry.dependencies") if k != "python")
        for s in specs:
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", s)
            if m:
                deps.add(norm_dep(m.group(1)))
    pipfile = os.path.join(d, "Pipfile")
    if os.path.exists(pipfile):
        deps.update(norm_dep(k) for k in toml_section_keys(read(pipfile), "packages"))
    if exists(d, "setup.py"):
        for m in re.finditer(r"['\"]([A-Za-z0-9_.\-]+)\s*(?:[<>=~!].*?)?['\"]", read(os.path.join(d, "setup.py"))):
            deps.add(norm_dep(m.group(1)))
    return deps


def go_deps(text):
    deps = set()
    for m in re.finditer(r"^\s*(?:require\s+)?([a-zA-Z0-9.\-]+\.[a-z]{2,}/[^\s]+)\s+v[\d.]", text, re.M):
        deps.add(m.group(1).lower())
    return deps


def cargo_deps(path):
    data = load_toml(path)
    text = read(path)
    if data:
        names = set()
        for key in ("dependencies", "workspace"):
            section = data.get(key) or {}
            if key == "workspace":
                section = section.get("dependencies") or {}
            names.update(k.lower() for k in section)
        return names
    return {k.lower() for k in toml_section_keys(text, "dependencies")}


def gem_deps(text):
    return {m.group(1).lower() for m in re.finditer(r"^\s*gem\s+['\"]([A-Za-z0-9_.\-]+)['\"]", text, re.M)}


def composer_deps(data):
    return {k.lower() for k in (data.get("require") or {})}


def jvm_deps(d):
    deps, text = set(), ""
    for fn in ("pom.xml", "build.gradle", "build.gradle.kts", "build.sbt", "project.clj", "deps.edn"):
        if exists(d, fn):
            text += read(os.path.join(d, fn)) + "\n"
    for g, a in re.findall(r"<groupId>\s*([^<\s]+)\s*</groupId>\s*<artifactId>\s*([^<\s]+)\s*</artifactId>", text):
        deps.add((g + ":" + a).lower())
        deps.add(a.lower())
    for g, a in re.findall(r"['\"]([a-zA-Z0-9_.\-]+):([a-zA-Z0-9_.\-]+)(?::[^'\"]*)?['\"]", text):
        deps.add((g + ":" + a).lower())
        deps.add(a.lower())
    for g, a in re.findall(r"\"([a-zA-Z0-9_.\-]+)\"\s*%%?\s*\"([a-zA-Z0-9_.\-]+)\"", text):
        deps.add((g + ":" + a).lower())
        deps.add(a.lower())
    return deps, text


def dotnet_deps(d):
    deps, text = set(), ""
    for fn in os.listdir(d):
        if fn.endswith((".csproj", ".fsproj", ".vbproj")):
            text += read(os.path.join(d, fn))
    for m in re.finditer(r"<PackageReference\s+Include=\"([^\"]+)\"", text):
        deps.add(m.group(1).lower())
    return deps, text


def mix_deps(text):
    return {m.group(1).lower() for m in re.finditer(r"\{\s*:([a-z0-9_]+)\s*,", text)}


def services_from_deps(deps):
    found = set()
    for svc, names in SERVICE_DEPS.items():
        if deps & names:
            found.add(svc)
    return found


# --------------------------------------------------------------------------
# Source scanning (env vars, ports, health routes)
# --------------------------------------------------------------------------
def iter_source_files(d, max_files=MAX_SOURCE_FILES):
    count = 0
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SOURCE_EXTS:
                yield os.path.join(dirpath, fn)
                count += 1
                if count >= max_files:
                    return


def scan_sources(d, nested_roots=()):
    env, ports, health = set(), [], set()
    nested = [os.path.normcase(os.path.abspath(n)) for n in nested_roots]
    env_res = [re.compile(p) for p in ENV_PATTERNS]
    for path in iter_source_files(d):
        ap = os.path.normcase(os.path.abspath(path))
        if any(ap.startswith(n + os.sep) for n in nested):
            continue  # belongs to a nested service
        text = read(path)
        if not text:
            continue
        is_cfg = path.endswith((".yml", ".yaml", ".properties"))
        for r in env_res:
            if is_cfg and not r.pattern.startswith(r"\$\{"):
                continue
            if not is_cfg and r.pattern.startswith(r"\$\{"):
                continue
            env.update(r.findall(text))
        if not is_cfg or path.endswith(".properties") or "application" in os.path.basename(path):
            for r in PORT_RES:
                for p in r.findall(text):
                    if 1024 <= int(p) <= 65535:
                        ports.append(int(p))
        health.update(HEALTH_RE.findall(text))
    env = {e for e in env if e not in ENV_IGNORE}
    return env, ports, health


def env_example_vars(d):
    names = set()
    for fn in (".env.example", ".env.sample", ".env.template", ".env.dist", "example.env"):
        for line in read(os.path.join(d, fn)).splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
            if m:
                names.add(m.group(1))
    return names


def most_common(values):
    if not values:
        return None
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


# --------------------------------------------------------------------------
# Per-ecosystem detectors. Each returns a dict or None.
# --------------------------------------------------------------------------
NODE_FRAMEWORKS = [  # (dependency, framework, kind, default port)
    ("next", "nextjs", "web", 3000),
    ("nuxt", "nuxt", "web", 3000),
    ("@remix-run/node", "remix", "web", 3000),
    ("@react-router/node", "react-router", "web", 3000),
    ("@react-router/serve", "react-router", "web", 3000),
    ("@sveltejs/kit", "sveltekit", "web", 3000),
    ("@solidjs/start", "solidstart", "web", 3000),
    ("@tanstack/react-start", "tanstack-start", "web", 3000),
    ("@analogjs/platform", "analog", "web", 3000),
    ("astro", "astro", "static", 4321),
    ("@nestjs/core", "nestjs", "web", 3000),
    ("@adonisjs/core", "adonisjs", "web", 3333),
    ("@redwoodjs/core", "redwood", "web", 8910),
    ("@strapi/strapi", "strapi", "web", 1337),
    ("@medusajs/medusa", "medusa", "web", 9000),
    ("@keystone-6/core", "keystone", "web", 3000),
    ("@directus/api", "directus", "web", 8055),
    ("@feathersjs/feathers", "feathers", "web", 3030),
    ("sails", "sails", "web", 1337),
    ("hono", "hono", "web", 3000),
    ("elysia", "elysia", "web", 3000),
    ("fastify", "fastify", "web", 3000),
    ("@hapi/hapi", "hapi", "web", 3000),
    ("koa", "koa", "web", 3000),
    ("express", "express", "web", 3000),
    ("@angular/core", "angular", "static", 80),
    ("gatsby", "gatsby", "static", 80),
    ("@docusaurus/core", "docusaurus", "static", 80),
    ("vitepress", "vitepress", "static", 80),
    ("@11ty/eleventy", "eleventy", "static", 80),
    ("react-scripts", "create-react-app", "static", 80),
    ("@vue/cli-service", "vue-cli", "static", 80),
    ("vite", "vite", "static", 80),
    ("parcel", "parcel", "static", 80),
]
NODE_UNSUPPORTED = {"react-native": "mobile", "expo": "mobile", "electron": "desktop",
                    "@tauri-apps/api": "desktop", "@capacitor/core": "mobile"}
STATIC_OUT = {"vite": "dist", "astro": "dist", "create-react-app": "build", "vue-cli": "dist",
              "gatsby": "public", "docusaurus": "build", "vitepress": ".vitepress/dist",
              "eleventy": "_site", "parcel": "dist", "angular": "dist/<project>/browser"}


def detect_node(d, root):
    pkg_path = os.path.join(d, "package.json")
    deno = first_existing(d, "deno.json", "deno.jsonc")
    if not os.path.exists(pkg_path) and not deno:
        return None
    pkg = load_json(pkg_path) if os.path.exists(pkg_path) else {}
    deps, dev = node_deps(pkg), node_dev_deps(pkg)
    alld = deps | dev
    scripts = pkg.get("scripts") or {}
    info = {"language": "javascript", "runtime": "node"}
    if exists(d, "tsconfig.json") or "typescript" in alld:
        info["language"] = "typescript"

    # Package manager: lockfile wins, then packageManager field, then root lockfile (workspaces).
    pm_field = (pkg.get("packageManager") or "").split("@")[0] or None
    lock = None
    for search_dir in (d, root):
        for fn, pm in (("pnpm-lock.yaml", "pnpm"), ("bun.lock", "bun"), ("bun.lockb", "bun"),
                       ("yarn.lock", "yarn"), ("package-lock.json", "npm"), ("npm-shrinkwrap.json", "npm"),
                       ("deno.lock", "deno")):
            if os.path.exists(os.path.join(search_dir, fn)):
                lock = (fn if search_dir == d else os.path.relpath(os.path.join(search_dir, fn), d).replace("\\", "/"), pm)
                break
        if lock:
            break
    if lock:
        info["package_manager"], info["lockfile"] = lock[1], lock[0]
    else:
        info["package_manager"], info["lockfile"] = pm_field or ("deno" if deno and not pkg else "npm"), None
    if info["package_manager"] == "yarn":
        info["yarn_berry"] = exists(d, ".yarnrc.yml") or exists(root, ".yarnrc.yml")
    if info["package_manager"] == "bun" or exists(d, "bunfig.toml"):
        info["runtime"] = "bun"
    if deno and not pkg:
        info["runtime"], info["package_manager"] = "deno", "deno"

    # Runtime version
    ver, src = None, None
    for fn in (".nvmrc", ".node-version"):
        for search_dir in (d, root):
            v = read(os.path.join(search_dir, fn)).strip()
            if v:
                ver, src = clean_version(v) or v, fn
                break
        if ver:
            break
    if not ver:
        tv = read(os.path.join(d, ".tool-versions")) or read(os.path.join(root, ".tool-versions"))
        m = re.search(r"^nodejs\s+(\S+)", tv, re.M)
        if m:
            ver, src = clean_version(m.group(1)), ".tool-versions"
    if not ver and (pkg.get("engines") or {}).get("node"):
        ver, src = clean_version(pkg["engines"]["node"]), "package.json engines.node"
    if not ver and (pkg.get("volta") or {}).get("node"):
        ver, src = clean_version(pkg["volta"]["node"]), "package.json volta.node"
    info["runtime_version"], info["version_source"] = ver, src

    # Framework
    framework, kind, port = None, None, None
    for dep, fw, k, p in NODE_FRAMEWORKS:
        if dep in alld or (dep == "next" and exists(d, "next.config.js", "next.config.mjs", "next.config.ts")):
            framework, kind, port = fw, k, p
            break
    for dep, k in NODE_UNSUPPORTED.items():
        if dep in alld:
            kind, framework = k, framework or dep
    if framework == "astro":
        if any(x.startswith("@astrojs/node") for x in alld):
            kind, port = "web", 4321
    if framework == "sveltekit" and "@sveltejs/adapter-static" in alld:
        kind, port = "static", 80
    if framework == "nextjs":
        cfg = ""
        for fn in ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"):
            cfg += read(os.path.join(d, fn))
        info["next_standalone"] = "standalone" in cfg
        if re.search(r"output\s*:\s*['\"]export['\"]", cfg):
            kind, port = "static", 80
            info["static_out"] = "out"
    if framework == "nuxt" and re.search(r"nuxi?\s+generate", " ".join(scripts.values())):
        kind, port, info["static_out"] = "static", 80, ".output/public"
    if kind == "static" and "static_out" not in info:
        info["static_out"] = STATIC_OUT.get(framework, "dist")
    if not framework:
        if scripts.get("start") or deno or exists(d, "server.js", "app.js", "server.ts", "main.ts", "server.mjs"):
            kind, port = "web", 3000
        elif pkg.get("bin"):
            kind = "cli"
        elif pkg.get("workspaces") or exists(d, "pnpm-workspace.yaml"):
            kind = "workspace-root"
        else:
            kind = "library"

    info.update({
        "framework": framework, "kind": kind, "default_port": port,
        "build_cmd": ("%s run build" % info["package_manager"]) if "build" in scripts else None,
        "start_cmd": ("%s start" % info["package_manager"] if info["package_manager"] != "npm" else "npm start") if "start" in scripts else None,
        "scripts": {k: v for k, v in scripts.items() if k in ("build", "start", "dev", "test", "lint", "typecheck", "migrate", "db:migrate", "prisma:migrate")},
        "entrypoint": pkg.get("main"),
        "module_type": pkg.get("type", "commonjs"),
        "deps": deps | dev,
        "prod_deps": deps,
    })
    if "prisma" in alld or "@prisma/client" in alld:
        schema = read(os.path.join(d, "prisma", "schema.prisma"))
        m = re.search(r"provider\s*=\s*\"(postgresql|mysql|sqlite|mongodb|sqlserver|cockroachdb)\"", schema)
        info["orm"] = "prisma"
        if m:
            info["prisma_provider"] = m.group(1)
    if pkg.get("workspaces") or exists(d, "pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json"):
        info["monorepo_tool"] = ("turbo" if exists(d, "turbo.json") else "nx" if exists(d, "nx.json")
                                 else "lerna" if exists(d, "lerna.json") else info["package_manager"] + "-workspaces")
    return info


PY_FRAMEWORKS = [
    ("django", "django", "web", 8000), ("fastapi", "fastapi", "web", 8000),
    ("litestar", "litestar", "web", 8000), ("starlette", "starlette", "web", 8000),
    ("flask", "flask", "web", 8000), ("quart", "quart", "web", 8000),
    ("sanic", "sanic", "web", 8000), ("aiohttp", "aiohttp", "web", 8080),
    ("tornado", "tornado", "web", 8888), ("falcon", "falcon", "web", 8000),
    ("pyramid", "pyramid", "web", 6543), ("bottle", "bottle", "web", 8080),
    ("streamlit", "streamlit", "web", 8501), ("gradio", "gradio", "web", 7860),
    ("dash", "dash", "web", 8050), ("panel", "panel", "web", 5006),
    ("chainlit", "chainlit", "web", 8000), ("reflex", "reflex", "web", 3000),
    ("mkdocs", "mkdocs", "static", 80),
]


def detect_python(d, root):
    if not exists(d, "pyproject.toml", "setup.py", "Pipfile", "manage.py") and \
            not any(re.match(r"requirements.*\.txt$", f) for f in os.listdir(d)):
        return None
    deps = python_deps(d)
    pp = load_toml(os.path.join(d, "pyproject.toml"))
    pp_text = read(os.path.join(d, "pyproject.toml"))
    if not deps and not pp and not exists(d, "manage.py", "Pipfile"):
        return None
    info = {"language": "python", "runtime": "python"}
    for fn, pm in (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("pdm.lock", "pdm"),
                   ("Pipfile.lock", "pipenv"), ("requirements.txt", "pip")):
        if exists(d, fn):
            info["package_manager"], info["lockfile"] = pm, fn
            break
    else:
        tool = (pp.get("tool") or {})
        info["package_manager"] = "poetry" if "poetry" in tool or "[tool.poetry]" in pp_text else \
            "uv" if "uv" in tool else "pdm" if "pdm" in tool else "pip"
        info["lockfile"] = None
    reqs = sorted(f for f in os.listdir(d) if re.match(r"requirements.*\.txt$", f))
    info["requirements_files"] = reqs

    ver, src = None, None
    for search_dir in (d, root):
        v = read(os.path.join(search_dir, ".python-version")).strip()
        if v:
            ver, src = clean_version(v), ".python-version"
            break
    if not ver:
        rt = read(os.path.join(d, "runtime.txt"))
        if "python" in rt:
            ver, src = clean_version(rt), "runtime.txt"
    if not ver:
        rp = (pp.get("project") or {}).get("requires-python") or \
            (((pp.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}).get("python")
        if not rp:
            m = re.search(r"requires-python\s*=\s*['\"]([^'\"]+)", pp_text)
            rp = m.group(1) if m else None
        if rp:
            ver, src = clean_version(rp), "pyproject requires-python (minimum)"
    info["runtime_version"], info["version_source"] = ver, src

    framework, kind, port = None, None, None
    for dep, fw, k, p in PY_FRAMEWORKS:
        if dep in deps or (fw == "django" and exists(d, "manage.py")):
            framework, kind, port = fw, k, p
            break
    servers = [s for s in ("gunicorn", "uvicorn", "granian", "hypercorn", "daphne", "waitress") if s in deps]
    workers = sorted(deps & WORKER_DEPS)
    if not framework:
        kind = "worker" if workers else ("cli" if (pp.get("project") or {}).get("scripts") else "library")

    entry = None
    if framework == "django":
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            if "wsgi.py" in filenames and "settings.py" in filenames or \
                    ("wsgi.py" in filenames and os.path.isdir(os.path.join(dirpath, "settings"))):
                mod = os.path.relpath(dirpath, d).replace(os.sep, ".")
                entry = mod + ".wsgi:application"
                if "asgi.py" in filenames:
                    info["asgi"] = mod + ".asgi:application"
                break
    elif framework in ("fastapi", "flask", "starlette", "litestar", "quart", "sanic", "falcon"):
        ctor = {"fastapi": r"FastAPI\(", "flask": r"Flask\(", "starlette": r"Starlette\(",
                "litestar": r"Litestar\(", "quart": r"Quart\(", "sanic": r"Sanic\(", "falcon": r"falcon\.(?:asgi\.)?App\("}[framework]
        for path in iter_source_files(d, 800):
            if not path.endswith(".py"):
                continue
            m = re.search(r"^(\w+)\s*=\s*(?:\w+\.)?" + ctor, read(path), re.M)
            if m:
                mod = os.path.relpath(path, d)[:-3].replace(os.sep, ".")
                if mod.startswith("src."):
                    info["src_layout"] = True
                entry = "%s:%s" % (mod, m.group(1))
                break
        if not entry and framework == "flask":
            for path in iter_source_files(d, 800):
                if path.endswith(".py") and re.search(r"def\s+create_app\(", read(path)):
                    entry = os.path.relpath(path, d)[:-3].replace(os.sep, ".") + ":create_app()"
                    break
    elif framework in ("streamlit", "gradio", "dash", "chainlit"):
        for cand in ("app.py", "main.py", "streamlit_app.py", "Home.py"):
            if exists(d, cand):
                entry = cand
                break

    info.update({"framework": framework, "kind": kind, "default_port": port,
                 "app_servers": servers, "workers": workers, "entrypoint": entry, "deps": deps,
                 "prod_deps": deps})
    if "alembic" in deps or exists(d, "alembic.ini"):
        info["migrations"] = "alembic upgrade head"
    elif framework == "django":
        info["migrations"] = "python manage.py migrate --noinput"
    return info


def detect_go(d, root):
    if not exists(d, "go.mod"):
        return None
    text = read(os.path.join(d, "go.mod"))
    deps = go_deps(text)
    info = {"language": "go", "runtime": "go", "package_manager": "go", "lockfile": "go.sum" if exists(d, "go.sum") else None}
    m = re.search(r"^toolchain\s+go(\S+)", text, re.M) or re.search(r"^go\s+(\S+)", text, re.M)
    info["runtime_version"] = clean_version(m.group(1)) if m else None
    info["version_source"] = "go.mod" if m else None
    m = re.search(r"^module\s+(\S+)", text, re.M)
    info["module"] = m.group(1) if m else None
    fw = None
    for dep, name in (("github.com/gin-gonic/gin", "gin"), ("github.com/labstack/echo/v4", "echo"),
                      ("github.com/gofiber/fiber/v2", "fiber"), ("github.com/gofiber/fiber/v3", "fiber"),
                      ("github.com/go-chi/chi/v5", "chi"), ("github.com/gorilla/mux", "gorilla"),
                      ("github.com/beego/beego/v2", "beego"), ("github.com/gobuffalo/buffalo", "buffalo"),
                      ("github.com/danielgtaylor/huma/v2", "huma"), ("connectrpc.com/connect", "connect"),
                      ("google.golang.org/grpc", "grpc")):
        if dep in deps:
            fw = name
            break
    mains = []
    if exists(d, "main.go"):
        mains.append(".")
    cmd = os.path.join(d, "cmd")
    if os.path.isdir(cmd):
        for sub in sorted(os.listdir(cmd)):
            if os.path.exists(os.path.join(cmd, sub, "main.go")):
                mains.append("./cmd/" + sub)
    info.update({"framework": fw or "net/http", "kind": "web" if (fw or mains) else "library",
                 "default_port": 50051 if fw == "grpc" else 8080, "main_packages": mains,
                 "needs_cgo": bool(deps & {"github.com/mattn/go-sqlite3", "github.com/confluentinc/confluent-kafka-go/v2"}),
                 "deps": deps, "prod_deps": deps,
                 "entrypoint": mains[0] if mains else None})
    return info


def detect_rust(d, root):
    if not exists(d, "Cargo.toml"):
        return None
    path = os.path.join(d, "Cargo.toml")
    data, text = load_toml(path), read(path)
    deps = cargo_deps(path)
    info = {"language": "rust", "runtime": "rust", "package_manager": "cargo",
            "lockfile": "Cargo.lock" if exists(d, "Cargo.lock") else None}
    tc = read(os.path.join(d, "rust-toolchain.toml")) or read(os.path.join(d, "rust-toolchain"))
    m = re.search(r"channel\s*=\s*\"([^\"]+)\"", tc) or re.match(r"\s*(\d[\d.]*)", tc)
    rv = (data.get("package") or {}).get("rust-version")
    if not rv:
        m2 = re.search(r"^rust-version\s*=\s*\"([^\"]+)\"", text, re.M)
        rv = m2.group(1) if m2 else None
    info["runtime_version"] = clean_version(m.group(1)) if m else clean_version(rv)
    info["version_source"] = "rust-toolchain" if m else ("Cargo.toml rust-version" if rv else None)
    name = (data.get("package") or {}).get("name")
    if not name:
        m = re.search(r"^\[package\][^\[]*?^name\s*=\s*\"([^\"]+)\"", text, re.M | re.S)
        name = m.group(1) if m else None
    bins = [b.get("name") for b in (data.get("bin") or []) if isinstance(b, dict) and b.get("name")]
    if not bins and not data:
        bins = re.findall(r"^\[\[bin\]\][^\[]*?^name\s*=\s*\"([^\"]+)\"", text, re.M | re.S)
    fw = next((n for n in ("axum", "actix-web", "rocket", "warp", "poem", "salvo", "tide", "loco-rs", "tonic") if n in deps), None)
    is_ws = "[workspace]" in text
    has_main = exists(d, "src/main.rs") or bool(bins)
    info.update({"framework": fw, "kind": "web" if fw else ("cli" if has_main else ("workspace-root" if is_ws else "library")),
                 "default_port": 50051 if fw == "tonic" else (8000 if fw == "rocket" else 8080),
                 "binary": bins[0] if bins else name, "binaries": bins or ([name] if name else []),
                 "workspace": is_ws, "deps": deps, "prod_deps": deps,
                 "needs_openssl": bool(deps & {"openssl", "native-tls", "openssl-sys"})})
    return info


def detect_jvm(d, root):
    build = first_existing(d, "pom.xml", "build.gradle.kts", "build.gradle", "build.sbt", "project.clj", "deps.edn")
    if not build:
        return None
    deps, text = jvm_deps(d)
    if build == "pom.xml":
        tool = "maven"
    elif build.startswith("build.gradle"):
        tool = "gradle"
    elif build == "build.sbt":
        tool = "sbt"
    else:
        tool = "leiningen" if build == "project.clj" else "clojure-cli"
    lang = "kotlin" if (build.endswith(".kts") and "kotlin(" in text) or "kotlin-stdlib" in text or \
        "org.jetbrains.kotlin" in text else ("scala" if tool == "sbt" else ("clojure" if tool in ("leiningen", "clojure-cli") else "java"))
    info = {"language": lang, "runtime": "jvm", "package_manager": tool, "build_file": build,
            "wrapper": exists(d, "mvnw") if tool == "maven" else exists(d, "gradlew") if tool == "gradle" else False,
            "lockfile": None}
    ver = None
    for pat in (r"<java\.version>\s*(\d+)", r"<maven\.compiler\.release>\s*(\d+)", r"<release>\s*(\d+)\s*</release>",
                r"languageVersion(?:\.set)?\s*[=(]\s*JavaLanguageVersion\.of\(\s*(\d+)", r"jvmToolchain\(\s*(\d+)",
                r"sourceCompatibility\s*=\s*(?:JavaVersion\.VERSION_)?['\"]?(\d+)", r"<maven\.compiler\.source>\s*(\d+)"):
        m = re.search(pat, text)
        if m:
            ver = m.group(1)
            break
    for fn in (".java-version", ".sdkmanrc"):
        v = read(os.path.join(d, fn))
        if v and not ver:
            ver = clean_version(v.split("=")[-1])
    info["runtime_version"], info["version_source"] = (ver, build) if ver else (None, None)
    fw, port = None, 8080
    if "org.springframework.boot" in text or "spring-boot-starter-web" in deps or "spring-boot-starter-webflux" in deps:
        fw = "spring-boot"
    elif "io.quarkus" in text:
        fw = "quarkus"
    elif "io.micronaut" in text:
        fw = "micronaut"
    elif "io.ktor" in text:
        fw = "ktor"
    elif "io.javalin" in text:
        fw = "javalin"
    elif "io.vertx" in text:
        fw = "vertx"
    elif "io.dropwizard" in text:
        fw = "dropwizard"
    elif "com.typesafe.play" in text or "org.playframework" in text or "PlayScala" in text:
        fw, port = "play", 9000
    elif "io.helidon" in text:
        fw = "helidon"
    elif tool == "leiningen" and "ring" in text:
        fw, port = "ring", 3000
    info.update({"framework": fw, "kind": "web" if fw else "library", "default_port": port,
                 "actuator": "spring-boot-starter-actuator" in deps, "deps": deps, "prod_deps": deps,
                 "native_image": "org.graalvm.buildtools" in text or "quarkus.native" in text})
    for prop in ("src/main/resources/application.properties", "src/main/resources/application.yml",
                 "src/main/resources/application.yaml"):
        m = re.search(r"server\.port\s*[=:]\s*\$?\{?(?:PORT:)?(\d{4,5})", read(os.path.join(d, prop)))
        if m:
            info["config_port"] = int(m.group(1))
    return info


def detect_dotnet(d, root):
    projs = [f for f in os.listdir(d) if f.endswith((".csproj", ".fsproj", ".vbproj"))]
    sln = [f for f in os.listdir(d) if f.endswith((".sln", ".slnx"))]
    if not projs and not sln:
        return None
    if not projs:
        return {"language": "csharp", "runtime": "dotnet", "kind": "workspace-root", "framework": None,
                "solution": sln[0], "package_manager": "nuget", "lockfile": None, "deps": set(), "prod_deps": set()}
    deps, text = dotnet_deps(d)
    m = re.search(r"<TargetFrameworks?>\s*net(\d+\.\d+)", text)
    gj = load_json(os.path.join(root, "global.json"))
    web = "Microsoft.NET.Sdk.Web" in text or "Microsoft.NET.Sdk.Worker" in text
    info = {"language": "fsharp" if projs[0].endswith(".fsproj") else "csharp", "runtime": "dotnet",
            "package_manager": "nuget", "lockfile": "packages.lock.json" if exists(d, "packages.lock.json") else None,
            "runtime_version": m.group(1) if m else clean_version((gj.get("sdk") or {}).get("version")),
            "version_source": "TargetFramework" if m else ("global.json" if gj else None),
            "project_file": projs[0], "assembly": os.path.splitext(projs[0])[0],
            "framework": "aspnetcore" if "Microsoft.NET.Sdk.Web" in text else ("worker" if "Sdk.Worker" in text else None),
            "kind": "web" if "Microsoft.NET.Sdk.Web" in text else ("worker" if web else "library"),
            "default_port": 8080, "deps": deps, "prod_deps": deps,
            "invariant_globalization": "InvariantGlobalization>true" in text}
    return info


def detect_ruby(d, root):
    if not exists(d, "Gemfile"):
        return None
    text = read(os.path.join(d, "Gemfile"))
    deps = gem_deps(text)
    info = {"language": "ruby", "runtime": "ruby", "package_manager": "bundler",
            "lockfile": "Gemfile.lock" if exists(d, "Gemfile.lock") else None}
    v = read(os.path.join(d, ".ruby-version")).strip()
    m = re.search(r"^\s*ruby\s+['\"]([^'\"]+)", text, re.M)
    tv = re.search(r"^ruby\s+(\S+)", read(os.path.join(d, ".tool-versions")), re.M)
    info["runtime_version"] = clean_version(v) or (clean_version(m.group(1)) if m else None) or (clean_version(tv.group(1)) if tv else None)
    info["version_source"] = ".ruby-version" if v else ("Gemfile" if m else (".tool-versions" if tv else None))
    fw, port = None, 3000
    if "rails" in deps or exists(d, "config/application.rb"):
        fw = "rails"
        lock = read(os.path.join(d, "Gemfile.lock"))
        rm = re.search(r"^\s{4}rails \((\d+\.\d+)", lock, re.M)
        info["rails_version"] = rm.group(1) if rm else None
        info["migrations"] = "bin/rails db:prepare"
    elif "sinatra" in deps:
        fw, port = "sinatra", 4567
    elif "hanami" in deps:
        fw, port = "hanami", 2300
    elif "roda" in deps:
        fw, port = "roda", 9292
    elif "jekyll" in deps:
        fw, port = "jekyll", 80
    elif exists(d, "config.ru"):
        fw, port = "rack", 9292
    kind = "static" if fw == "jekyll" else ("web" if fw else ("worker" if deps & WORKER_DEPS else "library"))
    info.update({"framework": fw, "kind": kind, "default_port": port, "app_servers": [s for s in ("puma", "unicorn", "falcon", "thruster") if s in deps],
                 "workers": sorted(deps & WORKER_DEPS), "deps": deps, "prod_deps": deps,
                 "assets": exists(d, "app/assets", "app/javascript"), "bootsnap": "bootsnap" in deps})
    if fw == "jekyll":
        info["static_out"] = "_site"
    return info


def detect_php(d, root):
    wp = exists(d, "wp-config.php", "wp-config-sample.php", "wp-content")
    if not exists(d, "composer.json") and not wp and not exists(d, "index.php"):
        return None
    data = load_json(os.path.join(d, "composer.json"))
    deps = composer_deps(data)
    info = {"language": "php", "runtime": "php", "package_manager": "composer",
            "lockfile": "composer.lock" if exists(d, "composer.lock") else None,
            "runtime_version": clean_version((data.get("require") or {}).get("php")),
            "version_source": "composer.json require.php" if (data.get("require") or {}).get("php") else None}
    fw = None
    for dep, name in (("laravel/framework", "laravel"), ("symfony/framework-bundle", "symfony"),
                      ("slim/slim", "slim"), ("cakephp/cakephp", "cakephp"), ("yiisoft/yii2", "yii"),
                      ("codeigniter4/framework", "codeigniter"), ("laminas/laminas-mvc", "laminas"),
                      ("drupal/core-recommended", "drupal"), ("drupal/core", "drupal"),
                      ("roots/wordpress", "wordpress"), ("johnpbloch/wordpress", "wordpress")):
        if dep in deps:
            fw = name
            break
    if not fw and wp:
        fw = "wordpress"
    exts = sorted(k[4:] for k in deps if k.startswith("ext-"))
    info.update({"framework": fw, "kind": "web", "default_port": 8080, "deps": deps, "prod_deps": deps,
                 "php_extensions": exts, "octane": "laravel/octane" in deps,
                 "assets": exists(d, "package.json") and exists(d, "vite.config.js", "vite.config.ts", "webpack.mix.js")})
    if fw == "laravel":
        info["migrations"] = "php artisan migrate --force"
        info["workers"] = ["queue:work"] + (["horizon"] if "laravel/horizon" in deps else [])
    elif fw == "symfony":
        info["migrations"] = "php bin/console doctrine:migrations:migrate --no-interaction"
    return info


def detect_elixir(d, root):
    if not exists(d, "mix.exs"):
        return None
    text = read(os.path.join(d, "mix.exs"))
    deps = mix_deps(text)
    tv = read(os.path.join(d, ".tool-versions")) or read(os.path.join(root, ".tool-versions"))
    em = re.search(r"^elixir\s+(\S+)", tv, re.M)
    om = re.search(r"^erlang\s+(\S+)", tv, re.M)
    am = re.search(r"app:\s*:([a-z0-9_]+)", text)
    fw = "phoenix" if "phoenix" in deps else ("plug" if "plug_cowboy" in deps or "bandit" in deps else None)
    if "[apps_path" in text or "apps_path:" in text:
        kind = "workspace-root"
    else:
        kind = "web" if fw else "library"
    return {"language": "elixir", "runtime": "beam", "package_manager": "mix",
            "lockfile": "mix.lock" if exists(d, "mix.lock") else None,
            "runtime_version": clean_version(em.group(1)) if em else None,
            "otp_version": clean_version(om.group(1)) if om else None,
            "version_source": ".tool-versions" if em else None, "app_name": am.group(1) if am else None,
            "framework": fw, "kind": kind, "default_port": 4000, "deps": deps, "prod_deps": deps,
            "migrations": "bin/migrate" if "ecto_sql" in deps else None,
            "assets": exists(d, "assets")}


def detect_misc(d, root):
    """Smaller ecosystems and static site generators."""
    if exists(d, "Package.swift"):
        text = read(os.path.join(d, "Package.swift"))
        fw = "vapor" if "vapor/vapor" in text else ("hummingbird" if "hummingbird" in text else None)
        m = re.search(r"swift-tools-version:\s*([\d.]+)", text)
        return {"language": "swift", "runtime": "swift", "package_manager": "swiftpm",
                "lockfile": "Package.resolved" if exists(d, "Package.resolved") else None,
                "runtime_version": m.group(1) if m else None, "version_source": "Package.swift" if m else None,
                "framework": fw, "kind": "web" if fw else "cli", "default_port": 8080, "deps": set(), "prod_deps": set()}
    if exists(d, "pubspec.yaml"):
        text = read(os.path.join(d, "pubspec.yaml"))
        if "flutter:" in text:
            return {"language": "dart", "runtime": "flutter", "package_manager": "pub", "lockfile": None,
                    "framework": "flutter", "kind": "static", "static_out": "build/web", "default_port": 80,
                    "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set(),
                    "notes": ["Flutter: only the web target can be containerized (flutter build web)."]}
        fw = "dart_frog" if "dart_frog" in text else ("shelf" if "shelf" in text else None)
        return {"language": "dart", "runtime": "dart", "package_manager": "pub",
                "lockfile": "pubspec.lock" if exists(d, "pubspec.lock") else None, "framework": fw,
                "kind": "web" if fw else "cli", "default_port": 8080, "runtime_version": None,
                "version_source": None, "deps": set(), "prod_deps": set()}
    if exists(d, "gleam.toml"):
        return {"language": "gleam", "runtime": "beam", "package_manager": "gleam", "lockfile": "manifest.toml",
                "framework": "wisp" if "wisp" in read(os.path.join(d, "gleam.toml")) else None, "kind": "web",
                "default_port": 8000, "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    if any(f.endswith(".cabal") for f in os.listdir(d)) or exists(d, "stack.yaml"):
        return {"language": "haskell", "runtime": "ghc", "package_manager": "stack" if exists(d, "stack.yaml") else "cabal",
                "lockfile": None, "framework": None, "kind": "web", "default_port": 8080,
                "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    if exists(d, "build.zig"):
        return {"language": "zig", "runtime": "zig", "package_manager": "zig", "lockfile": None, "framework": None,
                "kind": "cli", "default_port": 8080, "runtime_version": None, "version_source": None,
                "deps": set(), "prod_deps": set()}
    if exists(d, "shard.yml"):
        text = read(os.path.join(d, "shard.yml"))
        fw = "kemal" if "kemal" in text else ("lucky" if "lucky" in text else ("amber" if "amber" in text else None))
        return {"language": "crystal", "runtime": "crystal", "package_manager": "shards",
                "lockfile": "shard.lock" if exists(d, "shard.lock") else None, "framework": fw,
                "kind": "web" if fw else "cli", "default_port": 3000, "runtime_version": None,
                "version_source": None, "deps": set(), "prod_deps": set()}
    if exists(d, "CMakeLists.txt", "meson.build") or (exists(d, "Makefile") and any(
            f.endswith((".c", ".cpp", ".cc")) for f in os.listdir(d))):
        return {"language": "c/c++", "runtime": "native", "package_manager": "cmake" if exists(d, "CMakeLists.txt") else
                ("meson" if exists(d, "meson.build") else "make"), "lockfile": "vcpkg.json" if exists(d, "vcpkg.json") else
                ("conanfile.txt" if exists(d, "conanfile.txt") else None), "framework": None, "kind": "cli",
                "default_port": 8080, "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    hugo = first_existing(d, "hugo.toml", "hugo.yaml", "hugo.json")
    if hugo or (exists(d, "config.toml") and exists(d, "content") and (exists(d, "themes") or exists(d, "layouts"))):
        return {"language": "go-template", "runtime": "hugo", "package_manager": "hugo", "lockfile": None,
                "framework": "hugo", "kind": "static", "static_out": "public", "default_port": 80,
                "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    if exists(d, "mkdocs.yml"):
        return {"language": "markdown", "runtime": "python", "package_manager": "pip", "lockfile": None,
                "framework": "mkdocs", "kind": "static", "static_out": "site", "default_port": 80,
                "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    if d == root and exists(d, "index.html"):
        return {"language": "html", "runtime": "none", "package_manager": None, "lockfile": None,
                "framework": "plain-html", "kind": "static", "static_out": ".", "default_port": 80,
                "runtime_version": None, "version_source": None, "deps": set(), "prod_deps": set()}
    return None


DETECTORS = [detect_node, detect_python, detect_go, detect_rust, detect_jvm, detect_dotnet,
             detect_ruby, detect_php, detect_elixir, detect_misc]


# --------------------------------------------------------------------------
# Project-wide detection
# --------------------------------------------------------------------------
def candidate_dirs(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = 0 if dirpath == root else os.path.relpath(dirpath, root).count(os.sep) + 1
        dirnames[:] = sorted(x for x in dirnames if x not in SKIP_DIRS and not x.startswith("."))
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        out.append(dirpath)
    return out


def existing_deploy_files(root):
    found = {}
    patterns = {
        "dockerfiles": lambda rel, fn: fn == "Dockerfile" or fn.startswith("Dockerfile.") or fn.endswith(".Dockerfile") or fn == "Containerfile",
        "dockerignore": lambda rel, fn: fn == ".dockerignore",
        "compose": lambda rel, fn: re.match(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$", fn) is not None,
        "github_workflows": lambda rel, fn: "/.github/workflows/" in "/" + rel and fn.endswith((".yml", ".yaml")),
        "gitlab_ci": lambda rel, fn: fn == ".gitlab-ci.yml",
        "other_ci": lambda rel, fn: rel in ("bitbucket-pipelines.yml", ".circleci/config.yml", "azure-pipelines.yml",
                                            "Jenkinsfile", ".drone.yml", ".woodpecker.yml", "cloudbuild.yaml", "buildspec.yml"),
        "kubernetes": lambda rel, fn: fn.endswith((".yaml", ".yml")) and re.search(r"(^|/)(k8s|kubernetes|manifests|deploy|kustomize)/", rel) is not None,
        "helm": lambda rel, fn: fn == "Chart.yaml",
        "kustomize": lambda rel, fn: fn in ("kustomization.yaml", "kustomization.yml"),
        "terraform": lambda rel, fn: fn.endswith(".tf"),
        "platform": lambda rel, fn: rel in ("fly.toml", "render.yaml", "railway.json", "railway.toml", "vercel.json",
                                            "netlify.toml", "Procfile", "app.yaml", "app.json", "heroku.yml",
                                            "config/deploy.yml", "serverless.yml", "wrangler.toml", "wrangler.jsonc",
                                            "nixpacks.toml", "Dockerrun.aws.json", "copilot/.workspace", "apprunner.yaml",
                                            "zeabur.json", "koyeb.yaml", ".do/app.yaml", "captain-definition", "porter.yaml"),
        "env_files": lambda rel, fn: fn.startswith(".env"),
        "dependabot_renovate": lambda rel, fn: rel in (".github/dependabot.yml", "renovate.json", ".github/renovate.json", "renovate.json5"),
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames if x not in (SKIP_DIRS - {".github"}) or x == ".github"]
        dirnames[:] = [x for x in dirnames if x not in {".git", "node_modules", "vendor", ".venv", "venv", "target", ".terraform"}]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            for key, test in patterns.items():
                if test(rel, fn):
                    found.setdefault(key, []).append(rel)
    if "kubernetes" in found:  # keep only files that are actually manifests
        found["kubernetes"] = [p for p in found["kubernetes"] if re.search(r"^\s*(apiVersion|kind):", read(os.path.join(root, p), 4096), re.M)]
        if not found["kubernetes"]:
            del found["kubernetes"]
    return {k: sorted(v) for k, v in found.items()}


def git_info(root):
    info = {}
    try:
        url = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    if not url:
        cfg = read(os.path.join(root, ".git", "config"))
        m = re.search(r"\[remote \"origin\"\][^\[]*url\s*=\s*(\S+)", cfg, re.S)
        url = m.group(1) if m else ""
    if url:
        info["remote_url"] = re.sub(r"//[^@/]+@", "//", url)  # never echo embedded credentials
        m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
        if m:
            info["owner"], info["repo"] = m.group(1), m.group(2)
            info["host"] = "github" if "github.com" in url else ("gitlab" if "gitlab" in url else ("bitbucket" if "bitbucket" in url else "other"))
            info["image_ref"] = "ghcr.io/%s/%s" % (m.group(1).lower(), m.group(2).lower()) if info["host"] == "github" else None
    gi = read(os.path.join(root, ".gitignore"))
    info["gitignore_has_env"] = bool(re.search(r"^/?\.env(\*|\.\*)?\s*$", gi, re.M))
    return info


def detect(root):
    root = os.path.abspath(root)
    services, packages = [], []
    claimed = []
    for d in candidate_dirs(root):
        for det in DETECTORS:
            try:
                info = det(d, root)
            except Exception as e:  # a broken manifest must not kill detection
                info = {"error": "%s failed: %s" % (det.__name__, e)}
            if info:
                info["path"] = os.path.relpath(d, root).replace(os.sep, "/") if d != root else "."
                info["name"] = os.path.basename(d) if d != root else os.path.basename(root)
                if "error" in info:
                    packages.append(info)
                    continue
                claimed.append((d, info))
    # A dir can match several detectors (e.g. Rails app with package.json for assets,
    # Laravel with vite). Keep the primary one; the secondary becomes an "assets" note.
    by_dir = {}
    for d, info in claimed:
        by_dir.setdefault(d, []).append(info)
    priority = ["ruby", "php", "python", "elixir", "jvm", "dotnet", "go", "rust"]
    for d, infos in by_dir.items():
        if len(infos) > 1:
            primary = None
            for rt in priority:
                primary = next((i for i in infos if i.get("runtime") == rt or (rt == "jvm" and i.get("runtime") == "jvm")), None)
                if primary and primary.get("kind") not in ("library",):
                    break
                primary = None
            if primary is None:
                web = [i for i in infos if i.get("kind") in ("web", "static", "worker")]
                primary = web[0] if web else infos[0]
            node = next((i for i in infos if i.get("runtime") in ("node", "bun") and i is not primary), None)
            if node:
                primary.setdefault("notes", []).append(
                    "Also has package.json (%s) - build frontend assets in a node build stage." % (node.get("framework") or "assets"))
                primary["asset_build"] = {"package_manager": node.get("package_manager"), "lockfile": node.get("lockfile"),
                                          "build_cmd": node.get("build_cmd")}
            by_dir[d] = [primary]
    dirs = sorted(by_dir)
    for d in dirs:
        info = by_dir[d][0]
        nested = [x for x in dirs if x != d and x.startswith(d + os.sep)]
        env, ports, health = scan_sources(d, nested)
        env |= env_example_vars(d)
        info["env_vars"] = sorted(env)
        info["health_routes"] = sorted(health)
        info["health_path"] = next((h for h in ("/healthz", "/health", "/api/health", "/up", "/livez", "/ready", "/ping", "/status") if h in health), None)
        if info.get("framework") == "rails" and not info["health_path"] and version_tuple(info.get("rails_version") or "0") >= (7, 1):
            info["health_path"] = "/up"
        if info.get("framework") == "spring-boot" and info.get("actuator"):
            info["health_path"] = "/actuator/health"
        if info.get("framework") in ("quarkus",) and "quarkus-smallrye-health" in str(info.get("deps")):
            info["health_path"] = "/q/health"
        detected = most_common([p for p in ports if p not in (5432, 3306, 6379, 27017, 5672, 9092, 9200)])
        info["port"] = info.get("config_port") or detected or info.get("default_port")
        info["port_source"] = "config" if info.get("config_port") else ("source code" if detected else "framework default")
        if info.get("kind") == "static":
            info["port"], info["port_source"] = 8080, "static server (unprivileged nginx)"
        deps = set(info.get("deps") or ())
        svcs = services_from_deps(deps)
        if info.get("prisma_provider") in ("postgresql", "cockroachdb"):
            svcs.add("postgres")
        elif info.get("prisma_provider") == "mysql":
            svcs.add("mysql")
        elif info.get("prisma_provider") == "mongodb":
            svcs.add("mongodb")
        if "celery" in deps and not svcs & {"redis", "rabbitmq"}:
            svcs.add("redis")
        envtxt = " ".join(info["env_vars"])
        for pat, svc in ENV_SERVICE_HINTS:
            if re.search(pat, envtxt):
                svcs.add(svc)
        if "DATABASE_URL" in envtxt and not svcs & {"postgres", "mysql", "mongodb"}:
            info.setdefault("notes", []).append("Reads DATABASE_URL but no DB driver identified - ask which database.")
        if info.get("framework") in ("laravel", "symfony", "drupal", "wordpress"):
            envex = read(os.path.join(d, ".env.example"))
            m = re.search(r"^DB_CONNECTION=(\w+)", envex, re.M)
            if m:
                svcs.add({"pgsql": "postgres", "mysql": "mysql", "mariadb": "mysql", "sqlite": "sqlite"}.get(m.group(1), m.group(1)))
            elif info["framework"] == "wordpress":
                svcs.add("mysql")
        if deps & SQLITE_DEPS or "sqlite" in svcs:
            svcs.discard("sqlite")
            info.setdefault("notes", []).append("Uses SQLite - needs a persistent volume and must run as a single replica.")
        info["backing_services"] = sorted(svcs)
        info["workers"] = info.get("workers") or sorted(deps & WORKER_DEPS)
        for k in ("deps", "prod_deps", "config_port"):
            info.pop(k, None)
        if info.get("kind") in ("mobile", "desktop"):
            info.setdefault("notes", []).append("%s app - not containerizable; only its backend (if any) can be deployed." % info["kind"])
        if info.get("kind") in ("library", "workspace-root", "cli"):
            packages.append(info)
        else:
            services.append(info)

    root_info = by_dir.get(root, [{}])[0]
    monorepo = None
    if root_info.get("monorepo_tool") or root_info.get("kind") == "workspace-root" or len(services) > 1:
        monorepo = {"tool": root_info.get("monorepo_tool"), "service_count": len(services),
                    "note": "Build each service image with the REPO ROOT as context (shared lockfile/packages)."
                    if root_info.get("monorepo_tool") else "Multiple independent services - one Dockerfile per service directory."}
    existing = existing_deploy_files(root)
    backing = sorted({s for svc in services for s in svc.get("backing_services", [])})
    env_all = sorted({e for svc in services for e in svc.get("env_vars", [])})
    warnings = []
    if not services:
        warnings.append("No deployable service detected - ask the user what to run before generating anything.")
    if existing.get("dockerfiles") or existing.get("compose"):
        warnings.append("Deployment files already exist - prefer Audit/Fix mode over regenerating.")
    for svc in services:
        if svc.get("kind") == "web" and not svc.get("health_path"):
            warnings.append("%s: no health route found - add a minimal /healthz endpoint (or use a TCP/process check)." % svc["path"])
        if not svc.get("lockfile") and svc.get("package_manager") not in (None, "go", "hugo"):
            warnings.append("%s: no lockfile - builds are not reproducible; generate and commit one." % svc["path"])
        if not svc.get("runtime_version") and svc.get("runtime") not in ("none", "hugo"):
            warnings.append("%s: runtime version not declared - use the default in references/versions.md and pin it." % svc["path"])
    gi = git_info(root)
    if existing.get("env_files") and ".env" in existing["env_files"] and not gi.get("gitignore_has_env"):
        warnings.append(".env exists but is not in .gitignore - secrets may be committed.")
    return {"tool": "vibe-ship detect", "version": "2.0.0", "root": root, "services": services,
            "packages": packages, "monorepo": monorepo, "backing_services": backing,
            "env_vars": env_all, "existing": existing, "git": gi, "warnings": warnings}


def print_human(r):
    print("=" * 64)
    print("VIBE-SHIP STACK DETECTION  -  %s" % r["root"])
    print("=" * 64)
    if r["monorepo"]:
        print("Monorepo: %s (%d services) - %s" % (r["monorepo"]["tool"] or "multi-service", r["monorepo"]["service_count"], r["monorepo"]["note"]))
    for s in r["services"]:
        print("\n[%s] %s" % (s["path"], s.get("kind")))
        print("  stack:     %s / %s%s" % (s.get("language"), s.get("framework") or "-",
                                          ("  (runtime %s)" % s["runtime"]) if s.get("runtime") not in (s.get("language"),) else ""))
        print("  version:   %s%s" % (s.get("runtime_version") or "not declared", (" from %s" % s["version_source"]) if s.get("version_source") else ""))
        print("  packages:  %s, lockfile: %s" % (s.get("package_manager"), s.get("lockfile") or "NONE"))
        print("  port:      %s (%s)   health: %s" % (s.get("port"), s.get("port_source"), s.get("health_path") or "none found"))
        for k in ("entrypoint", "build_cmd", "start_cmd", "migrations", "static_out", "binary"):
            if s.get(k):
                print("  %-10s %s" % (k + ":", s[k]))
        if s.get("workers"):
            print("  workers:   %s" % ", ".join(s["workers"]))
        if s.get("backing_services"):
            print("  services:  %s" % ", ".join(s["backing_services"]))
        if s.get("env_vars"):
            print("  env vars:  %s" % ", ".join(s["env_vars"][:25]) + (" ..." if len(s["env_vars"]) > 25 else ""))
        for n in s.get("notes", []):
            print("  note:      %s" % n)
    if r["packages"]:
        print("\nNon-deployable packages: %s" % ", ".join("%s (%s)" % (p["path"], p.get("kind") or p.get("error")) for p in r["packages"]))
    if r["existing"]:
        print("\nExisting deployment files:")
        for k, v in r["existing"].items():
            print("  %-20s %s" % (k, ", ".join(v[:8]) + (" ..." if len(v) > 8 else "")))
    if r["git"].get("image_ref"):
        print("\nSuggested image: %s" % r["git"]["image_ref"])
    if r["warnings"]:
        print("\nWarnings:")
        for w in r["warnings"]:
            print("  - " + w)
    print("=" * 64)


def main():
    ap = argparse.ArgumentParser(description="Detect a project's stack for deployment generation")
    ap.add_argument("--path", default=".", help="Project root (default: current directory)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()
    if not os.path.isdir(args.path):
        print("Not a directory: %s" % args.path, file=sys.stderr)
        return 2
    result = detect(args.path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=False, default=sorted))
    else:
        print_human(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
