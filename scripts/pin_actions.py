#!/usr/bin/env python3
"""
vibe-ship action pinner

Rewrites GitHub Actions `uses:` references from mutable tags to immutable
commit SHAs, keeping the human-readable version as a comment:

    uses: actions/checkout@v5      ->  uses: actions/checkout@<40-char sha> # v5.0.1

Resolution uses `git ls-remote` against the public action repository, so
no GitHub token or third-party package is needed. Re-running is safe: an
already-pinned ref is left alone unless --upgrade is passed.

Usage:
    python3 pin_actions.py .github/workflows            # pin in place
    python3 pin_actions.py ci.yml --dry-run             # show what would change
    python3 pin_actions.py .github/workflows --upgrade  # move to newest release (same major unless --major)
"""

import argparse
import os
import re
import subprocess
import sys

USES_RE = re.compile(r"^(?P<prefix>\s*-?\s*uses:\s*['\"]?)(?P<action>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[^@\s'\"]+)?)@(?P<ref>[^\s'\"#]+)(?P<quote>['\"]?)(?P<rest>.*)$")
SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_cache = {}


def ls_remote(repo):
    if repo in _cache:
        return _cache[repo]
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", "https://github.com/%s" % repo],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print("  ! %s: git ls-remote failed (%s)" % (repo, e), file=sys.stderr)
        _cache[repo] = {}
        return {}
    tags = {}
    for line in out.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):           # peeled annotated tag -> commit SHA wins
            tags[name[:-3]] = sha
        else:
            tags.setdefault(name, sha)
    _cache[repo] = tags
    return tags


def semver(tag):
    m = SEMVER_RE.match(tag)
    if not m:
        return None
    return tuple(int(x) if x else -1 for x in m.groups())


def resolve(action, ref, upgrade=False, allow_major=False):
    """Return (sha, version_label) or (None, reason)."""
    repo = "/".join(action.split("/")[:2])
    tags = ls_remote(repo)
    if not tags:
        return None, "no tags found"
    want = semver(ref)
    releases = sorted(((semver(t), t) for t in tags if semver(t) and semver(t)[2] >= 0), reverse=True)
    if upgrade and releases:
        if allow_major or want is None:
            v, t = releases[0]
        else:
            same = [(v, t) for v, t in releases if v[0] == want[0]]
            v, t = (same or releases)[0]
        return tags[t], t
    if ref in tags:
        sha = tags[ref]
        # Prefer the most specific release tag that points at the same commit (v5 -> v5.0.1).
        label = next((t for v, t in releases if tags[t] == sha), ref)
        return sha, label
    if want:
        cands = [(v, t) for v, t in releases if v[0] == want[0] and (want[1] < 0 or v[1] == want[1])]
        if cands:
            return tags[cands[0][1]], cands[0][1]
    return None, "ref %s not found" % ref


def process(path, dry_run, upgrade, allow_major):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    changed = 0
    for i, line in enumerate(lines):
        m = USES_RE.match(line)
        if not m or m.group("action").startswith(("./", "docker:")):
            continue
        action, ref = m.group("action"), m.group("ref")
        pinned = re.fullmatch(r"[0-9a-f]{40}", ref) is not None
        if pinned and not upgrade:
            continue
        if pinned:
            cm = re.search(r"#\s*(v?\d[\w.\-]*)", m.group("rest"))
            ref = cm.group(1) if cm else ref
        sha, label = resolve(action, ref, upgrade, allow_major)
        if not sha:
            print("  ! %s:%d %s@%s - %s (left unchanged)" % (path, i + 1, action, ref, label), file=sys.stderr)
            continue
        rest = re.sub(r"\s*#.*$", "", m.group("rest"))
        new = "%s%s@%s%s%s # %s" % (m.group("prefix"), action, sha, m.group("quote"), rest, label)
        if new != line:
            print("  %s:%d  %s@%s -> %s (%s)" % (path, i + 1, action, m.group("ref")[:12], sha[:12], label))
            lines[i] = new
            changed += 1
    if changed and not dry_run:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
    return changed


def main():
    ap = argparse.ArgumentParser(description="Pin GitHub Actions to commit SHAs")
    ap.add_argument("paths", nargs="+", help="Workflow files or directories (e.g. .github/workflows)")
    ap.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    ap.add_argument("--upgrade", action="store_true", help="Move to the newest release in the same major")
    ap.add_argument("--major", action="store_true", help="With --upgrade, allow jumping to a new major")
    args = ap.parse_args()
    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names) if n.endswith((".yml", ".yaml"))]
        elif os.path.isfile(p):
            files.append(p)
        else:
            print("Not found: %s" % p, file=sys.stderr)
            return 2
    total = sum(process(f, args.dry_run, args.upgrade, args.major) for f in files)
    print("%s %d reference(s) in %d file(s)." % ("Would pin" if args.dry_run else "Pinned", total, len(files)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
