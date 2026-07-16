from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_launch_metrics.py"
SPEC = importlib.util.spec_from_file_location("collect_launch_metrics", SCRIPT)
assert SPEC and SPEC.loader
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_collect_aggregates_github_and_pypi_metrics() -> None:
    responses = {
        "repos/xiaohei-info/oh-my-multica": {
            "stargazers_count": 12,
            "forks_count": 3,
            "subscribers_count": 4,
            "open_issues_count": 5,
        },
        "repos/xiaohei-info/oh-my-multica/traffic/views": {
            "count": 120,
            "uniques": 45,
        },
        "repos/xiaohei-info/oh-my-multica/traffic/clones": {
            "count": 30,
            "uniques": 18,
        },
        "repos/xiaohei-info/oh-my-multica/traffic/popular/referrers": [
            {"referrer": "news.ycombinator.com", "count": 20, "uniques": 10}
        ],
        "repos/xiaohei-info/oh-my-multica/releases?per_page=100": [
            {
                "tag_name": "v1.0.0",
                "published_at": "2026-07-16T12:00:00Z",
                "assets": [{"name": "package.whl", "download_count": 7}],
            }
        ],
    }

    result = metrics.collect_metrics(
        "xiaohei-info/oh-my-multica",
        "oh-my-multica",
        gh_api=lambda endpoint: responses[endpoint],
        discussion_count=lambda _repo: 6,
        pypi_recent=lambda _package: {
            "last_day": 8,
            "last_week": 21,
            "last_month": 34,
        },
        now=lambda: datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc),
    )

    assert result["collected_at"] == "2026-07-16T12:30:00Z"
    assert result["github"]["stars"] == 12
    assert result["github"]["traffic"]["views_unique"] == 45
    assert result["github"]["discussions"] == 6
    assert result["github"]["releases"]["total_downloads"] == 7
    assert result["pypi"]["status"] == "available"
    assert result["pypi"]["downloads"]["last_week"] == 21


def test_collect_marks_unpublished_pypi_package_as_unavailable() -> None:
    def gh_api(endpoint: str):
        if endpoint.endswith("/releases?per_page=100"):
            return []
        if endpoint.endswith("/popular/referrers"):
            return []
        if endpoint.endswith("/views") or endpoint.endswith("/clones"):
            return {"count": 0, "uniques": 0}
        return {
            "stargazers_count": 0,
            "forks_count": 0,
            "subscribers_count": 1,
            "open_issues_count": 0,
        }

    def unavailable(_package: str):
        raise metrics.MetricUnavailable("package has no public download data")

    result = metrics.collect_metrics(
        "xiaohei-info/oh-my-multica",
        "oh-my-multica",
        gh_api=gh_api,
        discussion_count=lambda _repo: 0,
        pypi_recent=unavailable,
        now=lambda: datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert result["github"]["releases"]["count"] == 0
    assert result["pypi"] == {
        "package": "oh-my-multica",
        "status": "unavailable",
        "reason": "package has no public download data",
    }
