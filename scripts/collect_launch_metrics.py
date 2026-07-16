#!/usr/bin/env python3
"""Collect public launch metrics without adding product telemetry."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class MetricUnavailable(RuntimeError):
    """A metric source is not available yet or cannot be queried."""


def _json_command(args: list[str]):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MetricUnavailable(f"{' '.join(args[:2])} failed: {message}")
    return json.loads(result.stdout)


def github_api(endpoint: str):
    return _json_command(["gh", "api", endpoint])


def github_discussion_count(repo: str) -> int:
    owner, name = repo.split("/", 1)
    query = (
        "query($owner:String!,$name:String!){"
        "repository(owner:$owner,name:$name){discussions{totalCount}}}"
    )
    data = _json_command([
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
    ])
    return int(data["data"]["repository"]["discussions"]["totalCount"])


def pypi_recent_downloads(package: str) -> dict[str, int]:
    url = f"https://pypistats.org/api/packages/{quote(package)}/recent"
    request = Request(url, headers={"User-Agent": "oh-my-multica-launch-metrics/1"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise MetricUnavailable("package has no public download data") from exc
        raise MetricUnavailable(f"PyPI Stats returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise MetricUnavailable(f"PyPI Stats request failed: {exc}") from exc

    data = payload.get("data") or {}
    return {
        "last_day": int(data.get("last_day", 0)),
        "last_week": int(data.get("last_week", 0)),
        "last_month": int(data.get("last_month", 0)),
    }


def collect_metrics(
    repo: str,
    package: str,
    *,
    gh_api: Callable[[str], object] = github_api,
    discussion_count: Callable[[str], int] = github_discussion_count,
    pypi_recent: Callable[[str], dict[str, int]] = pypi_recent_downloads,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict:
    repository = gh_api(f"repos/{repo}")
    views = gh_api(f"repos/{repo}/traffic/views")
    clones = gh_api(f"repos/{repo}/traffic/clones")
    referrers = gh_api(f"repos/{repo}/traffic/popular/referrers")
    releases = gh_api(f"repos/{repo}/releases?per_page=100")

    total_downloads = sum(
        int(asset.get("download_count", 0))
        for release in releases
        for asset in release.get("assets", [])
    )
    latest = releases[0] if releases else None

    try:
        pypi = {
            "package": package,
            "status": "available",
            "downloads": pypi_recent(package),
        }
    except MetricUnavailable as exc:
        pypi = {
            "package": package,
            "status": "unavailable",
            "reason": str(exc),
        }

    collected_at = now().astimezone(timezone.utc).isoformat(timespec="seconds")
    if collected_at.endswith("+00:00"):
        collected_at = collected_at[:-6] + "Z"

    return {
        "schema_version": 1,
        "collected_at": collected_at,
        "repository": repo,
        "github": {
            "stars": int(repository.get("stargazers_count", 0)),
            "forks": int(repository.get("forks_count", 0)),
            "watchers": int(repository.get("subscribers_count", 0)),
            "open_issues_and_prs": int(repository.get("open_issues_count", 0)),
            "discussions": int(discussion_count(repo)),
            "traffic": {
                "views": int(views.get("count", 0)),
                "views_unique": int(views.get("uniques", 0)),
                "clones": int(clones.get("count", 0)),
                "clones_unique": int(clones.get("uniques", 0)),
            },
            "top_referrers": list(referrers)[:10],
            "releases": {
                "count": len(releases),
                "latest_tag": latest.get("tag_name") if latest else None,
                "latest_published_at": latest.get("published_at") if latest else None,
                "total_downloads": total_downloads,
            },
        },
        "pypi": pypi,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="xiaohei-info/oh-my-multica")
    parser.add_argument("--package", default="oh-my-multica")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        result = collect_metrics(args.repo, args.package)
    except (MetricUnavailable, ValueError, json.JSONDecodeError) as exc:
        print(f"metrics collection failed: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
