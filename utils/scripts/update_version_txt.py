#!/usr/bin/env python3
"""
utils/scripts/update-version-txt.py

Reads the latest version from release-note.md and writes version.txt with:
  version_main: X.Y.Z
  version_dev: dev | X.Y.Z

Designed to be run as a post-release hook by python-semantic-release (or manually).
"""

from pathlib import Path
import re
import sys
import subprocess
import os
import tempfile

RELEASE_NOTE = Path("release-note.md")
OUT_FILE = Path("version.txt")


def extract_latest_version(changelog: Path) -> str:
    if not changelog.exists():
        raise FileNotFoundError(f"{changelog} not found")
    text = changelog.read_text(encoding="utf8")
    # match patterns like:
    # ## v2.3.0
    # ## [v2.3.0]
    # ## [2.3.0] 
    # or just the first semver anywhere
    m = re.search(r"##\s*\[?v?(\d+\.\d+\.\d+)\]?", text)
    if m:
        return m.group(1)
    # fallback: first semver anywhere
    m2 = re.search(r"\b[vV]?(\d+\.\d+\.\d+)\b", text)
    if m2:
        return m2.group(1)
    raise ValueError("Could not find semver (e.g. v2.3.0 or 2.3.0) in release-note.md")


def detect_branch() -> str:
    # Prefer CI provided variable
    github_ref = os.environ.get("GITHUB_REF")
    if github_ref:
        # refs/heads/<branch> or refs/tags/<tag>
        parts = github_ref.strip().split("/")
        if len(parts) >= 3 and parts[1] == "heads":
            return "/".join(parts[2:]) if len(parts) > 3 else parts[-1]
        # if it's a tag ref, return tag name
        return parts[-1]

    # Try git
    try:
        out = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL)
        branch = out.decode().strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass

    # Fallback to reading .git/HEAD
    head = Path(".git/HEAD")
    if head.exists():
        try:
            head_txt = head.read_text().strip()
            if head_txt.startswith("ref:"):
                return head_txt.split("/")[-1]
        except Exception:
            pass

    # last resort
    return "unknown"


def write_version_file(version: str, branch: str, out_file: Path):
    # dev_val: if branch is main use version, else use "dev"
    dev_val = version if branch == "main" else "dev"
    content = f"version_main: {version}\nversion_dev: {dev_val}\n"
    # atomic write
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf8") as tf:
        tf.write(content)
        tmpname = tf.name
    Path(tmpname).replace(out_file)
    print(f"✅ {out_file} updated (branch={branch}):\n{content}")


def main():
    try:
        version = extract_latest_version(RELEASE_NOTE)
    except Exception as e:
        print(f"ERROR: failed to extract version: {e}", file=sys.stderr)
        sys.exit(2)

    branch = detect_branch()
    try:
        write_version_file(version, branch, OUT_FILE)
    except Exception as e:
        print(f"ERROR: failed to write {OUT_FILE}: {e}", file=sys.stderr)
        sys.exit(3)

    # success
    return 0


if __name__ == "__main__":
    sys.exit(main())