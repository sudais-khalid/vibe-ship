"""Regression tests for scripts/audit.py. Run: python -m unittest discover -s tests"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import audit  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def rules_for(path, **kw):
    rep = audit.run_audit(path, **kw)
    return rep, {f["rule"] for f in rep.findings}


class TempProject:
    """Create a throwaway project from a {relative path: content} dict."""

    def __init__(self, files):
        self.files = files

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="vibe-ship-test-")
        for rel, content in self.files.items():
            full = os.path.join(self.dir, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
        return self.dir

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


DOCKERIGNORE = ".git\n.env\n.env.*\nnode_modules\n"


class FixtureTests(unittest.TestCase):
    def test_bad_fixture_catches_planted_issues(self):
        rep, rules = rules_for(os.path.join(FIX, "bad"))
        expected = {
            # Dockerfile
            "VS-D001", "VS-D011", "VS-D040", "VS-D041", "VS-D042", "VS-D052", "VS-D013", "VS-D014",
            "VS-D022", "VS-D030", "VS-D064", "VS-D080", "VS-D082", "VS-D100", "VS-D031", "VS-D034",
            # Compose
            "VS-C001", "VS-C002", "VS-C003", "VS-C010", "VS-C020", "VS-C030", "VS-C041", "VS-C060",
            "VS-C070", "VS-C090",
            # GitHub Actions
            "VS-G001", "VS-G002", "VS-G003", "VS-G010", "VS-G011", "VS-G020", "VS-G030", "VS-G040",
            # Kubernetes
            "VS-K001", "VS-K030", "VS-K032", "VS-K035", "VS-K036", "VS-K040", "VS-K041", "VS-K050",
            # Secrets
            "VS-S001",
        }
        missing = expected - rules
        self.assertFalse(missing, "rules not triggered: %s" % sorted(missing))
        # .env is "not ignored" before the fixture is committed and "tracked" after.
        self.assertTrue(rules & {"VS-R001", "VS-R002"})
        score, grade = audit.score(rep.findings)
        self.assertEqual(grade, "F")

    def test_good_fixture_is_clean(self):
        rep, _ = rules_for(os.path.join(FIX, "good"))
        noisy = [f for f in rep.findings if f["severity"] != "INFO"]
        self.assertEqual(noisy, [], json.dumps(noisy, indent=2))
        self.assertEqual(audit.score(rep.findings), (100, "A"))


class DockerfileTests(unittest.TestCase):
    def audit_df(self, dockerfile, extra=None):
        files = {"Dockerfile": dockerfile, ".dockerignore": DOCKERIGNORE}
        files.update(extra or {})
        with TempProject(files) as d:
            return rules_for(d)[1]

    def test_slim_image_curl_healthcheck_is_flagged(self):
        rules = self.audit_df(
            "FROM python:3.14-slim\nWORKDIR /app\nENV PYTHONUNBUFFERED=1\nRUN useradd -r app\nUSER app\n"
            "HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1\n"
            'CMD ["gunicorn", "app:app"]\n')
        self.assertIn("VS-D020", rules)

    def test_curl_installed_in_final_stage_is_fine(self):
        rules = self.audit_df(
            "FROM python:3.14-slim\nWORKDIR /app\nENV PYTHONUNBUFFERED=1\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*\n"
            "RUN useradd -r app\nUSER app\nHEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1\n"
            'CMD ["gunicorn", "app:app"]\n')
        self.assertNotIn("VS-D020", rules)

    def test_alpine_wget_healthcheck_is_fine(self):
        rules = self.audit_df(
            "FROM node:24-alpine\nWORKDIR /app\nUSER node\nHEALTHCHECK CMD wget -qO- http://127.0.0.1:3000/ || exit 1\n"
            'CMD ["node", "server.js"]\n')
        self.assertNotIn("VS-D020", rules)
        self.assertNotIn("VS-D011", rules)

    def test_user_inherited_through_stage_chain(self):
        rules = self.audit_df(
            "FROM node:24-alpine AS base\nWORKDIR /app\nUSER node\n"
            "FROM base AS runtime\nHEALTHCHECK CMD wget -qO- http://127.0.0.1:3000/ || exit 1\n"
            'CMD ["node", "server.js"]\n')
        self.assertNotIn("VS-D011", rules)
        self.assertNotIn("VS-D002", rules, "stage alias must not be treated as an untagged image")

    def test_distroless_nonroot_needs_no_user(self):
        rules = self.audit_df(
            "FROM golang:1.27-alpine AS build\nWORKDIR /src\nCOPY go.mod go.sum ./\nRUN go mod download\nCOPY . .\n"
            "RUN CGO_ENABLED=0 go build -o /out/app ./cmd/server\n"
            "FROM gcr.io/distroless/static-debian12:nonroot\nWORKDIR /app\nCOPY --from=build /out/app /app/app\n"
            'ENTRYPOINT ["/app/app"]\n')
        self.assertNotIn("VS-D011", rules)
        self.assertNotIn("VS-D031", rules)

    def test_shell_healthcheck_on_distroless_is_flagged(self):
        rules = self.audit_df(
            "FROM gcr.io/distroless/static-debian12:nonroot\nWORKDIR /app\nCOPY app /app/app\n"
            "HEALTHCHECK CMD /app/app health\nENTRYPOINT [\"/app/app\"]\n")
        self.assertIn("VS-D021", rules)

    def test_explicit_root_user(self):
        rules = self.audit_df("FROM node:24-alpine\nWORKDIR /app\nUSER root\nCMD [\"node\", \"x.js\"]\n")
        self.assertIn("VS-D010", rules)

    def test_line_continuations_and_heredoc(self):
        rules = self.audit_df(
            "FROM debian:trixie-slim\nWORKDIR /app\n"
            "RUN apt-get update \\\n    # comment inside continuation\n    && apt-get install -y \\\n      curl\n"
            "RUN <<EOF\nset -e\ncurl -fsSL https://x.example/install.sh | sh\nEOF\n"
            "USER 10001\nCMD [\"/bin/true\"]\n")
        self.assertIn("VS-D060", rules)
        self.assertIn("VS-D061", rules)
        self.assertIn("VS-D052", rules, "heredoc body must be analyzed")

    def test_build_arg_base_image(self):
        rules = self.audit_df("ARG NODE_VERSION=24.11\nFROM node:${NODE_VERSION}-alpine\nWORKDIR /app\nUSER node\n"
                              "CMD [\"node\", \"x.js\"]\n")
        self.assertNotIn("VS-D004", rules)
        self.assertNotIn("VS-D002", rules)
        rules = self.audit_df("ARG TAG\nFROM node:${TAG}\nWORKDIR /app\nUSER node\nCMD [\"node\", \"x.js\"]\n")
        self.assertIn("VS-D004", rules)

    def test_secret_env_placeholders_not_flagged(self):
        rules = self.audit_df("FROM node:24-alpine\nWORKDIR /app\nENV DB_PASSWORD_FILE=/run/secrets/db JWT_SECRET=${JWT_SECRET}\n"
                              "USER node\nCMD [\"node\", \"x.js\"]\n")
        self.assertNotIn("VS-D040", rules)

    def test_inline_suppression(self):
        rules = self.audit_df("FROM node:24-alpine\nWORKDIR /app\nUSER node\n"
                              "# vibe-ship: ignore VS-D080 wrapper script handles signals\nCMD node x.js\n")
        self.assertNotIn("VS-D080", rules)
        rules = self.audit_df("# vibe-ship: ignore-file VS-D022\nFROM node:24-alpine\nWORKDIR /app\nUSER node\nCMD [\"node\", \"x.js\"]\n")
        self.assertNotIn("VS-D022", rules)

    def test_dockerignore_gaps(self):
        with TempProject({"Dockerfile": "FROM node:24-alpine\n", ".dockerignore": "dist\n", "package.json": "{}"}) as d:
            rules = rules_for(d)[1]
        self.assertTrue({"VS-D101", "VS-D102", "VS-D103"} <= rules)

    def test_dev_server_command(self):
        rules = self.audit_df("FROM python:3.14-slim\nWORKDIR /app\nENV PYTHONUNBUFFERED=1\nUSER 1000\n"
                              'CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]\n')
        self.assertIn("VS-D085", rules)


class ComposeAndCITests(unittest.TestCase):
    def test_localhost_bound_db_port_ok(self):
        compose = ("services:\n  db:\n    image: postgres:18-alpine\n    ports:\n      - \"127.0.0.1:5432:5432\"\n"
                   "    volumes:\n      - pg:/var/lib/postgresql\nvolumes:\n  pg:\n")
        with TempProject({"compose.yaml": compose}) as d:
            rules = rules_for(d)[1]
        self.assertNotIn("VS-C030", rules)
        self.assertNotIn("VS-C060", rules)

    def test_anchors_merge_keys_and_flow_maps(self):
        compose = ("x-restart: &restart\n  restart: unless-stopped\nx-logging: &logging\n  driver: json-file\n"
                   "  options: { max-size: \"10m\" }\nservices:\n  api:\n    image: x/api:1.2\n    <<: *restart\n"
                   "    environment:\n      DATABASE_URL: postgres://${DB_USER:-app}:${DB_PASSWORD}@db:5432/app\n"
                   "    deploy: { resources: { limits: { memory: 256M } } }\n    logging: *logging\n"
                   "    depends_on: { db: { condition: service_healthy } }\n    healthcheck: { test: [\"CMD\", \"true\"] }\n"
                   "    cap_add: [SYS_ADMIN]\n  db:\n    image: postgres:18-alpine\n    <<: *restart\n"
                   "    volumes: [pg:/var/lib/postgresql/data]\n    deploy: { resources: { limits: { memory: 512M } } }\n"
                   "    logging: *logging\n    healthcheck:\n      test: [\"CMD-SHELL\", \"pg_isready\"]\nvolumes:\n  pg:\n")
        with TempProject({"compose.yaml": compose}) as d:
            rules = rules_for(d)[1]
        for rid in ("VS-C050", "VS-C051", "VS-C052", "VS-C040", "VS-S001", "VS-C060"):
            self.assertNotIn(rid, rules)
        self.assertIn("VS-C004", rules, "flow-sequence cap_add must be read")
        self.assertIn("VS-C061", rules, "flow-sequence volume at the pre-18 data dir")

    def test_list_form_env_secret(self):
        compose = "services:\n  api:\n    image: x/api:1.2\n    environment:\n      - API_KEY=abcd1234efgh\n      - LOG_LEVEL=info\n"
        with TempProject({"compose.yaml": compose}) as d:
            rules = rules_for(d)[1]
        self.assertIn("VS-C020", rules)

    def test_env_passed_through_is_safe(self):
        wf = ("name: t\non: [issues]\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
              "    timeout-minutes: 5\n    steps:\n      - env:\n          TITLE: ${{ github.event.issue.title }}\n"
              "        run: echo \"$TITLE\"\n")
        with TempProject({".github/workflows/t.yml": wf}) as d:
            rules = rules_for(d)[1]
        self.assertNotIn("VS-G011", rules)

    def test_unpinned_push_on_pr(self):
        wf = ("on:\n  pull_request:\n  push:\n    branches: [main]\npermissions:\n  contents: read\njobs:\n  b:\n"
              "    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n"
              "      - uses: docker/build-push-action@263435318d21b8e681c14492fe198d362a7d2c83\n        with:\n          push: true\n")
        with TempProject({".github/workflows/b.yml": wf}) as d:
            rules = rules_for(d)[1]
        self.assertIn("VS-G041", rules)
        self.assertNotIn("VS-G001", rules)

    def test_gitlab_latest_and_secret(self):
        gl = "image: node:latest\nvariables:\n  DEPLOY_TOKEN: abcdef123456\ntest:\n  script:\n    - npm test\n"
        with TempProject({".gitlab-ci.yml": gl}) as d:
            rules = rules_for(d)[1]
        self.assertTrue({"VS-L001", "VS-L002", "VS-G050"} <= rules)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "audit.py")] + list(args),
                              capture_output=True, text=True, encoding="utf-8")

    def test_fail_on_exit_code(self):
        self.assertEqual(self.run_cli("--path", os.path.join(FIX, "bad"), "--fail-on", "high").returncode, 1)
        self.assertEqual(self.run_cli("--path", os.path.join(FIX, "good"), "--fail-on", "low").returncode, 0)

    def test_sarif_is_valid_json(self):
        out = self.run_cli("--path", os.path.join(FIX, "bad"), "--format", "sarif")
        data = json.loads(out.stdout)
        self.assertEqual(data["version"], "2.1.0")
        results = data["runs"][0]["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1 for r in results))

    def test_list_rules(self):
        out = self.run_cli("--list-rules")
        self.assertIn("VS-D001", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_every_rule_has_valid_severity(self):
        for rid, (sev, cat, title, fix) in audit.RULES.items():
            self.assertIn(sev, audit.SEVERITIES, rid)
            self.assertTrue(title and fix and cat, rid)


if __name__ == "__main__":
    unittest.main()
