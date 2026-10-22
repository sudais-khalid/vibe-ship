#!/usr/bin/env python3
"""Build vibe-ship.skill (zip) for Claude.ai / API upload: SKILL.md + scripts/ + references/ under vibe-ship/."""

import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "vibe-ship.skill")
INCLUDE = ["SKILL.md", "LICENSE", "scripts", "references"]


def main():
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            src = os.path.join(ROOT, item)
            paths = [src] if os.path.isfile(src) else [
                os.path.join(d, f) for d, _, files in os.walk(src) for f in files]
            for p in sorted(paths):
                if "__pycache__" in p or p.endswith(".pyc"):
                    continue
                arc = "vibe-ship/" + os.path.relpath(p, ROOT).replace(os.sep, "/")
                z.write(p, arc)
                count += 1
    print("wrote %s (%d files, %d KB)" % (OUT, count, os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
