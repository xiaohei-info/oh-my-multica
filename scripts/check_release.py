#!/usr/bin/env python3
"""Fail a release when its public version facts disagree."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _match(path: Path, pattern: str, label: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"cannot find {label} in {path.relative_to(ROOT)}")
    return match.group(1)


def _project_version() -> str:
    return _match(
        ROOT / "pyproject.toml",
        r'^version\s*=\s*"([^"]+)"\s*$',
        "project version",
    )


def _package_version() -> str:
    return _match(
        ROOT / "src" / "omac" / "__init__.py",
        r'^__version__\s*=\s*"([^"]+)"\s*$',
        "package version",
    )


def check(tag: str) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return [f"tag {tag!r} must use the form vMAJOR.MINOR.PATCH"]

    tag_version = tag.removeprefix("v")
    project_version = _project_version()
    package_version = _package_version()

    if tag_version != project_version:
        errors.append(
            f"tag version {tag_version} does not match project version {project_version}"
        )
    if package_version != project_version:
        errors.append(
            f"package version {package_version} does not match project version {project_version}"
        )

    for readme in ("README.md", "README.zh-CN.md"):
        badge = f"version-{project_version}-blue"
        if badge not in (ROOT / readme).read_text(encoding="utf-8"):
            errors.append(f"{readme} does not contain the {project_version} version badge")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{project_version}]" not in changelog:
        errors.append(f"CHANGELOG.md does not contain a {project_version} release section")

    notes = ROOT / "docs" / "releases" / f"{project_version}.md"
    if not notes.is_file():
        errors.append(f"missing release notes: {notes.relative_to(ROOT)}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, for example v1.0.0")
    args = parser.parse_args()

    try:
        errors = check(args.tag)
    except ValueError as exc:
        errors = [str(exc)]

    if errors:
        for error in errors:
            print(f"release check failed: {error}", file=sys.stderr)
        return 1

    print(f"release metadata is consistent for {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
