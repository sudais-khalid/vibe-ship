# vibe-ship

A Claude Skill that turns any codebase into something deployable with `docker compose up` and shippable through CI/CD. Hardened by default, generated in one pass.

[![Listed on ClaudePluginHub](https://www.claudepluginhub.com/badge/sudais-khalid-vibe-ship)](https://www.claudepluginhub.com/plugins/sudais-khalid-vibe-ship?ref=badge)

Built by [Sudais Khalid](https://sudaiskhalid.com).

## What it does

Point Claude at any project and vibe-ship will:

- **Auto-detect the stack**: Node, Python, Go, Java/Kotlin, Ruby, Rust, PHP, or a static frontend, from files already in the repo (`package.json`, `requirements.txt`, `go.mod`, etc.)
- **Generate real files**, not advice:
  - Multi-stage, non-root `Dockerfile` with a health check
  - `.dockerignore`
  - `docker-compose.yml` with detected dependencies (Postgres, Redis, etc.), resource limits, and named volumes
  - `.env.example`
  - `.github/workflows/ci-cd.yml`: lint and test, then build, then push to GHCR, plus a dependency vulnerability scan
  - `DEPLOYMENT.md` summarizing everything and what is left to do manually
- **Audit an existing setup**: run `scripts/audit.py` against a project's Dockerfile, compose file, or CI config for a scored, static findings report. No Docker daemon needed.
- **Generate Kubernetes manifests on request**: Deployment, Service, and HPA with zero-downtime rolling updates, only when you actually need them

## Why

Most public deployment skills are markdown checklists. This one writes working files and includes an executable static analyzer, so it can review deployment setups it did not even generate.

## Install

This repo is built so the download works straight out of the box. No digging through nested folders.

### Fastest: Claude.ai or the Claude API

Just download **[vibe-ship.skill](./vibe-ship.skill)** from this repo and upload it directly in Settings under Capabilities and Skills (claude.ai), or through the Skills API. One file, one upload, done.

### Claude Code: download as zip

1. Click the green **Code** button at the top of this repo, then **Download ZIP**.
2. Extract it. You will get a folder like `vibe-ship-main`.
3. Rename that folder to `vibe-ship` and drop it straight into your skills directory:

```bash
cp -r vibe-ship-main ~/.claude/skills/vibe-ship
```

Project-scoped instead of personal? Copy it into `.claude/skills/vibe-ship` inside your repo instead.

Restart your Claude Code session, then run `/skills` to confirm it loaded.

### Claude Code: as a plugin

```
/plugin marketplace add sudais-khalid/vibe-ship
/plugin install vibe-ship@vibe-ship
```

### Claude Code: git clone

```bash
git clone https://github.com/sudais-khalid/vibe-ship.git
cp -r vibe-ship ~/.claude/skills/vibe-ship
```

## Usage

Just talk normally:

- "Dockerize this app"
- "Make this production-ready"
- "Vibe ship this"
- "Audit my existing Dockerfile"

The skill triggers automatically based on context. No special syntax needed.

## Try the audit script directly

```bash
python3 scripts/audit.py --path /path/to/your/project
```

## Structure

```
vibe-ship/
├── SKILL.md                    main workflow
├── scripts/
│   └── audit.py                static analyzer for existing setups
├── references/
│   ├── docker-patterns.md      Dockerfile templates per stack
│   ├── cicd-patterns.md        GitHub Actions templates per stack
│   ├── security-checklist.md
│   ├── scalability-checklist.md
│   ├── anti-patterns.md        concrete gotchas table
│   └── kubernetes.md           opt-in K8s manifests
├── vibe-ship.skill              packaged file, ready to upload
└── .claude-plugin/
    └── marketplace.json
```

Everything the skill needs (`SKILL.md`, `scripts/`, `references/`) sits at the top level of the repo, so a plain zip download is already a working skill folder.

## Contributing

Issues and PRs welcome. Especially additional stack support, cloud-specific CI/CD variants, and more audit rules.

## License

MIT. See [LICENSE](./LICENSE)
