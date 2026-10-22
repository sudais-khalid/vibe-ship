#!/usr/bin/env python3
"""
vibe-ship audit

Static analyzer for deployment setups. Scans a project for Dockerfiles,
.dockerignore, Compose files, GitHub Actions / GitLab CI pipelines and
Kubernetes manifests, checks them against 90+ rules (security, reliability,
supply chain, scalability) and prints a scored report.

  - No Docker daemon, network access or third-party packages required.
  - Real Dockerfile parsing: line continuations, heredocs, multi-stage
    inheritance (USER / HEALTHCHECK / base image follow the stage chain).
  - YAML is read by key path, so rules know which service/job/container a
    line belongs to instead of regex-matching the whole file.
  - Stable rule IDs (VS-D001 ...), inline suppressions, CI exit codes,
    SARIF output for GitHub code scanning.

Usage:
    python3 audit.py --path .                         # human report
    python3 audit.py --path . --format json|sarif|markdown
    python3 audit.py --path . --fail-on high          # exit 1 on HIGH or worse
    python3 audit.py --path . --ignore VS-D003,VS-G001
    python3 audit.py --list-rules                     # rule catalog (markdown)

Suppress a finding inline (Dockerfile / YAML):
    # vibe-ship: ignore VS-D011 reason...      (applies to the same or next line)
    # vibe-ship: ignore-file VS-D020           (applies to the whole file)
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

VERSION = "2.0.0"
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
PENALTY = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", ".terraform",
             "target", ".next", ".nuxt", "dist", ".gradle", "Pods", ".tox", "bower_components"}

# --------------------------------------------------------------------------
# Rule catalog: id -> (severity, category, title, fix)
# --------------------------------------------------------------------------
RULES = {
    # Dockerfile - base images
    "VS-D001": ("HIGH", "reproducibility", "Base image uses the ':latest' tag",
                "Pin a specific version, e.g. 'node:24-alpine' (and ideally a digest)."),
    "VS-D002": ("MEDIUM", "reproducibility", "Base image has no tag (implicitly ':latest')",
                "Add an explicit version tag to the FROM line."),
    "VS-D003": ("INFO", "supply-chain", "Base image not pinned by digest",
                "For fully reproducible builds pin '@sha256:...' and let Dependabot/Renovate bump it."),
    "VS-D004": ("LOW", "reproducibility", "Base image tag comes from a build ARG with no default",
                "Give the ARG a pinned default so plain 'docker build' is reproducible."),
    "VS-D005": ("LOW", "reproducibility", "Base image tag is only a major version on a fast-moving runtime",
                "Use at least major.minor (e.g. 'python:3.14-slim') so rebuilds don't jump minors silently."),
    # Dockerfile - users / privileges
    "VS-D010": ("CRITICAL", "security", "Final stage explicitly runs as root",
                "Switch to an unprivileged user (USER app / USER 10001) before CMD."),
    "VS-D011": ("CRITICAL", "security", "No USER in final stage - container runs as root",
                "Create an unprivileged user and add 'USER <name>' before CMD."),
    "VS-D012": ("MEDIUM", "security", "sudo used inside the image",
                "Run privileged steps before USER instead of installing/using sudo."),
    "VS-D013": ("HIGH", "security", "World-writable permissions (chmod 777)",
                "Grant ownership to the app user (chown/COPY --chown) with 755/644 instead."),
    "VS-D014": ("HIGH", "security", "SSH port 22 exposed",
                "Containers should not run sshd; use 'docker exec' / 'kubectl exec' for access."),
    # Dockerfile - health
    "VS-D020": ("HIGH", "reliability", "HEALTHCHECK uses a tool that does not exist in the final image",
                "Install the tool in the final stage, use one the base ships (busybox wget on alpine), or use a language-native check."),
    "VS-D021": ("HIGH", "reliability", "Shell-form HEALTHCHECK on an image without a shell",
                "Use exec form (HEALTHCHECK CMD [\"/app/healthcheck\"]) or rely on orchestrator probes."),
    "VS-D022": ("MEDIUM", "reliability", "No HEALTHCHECK in final stage",
                "Add HEALTHCHECK hitting a real endpoint, or document that orchestrator probes cover it (then suppress)."),
    # Dockerfile - build structure
    "VS-D030": ("MEDIUM", "size", "Single-stage build compiles/bundles in the runtime image",
                "Use a multi-stage build: toolchain in a build stage, only artifacts in a slim runtime stage."),
    "VS-D031": ("LOW", "performance", "'COPY . .' before dependency install busts the layer cache",
                "Copy only manifests/lockfiles first, install, then copy the rest."),
    "VS-D032": ("LOW", "reliability", "No WORKDIR in final stage",
                "Set WORKDIR /app so files don't land in / and relative paths are predictable."),
    "VS-D033": ("INFO", "maintainability", "'RUN cd ...' instead of WORKDIR",
                "Use WORKDIR to change directory; 'cd' only affects that one RUN."),
    "VS-D034": ("INFO", "maintainability", "MAINTAINER is deprecated",
                "Use LABEL org.opencontainers.image.authors=\"...\"."),
    # Dockerfile - secrets
    "VS-D040": ("CRITICAL", "secrets", "Secret value baked into the image with ENV",
                "Pass at runtime (env_file / orchestrator secrets). ENV values are visible via 'docker inspect'."),
    "VS-D041": ("HIGH", "secrets", "Secret-looking build ARG (visible in 'docker history')",
                "Use BuildKit secrets: RUN --mount=type=secret,id=name ... and 'docker build --secret'."),
    "VS-D042": ("CRITICAL", "secrets", "Secret file copied into the image",
                "Never COPY .env / keys into an image; inject at runtime and exclude them in .dockerignore."),
    # Dockerfile - supply chain / package managers
    "VS-D050": ("MEDIUM", "supply-chain", "ADD fetches a remote URL without checksum",
                "Use 'ADD --checksum=sha256:...' or curl + sha256sum verification in a RUN."),
    "VS-D051": ("LOW", "maintainability", "ADD used for a local file",
                "Use COPY for local files; ADD's implicit tar extraction surprises people."),
    "VS-D052": ("HIGH", "supply-chain", "Remote script piped straight into a shell",
                "Download, verify a checksum/signature, then execute - or use a pinned package."),
    "VS-D060": ("LOW", "size", "apt-get install without --no-install-recommends",
                "Add --no-install-recommends to avoid pulling in unneeded packages."),
    "VS-D061": ("LOW", "size", "apt lists not cleaned in the same RUN",
                "Append '&& rm -rf /var/lib/apt/lists/*' (or use a cache mount)."),
    "VS-D062": ("LOW", "size", "apk add without --no-cache",
                "Use 'apk add --no-cache ...'."),
    "VS-D063": ("LOW", "size", "pip install without --no-cache-dir",
                "Add --no-cache-dir (or use a BuildKit cache mount)."),
    "VS-D064": ("MEDIUM", "reproducibility", "JS dependencies installed without honoring the lockfile",
                "Use 'npm ci', 'yarn install --immutable' (berry) / '--frozen-lockfile', 'pnpm install --frozen-lockfile', 'bun install --frozen-lockfile'."),
    "VS-D065": ("LOW", "reproducibility", "Package-manager self-update / unpinned global install in image",
                "Pin the version (corepack, 'npm i -g pnpm@x.y.z', 'pip install uv==x.y')."),
    # Dockerfile - runtime behaviour
    "VS-D080": ("MEDIUM", "reliability", "Shell-form CMD/ENTRYPOINT - app won't receive SIGTERM",
                "Use exec form: CMD [\"node\", \"server.js\"] so the app is PID 1 and shuts down gracefully."),
    "VS-D081": ("LOW", "reliability", "Multiple CMD/ENTRYPOINT in one stage (only the last applies)",
                "Keep a single CMD/ENTRYPOINT per stage."),
    "VS-D082": ("MEDIUM", "reliability", "Started through a package manager (npm/yarn/pnpm start)",
                "Run the runtime directly (CMD [\"node\", \"dist/server.js\"]) - package managers don't forward signals reliably."),
    "VS-D083": ("LOW", "observability", "Python image without PYTHONUNBUFFERED=1",
                "Set ENV PYTHONUNBUFFERED=1 so logs reach stdout immediately."),
    "VS-D084": ("INFO", "performance", "JVM started without container-aware heap sizing",
                "Add -XX:MaxRAMPercentage=75 (JAVA_TOOL_OPTIONS) - the default only uses 25% of the container limit."),
    "VS-D085": ("LOW", "reliability", "Development server used as the production command",
                "Use a production server (gunicorn/uvicorn workers, 'next start', 'rails s' with puma, etc.)."),
    # .dockerignore
    "VS-D100": ("HIGH", "secrets", "No .dockerignore for this build context",
                "Add .dockerignore excluding .git, .env*, node_modules, build output, keys."),
    "VS-D101": ("HIGH", "secrets", ".dockerignore does not exclude .env files",
                "Add '.env' and '.env.*' (keep '!.env.example' if needed)."),
    "VS-D102": ("MEDIUM", "size", ".dockerignore does not exclude .git",
                "Add '.git' - it bloats context and leaks history."),
    "VS-D103": ("MEDIUM", "size", ".dockerignore does not exclude node_modules",
                "Add 'node_modules' so host-built native modules don't leak into the image."),
    # Compose
    "VS-C001": ("CRITICAL", "security", "Service runs privileged",
                "Remove 'privileged: true'; grant only the specific capability needed via cap_add."),
    "VS-C002": ("CRITICAL", "security", "Docker socket mounted into a container",
                "Mounting /var/run/docker.sock is root on the host. Use a socket proxy with a read-only allowlist if unavoidable."),
    "VS-C003": ("HIGH", "security", "Host namespace shared (network_mode/pid/ipc: host)",
                "Use the default bridge network and publish only needed ports."),
    "VS-C004": ("HIGH", "security", "Dangerous capability added",
                "Drop SYS_ADMIN/NET_ADMIN/ALL; add only narrowly scoped capabilities."),
    "VS-C005": ("HIGH", "security", "Seccomp/AppArmor disabled",
                "Remove 'unconfined' security_opt entries."),
    "VS-C010": ("HIGH", "reproducibility", "Service image uses ':latest' or no tag",
                "Pin image versions (postgres:18-alpine, redis:8-alpine)."),
    "VS-C020": ("CRITICAL", "secrets", "Hardcoded credential in compose environment",
                "Use ${VAR} interpolation from a gitignored .env, env_file, or compose secrets."),
    "VS-C030": ("MEDIUM", "security", "Database/cache port published on all host interfaces",
                "Remove the port mapping (services talk over the compose network) or bind to 127.0.0.1."),
    "VS-C040": ("LOW", "reliability", "Services without a healthcheck",
                "Add healthcheck blocks so depends_on can wait for readiness and restarts are automatic."),
    "VS-C041": ("LOW", "reliability", "depends_on without readiness condition",
                "Use 'depends_on: {db: {condition: service_healthy}}' - plain lists only wait for container start."),
    "VS-C050": ("LOW", "reliability", "Services without a restart policy",
                "Add 'restart: unless-stopped' for long-running services."),
    "VS-C051": ("LOW", "scalability", "Services without resource limits",
                "Add deploy.resources.limits (cpus/memory) so one service can't starve the host."),
    "VS-C052": ("LOW", "reliability", "No log rotation configured",
                "Default json-file logs grow forever: set logging.options max-size/max-file (or daemon-wide)."),
    "VS-C060": ("MEDIUM", "data", "Stateful service without a volume",
                "Mount a named volume at the data directory or data is lost on 'docker compose down'."),
    "VS-C061": ("HIGH", "data", "Postgres 18+ volume mounted at the old /var/lib/postgresql/data path",
                "Postgres 18 images moved PGDATA to /var/lib/postgresql/<major>/docker - mount the volume at /var/lib/postgresql."),
    "VS-C070": ("LOW", "reliability", "Source bind-mount in a non-dev compose file",
                "Move '.:/app' style mounts to compose.override.yml / compose.dev.yml."),
    "VS-C080": ("INFO", "scalability", "container_name set (prevents scaling)",
                "Remove container_name if you may ever run --scale > 1."),
    "VS-C090": ("INFO", "maintainability", "Obsolete top-level 'version:' key",
                "Remove it - Compose v2 ignores it and prints a warning."),
    # GitHub Actions
    "VS-G001": ("MEDIUM", "supply-chain", "Action not pinned to a commit SHA",
                "Pin to the full 40-char SHA with a version comment (scripts/pin_actions.py does this)."),
    "VS-G002": ("HIGH", "supply-chain", "Action referenced by a moving branch (@main/@master)",
                "Pin to a released version's commit SHA."),
    "VS-G003": ("MEDIUM", "maintainability", "Deprecated / archived action or workflow command",
                "Replace with the maintained alternative noted in the finding."),
    "VS-G010": ("CRITICAL", "security", "pull_request_target checks out untrusted PR code",
                "Never check out the PR head in pull_request_target; use pull_request, or split into a privileged workflow_run."),
    "VS-G011": ("HIGH", "security", "Untrusted event data interpolated into a run script (script injection)",
                "Pass it through env: (env: TITLE: ${{ github.event.issue.title }}) and use \"$TITLE\" in the script."),
    "VS-G020": ("MEDIUM", "security", "No explicit GITHUB_TOKEN permissions",
                "Add top-level 'permissions: contents: read' and grant more per job only where needed."),
    "VS-G021": ("HIGH", "security", "Workflow grants write-all permissions",
                "Scope permissions per job (contents: read, packages: write, ...)."),
    "VS-G022": ("HIGH", "security", "Insecure workflow commands re-enabled",
                "Remove ACTIONS_ALLOW_UNSECURE_COMMANDS; use $GITHUB_ENV / $GITHUB_OUTPUT."),
    "VS-G030": ("CRITICAL", "secrets", "Literal credential in workflow",
                "Use ${{ secrets.NAME }} (or OIDC for cloud logins)."),
    "VS-G040": ("MEDIUM", "reliability", "Image built/deployed without any test step",
                "Add a test job and make build/deploy 'needs:' it."),
    "VS-G041": ("MEDIUM", "reliability", "Image push not gated to the main branch / non-PR events",
                "Gate with if: github.event_name != 'pull_request' (or push: ${{ github.event_name != 'pull_request' }})."),
    "VS-G042": ("LOW", "reliability", "Deploy workflow without a concurrency group",
                "Add 'concurrency: {group: deploy-${{ github.ref }}, cancel-in-progress: false}' to avoid racing deploys."),
    "VS-G043": ("INFO", "cost", "Jobs without timeout-minutes (default 6 hours)",
                "Set timeout-minutes on jobs so hung runs don't burn minutes."),
    "VS-G050": ("LOW", "supply-chain", "No dependency or image vulnerability scanning anywhere",
                "Add Trivy/Grype/osv-scanner to CI and enable Dependabot or Renovate."),
    "VS-G051": ("INFO", "supply-chain", "Image published without SBOM/provenance attestations",
                "Set 'provenance: mode=max' and 'sbom: true' on docker/build-push-action, optionally sign with cosign."),
    # GitLab CI
    "VS-L001": ("HIGH", "reproducibility", "CI job image uses ':latest' or no tag",
                "Pin job images (node:24-alpine, docker:28)."),
    "VS-L002": ("CRITICAL", "secrets", "Literal credential in .gitlab-ci.yml",
                "Use masked/protected CI/CD variables."),
    # Kubernetes
    "VS-K001": ("HIGH", "reproducibility", "Container image uses ':latest' or no tag",
                "Deploy immutable tags (git SHA) or digests."),
    "VS-K010": ("MEDIUM", "scalability", "Container without resource requests/limits",
                "Set requests (scheduling) and a memory limit (OOM safety)."),
    "VS-K020": ("MEDIUM", "reliability", "Container without readinessProbe",
                "Add a readinessProbe so traffic only reaches ready pods (required for zero-downtime rollouts)."),
    "VS-K021": ("LOW", "reliability", "Container without livenessProbe",
                "Add a livenessProbe (lenient thresholds) so hung pods are restarted."),
    "VS-K030": ("CRITICAL", "security", "Privileged container",
                "Remove privileged: true."),
    "VS-K031": ("MEDIUM", "security", "allowPrivilegeEscalation not disabled",
                "Set securityContext.allowPrivilegeEscalation: false."),
    "VS-K032": ("HIGH", "security", "Pod/container may run as root",
                "Set runAsNonRoot: true and a non-zero runAsUser."),
    "VS-K033": ("LOW", "security", "Root filesystem is writable",
                "Set readOnlyRootFilesystem: true and mount emptyDir for /tmp."),
    "VS-K034": ("LOW", "security", "Linux capabilities not dropped",
                "Set securityContext.capabilities.drop: [\"ALL\"]."),
    "VS-K035": ("HIGH", "security", "Host namespaces shared (hostNetwork/hostPID/hostIPC)",
                "Remove host namespace sharing."),
    "VS-K036": ("HIGH", "security", "hostPath volume",
                "Use PersistentVolumeClaims / emptyDir instead of host paths."),
    "VS-K040": ("CRITICAL", "secrets", "Literal secret in container env",
                "Use valueFrom.secretKeyRef / envFrom.secretRef."),
    "VS-K041": ("HIGH", "secrets", "Kubernetes Secret with real data committed",
                "Commit only *.example manifests; create real Secrets out-of-band (kubectl, External Secrets, SOPS, Sealed Secrets)."),
    "VS-K050": ("LOW", "reliability", "Single-replica Deployment",
                "Run >= 2 replicas (plus a PodDisruptionBudget) for zero-downtime node maintenance."),
    "VS-K051": ("INFO", "security", "No seccompProfile set",
                "Set securityContext.seccompProfile.type: RuntimeDefault."),
    # Repository-level
    "VS-R001": ("HIGH", "secrets", ".env exists but is not gitignored",
                "Add '.env' and '.env.*' (with '!.env.example') to .gitignore."),
    "VS-R002": ("CRITICAL", "secrets", ".env file is tracked by git",
                "git rm --cached .env, rotate every secret in it, and gitignore it."),
    "VS-S001": ("CRITICAL", "secrets", "Credential pattern found in a deployment file",
                "Remove it, rotate the credential, and load it from a secret store at runtime."),
}

SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:SECRET|PASSWORD|PASSWD|PASS|PWD|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CLIENT_SECRET|"
    r"CREDENTIALS?|AUTH_KEY|SIGNING_KEY|ENCRYPTION_KEY|DSN|CONNECTION_STRING)(?:$|_)", re.I)
SECRET_NAME_SAFE = re.compile(r"(_FILE|_PATH|_DIR|_NAME|_LENGTH|_TTL|_EXPIRY|_EXPIRES?_IN|_HEADER|_ID|_URL_PATH|_ALGORITHM|_PROVIDER|_MIN_LENGTH)$", re.I)
PLACEHOLDER_RE = re.compile(
    r"^(\$\{?[A-Za-z_][A-Za-z0-9_]*(:?[-?][^}]*)?\}?|\$\(.*\)|\{\{.*\}\}|<.*>|changeme|change[-_]?me|"
    r"replace[-_]?me|your[-_].*|xxx+|\*+|example|placeholder|todo|dummy|secret|password|test|none|null|"
    r"\${{.*}}|)$", re.I)
SECRET_VALUE_PATTERNS = [
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("OpenAI/Anthropic key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("Database URL with password", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/@]+:(?!\$|\{|<|\*|password@|pass@|changeme)[^@\s/]{3,}@")),
]

STATEFUL_IMAGES = {
    "postgres": "/var/lib/postgresql", "postgis/postgis": "/var/lib/postgresql", "timescale/timescaledb": "/var/lib/postgresql",
    "pgvector/pgvector": "/var/lib/postgresql", "mysql": "/var/lib/mysql", "mariadb": "/var/lib/mysql", "mongo": "/data/db",
    "redis": "/data", "valkey/valkey": "/data", "elasticsearch": "/usr/share/elasticsearch/data",
    "opensearchproject/opensearch": "/usr/share/opensearch/data", "rabbitmq": "/var/lib/rabbitmq",
    "minio/minio": "/data", "clickhouse/clickhouse-server": "/var/lib/clickhouse", "neo4j": "/data",
    "cassandra": "/var/lib/cassandra", "influxdb": "/var/lib/influxdb2", "apache/kafka": "/var/lib/kafka",
    "bitnami/kafka": "/bitnami/kafka", "getmeili/meilisearch": "/meili_data", "qdrant/qdrant": "/qdrant/storage",
}
DB_PORTS = {"5432", "3306", "6379", "27017", "5672", "15672", "9200", "9300", "9092", "11211", "8123", "9000", "7687", "26257", "6333", "1433", "1521"}
FAST_MOVING = {"python", "node", "golang", "ruby", "php", "rust", "elixir", "erlang"}


# --------------------------------------------------------------------------
# Findings + suppressions
# --------------------------------------------------------------------------
class Report:
    def __init__(self, root, ignore):
        self.root = root
        self.ignore = set(ignore)
        self.findings = []
        self.scanned = {"dockerfiles": [], "dockerignore": [], "compose": [], "github_workflows": [],
                        "gitlab_ci": [], "kubernetes": [], "skipped": []}
        self.suppress = {}  # rel path -> {"file": set(ids), lineno: set(ids)}

    def rel(self, path):
        try:
            return os.path.relpath(path, self.root).replace(os.sep, "/")
        except ValueError:
            return path.replace(os.sep, "/")

    def load_suppressions(self, path, lines):
        sup = {"file": set()}
        for i, line in enumerate(lines, start=1):
            m = re.search(r"vibe-ship:\s*ignore(-file)?\s*[= ]\s*((?:VS-[A-Z]\d{3}[,\s]*)+)", line)
            if m:
                ids = set(re.findall(r"VS-[A-Z]\d{3}", m.group(2)))
                if m.group(1):
                    sup["file"] |= ids
                else:
                    sup.setdefault(i, set()).update(ids)
                    sup.setdefault(i + 1, set()).update(ids)
        self.suppress[self.rel(path)] = sup

    def add(self, rule_id, path, line=None, detail=None, scope=None):
        """scope: (first, last) line range - a suppression anywhere inside it applies (stage-level rules)."""
        if rule_id in self.ignore:
            return
        rel = self.rel(path) if path else "."
        sup = self.suppress.get(rel, {})
        if rule_id in sup.get("file", ()) or (line and rule_id in sup.get(line, ())):
            return
        if scope and any(rule_id in ids for ln, ids in sup.items() if ln != "file" and scope[0] <= ln <= scope[1]):
            return
        sev, cat, title, fix = RULES[rule_id]
        key = (rule_id, rel, line, detail)
        if any((f["rule"], f["file"], f["line"], f.get("detail")) == key for f in self.findings):
            return
        self.findings.append({"rule": rule_id, "severity": sev, "category": cat, "file": rel,
                              "line": line, "message": title + (": " + detail if detail else ""),
                              "detail": detail, "fix": fix})


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def is_placeholder(value):
    v = value.strip().strip("'\"").strip()
    return bool(PLACEHOLDER_RE.match(v)) or v.startswith("${") or v.startswith("$")


def scan_secret_values(rep, path, lines):
    for i, line in enumerate(lines, start=1):
        for label, rx in SECRET_VALUE_PATTERNS:
            m = rx.search(line)
            if m and not (label.startswith("Database URL") and re.search(r"\$\{|\$\(|\$[A-Za-z_]|\{\{", m.group(0))):
                rep.add("VS-S001", path, i, label)


# --------------------------------------------------------------------------
# Dockerfile parsing
# --------------------------------------------------------------------------
def parse_dockerfile(lines):
    """Return list of instructions: dict(line, instr, args, flags, heredoc)."""
    escape = "\\"
    for line in lines[:5]:
        m = re.match(r"\s*#\s*escape\s*=\s*(\S)", line)
        if m:
            escape = m.group(1)
    out, i, n = [], 0, len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        start = i + 1
        buf = stripped
        while buf.endswith(escape) and i + 1 < n:
            buf = buf[:-1].rstrip() + " "
            i += 1
            nxt = lines[i].strip()
            while (not nxt or nxt.startswith("#")) and i + 1 < n:  # comments/blank lines inside continuations
                i += 1
                nxt = lines[i].strip()
            buf += nxt
        m = re.match(r"([A-Za-z]+)\s*(.*)", buf)
        if not m:
            i += 1
            continue
        instr, args = m.group(1).upper(), m.group(2)
        heredoc = []
        for tag in re.findall(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", args):
            body = []
            while i + 1 < n:
                i += 1
                if lines[i].strip() == tag:
                    break
                body.append(lines[i])
            heredoc.extend(body)
        flags = dict(re.findall(r"--([a-z\-]+)(?:=(\S+))?", args.split("[")[0] if instr in ("CMD", "ENTRYPOINT") else args))
        out.append({"line": start, "instr": instr, "args": args, "flags": flags,
                    "body": args + ("\n" + "\n".join(heredoc) if heredoc else "")})
        i += 1
    return out


def split_image(ref):
    """'ghcr.io/x/y:1.2@sha256:..' -> (name, tag, digest)."""
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    name, tag = ref, None
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = ref.rsplit(":", 1)
    return name, tag, digest


def short_name(name):
    n = name.lower()
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/", "library/"):
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n


def image_traits(name, tag):
    n, t = short_name(name), (tag or "").lower()
    base = n.rsplit("/", 1)[-1]
    traits = {"curl": None, "wget": None, "shell": True, "nonroot": False, "windows": False}
    if any(x in n for x in ("windows", "nanoserver", "servercore")):
        traits["windows"] = True
        return traits
    if n == "scratch" or "distroless" in n or "chiseled" in t or n.startswith("cgr.dev/") or "chainguard" in n:
        traits.update(curl=False, wget=False, shell=("-dev" in t or ":latest-dev" in t))
        traits["nonroot"] = "nonroot" in t or n.startswith("cgr.dev/") or "chainguard" in n or "chiseled" in t
        return traits
    if "nonroot" in t or "unprivileged" in n or n.startswith("bitnami/"):
        traits["nonroot"] = True
    is_alpine = "alpine" in t or base in ("alpine", "busybox")
    is_slim = "slim" in t or base == "ubuntu" or (base == "debian" and "slim" in t)
    lang = base in ("node", "python", "ruby", "golang", "php", "alpine", "debian", "ubuntu", "bun", "deno",
                    "rust", "elixir", "erlang", "perl", "eclipse-temurin", "amazoncorretto", "openjdk")
    if is_alpine and lang:
        traits.update(curl=False, wget=True)
    elif is_slim and lang:
        traits.update(curl=False, wget=False)
    elif lang and not t.endswith("alpine") and base in ("node", "python", "ruby", "golang", "rust", "buildpack-deps", "perl"):
        traits.update(curl=True, wget=True)  # full Debian "buildpack-deps" variants
    if n in ("oven/bun",) and ("slim" in t or "distroless" in t):
        traits.update(curl=False, wget=False)
    if base == "aspnet" or base == "runtime" or base == "runtime-deps":
        traits.update(curl=False, wget=("alpine" in t))
        if "chiseled" in t or "distroless" in t:
            traits.update(shell=False, nonroot=True)
    return traits


def audit_dockerfile(rep, path):
    lines = read_lines(path)
    rep.load_suppressions(path, lines)
    rep.scanned["dockerfiles"].append(rep.rel(path))
    scan_secret_values(rep, path, lines)
    ins = parse_dockerfile(lines)
    if not ins:
        return
    global_args = {}
    stages, cur = [], None
    for it in ins:
        if it["instr"] == "ARG" and cur is None:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:=(\S+))?", it["args"])
            if m:
                global_args[m.group(1)] = m.group(2)
            continue
        if it["instr"] == "FROM":
            parts = [p for p in it["args"].split() if not p.startswith("--")]
            image = parts[0] if parts else ""
            alias = parts[2].lower() if len(parts) >= 3 and parts[1].lower() == "as" else None
            cur = {"line": it["line"], "image": image, "alias": alias, "ins": [], "index": len(stages)}
            stages.append(cur)
            continue
        if cur is not None:
            cur["ins"].append(it)
    if not stages:
        return
    aliases = {s["alias"]: s for s in stages if s["alias"]}

    def resolve(img):
        def sub(m):
            v = global_args.get(m.group(1) or m.group(2))
            return v if v else m.group(0)
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, img)

    # Base image checks
    for s in stages:
        img = resolve(s["image"])
        s["resolved"] = img
        if img.lower() in aliases or img.lower() == "scratch" or re.fullmatch(r"\d+", img):
            continue
        if "$" in img:
            rep.add("VS-D004", path, s["line"], s["image"])
            continue
        name, tag, digest = split_image(img)
        if digest:
            continue
        if tag == "latest":
            rep.add("VS-D001", path, s["line"], img)
        elif tag is None:
            rep.add("VS-D002", path, s["line"], img)
        else:
            base = short_name(name).rsplit("/", 1)[-1]
            if base in FAST_MOVING and re.fullmatch(r"\d+(-[a-z0-9.\-]+)?", tag) and base not in ("node", "rust"):
                rep.add("VS-D005", path, s["line"], img)
            rep.add("VS-D003", path, s["line"], img)

    def chain(stage):
        """Stages from the given one back to its external base."""
        seen, c = [], stage
        while c is not None and c not in seen:
            seen.append(c)
            c = aliases.get(c["resolved"].lower())
            if c is None:
                break
        return seen

    final = stages[-1]
    fscope = (final["line"], len(lines) + 1)
    fchain = chain(final)
    base_img = fchain[-1]["resolved"]
    bname, btag, _ = split_image(base_img)
    traits = image_traits(bname, btag)
    chain_ins = [it for st in reversed(fchain) for it in st["ins"]]  # base-most first

    # USER
    users = [it for it in chain_ins if it["instr"] == "USER"]
    if not traits["windows"] and base_img.lower() != "scratch" or users:
        if users:
            u = users[-1]["args"].split(":")[0].strip().lower()
            if u in ("root", "0"):
                rep.add("VS-D010", path, users[-1]["line"])
        elif not traits["nonroot"] and not traits["windows"]:
            rep.add("VS-D011", path, final["line"], "final stage FROM %s" % final["image"], scope=fscope)

    # HEALTHCHECK
    hcs = [it for it in chain_ins if it["instr"] == "HEALTHCHECK"]
    hc = hcs[-1] if hcs else None
    installed = " ".join(it["body"] for it in chain_ins if it["instr"] == "RUN")
    copied = " ".join(it["args"] for it in chain_ins if it["instr"] in ("COPY", "ADD"))
    if hc is None:
        if not traits["windows"]:
            rep.add("VS-D022", path, final["line"], scope=fscope)
    elif not re.match(r"NONE\b", hc["args"], re.I):
        cmd = re.sub(r"^(--\S+\s+)*CMD\s+", "", re.sub(r"--[a-z\-]+=\S+\s*", "", hc["args"]), flags=re.I)
        exec_form = cmd.strip().startswith("[")
        if not exec_form and traits["shell"] is False:
            rep.add("VS-D021", path, hc["line"], base_img)
        for tool in ("curl", "wget"):
            if re.search(r"(^|[\s\[\"'/])%s\b" % tool, cmd) and traits.get(tool) is False:
                if not re.search(r"\b(apt-get|apt|apk|microdnf|dnf|yum)\b[^&;|]*\b(install|add)\b[^&;|]*\b%s\b" % tool, installed) \
                        and tool not in copied:
                    rep.add("VS-D020", path, hc["line"], "'%s' is not installed in %s" % (tool, base_img))

    # Per-stage checks
    build_tools = re.compile(r"\b(npm run build|yarn build|pnpm( run)? build|bun run build|go build|cargo build|mvn|gradle|"
                             r"dotnet (publish|build)|gcc|g\+\+|make\b|mix release|composer install|tsc\b|vite build|next build)")
    for s in stages:
        cmds = [it for it in s["ins"] if it["instr"] in ("CMD", "ENTRYPOINT")]
        for kind in ("CMD", "ENTRYPOINT"):
            same = [c for c in cmds if c["instr"] == kind]
            if len(same) > 1:
                rep.add("VS-D081", path, same[-1]["line"], kind)
        copy_all_line, install_seen = None, False
        for it in s["ins"]:
            a, body, instr = it["args"], it["body"], it["instr"]
            if instr == "MAINTAINER":
                rep.add("VS-D034", path, it["line"])
            if instr in ("COPY", "ADD"):
                srcs = [p for p in a.split() if not p.startswith("--")][:-1]
                if not install_seen and any(p in (".", "./") for p in srcs) and copy_all_line is None:
                    copy_all_line = it["line"]
                for p in srcs:
                    pl = p.lower().strip("\"'[],")
                    base = pl.rsplit("/", 1)[-1]
                    if base == ".env" or re.fullmatch(r"\.env\.(?!example|sample|template|dist)[\w.-]+", base) or \
                            base in ("id_rsa", "id_ed25519", ".npmrc", ".pypirc", "credentials.json", ".netrc") or \
                            base.endswith((".pem", ".key", ".p12", ".pfx")) and "--from" not in a:
                        rep.add("VS-D042", path, it["line"], p)
                if instr == "ADD":
                    if any(re.match(r"https?://", p) for p in srcs):
                        if "checksum" not in it["flags"]:
                            rep.add("VS-D050", path, it["line"])
                    elif srcs and not any(re.search(r"\.(tar(\.\w+)?|tgz|tbz2?|txz)$", p) for p in srcs) and \
                            not any(p.startswith("git@") or p.endswith(".git") for p in srcs):
                        rep.add("VS-D051", path, it["line"])
            if instr == "ENV":
                pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)", a)
                if not pairs:
                    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(.+)", a)
                    pairs = [(m.group(1), m.group(2))] if m else []
                for k, v in pairs:
                    if SECRET_NAME_RE.search(k) and not SECRET_NAME_SAFE.search(k) and not is_placeholder(v):
                        rep.add("VS-D040", path, it["line"], k)
            if instr == "ARG" and not it["flags"]:
                m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", a)
                if m and SECRET_NAME_RE.search(m.group(1)) and not SECRET_NAME_SAFE.search(m.group(1)):
                    rep.add("VS-D041", path, it["line"], m.group(1))
            if instr == "EXPOSE" and re.search(r"(^|\s)22(/tcp)?(\s|$)", a):
                rep.add("VS-D014", path, it["line"])
            if instr == "RUN":
                cache_mount = "type=cache" in a
                if re.search(r"\b(apt-get|apt)\s+(-\S+\s+)*install\b", body):
                    if "--no-install-recommends" not in body and "Install-Recommends" not in body:
                        rep.add("VS-D060", path, it["line"])
                    if "/var/lib/apt/lists" not in body and not cache_mount:
                        rep.add("VS-D061", path, it["line"])
                if re.search(r"\bapk\s+(-\S+\s+)*add\b", body) and "--no-cache" not in body and not cache_mount:
                    rep.add("VS-D062", path, it["line"])
                if re.search(r"\bpip3?\s+install\b", body) and "--no-cache-dir" not in body and not cache_mount \
                        and "PIP_NO_CACHE_DIR" not in " ".join(x["args"] for x in chain_ins + s["ins"] if x["instr"] == "ENV"):
                    if not re.search(r"pip3?\s+install\s+(-U\s+|--upgrade\s+)?(pip|uv|poetry|pipenv|setuptools|wheel)(==[\d.]+)?\s*($|&&|;)", body):
                        rep.add("VS-D063", path, it["line"])
                for seg in re.split(r"&&|;|\|\|", body):
                    seg = seg.strip()
                    if re.match(r"(\S+=\S+\s+)*npm\s+(install|i)(\s|$)", seg) and not re.search(r"\s-g\b|--global", seg):
                        rep.add("VS-D064", path, it["line"], "npm install -> npm ci")
                    elif re.match(r"(\S+=\S+\s+)*yarn(\s+install)?\s*($|--(?!frozen|immutable))", seg) and "frozen-lockfile" not in seg and "immutable" not in seg:
                        rep.add("VS-D064", path, it["line"], "yarn install without --immutable/--frozen-lockfile")
                    elif re.match(r"(\S+=\S+\s+)*pnpm\s+(install|i)(\s|$)", seg) and "frozen-lockfile" not in seg:
                        rep.add("VS-D064", path, it["line"], "pnpm install without --frozen-lockfile")
                    elif re.match(r"(\S+=\S+\s+)*bun\s+(install|i)(\s|$)", seg) and "frozen-lockfile" not in seg:
                        rep.add("VS-D064", path, it["line"], "bun install without --frozen-lockfile")
                    if re.search(r"npm\s+(i|install)\s+(-g|--global)\s+(npm|pnpm|yarn)(\s|$)", seg) or \
                            re.search(r"npm\s+(i|install)\s+(-g|--global)\s+(npm|pnpm|yarn)@latest", seg):
                        rep.add("VS-D065", path, it["line"], seg[:60])
                    if re.search(r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", seg):
                        rep.add("VS-D052", path, it["line"])
                    if re.match(r"cd\s+\S+$", seg) and seg is not None and seg == body.strip():
                        rep.add("VS-D033", path, it["line"])
                if re.search(r"(^|\s|;|&&)sudo\s", body):
                    rep.add("VS-D012", path, it["line"])
                if re.search(r"chmod\s+(-R\s+)?(0?777|a\+rwx|o\+w)\b", body):
                    rep.add("VS-D013", path, it["line"])
                if re.search(r"(npm (ci|install|i)|yarn|pnpm (install|i)|bun install|pip3? install|poetry install|uv sync|uv pip install|"
                             r"pipenv install|bundle install|go mod download|cargo (build|fetch)|composer install|mvn .*dependency|"
                             r"gradle .*dependencies|dotnet restore|mix deps.get)", body):
                    install_seen = True
        if copy_all_line and install_seen:
            rep.add("VS-D031", path, copy_all_line)

    # Final-stage runtime checks
    fins = final["ins"]
    last_cmd = next((it for it in reversed(chain_ins) if it["instr"] == "CMD"), None)
    last_ep = next((it for it in reversed(chain_ins) if it["instr"] == "ENTRYPOINT"), None)
    primary = last_ep or last_cmd
    server_image = short_name(bname).rsplit("/", 1)[-1] in ("nginx", "nginx-unprivileged", "caddy", "httpd", "traefik", "openresty", "haproxy")
    abs_cmd = bool(primary and re.match(r"""\[\s*["']/""", primary["args"].strip()))
    if not any(it["instr"] == "WORKDIR" for it in chain_ins) and base_img.lower() != "scratch" and not server_image and not abs_cmd:
        rep.add("VS-D032", path, final["line"], scope=fscope)
    if primary and primary in fins:
        a = primary["args"].strip()
        if not a.startswith("[") and not re.match(r"exec\s", a):
            rep.add("VS-D080", path, primary["line"], "%s %s" % (primary["instr"], a[:60]))
    for c in (last_ep, last_cmd):
        if c and c in fins:
            a = c["args"]
            if re.search(r"(^|[\[\s\"'])(npm|yarn|pnpm|bun)[\"']?\s*,?\s*[\"']?(run\s+)?(start|serve|prod)\b", a):
                rep.add("VS-D082", path, c["line"], a[:60])
            if re.search(r"manage\.py[\"']?\s*,\s*[\"']?runserver|manage\.py\s+runserver|flask[\"']?\s*,?\s*[\"']?run\b|"
                         r"\bnext[\"']?\s*,?\s*[\"']?dev\b|npm[\"']?\s*,?\s*[\"']?run[\"']?\s*,?\s*[\"']?dev\b|--reload\b|"
                         r"\bnodemon\b|\bvite[\"']?\s*,?\s*[\"']?(dev|serve)?[\"']?\s*\]|php[\"']?\s*,\s*[\"']?artisan[\"']?\s*,\s*[\"']?serve|"
                         r"artisan serve", a):
                rep.add("VS-D085", path, c["line"], a[:60])
    bshort = short_name(bname).rsplit("/", 1)[-1]
    envs = " ".join(it["args"] for it in chain_ins if it["instr"] == "ENV")
    if bshort == "python" or (primary and re.search(r"\b(python3?|gunicorn|uvicorn|granian|celery|daphne|hypercorn)\b", primary["args"])):
        if "PYTHONUNBUFFERED" not in envs and "-u" not in (primary["args"] if primary else "") and bshort not in ("node",):
            rep.add("VS-D083", path, final["line"], scope=fscope)
    if primary and re.search(r"\bjava\b", primary["args"]) and "MaxRAMPercentage" not in envs + primary["args"] and \
            "Xmx" not in envs + primary["args"] and "JAVA_TOOL_OPTIONS" not in envs and "JAVA_OPTS" not in envs + primary["args"]:
        rep.add("VS-D084", path, primary["line"])

    # Single-stage builds that compile in the runtime image
    if len(stages) == 1 and build_tools.search(" ".join(it["body"] for it in final["ins"] if it["instr"] == "RUN")):
        rep.add("VS-D030", path, final["line"], scope=fscope)

    audit_dockerignore(rep, path, ins)


def audit_dockerignore(rep, dockerfile, ins):
    ctx = os.path.dirname(dockerfile)
    candidates = [os.path.join(ctx, os.path.basename(dockerfile) + ".dockerignore"), os.path.join(ctx, ".dockerignore")]
    # Monorepos often build sub-service Dockerfiles with the repo root as context.
    if os.path.abspath(ctx) != os.path.abspath(rep.root):
        candidates.append(os.path.join(rep.root, ".dockerignore"))
    di = next((c for c in candidates if os.path.exists(c)), None)
    if di is None:
        rep.add("VS-D100", dockerfile, None, "expected %s" % rep.rel(os.path.join(ctx, ".dockerignore")))
        return
    if rep.rel(di) in rep.scanned["dockerignore"]:
        return
    rep.scanned["dockerignore"].append(rep.rel(di))
    lines = read_lines(di)
    rep.load_suppressions(di, lines)
    pats = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    positive = [p for p in pats if not p.startswith("!")]

    def covers(name):
        for p in positive:
            q = p.lstrip("/").rstrip("/")
            if q in ("*", "**", "**/*"):
                return True
            if fnmatch.fnmatch(name, q) or fnmatch.fnmatch(name, q.replace("**/", "")) or q == name:
                return True
        return False

    if not covers(".env"):
        rep.add("VS-D101", di, None)
    if not covers(".git"):
        rep.add("VS-D102", di, None)
    copies_node = any("package.json" in it["args"] or "node_modules" in it["args"] for it in ins) or \
        os.path.exists(os.path.join(ctx, "package.json"))
    if copies_node and not covers("node_modules"):
        rep.add("VS-D103", di, None)


# --------------------------------------------------------------------------
# Minimal YAML reader: each line -> key path. Enough to reason about which
# service/job/container a setting belongs to without a YAML dependency.
# --------------------------------------------------------------------------
def strip_comment(s):
    out, q = [], None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def split_flow(body):
    """Split the inside of a flow collection on top-level commas, respecting quotes and nesting."""
    parts, depth, q, cur = [], 0, None, []
    for ch in body:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def flow_entries(text, path, line):
    """Expand '{a: 1, b: [x, y]}' / '[a, b]' into the same entries block-style YAML would produce."""
    text = text.strip()
    out = []
    if text.startswith("{") and text.endswith("}"):
        for part in split_flow(text[1:-1]):
            m = re.match(r"""^("[^"]*"|'[^']*'|[^:]+?)\s*:(?:\s+(.*))?$""", part, re.S)
            if not m:
                continue
            k, v = unquote(m.group(1)), (m.group(2) or "").strip()
            nested = v[:1] in ("{", "[") and v[-1:] in ("}", "]")
            out.append({"line": line, "path": path + (k,), "key": k, "value": "" if nested else unquote(v), "raw": v, "block": []})
            if nested:
                out.extend(flow_entries(v, path + (k,), line))
    elif text.startswith("[") and text.endswith("]"):
        for i, part in enumerate(split_flow(text[1:-1])):
            ip = path + ("[%d]" % i,)
            if part[:1] in ("{", "[") and part[-1:] in ("}", "]"):
                out.extend(flow_entries(part, ip, line))
            else:
                out.append({"line": line, "path": ip, "key": None, "value": unquote(part), "raw": part, "block": []})
    return out


def resolve_aliases(entries):
    """Apply YAML anchors: 'x: &a {...}' + 'y: *a' and merge keys '<<: *a' (compose x- extension fields)."""
    anchors = {}
    for e in entries:
        m = re.match(r"&([\w.\-]+)\s*(.*)$", e["raw"] or "")
        if m:
            anchors[m.group(1)] = e["path"]
            if m.group(2):
                e["value"] = unquote(m.group(2))
    if not anchors:
        return entries
    out = list(entries)
    for e in entries:
        m = re.match(r"\*([\w.\-]+)$", (e["raw"] or "").strip())
        if not m or m.group(1) not in anchors:
            continue
        src = anchors[m.group(1)]
        dest = e["path"][:-1] if e["key"] == "<<" else e["path"]
        for x in entries:
            if x["path"][:len(src)] == src and len(x["path"]) > len(src):
                np = dest + x["path"][len(src):]
                if not any(y["path"] == np for y in out):
                    out.append(dict(x, path=np, line=e["line"]))
    return out


KEY_RE = re.compile(r"""^(?P<key>"[^"]+"|'[^']+'|[^\s'"\-\[{][^:]*?|-[^\s][^:]*?)\s*:(?:\s+(?P<val>.*))?$""")


def yaml_docs(lines):
    docs, entries = [], []
    stack = [(-1, ())]
    counters = {}
    block = None  # (indent, entry)
    for idx, raw in enumerate(lines, start=1):
        if block is not None:
            ind = len(raw) - len(raw.lstrip(" "))
            if not raw.strip() or ind > block[0]:
                block[1]["block"].append(raw)
                continue
            block = None
        if raw.strip() in ("---", "...") or raw.startswith("--- "):
            if entries:
                docs.append(entries)
            entries, stack, counters = [], [(-1, ())], {}
            continue
        line = strip_comment(raw.replace("\t", "  "))
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ()
        while content.startswith("- ") or content == "-":
            key = (len(docs), parent)
            counters[key] = counters.get(key, -1) + 1
            item_path = parent + ("[%d]" % counters[key],)
            stack.append((indent, item_path))
            rest = content[1:].lstrip()
            indent = indent + (len(content) - len(rest))
            parent = item_path
            content = rest
            if not content:
                break
        if not content:
            continue
        m = KEY_RE.match(content)
        if m and not content.startswith(("'", '"')) or (m and re.match(r"""^("[^"]+"|'[^']+')\s*:""", content)):
            key = unquote(m.group("key"))
            val = (m.group("val") or "").strip()
            entry = {"line": idx, "path": parent + (key,), "key": key, "value": unquote(val), "raw": val, "block": []}
            entries.append(entry)
            if val[:1] in ("|", ">") and re.fullmatch(r"[|>][+-]?\d*", val):
                block = (indent, entry)
            elif val == "" or val.startswith("&"):
                stack.append((indent, parent + (key,)))
                fv = re.sub(r"^&[\w.\-]+\s*", "", val)
                if fv[:1] in ("{", "["):
                    entries.extend(flow_entries(fv, parent + (key,), idx))
            elif val[:1] in ("{", "[") and val[-1:] in ("}", "]"):
                entry["value"] = ""
                entries.extend(flow_entries(val, parent + (key,), idx))
            continue
        entries.append({"line": idx, "path": parent, "key": None, "value": unquote(content), "raw": content, "block": []})
    if entries:
        docs.append(entries)
    return [resolve_aliases(d) for d in docs]


def under(entries, prefix):
    n = len(prefix)
    return [e for e in entries if e["path"][:n] == prefix]


def children(entries, prefix):
    """Distinct immediate child keys under prefix (ordered)."""
    n, seen = len(prefix), []
    for e in entries:
        if e["path"][:n] == prefix and len(e["path"]) > n and e["path"][n] not in seen:
            seen.append(e["path"][n])
    return seen


def find(entries, path):
    for e in entries:
        if e["path"] == path:
            return e
    return None


def text_of(e):
    return (e["value"] + "\n" + "\n".join(e["block"])) if e else ""


# --------------------------------------------------------------------------
# Compose
# --------------------------------------------------------------------------
def audit_compose(rep, path):
    lines = read_lines(path)
    rep.load_suppressions(path, lines)
    rep.scanned["compose"].append(rep.rel(path))
    scan_secret_values(rep, path, lines)
    docs = yaml_docs(lines)
    if not docs:
        return
    e = docs[0]
    v = find(e, ("version",))
    if v:
        rep.add("VS-C090", path, v["line"])
    fname = os.path.basename(path).lower()
    is_dev = any(x in fname for x in ("override", "dev", "local", "test", "ci"))
    services = children(e, ("services",))
    no_hc, no_restart, no_limits, no_logging = [], [], [], []
    has_x_logging = any(p for p in children(e, ()) if str(p).startswith("x-")) and "logging" in "\n".join(lines)
    hc_services = set()
    for s in services:
        if find(e, ("services", s, "healthcheck")) or find(e, ("services", s, "healthcheck", "test")):
            hc_services.add(s)
    for s in services:
        se = under(e, ("services", s))
        sline = next((x["line"] for x in e if x["path"] == ("services", s)), se[0]["line"] if se else None)
        get = lambda *p: find(e, ("services", s) + p)
        priv = get("privileged")
        if priv and priv["value"].lower() == "true":
            rep.add("VS-C001", path, priv["line"], s)
        for x in under(e, ("services", s, "volumes")):
            val = x["value"] or ""
            src = find(e, x["path"][:-1] + ("source",)) if x["key"] == "source" else None
            if "docker.sock" in val or (src and "docker.sock" in src["value"]):
                rep.add("VS-C002", path, x["line"], s)
        for k in ("network_mode", "pid", "ipc"):
            x = get(k)
            if x and x["value"] == "host":
                rep.add("VS-C003", path, x["line"], "%s: %s: host" % (s, k))
        for x in under(e, ("services", s, "cap_add")):
            if x["value"].upper().replace("CAP_", "") in ("SYS_ADMIN", "ALL", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE", "DAC_READ_SEARCH"):
                rep.add("VS-C004", path, x["line"], "%s: %s" % (s, x["value"]))
        for x in under(e, ("services", s, "security_opt")):
            if "unconfined" in x["value"]:
                rep.add("VS-C005", path, x["line"], s)
        img = get("image")
        build = get("build") or get("build", "context")
        if img and not build:
            name, tag, digest = split_image(img["value"])
            if "$" not in img["value"] and not digest and (tag in (None, "latest")):
                rep.add("VS-C010", path, img["line"], "%s: %s" % (s, img["value"]))
        # environment: map or list form
        for x in under(e, ("services", s, "environment")):
            if x["key"] is not None and len(x["path"]) == 4:
                k, val = x["key"], x["value"]
            elif x["key"] is None:
                if "=" not in x["value"]:
                    continue
                k, val = x["value"].split("=", 1)
            else:
                continue
            if SECRET_NAME_RE.search(k) and not SECRET_NAME_SAFE.search(k) and val and not is_placeholder(val):
                rep.add("VS-C020", path, x["line"], "%s: %s" % (s, k))
        for x in under(e, ("services", s, "ports")):
            if x["key"] not in (None, "published", "target", "host_ip"):
                continue
            if x["key"] == "published" or x["key"] is None:
                spec = x["value"]
                if x["key"] == "published":
                    tgt = find(e, x["path"][:-1] + ("target",))
                    hip = find(e, x["path"][:-1] + ("host_ip",))
                    if hip and hip["value"] in ("127.0.0.1", "::1", "localhost"):
                        continue
                    spec = "%s:%s" % (spec, tgt["value"] if tgt else "")
                parts = spec.split("/")[0].split(":")
                if len(parts) >= 3 and parts[0] in ("127.0.0.1", "localhost", "[::1]", "::1"):
                    continue
                container_port = parts[-1]
                if len(parts) >= 2 and (container_port in DB_PORTS or parts[-2] in DB_PORTS):
                    image_v = (img["value"] if img else "").lower()
                    if any(db in image_v for db in ("postgres", "mysql", "mariadb", "mongo", "redis", "valkey", "rabbit",
                                                     "elastic", "opensearch", "kafka", "memcached", "clickhouse", "neo4j",
                                                     "cockroach", "qdrant", "mssql", "oracle", "minio")) or container_port in ("5432", "3306", "6379", "27017"):
                        rep.add("VS-C030", path, x["line"], "%s: %s" % (s, spec))
        if s not in hc_services:
            image_v = (img["value"] if img else "")
            if not any(t in image_v for t in ("mailpit", "mailhog", "adminer", "pgadmin")):
                no_hc.append(s)
        dep = under(e, ("services", s, "depends_on"))
        for x in dep:
            if x["key"] is None and len(x["path"]) == 4 and x["value"] in hc_services:
                rep.add("VS-C041", path, x["line"], "%s -> %s" % (s, x["value"]))
        if not get("restart") and not find(e, ("services", s, "deploy", "restart_policy")) and \
                not (get("profiles")) and not any(x["key"] == "condition" and "completed" in x["value"] for x in dep):
            cmd = text_of(get("command")) if get("command") else ""
            if not re.search(r"\b(migrate|seed|init|setup)\b", cmd + s):
                no_restart.append(s)
        if not (get("mem_limit") or get("cpus") or find(e, ("services", s, "deploy", "resources", "limits")) or
                find(e, ("services", s, "deploy", "resources", "limits", "memory"))):
            no_limits.append(s)
        if not get("logging") and not has_x_logging:
            no_logging.append(s)
        if img:
            iname = short_name(split_image(img["value"])[0])
            data_dir = STATEFUL_IMAGES.get(iname) or STATEFUL_IMAGES.get(iname.rsplit("/", 1)[-1])
            if data_dir and not under(e, ("services", s, "volumes")) and not get("tmpfs"):
                rep.add("VS-C060", path, img["line"], "%s (%s)" % (s, data_dir))
            pg = re.match(r"(?:postgres|postgis/postgis|pgvector/pgvector|timescale/timescaledb)$", iname)
            ptag = split_image(img["value"])[1] or ""
            m = re.match(r"(?:pg)?(\d+)", ptag)
            if pg and m and int(m.group(1)) >= 18:
                for x in under(e, ("services", s, "volumes")):
                    if re.search(r":/var/lib/postgresql/data/?(:|$)", x["value"]) or (x["key"] == "target" and x["value"].rstrip("/") == "/var/lib/postgresql/data"):
                        rep.add("VS-C061", path, x["line"], "%s: %s" % (s, x["value"]))
        if not is_dev:
            for x in under(e, ("services", s, "volumes")):
                if x["key"] is None and re.match(r"^\.{1,2}(/[^:]*)?:/", x["value"]) and not x["value"].endswith(":ro") \
                        and not re.search(r"\.(conf|ya?ml|json|toml|ini|sql|sh|cfg|caddyfile)(:|$)", x["value"], re.I) \
                        and "Caddyfile" not in x["value"] and "nginx" not in x["value"]:
                    rep.add("VS-C070", path, x["line"], "%s: %s" % (s, x["value"]))
        cn = get("container_name")
        if cn and not (img and short_name(split_image(img["value"])[0]).rsplit("/", 1)[-1] in STATEFUL_IMAGES):
            rep.add("VS-C080", path, cn["line"], s)
        del sline
    if services:
        if no_hc:
            rep.add("VS-C040", path, None, ", ".join(no_hc))
        if no_restart and not is_dev:
            rep.add("VS-C050", path, None, ", ".join(no_restart))
        if no_limits and not is_dev:
            rep.add("VS-C051", path, None, ", ".join(no_limits))
        if no_logging and not is_dev and len(no_logging) == len(services):
            rep.add("VS-C052", path, None)


# --------------------------------------------------------------------------
# GitHub Actions
# --------------------------------------------------------------------------
UNTRUSTED_CTX = re.compile(
    r"\$\{\{\s*(github\.event\.(issue\.(title|body)|pull_request\.(title|body|head\.ref|head\.label|head\.repo\.default_branch)|"
    r"comment\.body|review\.body|review_comment\.body|discussion\.(title|body)|pages\.[^}]*\.page_name|"
    r"head_commit\.(message|author\.(name|email))|commits\.[^}]*\.(message|author\.(name|email))|"
    r"workflow_run\.(head_branch|head_commit\.message|display_title))|github\.head_ref)\s*\}\}")
DEPRECATED_ACTIONS = {
    "actions-rs/": "actions-rs is archived - use dtolnay/rust-toolchain",
    "actions/create-release": "archived - use softprops/action-gh-release or 'gh release create'",
    "actions/upload-release-asset": "archived - use 'gh release upload'",
    "actions/setup-ruby": "archived - use ruby/setup-ruby",
    "docker/build-push-action@v1": "v1 is obsolete - use a current major",
}
OLD_MAJORS = {"actions/checkout": 4, "actions/setup-node": 4, "actions/setup-python": 5, "actions/setup-go": 5,
              "actions/setup-java": 4, "actions/cache": 4, "actions/upload-artifact": 4, "actions/download-artifact": 4,
              "docker/login-action": 3, "docker/build-push-action": 5, "docker/setup-buildx-action": 3,
              "docker/metadata-action": 5, "github/codeql-action/upload-sarif": 3, "github/codeql-action/init": 3,
              "github/codeql-action/analyze": 3}


def audit_github_workflow(rep, path, repo_state):
    lines = read_lines(path)
    rep.load_suppressions(path, lines)
    rep.scanned["github_workflows"].append(rep.rel(path))
    scan_secret_values(rep, path, lines)
    docs = yaml_docs(lines)
    if not docs:
        return
    e = docs[0]
    full = "\n".join(lines)
    triggers = text_of(find(e, ("on",))) + " " + " ".join(str(c) for c in children(e, ("on",)))
    triggers += " " + " ".join(x["value"] for x in under(e, ("on",)) if x["key"] is None)
    if re.search(r"\b(scan|trivy|grype|snyk|osv-scanner|npm audit|pip-audit|govulncheck|cargo audit|bundler-audit|"
                 r"dependency-review-action|codeql|anchore|docker scout|safety check)\b", full, re.I):
        repo_state["scanning"] = True

    # uses: pinning / deprecations
    for x in e:
        if x["key"] != "uses":
            continue
        ref = x["value"].strip()
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        action, _, version = ref.partition("@")
        for prefix, why in DEPRECATED_ACTIONS.items():
            if ref.startswith(prefix) or action == prefix:
                rep.add("VS-G003", path, x["line"], "%s (%s)" % (ref, why))
        if re.fullmatch(r"[0-9a-f]{40}", version):
            continue
        if version in ("main", "master", "HEAD", "develop", "dev", "") or not re.match(r"v?\d", version):
            rep.add("VS-G002", path, x["line"], ref)
        else:
            rep.add("VS-G001", path, x["line"], ref)
            m = re.match(r"v?(\d+)", version)
            if m and action in OLD_MAJORS and int(m.group(1)) < OLD_MAJORS[action]:
                rep.add("VS-G003", path, x["line"], "%s is an outdated major (deprecated Node runtime)" % ref)
    for i, line in enumerate(lines, start=1):
        if re.search(r"::(set-output|save-state|set-env|add-path)\b", line):
            rep.add("VS-G003", path, i, "'::set-output'-style commands are disabled - write to $GITHUB_OUTPUT/$GITHUB_ENV")
        if "ACTIONS_ALLOW_UNSECURE_COMMANDS" in line:
            rep.add("VS-G022", path, i)

    # pull_request_target + checkout of PR head
    if "pull_request_target" in triggers:
        for x in e:
            if x["key"] == "ref" and re.search(r"github\.event\.pull_request\.head\.(sha|ref)|github\.head_ref|refs/pull/", x["value"]):
                rep.add("VS-G010", path, x["line"])

    # script injection in run: blocks and github-script
    for x in e:
        if x["key"] in ("run", "script"):
            body = text_of(x)
            for off, bl in enumerate([x["value"]] + x["block"]):
                if UNTRUSTED_CTX.search(bl):
                    rep.add("VS-G011", path, x["line"] + off if off else x["line"], UNTRUSTED_CTX.search(bl).group(1))
            del body

    # permissions
    top_perm = find(e, ("permissions",))
    jobs = children(e, ("jobs",))
    job_perm = [j for j in jobs if find(e, ("jobs", j, "permissions"))]
    if top_perm is None and len(job_perm) < len(jobs):
        rep.add("VS-G020", path, None)
    for x in e:
        if x["key"] == "permissions" and x["value"] in ("write-all",):
            rep.add("VS-G021", path, x["line"])

    # literal credentials
    for x in e:
        if x["key"] and re.search(r"(password|passwd|token|api[_-]?key|secret|access[_-]?key)$", x["key"], re.I) \
                and x["value"] and "${{" not in x["value"] and not is_placeholder(x["value"]) and \
                x["key"].lower() not in ("github-token", "token-format") and len(x["value"]) >= 6 and \
                x["path"][-2:-1] in (("with",), ("env",)):
            rep.add("VS-G030", path, x["line"], x["key"])

    builds = re.search(r"docker/build-push-action|docker (buildx )?build|docker push|buildah|kaniko|ko build|jib|"
                       r"spring-boot:build-image|pack build", full)
    pushes = re.search(r"push:\s*(true|\$\{\{)|docker push|--push\b|jib\b|ko build", full)
    deploys = re.search(r"\b(deploy|kubectl apply|helm upgrade|flyctl|railway up|render|vercel|netlify deploy|"
                        r"aws ecs|gcloud run deploy|az containerapp|kamal deploy|ssh-action|appleboy)\b", full, re.I)
    if (builds or deploys) and not repo_state.get("tests_anywhere_checked"):
        pass
    if builds or deploys:
        if not re.search(r"\b(test|pytest|jest|vitest|mocha|rspec|go test|cargo test|mvn( -B)? (verify|test)|gradlew? (test|check|build)|"
                         r"dotnet test|mix test|phpunit|pest|npm (run )?test|pnpm (run )?test|yarn test|bun test|playwright|cypress|"
                         r"rails test|tox|nox|make test|deno test|swift test|dart test|ctest|bundle exec rake)\b", full, re.I) \
                and not re.search(r"workflow_run|needs:\s*\[?\s*['\"]?test", full):
            rep.add("VS-G040", path, None)
    if pushes and re.search(r"\bpull_request\b", triggers) and "pull_request_target" not in triggers:
        gated = re.search(r"github\.event_name\s*!=\s*'pull_request'|github\.event_name\s*==\s*'push'|"
                          r"github\.ref\s*==\s*'refs/heads/|startsWith\(\s*github\.ref,\s*'refs/tags/|github\.event_name\s*!=\s*\"pull_request\"|"
                          r"github\.ref_name\s*==|github\.event_name\s*==\s*'release'|github\.ref_type\s*==\s*'tag'", full)
        if not gated:
            rep.add("VS-G041", path, None)
    if deploys and not find(e, ("concurrency",)) and not any(find(e, ("jobs", j, "concurrency")) for j in jobs) and \
            re.search(r"\b(deploy|kubectl apply|helm upgrade|flyctl|kamal|ssh-action)\b", full, re.I):
        rep.add("VS-G042", path, None)
    no_timeout = [j for j in jobs if not find(e, ("jobs", j, "timeout-minutes")) and not find(e, ("jobs", j, "uses"))]
    if no_timeout:
        rep.add("VS-G043", path, None, ", ".join(no_timeout))
    if re.search(r"docker/build-push-action", full) and pushes and not re.search(r"sbom:\s*true|provenance:\s*(mode=max|true)|cosign|attest-build-provenance", full):
        rep.add("VS-G051", path, None)


def audit_gitlab(rep, path, repo_state):
    lines = read_lines(path)
    rep.load_suppressions(path, lines)
    rep.scanned["gitlab_ci"].append(rep.rel(path))
    scan_secret_values(rep, path, lines)
    full = "\n".join(lines)
    if re.search(r"\b(trivy|grype|snyk|dependency_scanning|container_scanning|Security/|osv-scanner|npm audit|pip-audit)\b", full):
        repo_state["scanning"] = True
    docs = yaml_docs(lines)
    if not docs:
        return
    for x in docs[0]:
        if x["key"] in ("image", "name") and (x["key"] == "image" or x["path"][-2:-1] == ("image",)):
            v = x["value"]
            if not v or "$" in v:
                continue
            name, tag, digest = split_image(v)
            if not digest and tag in (None, "latest"):
                rep.add("VS-L001", path, x["line"], v)
        if x["key"] and SECRET_NAME_RE.search(x["key"]) and not SECRET_NAME_SAFE.search(x["key"]) and \
                x["path"][-2:-1] == ("variables",) and x["value"] and not is_placeholder(x["value"]):
            rep.add("VS-L002", path, x["line"], x["key"])


# --------------------------------------------------------------------------
# Kubernetes
# --------------------------------------------------------------------------
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job", "CronJob"}


def audit_k8s(rep, path):
    lines = read_lines(path)
    if any("{{" in l for l in lines):
        rep.scanned["skipped"].append(rep.rel(path) + " (templated)")
        return
    docs = yaml_docs(lines)
    kinds = [find(d, ("kind",)) for d in docs]
    if not any(k and (k["value"] in WORKLOADS or k["value"] == "Secret") for k in kinds):
        return
    rep.load_suppressions(path, lines)
    rep.scanned["kubernetes"].append(rep.rel(path))
    scan_secret_values(rep, path, lines)
    fname = os.path.basename(path).lower()
    for d, k in zip(docs, kinds):
        if not k:
            continue
        kind = k["value"]
        if kind == "Secret":
            data = [x for x in d if x["path"][:1] in (("data",), ("stringData",)) and len(x["path"]) == 2 and x["value"]]
            real = [x for x in data if not is_placeholder(x["value"]) and not re.fullmatch(r"(base64|BASE64).*|Y2hhbmdlbWU=?|", x["value"])]
            if real and not any(t in fname for t in ("example", "sample", "template", "dummy")):
                rep.add("VS-K041", path, k["line"])
            continue
        if kind not in WORKLOADS:
            continue
        spec = {"Pod": ("spec",), "CronJob": ("spec", "jobTemplate", "spec", "template", "spec")}.get(kind, ("spec", "template", "spec"))
        if kind == "Deployment":
            r = find(d, ("spec", "replicas"))
            if r and r["value"] == "1" and not find(d, ("metadata", "annotations")):
                rep.add("VS-K050", path, r["line"])
        for hk in ("hostNetwork", "hostPID", "hostIPC"):
            x = find(d, spec + (hk,))
            if x and x["value"] == "true":
                rep.add("VS-K035", path, x["line"], hk)
        for x in under(d, spec + ("volumes",)):
            if x["key"] == "hostPath":
                rep.add("VS-K036", path, x["line"])
        pod_nonroot = find(d, spec + ("securityContext", "runAsNonRoot"))
        pod_user = find(d, spec + ("securityContext", "runAsUser"))
        pod_seccomp = find(d, spec + ("securityContext", "seccompProfile", "type"))
        for group in ("containers", "initContainers"):
            for c in children(d, spec + (group,)):
                cp = spec + (group, c)
                name = find(d, cp + ("name",))
                cname = name["value"] if name else c
                cline = name["line"] if name else k["line"]
                g = lambda *p: find(d, cp + p)
                img = g("image")
                if img and "$" not in img["value"]:
                    _, tag, digest = split_image(img["value"])
                    if not digest and tag in (None, "latest"):
                        rep.add("VS-K001", path, img["line"], "%s: %s" % (cname, img["value"]))
                if group == "containers":
                    if not g("resources", "limits") and not g("resources", "requests") and not g("resources", "limits", "memory"):
                        rep.add("VS-K010", path, cline, cname)
                    if kind not in ("Job", "CronJob"):
                        if not g("readinessProbe") and not under(d, cp + ("readinessProbe",)):
                            rep.add("VS-K020", path, cline, cname)
                        if not g("livenessProbe") and not under(d, cp + ("livenessProbe",)):
                            rep.add("VS-K021", path, cline, cname)
                pv = g("securityContext", "privileged")
                if pv and pv["value"] == "true":
                    rep.add("VS-K030", path, pv["line"], cname)
                ape = g("securityContext", "allowPrivilegeEscalation")
                if not ape or ape["value"] != "false":
                    rep.add("VS-K031", path, ape["line"] if ape else cline, cname)
                nr = g("securityContext", "runAsNonRoot") or pod_nonroot
                ru = g("securityContext", "runAsUser") or pod_user
                if (ru and ru["value"] == "0") or (not (nr and nr["value"] == "true") and not (ru and ru["value"] not in ("0", ""))):
                    rep.add("VS-K032", path, cline, cname)
                ro = g("securityContext", "readOnlyRootFilesystem")
                if not ro or ro["value"] != "true":
                    rep.add("VS-K033", path, cline, cname)
                drops = [x["value"].upper() for x in under(d, cp + ("securityContext", "capabilities", "drop"))]
                dropline = g("securityContext", "capabilities", "drop")
                if "ALL" not in drops and not (dropline and "ALL" in dropline["value"].upper()):
                    rep.add("VS-K034", path, cline, cname)
                if not pod_seccomp and not g("securityContext", "seccompProfile", "type"):
                    rep.add("VS-K051", path, cline, cname)
                for x in under(d, cp + ("env",)):
                    if x["key"] == "value":
                        nm = find(d, x["path"][:-1] + ("name",))
                        if nm and SECRET_NAME_RE.search(nm["value"]) and not SECRET_NAME_SAFE.search(nm["value"]) and \
                                x["value"] and not is_placeholder(x["value"]):
                            rep.add("VS-K040", path, x["line"], "%s: %s" % (cname, nm["value"]))


# --------------------------------------------------------------------------
# Discovery + repo-level checks
# --------------------------------------------------------------------------
def discover(root, excludes):
    found = {"dockerfiles": [], "compose": [], "github": [], "gitlab": [], "k8s": []}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if any(fnmatch.fnmatch(rel_dir, ex) or fnmatch.fnmatch(rel_dir + "/", ex) for ex in excludes):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(rel, ex) for ex in excludes):
                continue
            low = fn.lower()
            if fn in ("Dockerfile", "Containerfile") or fn.startswith("Dockerfile.") and not fn.endswith(".dockerignore") \
                    or (low.endswith(".dockerfile")):
                found["dockerfiles"].append(full)
            elif re.match(r"^(docker-)?compose([.\-][\w-]+)?\.ya?ml$", low):
                found["compose"].append(full)
            elif "/.github/workflows/" in "/" + rel and low.endswith((".yml", ".yaml")):
                found["github"].append(full)
            elif low == ".gitlab-ci.yml" or (low.endswith((".yml", ".yaml")) and "/.gitlab/ci/" in "/" + rel):
                found["gitlab"].append(full)
            elif low.endswith((".yml", ".yaml")) and "/.github/" not in "/" + rel:
                head = "\n".join(read_lines(full)[:60])
                if re.search(r"^apiVersion:", head, re.M) and re.search(r"^kind:", head, re.M):
                    found["k8s"].append(full)
    return found


def repo_checks(rep, root):
    env = os.path.join(root, ".env")
    if not os.path.exists(env):
        return
    gi = "\n".join(read_lines(os.path.join(root, ".gitignore")))
    ignored = bool(re.search(r"^/?\.env(\*|\.\*|\b)?\s*$", gi, re.M)) or bool(re.search(r"^\*\.env\s*$|^\.env\*\s*$", gi, re.M))
    tracked = False
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", ".env"], capture_output=True, text=True, timeout=5)
        tracked = out.returncode == 0
        if not ignored:
            chk = subprocess.run(["git", "-C", root, "check-ignore", "-q", ".env"], capture_output=True, timeout=5)
            ignored = chk.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    if tracked:
        rep.add("VS-R002", env, None)
    elif not ignored:
        rep.add("VS-R001", env, None)


def run_audit(root, ignore=(), excludes=()):
    root = os.path.abspath(root)
    rep = Report(root if os.path.isdir(root) else os.path.dirname(root), ignore)
    if os.path.isfile(root):  # single-file mode
        f = root
        low = os.path.basename(f).lower()
        state = {}
        if "dockerfile" in low or low == "containerfile":
            audit_dockerfile(rep, f)
        elif "compose" in low:
            audit_compose(rep, f)
        elif low == ".gitlab-ci.yml":
            audit_gitlab(rep, f, state)
        elif "/.github/workflows/" in f.replace(os.sep, "/"):
            audit_github_workflow(rep, f, state)
        else:
            audit_k8s(rep, f)
        return rep
    found = discover(root, excludes)
    state = {"scanning": False}
    for f in found["dockerfiles"]:
        audit_dockerfile(rep, f)
    for f in found["compose"]:
        audit_compose(rep, f)
    for f in found["github"]:
        audit_github_workflow(rep, f, state)
    for f in found["gitlab"]:
        audit_gitlab(rep, f, state)
    for f in found["k8s"]:
        audit_k8s(rep, f)
    for p in (".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", ".github/renovate.json", "renovate.json5", ".renovaterc"):
        if os.path.exists(os.path.join(root, p)):
            state["scanning"] = True
    if (found["github"] or found["gitlab"]) and not state["scanning"]:
        rep.add("VS-G050", None, None)
    repo_checks(rep, root)
    return rep


# --------------------------------------------------------------------------
# Scoring + output
# --------------------------------------------------------------------------
def score(findings):
    s = 100
    for f in findings:
        s -= PENALTY[f["severity"]]
    s = max(0, s)
    grade = "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 70 else "D" if s >= 60 else "F"
    sevs = {f["severity"] for f in findings}
    if "CRITICAL" in sevs and grade in "ABC":
        grade = "D"
    elif "HIGH" in sevs and grade == "A":
        grade = "B"
    return s, grade


def counts(findings):
    c = {s: 0 for s in SEVERITIES}
    for f in findings:
        c[f["severity"]] += 1
    return c


def sort_findings(fs):
    return sorted(fs, key=lambda f: (SEV_RANK[f["severity"]], f["file"], f["line"] or 0, f["rule"]))


def out_text(rep, findings, s, grade):
    sc = rep.scanned
    c = counts(findings)
    bar = "=" * 68
    print(bar)
    print("VIBE-SHIP AUDIT  v%s   score %d/100  grade %s" % (VERSION, s, grade))
    print(bar)
    print("Scanned: %d Dockerfile(s), %d .dockerignore, %d compose, %d GitHub workflow(s), %d GitLab CI, %d k8s manifest(s)"
          % (len(sc["dockerfiles"]), len(sc["dockerignore"]), len(sc["compose"]), len(sc["github_workflows"]),
             len(sc["gitlab_ci"]), len(sc["kubernetes"])))
    if sc["skipped"]:
        print("Skipped (templated): %s" % ", ".join(sc["skipped"]))
    print("  ".join("%s: %d" % (k, v) for k, v in c.items()))
    print("-" * 68)
    if not findings:
        print("No issues found.")
    for f in findings:
        loc = "%s:%s" % (f["file"], f["line"]) if f["line"] else f["file"]
        print("[%s] %s  %s" % (f["severity"], f["rule"], loc))
        print("  Issue: %s" % f["message"])
        print("  Fix:   %s" % f["fix"])
        print()
    print(bar)


def out_markdown(rep, findings, s, grade):
    c = counts(findings)
    print("## vibe-ship audit: %d/100 (grade %s)\n" % (s, grade))
    print("| " + " | ".join(SEVERITIES) + " |")
    print("|" + "---|" * len(SEVERITIES))
    print("| " + " | ".join(str(c[x]) for x in SEVERITIES) + " |\n")
    if not findings:
        print("No issues found.")
        return
    print("| Severity | Rule | Location | Issue | Fix |")
    print("|---|---|---|---|---|")
    for f in findings:
        loc = "`%s:%s`" % (f["file"], f["line"]) if f["line"] else "`%s`" % f["file"]
        print("| %s | %s | %s | %s | %s |" % (f["severity"], f["rule"], loc, f["message"].replace("|", "\\|"), f["fix"].replace("|", "\\|")))


def out_sarif(rep, findings):
    level = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note", "INFO": "note"}
    secsev = {"CRITICAL": "9.5", "HIGH": "7.5", "MEDIUM": "5.0", "LOW": "3.0", "INFO": "1.0"}
    used = sorted({f["rule"] for f in findings})
    rules = []
    for rid in used:
        sev, cat, title, fix = RULES[rid]
        rules.append({"id": rid, "name": rid.replace("-", ""), "shortDescription": {"text": title},
                      "fullDescription": {"text": title}, "help": {"text": fix, "markdown": fix},
                      "defaultConfiguration": {"level": level[sev]},
                      "properties": {"tags": [cat, "deployment"], "security-severity": secsev[sev], "problem.severity": level[sev]}})
    results = []
    for f in findings:
        results.append({"ruleId": f["rule"], "ruleIndex": used.index(f["rule"]), "level": level[f["severity"]],
                        "message": {"text": "%s. Fix: %s" % (f["message"], f["fix"])},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": f["file"] if f["file"] != "." else ".gitignore"},
                                                            "region": {"startLine": f["line"] or 1}}}]})
    print(json.dumps({"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
                      "runs": [{"tool": {"driver": {"name": "vibe-ship", "version": VERSION,
                                                    "informationUri": "https://github.com/sudais-khalid/vibe-ship", "rules": rules}},
                                "results": results}]}, indent=2))


def list_rules():
    print("| Rule | Severity | Category | Check | Fix |")
    print("|---|---|---|---|---|")
    for rid in sorted(RULES, key=lambda r: (r[3], r)):
        sev, cat, title, fix = RULES[rid]
        print("| %s | %s | %s | %s | %s |" % (rid, sev, cat, title, fix.replace("|", "\\|")))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit Dockerfiles, Compose, CI pipelines and Kubernetes manifests")
    ap.add_argument("--path", default=".", help="Project root (or a single file) to scan")
    ap.add_argument("--format", choices=["text", "json", "sarif", "markdown"], default="text")
    ap.add_argument("--json", action="store_true", help="Shorthand for --format json")
    ap.add_argument("--fail-on", choices=[s.lower() for s in SEVERITIES] + ["none"], default="none",
                    help="Exit 1 if any finding at or above this severity")
    ap.add_argument("--min-severity", choices=[s.lower() for s in SEVERITIES], default="info",
                    help="Hide findings below this severity")
    ap.add_argument("--ignore", default="", help="Comma-separated rule IDs to skip")
    ap.add_argument("--exclude", action="append", default=[], help="Glob of paths to skip (repeatable)")
    ap.add_argument("--list-rules", action="store_true", help="Print the rule catalog as markdown and exit")
    args = ap.parse_args(argv)

    if args.list_rules:
        list_rules()
        return 0
    if not os.path.exists(args.path):
        print("Path not found: %s" % args.path, file=sys.stderr)
        return 2
    fmt = "json" if args.json else args.format
    rep = run_audit(args.path, [x.strip() for x in args.ignore.split(",") if x.strip()], args.exclude)
    findings = sort_findings([f for f in rep.findings if SEV_RANK[f["severity"]] <= SEV_RANK[args.min_severity.upper()]])
    s, grade = score(rep.findings)
    nothing = not any(rep.scanned[k] for k in ("dockerfiles", "compose", "github_workflows", "gitlab_ci", "kubernetes"))
    if fmt == "json":
        print(json.dumps({"tool": "vibe-ship audit", "version": VERSION, "score": s, "grade": grade,
                          "counts": counts(rep.findings), "scanned": rep.scanned, "findings": findings}, indent=2))
    elif fmt == "sarif":
        out_sarif(rep, findings)
    elif fmt == "markdown":
        out_markdown(rep, findings, s, grade)
    else:
        if nothing:
            print("No Dockerfile, compose file, CI pipeline or Kubernetes manifest found under: %s" % args.path)
        else:
            out_text(rep, findings, s, grade)
    if args.fail_on != "none":
        limit = SEV_RANK[args.fail_on.upper()]
        if any(SEV_RANK[f["severity"]] <= limit for f in rep.findings):
            return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
