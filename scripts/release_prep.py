"""Release helpers for .rwx/release.yml: version bump and CHANGELOG cut."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def pyproject_version(text: str) -> str:
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("pyproject.toml: version not found")
    return m.group(1)


def cut_changelog(text: str, version: str, today: str) -> str:
    if f"## [{version}]" in text:
        raise SystemExit(f"CHANGELOG already has [{version}]")
    marker = "## [Unreleased]"
    if marker not in text:
        raise SystemExit("CHANGELOG has no [Unreleased] header")
    return text.replace(marker, f"{marker}\n\n## [{version}] - {today}", 1)


def main() -> None:
    cmd, version = sys.argv[1], sys.argv[2]
    if not SEMVER.match(version):
        raise SystemExit(f"bad version: {version}")
    if cmd == "compare":
        cur = tuple(map(int, pyproject_version(Path("pyproject.toml").read_text()).split(".")))
        new = tuple(map(int, version.split(".")))
        print("equal" if cur == new else "bump" if cur < new else "older")
    elif cmd == "readme":
        p = Path("README.md")
        p.write_text(re.sub(r"(Alpha \(`)[^`]+(`)", rf"\g<1>{version}\g<2>", p.read_text(), count=1))
    elif cmd == "changelog":
        p = Path("CHANGELOG.md")
        p.write_text(cut_changelog(p.read_text(), version, date.today().isoformat()))
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
