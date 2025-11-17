#!/usr/bin/env python3
import re
import sys
import os
from datetime import datetime

VERSION_FILE = "version.txt"
RELEASE_NOTE_FILE = "release-note.md"

def read_version():
    with open(VERSION_FILE, 'r') as f:
        lines = f.readlines()

    main_ver_line = [line for line in lines if line.startswith("version_main:")][0]
    current_version = main_ver_line.split(":")[1].strip()
    return current_version, lines

def parse_commit_message(commit_msg):
    commit_msg = commit_msg.lower()
    if "rel:" in commit_msg or "release:" in commit_msg:
        return "major"
    elif "feat:" in commit_msg or "feature:" in commit_msg:
        return "minor"
    elif "fix:" in commit_msg or "bugfix:" in commit_msg:
        return "patch"
    else:
        return "patch"  # default to patch for safety

def bump_version(version_str, bump_type):
    major, minor, patch = map(int, version_str.split('.'))
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch
        return f"{major}.{minor}.{patch + 1}"

def generate_release_note_section(new_version, commit_subject, commit_body=""):
    today = datetime.now().strftime("%b %d  %Y")
    highlights = commit_body.strip() or commit_subject.strip()

    # Format bullet points if multiple lines
    if "\n" in highlights:
        bullets = "\n".join(f"- {line.strip()}" for line in highlights.split("\n") if line.strip())
    else:
        bullets = f"- {highlights}"

    note = f"""
## ➕ v{new_version} — {commit_subject}
📅 {today}  

### Highlights
{bullets}

---
"""
    return note

def update_version_file(lines, new_version):
    for i, line in enumerate(lines):
        if line.startswith("version_main:"):
            lines[i] = f"version_main: {new_version}\n"
            break
    with open(VERSION_FILE, 'w') as f:
        f.writelines(lines)

def prepend_to_release_notes(note_content):
    with open(RELEASE_NOTE_FILE, 'r') as f:
        content = f.read()

    insertion_point = content.find("\n---\n\n") + len("\n---\n\n")
    updated_content = content[:insertion_point] + note_content + content[insertion_point:]

    with open(RELEASE_NOTE_FILE, 'w') as f:
        f.write(updated_content)

def update_timeline_summary(new_version, subject):
    from datetime import datetime
    import re

    # Format date with non-breaking thin spaces (U+202F) to match existing style
    now = datetime.now()
    day = str(now.day)
    month = now.strftime("%b")
    year = str(now.year)
    today = f"{day}\u202f{month}\u202f{year}"
    timeline_entry = f"{today}\u202f→\u202fv{new_version}\u202f—\u202f{subject}"

    try:
        with open(RELEASE_NOTE_FILE, 'r', encoding='utf-8') as f:
            content = f.read()

        # Match actual header in your file
        marker = "## 📅 Timeline Summary (Visual Overview)"
        if marker not in content:
            print(f"⚠️ Timeline marker '{marker}' not found. Skipping timeline update.")
            return

        header_end = match.end()
        header = content[:header_end]
        rest = content[header_end:]

        # Find where timeline entries end (before first detailed release note)
        timeline_end_pos = rest.find("\n## ➕")
        if timeline_end_pos == -1:
            timeline_end_pos = len(rest)

        timeline_block = rest[:timeline_end_pos]
        after_block = rest[timeline_end_pos:]

        # Avoid duplicates
        if timeline_entry in timeline_block:
            print("⏭️ Timeline entry already exists. Skipping.")
            return

        # ✅ APPEND at BOTTOM (chronological order)
        if not timeline_block.endswith('\n'):
            timeline_block += '\n'
        timeline_block += timeline_entry + '\n'

        # Reconstruct
        updated_content = header + timeline_block + after_block

        with open(RELEASE_NOTE_FILE, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        print(f"📈 Timeline summary updated (appended at bottom): {timeline_entry}")

    except Exception as e:
        print(f"❌ Failed to update timeline: {e}")

def main(commit_message):
    current_version, version_lines = read_version()
    print(f"📥 Current version: {current_version}")
    bump_type = parse_commit_message(commit_message)

    # Split subject and body (conventional commits style)
    parts = commit_message.split('\n', 1)
    subject = parts[0].replace('feat:', '').replace('fix:', '').replace('chore:', '').replace('docs:', '').strip().capitalize()
    body = parts[1].strip() if len(parts) > 1 else ""

    new_version = bump_version(current_version, bump_type)
    print(f"📤 New version: {new_version} (bump: {bump_type})")
    print(f"🔖 Bumping version {current_version} → {new_version} ({bump_type})")

    update_version_file(version_lines, new_version)
    release_note = generate_release_note_section(new_version, subject, body)
    prepend_to_release_notes(release_note)
    update_timeline_summary(new_version, subject)
    print(f"💾 Updated {VERSION_FILE} and {RELEASE_NOTE_FILE}")

    # Output for GitHub Actions to use
    github_output = os.getenv('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a') as f:
            f.write(f"new_version={new_version}\n")
            f.write(f"bump_type={bump_type}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bump_version.py '<commit message>'")
        sys.exit(1)
    main(sys.argv[1])