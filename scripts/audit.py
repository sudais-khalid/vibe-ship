#!/usr/bin/env python3
"""
vibe-deploy audit script

Statically scans a project for Dockerfile, docker-compose.yml, and
.github/workflows/*.yml, checks each against known anti-patterns and
best practices, and prints a scored findings report.

No Docker daemon or network access required - pure text/regex analysis.

Usage:
    python3 audit.py --path /path/to/project
    python3 audit.py --path /path/to/project --json
"""

import argparse
import json
import os
import re
import sys

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def finding(severity, file, line, message, fix):
    return {
        "severity": severity,
        "file": file,
        "line": line,
        "message": message,
        "fix": fix,
    }


def audit_dockerfile(path):
    findings = []
    with open(path, "r", errors="ignore") as f:
        lines = f.readlines()

    has_user = False
    has_healthcheck = False
    has_multistage = len(re.findall(r"^\s*FROM\s+.+\s+AS\s+\w+", "".join(lines), re.IGNORECASE | re.MULTILINE)) > 0
    copy_all_seen_before_install = False
    install_seen = False

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        if re.match(r"^FROM\s+\S+:latest", stripped, re.IGNORECASE):
            findings.append(finding(
                "HIGH", path, i,
                "Base image uses ':latest' tag - non-reproducible builds",
                "Pin an exact version, e.g. 'node:20-alpine' instead of 'node:latest'"
            ))
        if re.match(r"^FROM\s+\S+$", stripped, re.IGNORECASE) and ":" not in stripped.split()[1]:
            findings.append(finding(
                "MEDIUM", path, i,
                "Base image has no explicit tag (defaults to :latest)",
                "Add an explicit version tag to the base image"
            ))

        if re.match(r"^USER\s+", stripped, re.IGNORECASE):
            has_user = True
        if re.match(r"^HEALTHCHECK", stripped, re.IGNORECASE):
            has_healthcheck = True

        if re.match(r"^COPY\s+\.\s+\.", stripped) and not install_seen:
            copy_all_seen_before_install = True
        if re.search(r"(npm (ci|install)|pip install|bundle install|go mod download|cargo build)", stripped, re.IGNORECASE):
            install_seen = True

        secret_env_match = re.match(r"^(ENV|ARG)\s+.*(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)", stripped, re.IGNORECASE)
        if secret_env_match:
            findings.append(finding(
                "CRITICAL", path, i,
                f"Possible secret baked into image layer via {secret_env_match.group(1)}",
                "Never bake secrets into the image. Pass at runtime via env_file/secrets manager, or use BuildKit secret mounts for build-time secrets."
            ))

        apt_install_match = re.search(r"apt-get install", stripped, re.IGNORECASE)
        if apt_install_match and "rm -rf /var/lib/apt/lists" not in stripped:
            findings.append(finding(
                "LOW", path, i,
                "apt-get install without cache cleanup in the same RUN layer",
                "Add '&& rm -rf /var/lib/apt/lists/*' to the same RUN command"
            ))

    if not has_user:
        findings.append(finding(
            "CRITICAL", path, None,
            "No USER instruction found - container runs as root",
            "Add a non-root user and 'USER appuser' before CMD"
        ))
    if not has_healthcheck:
        findings.append(finding(
            "MEDIUM", path, None,
            "No HEALTHCHECK instruction found",
            "Add a HEALTHCHECK hitting a real endpoint so orchestrators can detect failures"
        ))
    if not has_multistage:
        findings.append(finding(
            "MEDIUM", path, None,
            "Single-stage build - build tools/dev dependencies may ship in the final image",
            "Use a multi-stage build: build stage + slim runtime stage"
        ))
    if copy_all_seen_before_install:
        findings.append(finding(
            "LOW", path, None,
            "'COPY . .' appears before dependency install, busting the Docker layer cache on every code change",
            "Copy only the lockfile/manifest first, install dependencies, then COPY the rest"
        ))

    return findings


def audit_compose(path):
    findings = []
    with open(path, "r", errors="ignore") as f:
        content = f.read()
        lines = content.splitlines()

    if re.search(r"5432:5432|3306:3306|6379:6379|27017:27017", content):
        findings.append(finding(
            "MEDIUM", path, None,
            "A database/cache port appears to be exposed directly to the host",
            "Only expose ports actually needed from outside the compose network"
        ))

    if not re.search(r"healthcheck", content, re.IGNORECASE):
        findings.append(finding(
            "LOW", path, None,
            "No healthcheck defined for any service",
            "Add a healthcheck block to the app service"
        ))

    if not re.search(r"mem_limit|resources:\s*\n\s*limits", content):
        findings.append(finding(
            "LOW", path, None,
            "No resource limits (mem_limit/cpus) set on services",
            "Add conservative mem_limit/cpus per service as a starting point"
        ))

    for i, line in enumerate(lines, start=1):
        if re.search(r"(PASSWORD|SECRET|TOKEN|API_KEY)\s*[:=]\s*['\"]?\w+", line, re.IGNORECASE) and "env_file" not in line:
            findings.append(finding(
                "CRITICAL", path, i,
                "Possible hardcoded credential in compose file",
                "Move to .env and reference via env_file, never commit real values"
            ))

    if not re.search(r"volumes:", content):
        findings.append(finding(
            "LOW", path, None,
            "No named volumes found - stateful services may lose data on restart",
            "Add named volumes for any database/cache data directories"
        ))

    return findings


def audit_ci_workflow(path):
    findings = []
    with open(path, "r", errors="ignore") as f:
        content = f.read()

    if re.search(r"password\s*:\s*['\"]?\w+['\"]?", content, re.IGNORECASE) and "secrets." not in content:
        findings.append(finding(
            "CRITICAL", path, None,
            "Possible literal credential in workflow file instead of a secrets reference",
            "Use ${{ secrets.NAME }} instead of literal values"
        ))

    if not re.search(r"(test|pytest|jest|rspec|go test|cargo test)", content, re.IGNORECASE):
        findings.append(finding(
            "MEDIUM", path, None,
            "No test step detected in the workflow",
            "Add a test step before build-and-push, and gate the latter on it passing"
        ))

    if re.search(r"docker/build-push-action|docker build", content) and "if:" not in content:
        findings.append(finding(
            "MEDIUM", path, None,
            "Build-and-push step doesn't appear gated to a specific branch",
            "Gate push behind 'if: github.ref == refs/heads/main' to avoid pushing throwaway images"
        ))

    if not re.search(r"trivy|snyk|grype|dependabot|npm audit|pip-audit", content, re.IGNORECASE):
        findings.append(finding(
            "LOW", path, None,
            "No dependency vulnerability scanning step detected",
            "Add a scan step (e.g. Trivy) - can start non-blocking"
        ))

    return findings


def find_files(root):
    dockerfiles, composes, workflows = [], [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "venv", ".venv")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn == "Dockerfile" or fn.startswith("Dockerfile."):
                dockerfiles.append(full)
            elif fn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                composes.append(full)
            elif dirpath.endswith(os.path.join(".github", "workflows")) and (fn.endswith(".yml") or fn.endswith(".yaml")):
                workflows.append(full)
    return dockerfiles, composes, workflows


def main():
    parser = argparse.ArgumentParser(description="Audit deployment files for security/scalability best practices")
    parser.add_argument("--path", default=".", help="Project root to scan")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of a formatted report")
    args = parser.parse_args()

    dockerfiles, composes, workflows = find_files(args.path)
    all_findings = []

    for d in dockerfiles:
        all_findings.extend(audit_dockerfile(d))
    for c in composes:
        all_findings.extend(audit_compose(c))
    for w in workflows:
        all_findings.extend(audit_ci_workflow(w))

    all_findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 99))

    if not dockerfiles and not composes and not workflows:
        print("No Dockerfile, docker-compose.yml, or GitHub Actions workflow found under: " + args.path)
        sys.exit(0)

    if args.json:
        print(json.dumps({
            "scanned": {"dockerfiles": dockerfiles, "compose_files": composes, "workflows": workflows},
            "findings": all_findings,
        }, indent=2))
        return

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in all_findings:
        counts[f["severity"]] += 1

    print("=" * 60)
    print("VIBE-SHIP AUDIT REPORT")
    print("=" * 60)
    print(f"Scanned: {len(dockerfiles)} Dockerfile(s), {len(composes)} compose file(s), {len(workflows)} workflow(s)")
    print(f"CRITICAL: {counts['CRITICAL']}  HIGH: {counts['HIGH']}  MEDIUM: {counts['MEDIUM']}  LOW: {counts['LOW']}")
    print("-" * 60)

    if not all_findings:
        print("No issues found. Clean bill of health.")
    else:
        for f in all_findings:
            loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
            print(f"[{f['severity']}] {loc}")
            print(f"  Issue: {f['message']}")
            print(f"  Fix:   {f['fix']}")
            print()

    print("=" * 60)


if __name__ == "__main__":
    main()
