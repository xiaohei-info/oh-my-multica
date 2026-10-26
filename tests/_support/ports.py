"""Ports a test hands to code that must not touch them.

These hold no state and simulate nothing: every call fails the test. A test that needs the platform's behaviour runs on
the Docker Multica (tests/it); a test that feeds canned payloads to an adapter builds that adapter itself.
"""
import pytest

from omac.engines.codehost import CodeHost


class UnusedCodeHost(CodeHost):
    def _fail(self, name):
        pytest.fail(f"CodeHost.{name} must not be called here")

    def request_pull_request_merge(self, *args, **kwargs):
        self._fail("request_pull_request_merge")

    def observe_pull_request(self, *args, **kwargs):
        self._fail("observe_pull_request")

    def check_pull_request(self, *args, **kwargs):
        self._fail("check_pull_request")

    def read_pull_request_readiness(self, *args, **kwargs):
        self._fail("read_pull_request_readiness")


class UnusedStore:
    """Stands in for any port object (store or runtime) that code under test must never use: every attribute access fails the test."""

    def __getattr__(self, name):
        pytest.fail(f"{name} must not be used here")
