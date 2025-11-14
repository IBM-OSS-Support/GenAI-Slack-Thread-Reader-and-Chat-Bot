import re
from pathlib import Path

def extract_latest_version(changelog: Path) -> str:
    text = changelog.read_text(encoding="utf8")
    match = re.search(r"##\s*\[?v?(\d+\.\d+\.\d+)\]?", text)
    if not match:
        raise ValueError("❌  Could not find version header in release-note.md")
    return match.group(1)

def write_version_file(version: str, branch: str):
    version_file = Path("version.txt")
    dev_val = "dev" if branch != "main" else version
    content = f"version_main: {version}\nversion_dev: {dev_val}\n"
    version_file.write_text(content, encoding="utf8")
    print(f"✅ version.txt updated:\n{content}")

def main():
    changelog = Path("release-note.md")
    version = extract_latest_version(changelog)
    head = Path(".git/HEAD").read_text() if Path(".git/HEAD").exists() else "refs/heads/dev"
    branch = head.strip().split("/")[-1]
    write_version_file(version, branch)

if __name__ == "__main__":
    main()