"""Merge configuration (pure): the default command, retry limits.

The merge gate itself runs on Docker Multica with the gh shim as code host: tests/it/test_it_delivery_merge.py and
tests/it/test_it_merge_recovery.py; its decisions are the plans of tests/test_decide_merge.py and tests/test_decide_plans.py.
"""
from omac.core.config import DEFAULT_GITHUB_MERGE_COMMAND, DEFAULT_RETRY, get_merge_config, resolve_retry


class TestMergeConfig:
    def test_get_merge_config_defaults_to_github_merge(self):
        expected = {"command": DEFAULT_GITHUB_MERGE_COMMAND, "timeout_minutes": 30}
        assert get_merge_config({}) == expected
        assert get_merge_config({"merge": {}}) == expected
        assert get_merge_config({"merge": {"command": ""}}) == expected

    def test_get_merge_config_present(self):
        cfg = {"merge": {"command": "gh pr merge {pr_url} --squash"}}
        assert get_merge_config(cfg) == cfg["merge"]

    def test_resolve_retry_merge_default_and_custom(self):
        assert resolve_retry({})["merge"] == DEFAULT_RETRY["merge"]
        assert resolve_retry({"retry": {"merge": 5}})["merge"] == 5

    def test_resolve_retry_merge_zero(self):
        assert resolve_retry({"retry": {"merge": 0}})["merge"] == 0
