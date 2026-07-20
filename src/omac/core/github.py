"""Pure GitHub identifier validation shared by adapters and delivery gates."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


_REPO_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def canonical_github_pr_url(value) -> str:
    """Return a canonical github.com PR URL or raise ValueError."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PR URL must be a non-empty string")
    raw = value.strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "PR URL must use https://github.com/<owner>/<repo>/pull/<number>")
    segments = parsed.path.rstrip("/").split("/")
    if len(segments) != 5 or segments[0] or segments[3] != "pull":
        raise ValueError(
            "PR URL must use https://github.com/<owner>/<repo>/pull/<number>")
    owner, repo, number = segments[1], segments[2], segments[4]
    if (
        not _REPO_SEGMENT.fullmatch(owner)
        or not _REPO_SEGMENT.fullmatch(repo)
        or not number.isdigit()
        or int(number) < 1
    ):
        raise ValueError(
            "PR URL must use https://github.com/<owner>/<repo>/pull/<number>")
    return f"https://github.com/{owner}/{repo}/pull/{int(number)}"
